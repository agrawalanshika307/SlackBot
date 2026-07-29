"""
tunnel.py
---------
Starts an ngrok tunnel to localhost:8000 using the Python ngrok SDK.
Run this in a separate terminal while uvicorn is running.

Usage:
    python tunnel.py

Requires NGROK_AUTHTOKEN in your .env (or environment).
Get a free token at: https://dashboard.ngrok.com/authtokens
"""

import os
import sys
import time
from pathlib import Path

# ── Load .env so NGROK_AUTHTOKEN is available ─────────────────────────────────
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# ── Validate authtoken is present ─────────────────────────────────────────────
authtoken = os.environ.get("NGROK_AUTHTOKEN", "")
if not authtoken:
    print("ERROR: NGROK_AUTHTOKEN is not set.")
    print("  1. Get a free token at: https://dashboard.ngrok.com/authtokens")
    print("  2. Add it to your .env file:")
    print("     NGROK_AUTHTOKEN=your-token-here")
    sys.exit(1)

# ── Import ngrok ──────────────────────────────────────────────────────────────
try:
    import ngrok
except ImportError:
    print("ERROR: ngrok Python package not found. Run: pip install ngrok")
    sys.exit(1)

print("Starting ngrok tunnel to http://localhost:8000 ...")

try:
    tunnel = ngrok.forward(8000, authtoken=authtoken)
    url = tunnel.url()

    print("")
    print("Tunnel is live!")
    print("")
    print("  Public URL  : " + url)
    print("  Slack URL   : " + url + "/slack/events")
    print("")
    print("  Paste the Slack URL into:")
    print("  Slack App Dashboard -> Event Subscriptions -> Request URL")
    print("")
    print("  Press Ctrl+C to stop.")
    print("-" * 60)

    while True:
        time.sleep(1)

except KeyboardInterrupt:
    print("\nTunnel stopped.")
    sys.exit(0)
except Exception as exc:
    err = str(exc)
    print("\nError: " + err)
    if "ERR_NGROK_4018" in err or "not authenticated" in err.lower():
        print("\n  Your authtoken was not accepted.")
        print("  Check it at: https://dashboard.ngrok.com/authtokens")
    sys.exit(1)
