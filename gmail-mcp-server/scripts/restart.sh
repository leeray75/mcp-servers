#!/usr/bin/env bash
# Restart the container and wait until it is healthy.
#   --recreate   recreate the container so changes to .env take effect
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

recreate=0
for arg in "$@"; do
  case "${arg}" in
    --recreate) recreate=1 ;;
    -h | --help)
      echo "usage: scripts/restart.sh [--recreate]"
      exit 0
      ;;
    *) die "unknown option: ${arg} (usage: scripts/restart.sh [--recreate])" ;;
  esac
done

require_docker
check_env

if [ "${recreate}" -eq 1 ] || [ "$(container_state)" = "not created" ]; then
  [ "${recreate}" -eq 1 ] || info "Container does not exist yet; creating it"
  build_image
  ensure_data_dir
  compose up -d --force-recreate "${SERVICE}"
else
  info "Restarting ${SERVICE}"
  compose restart "${SERVICE}"
fi
wait_healthy
