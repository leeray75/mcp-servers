#!/usr/bin/env bash
# Rebuild the image from scratch and recreate the container.
#   --keep-cache   reuse Docker's layer cache (faster; skips refreshing base layers)
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

use_cache=0
for arg in "$@"; do
  case "${arg}" in
    --keep-cache) use_cache=1 ;;
    -h | --help)
      echo "usage: scripts/rebuild.sh [--keep-cache]"
      exit 0
      ;;
    *) die "unknown option: ${arg} (usage: scripts/rebuild.sh [--keep-cache])" ;;
  esac
done

require_docker
check_env
require_firecrawl_network

if [ "${use_cache}" -eq 1 ]; then
  info "Building image (using cache)"
  compose build "${SERVICE}"
else
  info "Building image without cache"
  compose build --no-cache --pull "${SERVICE}"
fi

info "Recreating ${SERVICE}"
compose up -d --force-recreate "${SERVICE}"
wait_healthy
