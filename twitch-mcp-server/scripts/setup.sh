#!/usr/bin/env bash
# Interactively create (or update) .env. You are asked for the Client ID, the Client Secret and the
# broadcaster allowlist; the MCP bearer token is generated for you by default. Secrets are never
# printed or echoed.
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

# Twitch Client IDs are 30 lowercase alphanumeric characters.
validate_client_id() {
  if ! [[ "$1" =~ ^[a-z0-9]{30}$ ]]; then
    warn "that does not look like a Twitch Client ID (30 lowercase alphanumeric characters)."
    return 1
  fi
}

# Twitch Client Secrets are 30 lowercase alphanumeric characters.
validate_client_secret() {
  if ! [[ "$1" =~ ^[a-z0-9]{30}$ ]]; then
    warn "that does not look like a Twitch Client Secret (30 lowercase alphanumeric characters)."
    return 1
  fi
}

# A comma-separated list of 1-100 Twitch user ids (1-20 digits each), no duplicates.
validate_allowlist() {
  local value="$1" part count=0 seen=" "
  [[ "${value}" =~ ^[0-9,[:space:]]+$ ]] || { warn "use digits and commas only."; return 1; }
  IFS=',' read -r -a parts <<<"${value// /}"
  for part in "${parts[@]}"; do
    [[ "${part}" =~ ^[0-9]{1,20}$ ]] || { warn "each broadcaster id must be 1-20 digits."; return 1; }
    [[ "${seen}" != *" ${part} "* ]] || { warn "duplicate broadcaster id."; return 1; }
    seen="${seen}${part} "
    count=$((count + 1))
  done
  [ "${count}" -ge 1 ] && [ "${count}" -le 100 ] || { warn "list 1 to 100 broadcaster ids."; return 1; }
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

printf 'Twitch MCP server setup\n'
printf 'Answers are written to %s. Secrets are not echoed.\n\n' "${ENV_FILE}"

existing_token="" existing_port="" existing_allow="" existing_client_id=""
existing_tls_cert="" existing_tls_key=""
if [ -f "${ENV_FILE}" ]; then
  existing_token="$(env_get MCP_AUTH_TOKEN)"
  existing_port="$(env_get HOST_PORT)"
  existing_allow="$(env_get TWITCH_ALLOWED_BROADCASTER_IDS)"
  existing_client_id="$(env_get TWITCH_CLIENT_ID)"
  # T1: the TLS pair is load-bearing once configured (it decides the scheme of the URL registered
  # with Twitch), so re-running setup.sh must not silently drop it.
  existing_tls_cert="$(env_get TWITCH_TLS_CERT_FILE)"
  existing_tls_key="$(env_get TWITCH_TLS_KEY_FILE)"
  ask_yes_no ".env already exists. Update it (current values are offered as defaults)?" "y" ||
    die "left .env unchanged."
  [[ "${existing_token}" != replace-me* ]] || existing_token=""
  [[ "${existing_allow}" != replace-me* ]] || existing_allow=""
  [[ "${existing_client_id}" != replace-me* ]] || existing_client_id=""
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

printf '\n2) Twitch application credentials (https://dev.twitch.tv/console/apps)\n'
client_id="${existing_client_id}"
while true; do
  read -r -p "Client ID [${client_id:-none}]: " reply
  reply="${reply:-${client_id}}"
  if [ -n "${reply}" ] && validate_client_id "${reply}"; then
    client_id="${reply}"
    break
  fi
done
printf '   The Client Secret is not echoed.\n'
while true; do
  read -r -s -p "Client Secret: " client_secret
  printf '\n'
  if [ -n "${client_secret}" ] && is_safe_value "${client_secret}" &&
    [[ "${client_secret}" != replace-me* ]] && validate_client_secret "${client_secret}"; then
    break
  fi
  client_secret=""
done

printf '\n3) Broadcaster allowlist (the only channels the server will ever read; default-deny)\n'
printf '   Twitch user ids, not logins. Look one up with https://twitchtools.dev/id-lookup\n'
allow=""
while true; do
  read -r -p "Broadcaster ids, comma-separated [${existing_allow:-none}]: " reply
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

# R46: optional user identity. Off unless the human answers yes.
printf '\n5) Optional: user identity (sign in as yourself for subscribers, moderators, bans, automod)\n'
printf '   Add this exact Redirect URL in the Twitch Developer Console:\n'
printf '     %s\n' "$(redirect_uri)"
if ! tls_configured; then
  printf '   Twitch rejects a plain-http Redirect URL, so the server has to serve TLS first: run\n'
  printf '   scripts/make-cert.sh (Windows: scripts/make-cert.ps1), then add to .env\n'
  printf '     TWITCH_TLS_CERT_FILE=/certs/server.crt\n'
  printf '     TWITCH_TLS_KEY_FILE=/certs/server.key\n'
  printf '   and restart. The URL above then reads https:// and can be registered.\n'
fi
printf '\n'
oauth_enabled=0
oauth_scopes=""
if ask_yes_no "Enable user OAuth (TWITCH_USER_OAUTH)?" "n"; then
  oauth_enabled=1
  while true; do
    read -r -p "OAuth scopes, space-delimited (see README; leave blank for none) [user:read:email]: " reply
    reply="${reply:-user:read:email}"
    if [ -n "${reply}" ] && [[ "${reply}" =~ ^[A-Za-z0-9:_-]+([[:space:]]+[A-Za-z0-9:_-]+)*$ ]]; then
      oauth_scopes="${reply}"
      break
    fi
    warn "scopes are space-separated names using letters, digits, underscores, colons and dashes."
  done
fi

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
  printf 'TWITCH_CLIENT_ID=%s\n' "${client_id}"
  printf 'TWITCH_CLIENT_SECRET=%s\n' "${client_secret}"
  printf 'TWITCH_ALLOWED_BROADCASTER_IDS=%s\n' "${allow}"
  printf 'HOST_PORT=%s\n' "${host_port}"
  if [ -n "${existing_tls_cert}" ] && [ -n "${existing_tls_key}" ]; then
    printf 'TWITCH_TLS_CERT_FILE=%s\n' "${existing_tls_cert}"
    printf 'TWITCH_TLS_KEY_FILE=%s\n' "${existing_tls_key}"
  fi
  if [ "${oauth_enabled}" -eq 1 ]; then
    printf 'TWITCH_USER_OAUTH=true\n'
    printf 'TWITCH_OAUTH_SCOPES=%s\n' "${oauth_scopes}"
  fi
} >"${TMP_FILE}"
mv -f "${TMP_FILE}" "${ENV_FILE}"
TMP_FILE=""
chmod 600 "${ENV_FILE}" 2>/dev/null || true
client_secret=""
if [ "${oauth_enabled}" -eq 1 ]; then
  # R48/V6: the token is kept in the Docker named volume twitch-mcp-data. No host folder is created.
  printf 'The user token is kept in the Docker volume twitch-mcp-data. It is removed only by disconnect_twitch_account or: docker volume rm twitch-mcp-data\n'
fi

check_env
printf '\nSaved %s.\n\nNext step:\n' "${ENV_FILE}"
printf '  scripts/start.sh   (builds and starts the server, then prints the client config)\n'
