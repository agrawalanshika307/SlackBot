"""
mcp_server/slack_mcp_server.py
───────────────────────────────
Slack MCP Server — built with FastMCP.

Responsibilities:
  • Expose the `read_messages` tool over the MCP stdio transport.
  • Authenticate with Slack using SLACK_BOT_TOKEN from the environment.
  • Translate channel names → IDs when needed.
  • Return structured message data to the MCP client.

⚠️  IMPORTANT — stdio transport rules:
    • NEVER use print() in this file.  print() writes to stdout, which is
      the JSON-RPC pipe.  Anything unexpected on stdout will corrupt the
      MCP protocol framing and crash the client session.
    • Use sys.stderr.write() or the logging module (configured to stderr)
      for all debug/informational output.
"""

import json
import logging
import os
import sys

from mcp.server.fastmcp import FastMCP
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# ── Logging (stderr only — never stdout) ──────────────────────────────────────
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[SlackMCP] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ── Slack client ──────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
if not SLACK_BOT_TOKEN:
    log.error("SLACK_BOT_TOKEN is not set.  The read_messages tool will fail.")

slack = WebClient(token=SLACK_BOT_TOKEN)

# ── MCP Server ────────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="slack-mcp-server",
    instructions=(
        "This server exposes tools for reading Slack workspace messages. "
        "Use read_messages to fetch recent messages from a Slack channel."
    ),
)


# ── Helper: resolve channel name → ID ─────────────────────────────────────────
def _resolve_channel_id(channel: str) -> str:
    """
    Given a channel name (e.g. 'general' or '#general') or an already-valid
    channel ID (starts with 'C' or 'G'), return the canonical channel ID.

    Raises ValueError if the channel cannot be found.
    """
    # Strip leading '#' if present
    channel = channel.lstrip("#")

    # If it looks like a Slack ID already, trust it
    if channel.upper().startswith(("C", "G", "D")):
        return channel

    log.info("Resolving channel name '%s' → ID …", channel)
    try:
        # Paginate through all channels to find a match
        cursor = None
        while True:
            kwargs: dict = {
                "types": "public_channel,private_channel",
                "exclude_archived": True,
                "limit": 200,
            }
            if cursor:
                kwargs["cursor"] = cursor

            response = slack.conversations_list(**kwargs)
            for ch in response.get("channels", []):
                if ch["name"] == channel:
                    log.info("Resolved '%s' → %s", channel, ch["id"])
                    return ch["id"]

            # Follow pagination
            next_cursor = response.get("response_metadata", {}).get("next_cursor")
            if not next_cursor:
                break
            cursor = next_cursor

    except SlackApiError as exc:
        log.error("conversations_list failed: %s", exc.response["error"])
        raise ValueError(f"Slack API error while listing channels: {exc.response['error']}") from exc

    raise ValueError(
        f"Channel '{channel}' not found.  "
        "Make sure the bot has been invited to that channel and the name is correct."
    )


# ── Tool: read_messages ────────────────────────────────────────────────────────
@mcp.tool()
def read_messages(channel: str, limit: int = 20) -> str:
    """
    Read recent messages from a Slack channel.

    Args:
        channel: The Slack channel name (e.g. 'general') or channel ID
                 (e.g. 'C12345678').  Include or omit the leading '#'.
        limit:   Maximum number of messages to return (default: 20, max: 100).

    Returns:
        A JSON string containing a list of message objects, each with:
          - user:      Slack user ID of the sender (or 'bot' for bots).
          - text:      Message text content.
          - timestamp: Unix timestamp string (from Slack's 'ts' field).
          - readable_time: Human-readable UTC timestamp.
    """
    limit = max(1, min(limit, 100))  # clamp to [1, 100]

    log.info("read_messages called — channel=%s limit=%d", channel, limit)

    try:
        channel_id = _resolve_channel_id(channel)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    try:
        response = slack.conversations_history(
            channel=channel_id,
            limit=limit,
        )
    except SlackApiError as exc:
        error_code = exc.response["error"]
        log.error("conversations_history failed: %s", error_code)

        # Provide helpful messages for common errors
        hint = ""
        if error_code == "channel_not_found":
            hint = " The bot may not have been invited to this channel."
        elif error_code == "not_in_channel":
            hint = " Use /invite @YourBot in the Slack channel first."
        elif error_code in ("missing_scope", "token_revoked"):
            hint = " Check your SLACK_BOT_TOKEN and OAuth scopes."

        return json.dumps({"error": f"Slack API error: {error_code}.{hint}"})

    messages = response.get("messages", [])
    if not messages:
        return json.dumps({"messages": [], "info": "No messages found in this channel."})

    # Format messages into a clean structure
    formatted = []
    for msg in messages:
        # Convert Slack ts (Unix timestamp string) to readable UTC time
        ts_str = msg.get("ts", "0")
        try:
            from datetime import datetime, timezone
            ts_float = float(ts_str)
            readable = datetime.fromtimestamp(ts_float, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
        except (ValueError, OSError):
            readable = ts_str

        formatted.append(
            {
                "user": msg.get("user") or msg.get("bot_id") or "unknown",
                "text": msg.get("text", ""),
                "timestamp": ts_str,
                "readable_time": readable,
            }
        )

    log.info("Returning %d messages from channel %s", len(formatted), channel_id)
    return json.dumps(
        {
            "channel": channel,
            "channel_id": channel_id,
            "message_count": len(formatted),
            "messages": formatted,
        }
    )


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Starting Slack MCP Server on stdio transport …")
    mcp.run(transport="stdio")
