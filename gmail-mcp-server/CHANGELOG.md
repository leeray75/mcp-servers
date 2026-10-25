# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Server-hosted Google sign-in: `GET /auth` redirects to Google (state, PKCE S256,
  `access_type=offline`, `prompt=consent`) and `GET /oauth2callback` stores the token and
  resets the cached Gmail service, so re-authentication needs no restart. Pending attempts are
  in memory, expire after 10 minutes and are capped at 8.
- `HOST_PORT` setting (also passed into the container): used for the OAuth redirect URI and
  the sign-in link; the auth error returned to MCP clients now contains
  `http://127.0.0.1:<HOST_PORT>/auth`.
- `scripts/auth.sh [--no-browser]` prefers the running server, opens the browser
  automatically (`wslview`, `explorer.exe`, `xdg-open`, else prints the URL) and waits for the
  token file to change; `open_url` and `server_running` helpers in `scripts/lib/common.sh`.
- `GmailClient.reset()`; redaction of Google authorization codes in logs.
- `auth_status` tool (seventh tool, read-only): reports `state` (`signed_in`, `not_signed_in`,
  `token_unreadable`, `scopes_insufficient`, `rejected`), granted and missing scopes, token age,
  the time Google last rejected the credentials, warnings (including the 7-day Testing-mode expiry
  from six days after sign-in), the next step and per-OS open commands. It never contacts Google
  and never returns credentials.
- Four static MCP resources: `gmail://auth/status` (the `auth_status` payload),
  `gmail://guide/auth` (ordered sign-in procedure for agents), `gmail://guide/search` (Gmail search
  syntax) and `gmail://guide/limits` (capabilities, limits, safety).
- URL-mode elicitation of the sign-in page for clients on MCP 2026-07-28 that declare
  `elicitation.url` on the request (`InputRequiredResult`, `mode: "url"`), with the plain `/auth`
  URL only. Earlier revisions get the `AUTH_REQUIRED` text. Controlled by `AUTH_URL_ELICITATION` (default `true`).
- Stable `AUTH_REQUIRED` tool-error text for sign-in failures: the reason, the bare sign-in link,
  the OS open commands and the instruction to call `auth_status` and retry. No `scripts/auth.*`.
- README section on publishing the OAuth consent screen to *In production* (ends the 7-day
  Testing-mode refresh-token expiry), and on how sign-in is offered.

### Changed

- `BearerAuthMiddleware` exempts exactly `GET`/`HEAD` on `/healthz`, `/auth` and
  `/oauth2callback`. The MCP tool set is now seven tools: the six above plus `auth_status`.
- The sign-in success page now reads "Gmail is connected. You can close this tab and return to
  your assistant." (no client name).
- Sign-in failures from Gmail calls are reported with the `AUTH_REQUIRED` text instead of the
  previous "Gmail is not authorised" hint, which pointed at `scripts/auth.ps1`.

## [0.1.0] - 2026-10-01

### Added

- Streamable HTTP MCP server (official `mcp` SDK) with six tools: `search_threads`,
  `get_thread`, `get_message`, `list_labels`, `list_drafts` and `create_draft`.
  There is deliberately no send, delete, trash, modify, settings or attachment-download tool.
- Bearer-token authentication (constant-time comparison), Host/Origin loopback guard
  (DNS-rebinding defense), request-size cap and an unauthenticated `GET /healthz`.
- One-time OAuth sign-in (`auth` script / `gmail_mcp.auth_cli`) using the installed-app flow
  with PKCE; refresh token stored atomically with mode 0600 in a git-ignored volume.
- Strict input validation (ID patterns, CR/LF header-injection rejection, size and
  recipient limits) and bounded output (result cap, body truncation, thread budget).
- Structured JSON logging with secret redaction; no message content is logged.
- Hardened container: multi-stage build, hash-pinned dependencies, non-root UID 10001,
  read-only root filesystem, dropped capabilities, loopback-only published port.
- `scripts/` (`start`, `stop`, `restart`, `rebuild`, `auth`, `logs`, `status`) as `.sh` with
  PowerShell wrappers that run them in WSL2.
- Test suite (pytest, fully mocked Google API), `ruff`, `mypy --strict`, and a `Makefile`.
