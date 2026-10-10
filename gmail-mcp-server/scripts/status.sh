#!/usr/bin/env bash
# Report container state, health, and whether a Google token exists (never its contents).
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
else
  printf '.env      : MISSING (cp .env.example .env)\n'
fi

# The data directory may be owned by the container user, so ask the container.
if [ "${state}" = "running" ]; then
  if docker exec "${CONTAINER}" test -f /data/token.json; then
    printf 'google    : token file present (sign-in done)\n'
  else
    printf 'google    : no token file (open http://127.0.0.1:%s/auth or run scripts/auth.sh)\n' "$(host_port)"
  fi
elif docker image inspect "${IMAGE}" >/dev/null 2>&1 && [ -f "${ENV_FILE}" ]; then
  if compose run --rm --no-deps -T --entrypoint test "${SERVICE}" -f /data/token.json >/dev/null 2>&1; then
    printf 'google    : token file present (sign-in done)\n'
  else
    printf 'google    : no token file (run scripts/auth.sh)\n'
  fi
else
  printf 'google    : unknown (container not running and image not built)\n'
fi

[ "${state}" = "running" ] || exit 1
