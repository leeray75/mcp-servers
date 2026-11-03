#!/usr/bin/env bash
# Create the TLS material the OAuth redirect needs: a local certificate authority and a leaf for
# CN=localhost with SAN DNS:localhost,IP:127.0.0.1 (T-D5), signed by that CA. Twitch's developer
# console refuses a plain-http Redirect URL, so the server has to serve https to be sign-in-able at
# all (docs/specs/tls-oauth-redirect).
#
#   scripts/make-cert.sh            create what is missing, reuse a valid CA and leaf
#   scripts/make-cert.sh --force    reissue both, even if they are still valid
#
# Everything lands in the git-ignored ./certs, which docker-compose.tls.yml mounts read-only at
# /certs (T10, T11). Only the CA path and its fingerprint are ever printed - no key material, ever
# (T10, T9). Trust the CA once (Windows, current user, no admin rights):
#
#   certutil -addstore -user Root certs\ca.crt
#
# Without that the browser interstitials the callback, which is exactly the click-through this
# script exists to avoid (T-D4).
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

FORCE=0
for arg in "$@"; do
  case "${arg}" in
    --force) FORCE=1 ;;
    *) die "usage: scripts/make-cert.sh [--force]" ;;
  esac
done

command -v openssl >/dev/null 2>&1 ||
  die "openssl is required (in WSL2: sudo apt-get install openssl)"

CERTS_DIR="${ROOT_DIR}/certs"
CA_KEY="${CERTS_DIR}/ca.key"
CA_CRT="${CERTS_DIR}/ca.crt"
LEAF_KEY="${CERTS_DIR}/server.key"
LEAF_CRT="${CERTS_DIR}/server.crt"
CA_DAYS=3650  # ten years: re-importing a CA into the trust store is the annoying part (T-D5)
LEAF_DAYS=398 # a leaf's validity ceiling; rotate by re-running this script
CA_SUBJECT="/O=Twitch MCP local/CN=Twitch MCP local CA"
EXT_FILE=""
cleanup() { [ -z "${EXT_FILE}" ] || rm -f "${EXT_FILE}" ; }
trap cleanup EXIT INT TERM

# Owner-only from the moment of creation: a private key is never briefly world-readable (T4, T-D7).
umask 077
mkdir -p "${CERTS_DIR}"
chmod 700 "${CERTS_DIR}" 2>/dev/null || true

if [ ! -f "${CA_CRT}" ] || [ ! -f "${CA_KEY}" ] || [ "${FORCE}" -eq 1 ]; then
  info "Creating the local CA (valid ${CA_DAYS} days)"
  openssl genrsa -out "${CA_KEY}" 3072 >/dev/null 2>&1
  openssl req -x509 -new -sha256 -days "${CA_DAYS}" -key "${CA_KEY}" -subj "${CA_SUBJECT}" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -out "${CA_CRT}" >/dev/null 2>&1
else
  info "Reusing the existing local CA"
fi

# Reuse a leaf that still verifies and has more than 30 days left, so restarting the server does not
# reissue a certificate the browser has already been shown.
leaf_is_current() {
  [ -f "${LEAF_CRT}" ] && [ -f "${LEAF_KEY}" ] || return 1
  openssl verify -CAfile "${CA_CRT}" "${LEAF_CRT}" >/dev/null 2>&1 || return 1
  openssl x509 -checkend $((30 * 86400)) -noout -in "${LEAF_CRT}" >/dev/null 2>&1
}

if [ "${FORCE}" -eq 0 ] && leaf_is_current; then
  info "Reusing the existing leaf (still valid, signed by this CA)"
else
  info "Issuing the loopback leaf for localhost / 127.0.0.1 (valid ${LEAF_DAYS} days)"
  openssl genrsa -out "${LEAF_KEY}" 2048 >/dev/null 2>&1
  openssl req -new -key "${LEAF_KEY}" -subj "/CN=localhost" -out "${CERTS_DIR}/server.csr" \
    >/dev/null 2>&1
  EXT_FILE="$(mktemp)"
  # Chrome and Edge require a SAN, and 127.0.0.1 has to be an IP SAN because the registered URI uses
  # the literal address rather than the hostname (T-D5). serverAuth keeps it a web-server cert only.
  cat >"${EXT_FILE}" <<'EXTCNF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:localhost,IP:127.0.0.1
EXTCNF
  openssl x509 -req -in "${CERTS_DIR}/server.csr" -CA "${CA_CRT}" -CAkey "${CA_KEY}" \
    -CAcreateserial -days "${LEAF_DAYS}" -sha256 -extfile "${EXT_FILE}" \
    -out "${LEAF_CRT}" >/dev/null 2>&1
  rm -f "${CERTS_DIR}/server.csr"
fi

chmod 600 "${CA_KEY}" "${LEAF_KEY}" 2>/dev/null || true
chmod 644 "${CA_CRT}" "${LEAF_CRT}" 2>/dev/null || true

# The server does not check the key's permissions (S5): keeping the key owner-only is yours. The chmod
# above sets 600; on a Windows drive under WSL the NTFS ACL is the real control instead.

printf '\n'
info "Certificate authority : ${CA_CRT}"
info "CA SHA-256 fingerprint: $(openssl x509 -in "${CA_CRT}" -noout -fingerprint -sha256 |
  cut -d= -f2)"
printf '\n'
printf 'Next steps\n'
printf '  1) Trust the CA for this user only (no admin rights), then no certificate warning appears:\n'
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*)
    printf '       certutil -addstore -user Root %s\\ca.crt\n' "${ROOT_DIR//\//\\}"
    ;;
  Darwin)
    printf '       security add-trusted-cert -k ~/Library/Keychains/login.keychain-db %s/ca.crt\n' \
      "${CERTS_DIR}"
    ;;
  *)
    printf '       Windows : certutil -addstore -user Root <repo>\\certs\\ca.crt\n'
    printf '       macOS   : security add-trusted-cert -k \\\n'
    printf '                   ~/Library/Keychains/login.keychain-db <repo>/certs/ca.crt\n'
    printf '       (run scripts/make-cert.ps1 on Windows to have it done for you)\n'
    ;;
esac
printf '  2) Put these two lines in .env (paths inside the container):\n'
printf '       TWITCH_TLS_CERT_FILE=/certs/server.crt\n'
printf '       TWITCH_TLS_KEY_FILE=/certs/server.key\n'
printf '  3) scripts/restart.sh\n'
# The scheme is literal here on purpose (reviewer MAJOR 4). This script is the step *before* .env
# carries the TLS variables, so redirect_uri() - which reads .env to decide the scheme - would still
# say http at this exact moment and hand the operator the one URI Twitch's console rejects. The port
# still comes from .env, so the only thing fixed is https, which is what this script exists to enable.
# scripts/status.sh stays the derived answer once .env is configured (T-D6, T12).
printf '  4) Register exactly this OAuth Redirect URL at https://dev.twitch.tv/console/apps\n'
printf '       https://127.0.0.1:%s/oauth/callback\n\n' "$(host_port)"
