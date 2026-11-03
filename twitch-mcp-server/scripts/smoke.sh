#!/usr/bin/env bash
# End-to-end check against the running container, over HTTP, exactly as an MCP client would,
# using LIVE Twitch data (the app credentials in .env). Prints sizes only, never content.
#   scripts/smoke.sh
# The bearer token is read from .env and never printed.
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

if [ "$#" -gt 0 ]; then
  die "usage: scripts/smoke.sh (takes no arguments)"
fi
command -v python3 >/dev/null 2>&1 || die "python3 is required"
[ -f "${ENV_FILE}" ] || die ".env not found. Run scripts/setup.sh first."
token="$(env_get MCP_AUTH_TOKEN)"
[ -n "${token}" ] || die "MCP_AUTH_TOKEN is empty in .env"

export SMOKE_URL
SMOKE_URL="$(mcp_url)"
export SMOKE_TOKEN="${token}"
# With user OAuth on, also check the two account tools (status only; no sign-in is attempted).
if user_oauth_enabled; then export SMOKE_USER_OAUTH=1; else export SMOKE_USER_OAUTH=0; fi
# One allowlisted broadcaster id is used as the read argument; it is never printed.
export SMOKE_BROADCASTER_ID
SMOKE_BROADCASTER_ID="$(env_get TWITCH_ALLOWED_BROADCASTER_IDS | tr -d '[:space:]' | cut -d, -f1)"
[ -n "${SMOKE_BROADCASTER_ID}" ] || die "TWITCH_ALLOWED_BROADCASTER_IDS is empty in .env"

python3 - <<'PYSMOKE'
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

URL = os.environ["SMOKE_URL"]
BASE = URL.rsplit("/mcp", 1)[0]
# T-D8: with TLS on, the server's leaf comes from this project's own local CA, which the probe
# cannot verify through the container. Verification is disabled for THIS loopback self-probe only;
# nothing here ever talks to Twitch, and the server's own upstream calls always verify (T7).
CTX = ssl._create_unverified_context() if URL.startswith("https://") else None
TOKEN = os.environ["SMOKE_TOKEN"]
BROADCASTER_ID = os.environ["SMOKE_BROADCASTER_ID"]
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}
failures: list[str] = []
counter = [0]


def http(method: str, url: str, body: bytes | None = None, token: str | None = None):
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60, context=CTX) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def rpc(method: str, params: dict | None = None):
    counter[0] += 1
    payload = {"jsonrpc": "2.0", "id": counter[0], "method": method}
    if params is not None:
        payload["params"] = params
    status, raw = http("POST", URL, json.dumps(payload).encode(), TOKEN)
    if status != 200:
        raise RuntimeError(f"{method}: HTTP {status}")
    return json.loads(raw), len(raw)


def call(name: str, arguments: dict):
    data, size = rpc("tools/call", {"name": name, "arguments": arguments})
    result = data["result"]
    text = result["content"][0]["text"] if result.get("content") else ""
    return result, text, size


print("== health and auth")
status, _ = http("GET", BASE + "/healthz")
print(f"  healthz: {status}")
if status != 200:
    failures.append("healthz")
status, _ = http("POST", URL, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode())
print(f"  unauthenticated /mcp: {status}")
if status != 401:
    failures.append("unauthenticated request was not rejected")

print("== tools/list")
data, size = rpc("tools/list")
names = sorted(t["name"] for t in data["result"]["tools"])
print(f"  {len(names)} tools ({size}B)")
if os.environ.get("SMOKE_WRITES", "0") != "1" and any(
    n in set(names)
    for n in (
        "update_channel", "create_poll", "end_poll", "create_prediction", "end_prediction",
        "send_chat_message", "timeout_user", "remove_timeout", "ban_user", "unban_user",
        "set_automod_term", "remove_automod_term", "add_vip", "remove_vip", "add_moderator",
        "remove_moderator",
    )
):
    print("  note: write tools are registered (TWITCH_ENABLE_WRITES/TWITCH_WRITE_GROUPS are on)")

print("== twitch_account_status + get_channels + get_streams (sizes only)")
result, text, size = call("twitch_account_status", {})
if result.get("isError"):
    failures.append("twitch_account_status returned an error")
    print(f"  [FAIL] twitch_account_status: {size}B")
else:
    structured = result.get("structuredContent") or {}
    print(f"  [ok ] twitch_account_status: authenticated={structured.get('authenticated')} "
          f"as_of={structured.get('meta', {}).get('as_of')} response={size}B")

result, text, size = call("get_channels", {"broadcaster_id": BROADCASTER_ID})
if result.get("isError"):
    failures.append("get_channels returned an error")
    print(f"  [FAIL] get_channels: {size}B")
else:
    structured = result.get("structuredContent") or {}
    print(f"  [ok ] get_channels: channels={len(structured.get('channels', []))} "
          f"text={len(text.encode())}B response={size}B")

result, text, size = call("get_streams", {"user_id": BROADCASTER_ID})
if result.get("isError"):
    failures.append("get_streams returned an error")
    print(f"  [FAIL] get_streams: {size}B")
else:
    structured = result.get("structuredContent") or {}
    print(f"  [ok ] get_streams: streams={len(structured.get('streams', []))} "
          f"has_more={structured.get('pagination', {}).get('has_more')} response={size}B")

if os.environ.get("SMOKE_USER_OAUTH") == "1":
    print("== user OAuth (status only; no sign-in attempted)")
    account_tools = {"connect_twitch_account", "disconnect_twitch_account"}
    if not account_tools <= set(names):
        failures.append("user OAuth account tools are missing")

print()
if failures:
    print("SMOKE TEST FAILED:")
    for item in failures:
        print("  -", item)
    sys.exit(1)
print("smoke test passed")
PYSMOKE
