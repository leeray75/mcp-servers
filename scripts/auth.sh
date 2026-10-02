#!/usr/bin/env bash
# One-time (or repeat) Google sign-in. Prints a URL to open in your Windows browser;
# Google then redirects to http://127.0.0.1:8400/, which is published to loopback only.
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

if [ "$#" -gt 0 ]; then
  die "usage: scripts/auth.sh (takes no arguments)"
fi

require_docker
[ -f "${ENV_FILE}" ] || die ".env not found. Run: cp .env.example .env  and fill in the values."
for name in GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET; do
  value="$(env_get "${name}")"
  if [ -z "${value}" ] || [[ "${value}" == replace-me* ]]; then
    die "${name} is not set in .env. Create a Desktop OAuth client first (see README)."
  fi
done
check_env

build_image
ensure_data_dir

info "Starting sign-in (publishing 127.0.0.1:${AUTH_PORT} on this machine only)"
compose run --rm -p "127.0.0.1:${AUTH_PORT}:${AUTH_PORT}" auth

info "Signed in. A running server picks up the new token automatically."
