#!/usr/bin/env bash
# End-to-end check against the running container, over HTTP, exactly as an MCP client would,
# using LIVE ESPN data. Prints the byte size of every response.
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

python3 - <<'PYSMOKE'
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

URL = os.environ["SMOKE_URL"]
BASE = URL.rsplit("/mcp", 1)[0]
TOKEN = os.environ["SMOKE_TOKEN"]
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}
failures: list[str] = []
sizes: list[tuple[str, int, int]] = []
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


def call(label: str, name: str, args: dict, expect_error: bool = False):
    data, size = rpc("tools/call", {"name": name, "arguments": args})
    result = data["result"]
    text = result["content"][0]["text"] if result.get("content") else ""
    is_error = bool(result.get("isError"))
    ok = is_error if expect_error else not is_error
    text_bytes = len(text.encode())
    sizes.append((label, text_bytes, size))
    status = "ok " if ok else "FAIL"
    first = text.splitlines()[0][:90] if text else ""
    print(f"  [{status}] {label:<44} text={text_bytes:>6}B response={size:>7}B  {first}")
    if not ok:
        failures.append(f"{label}: {text[:200]}")
    return result, text


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
print(f"  {len(names)} tools ({size}B): {', '.join(names)}")
if len(names) != 11:
    failures.append("expected exactly 11 tools")

print("== list_leagues")
call("list_leagues", "list_leagues", {})

print("== scoreboards (today)")
for league in ("nfl", "nba", "mlb", "nhl", "epl"):
    result, text = call(f"get_scoreboard {league}", "get_scoreboard", {"league": league})
    sc = result.get("structuredContent") or {}
    if sc.get("count") == 0:
        print(f"      empty day handled: {sc.get('note', '')[:100]}")
    meta = sc.get("meta", {})
    if not meta.get("as_of"):
        failures.append(f"{league}: missing meta.as_of")

print("== game detail at all three levels (finished or live games, two or more sports)")
found: list[tuple[str, str, str]] = []
sports_seen: set[str] = set()
for league in ("nfl", "mlb", "nhl", "nba", "wnba", "epl"):
    for back in range(0, 12):
        day = (date.today() - timedelta(days=back)).isoformat()
        data, _ = rpc("tools/call", {"name": "get_scoreboard",
                                     "arguments": {"league": league, "date": day}})
        sc = data["result"].get("structuredContent") or {}
        games = [g for g in sc.get("games", []) if g["status"]["state"] in ("in", "post")]
        if games:
            found.append((league, games[0]["id"], games[0].get("short_name", "")))
            sports_seen.add(league)
            break
    if len(found) >= 3:
        break
if len(sports_seen) < 2:
    failures.append("could not find games from two sports for get_game")
for league, game_id, short in found:
    for detail in ("summary", "performers", "boxscore"):
        call(f"get_game {league} {game_id} {detail}", "get_game",
             {"league": league, "game_id": game_id, "detail": detail})

print("== odds")
call("get_odds nfl (today)", "get_odds", {"league": "nfl"})
if found:
    call(f"get_odds {found[0][0]} game_id", "get_odds", {"league": found[0][0], "game_id": found[0][1]})

print("== standings")
for league in ("nfl", "nba", "epl"):
    call(f"get_standings {league}", "get_standings", {"league": league})

print("== teams, schedule, news")
call("find_team nfl bills", "find_team", {"league": "nfl", "query": "bills"})
call("get_team_schedule nfl BUF", "get_team_schedule", {"league": "nfl", "team": "BUF", "limit": 5})
call("get_news nfl", "get_news", {"league": "nfl", "limit": 5})
print("== players")
call("find_player nfl skattebu (typo)", "find_player", {"league": "nfl", "query": "skattebu"})
call("get_player_stats nfl 4696981", "get_player_stats", {"league": "nfl", "player": "4696981"})
call("get_player_gamelog nfl skattebo 2025", "get_player_gamelog",
     {"league": "nfl", "player": "skattebo", "season": "2025"})

print("== rejection checks")
call("get_game bad game_id (expect error)", "get_game", {"league": "nfl", "game_id": "../1"}, True)
call("get_scoreboard bad league (expect error)", "get_scoreboard", {"league": "cricket"}, True)

print("== resources")
data, size = rpc("resources/read", {"uri": "sports://guide"})
print(f"  sports://guide: {size}B")
data, size = rpc("resources/read", {"uri": "sports://leagues/epl/teams"})
print(f"  sports://leagues/epl/teams: {size}B")

print()
if failures:
    print("SMOKE TEST FAILED:")
    for item in failures:
        print("  -", item)
    sys.exit(1)
print("smoke test passed")
PYSMOKE
