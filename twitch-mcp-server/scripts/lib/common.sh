# shellcheck shell=bash
# Shared helpers for the twitch-mcp-server scripts. Source this file; do not execute it.
# Callers are expected to have run `set -euo pipefail`.

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${LIB_DIR}/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
SERVICE="twitch-mcp"
CONTAINER="twitch-mcp"
IMAGE="twitch-mcp-server:local"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-90}"
MIN_TOKEN_LENGTH=32
DEFAULT_PORT=8770

info() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

# Verify Docker (Engine in WSL2, not Docker Desktop) and the compose plugin are usable.
require_docker() {
  command -v docker >/dev/null 2>&1 ||
    die "docker not found. Install Docker Engine inside WSL2 Ubuntu (see README)."
  docker info >/dev/null 2>&1 ||
    die "cannot reach the Docker daemon. Start it with: sudo service docker start"
  docker compose version >/dev/null 2>&1 ||
    die "the 'docker compose' plugin is missing (install docker-compose-plugin)."
}

# True when .env turns user identity on (TWITCH_USER_OAUTH=true). The value is compared, never printed.
user_oauth_enabled() {
  [ "$(env_get TWITCH_USER_OAUTH | tr '[:upper:]' '[:lower:]')" = "true" ]
}

# True when .env turns HTTPS on (docs/specs/tls-oauth-redirect, T1): both TLS variables set. Set
# alone, they are a configuration error that config.py refuses at startup; check_env warns first.
tls_configured() {
  [ -n "$(env_get TWITCH_TLS_CERT_FILE)" ] && [ -n "$(env_get TWITCH_TLS_KEY_FILE)" ]
}

# The scheme every URL this project prints or probes must use (T-D3): https exactly when the
# container is configured to serve TLS, so a printed endpoint can never disagree with the listener.
scheme() {
  if tls_configured; then
    printf 'https'
  else
    printf 'http'
  fi
}

# Run docker compose against this project, wherever the script is invoked from. With user OAuth on
# (R46/R48) the optional override is added: it mounts ./data for the token file and nothing else.
# With TLS configured the second override is added: it mounts ./certs read-only and sets the two
# container-side paths (T11). Both are opt-in, so the default deployment mounts nothing at all.
compose() {
  local -a overrides=()
  if user_oauth_enabled; then
    overrides+=(-f "${ROOT_DIR}/docker-compose.oauth.yml")
  fi
  if tls_configured; then
    overrides+=(-f "${ROOT_DIR}/docker-compose.tls.yml")
  fi
  docker compose --project-directory "${ROOT_DIR}" \
    -f "${ROOT_DIR}/docker-compose.yml" "${overrides[@]}" "$@"
}

# Print the value of KEY from .env without sourcing the file (it is data, not code).
env_get() {
  local key="$1"
  [ -f "${ENV_FILE}" ] || return 0
  { grep -E "^[[:space:]]*${key}=" "${ENV_FILE}" || true; } |
    tail -n 1 | tr -d '\r' |
    sed -E "s/^[[:space:]]*${key}=//; s/^[\"']//; s/[\"']\$//"
}

# Fail unless .env exists and every required variable is set to a real value.
# Only variable NAMES are ever printed, never values.
check_env() {
  [ -f "${ENV_FILE}" ] || die ".env not found. Run scripts/setup.sh (or: cp .env.example .env)."
  local value problems=0
  value="$(env_get MCP_AUTH_TOKEN)"
  if [ -z "${value}" ]; then
    warn "MCP_AUTH_TOKEN is missing or empty in .env"
    problems=1
  elif [[ "${value}" == replace-me* ]]; then
    warn "MCP_AUTH_TOKEN still has its placeholder value in .env"
    problems=1
  elif [ "${#value}" -lt "${MIN_TOKEN_LENGTH}" ]; then
    warn "MCP_AUTH_TOKEN must be at least ${MIN_TOKEN_LENGTH} characters (try: openssl rand -hex 32)"
    problems=1
  fi
  value="$(env_get TWITCH_CLIENT_ID)"
  if [ -z "${value}" ]; then
    warn "TWITCH_CLIENT_ID is missing or empty in .env"
    problems=1
  elif [[ "${value}" == replace-me* ]]; then
    warn "TWITCH_CLIENT_ID still has its placeholder value in .env"
    problems=1
  fi
  value="$(env_get TWITCH_CLIENT_SECRET)"
  if [ -z "${value}" ]; then
    warn "TWITCH_CLIENT_SECRET is missing or empty in .env"
    problems=1
  elif [[ "${value}" == replace-me* ]]; then
    warn "TWITCH_CLIENT_SECRET still has its placeholder value in .env"
    problems=1
  fi
  value="$(env_get TWITCH_ALLOWED_BROADCASTER_IDS)"
  if [ -z "${value}" ]; then
    warn "TWITCH_ALLOWED_BROADCASTER_IDS is missing or empty in .env (default-deny: at least one broadcaster id)"
    problems=1
  elif [[ "${value}" == replace-me* ]]; then
    warn "TWITCH_ALLOWED_BROADCASTER_IDS still has its placeholder value in .env"
    problems=1
  fi
  # R28: warn (never die) when the write kill switches are thrown - writes act in public.
  if [ "$(env_get TWITCH_ENABLE_WRITES | tr '[:upper:]' '[:lower:]')" = "true" ]; then
    warn "TWITCH_ENABLE_WRITES=true: write tools will be registered for the groups in TWITCH_WRITE_GROUPS"
    value="$(env_get TWITCH_WRITE_GROUPS)"
    if [ -z "${value}" ]; then
      warn "TWITCH_WRITE_GROUPS is empty, so no write group is enabled (this is the safe default)"
    fi
  fi
  # T1: the two TLS variables are all-or-nothing, and the folder they point at has to exist. The
  # names of the variables are printed, never a path or any certificate content (T9).
  value="$(env_get TWITCH_TLS_CERT_FILE)"
  if [ -n "${value}" ] && [ -z "$(env_get TWITCH_TLS_KEY_FILE)" ]; then
    warn "TWITCH_TLS_KEY_FILE is missing while TWITCH_TLS_CERT_FILE is set (both or neither)"
    problems=1
  fi
  value="$(env_get TWITCH_TLS_KEY_FILE)"
  if [ -n "${value}" ] && [ -z "$(env_get TWITCH_TLS_CERT_FILE)" ]; then
    warn "TWITCH_TLS_CERT_FILE is missing while TWITCH_TLS_KEY_FILE is set (both or neither)"
    problems=1
  fi
  if tls_configured && [ -z "$(ls -A "${ROOT_DIR}/certs" 2>/dev/null)" ]; then
    warn "TLS is configured but ${ROOT_DIR}/certs is empty - run scripts/make-cert.sh (or .ps1)"
    problems=1
  fi
  value="$(env_get HOST_PORT)"
  if [ -n "${value}" ] && ! [[ "${value}" =~ ^[0-9]{1,5}$ ]]; then
    warn "HOST_PORT must be a number"
    problems=1
  fi
  [ "${problems}" -eq 0 ] || die "fix the problems above in ${ENV_FILE} and try again."
}

host_port() {
  local port
  port="$(env_get HOST_PORT)"
  printf '%s' "${port:-${DEFAULT_PORT}}"
}

mcp_url() {
  printf '%s://127.0.0.1:%s/mcp' "$(scheme)" "$(host_port)"
}

# The exact Redirect URL to paste into the Twitch Developer Console (T-D6, T12). It is derived from
# the same two values the server itself uses, so what the operator registers and what the server
# sends cannot drift apart (T3).
redirect_uri() {
  printf '%s://127.0.0.1:%s/oauth/callback' "$(scheme)" "$(host_port)"
}

# Build the image (a cached no-op when nothing changed).
build_image() {
  info "Building image (cached layers are reused)"
  compose build "${SERVICE}"
}

container_state() {
  local out
  if out="$(docker inspect -f '{{.State.Status}}' "${CONTAINER}" 2>/dev/null)"; then
    printf '%s' "${out}"
  else
    printf 'not created'
  fi
}

container_health() {
  local out
  if out="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
    "${CONTAINER}" 2>/dev/null)"; then
    printf '%s' "${out}"
  else
    printf 'n/a'
  fi
}

# Block until the container reports healthy, or fail with recent logs.
wait_healthy() {
  info "Waiting for the container to become healthy (up to ${HEALTH_TIMEOUT_SECONDS}s)"
  local waited=0 state health
  while [ "${waited}" -lt "${HEALTH_TIMEOUT_SECONDS}" ]; do
    state="$(container_state)"
    health="$(container_health)"
    if [ "${state}" = "running" ] && [ "${health}" = "healthy" ]; then
      info "Healthy"
      return 0
    fi
    if [ "${state}" = "exited" ] || [ "${state}" = "dead" ]; then
      break
    fi
    sleep 2
    waited=$((waited + 2))
  done
  warn "container state=$(container_state) health=$(container_health); recent logs:"
  docker logs --tail 30 "${CONTAINER}" >&2 2>&1 || true
  die "the container did not become healthy."
}

print_goose_hint() {
  cat <<EOF

MCP endpoint: $(mcp_url)

Add it to Claude Code (project or user scope):

  claude mcp add --transport http twitch $(mcp_url) \\
    --header "Authorization: Bearer <MCP_AUTH_TOKEN from your .env>"

Add it to Goose (extensions section of config.yaml):

  twitch:
    name: twitch
    description: Allowlisted Twitch channels (reads; writes only when both write switches are on)
    type: streamable_http
    enabled: true
    uri: $(mcp_url)
    headers:
      Authorization: "Bearer <MCP_AUTH_TOKEN from your .env>"
    timeout: 60

(Your token is not printed here. See README for the exact format.)
EOF
}
