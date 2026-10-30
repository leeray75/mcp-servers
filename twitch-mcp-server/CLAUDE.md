# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Process: Spec-Driven Development is mandatory.** Changes to this server follow the SDD workflow in
> [`../AGENTS.md`](../AGENTS.md): plan with the human, write a spec, get explicit approval, then implement, review
> (fix loop, max 5 rounds) and write a summary report. This file stays authoritative for this server's technical
> invariants; AGENTS.md governs process. The technical spec is [`docs/SPEC.md`](docs/SPEC.md) (approved 2026-10-29);
> requirement numbers `R1`–`R57` and decisions `D1`–`D10` below refer to it. Amendment 3
> (`docs/specs/simplification/SPEC.md`, S1–S15) removes R23 and parts of the TLS spec; `S1`–`S15` refer to it.

## What this is

A self-hosted MCP server (Python 3.12, official `mcp` SDK 2.3) that gives an agent access to Twitch through the
Helix REST API: **16 app-token read tools** by default, the other **7 reads plus 2 account tools** only once an
account is actually signed in, and **16 write tools**
across six groups that exist only when both write kill switches are thrown. Streamable HTTP on
`127.0.0.1:8770` (TLS optional, see Architecture) behind a bearer token, in Docker inside WSL2. `README.md` covers operation;
`docs/specs/initial-build/` holds the build record.

Two credentials: an **app token** (`client_credentials`, always minted from `TWITCH_CLIENT_ID` +
`TWITCH_CLIENT_SECRET`, ~58 days, re-minted below 24 h) and an optional **user token**
(authorization-code grant, **no PKCE** — Twitch does not accept `code_challenge`, D8, ~4 h, refreshed from a
`0600` store under `/data`). Each has its own rate-limit bucket because Twitch keeps them separate (R20).

It deliberately **has no EventSub** (neither webhooks nor WebSocket), no IRC chat client, no PubSub, no
"call any endpoint" passthrough, no prompts and no resources (spec §3). Its own HTTP client is the only network
code. Lineage: this server copies `../discord-mcp-server` (gmail → vision → sports → discord → twitch).

## Commands

Run in WSL/Linux with the venv outside the Windows drive (`VENV=~/.venvs/twitch-mcp`, create it with
`uv venv ~/.venvs/twitch-mcp --python 3.12`):

- `make install VENV=... PYTHON=python3` - hash-pinned dev install plus editable package
- `make check VENV=...` - ruff + `mypy --strict` + pytest (coverage gate 90%, branch)
- Single test: `pytest tests/test_service.py::test_name --no-cov`
- pytest runs with a 30-second per-test timeout (`pytest-timeout`, `timeout_method = signal`, S11–S12). Do not raise the value silently.
- `make lock VENV=...` re-resolves and re-hashes `requirements*.txt` with uv (never hand-edit; this regenerates `requirements-dev.txt` with `pytest-timeout`); `make audit` runs pip-audit
- Review runs `make check` and reads the code against the spec. Do not build mutation-testing harnesses or scratch-copy matrices (S13).
- Live (opt-in, never part of `make check`): `pytest -m live --no-cov` and `python tools/try_live.py <tool>` with the operator's `.env`

Operating the container: `scripts/{setup,start,stop,restart,rebuild,logs,status,smoke}.sh` (each with a `.ps1` twin).
`restart.sh --recreate` is needed after editing `.env`. `.env` holds the client secret and the MCP token: never print,
log or commit it.

## Architecture (`src/twitch_mcp/`)

- `__main__.py` entry (uvicorn on `0.0.0.0:8000` inside the container); `config.py` the only module that reads the
  environment (pydantic-settings; errors name fields, never values; required at startup: `MCP_AUTH_TOKEN` ≥32 chars,
  `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_ALLOWED_BROADCASTER_IDS` 1–100 ids of 1–20 digits; a
  `replace-me*` value aborts; `TWITCH_API_BASE`/`TWITCH_AUTH_BASE` must match the two pinned hosts exactly).
- `server.py` builds `TwitchServer` (an `MCPServer` subclass), the `/healthz` route, the `/oauth/*` routes (only when
  user OAuth is on) and the middleware. Its lifespan runs the boot probe (validate bases → mint/refresh app token →
  `GET /oauth2/validate` → load/validate/refresh the user token and resolve `user_id` → tool plan), which sets
  `tool_count` and `twitch_reachable` for `/healthz` and lets the server start **degraded** rather than crash on a
  dead token (R50). Each tool returns `Annotated[CallToolResult, Model]` so `outputSchema` comes from the model, and
  unset keys are omitted (`exclude_unset`), never emitted as `null` (R38). `call_tool` is overridden to reject unknown
  tools, unknown arguments and schema violations in the sibling's R30 error shape before any service call (R39).
  `server.py` deliberately has **no** `from __future__ import annotations` (the SDK reads `Field(...)` metadata at
  registration time).
- `service.py` `TwitchService`: one method per tool (guard → identity → confirmation (writes) → client → projection).
- `twitch_client.py` **the only module that imports httpx** (pinned by a grep test, R11): host pinning to the two
  bases, `GET`/`POST`/`PATCH`/`PUT`/`DELETE`, `Client-Id` + `Bearer` asserted before the socket (R13), redirects
  refused (R12), streamed byte cap (R14), read retries on 5xx/transport with a single 503 retry (R15), **no write
  retries ever** (R16), 429 bucket parking (R21/R22), the `401` circuit (R24), Helix list-repeat query encoding and
  validated/percent-encoded path segments (R17/R18), numeric-code error mapping, and the two fixed
  `POST https://id.twitch.tv/oauth2/token` (+ `/oauth2/revoke`) form calls.
- `ratelimit.py` app and user buckets keyed by token type, learned from `Ratelimit-Limit/-Remaining/-Reset` only,
  injectable clock, no network import (R19–R22). **No limit value or point cost is ever baked into the code.**
- `cache.py` TTL/LRU cache bounded by entries and bytes, used for identity verification (R30), metadata
  reads and the R26 owner lookup only (S4).
- `guard.py` broadcaster allowlist: default-deny, enforced **before** any request, one identical denial message for
  unknown id / 403 / 404 / 5xx so the response cannot probe the allowlist (R25–R27).
- `identity.py` the operator's own `user_id` from `/oauth2/validate` (never from an argument) and verified
  moderator/editor status from `GET /moderation/moderators` / `GET /channels/editors`, cached for
  `TWITCH_IDENTITY_TTL_SECONDS` (default 300) and invalidated on token refresh, disconnect and the first `401`
  (R29/R30).
- `confirmation.py` single-use in-memory confirmation tokens bound to `(tool, canonical-args-hash, broadcaster_id)`,
  TTL `TWITCH_CONFIRM_TOKEN_TTL_SECONDS` (30–3600), cleared on disconnect, refresh, circuit open and process exit;
  never logged, never on disk, never in a result other than the one that minted them (R31–R34).
- `capabilities.py` group switches + token presence + `TWITCH_DISABLE_<TOOL>` → the ordered tool plan. It reads no
  scope table: the plan depends on configuration and token presence only, and a missing scope is read from Twitch's
  own 401/403 body at call time (D10). No tool is ever added at runtime (R36).
- `oauth.py` authorize-URL builder, the pending flow (single-use `state`, `iss`, 10-minute TTL), the `0600` token
  store and `UserAuth` (refresh under one lock, R47–R49). It never imports httpx. `disconnect_twitch_account` deletes
  only the server's own stored credential and calls `POST /oauth2/revoke` — no other account action (R51).
- `models.py` pydantic result models. `project.py` raw Helix JSON → models.
- `errors.py` `TwitchMcpError(kind, message, code, http_status, retryable, retry_after, hint)`; `kind` derives from
  HTTP status plus Twitch's numeric/error code, never from message text (R43/R44). A missing-scope `401`/`403`
  returns `kind=permission`, `retryable=false`, and a `hint` naming the scope Twitch asked for plus the scopes the
  token holds — the message says "not authorized", never "no results" (R45).
- `security.py` bearer + Host/Origin middleware and `logging_setup.py` redacting JSON logs (both copied from the
  sibling and adapted). There is no plaintext-refusal middleware (S7); uvicorn refuses plaintext at the TLS
  handshake.
- TLS is opt-in and both-or-neither (`TWITCH_TLS_CERT_FILE`, `TWITCH_TLS_KEY_FILE`). The key's permissions and
  location are not checked (S5–S6).
- uvicorn terminates TLS when both TLS variables are set; the certificate material is mounted read-only at
  `/certs`. The app never redirects a plaintext request (S7).

## Invariants to preserve

- **Tool set** (R35–R37): exactly the 23 reads of spec §6.4 in order, then the 2 account tools when
  `TWITCH_USER_OAUTH=true`, then write tools whose group is in `TWITCH_WRITE_GROUPS` while
  `TWITCH_ENABLE_WRITES=true`. `tests/test_server.py` pins the exact names and order **for every switch
  combination**. Default plan is the 16 app-token reads; a signed-in account takes it to 25 (23 reads + 2 account);
  maximum is 41 (A2-D1). A tool is omitted only for a disabled group, a missing
  token type, or `TWITCH_DISABLE_<TOOL>=true` — and nothing is ever added at runtime.
- **Two kill switches** (R28): with `TWITCH_ENABLE_WRITES=false` (default) or an empty `TWITCH_WRITE_GROUPS` (default)
  no write tool is registered. Do not collapse them into one flag.
- **Writes never retry** (R16), under any condition — not 5xx, not transport error, not 429. An uncertain write
  outcome is `kind=unknown_outcome` with a hint to check the channel. A test asserts the retry helper is unreachable
  from every write path. Reads do retry (R15); keep the asymmetry.
- **Verified identity** (R29): a write targets the token's own `user_id` or a channel where `identity.py` verified
  moderator/editor status. `TWITCH_ALLOWED_BROADCASTER_IDS` is necessary, never sufficient, for a moderation or
  channel-management write. Verification is invalidated on refresh, disconnect and the first `401` (R30).
- **Confirmation tokens** (R31–R34): the first turn of every write returns `kind=confirmation_required` as a
  **successful** (non-`isError`) result carrying the resolved plan and a token, and sends no write. Tokens are
  single-use, TTL-bound, bound to `(tool, canonical-args-hash, broadcaster_id)`, cleared on disconnect/refresh/circuit
  open/exit, never logged, never persisted, never echoed in a later result. A valid token **still** re-runs the guard
  before dispatch.
- **Uniform denial** (R27): every lookup failure — unknown id, 403, 404, 5xx — returns the identical
  `forbidden_channel` message. Auth, rate-limit and unavailable pass through unchanged. A guard refusal causes zero
  network activity (a test counts requests).
- **Headers-only rate limits** (R19): limits come from `Ratelimit-Limit/-Remaining/-Reset` only. Never hard-code an
  endpoint's point cost or a limit value (a test greps for a baked-in 800). App and user buckets stay separate (R20).
  A wait beyond `TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS` fails fast with `retry_after` and makes no request (R21). A
  shared/global 429 is never replayed in-call and never counted as invalid (R22).
- **The `401` circuit** (R24): the first `401` from the API base stops all further upstream traffic for the process
  lifetime; later calls return `kind=unauthenticated` without a request. Only a restart clears it.
- **One httpx module** (R11): only `twitch_client.py` imports httpx. Only the two pinned hosts are contacted;
  redirects are never followed. `POST` goes only to `/oauth2/token` (and `POST /oauth2/revoke`); `DELETE`/`PUT`/
  `PATCH`/`POST` only to the fixed `/helix/...` templates of spec §6.4. No agent-supplied string becomes a raw path
  segment: ids are digits, cursors are base64-charset, logins match `[A-Za-z0-9_]{1,25}`, all percent-encoded (R17).
  Query lists repeat the parameter per value (`&id=1&id=2`), never comma-delimited (R18).
- **Empty means empty** (R45): a missing scope is `kind=permission` naming the scope Twitch requested and the scopes
  held — never an empty list, never "no results". A `pagination.cursor` of `{}` is `has_more=false` (R42); Helix
  lists are dynamic views and may repeat or drop entries between pages (the README says so).
- **Complete pages** (R41): a page is returned in full; nothing is truncated to fit a size budget.
  `tests/test_sizes.py` asserts each tool's worst-case structured projection at its maximum `first` is under 4 MiB —
  keep the README figures in step with it. Over the byte cap the failure is `kind=too_large` with a hint to lower
  `first` (R14), never a shortened result. Mutually exclusive cursors in one call are a schema error (R40).
- **Errors** (R43/R44): every tool failure is an `isError` tool result with `{kind, message, code, http_status,
  retryable, retry_after, hint}`. JSON-RPC errors are reserved for protocol faults and unexpected exceptions
  (generic message).
- **Logging and privacy** (R52–R54): logs, errors and `/healthz` never carry the MCP bearer, the client secret, the
  OAuth code, `state`, the refresh token, access tokens, URLs with query strings, chat message bodies or response
  bodies. `/healthz` returns only `{status, version, tool_count, twitch_reachable}` (R3). Lines carry the route
  template (`GET /channels`, `POST /moderation/timeouts`), status, token type (app|user), duration and outcome —
  never argument values. Exception: a **write** log records the broadcaster id, tool name and outcome, for
  auditability of irreversible actions (R53). `logging_setup.py` redacts by key name and by regex (Bearer,
  `client_secret`, `access_token`, `refresh_token`, `code=`); a test asserts each pattern.
- **OAuth** (R46–R51): off by default, then no `/oauth/*` routes, no token file, no account tools and app-token
  reads only. When on, the two routes are exempt from bearer auth and protected instead by the loopback bind, the
  Host/Origin guard, single-use `state`, an `iss` check and a 10-minute TTL. The `0600` store under `/data` is the
  **sole** persistent artifact. Refresh runs under one lock; a refresh failure forces re-auth and clears the store —
  never a silent app-token fallback for a user-only tool (R49).
- **State is process-wide** (cache, buckets, circuit, confirmations). Nothing is keyed to an MCP session or
  connection (spec §7).
- **Tests never touch the network** (R55): an autouse fixture fails any real socket use; fixtures are synthetic and
  stay under 50 KB. `make check` is the gate (R56) with the 90% branch coverage floor; `requirements*.txt` are
  hash-pinned and regenerated only by `make lock`.
- **Container** (R1/R2): `127.0.0.1:8770:8000` only, UID 10001, read-only rootfs, `cap_drop: ALL`,
  `no-new-privileges`, memory limit, tmpfs `/tmp`, and the only host mount is `./data` at `/data` from
  `docker-compose.oauth.yml`. A test asserts the compose string carries the `127.0.0.1:` prefix (R1). When TLS is configured, `./certs` is mounted read-only at `/certs` (T11).
- **TLS** (T1–T4, T6, T7, T9–T12, S5–S7): both TLS variables or neither (T1); plain HTTP unchanged when neither is
  set (T2); `redirect_uri` and `sign_in_url` use `https` when TLS is on (T3); the key's mode is not checked and key
  permissions are the operator's responsibility (S5); `/healthz` reports `tls` and needs no bearer (T6).
- **Mirror-the-sibling rule**: `security.py`, `logging_setup.py` and `scripts/lib/` drifted from
  `../discord-mcp-server` by design; a fix to one copy does not propagate. Check the sibling when fixing a
  shared-pattern bug.
