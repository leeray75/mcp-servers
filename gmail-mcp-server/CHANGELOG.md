# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Container DNS: compose sets `dns:` on both services from `CONTAINER_DNS_1` (default `8.8.8.8`)
  and `CONTAINER_DNS_2` (default `1.1.1.1`), documented in `.env.example` and the README.
- `gmail_mcp.netcheck` (DNS probe for the two Google hosts, stdlib plus httplib2 for error types, bounded by a wall-clock
  timeout, never raises or returns addresses) and `gmail_mcp.healthcheck`
  (`python -m gmail_mcp.healthcheck`).
- Network triage section in the README.
- `NETWORK_UNREACHABLE` tool error: a Gmail call that fails because the Google hostnames do not
  resolve in the server container now says so (do not retry in a loop, call `auth_status`, restart
  if it reports `network: dns_failure`) instead of "try again shortly". `netcheck.is_dns_failure`
  checks the exception cause chain.
- `auth_status` field `network` (`ok` or `dns_failure`), from a bounded (2 s) DNS probe of the two
  Google hostnames, also in `gmail://auth/status`. `dns_failure` adds a restart instruction to
  `next_step` and a warning.

### Changed

- The container healthcheck (Dockerfile `HEALTHCHECK` and compose `healthcheck.test`) runs
  `python -m gmail_mcp.healthcheck` and now also requires DNS resolution of the Google hosts, so
  `unhealthy` reflects a dead resolver. It is a label only; the container is not restarted.
- `auth_status` and `gmail://auth/status` now resolve the Google hostnames (DNS only; no request is
  sent to Google). This amends the earlier "pure, no network" statement; `compute_auth_status`
  itself stays pure and receives the probe result.
- `ready_to_retry` is true only when `state` is `signed_in` and `network` is `ok`. The guide
  resource and the `auth_status` description say to retry only on `ready_to_retry: true`.

## [0.2.0] - 2026-10-25

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
  the OS open commands, the copy-and-paste link for the user's own browser, the ban on
  browser-automation tools and credential entry, and the instruction to retry only when
  `ready_to_retry` is true. No `scripts/auth.*`.
- Agent guardrails in `gmail://guide/auth`: who opens the sign-in page (shell open command, else
  copy and paste), no browser-automation tools on it, and never type or capture credentials.
- `auth_status` fields `ready_to_retry` (true only when `signed_in`) and `checked_at` (UTC time of
  each computation), and the state `sign_in_in_progress` for an unexpired pending sign-in
  attempt (no state value, verifier or timing is exposed).
- Descriptions for all four MCP resources (listed by `resources/list`).
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
