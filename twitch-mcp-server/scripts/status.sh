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
  allow="$(env_get TWITCH_ALLOWED_BROADCASTER_IDS)"
  count=0
  if [ -n "${allow}" ]; then
    count="$(printf '%s' "${allow}" | tr ',' '\n' | grep -c .)"
  fi
  printf 'allowlist : %s broadcaster id(s) configured (ids not printed)\n' "${count}"
  # Names/states only - never credential values (R52). Group names are configuration, not secrets.
  if user_oauth_enabled; then
    printf 'user oauth: on\n'
  else
    printf 'user oauth: off\n'
  fi
  writes="$(env_get TWITCH_ENABLE_WRITES | tr '[:upper:]' '[:lower:]')"
  if [ "${writes}" = "true" ]; then
    printf 'writes    : ENABLED for groups: %s\n' "$(env_get TWITCH_WRITE_GROUPS)"
  else
    printf 'writes    : off (reads only)\n'
  fi
  # T6/T12: whether the listener speaks TLS, and the one URI the operator has to register. No
  # certificate content or key path is ever printed here (T9).
  if tls_configured; then
    printf 'tls       : on (https)\n'
  else
    printf 'tls       : off (plain http, loopback only)\n'
  fi
  if user_oauth_enabled; then
    printf 'register  : %s\n' "$(redirect_uri)"
  fi
else
  printf '.env      : MISSING (run scripts/setup.sh)\n'
fi

[ "${state}" = "running" ] || exit 1
