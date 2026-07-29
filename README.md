# AI Teammate — Slack + FastAPI + Gemini + MCP

A **production-ready AI Teammate** that uses:

- **Slack** as the trigger (users @mention the bot) and data source (read messages via MCP)
- **Gemini** as the reasoning engine (via Google AI Studio)
- **MCP (Model Context Protocol)** as the tool architecture
- **FastAPI** as the backend / orchestrator

---

## Architecture

```
Slack (@mention)                 User (direct REST call)
       │                                │
       ▼                                ▼
 POST /slack/events            POST /chat/
       │                                │
       └──────────────┬─────────────────┘
                      ▼
            FastAPI Orchestrator
                      │
               Gemini API (reasoning)
                      │
          ┌───────────┴────────────┐
    No tool needed        Tool call requested
          │                        │
          │             MCPClient (stdio subprocess)
          │                        │
          │             Slack MCP Server
          │                        │
          │             slack_sdk → Slack Web API
          │                        │
          │              Tool result returned
          │                        │
          └───────────────────────┘
                      │
               Gemini final answer
                      │
          ┌───────────┴───────────┐
    REST response          Slack reply (in-thread)
```

**Key principle:** Gemini reasons, the backend executes, Slack is data-only.

---

## Project Structure

```
slackBot/
├── app/
│   ├── api/
│   │   ├── chat.py              # POST /chat endpoint (direct REST)
│   │   └── slack_events.py      # POST /slack/events (Slack @mention trigger)
│   ├── llm/
│   │   └── gemini_client.py     # Gemini wrapper (manual tool-call loop)
│   ├── mcp/
│   │   └── mcp_client.py        # MCP stdio client
│   ├── services/
│   │   └── orchestrator.py      # Core orchestration logic
│   ├── config.py                # Pydantic Settings (env vars)
│   └── main.py                  # FastAPI app entry point
│
├── mcp_server/
│   └── slack_mcp_server.py      # FastMCP server exposing read_messages
│
├── requirements.txt
├── .env.example                 # Template — copy to .env
└── README.md
```

---

## Manual Setup Steps

These steps require interacting with third-party dashboards and **cannot be automated**.

### Step 1 — Create a Slack App

1. Go to **https://api.slack.com/apps**
2. Click **Create New App** → **From Scratch**
3. Enter a name (e.g. `AI Teammate`) and select your workspace
4. Click **Create App**

---

### Step 2 — Configure OAuth Scopes

In your app dashboard: **OAuth & Permissions** → **Bot Token Scopes** → **Add an OAuth Scope**:

| Scope | Purpose |
|---|---|
| `channels:read` | List public channels |
| `channels:history` | Read messages in public channels |
| `groups:read` | List private channels the bot is in |
| `groups:history` | Read messages in private channels the bot is in |
| `chat:write` | **Post the AI reply back to Slack** |

---

### Step 3 — Copy Your Signing Secret

1. In your Slack App dashboard → **Basic Information** → **App Credentials**
2. Copy the **Signing Secret**
3. Paste it into your `.env` as `SLACK_SIGNING_SECRET=...`

This lets the backend verify every incoming event genuinely came from Slack (HMAC-SHA256).

---

### Step 4 — Enable Event Subscriptions

This is how Slack notifies your app when someone @mentions the bot.

1. In your Slack App dashboard → **Event Subscriptions**
2. Toggle **Enable Events** → **On**
3. In the **Request URL** field, enter your public URL (see ngrok setup below):
   ```
   https://<your-ngrok-subdomain>.ngrok-free.app/slack/events
   ```
   > Slack will immediately send a `url_verification` challenge. Your FastAPI server must be running with ngrok active for this to succeed — the page will show a green checkmark.
4. Under **Subscribe to bot events** → **Add Bot User Event** → add:
   - `app_mention`
5. Click **Save Changes**

---

### Step 5 — Install the App to Your Workspace

1. **OAuth & Permissions** → **Install to Workspace** (or **Reinstall** if already installed — required after adding `chat:write`)
2. Review permissions → **Allow**
3. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

---

### Step 6 — Invite the Bot to Channels

For every Slack channel you want the bot to participate in, open the channel and run:

```
/invite @AI Teammate
```

> The bot must be a member of a channel to read messages or reply in it.

---

### Step 7 — Get a Gemini API Key

1. Go to **https://aistudio.google.com/app/apikey**
2. Click **Create API Key**
3. Copy the key

---

## ngrok Setup (Expose localhost to Slack)

Slack's Events API requires a **public HTTPS URL**. ngrok creates a secure tunnel from the internet to your local machine.

### Install ngrok

Download from **https://ngrok.com/download** and follow the install instructions.

Or on Windows with winget:
```powershell
winget install ngrok
```

### Start the tunnel

```bash
# In a SEPARATE terminal — keep this running while you develop
ngrok http 8000
```

You'll see output like:
```
Forwarding  https://a1b2c3d4.ngrok-free.app -> http://localhost:8000
```

Copy the `https://...ngrok-free.app` URL and use it as your Slack Request URL:
```
https://a1b2c3d4.ngrok-free.app/slack/events
```

> ⚠️ The ngrok URL changes every time you restart ngrok (free plan). When it changes,
> update the Request URL in **Slack App → Event Subscriptions**.

---

## Installation

### 1. Navigate to the project directory

```bash
cd slackBot
```

### 2. Create and activate a virtual environment

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
copy .env.example .env      # Windows
# cp .env.example .env      # macOS / Linux
```

Open `.env` and fill in your real values:

```env
GEMINI_API_KEY=AIza...your-key-here...
SLACK_BOT_TOKEN=xoxb-...your-token-here...
SLACK_SIGNING_SECRET=abc123...your-signing-secret-here...
SLACK_BOT_USER_ID=          # optional — auto-fetched at startup if empty
```

---

## Running the Server

### Terminal 1 — FastAPI

```bash
uvicorn app.main:app --reload --port 8000
```

On startup you'll see:

```
✅ Configuration loaded successfully.
   Gemini model : gemini-2.0-flash
   MCP server   : mcp_server/slack_mcp_server.py
   Slack token  : xoxb-123…
   Bot user ID  : U12345678
```

### Terminal 2 — ngrok

```bash
ngrok http 8000
```

Paste the `https://...ngrok-free.app` URL into **Slack App → Event Subscriptions → Request URL**.

---

## Using the Bot

### Via Slack @mention (primary flow)

1. Open any Slack channel where the bot is invited
2. Type a message that @mentions the bot:

```
@AI Teammate what are the last 5 messages in #general?
```

The bot will:
- Immediately respond with `⏳ Thinking…` (within 3 seconds — satisfies Slack's timeout)
- Process the question through Gemini → MCP → Slack Web API
- Post the AI-generated answer in the **same thread**

### Via REST API (direct, no Slack needed)

```bash
curl -X POST http://localhost:8000/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "What are the last 5 messages in #general?"}'
```

**Response:**
```json
{
  "response": "Here are the last 5 messages from #general:\n\n1. **alice** (2024-01-15 09:32 UTC): Good morning everyone!\n...",
  "tool_used": true,
  "tool_name": "read_messages",
  "tool_arguments": {"channel": "general", "limit": 5}
}
```

### Interactive Docs

Once running, open **http://localhost:8000/docs** for the Swagger UI.

---

## How the Slack Mention Flow Works (Step by Step)

```
1.  User types: "@AI Teammate summarise today's conversation in #engineering"
2.  Slack POSTs a JSON event to POST /slack/events
3.  FastAPI verifies HMAC-SHA256 signature using SLACK_SIGNING_SECRET
4.  Handler checks event dedup set — is this a retry?
5.  Handler strips <@BOTID> prefix → extracts the clean question
6.  Handler returns HTTP 200 immediately  ← Slack sees success, won't retry
7.  asyncio.create_task() fires in background:
       a. Posts "⏳ Thinking…" to the Slack thread
       b. Calls orchestrator.process(question)
       c. Orchestrator → Gemini → tool call detected
       d. MCP Client → Slack MCP Server subprocess → Slack Web API
       e. Messages returned → Gemini generates summary
       f. chat_postMessage() posts final answer to thread
```

---

## Tool Reference

### `read_messages`

Reads recent messages from a Slack channel.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `channel` | string | ✅ | — | Channel name (`general`) or ID (`C12345678`) |
| `limit` | integer | ❌ | `20` | Number of messages to return (max 100) |

**Returns:** JSON with `channel`, `channel_id`, `message_count`, and a `messages` list. Each message has `user`, `text`, `timestamp`, and `readable_time`.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | Google AI Studio API key |
| `SLACK_BOT_TOKEN` | ✅ | Slack Bot User OAuth Token (`xoxb-...`) |
| `SLACK_SIGNING_SECRET` | ✅ | Slack App signing secret (for event verification) |
| `SLACK_BOT_USER_ID` | ❌ | Bot's Slack user ID — auto-fetched at startup if empty |
| `GEMINI_MODEL` | ❌ | Gemini model name (default: `gemini-2.0-flash`) |
| `MCP_SERVER_SCRIPT` | ❌ | Path to MCP server script (default: `mcp_server/slack_mcp_server.py`) |

---

## Troubleshooting

| Error / Symptom | Cause | Fix |
|---|---|---|
| `❌ Configuration error` | Missing `.env` variables | Copy `.env.example` → `.env` and fill in values |
| `channel_not_found` | Bot not in channel | Run `/invite @AI Teammate` in Slack |
| `missing_scope` | OAuth scope not added | Add scope in api.slack.com/apps → reinstall app |
| Bot can't post replies | `chat:write` scope missing | Add `chat:write` scope and reinstall the app |
| `MCP server script not found` | Wrong working directory | Run `uvicorn` from the project root (`slackBot/`) |
| `token_revoked` | Token expired | Reinstall the Slack app and copy the new token |
| Slack shows "URL not verified" | Server not running / wrong URL | Start FastAPI + ngrok, paste the correct ngrok URL |
| Bot doesn't respond to mentions | Event Subscriptions not set up | Enable Events and add `app_mention` bot event |
| Bot responds twice | Slack retry + duplicate processing | Built-in dedup set handles this automatically |
| `Invalid Slack signature` (403) | Wrong `SLACK_SIGNING_SECRET` | Re-copy signing secret from Basic Information |
