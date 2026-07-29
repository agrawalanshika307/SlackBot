"""
app/api/slack_events.py
────────────────────────
FastAPI router — POST /slack/events

Handles two types of Slack Events API payloads:

  1. url_verification  — one-time handshake Slack uses to confirm the endpoint.
                         We simply echo the 'challenge' field back.

  2. event_callback    — the real event.  We filter for 'app_mention' events,
                         extract the question text, run it through the existing
                         orchestrator, and post the AI reply back to Slack in
                         the same thread.

Security:
  Every incoming request is verified against the X-Slack-Signature header using
  our Slack Signing Secret (HMAC-SHA256).  Requests that fail verification are
  rejected with 403.

  Slack guarantees "at-least-once" delivery — it retries if your server is
  slow or returns an error.  We keep a short-lived dedup set of processed
  event_ts IDs to avoid answering the same mention twice.

Design constraint:
  This handler must return HTTP 200 to Slack within 3 seconds, or Slack will
  retry.  For production workloads the Gemini+MCP call can take longer than
  that, so we acknowledge immediately and process in the background via
  asyncio.create_task().
"""

import asyncio
import hashlib
import hmac
import logging
import re
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Request, Response
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from app.config import get_settings
from app.services import orchestrator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/slack", tags=["Slack Events"])

# ── Deduplication ─────────────────────────────────────────────────────────────
# Slack retries events that don't receive a quick 200.  We track the last N
# event_ts values we've already processed.  A deque with maxlen is O(1) for
# append and automatically evicts old entries.
_SEEN_EVENT_IDS: deque[str] = deque(maxlen=512)


def _already_seen(event_id: str) -> bool:
    """Return True if we have already processed this event; record it if not."""
    if event_id in _SEEN_EVENT_IDS:
        return True
    _SEEN_EVENT_IDS.append(event_id)
    return False


# ── Signature verification ────────────────────────────────────────────────────

def _verify_slack_signature(request_body: bytes, timestamp: str, signature: str) -> bool:
    """
    Verify the X-Slack-Signature header.

    Slack signs every request with HMAC-SHA256 using the Signing Secret:
        base_string = "v0:{timestamp}:{body}"
        expected    = "v0=" + hmac_sha256(signing_secret, base_string)

    We also reject requests whose timestamp is more than 5 minutes old to
    prevent replay attacks.

    Returns True if the signature is valid, False otherwise.
    """
    settings = get_settings()

    # If no signing secret is configured, skip verification (dev mode).
    if not settings.slack_signing_secret:
        log.warning(
            "SLACK_SIGNING_SECRET is not set — skipping signature verification. "
            "This is insecure; set it before deploying."
        )
        return True

    # Reject stale timestamps (replay-attack protection)
    try:
        ts_int = int(timestamp)
    except (TypeError, ValueError):
        return False

    if abs(time.time() - ts_int) > 300:  # 5 minutes
        log.warning("Slack request timestamp is too old (%s); rejecting.", timestamp)
        return False

    # Compute expected signature
    base_string = f"v0:{timestamp}:{request_body.decode('utf-8')}"
    expected_sig = (
        "v0="
        + hmac.new(
            settings.slack_signing_secret.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )

    return hmac.compare_digest(expected_sig, signature)


# ── Bot user ID resolution ────────────────────────────────────────────────────

_bot_user_id: str | None = None  # cached after first lookup


async def _get_bot_user_id() -> str:
    """
    Return the bot's own Slack user ID (e.g. 'U12345678').

    Tries settings first, then calls auth.test once and caches the result.
    """
    global _bot_user_id
    if _bot_user_id:
        return _bot_user_id

    settings = get_settings()
    if settings.slack_bot_user_id:
        _bot_user_id = settings.slack_bot_user_id
        return _bot_user_id

    # Auto-fetch from Slack
    client = WebClient(token=settings.slack_bot_token)
    try:
        resp = client.auth_test()
        _bot_user_id = resp["user_id"]
        log.info("Auto-resolved bot user ID: %s", _bot_user_id)
    except SlackApiError as exc:
        log.error("auth.test failed: %s", exc.response.get("error"))
        _bot_user_id = ""  # fall back to regex strip

    return _bot_user_id or ""


# ── Background processing ─────────────────────────────────────────────────────

async def _handle_mention(
    channel: str,
    thread_ts: str,
    raw_text: str,
    event_ts: str,
) -> None:
    """
    Background task: run the user's question through the orchestrator and post
    the answer back to Slack.

    Parameters
    ----------
    channel   : Slack channel ID where the mention happened.
    thread_ts : Thread timestamp to reply into (the message's own ts).
    raw_text  : The full mention text, including the <@BOTID> prefix.
    event_ts  : Event timestamp used for deduplication.
    """
    settings = get_settings()
    client = WebClient(token=settings.slack_bot_token)

    # ── Strip the bot mention prefix ──────────────────────────────────────────
    # Slack formats mentions as <@UXXXXXXX>.  Strip it to get the clean query.
    bot_id = await _get_bot_user_id()
    if bot_id:
        # Try to remove the specific bot mention first
        question = re.sub(rf"<@{re.escape(bot_id)}>", "", raw_text, count=1).strip()
    else:
        # Fall back: strip any <@UXXXXXXX> pattern
        question = re.sub(r"<@[A-Z0-9]+>", "", raw_text, count=1).strip()

    if not question:
        question = "Hello! How can I help you?"

    log.info(
        "Processing mention — channel=%s thread=%s question=%r",
        channel, thread_ts, question[:120],
    )

    # ── Post a typing indicator ───────────────────────────────────────────────
    # Gives the user instant feedback while Gemini+MCP works.
    try:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="⏳ Thinking…",
        )
    except SlackApiError:
        pass  # non-critical

    # ── Run through the orchestrator (Gemini + MCP) ───────────────────────────
    try:
        result = await orchestrator.process(question)
        answer = result.response
    except Exception as exc:
        log.exception("Orchestrator failed for event %s", event_ts)
        answer = f"❌ Sorry, I hit an error: `{type(exc).__name__}: {exc}`"

    # ── Post the final answer ─────────────────────────────────────────────────
    try:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=answer,
        )
        log.info("Replied to mention in channel=%s thread=%s", channel, thread_ts)
    except SlackApiError as exc:
        log.error(
            "Failed to post reply: %s — did you add chat:write scope and reinstall the app?",
            exc.response.get("error"),
        )


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post(
    "/events",
    summary="Receive Slack Events API payloads",
    response_description="HTTP 200 — always returned quickly to satisfy Slack's 3-second rule.",
)
async def slack_events(request: Request) -> Response:
    """
    Receive and handle Slack Events API payloads.

    Two payload types are handled:
    - **url_verification**: echoes the challenge (required during initial setup).
    - **event_callback / app_mention**: processes the user's mention in the background.

    All other event types are acknowledged silently.
    """
    # ── Read raw body (needed for signature verification before JSON parse) ───
    body_bytes = await request.body()

    # ── Signature verification ────────────────────────────────────────────────
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _verify_slack_signature(body_bytes, timestamp, signature):
        log.warning("Invalid Slack signature — rejecting request.")
        raise HTTPException(status_code=403, detail="Invalid Slack signature.")

    # ── Parse JSON ────────────────────────────────────────────────────────────
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    payload_type = payload.get("type")

    # ── 1. URL Verification handshake ─────────────────────────────────────────
    if payload_type == "url_verification":
        challenge = payload.get("challenge", "")
        log.info("Slack URL verification challenge received — echoing back.")
        return Response(content=challenge, media_type="text/plain")

    # ── 2. Event callback ─────────────────────────────────────────────────────
    if payload_type == "event_callback":
        event = payload.get("event", {})
        event_type = event.get("type")
        event_ts = event.get("event_ts") or event.get("ts", "")

        # Only handle app_mention events
        if event_type != "app_mention":
            log.debug("Ignoring event type: %s", event_type)
            return Response(content="ok", media_type="text/plain")

        # Deduplication — Slack retries if we're slow
        if _already_seen(event_ts):
            log.info("Duplicate event_ts=%s — ignoring.", event_ts)
            return Response(content="ok", media_type="text/plain")

        channel = event.get("channel", "")
        # Reply in the same thread; if the mention is at top level use its ts
        thread_ts = event.get("thread_ts") or event.get("ts", "")
        raw_text = event.get("text", "")

        log.info(
            "Received app_mention — channel=%s event_ts=%s text=%r",
            channel, event_ts, raw_text[:80],
        )

        # ── Fire-and-forget background task ──────────────────────────────────
        # We MUST return 200 within 3 s or Slack will retry.
        # The actual processing (Gemini + MCP) can take longer.
        asyncio.create_task(
            _handle_mention(
                channel=channel,
                thread_ts=thread_ts,
                raw_text=raw_text,
                event_ts=event_ts,
            )
        )

        return Response(content="ok", media_type="text/plain")

    # ── Unknown payload type ──────────────────────────────────────────────────
    log.debug("Unhandled Slack payload type: %s", payload_type)
    return Response(content="ok", media_type="text/plain")
