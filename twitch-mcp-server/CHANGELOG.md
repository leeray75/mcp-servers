# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- The user-token store moves from the `./data` bind mount to the Docker named volume `twitch-mcp-data`
  (`docker-compose.oauth.yml`, Amendment 6, V1–V8). A bind-mounted folder on the Windows drive gave the container user
  files it could not chmod to `0600`, so the token could not be saved (`upstream` outcome). The base deployment still
  mounts nothing, and the MCP tool set and wire behaviour are unchanged. `setup.sh` creates no folder and prints the
  volume name.
- Operator notes: (a) an existing `./data/twitch_user_token.json` is left in place by the upgrade; it is a live refresh
  token, so delete it after signing in again. (b) Turning `TWITCH_USER_OAUTH` off leaves the volume, and the token,
  until `docker volume rm twitch-mcp-data`. (c) An existing volume is not re-initialised; Docker copies the image's
  `/data` ownership only into an empty volume. Legacy upgrade: older versions stored the token in `./data`; delete any
  `twitch_user_token.json` there after upgrading. Stop and restart keep the token; delete it with
  `disconnect_twitch_account` or `docker volume rm twitch-mcp-data`.

### Removed

- Per-minute tool-call budget (R23): `budget.py`, the `TOOL_CALLS_PER_MINUTE` and
  `TOOL_CALLS_PER_MINUTE_PER_TOOL` variables and their tests. Twitch's limits are still enforced through
  header learning and parking (R19–R22). Simplification amendment S1–S2.
- TLS key-mode probe and rule (T4 mode clause, T-D7, T-D10): the server no longer checks the private key's
  permissions. Keep the key owner-only (S5).
- TLS repository-tree check (T5) (S6).
- Plaintext-refusal middleware (T8, T-D9): uvicorn refuses plaintext at the TLS handshake (S7).

### Changed

- The key-mode and repository-location rules no longer exist (T4, T5); `make-cert.sh` and the TLS
  compose override now say the key's permissions are the operator's responsibility.
- Test runs have a 30-second per-test timeout (`pytest-timeout`, `timeout_method = "signal"`), so a
  hanging test fails instead of blocking the gate (S11–S12).

### Added

- Initial build of `twitch-mcp-server` (spec: `docs/SPEC.md`, approved 2026-10-29; build record
  `docs/specs/initial-build/`).
- Twenty-three read-only tools over the Twitch Helix API (`GET /helix/...` only): `twitch_account_status`,
  `get_users`, `get_users_follows`, `get_channels`, `search_channels`, `get_streams`, `get_top_games`,
  `get_games`, `get_videos`, `get_clips`, `get_polls`, `get_predictions`, `get_emotes`,
  `get_channel_chat_settings`, `get_channel_chat_badges`, `get_stream_markers`,
  `get_broadcaster_subscribers`, `get_channel_editors`, `get_moderators`, `get_banned_users`,
  `get_automod_settings`, `get_chat_chatters`, `get_schedule`.
- Default-deny broadcaster allowlist (`TWITCH_ALLOWED_BROADCASTER_IDS`, 1–100 user ids) enforced by one
  guard before any request, with one identical denial message for every lookup failure so the allowlist
  cannot be probed (R25–R27).
- Optional user identity (`TWITCH_USER_OAUTH=true`, off by default): loopback sign-in routes
  `GET /oauth/start` and `GET /oauth/callback` with single-use `state`, an `iss` check when Twitch supplies
  one, and a 10-minute TTL (no PKCE — Twitch does not accept `code_challenge`), the two account tools
  `connect_twitch_account` / `disconnect_twitch_account`, and a token store at
  `/data/twitch_user_token.json` written atomically with mode 0600 (the only persistent artifact).
  Refresh runs under one lock; a rejected refresh forces re-auth and never falls back to the app token.
- Sixteen write tools across six groups (`channel`, `polls`, `predictions`, `chat`, `moderation`,
  `roles`), each requiring **two independent kill switches** — `TWITCH_ENABLE_WRITES=true` **and**
  membership in `TWITCH_WRITE_GROUPS` — before the tool is registered at all (R28). Both default off;
  with the defaults the server exposes only the 16 app-token read tools (spec A2-D1).
- Write safety: target authorization against **verified** identity (the operator's own `user_id` from
  `/oauth2/validate`, or a verified appearance in `GET /moderation/moderators` / `GET /channels/editors`,
  never merely an allowlisted id — R29/R30); a single-use, TTL-bound, argument-hash-bound confirmation
  token minted by a `kind=confirmation_required` first turn that sends no write request (R31–R34); and
  **writes are never retried** under any condition, with an uncertain outcome reported as
  `kind=unknown_outcome` instead of a second dispatch (R16).
- Two independent rate-limit buckets (app token and user token) learned only from
  `Ratelimit-Limit`/`-Remaining`/`-Reset` headers — no endpoint cost or limit value is hard-coded
  (R19–R22); a 429 parks its bucket, waits longer than `TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS` fail fast,
  and shared/global 429s are never replayed. The first `401` opens a process-lifetime circuit that stops
  all upstream traffic (R24).
- Single `httpx` module (`twitch_client.py`, pinned by a grep test), host pinning to
  `https://api.twitch.tv/helix` and `https://id.twitch.tv`, redirects refused, streamed response byte cap,
  `Client-Id` + `Authorization` required before the socket, Helix list-repeat query encoding (R11–R18).
- Tool-call budget (overall and per tool), TTL/LRU cache bounded by entries and bytes (metadata and
  identity verification only), structured JSON logs with key-name and regex redaction (R52–R54).
- Container: Streamable HTTP on `127.0.0.1:8770` (host loopback only), bearer authentication on every
  route but `GET /healthz`, Host/Origin guard, non-root UID 10001, read-only rootfs, all capabilities
  dropped, `no-new-privileges`, PID/memory/CPU limits. The only host mount is `./data`, added by
  `docker-compose.oauth.yml` when user OAuth is on (R1/R2).
- Operations scripts (`setup`, `start`, `stop`, `restart`, `rebuild`, `logs`, `status`, `smoke`) with
  PowerShell twins, hash-pinned `requirements*.txt` generated by `make lock` (uv), `make check`
  (ruff + `mypy --strict` + pytest with a 90% branch-coverage gate) and `make audit` (pip-audit).
- **HTTPS for the OAuth redirect** (spec: `docs/specs/tls-oauth-redirect`, approved 2026-10-30; T1–T12). Twitch's
  developer console rejects a plain-`http` Redirect URL, which made user sign-in unreachable. TLS is now terminated
  by uvicorn inside the existing container, opted into with `TWITCH_TLS_CERT_FILE` + `TWITCH_TLS_KEY_FILE` — both or
  neither, and both unset leaves the previous plain-HTTP behaviour byte-identical (T1, T2). The scheme of
  `redirect_uri` and `sign_in_url` is derived from that same flag, so the URI registered with Twitch and the URI sent
  to `id.twitch.tv` cannot disagree (T3). Startup refuses a missing, unreadable, unparseable or mismatched
  certificate, a key whose mode is not `0600`, and any key inside the repository outside the git-ignored `certs/`
  (T4, T5) — always naming the field, never the path or the content (T9). The `0600` rule is enforced wherever the
  filesystem can express a Unix mode and skipped, after a probe beside the key proves it cannot, on volumes such as
  a Windows drive under WSL where every file reports `0777` (T-D10). `/healthz` gains one key, `tls`, and stays
  unauthenticated (T6). Cleartext requests to a TLS-enabled server are refused with `400` and never answered with a
  `3xx`, because a redirect would carry the bearer token in the clear (T8, T-D9).
- `scripts/make-cert.sh` and `scripts/make-cert.ps1` (T10): a local CA (10 years) plus a `CN=localhost` leaf with
  `SAN DNS:localhost,IP:127.0.0.1` (398 days) into the git-ignored `certs/`, printing only the CA path and its
  SHA-256 fingerprint; the PowerShell twin then imports the CA into the **current user's** trusted-root store with
  `certutil -addstore -user Root` — no administrator rights, nothing machine-wide, no browser warning on the
  callback (T-D4); on macOS the same user-scoped trust step is the login keychain,
  `security add-trusted-cert -k ~/Library/Keychains/login.keychain-db`, which `make-cert.sh` prints on that platform
  (T-D11). `docker-compose.tls.yml` mounts `./certs` read-only at `/certs`, applied only when TLS is
  configured (T11); the container healthcheck and `scripts/status.sh` / `smoke.sh` follow the scheme, disabling
  certificate verification for the loopback self-probe only (T-D8).

### Changed

Corrections made against the first review round, each ruled by the human and recorded as an amendment in
`docs/SPEC.md` (A2).

- The registered tool count is stated as it actually behaves: **16** app-token reads by default, **25** with a
  signed-in account, **41** maximum. The earlier prose ("23 by default") described the spec's full read set as if it
  were the default (A2-D1).
- A `404` from Helix on a **read** is reported as the new `kind=not_found` instead of `upstream`. Guard ownership
  lookups are unaffected and still collapse to the uniform `forbidden_channel` denial (R27), so a `404` can never be
  used to probe the allowlist; a `404` on a **write** remains `unknown_outcome` (R16, A2-D2).
- Three projections now follow the documented Helix wire format rather than the draft models (A2-D3):
  `get_chat_chatters` returns a flat chatter list with `chatter_count` from `total`; `get_schedule` reads `data` as
  an object carrying the broadcaster fields and a `segments` array, with each segment's game as a nested `category`
  object; `get_automod_settings` returns one record of integer strictness levels.
- The first `401` now invalidates the identity and confirmation caches through the circuit's auth listener, so a
  stale verified-identity verdict cannot survive a rejected token (R30).
- The app access-token mint is exempted from the "every request carries client-id and bearer" invariant, which
  `POST /oauth2/token` legitimately does not (R13); the check still holds for every other endpoint.
- Projections refuse to launder an unreadable Helix body: `get_automod_settings` now fails `kind=upstream` when its
  record carries none of the nine strictness levels (previously it returned a successful-looking record of all-absent
  levels), and any value pydantic cannot represent is reported as `kind=upstream` instead of surfacing to the agent as
  an opaque JSON-RPC internal error (R43/R45, G5).

### Fixed

- OAuth sign-in no longer requires an `iss` query parameter, which Twitch does not send; `iss` is still checked
  when present.
- OAuth sign-in accepts Twitch's documented `scope` array on the authorization-code exchange and the refresh; the
  validate login is read from `login`; disconnect revokes the token at Twitch (client id now sent) and deletes the
  local credential even for a dead token; a refresh rejection clears the stored credential; an app-token mint
  rejection now points at `TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET`.
