"""
app/config.py
─────────────
Centralised configuration loaded from environment variables / .env file.

All secrets are validated at startup via Pydantic Settings.
If a required variable is missing, the app will refuse to start with a
clear error message — no silent failures.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from the environment."""

    # Gemini
    gemini_api_key: str
    gemini_model: str = "gemini-3.5-flash"

    # Slack (used by the MCP server subprocess and Events API)
    slack_bot_token: str

    # Slack Events API — used to verify that incoming requests genuinely come
    # from Slack (HMAC-SHA256 signature check).  Find this in your Slack App
    # dashboard → Basic Information → App Credentials → Signing Secret.
    slack_signing_secret: str = ""

    # Optional: pre-set the bot's own user ID (e.g. "U12345678") so we can
    # strip the <@BOTID> prefix from mention text.  If left empty the app will
    # auto-fetch it from Slack at startup using auth.test.
    slack_bot_user_id: str = ""

    # MCP Server
    # Path to the Slack MCP Server script, relative to project root.
    mcp_server_script: str = "mcp_server/slack_mcp_server.py"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",   # silently ignore unknown env vars (e.g. NGROK_AUTHTOKEN)
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached Settings instance.

    Using lru_cache ensures the .env file is read only once for the
    lifetime of the process, which is efficient and consistent.
    """
    return Settings()
