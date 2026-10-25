# Easy Re-authentication — Specification

Normative words (MUST, SHOULD, MAY) follow RFC 2119. This spec amends `docs/SPEC.md`;
where they conflict, this document wins and `docs/SPEC.md` MUST be updated to match.
Decisions D1–D4 are in [PLAN.md](PLAN.md).

## R1. Configuration

- R1.1 `Settings` MUST gain `host_port: int` (env `HOST_PORT`, default `8765`, range
  1–65535). It is the port the user's browser uses to reach the server (the published host
  port), distinct from the in-container `PORT`.
- R1.2 `docker-compose.yml` MUST pass `HOST_PORT: "${HOST_PORT:-8765}"` into the
  `gmail-mcp` service environment (the `auth` service MAY receive it too). The port mapping
  MUST stay bound to `127.0.0.1`.
- R1.3 `.env.example` and the README configuration table MUST describe that `HOST_PORT` is
  now also read by the server.

## R2. Server-hosted sign-in routes

Both routes are plain HTTP GET routes added to the Starlette app in `create_app`.

### R2.1 `GET /auth`
- MUST create an OAuth flow (`google_auth_oauthlib.flow.Flow.from_client_config`, the same
  `installed` client config as `auth.build_flow`) with redirect URI
  `http://127.0.0.1:<host_port>/oauth2callback`, the configured scopes, PKCE
  (`code_verifier` generated per request, S256), `access_type=offline`,
  `prompt=consent`, and a cryptographically random `state` (`secrets.token_urlsafe(32)`
  or equivalent).
- MUST respond `302 Found` with `Location` = the Google authorization URL.
- MUST store the pending attempt (state → code verifier, redirect URI, expiry) in an
  in-memory store owned by the app (no globals shared across `create_app` calls). Not
  persisted.
- Pending attempts MUST expire after 10 minutes and the store MUST be bounded (at most 8
  pending attempts; adding a 9th evicts the oldest). Expired entries are purged
  opportunistically.
- MUST work whether the browser used `127.0.0.1` or `localhost` in the Host header (the
  redirect URI always uses `127.0.0.1`).

### R2.2 `GET /oauth2callback`
- Query `error=...` (user denied) → HTML error page, HTTP 400, nothing written.
- Missing/unknown/expired/already-used `state` → HTML error page, HTTP 400, nothing
  written. The state MUST be consumed (removed) on first use regardless of outcome. State
  lookup SHOULD use a constant-time comparison or a dict lookup keyed by the exact value
  (no prefix matching).
- Missing `code` → HTTP 400.
- Otherwise exchange the code for tokens with the stored code verifier. The exchange is a
  blocking network call and MUST run off the event loop (`anyio.to_thread.run_sync` /
  `asyncio.to_thread`). Pass the code directly (`fetch_token(code=...)`) rather than an
  `http://` authorization-response URL; oauthlib's insecure-transport and relaxed-scope
  environment switches MUST NOT be set process-wide.
- If no refresh token is returned → HTTP 400 page telling the user to revoke the app at
  `https://myaccount.google.com/permissions` and try again.
- If granted scopes do not cover the configured scopes → HTTP 400 page telling the user to
  tick every permission box; nothing written.
- On success: `write_token(Path(settings.token_path), refresh_token, granted)`, then
  reset the Gmail client's cached service (R3), then HTTP 200 success page ("Gmail is
  connected. You can close this tab and return to Goose.").
- Any unexpected exception → HTTP 500 generic page; log only the exception class name.

### R2.3 Page and response requirements (all `/auth*` and `/oauth2callback` responses)
- Pages MUST be minimal static HTML with no scripts, no external resources and no
  user-controlled content reflected (do not echo `error`, `state`, `code` or any query
  value).
- Headers MUST include `Cache-Control: no-store`, `Referrer-Policy: no-referrer`,
  `X-Content-Type-Options: nosniff`, and
  `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`.
- Only `GET` (and MAY `HEAD`) are allowed; other methods → 405.

### R2.4 Middleware
- `BearerAuthMiddleware` MUST exempt exactly `GET`/`HEAD` on `/healthz`, `/auth` and
  `/oauth2callback`. Every other path, including `/mcp`, still requires the bearer token.
  The exemption MUST match the path exactly (no prefix matching such as `/auth/../mcp` or
  `/authx`).
- `HostGuardMiddleware` MUST continue to run first for every route, including the new ones
  (non-loopback Host → 421; non-loopback Origin → 403).
- `/healthz` behaviour is unchanged and MUST still not reveal whether a token exists.

### R2.5 Logging
- Log one INFO event per sign-in outcome (`started`, `succeeded`, `denied`,
  `bad_state`, `scope_missing`, `no_refresh_token`, `failed`) with no codes, states,
  tokens, verifiers, URLs containing them, email addresses or scopes values.
- The redaction filter SHOULD additionally scrub Google authorization codes
  (pattern `4/0[A-Za-z0-9_-]{10,}`) as defense in depth.

### R2.6 Write safety
- The server process writes the token file. Concurrent callbacks MUST NOT corrupt it
  (`write_token` is atomic; additionally serialize writes with a lock).

## R3. Picking up the new token

- `GmailClient` MUST gain a public method (e.g. `reset()`) that drops the cached service
  under its lock, so the next tool call rebuilds credentials from the new token file.
- The sign-in route MUST call it after a successful write. No server restart is needed.

## R4. Clickable error hint

- The user-facing auth error MUST contain the absolute link built from `host_port`, for
  example:
  `Gmail is not authorised. Open http://127.0.0.1:8765/auth in your browser to sign in
  (or run scripts/auth.ps1 on Windows / scripts/auth.sh in WSL).`
- The link MUST reflect a non-default `HOST_PORT`. Implementation is free (e.g. configure
  the hint once at app creation, or build it in the error mapper), but `AuthRequiredError`
  messages MUST stay free of secrets and tool descriptions are otherwise unchanged.
- The tool set MUST remain exactly the six tools (D3). Existing tool-set test unchanged.

## R5. Scripts

### R5.1 `scripts/auth.sh`
- MUST accept an optional `--no-browser` flag (and reject any other argument with a usage
  message). `--no-browser` keeps today's print-the-URL behaviour.
- **Server path (preferred):** if the `gmail-mcp` container is running and
  `http://127.0.0.1:<HOST_PORT>/healthz` returns 200, the script MUST open
  `http://127.0.0.1:<HOST_PORT>/auth` in the browser (or print it with `--no-browser`),
  then wait (poll every 2 s, timeout 5 min) for `data/token.json` to be created or for its
  modification time to change, and report success or timeout. It MUST NOT read or print
  the file's contents.
- **One-off container path (fallback):** otherwise, run the existing `auth` service with
  stdout streamed to the terminal line by line; the first line containing
  `https://accounts.google.com/` MUST trigger opening that URL in the browser (unless
  `--no-browser`). Exit codes from the container MUST be propagated.
- Browser opener (shared helper in `scripts/lib/common.sh`, e.g. `open_url`): try
  `wslview`, then `explorer.exe` (WSL), then `xdg-open`, then fall back to printing
  "Open this URL in your browser: ...". The URL MUST be passed as a single argument (no
  `eval`, no `cmd.exe /c start` since `&` in the URL breaks cmd parsing). Opener failures
  MUST NOT abort the script; it falls back to printing.
- `explorer.exe` returns exit code 1 even on success; the helper MUST treat that as
  success.
- Remain `set -euo pipefail` clean; `bash -n` and (if available) `shellcheck` MUST pass.

### R5.2 `scripts/auth.ps1`
- Continues to forward arguments to `auth.sh` (so `.\scripts\auth.ps1 --no-browser`
  works). No other change required.

### R5.3 `auth_cli.py`
- Behaviour unchanged except the printed prompt SHOULD mention that the script will try to
  open the browser automatically. It MUST still not try to open a browser itself (none in
  the container).

## R6. Documentation

- README: new "Re-authenticating" section (click the link in Goose's error, or run
  `scripts/auth.ps1`); update the "Sign in once" step, scripts table (`auth [--no-browser]`),
  troubleshooting (e.g. "link does not load" → server not running, use the script;
  `redirect_uri_mismatch` → client must be Desktop type), security model (the two new
  unauthenticated loopback routes and their protections; residual risk below).
- `docs/SPEC.md` §4.1/§4.2/§4.3/§8 updated to match; CHANGELOG entry; CLAUDE.md
  architecture/invariants mention the sign-in routes.
- Residual risk to document honestly: any local process (or a web page the user visits)
  can open `/auth` and start a sign-in, but nothing is stored unless the user approves the
  Google consent screen; a local process that can already read `data/token.json` is
  outside this server's threat model.

## R7. Tests (Gmail/Google fully mocked, no network)

At minimum:
- `/auth` returns 302 to `accounts.google.com` with `state`, `code_challenge`,
  `code_challenge_method=S256`, `access_type=offline`, `prompt=consent`, and a
  `redirect_uri` using `host_port` (default and a non-default value).
- `/auth` and `/oauth2callback` work without a bearer token; `/mcp` still returns 401
  without one; `/authx`, `/auth/` variants and POST to the new routes do not bypass auth or
  return 405 as appropriate.
- Non-loopback Host on the new routes → 421.
- Callback: success writes the token (temp dir) with the granted scopes and calls
  `GmailClient.reset()`; unknown state, reused state, expired state, `error=access_denied`,
  missing code, no refresh token, missing scope, exchange exception → correct status and
  no token written.
- Pending store bound (9th attempt evicts oldest) and expiry.
- Security headers present on every new response; no query values reflected in the body.
- Auth error message contains `http://127.0.0.1:<host_port>/auth`.
- `GmailClient.reset()` causes the factory to be called again.
- Logs from the flow contain no code/state/token values (use a sentinel value and assert
  it is absent from captured logs).
- Existing tests keep passing; the coverage gate (≥ 85 %) holds.

## R8. Quality gate

`ruff check`, `ruff format --check`, `mypy --strict`, `pytest` with coverage ≥ 85 %, plus
`bash -n` on changed scripts (and `shellcheck` if installed). If Docker is reachable in
WSL, the image MUST still build. Requirements files MUST NOT be hand-edited; no new runtime
dependency should be needed (`google-auth-oauthlib` already provides `Flow`).

## R9. Acceptance checklist

- [ ] R1 `HOST_PORT` in settings + compose + docs
- [ ] R2 `/auth` and `/oauth2callback` with state/PKCE/expiry/bounds/headers/logging
- [ ] R3 `GmailClient.reset()` called after success
- [ ] R4 error hint contains the working link
- [ ] R5 `auth.sh` server path, fallback auto-open, `--no-browser`, `open_url` helper
- [ ] R6 README, SPEC.md, CHANGELOG, CLAUDE.md updated
- [ ] R7 tests added; R8 gate green
- [ ] Six-tool invariant and all existing invariants preserved
