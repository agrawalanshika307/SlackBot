"""
app/api/chat.py
────────────────
FastAPI router — POST /chat endpoint.

This is the single entry point for user queries.  It:
  1. Validates the incoming request.
  2. Delegates processing to the orchestrator.
  3. Returns a structured JSON response.

The API layer contains zero business logic.  It only handles:
  • HTTP concerns (request/response shapes, status codes, error handling).
  • Calling the orchestrator.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import orchestrator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])


# ── Request / Response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """Incoming user query."""

    message: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="The user's natural-language question or command.",
        examples=["What are the recent messages in #general?"],
    )


class ChatResponse(BaseModel):
    """Response returned to the user."""

    response: str = Field(
        ...,
        description="The AI Teammate's answer to the user's question.",
    )
    tool_used: bool = Field(
        ...,
        description="True if a Slack tool was invoked to answer the question.",
    )
    tool_name: str | None = Field(
        default=None,
        description="Name of the tool that was called, if any.",
    )
    tool_arguments: dict | None = Field(
        default=None,
        description="Arguments passed to the tool, if any.",
    )


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=ChatResponse,
    summary="Send a message to the AI Teammate",
    response_description="The AI's response, with metadata about tool usage.",
)
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Send a natural-language message to the AI Teammate and receive a response.

    The backend will:
    1. Forward the message to Gemini.
    2. If Gemini requests a Slack tool, execute it via the MCP server.
    3. Return Gemini's final answer.

    **Example queries:**
    - "What are the last 10 messages in #general?"
    - "Summarise today's conversation in #engineering."
    - "What is MCP?"  ← answered directly, no Slack call needed.
    """
    log.info("POST /chat — message: %r", request.message[:80])

    try:
        result = await orchestrator.process(request.message)
    except FileNotFoundError as exc:
        log.error("MCP server script not found: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=(
                "MCP server script not found.  "
                "Ensure mcp_server/slack_mcp_server.py exists in the project root."
            ),
        ) from exc
    except ValueError as exc:
        log.error("Orchestrator value error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Unexpected orchestrator error")
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred: {type(exc).__name__}: {exc}",
        ) from exc

    return ChatResponse(
        response=result.response,
        tool_used=result.tool_used,
        tool_name=result.tool_name,
        tool_arguments=result.tool_arguments,
    )
