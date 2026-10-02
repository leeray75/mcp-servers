#!/usr/bin/env bash
# Stop and remove the container. The ./data volume (Google token) is kept.
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

if [ "$#" -gt 0 ]; then
  die "usage: scripts/stop.sh (takes no arguments)"
fi

require_docker
# `down` needs .env only to parse the compose file; fall back to an empty one so
# stop works even after the user deleted .env.
if [ -f "${ENV_FILE}" ]; then
  compose down --remove-orphans
else
  warn ".env not found; stopping the container directly"
  docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
fi
info "Stopped. Sign-in data in ./data was kept."
