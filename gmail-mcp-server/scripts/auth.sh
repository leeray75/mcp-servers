#!/usr/bin/env bash
# Google sign-in (first time, or after the token expires or is revoked).
#
# Preferred: if the server is running, open its own sign-in page
# (http://127.0.0.1:<HOST_PORT>/auth) and wait for the token file to change.
# Fallback: run a one-off container that prints the Google URL, which is opened
# automatically (published on 127.0.0.1:8400 only). Use --no-browser to just print URLs.
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

usage() {
  die "usage: scripts/auth.sh [--no-browser]"
}

no_browser=0
case "$#" in
  0) ;;
  1) if [ "$1" = "--no-browser" ]; then no_browser=1; else usage; fi ;;
  *) usage ;;
esac

WAIT_SECONDS=300
POLL_SECONDS=2

require_docker
[ -f "${ENV_FILE}" ] || die ".env not found. Run: cp .env.example .env  and fill in the values."
for name in GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET; do
  value="$(env_get "${name}")"
  if [ -z "${value}" ] || [[ "${value}" == replace-me* ]]; then
    die "${name} is not set in .env. Create a Desktop OAuth client first (see README)."
  fi
done
check_env

# Modification time of the token file (empty if absent). Never reads its contents.
token_stamp() {
  stat -c '%y' "${DATA_DIR}/token.json" 2>/dev/null || true
}

sign_in_via_server() {
  local url before waited=0
  url="http://127.0.0.1:$(host_port)/auth"
  before="$(token_stamp)"
  if [ "${no_browser}" -eq 1 ]; then
    info "Open this URL in your browser: ${url}"
  else
    info "Opening the sign-in page: ${url}"
    open_url "${url}"
  fi
  info "Waiting for you to approve access in the browser (up to ${WAIT_SECONDS}s)"
  while [ "${waited}" -lt "${WAIT_SECONDS}" ]; do
    sleep "${POLL_SECONDS}"
    waited=$((waited + POLL_SECONDS))
    if [ -n "$(token_stamp)" ] && [ "$(token_stamp)" != "${before}" ]; then
      info "Signed in. The running server uses the new token immediately."
      return 0
    fi
  done
  die "timed out waiting for sign-in. Run this script again, or open ${url} yourself. If the page did not load, the container may predate this feature: run scripts/rebuild.sh (rebuild.ps1 on Windows)."
}

sign_in_via_container() {
  local rc line url opened=0
  build_image
  ensure_data_dir
  info "Starting sign-in (publishing 127.0.0.1:${AUTH_PORT} on this machine only)"
  set +e
  compose run --rm -T -e PYTHONUNBUFFERED=1 -p "127.0.0.1:${AUTH_PORT}:${AUTH_PORT}" auth 2>&1 |
    while IFS= read -r line; do
      line="${line%$'\r'}"
      printf '%s\n' "${line}"
      if [ "${opened}" -eq 0 ] && [ "${no_browser}" -eq 0 ] &&
        [[ "${line}" =~ (https://accounts\.google\.com/[^[:space:]]+) ]]; then
        opened=1
        url="${BASH_REMATCH[1]}"
        open_url "${url}"
      fi
    done
  rc="${PIPESTATUS[0]}"
  set -e
  [ "${rc}" -eq 0 ] || exit "${rc}"
  info "Signed in. A running server picks up the new token automatically."
}

if server_running && auth_route_available; then
  sign_in_via_server
else
  if server_running; then
    warn "the running server has no /auth page (older image). Run scripts/rebuild.sh"
    warn "(rebuild.ps1 on Windows) to enable it. Using the one-off container for now."
  fi
  sign_in_via_container
fi
