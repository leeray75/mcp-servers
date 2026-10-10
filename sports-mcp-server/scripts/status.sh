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
  upstream="$(env_get ESPN_BASE_URL)"
  zone="$(env_get DEFAULT_TIMEZONE)"
  printf 'upstream  : %s\n' "${upstream:-https://site.api.espn.com (default)}"
  printf 'time zone : %s\n' "${zone:-America/New_York (default)}"
else
  printf '.env      : MISSING (run scripts/setup.sh)\n'
fi

[ "${state}" = "running" ] || exit 1
