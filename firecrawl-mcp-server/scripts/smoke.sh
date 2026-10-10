#!/usr/bin/env bash
# End-to-end check against the running container, over HTTP, exactly as an MCP client would.
#   scripts/smoke.sh              all steps, including a LIVE scrape of https://example.com and a LIVE search
#   scripts/smoke.sh --no-live    skip the live scrape and live search (steps 5 and 6)
# The bearer token (optional) is read from .env and never printed; 401 checks are skipped when it is unset.
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

live=1
for arg in "$@"; do
  case "${arg}" in
    --no-live) live=0 ;;
    -h | --help)
      echo "usage: scripts/smoke.sh [--no-live]"
      exit 0
      ;;
    *) die "unknown option: ${arg} (usage: scripts/smoke.sh [--no-live])" ;;
  esac
done
command -v python3 >/dev/null 2>&1 || die "python3 is required"
[ -f "${ENV_FILE}" ] || die ".env not found. Run scripts/setup.sh first."
token="$(env_get MCP_AUTH_TOKEN)"  # may be empty: auth disabled

export SMOKE_URL SMOKE_LIVE="${live}" SMOKE_TOKEN="${token}"
SMOKE_URL="$(mcp_url)"

python3 - <<'PYSMOKE'
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

URL = os.environ["SMOKE_URL"]
BASE = URL.rsplit("/mcp", 1)[0]
TOKEN = os.environ["SMOKE_TOKEN"]
LIVE = os.environ["SMOKE_LIVE"] == "1"
AUTHLINE = ("Authorization: Bearer " + TOKEN + "\r\n") if TOKEN else ""
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}
failures = []
counter = [0]


def http(method, url, body=None, token=None, timeout=120):
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def parse(text):
    """Stateless server answers with SSE ('data:' lines) or plain JSON."""
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    last = None
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                last = json.loads(payload)
    if last is None:
        raise RuntimeError("no data: line in response")
    return last


def rpc(method, params=None):
    counter[0] += 1
    payload = {"jsonrpc": "2.0", "id": counter[0], "method": method}
    if params is not None:
        payload["params"] = params
    status, raw = http("POST", URL, json.dumps(payload).encode(), TOKEN)
    if status != 200:
        raise RuntimeError(f"{method}: HTTP {status}")
    return parse(raw)


def check(label, ok, detail=""):
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


print("== 1. health")
status, body = http("GET", BASE + "/healthz")
check("/healthz is 200", status == 200, body.strip())

print("== 2. auth")
if TOKEN:
    status, _ = http("POST", URL, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode())
    check("/mcp without token is 401", status == 401, str(status))
    status, _ = http("POST", URL, b"{}", "wrong-" + TOKEN)
    check("/mcp with wrong token is 401", status == 401, str(status))
else:
    print("  [skip] auth disabled (no MCP_AUTH_TOKEN): 401 checks not applicable")

print("== 3. initialize")
init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "smoke", "version": "0.0.1"}})
info = init.get("result", {}).get("serverInfo", {})
check("initialize returns serverInfo", bool(info.get("name")), f"{info.get('name')} {info.get('version')}")

print("== 4. tools/list")
names = sorted(t["name"] for t in rpc("tools/list")["result"]["tools"])
check("tools/list contains firecrawl_scrape", "firecrawl_scrape" in names, f"{len(names)} tools")

print("== 5. live scrape of https://example.com")
if LIVE:
    res = rpc("tools/call", {"name": "firecrawl_scrape",
                             "arguments": {"url": "https://example.com", "formats": ["markdown"]}})
    result = res.get("result", {})
    text = "".join(c.get("text", "") for c in result.get("content", []))
    check("scrape result contains 'Example Domain'",
          "Example Domain" in text and not result.get("isError"), f"{len(text)} chars")
else:
    print("  [skip] --no-live")

print("== 6. live search (exercises the API request filter)")
if LIVE:
    res = rpc("tools/call", {"name": "firecrawl_search", "arguments": {"query": "firecrawl", "limit": 3}})
    result = res.get("result", {})
    text = "".join(c.get("text", "") for c in result.get("content", []))
    check("search returns no error and at least one http(s) URL",
          not result.get("isError") and ("http://" in text or "https://" in text), f"{len(text)} chars")
else:
    print("  [skip] --no-live")

print("== 7. client abort mid-stream, then health")
u = urlparse(URL)
body = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "tools/list"}).encode()
try:
    sock = socket.create_connection((u.hostname, u.port), timeout=10)
    req = (f"POST /mcp HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n{AUTHLINE}"
           "Content-Type: application/json\r\nAccept: application/json, text/event-stream\r\n"
           f"Content-Length: {len(body) + 100}\r\n\r\n").encode() + body  # body deliberately short
    sock.sendall(req)
    sock.close()  # abort with the request unfinished
    aborted = True
except OSError as exc:
    aborted = False
    print("  abort failed:", exc)
# Abort while the response is still being produced (a streamed tools/call in flight).
call = ({"name": "firecrawl_scrape", "arguments": {"url": "https://example.com", "formats": ["markdown"]}}
        if LIVE else None)
body2 = json.dumps({"jsonrpc": "2.0", "id": 98, "method": "tools/call" if call else "tools/list",
                    **({"params": call} if call else {})}).encode()
try:
    sock = socket.create_connection((u.hostname, u.port), timeout=10)
    hdr = (f"POST /mcp HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n{AUTHLINE}"
           f"Content-Type: application/json\r\nAccept: application/json, text/event-stream\r\n"
           f"Content-Length: {len(body2)}\r\n\r\n")
    sock.sendall(hdr.encode() + body2)
    sock.close()  # response not read: abort mid-response
except OSError as exc:
    aborted = False
    print("  response abort failed:", exc)
import time
time.sleep(1)
status, _ = http("GET", BASE + "/healthz")
check("/healthz still 200 after aborted request", aborted and status == 200, str(status))

print()
if failures:
    print("SMOKE TEST FAILED:")
    for item in failures:
        print("  -", item)
    sys.exit(1)
print("smoke test passed")
PYSMOKE
