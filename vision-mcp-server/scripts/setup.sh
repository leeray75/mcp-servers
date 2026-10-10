#!/usr/bin/env bash
# Interactively create (or update) .env. Prompts for the vision backend key, the MCP bearer
# token and the images folder. Secrets are read silently and never printed.
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

if [ "$#" -gt 0 ]; then
  die "usage: scripts/setup.sh (takes no arguments)"
fi
{ [ -t 0 ] && [ -t 1 ]; } || die "setup.sh is interactive and needs a terminal."

TMP_FILE=""
cleanup() {
  [ -z "${TMP_FILE}" ] || rm -f "${TMP_FILE}"
}
trap cleanup EXIT
trap 'printf "\n"; die "cancelled; .env was not changed."' INT TERM

# A value is safe to write unquoted into an env file: no whitespace, quotes, #, or $.
# (Backslashes are fine: Windows paths are written literally.)
is_safe_value() {
  [[ "$1" =~ ^[^[:space:]\"\'#\$]+$ ]]
}

# ask_yes_no PROMPT DEFAULT(y|n) -> returns 0 for yes.
ask_yes_no() {
  local prompt="$1" default="$2" hint reply
  if [ "${default}" = "y" ]; then hint="Y/n"; else hint="y/N"; fi
  while true; do
    read -r -p "${prompt} [${hint}] " reply
    reply="${reply:-${default}}"
    case "${reply}" in
      [Yy] | [Yy][Ee][Ss]) return 0 ;;
      [Nn] | [Nn][Oo]) return 1 ;;
      *) printf 'Please answer y or n.\n' ;;
    esac
  done
}

# ask_value VAR PROMPT DEFAULT: visible input, re-prompted until $VALIDATOR accepts it.
ask_value() {
  local var="$1" prompt="$2" default="${3:-}" reply
  while true; do
    if [ -n "${default}" ]; then
      read -r -p "${prompt} [${default}]: " reply
      reply="${reply:-${default}}"
    else
      read -r -p "${prompt}: " reply
    fi
    if "${VALIDATOR}" "${reply}"; then
      printf -v "${var}" '%s' "${reply}"
      return 0
    fi
  done
}

# ask_secret VAR PROMPT HAVE_EXISTING(0|1): hidden input. Empty input keeps the existing value.
ask_secret() {
  local var="$1" prompt="$2" have_existing="$3" reply
  while true; do
    if [ "${have_existing}" -eq 1 ]; then
      read -r -s -p "${prompt} (press Enter to keep the current value): " reply
    else
      read -r -s -p "${prompt}: " reply
    fi
    printf '\n'
    if [ -z "${reply}" ] && [ "${have_existing}" -eq 1 ]; then
      return 0
    fi
    if "${VALIDATOR}" "${reply}"; then
      printf -v "${var}" '%s' "${reply}"
      return 0
    fi
  done
}

validate_secret() {
  if ! is_safe_value "$1"; then
    warn "the value must not contain spaces, quotes, # or \$."
    return 1
  fi
  [[ "$1" != replace-me* ]] || { warn "that is still the placeholder."; return 1; }
}

validate_token() {
  validate_secret "$1" || return 1
  if [ "${#1}" -lt "${MIN_TOKEN_LENGTH}" ]; then
    warn "the token must be at least ${MIN_TOKEN_LENGTH} characters."
    return 1
  fi
}

validate_dir() {
  if ! is_safe_value "$1" || [[ "$1" != /* ]]; then
    warn "enter an absolute WSL path without spaces, e.g. /mnt/c/Users/me/Pictures"
    return 1
  fi
  [ -d "$1" ] || { warn "that directory does not exist."; return 1; }
}

validate_windows_dir() {
  [ -z "$1" ] && return 0
  if ! is_safe_value "$1" || ! [[ "$1" =~ ^[A-Za-z]:[\\/] ]]; then
    warn "enter a Windows path without spaces like C:\\Users\\me\\Pictures (or leave empty)."
    return 1
  fi
}

validate_port() {
  if ! [[ "$1" =~ ^[0-9]{1,5}$ ]] || [ "$1" -lt 1024 ] || [ "$1" -gt 65535 ]; then
    warn "enter a port between 1024 and 65535."
    return 1
  fi
}

validate_url() {
  [[ "$1" =~ ^https?://[^[:space:]\"\'#\$]+$ ]] || { warn "enter an http(s) URL."; return 1; }
}

generate_token() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

printf 'Vision MCP server setup\n'
printf 'Answers are written to %s. Secrets are not echoed.\n\n' "${ENV_FILE}"

existing_key="" existing_token="" existing_port="" existing_url="" existing_model=""
existing_dir="" existing_win=""
if [ -f "${ENV_FILE}" ]; then
  existing_key="$(env_get VISION_API_KEY)"
  existing_token="$(env_get MCP_AUTH_TOKEN)"
  existing_port="$(env_get HOST_PORT)"
  existing_url="$(env_get VISION_BASE_URL)"
  existing_model="$(env_get VISION_MODEL)"
  existing_dir="$(env_get HOST_IMAGES_DIR)"
  existing_win="$(env_get WINDOWS_IMAGES_DIR)"
  ask_yes_no ".env already exists. Update it (current values are offered as defaults)?" "y" ||
    die "left .env unchanged."
  [[ "${existing_key}" != replace-me* ]] || existing_key=""
  [[ "${existing_token}" != replace-me* ]] || existing_token=""
  [[ "${existing_dir}" != /mnt/c/Users/YOU/* ]] || existing_dir=""
  [[ "${existing_win}" != C:\\Users\\YOU\\* ]] || existing_win=""
fi

printf '1) Vision backend (any OpenAI-compatible endpoint, e.g. your LiteLLM proxy)\n'
VALIDATOR=validate_url
base_url="${existing_url:-http://spark-8013.taileaa4a8.ts.net:4000/v1}"
ask_value base_url "Base URL" "${base_url}"
VALIDATOR=validate_secret
model="${existing_model:-qwen3.8-27b}"
ask_value model "Model name" "${model}"
have_key=0
[ -z "${existing_key}" ] || have_key=1
api_key="${existing_key}"
ask_secret api_key "API key" "${have_key}"

printf '\n2) MCP bearer token (Goose must send this; minimum %s characters)\n' "${MIN_TOKEN_LENGTH}"
mcp_token="${existing_token}"
keep_token=0
if [ -n "${existing_token}" ] && ask_yes_no "Keep the current token?" "y"; then
  keep_token=1
fi
if [ "${keep_token}" -eq 0 ]; then
  if ask_yes_no "Generate a random token for you?" "y"; then
    mcp_token="$(generate_token)"
    printf 'Generated. It is saved in .env (not shown here); copy it from there into Goose.\n'
  else
    VALIDATOR=validate_token
    ask_secret mcp_token "Token" 0
  fi
fi

printf '\n3) Images folder (the ONLY host folder the server can read; mounted read-only)\n'
VALIDATOR=validate_dir
images_dir="${existing_dir}"
ask_value images_dir "Folder (WSL path)" "${images_dir}"
VALIDATOR=validate_windows_dir
win_dir="${existing_win}"
if [ -z "${win_dir}" ] && [[ "${images_dir}" =~ ^/mnt/([a-z])/(.*)$ ]]; then
  drive="${BASH_REMATCH[1]}"
  rest="${BASH_REMATCH[2]//\//\\}"
  win_dir="${drive^^}:\\${rest}"
fi
ask_value win_dir "Same folder as Windows spells it (optional)" "${win_dir}"

printf '\n4) Options\n'
VALIDATOR=validate_port
host_port="${existing_port:-${DEFAULT_PORT}}"
ask_value host_port "Host port (published on 127.0.0.1 only)" "${host_port}"

if [ -f "${ENV_FILE}" ]; then
  ask_yes_no "Write these values to ${ENV_FILE}, replacing its contents?" "y" ||
    die "left .env unchanged."
fi

# Write atomically with owner-only permissions (modes are not enforced on /mnt/c; see README).
umask 077
TMP_FILE="$(mktemp "${ROOT_DIR}/.env.XXXXXX")"
{
  printf '# Generated by scripts/setup.sh. Git-ignored; never commit or share this file.\n'
  printf 'VISION_BASE_URL=%s\n' "${base_url}"
  printf 'VISION_MODEL=%s\n' "${model}"
  printf 'VISION_API_KEY=%s\n' "${api_key}"
  printf 'MCP_AUTH_TOKEN=%s\n' "${mcp_token}"
  printf 'HOST_IMAGES_DIR=%s\n' "${images_dir}"
  [ -z "${win_dir}" ] || printf 'WINDOWS_IMAGES_DIR=%s\n' "${win_dir}"
  printf 'HOST_PORT=%s\n' "${host_port}"
} >"${TMP_FILE}"
mv -f "${TMP_FILE}" "${ENV_FILE}"
TMP_FILE=""
chmod 600 "${ENV_FILE}" 2>/dev/null || true

check_env
printf '\nSaved %s.\n\nNext step:\n' "${ENV_FILE}"
printf '  scripts/start.sh   (builds and starts the server, then prints the Goose config)\n'
