"""
app/services/orchestrator.py
─────────────────────────────
The Orchestrator — the core of the AI Teammate backend.

This module is the single place that ties Gemini reasoning and MCP tool
execution together.  It implements the complete request lifecycle:

  1. Open an MCP session (launches the Slack MCP Server subprocess).
  2. Fetch available tools from the MCP server.
  3. Build a GeminiClient pre-loaded with those tools.
  4. Send the user message to Gemini.
  5. If Gemini requests a tool:
       a. Extract the tool name and arguments.
       b. Execute the tool via the MCP client.
       c. Send the result back to Gemini.
       d. Return Gemini's final natural-language answer.
  6. If Gemini answers directly:
       a. Return the answer.

Design principles:
  • No business/reasoning logic lives here — only orchestration.
  • Gemini never calls Slack.  The MCP server never calls Gemini.
  • The orchestrator is the only component that knows about both sides.
"""

import logging
from dataclasses import dataclass

from app.config import get_settings
from app.llm.gemini_client import GeminiClient
from app.mcp.mcp_client import MCPClient

log = logging.getLogger(__name__)


@dataclass
class OrchestratorResult:
    """
    The structured result returned by the orchestrator to the API layer.

    Attributes
    ----------
    response : str
        The final natural-language answer for the user.
    tool_used : bool
        True if a tool was called during this request.
    tool_name : str | None
        Name of the tool that was called, or None.
    tool_arguments : dict | None
        Arguments passed to the tool, or None.
    """

    response: str
    tool_used: bool
    tool_name: str | None = None
    tool_arguments: dict | None = None


async def process(user_message: str) -> OrchestratorResult:
    """
    Main orchestration loop.  Called once per user request.

    Parameters
    ----------
    user_message : str
        The user's natural-language query.

    Returns
    -------
    OrchestratorResult
        The final answer plus metadata about what happened.

    Raises
    ------
    RuntimeError
        If the MCP server cannot be reached or Gemini fails.
    """
    settings = get_settings()

    log.info("=== Orchestrator: processing new request ===")
    log.info("User message: %r", user_message)

    # ── Step 1: Open MCP session ──────────────────────────────────────────────
    async with MCPClient(
        server_script=settings.mcp_server_script,
        slack_bot_token=settings.slack_bot_token,
    ) as mcp_client:

        # ── Step 2: Discover tools from MCP server ────────────────────────────
        mcp_tools = await mcp_client.list_tools()

        # ── Step 3: Build Gemini client with tools registered ─────────────────
        gemini = GeminiClient(
            api_key=settings.gemini_api_key,
            model_name=settings.gemini_model,
            mcp_tools=mcp_tools,
        )

        # ── Step 4: Send user message to Gemini ───────────────────────────────
        log.info("Step 4: Sending user message to Gemini …")
        first_response = gemini.send_message(user_message)

        # ── Step 5: Check if Gemini wants to call a tool ──────────────────────
        function_call_part = GeminiClient.extract_function_call_part(first_response)

        if function_call_part is not None:
            # ── Step 5a: Gemini requested a tool call ─────────────────────────
            tool_name = function_call_part.function_call.name
            tool_args = dict(function_call_part.function_call.args)

            log.info(
                "Step 5a: Gemini requested tool '%s' with args %s",
                tool_name,
                tool_args,
            )

            # ── Step 5b: Execute the tool via MCP client ──────────────────────
            log.info("Step 5b: Executing MCP tool '%s' …", tool_name)
            tool_result = await mcp_client.call_tool(tool_name, tool_args)

            log.info(
                "Step 5b: Tool '%s' returned %d chars.",
                tool_name,
                len(tool_result),
            )

            # ── Step 5c: Send tool result back to Gemini ──────────────────────
            log.info("Step 5c: Feeding tool result back to Gemini …")
            final_response = gemini.send_tool_result(
                original_message=user_message,
                function_call_part=function_call_part,
                tool_result=tool_result,
            )

            # ── Step 5d: Extract final answer ─────────────────────────────────
            final_text = GeminiClient.extract_text(final_response)

            if not final_text:
                final_text = (
                    "I retrieved the data from Slack but couldn't generate a "
                    "readable response.  Here is the raw result:\n\n" + tool_result
                )

            log.info("=== Orchestrator: finished (tool used) ===")
            return OrchestratorResult(
                response=final_text,
                tool_used=True,
                tool_name=tool_name,
                tool_arguments=tool_args,
            )

        else:
            # ── Step 6: Gemini answered directly (no tool needed) ─────────────
            direct_text = GeminiClient.extract_text(first_response)

            if not direct_text:
                direct_text = (
                    "I was unable to generate a response.  "
                    "Please rephrase your question."
                )

            log.info("=== Orchestrator: finished (no tool used) ===")
            return OrchestratorResult(
                response=direct_text,
                tool_used=False,
            )
