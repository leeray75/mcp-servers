#!/usr/bin/env bash
# End-to-end check against the running container, over HTTP, exactly as an MCP client would,
# using LIVE Discord data (the bot token in .env). Prints sizes only, never content.
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
# U33: with user OAuth on, also check the five account tools (status only; no sign-in is attempted).
if user_oauth_enabled; then export SMOKE_USER_OAUTH=1; else export SMOKE_USER_OAUTH=0; fi

python3 - <<'PYSMOKE'
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ["SMOKE_URL"]
BASE = URL.rsplit("/mcp", 1)[0]
TOKEN = os.environ["SMOKE_TOKEN"]
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
        with urllib.request.urlopen(req, timeout=60) as resp:
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

print("== list_guilds (sizes only)")
data, size = rpc("tools/call", {"name": "list_guilds", "arguments": {}})
result = data["result"]
text = result["content"][0]["text"] if result.get("content") else ""
if result.get("isError"):
    failures.append("list_guilds returned an error")
    print(f"  [FAIL] list_guilds: {len(text.encode())}B text, {size}B response")
else:
    structured = result.get("structuredContent") or {}
    guilds = structured.get("guilds", [])
    missing = structured.get("missing_allowlisted_guild_ids", [])
    print(f"  [ok ] list_guilds: text={len(text.encode())}B response={size}B "
          f"guilds={len(guilds)} missing={len(missing)}")

if os.environ.get("SMOKE_USER_OAUTH") == "1":
    print("== user OAuth (status only; no sign-in attempted)")
    account_tools = {
        "discord_account_status",
        "connect_discord_account",
        "disconnect_discord_account",
        "list_my_guilds",
        "get_guild_invite_link",
    }
    if len(names) > 19 or not account_tools <= set(names):
        failures.append("user OAuth tools are missing or the tool list is too long")
    data, size = rpc("tools/call", {"name": "discord_account_status", "arguments": {}})
    status_result = data["result"]
    if status_result.get("isError"):
        failures.append("discord_account_status returned an error")
        print(f"  [FAIL] discord_account_status: {size}B")
    else:
        state = (status_result.get("structuredContent") or {}).get("state")
        print(f"  [ok ] discord_account_status: state={state} response={size}B")

print()
if failures:
    print("SMOKE TEST FAILED:")
    for item in failures:
        print("  -", item)
    sys.exit(1)
print("smoke test passed")
PYSMOKE
