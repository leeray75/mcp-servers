# User OAuth mode (guild discovery) — Specification

- **Status:** Approved — 2026-10-29, approved by: human
- **Date:** 2026-10-29
- **Amends:** `../../SPEC.md` (approved 2026-10-28, with A1 §16 and A2 §17). This document is amendment **A3**. Where it
  conflicts with `../../SPEC.md` (§§1–17), this document wins, and `../../SPEC.md` gets a short §18 pointing here (U35).
  Normative words (MUST, SHOULD, MAY) follow RFC 2119.
- **Phase-1 input:** `PLAN.md` in this folder (written by another agent; treated as untrusted input; every factual claim
  re-checked below). Decisions D17–D28 were made by the human in chat on 2026-10-29.

## 1. Problem / motivation

The human wants to find their Discord servers, see which ones they can add the bot to, and fill
`DISCORD_ALLOWED_GUILD_IDS` without Developer Mode id-hunting. The original wish, reading message content in a server
where the human is only a member, **cannot be met through any Discord-sanctioned API** (§1.1), so this feature is
deliberately limited to the discovery and onboarding that user OAuth does allow.

### 1.1 Verified ground truth (docs.discord.com, fetched 2026-10-29)

From `developers/topics/oauth2` and `developers/resources/user`:

1. No user-OAuth scope reads guild channels, messages, members or audit logs. `guilds` only lets
   `GET /users/@me/guilds` return "basic information about all of a user's guilds"; `identify` allows `GET /users/@me`
   (no email); `guilds.members.read` returns only the user's own member object.
2. `messages.read` is for the local RPC server only and is restricted to approved partners. Not usable here.
3. Automating user accounts outside the OAuth2/bot API ("self-bots") breaks the developer terms. Not an option.
4. Authorization code grant. Token URL `https://discord.com/api/oauth2/token`, revoke URL
   `https://discord.com/api/oauth2/token/revoke`; both accept **only** `application/x-www-form-urlencoded`. Every call to
   these endpoints needs HTTP Basic auth or `client_id` + `client_secret` in the body. `expires_in` (example 604800 s);
   refresh with `grant_type=refresh_token`. Refresh-token lifetime and rotation are not documented.
5. `state` is "highly recommended"; every authorization redirect carries `iss=https://discord.com` (RFC 9207).
6. PKCE: the docs say to prefer "the authorization code grant with PKCE" over implicit, but **document no PKCE
   parameters and no public-client exchange**. A secret-less exchange is therefore not relied on (D19); PKCE is sent as
   defence in depth behind a kill switch (U8) and checked live (AC-L1).
7. `redirect_uri` must exactly match a URI registered for the app; the token exchange must repeat it.
8. Revocation takes `token` and optional `token_type_hint`; revoking either token revokes **all** tokens of that
   authorization.
9. `GET /users/@me/guilds`: partial guilds with `id`, `name`, `icon`, `banner`, `owner`, `permissions`, `features`, and
   `approximate_member_count` / `approximate_presence_count` when `with_counts=true`. Query `before`, `after`,
   `limit` (1–200, default 200), `with_counts` (default false). 200 is the maximum for non-bot users.
10. Bot authorization URL `https://discord.com/oauth2/authorize` with `client_id`, `scope=bot`, `permissions`,
    `guild_id` (pre-fills the guild) and `disable_guild_select`.
11. No OAuth-specific rate limits are documented; the general header-driven rules apply.

PLAN.md corrections: the Gmail server does **not** use code-paste (its OAuth redirect lands on its own loopback route),
and PKCE without a secret is unsupported by the docs.

## 2. Goals

- G1. List the guilds of the authorized human with owner and permission information, flags that show which ones are
  allowlisted, already have the bot, or allow the human to add it.
- G2. Produce a one-click bot-invite link for a chosen guild.
- G3. Manage the single user token: connect, status, disconnect.
- G4. Keep every existing invariant (§6) except the ones this amendment names and narrows; with
  `DISCORD_USER_OAUTH=false` (the default) the server's tool set, routes, container and behavior are unchanged.

## 3. Non-goals

- Reading any guild content with the user token (impossible, §1.1).
- Multi-user support. One human, one account token, single-tenant server.
- Any Discord write: `guilds.join`, `gdm.join`, `webhook.incoming`, membership changes.
- Partner-gated scopes (rpc family, `messages.read`), self-bots, gateway connections.
- `guilds.members.read` / a `get_my_membership` tool (D17 option 2, not chosen).
- Allowlist editing from a tool (the human edits `.env`).

## 4. Decisions (made with the human on 2026-10-29)

| # | Question | Decision |
|---|---|---|
| D17 | Scope | **Discovery helper**: `list_my_guilds`, `get_guild_invite_link`, `connect_discord_account`, `discord_account_status`, `disconnect_discord_account`. OAuth scopes fixed at `identify guilds`. |
| D18 | Authorization return path | **Callback on the existing port**: redirect URI `http://127.0.0.1:<HOST_PORT>/oauth/callback`, served by the MCP server itself (Gmail `/auth` pattern). The authorization code never passes through the model. |
| D19 | Client type | **Confidential client + S256 PKCE**: client secret in `.env`, required only when the feature is on; PKCE as defence in depth. |
| D20 | Token storage | **`./data` bind mount**, token JSON written atomically with mode 0600; `SecretStr` in memory. Amends "mounts no host folders", but only when the feature is on (compose override, U30). |
| D21 | CSRF | **`state` + `iss` + expiry**: single-use random `state`, 10-minute TTL, one pending flow at a time; the callback refuses a missing or mismatched `state` or an `iss` other than `https://discord.com`. |
| D22 | When is `get_guild_invite_link` registered | **Only with OAuth on.** Off: exactly the current 14 tools. On: 19 tools. |
| D23 | Network invariants | **Narrow POST + isolated limiter**: `discord_client.py` remains the only httpx module; POST allowed only to the two fixed OAuth URLs (form-urlencoded); user-token calls have their own rate-limit state and hard-stop, separate from the bot's. |
| D24 | Expiry | **Auto-refresh, serialized**: refresh under a lock within 5 minutes of expiry or after a user-token 401; persist atomically; refresh rejected → clear the token, `kind=auth` with a connect hint. A user-token 401 never trips the bot hard-stop. |
| D25 | Connect UX | **URL + URL-mode elicitation** (Gmail agent-auth pattern) for 2026-07-28 clients that declare it; every client also gets the URL and OS open commands in the result. |
| D26 | Redirect URI source | **Derived from `HOST_PORT`** (default 8769). No separate setting. |
| D27 | `list_my_guilds` fields | **Discord fields + derived flags**: `allowlisted`, `bot_present`, `can_add_bot`; optional `with_counts` argument, default false. |
| D28 | Health and disconnect | **`/healthz` unchanged** (says nothing about the account); **best-effort revoke**: disconnect calls revoke, always deletes the local token, reports `revoke_failed` if Discord errors. |

Design refinement the human approves with this spec (follows from D18 + D25 and the Gmail precedent, which keeps
`state` out of the elicited URL): the `state` and PKCE verifier are created when the browser opens the server's own
`/oauth/start` page, not inside the tool call. `connect_discord_account` only returns (and elicits) that plain URL.

## 5. Requirements

### Configuration (`config.py`, the only environment reader)

- **U1** New settings, all optional when the feature is off:
  - `DISCORD_USER_OAUTH: bool = false` — master switch.
  - `DISCORD_OAUTH_CLIENT_SECRET: SecretStr | None` — required (non-empty, no `replace-me` placeholder) when the
    switch is on.
  - `DISCORD_APPLICATION_ID` (exists today, optional) — becomes **required** when the switch is on (it is the OAuth
    `client_id`; same snowflake validation as today).
  - `HOST_PORT: int = 8769`, range 1–65535 (already written to `.env` by `setup.sh`; now also read by config). Used
    only to build the loopback URLs.
  - `DISCORD_OAUTH_TOKEN_FILE: str = "/data/discord_user_token.json"` — absolute path required.
  - `DISCORD_OAUTH_URL_ELICITATION: bool = true` — kill switch for U17.
  - `DISCORD_OAUTH_PKCE: bool = true` — kill switch for U8 (in case Discord rejects the PKCE parameters, AC-L1).
- **U2** Validation errors name fields, never values (unchanged rule). A missing secret or application id with the switch
  on is a config error naming the field.
- **U3** With `DISCORD_USER_OAUTH=false`, none of the new settings is read for behavior, the extra routes and tools do not
  exist, and the existing test suite passes unchanged except for the new tests in §8.
- **U4** If the boot probe reads an application id from `/applications/@me` and it differs from
  `DISCORD_APPLICATION_ID`, the five OAuth tools are omitted and both OAuth routes return 404, with boot-line reason
  `user_oauth=disabled(application_id_mismatch)`. If the probe could not read the id, the setting is trusted.

### Tool registry

- **U5** With the switch on (and U4 satisfied), these tools are appended **after** the existing tools, in this order:
  `discord_account_status`, `connect_discord_account`, `disconnect_discord_account`, `list_my_guilds`,
  `get_guild_invite_link`. Intent-gated omissions of the bot tools are unchanged. A test pins both exact sets
  (off: the 14 as today; on: the 14 + 5, minus intent omissions).
- **U6** Annotations: `discord_account_status`, `list_my_guilds`, `get_guild_invite_link` are read-only, idempotent and
  non-destructive. `connect_discord_account` is read-only and idempotent (it changes nothing; the browser does).
  `disconnect_discord_account` is `readOnlyHint=false`, `destructiveHint=true`, `idempotentHint=true` (it deletes the
  server's own credential, never Discord content). `openWorldHint=false` for the URL builder and status, `true` for
  `list_my_guilds` and `disconnect_discord_account`.
- **U7** The five tools are subject to the existing tool-call budget (R36-R37), the unknown-argument rejection and the
  R30 error shape like every other tool. Results follow R11 (structured model + text rendering).

### Authorization flow

- **U8** `GET /oauth/start` (unauthenticated, only when the feature is on) creates a pending flow and answers `302` to
  `https://discord.com/oauth2/authorize` with exactly: `response_type=code`, `client_id`, `scope=identify guilds`
  (space-separated, percent-encoded), `redirect_uri` (U10), `state`, `prompt=consent`, and, when `DISCORD_OAUTH_PKCE` is
  true, `code_challenge` (base64url SHA-256 of the verifier, no padding) and `code_challenge_method=S256`. Response headers
  `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
- **U9** Pending flow: `state` is 32 bytes from `secrets.token_urlsafe`; the PKCE verifier is 64 characters from the
  RFC 7636 unreserved set (`secrets`). At most one pending flow exists; a new `/oauth/start` replaces the previous one.
  It expires 600 s after creation (injectable clock). It lives in process memory only, never on disk, never keyed to an
  MCP session.
- **U10** The redirect URI is exactly `http://127.0.0.1:<HOST_PORT>/oauth/callback`, used identically in the authorize
  redirect and the token exchange. The README tells the human to register this exact URI in the Developer Portal.
- **U11** `GET /oauth/callback` (unauthenticated, only when the feature is on) accepts query `code`, `state`, `iss`,
  `error`, `error_description` (all others ignored). It MUST refuse, with a generic error page and no Discord request,
  when: no pending flow exists, the flow expired, `state` is absent or differs (constant-time comparison), `iss` is
  absent or not exactly `https://discord.com`, or `code` is absent. Any refusal and any `error` from Discord consumes and
  clears the pending flow. `state` is single-use: a second callback with the same state is refused.
- **U12** On a valid callback the server exchanges the code: `POST <token URL>` (U22) with
  `grant_type=authorization_code`, `code`, `redirect_uri`, and `code_verifier` when PKCE is on, authenticated with HTTP
  Basic `client_id:client_secret`. A success needs `access_token`, `token_type` equal to `Bearer` (case-insensitive),
  integer `expires_in` > 0, `refresh_token`, and a `scope` that contains both `identify` and `guilds`; otherwise the
  connect fails as `upstream` and nothing is stored.
- **U13** After the exchange the server calls `GET /users/@me` with the new token to record `user_id` and `username`
  (identify scope). Failure of this call fails the connect (nothing stored; the tokens are revoked best-effort).
- **U14** On success the server stores the token (U19), replacing any previous one (the previous authorization is
  revoked best-effort first), and answers with a fixed HTML page: "Discord account connected. You can close this tab and
  return to your assistant." Error pages are fixed text ("Discord sign-in failed. Return to your assistant and call
  connect_discord_account again.") and never echo `code`, `state`, `error_description` or tokens. Both pages carry
  `Cache-Control: no-store`, `Referrer-Policy: no-referrer` and a restrictive CSP (`default-src 'none'`).
- **U15** Both OAuth routes accept only `GET`; any other method gets 405. They are exempt
  from the bearer check only when the feature is on; the Host/Origin guard still applies to them. With the feature off
  they do not exist and bearer middleware behavior is unchanged.

### Tools

- **U16** `discord_account_status()` returns `{enabled: true, state, user_id?, username?, scopes?, expires_at?,
  connected_at?, pending_sign_in: bool, last_error?, sign_in_url, redirect_uri}` where `state` ∈
  `connected | not_connected | token_file_invalid`. `sign_in_url` is `http://127.0.0.1:<HOST_PORT>/oauth/start`.
  `last_error` is one of `refresh_rejected | revoke_failed | callback_refused | exchange_failed | null` from the last
  OAuth event in this process. No network request. No token values ever.
- **U17** `connect_discord_account(reconnect: bool = false)`: if connected and `reconnect` is false, returns
  `{state: "already_connected", username, sign_in_url}` with no elicitation. Otherwise, if the request is a 2026-07-28
  request whose per-request client capabilities declare `elicitation.url` and `DISCORD_OAUTH_URL_ELICITATION` is true,
  returns an `InputRequiredResult` with one `elicitation/create` `mode: "url"` request whose `url` is exactly
  `sign_in_url` (no query, no state, nothing sensitive) and message "Discord needs you to sign in to connect your
  account". Every other request gets a normal result `{state: "authorization_required", sign_in_url, redirect_uri,
  instructions, open_commands: {windows, macos, linux}}`. On the client's retry the tool simply re-evaluates; it never
  blocks waiting for the browser. Nothing in this tool sends a Discord request or creates `state`.
- **U18** `disconnect_discord_account()`: if no token, returns `{state: "not_connected"}`. Otherwise `POST <revoke URL>`
  (U22) with `token=<refresh_token>`, `token_type_hint=refresh_token`, Basic auth; then always deletes the token file and
  in-memory token, and returns `{state: "disconnected", revoked: bool}`; a revoke failure (any error) sets `revoked:
  false` and `last_error=revoke_failed`. Also clears any pending flow.
- **U19** Token store (`oauth.py`): JSON `{version: 1, access_token, refresh_token, token_type, scope, expires_at
  (epoch seconds), user_id, username, connected_at}`. Written to a temp file in the same directory, created with
  `os.open(..., O_WRONLY|O_CREAT|O_EXCL, 0o600)`, fsynced, then `os.replace` (Gmail `auth.py` pattern). Loaded once at
  boot; a missing file means `not_connected`; an unreadable or invalid file means `token_file_invalid` (logged by
  reason, never content), treated as disconnected, and left in place until the next connect or disconnect overwrites or
  deletes it. A missing parent directory is a boot warning, not a crash; the first write then fails as `unavailable`.
- **U20** `list_my_guilds(with_counts: bool = false)`: needs a connected token, else `kind=auth` (U24). Pages
  `GET /users/@me/guilds?limit=200[&after=<last id>][&with_counts=true]` with the user token until a page has fewer than
  200 entries, at most 5 pages; if the cap is hit, `truncated: true`. Returns `{guilds: [...], count, truncated,
  bot_guilds_known: bool}`. Each guild: `id`, `name`, `icon` (hash or null), `owner` (bool), `permissions` (decimal
  string as given), `features` (list), `approximate_member_count` / `approximate_presence_count` only when requested
  (omitted otherwise, `exclude_unset`), and derived:
  - `allowlisted`: id ∈ `DISCORD_ALLOWED_GUILD_IDS`;
  - `bot_present`: id ∈ the bot's own guild list, fetched through the existing bot-token `/users/@me/guilds` path and
    cache that `list_guilds` uses (unfiltered ids only; no other bot request). If that fetch fails, `bot_present: null`
    for every guild and `bot_guilds_known: false`; the tool still succeeds;
  - `can_add_bot`: `owner` is true or `permissions` has ADMINISTRATOR (`1<<3`) or MANAGE_GUILD (`1<<5`).
  This tool is **not** gated by the guild allowlist (it reads the human's own account data). User-token results are not
  cached.
- **U21** `get_guild_invite_link(guild_id)`: validates the snowflake, makes no network request, and returns
  `{guild_id, url}` with `url = https://discord.com/oauth2/authorize?client_id=<DISCORD_APPLICATION_ID>&scope=bot&
  permissions=66688&guild_id=<id>&disable_guild_select=true` (parameters in that order, percent-encoded) plus a note that
  the human must have Manage Server in that guild and must add the id to `DISCORD_ALLOWED_GUILD_IDS` and restart with
  `--recreate`. Not allowlist-gated; does not require a connected token.

### HTTP client (`discord_client.py` stays the only httpx importer)

- **U22** OAuth URLs are derived from the scheme and host of `DISCORD_API_BASE`: `<scheme>://<host>/api/oauth2/token`
  and `.../api/oauth2/token/revoke`. These two are the **only** URLs ever sent a `POST`; the method set is
  `{GET, POST}` and a test asserts no other POST target exists. POST bodies are `application/x-www-form-urlencoded`;
  Basic auth; redirects never followed; the response size cap applies. POSTs are never retried (no replay of a code or
  refresh token), except that a 429 is waited out and retried under the existing rules.
- **U23** User-token GETs (`/users/@me`, `/users/@me/guilds`) use `Authorization: Bearer <access_token>` through a
  separate client instance (or equivalent) with its **own** `RateLimiter` (buckets, global pause, pacing, invalid-request
  window). A user-token 401 never sets the bot hard-stop, and the bot hard-stop never blocks user-token or OAuth
  requests. OAuth token-endpoint calls use the user-side limiter.
- **U24** Errors use the R30 shape. Mapping: no token → `kind=auth`, hint "Call connect_discord_account to connect your
  Discord account."; a user-token 401 → one serialized refresh (U25) and one replay of the GET, then `kind=auth` with the
  same hint; token-endpoint `400` with OAuth `error` = `invalid_grant` → `kind=auth`; other 4xx → `upstream`; 5xx and
  transport errors → `unavailable`, retryable. The OAuth JSON `error` **code** field (RFC 6749 §5.2) may be read; its
  `error_description` text never is. 429 handling is unchanged.
- **U25** Refresh: before any user-token request, if `expires_at - now < 300 s`, refresh first. All refreshes run under
  one `asyncio.Lock`; a caller that waited re-checks and reuses a token refreshed meanwhile (no double refresh).
  `grant_type=refresh_token`, `refresh_token`, Basic auth. On success the new pair (refresh token replaced if returned,
  kept otherwise) is persisted (U19) before use. On `invalid_grant` the token file and memory are cleared,
  `last_error=refresh_rejected`, and the call returns `kind=auth`. On 5xx or transport failure the stored token is kept
  and the call returns `unavailable`.

### Logging, secrets, health

- **U26** Registered secrets for redaction: the client secret at boot, every access and refresh token on load and on
  receipt, the PKCE verifier, `state` and the authorization code when seen. Logs carry route templates only
  (`GET /oauth/start`, `GET /oauth/callback`, `POST /oauth2/token`, `POST /oauth2/token/revoke`,
  `GET /users/@me/guilds`), status, duration and outcome; never query strings, bodies, usernames or guild names.
- **U27** `/healthz` is unchanged (D28): it neither reveals nor depends on the user-account state.
- **U28** The boot line adds `user_oauth=off`, `user_oauth=on(connected)`, `user_oauth=on(not_connected)`,
  `user_oauth=on(token_file_invalid)` or `user_oauth=disabled(application_id_mismatch)`. No usernames or ids.
- **U29** Error messages and tool results never contain tokens, the client secret, the code, `state`, the verifier, or
  Discord response bodies. Tool results may contain the human's own username, user id and guild names (that is the
  purpose of the feature).

### Container, scripts, docs

- **U30** `docker-compose.yml` is unchanged. A new `docker-compose.oauth.yml` override adds `volumes: ["./data:/data"]`
  and `DISCORD_OAUTH_TOKEN_FILE=/data/discord_user_token.json` to the `discord-mcp` service. `scripts/lib` selects the
  compose files: when `.env` has `DISCORD_USER_OAUTH=true` (read with the existing `env_get` helper, values never
  printed), every compose invocation adds `-f docker-compose.yml -f docker-compose.oauth.yml`. The `.ps1` twins forward
  as today.
- **U31** The Dockerfile creates `/data` owned by `10001:10001` (harmless when unmounted). `setup.sh` creates `./data`
  when the feature is enabled and ensures it is git-ignored (`discord-mcp-server/.gitignore` gains `data/`). The rootfs
  stays read-only; `/data` is the only writable non-tmpfs path, and only with the override.
- **U32** `.env.example` documents the new settings (feature off, placeholders only). `setup.sh` gains an optional
  prompt "Enable user OAuth guild discovery? [y/N]" which, on yes, asks for the client secret (hidden input, as for the
  bot token) and confirms `DISCORD_APPLICATION_ID`.
- **U33** `smoke.sh`: when the feature is on, additionally calls `discord_account_status` and checks the tool list has
  19 entries (or 19 minus intent omissions). It does not attempt sign-in.
- **U34** README: a "User OAuth guild discovery (optional)" section: what it can and cannot do (§1.1, stated plainly),
  portal setup (copy the client secret, add the exact redirect URI from U10), enabling, the sign-in flow, the token file
  location and how to delete it, the 7-day access-token / refresh behavior, PKCE kill switch, and that `/oauth/*` are
  loopback-only unauthenticated routes protected by `state`/`iss`. Tool table extended.
- **U35** `../../SPEC.md` gets §18 "Amendment A3 (user OAuth)" pointing to this file and listing the invariants it
  narrows (§6). `CHANGELOG.md` `[Unreleased]` entry. Server `CLAUDE.md`: architecture (`oauth.py`, user client), the
  amended invariants, and the five tools. Root `CLAUDE.md` table purpose cell mentions "optional user-OAuth guild
  discovery"; root `CHANGELOG.md` entry.

## 6. Design

### 6.1 Files

| File | Change |
|---|---|
| `config.py` | U1-U2 settings and cross-field validation (`model_validator`). |
| `oauth.py` (new) | Pending-flow store (state, verifier, expiry; injectable clock and RNG), PKCE helpers, authorize-URL and invite-URL builders, token store (load/save/delete, atomic 0600), `UserAuth` holding the in-memory token, the refresh lock and `last_error`. No httpx import. |
| `discord_client.py` | `post_form()` restricted to the two OAuth URLs; a user-side client (Bearer header supplied per request from `UserAuth`, own `RateLimiter`); OAuth error mapping (U24). Bot client unchanged in behavior. |
| `service.py` | `UserOAuthService` (or methods on `DiscordService`) for U16-U21; `list_my_guilds` reuses the bot guild-list fetch for `bot_present`. |
| `capabilities.py` | `USER_OAUTH_TOOL_SPECS` and plan logic for U4-U5. |
| `models.py` / `project.py` | Result models for the five tools; `MyGuild` projection. |
| `server.py` | Register the five tools (U5-U7, U17 elicitation), `/oauth/start` and `/oauth/callback` routes (U8-U15), boot line (U28). |
| `security.py` | Bearer exemption for the two OAuth paths, active only when passed in by `create_app` (U15). |
| `logging_setup.py` | No change expected beyond `register_secret` calls from `oauth.py` (U26). |
| `docker-compose.oauth.yml`, `Dockerfile`, `.gitignore`, `.env.example`, `scripts/` | U30-U33. |
| Docs | U34-U35. |

### 6.2 Flow

```
agent: connect_discord_account ──▶ (elicit / return) http://127.0.0.1:8769/oauth/start
browser ──GET /oauth/start──▶ server: new state + verifier ──302──▶ discord.com/oauth2/authorize
human consents ──▶ Discord ──302──▶ http://127.0.0.1:8769/oauth/callback?code&state&iss
server: check state/iss/expiry ──POST /api/oauth2/token──▶ Discord ──▶ GET /users/@me ──▶ store 0600 ──▶ success page
agent: list_my_guilds ──▶ (refresh if near expiry) GET /users/@me/guilds (user limiter) + bot guild ids (bot path, cached)
```

### 6.3 SDK gate

As in Gmail agent-auth R5.6: before writing U17, the implementer verifies with a test against the pinned `mcp` 2.3 SDK
in this server's stateless JSON mode that (a) per-request client capabilities and the protocol version are readable in
a tool handler and (b) an `InputRequiredResult` with a URL elicitation can be returned. If either fails, stop and report
(spec amendment); switching to stateful mode or patching the SDK is not allowed.

## 7. Security and invariants

| Invariant (server `CLAUDE.md`) | Effect of this amendment |
|---|---|
| Read-only; exactly 14 tools; no write tools | **Narrowed**: with the switch on, 19 tools. No tool writes to Discord. `disconnect_discord_account` deletes only the server's own credential and revokes the human's own grant. Off: unchanged. |
| Only `discord_client.py` imports httpx | Unchanged (a test still greps). |
| GET-only, only the `DISCORD_API_BASE` host | **Narrowed**: POST allowed to exactly two fixed OAuth URLs on the same host (U22). The authorize URL is only ever sent to the browser, never requested by the server. |
| Every guild/channel parameter goes through `guard.py` | Unchanged for all 14 bot tools. `list_my_guilds` reads the human's own account list and is explicitly not allowlist-gated; `get_guild_invite_link` makes no request. Neither can read guild content. |
| First 401 stops all Discord traffic | **Scoped**: applies to the bot token. User-token 401s are handled by U24-U25 and never affect bot traffic (U23). |
| Rate limits learned from headers | Unchanged; the user side has its own limiter instance with the same algorithm. |
| Logs/errors/healthz never contain tokens, URLs, bodies, content | Unchanged and extended to OAuth secrets (U26, U29). `/healthz` unchanged (U27). |
| State is process-wide, never keyed to a session | Unchanged: the pending flow and user token are process-wide. |
| Bearer on every route except `/healthz` | **Narrowed**: `GET /oauth/start` and `GET /oauth/callback` are unauthenticated **only** with the switch on; they are protected by the loopback bind, the Host/Origin guard, single-use `state`, `iss` and expiry. |
| Container: read-only rootfs, no host mounts | **Narrowed**: with the switch on, the compose override bind-mounts `./data` (U30). Off: identical compose. |
| Secrets only in `.env` | Extended: the token file is in the same trust domain; mode 0600 (not enforced on `/mnt/c` drvfs, as with Gmail; documented). |

Threats considered: login CSRF and code injection (state + iss + single use + expiry), code leakage to the model (code
only reaches the loopback callback), referrer leakage (`no-referrer`, no third-party resources on the pages), replay of
codes or refresh tokens (POSTs never retried), refresh races (lock), a stolen token file (0600, git-ignored, delete by
disconnect or by hand; revoke documented).

## 8. Test plan and acceptance criteria

All offline (autouse network guard stays); fake Discord transport extended with POST handling for the two OAuth URLs.
Coverage gate stays 90% branch.

| Req | Test |
|---|---|
| U1-U4 | Config: off ignores new fields; on requires secret and application id (errors name fields, no values); HOST_PORT range; token path absolute; mismatch omission. |
| U5-U7 | Exact tool lists and order, off and on, with and without intent omissions; annotations; budget and unknown-arg rejection apply. |
| U8-U10 | `/oauth/start` redirect query exact (with and without PKCE), challenge = S256(verifier), headers, replacement of a pending flow, expiry with a fake clock. |
| U11 | Table test of every refusal (no flow, expired, missing/wrong state, wrong/missing iss, missing code, `error=access_denied`, reuse); no fake-Discord request on refusal; flow cleared. |
| U12-U14 | Exchange body and Basic auth exact; missing fields, wrong token_type, missing scope → nothing stored; `/users/@me` failure → revoke attempted, nothing stored; success page text and headers; pages never echo inputs. |
| U15 | Off: `/oauth/*` behave like any unknown route behind bearer (401); on: no bearer needed, Host guard still rejects a foreign Host, POST → 405. |
| U16-U18 | Status in each state; connect: already_connected, elicitation for a 2026-07-28 request with `elicitation.url`, plain result otherwise and when the kill switch is off; disconnect revoke success, revoke failure still deletes, not_connected. |
| U19 | Atomic write (temp + replace), mode 0600 (on POSIX), invalid/partial file → `token_file_invalid`, missing dir warning. |
| U20 | Paging (fewer than 200 stops; 5-page cap → truncated), with_counts on/off (keys omitted when off), derived flags incl. ADMINISTRATOR/MANAGE_GUILD/owner, bot fetch failure → null flags, not allowlist-gated, no caching. |
| U21 | Exact URL, snowflake validation, no network. |
| U22-U23 | Only two POST targets (grep/introspection test); form encoding; POST not retried on 5xx; user 401 does not set the bot hard-stop and bot hard-stop does not block the user side; separate limiters. |
| U24-U25 | Error mapping table; concurrent calls near expiry → exactly one refresh; invalid_grant clears token; 5xx keeps token. |
| U26, U29 | Redaction of every OAuth secret in logs during a full fake flow; no secret in any result or error. |
| U27-U28 | `/healthz` identical on/off; boot-line variants. |
| U30-U33 | `docker compose -f docker-compose.yml -f docker-compose.oauth.yml config` valid; scripts select files by flag (`bash -n`, plus a test of the selection helper if practical); `.gitignore` contains `data/`. |
| SDK gate | §6.3 test. |

Acceptance:

- **AC-U1** `make check` green (ruff, format, `mypy --strict`, pytest ≥ 90% branch); `make audit` clean if dependencies
  change (none expected).
- **AC-U2** With the switch off, the existing 454 tests pass unmodified (aside from additions) and the off-mode tool list
  equals today's.
- **AC-U3** Reviewer APPROVED within 5 rounds.
- **AC-L1 (live, optional, the human's)** Register the redirect URI, enable, sign in through the browser, call
  `list_my_guilds`, `get_guild_invite_link`, `disconnect_discord_account`. Confirms Discord accepts the 127.0.0.1
  redirect and the PKCE parameters; if PKCE is rejected, set `DISCORD_OAUTH_PKCE=false` and record it.

## 9. Out of scope

- Everything in §3. Background refresh tasks. Allowlist editing. Showing the account state on `/healthz`.
- Fixes for the initial build's optional follow-ups (n2–n4, O1).
- shellcheck (still unavailable; scripts checked with `bash -n`).

## 10. Open questions

None.

## 11. Amendment A3.1 (2026-10-29, after review round 1)

- **Status:** Approved — 2026-10-29, approved by: human.
- **Trigger:** spec issues S1-S4 in `review-round-1.md`. Decisions made by the human in chat on 2026-10-29.

| # | Issue | Decision |
|---|---|---|
| D29 (S1) | Revoking the previous grant on reconnect may invalidate the new token (a revoke kills every token of the authorization, and authorizations are per user + app) | **Do not revoke on reconnect.** |
| D30 (S4, m2) | Revoke when a connect fails after the code exchange | **Best-effort revoke on every post-exchange failure.** |
| D31 (S3, m1) | New settings validated while the feature is off | **Ignore them when off.** |
| D32 (S2, S4) | U15 405 vs bearer; U33 tool count | **Accept the clarifications below.** |

Requirement changes (these replace the quoted text; everything else in U1-U35 stands):

- **U3 (replaces):** with `DISCORD_USER_OAUTH=false`, the new settings (`DISCORD_OAUTH_CLIENT_SECRET`, `HOST_PORT`,
  `DISCORD_OAUTH_TOKEN_FILE`, `DISCORD_OAUTH_URL_ELICITATION`, `DISCORD_OAUTH_PKCE`) are neither validated nor read for
  behavior: a malformed value in any of them MUST NOT stop the server from booting. The extra routes and tools do not
  exist, and the existing suite passes unchanged. The U1 ranges and formats apply only when the switch is on.
- **U13 (replaces the last sentence):** after a successful code exchange, **any** later failure of the connect (the
  U12 response checks, the `/users/@me` call or its projection, or storing the token per U19) fails the connect,
  stores nothing, and sends one best-effort revoke of the new grant (`token=<refresh_token or access_token>`); a revoke
  failure is ignored apart from `last_error=exchange_failed`.
- **U14 (replaces the first sentence):** on success the server stores the token (U19), replacing any previous one
  **without revoking the previous grant**. The README notes that old grants can be removed under Discord
  User Settings → Authorized Apps, and AC-L1 records what Discord does on reconnect.
- **U15 (replaces):** both OAuth routes accept only `GET`. The bearer middleware runs first: a non-GET request without a
  valid bearer gets 401 (exactly as for any other non-exempt route), and with a valid bearer gets 405.
- **U33 (replaces the count):** `smoke.sh` checks that the tool list has **at most 19** entries and contains all five
  account tools (intent omissions vary).
- **AC-L1 (adds):** reconnect once (`connect_discord_account(reconnect=true)`) and confirm `list_my_guilds` still works
  with the new token.

## 12. Amendment A3.2 (2026-10-29, after review round 2)

- **Status:** Approved — 2026-10-29, approved by: human.
- **Trigger:** minor n1 in `review-round-2.md`. Decision made by the human in chat on 2026-10-29.

| # | Issue | Decision |
|---|---|---|
| D33 (n1) | Disconnect cannot delete the token file | **Return an error and say so**: keep the built behavior and amend U18. |

- **U18 (adds a final sentence):** if deleting the token file fails (for example, a permission error), the in-memory
  token is still cleared and the revoke result is unaffected, but the tool returns `kind=unavailable` with a hint that
  the token file is still on disk and must be deleted by hand (the hint names the configured path, never its contents).
  The README says the same.
