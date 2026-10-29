#!/usr/bin/env bash
# Interactively create (or update) .env. You are asked for the bot token and the guild allowlist;
# the MCP bearer token is generated for you by default. Secrets are never printed or echoed.
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

validate_mcp_token() {
  if ! is_safe_value "$1"; then
    warn "the value must not contain spaces, quotes, # or \$."
    return 1
  fi
  [[ "$1" != replace-me* ]] || { warn "that is still the placeholder."; return 1; }
  if [ "${#1}" -lt "${MIN_TOKEN_LENGTH}" ]; then
    warn "the token must be at least ${MIN_TOKEN_LENGTH} characters."
    return 1
  fi
}

# Discord bot tokens are three dot-separated segments of [A-Za-z0-9_-].
validate_bot_token() {
  if ! [[ "$1" =~ ^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){2}$ ]]; then
    warn "that does not look like a Discord bot token (three dot-separated segments)."
    return 1
  fi
}

# A comma-separated list of 1-100 snowflakes (15-21 digits each), no duplicates.
validate_allowlist() {
  local value="$1" part count=0 seen=" "
  [[ "${value}" =~ ^[0-9,[:space:]]+$ ]] || { warn "use digits and commas only."; return 1; }
  IFS=',' read -r -a parts <<<"${value// /}"
  for part in "${parts[@]}"; do
    [[ "${part}" =~ ^[0-9]{15,21}$ ]] || { warn "each guild id must be 15-21 digits."; return 1; }
    [[ "${seen}" != *" ${part} "* ]] || { warn "duplicate guild id."; return 1; }
    seen="${seen}${part} "
    count=$((count + 1))
  done
  [ "${count}" -ge 1 ] && [ "${count}" -le 100 ] || { warn "list 1 to 100 guild ids."; return 1; }
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
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

printf 'Discord MCP server setup\n'
printf 'Answers are written to %s. Secrets are not echoed.\n\n' "${ENV_FILE}"

existing_token="" existing_port="" existing_allow=""
if [ -f "${ENV_FILE}" ]; then
  existing_token="$(env_get MCP_AUTH_TOKEN)"
  existing_port="$(env_get HOST_PORT)"
  existing_allow="$(env_get DISCORD_ALLOWED_GUILD_IDS)"
  ask_yes_no ".env already exists. Update it (current values are offered as defaults)?" "y" ||
    die "left .env unchanged."
  [[ "${existing_token}" != replace-me* ]] || existing_token=""
  [[ "${existing_allow}" != replace-me* ]] || existing_allow=""
fi

printf '1) MCP bearer token (MCP clients must send this; minimum %s characters)\n' "${MIN_TOKEN_LENGTH}"
mcp_token="${existing_token}"
keep_token=0
if [ -n "${existing_token}" ] && ask_yes_no "Keep the current token?" "y"; then
  keep_token=1
fi
if [ "${keep_token}" -eq 0 ]; then
  if ask_yes_no "Generate a random token for you?" "y"; then
    mcp_token="$(generate_token)"
    printf 'Generated. It is saved in .env (not shown here); copy it from there into your client.\n'
  else
    while true; do
      read -r -s -p "Token: " mcp_token
      printf '\n'
      validate_mcp_token "${mcp_token}" && break
    done
  fi
fi

printf '\n2) Discord bot token (Developer Portal > Bot > Reset Token; it is never echoed)\n'
bot_token=""
while true; do
  read -r -s -p "Bot token: " bot_token
  printf '\n'
  if [ -n "${bot_token}" ] && validate_bot_token "${bot_token}"; then
    break
  fi
  bot_token=""
done

printf '\n3) Guild allowlist (the only servers the bot will ever read; default-deny)\n'
printf '   Enable Developer Mode in Discord, then right-click a server > Copy Server ID.\n'
allow=""
while true; do
  read -r -p "Guild ids, comma-separated [${existing_allow:-none}]: " reply
  reply="${reply:-${existing_allow}}"
  if [ -n "${reply}" ] && validate_allowlist "${reply}"; then
    allow="${reply// /}"
    break
  fi
done

printf '\n4) Options\n'
host_port="${existing_port:-${DEFAULT_PORT}}"
while true; do
  read -r -p "Host port (published on 127.0.0.1 only) [${host_port}]: " reply
  reply="${reply:-${host_port}}"
  if validate_port "${reply}"; then
    host_port="${reply}"
    break
  fi
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
  printf 'MCP_AUTH_TOKEN=%s\n' "${mcp_token}"
  printf 'DISCORD_BOT_TOKEN=%s\n' "${bot_token}"
  printf 'DISCORD_ALLOWED_GUILD_IDS=%s\n' "${allow}"
  printf 'HOST_PORT=%s\n' "${host_port}"
} >"${TMP_FILE}"
mv -f "${TMP_FILE}" "${ENV_FILE}"
TMP_FILE=""
chmod 600 "${ENV_FILE}" 2>/dev/null || true
bot_token=""

check_env
printf '\nSaved %s.\n\nNext step:\n' "${ENV_FILE}"
printf '  scripts/start.sh   (builds and starts the server, then prints the client config)\n'
