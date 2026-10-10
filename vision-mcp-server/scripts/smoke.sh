#!/usr/bin/env bash
# End-to-end check against the running container, over HTTP, exactly as an MCP client would.
#   scripts/smoke.sh                 health + auth + tool list
#   scripts/smoke.sh IMAGE [QUESTION]  also calls describe_image (IMAGE as the server sees it)
# The bearer token is read from .env and never printed.
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

image="${1:-}"
question="${2:-Describe each shape with its color and position, and read any text exactly.}"

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
[ -f "${ENV_FILE}" ] || die ".env not found. Run scripts/setup.sh first."
token="$(env_get MCP_AUTH_TOKEN)"
[ -n "${token}" ] || die "MCP_AUTH_TOKEN is empty in .env"
url="$(mcp_url)"
base="${url%/mcp}"

code="$(curl -s -o /dev/null -w '%{http_code}' -m 10 "${base}/healthz" || true)"
[ "${code}" = "200" ] || die "GET /healthz returned ${code:-no response}; is the container running?"
info "healthz: 200"

code="$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST "${url}" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' || true)"
[ "${code}" = "401" ] || die "unauthenticated /mcp returned ${code}, expected 401"
info "auth: unauthenticated request rejected (401)"

rpc() {
  curl -s -m "${2:-300}" -X POST "${url}" \
    -H "Authorization: Bearer ${token}" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-06-18' \
    -d "$1"
}

tools="$(rpc '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' 20)"
python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print("tools : " + ", ".join(t["name"] for t in d["result"]["tools"]))' <<<"${tools}" ||
  die "tools/list failed"

if [ -n "${image}" ]; then
  info "calling describe_image (this takes a few seconds)"
  body="$(IMG="${image}" Q="${question}" python3 -c 'import json,os; print(json.dumps({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"describe_image","arguments":{"paths":[os.environ["IMG"]],"question":os.environ["Q"]}}}))')"
  rpc "${body}" 300 | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
r = d.get("result", {})
if r.get("isError"):
    print("tool error: " + r["content"][0]["text"]); sys.exit(1)
s = r["structuredContent"]
print("model :", s["model"], "| images:", s["images"], "| truncated:", s["truncated"])
print("answer:", s["description"])'
fi
info "smoke test passed"
