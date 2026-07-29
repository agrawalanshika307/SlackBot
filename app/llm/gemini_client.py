"""
app/llm/gemini_client.py
─────────────────────────
Gemini LLM Client — wraps the current `google-genai` SDK (google.genai).

NOTE: The older `google-generativeai` package is fully deprecated and
receives no further updates.  This module uses `google-genai` instead.

Responsibilities:
  • Configure the Gemini SDK with the API key.
  • Accept a list of tool definitions (translated from MCP → Gemini format).
  • Send user messages to Gemini.
  • Return either:
      - A plain text answer  (no tool needed), or
      - A Part containing a function_call (tool name + arguments).

Design notes:
  • We use the MANUAL function-calling loop with
    AutomaticFunctionCallingConfig(disable=True).  This is intentional —
    the backend must be the explicit orchestrator.  Gemini reasons;
    the backend decides when/how to call tools.
  • Multi-turn conversation is built explicitly as a list of Content objects.
"""

import logging
from typing import Any

from google import genai
from google.genai import types
from mcp.types import Tool as MCPTool

log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def mcp_tool_to_gemini_declaration(mcp_tool: MCPTool) -> types.FunctionDeclaration:
    """
    Convert an MCP Tool definition into a Gemini FunctionDeclaration.

    MCP tools expose a JSON Schema via `inputSchema`.
    Gemini FunctionDeclaration accepts a JSON Schema dict for `parameters`.
    """
    schema: dict = dict(mcp_tool.inputSchema) if mcp_tool.inputSchema else {}

    # Gemini requires "type": "object" at the top level.
    if "type" not in schema:
        schema = {"type": "object", "properties": schema}

    return types.FunctionDeclaration(
        name=mcp_tool.name,
        description=mcp_tool.description or f"Execute the {mcp_tool.name} tool.",
        parameters=schema,
    )


# ── Gemini Client ─────────────────────────────────────────────────────────────

class GeminiClient:
    """
    Thin wrapper around the Gemini generative model (google.genai SDK).

    Parameters
    ----------
    api_key : str
        Gemini API key.
    model_name : str
        Model identifier (default: 'gemini-1.5-flash').
    mcp_tools : list[MCPTool]
        Tools discovered from the MCP server.  Converted to Gemini format.
    """

    _SYSTEM_INSTRUCTION = (
        "You are an AI Teammate with access to Slack workspace data. "
        "When a user asks about Slack messages, channels, or conversations, "
        "use the read_messages tool to fetch real data before answering. "
        "Always be helpful, concise, and accurate. "
        "When presenting messages, format them clearly with timestamps and authors."
    )

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-1.5-flash",
        mcp_tools: list[MCPTool] | None = None,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name

        # Build Gemini tool list from MCP tool definitions
        self._gemini_tools: list[types.Tool] | None = None
        if mcp_tools:
            declarations = [mcp_tool_to_gemini_declaration(t) for t in mcp_tools]
            self._gemini_tools = [types.Tool(function_declarations=declarations)]
            log.info(
                "Registered %d tool(s) with Gemini: %s",
                len(declarations),
                [d.name for d in declarations],
            )

        log.info("GeminiClient initialised with model '%s'.", model_name)

    # ── First turn ────────────────────────────────────────────────────────────

    def send_message(self, user_message: str) -> types.GenerateContentResponse:
        """
        Send the user's message to Gemini and return the raw response.

        The response may contain either:
          • response.text  — a direct text answer (no tool needed)
          • a function_call Part — a tool call request from Gemini
        """
        log.info("Sending message to Gemini (len=%d chars).", len(user_message))

        config = types.GenerateContentConfig(
            system_instruction=self._SYSTEM_INSTRUCTION,
            tools=self._gemini_tools,
            # Disable automatic function calling — we orchestrate manually
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        response = self._client.models.generate_content(
            model=self._model_name,
            contents=user_message,
            config=config,
        )
        self._log_response(response)
        return response

    # ── Second turn (after tool execution) ────────────────────────────────────

    def send_tool_result(
        self,
        original_message: str,
        function_call_part: types.Part,
        tool_result: str,
    ) -> types.GenerateContentResponse:
        """
        After the backend has executed a tool, feed the result back to Gemini
        and get the final natural-language answer.

        Constructs the full multi-turn conversation:
          Turn 1 user:  original message
          Turn 1 model: function_call Part (Gemini's tool request)
          Turn 2 user:  function_response Part (our tool result)

        Parameters
        ----------
        original_message : str
            The original user query.
        function_call_part : types.Part
            The Part containing the function_call from Gemini's first response.
        tool_result : str
            The raw string result returned by the MCP tool.
        """
        import json as _json

        try:
            structured_result: Any = _json.loads(tool_result)
        except (_json.JSONDecodeError, ValueError):
            structured_result = {"result": tool_result}

        fn_name = function_call_part.function_call.name
        log.info("Sending tool result for '%s' back to Gemini …", fn_name)

        contents = [
            # Turn 1 – user message
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=original_message)],
            ),
            # Turn 1 – model response (Gemini's tool call request)
            types.Content(
                role="model",
                parts=[function_call_part],
            ),
            # Turn 2 – function result sent back as user role
            types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name=fn_name,
                        response={"result": structured_result},
                    )
                ],
            ),
        ]

        config = types.GenerateContentConfig(
            system_instruction=self._SYSTEM_INSTRUCTION,
            tools=self._gemini_tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        response = self._client.models.generate_content(
            model=self._model_name,
            contents=contents,
            config=config,
        )
        self._log_response(response)
        return response

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def extract_function_call_part(
        response: types.GenerateContentResponse,
    ) -> types.Part | None:
        """
        Return the first Part that contains a function_call, or None.

        We return the whole Part (not just the FunctionCall object) so that
        it can be placed verbatim into the multi-turn content history.
        """
        for candidate in response.candidates or []:
            for part in candidate.content.parts or []:
                if part.function_call and part.function_call.name:
                    return part
        return None

    @staticmethod
    def extract_text(response: types.GenerateContentResponse) -> str:
        """Extract text from a Gemini response; returns '' if none found."""
        try:
            if response.text:
                return response.text
        except (AttributeError, ValueError):
            pass

        for candidate in response.candidates or []:
            for part in candidate.content.parts or []:
                if hasattr(part, "text") and part.text:
                    return part.text
        return ""

    @staticmethod
    def _log_response(response: types.GenerateContentResponse) -> None:
        """Log a summary of what Gemini returned."""
        fc_part = GeminiClient.extract_function_call_part(response)
        if fc_part:
            fc = fc_part.function_call
            log.info(
                "Gemini returned a function call: name='%s' args=%s",
                fc.name,
                dict(fc.args),
            )
        else:
            text = GeminiClient.extract_text(response)
            log.info("Gemini returned a text response (%d chars).", len(text))
