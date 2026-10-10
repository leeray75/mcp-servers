# shellcheck shell=bash
# Shared helpers for the vision-mcp-server scripts. Source this file; do not execute it.
# Callers are expected to have run `set -euo pipefail`.

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${LIB_DIR}/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
SERVICE="vision-mcp"
CONTAINER="vision-mcp"
IMAGE="vision-mcp-server:local"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-90}"
MIN_TOKEN_LENGTH=32
DEFAULT_PORT=8766

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

# Run docker compose against this project, wherever the script is invoked from.
compose() {
  docker compose --project-directory "${ROOT_DIR}" "$@"
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
  local name value problems=0
  for name in VISION_API_KEY MCP_AUTH_TOKEN HOST_IMAGES_DIR; do
    value="$(env_get "${name}")"
    if [ -z "${value}" ]; then
      warn "${name} is missing or empty in .env"
      problems=1
    elif [[ "${value}" == replace-me* ]]; then
      warn "${name} still has its placeholder value in .env"
      problems=1
    fi
  done
  value="$(env_get MCP_AUTH_TOKEN)"
  if [ -n "${value}" ] && [ "${#value}" -lt "${MIN_TOKEN_LENGTH}" ]; then
    warn "MCP_AUTH_TOKEN must be at least ${MIN_TOKEN_LENGTH} characters (try: openssl rand -hex 32)"
    problems=1
  fi
  value="$(env_get HOST_IMAGES_DIR)"
  if [ -n "${value}" ] && [ ! -d "${value}" ]; then
    warn "HOST_IMAGES_DIR is not an existing directory (use the WSL spelling, e.g. /mnt/c/Users/me/Pictures)"
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
  printf 'http://127.0.0.1:%s/mcp' "$(host_port)"
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

Add it to Goose (extensions section of config.yaml):

  vision:
    name: vision
    description: Image understanding for text-only models (describe_image)
    type: streamable_http
    enabled: true
    uri: $(mcp_url)
    headers:
      Authorization: "Bearer <MCP_AUTH_TOKEN from your .env>"
    timeout: 300

(Your token is not printed here. See README for the exact format.)
EOF
}
