"""
app/main.py
────────────
FastAPI application entry point.

This module:
  • Creates the FastAPI app instance.
  • Registers all routers.
  • Adds CORS middleware.
  • Validates configuration on startup — fails fast if any secret is missing.
  • Provides the /health endpoint for liveness checks.
"""

import utils.transport
import utils.session
import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, slack_events
from app.config import get_settings

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="AI Teammate",
    description=(
        "A production-ready AI Teammate powered by Gemini + Slack + MCP.\n\n"
        "The backend orchestrates reasoning (Gemini) and tool execution "
        "(Slack via MCP) so the user gets intelligent, data-driven answers."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Allow all origins for local development.
# Restrict to specific origins in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Startup validation ────────────────────────────────────────────────────────

@app.on_event("startup")
async def validate_config() -> None:
    """
    Validate that all required environment variables are present.
    The app will refuse to start if any are missing.
    """
    try:
        settings = get_settings()
        log.info("✅ Configuration loaded successfully.")
        log.info("   Gemini model : %s", settings.gemini_model)
        log.info("   MCP server   : %s", settings.mcp_server_script)
        # Mask the token — show only the first 8 chars
        token_preview = settings.slack_bot_token[:8] + "…" if settings.slack_bot_token else "NOT SET"
        log.info("   Slack token  : %s", token_preview)
    except Exception as exc:
        log.critical(
            "❌ Configuration error: %s\n"
            "   Create a .env file with GEMINI_API_KEY and SLACK_BOT_TOKEN.",
            exc,
        )
        sys.exit(1)

    # Pre-fetch the bot's own Slack user ID so the first mention response is
    # instant (instead of paying the auth.test latency on the first event).
    try:
        from app.api.slack_events import _get_bot_user_id
        bot_uid = await _get_bot_user_id()
        if bot_uid:
            log.info("   Bot user ID  : %s", bot_uid)
        else:
            log.warning(
                "   Bot user ID could not be fetched automatically. "
                "Set SLACK_BOT_USER_ID in .env to suppress this warning."
            )
    except Exception as exc:
        log.warning("   Could not prefetch bot user ID: %s", exc)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(chat.router)
app.include_router(slack_events.router)

# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"], summary="Liveness check")
async def health() -> dict:
    """Returns 200 OK if the server is running and the vector store status."""
    from app.llm.retriever import get_retriever

    retriever = get_retriever()
    populated = retriever.is_populated()
    return {
        "status": "ok",
        "service": "ai-teammate",
        "vector_store": {
            "path": str(retriever.persist_directory),
            "populated": populated,
            "document_count": retriever.count(),
        },
    }


@app.get("/", tags=["Root"], summary="API information")
async def root() -> dict:
    """Returns basic API information."""
    return {
        "name": "AI Teammate",
        "version": "1.0.0",
        "docs": "/docs",
        "chat_endpoint": "POST /chat/",
    }
