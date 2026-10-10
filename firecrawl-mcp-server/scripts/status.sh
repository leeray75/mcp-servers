#!/usr/bin/env bash
# Report container state, health, endpoint and configuration presence (never secret values).
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

if [ "$#" -gt 0 ]; then
  die "usage: scripts/status.sh (takes no arguments)"
fi

require_docker

state="$(container_state)"
printf 'container : %s\n' "${state}"
printf 'health    : %s\n' "$(container_health)"
printf 'endpoint  : %s\n' "$(mcp_url)"

if [ -f "${ENV_FILE}" ]; then
  printf '.env      : present\n'
  upstream="$(env_get FIRECRAWL_API_URL)"
  printf 'upstream  : %s\n' "${upstream:-http://api:3002 (default)}"
  if docker network inspect "${FIRECRAWL_NETWORK}" >/dev/null 2>&1; then
    printf 'network   : %s present\n' "${FIRECRAWL_NETWORK}"
  else
    printf 'network   : %s MISSING (Firecrawl stack down?)\n' "${FIRECRAWL_NETWORK}"
  fi
else
  printf '.env      : MISSING (run scripts/setup.sh)\n'
fi

[ "${state}" = "running" ] || exit 1
