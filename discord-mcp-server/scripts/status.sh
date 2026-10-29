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
  allow="$(env_get DISCORD_ALLOWED_GUILD_IDS)"
  count=0
  if [ -n "${allow}" ]; then
    count="$(printf '%s' "${allow}" | tr ',' '\n' | grep -c .)"
  fi
  printf 'allowlist : %s guild id(s) configured (ids not printed)\n' "${count}"
else
  printf '.env      : MISSING (run scripts/setup.sh)\n'
fi

[ "${state}" = "running" ] || exit 1
