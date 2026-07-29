"""
app/mcp/mcp_client.py
──────────────────────
MCP Client — connects the FastAPI backend to the Slack MCP Server.

Transport:  stdio (the MCP server runs as a managed subprocess).

Usage pattern (async context manager):

    async with MCPClient() as client:
        tools   = await client.list_tools()
        result  = await client.call_tool("read_messages", {"channel": "general"})

The client automatically:
  • Launches the MCP server subprocess.
  • Initialises the MCP session (protocol handshake).
  • Tears down cleanly on exit (kills the subprocess).
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

log = logging.getLogger(__name__)


class MCPClient:
    """
    Async context manager that manages a single MCP session connected to
    the Slack MCP Server via stdio transport.

    Attributes
    ----------
    server_script : str
        Path to the MCP server script, relative to the project root.
    slack_bot_token : str
        Slack Bot Token forwarded to the subprocess environment.
    """

    def __init__(self, server_script: str, slack_bot_token: str) -> None:
        self._server_script = server_script
        self._slack_bot_token = slack_bot_token

        self._stdio_context = None
        self._session_context = None
        self.session: ClientSession | None = None

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "MCPClient":
        await self._connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._disconnect()

    # ── Internal: connect / disconnect ────────────────────────────────────────

    async def _connect(self) -> None:
        """
        Launch the MCP server subprocess and initialise the MCP session.

        The server is started with the current Python interpreter so that it
        shares the same virtual-env (and thus the same installed packages).
        """
        # Resolve the absolute path to the server script so it works
        # regardless of where uvicorn was launched from.
        project_root = Path(__file__).resolve().parent.parent.parent
        server_path = project_root / self._server_script

        if not server_path.exists():
            raise FileNotFoundError(
                f"MCP server script not found at: {server_path}\n"
                "Make sure mcp_server/slack_mcp_server.py exists."
            )

        log.info("Starting MCP subprocess: %s", server_path)

        # Build the subprocess environment:
        #   - inherit the current process env (PATH, VIRTUAL_ENV, etc.)
        #   - inject SLACK_BOT_TOKEN so the server can authenticate
        env = {**os.environ, "SLACK_BOT_TOKEN": self._slack_bot_token}

        server_params = StdioServerParameters(
            command=sys.executable,   # same Python interpreter as the backend
            args=[str(server_path)],
            env=env,
        )

        # Open the stdio transport (starts the subprocess)
        self._stdio_context = stdio_client(server_params)
        read, write = await self._stdio_context.__aenter__()

        # Create and initialise the MCP session
        self._session_context = ClientSession(read, write)
        self.session = await self._session_context.__aenter__()
        await self.session.initialize()

        log.info("MCP session initialised successfully.")

    async def _disconnect(self) -> None:
        """Gracefully close the session and terminate the subprocess."""
        if self._session_context:
            try:
                await self._session_context.__aexit__(None, None, None)
            except Exception as exc:
                log.warning("Error closing MCP session: %s", exc)

        if self._stdio_context:
            try:
                await self._stdio_context.__aexit__(None, None, None)
            except Exception as exc:
                log.warning("Error closing stdio transport: %s", exc)

        log.info("MCP session closed.")

    # ── Public API ────────────────────────────────────────────────────────────

    async def list_tools(self) -> list[Tool]:
        """
        Fetch the list of tools advertised by the MCP server.

        Returns
        -------
        list[Tool]
            MCP Tool objects, each containing name, description, and
            inputSchema (JSON Schema dict).
        """
        if self.session is None:
            raise RuntimeError("MCPClient is not connected.  Use async with MCPClient() as client.")

        response = await self.session.list_tools()
        tools: list[Tool] = response.tools
        log.info("MCP server exposes %d tool(s): %s", len(tools), [t.name for t in tools])
        return tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """
        Call a named MCP tool and return its result as a string.

        Parameters
        ----------
        tool_name : str
            The exact name of the tool to invoke (e.g. 'read_messages').
        arguments : dict
            Key-value pairs matching the tool's inputSchema.

        Returns
        -------
        str
            The text content returned by the tool.  If the tool returns
            structured data (JSON), this will be a JSON string.

        Raises
        ------
        RuntimeError
            If the client is not connected.
        ValueError
            If the tool call returns an error or empty result.
        """
        if self.session is None:
            raise RuntimeError("MCPClient is not connected.  Use async with MCPClient() as client.")

        log.info("Calling MCP tool '%s' with args: %s", tool_name, arguments)

        result = await self.session.call_tool(tool_name, arguments=arguments)

        # The result.content is a list of ContentBlocks; extract text blocks.
        if not result.content:
            raise ValueError(f"Tool '{tool_name}' returned an empty result.")

        # Concatenate all text content blocks
        text_parts: list[str] = []
        for block in result.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)

        if not text_parts:
            raise ValueError(f"Tool '{tool_name}' returned no text content.")

        combined = "\n".join(text_parts)
        log.info("Tool '%s' returned %d character(s).", tool_name, len(combined))
        return combined
