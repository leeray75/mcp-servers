#!/usr/bin/env bash
# Interactively create (or update) .env. Prompts for the Google OAuth client, the
# MCP bearer token, and optional settings. Secrets are read silently and never printed.
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

if [ "$#" -gt 0 ]; then
  die "usage: scripts/setup.sh (takes no arguments)"
fi
{ [ -t 0 ] && [ -t 1 ]; } || die "setup.sh is interactive and needs a terminal."

readonly DEFAULT_PORT=8765
readonly DEFAULT_SCOPES="gmail.readonly gmail.compose"
TMP_FILE=""
cleanup() {
  [ -z "${TMP_FILE}" ] || rm -f "${TMP_FILE}"
}
trap cleanup EXIT
trap 'printf "\n"; die "cancelled; .env was not changed."' INT TERM

# A value is safe to write unquoted into an env file: no whitespace, quotes, #, $ or backslash.
is_safe_value() {
  [[ "$1" =~ ^[^[:space:]\"\'#\$\\]+$ ]]
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

# ask_value VAR PROMPT DEFAULT: visible input. Re-prompts until the validator accepts it.
# Validator is the function named by $VALIDATOR (set by the caller); it prints its own error.
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

validate_client_id() {
  if ! is_safe_value "$1" || [[ "$1" != *.apps.googleusercontent.com ]]; then
    warn "expected an ID ending in .apps.googleusercontent.com (no spaces or quotes)."
    return 1
  fi
  [[ "$1" != replace-me* ]] || { warn "that is still the placeholder."; return 1; }
}

validate_client_secret() {
  if ! is_safe_value "$1"; then
    warn "the secret must not contain spaces, quotes, #, \$ or backslashes."
    return 1
  fi
  [[ "$1" != replace-me* ]] || { warn "that is still the placeholder."; return 1; }
}

validate_token() {
  if ! is_safe_value "$1"; then
    warn "the token must not contain spaces, quotes, #, \$ or backslashes."
    return 1
  fi
  if [ "${#1}" -lt "${MIN_TOKEN_LENGTH}" ]; then
    warn "the token must be at least ${MIN_TOKEN_LENGTH} characters."
    return 1
  fi
}

validate_port() {
  if ! [[ "$1" =~ ^[0-9]{1,5}$ ]] || [ "$1" -lt 1024 ] || [ "$1" -gt 65535 ]; then
    warn "enter a port between 1024 and 65535."
    return 1
  fi
}

generate_token() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    # 32 random bytes as hex; no openssl needed.
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

printf 'Gmail MCP server setup\n'
printf 'Answers are written to %s. Secrets are not echoed.\n\n' "${ENV_FILE}"

existing_id=""
existing_secret=""
existing_token=""
existing_port=""
existing_scopes=""
if [ -f "${ENV_FILE}" ]; then
  existing_id="$(env_get GOOGLE_CLIENT_ID)"
  existing_secret="$(env_get GOOGLE_CLIENT_SECRET)"
  existing_token="$(env_get MCP_AUTH_TOKEN)"
  existing_port="$(env_get HOST_PORT)"
  existing_scopes="$(env_get GMAIL_SCOPES)"
  ask_yes_no ".env already exists. Update it (current values are offered as defaults)?" "y" ||
    die "left .env unchanged."
  [[ "${existing_id}" != replace-me* ]] || existing_id=""
  [[ "${existing_secret}" != replace-me* ]] || existing_secret=""
  [[ "${existing_token}" != replace-me* ]] || existing_token=""
fi

printf '\n1) Google OAuth client (Cloud Console > Google Auth Platform > Clients, type "Desktop app")\n'
VALIDATOR=validate_client_id
ask_value client_id "Client ID" "${existing_id}"

have_secret=0
[ -z "${existing_secret}" ] || have_secret=1
VALIDATOR=validate_client_secret
client_secret="${existing_secret}"
ask_secret client_secret "Client secret" "${have_secret}"

printf '\n2) MCP bearer token (Goose must send this; minimum %s characters)\n' "${MIN_TOKEN_LENGTH}"
mcp_token="${existing_token}"
if [ -n "${existing_token}" ]; then
  if ask_yes_no "Keep the current token?" "y"; then
    keep_token=1
  else
    keep_token=0
  fi
else
  keep_token=0
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

printf '\n3) Options\n'
VALIDATOR=validate_port
host_port="${existing_port:-${DEFAULT_PORT}}"
ask_value host_port "Host port (published on 127.0.0.1 only)" "${host_port}"

scopes="${existing_scopes:-${DEFAULT_SCOPES}}"
printf 'Permissions:\n  1) read-only (search/read mail)\n  2) read + create drafts (default)\n'
default_choice=2
[ "${scopes}" != "gmail.readonly" ] || default_choice=1
while true; do
  read -r -p "Choose 1 or 2 [${default_choice}]: " choice
  choice="${choice:-${default_choice}}"
  case "${choice}" in
    1) scopes="gmail.readonly"; break ;;
    2) scopes="${DEFAULT_SCOPES}"; break ;;
    *) printf 'Please enter 1 or 2.\n' ;;
  esac
done

if [ -f "${ENV_FILE}" ]; then
  ask_yes_no "Write these values to ${ENV_FILE}, replacing its contents?" "y" ||
    die "left .env unchanged."
fi

# Write atomically with owner-only permissions (modes are not enforced on /mnt/c; see README).
umask 077
TMP_FILE="$(mktemp "${ROOT_DIR}/.env.XXXXXX")"
{
  printf '# Generated by scripts/setup.sh. Git-ignored; never commit or share this file.\n'
  printf 'GOOGLE_CLIENT_ID=%s\n' "${client_id}"
  printf 'GOOGLE_CLIENT_SECRET=%s\n' "${client_secret}"
  printf 'MCP_AUTH_TOKEN=%s\n' "${mcp_token}"
  printf 'HOST_PORT=%s\n' "${host_port}"
  printf 'GMAIL_SCOPES=%s\n' "${scopes}"
} >"${TMP_FILE}"
mv -f "${TMP_FILE}" "${ENV_FILE}"
TMP_FILE=""
chmod 600 "${ENV_FILE}" 2>/dev/null || true

check_env
printf '\nSaved %s.\n\nNext steps:\n' "${ENV_FILE}"
printf '  1. scripts/auth.sh    (one-time Google sign-in; add yourself as a test user first if the app is in Testing mode)\n'
printf '  2. scripts/start.sh   (starts the server and prints the Goose config)\n'
