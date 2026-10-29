#!/usr/bin/env bash
# Build (if needed) and start the Discord MCP server, then wait until it is healthy.
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

if [ "$#" -gt 0 ]; then
  die "usage: scripts/start.sh (takes no arguments)"
fi

require_docker
check_env
build_image

info "Starting ${SERVICE}"
compose up -d "${SERVICE}"
wait_healthy
print_goose_hint
