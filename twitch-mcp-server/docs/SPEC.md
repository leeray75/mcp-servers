# SPEC — twitch-mcp-server

**Status:** Approved
**Date:** 2026-10-29 (approved by: human, 2026-10-29 19:28 -04:00)
**Amends:** none (new server; the first spec for this directory)
**Amendment 1:** 2026-10-30 (approved by: human, 2026-10-30 00:55 -04:00) — see §0.
**Amendment 2:** 2026-10-30 (approved by: human, 2026-10-30 08:41 -04:00) — see §0b.
**Amendment 3:** 2026-10-30 (approved by: human) — see docs/specs/simplification/SPEC.md. Superseded in place by it, text not rewritten: R23 (§5.4), and the inventory lines at §2 (line 93), §5.2 (line 147), §6.1 (line 298), §6.5 (line 405) and the §8 row for `test_budget.py` (line 455).
**Process:** [../../AGENTS.md](../../AGENTS.md) (SDD). This file becomes authoritative for the server once approved;
`AGENTS.md` governs process, this file governs technical invariants.

---

## 0. Amendment 1 (2026-10-30, approved by: human)

Corrections and rulings made after the first implementation pass exposed defects in this spec itself.

| Ref | Ruling | Effect on this spec |
|---|---|---|
| **A1-D1** | Keep all **16** write tools / **41** total tools. The §6.4 table is authoritative; the prose count was an authoring error. | §6.4 prose corrected: "Writes (15)" → "**Writes (16)**", "Maximum 40 tools" → "**Maximum 41 tools**", §11 "up to 40" → "up to 41". Tests pinning 40 are updated to 41. |
| **A1-D2** | Where implementation and test disagree, **this spec is the authority**; fix the diverging side. Where the spec is silent, **stop and escalate to the human** — no assumption. | Governs interpretation of R1–R57 for the remainder of this build. |
| **A1-D3** | Mechanical test-harness fixes are pre-authorized (list/tuple comparison, JSON-Schema `$ref` resolution, `ExceptionGroup` unwrapping, missing fake routes). **Semantic** assertion changes require a fresh human ruling. | Constrains the Phase 4 implementer. |
| **A1-D4** | Phase 5 review runs with a **read-only reviewer subagent** and **stops after 3 rounds** (not the default 5). Anything unresolved at round 3 is escalated to the human. | Overrides the round-5 escalation point in `AGENTS.md` §7 for this feature. |
| **A1-D5** | `tools/try_live.py` is written but **not executed**; acceptance criteria A2/A3 are recorded as UNVERIFIED until the operator runs it with real credentials. | A2/A3 delivery status. |
| **A1-D6** | `scripts/auth.{sh,ps1}` are **removed** from the spec — no requirement ever defined their behaviour, so they are not built. | §6.1 directory listing corrected. |
| **A1-D7** | The freshly uv-resolved dependency versions (pydantic 2.14.2, pydantic-core 2.50.2, opentelemetry-api 1.45.2) are **accepted** as-is, even though they run one minor ahead of the sibling servers' venvs. | Closes SPEC-GAP 2. |

No requirement's behaviour was changed by this amendment; A1-D1 corrects a self-contradiction in the tool
*count*, and the rest close gaps or bound the process.

---

## 0b. Amendment 2 (2026-10-30, approved by: human)

Rulings made after the first review round exposed places where this spec was silent about the Helix wire
format, about the registered-tool count, and about how a Helix `404` on a read must be reported. Each was
escalated under **A1-D2** rather than assumed.

| Ref | Ruling | Effect on this spec |
|---|---|---|
| **A2-D1** | The **default** registered tool set is the **16 app-token reads**. The remaining 7 user-token reads appear **only once an account is signed in**, taking the read count to 23; with writes enabled the ceiling stays **41** (**A1-D1**). | Clarifies R37 and the §6.4 prose. `capabilities.DEFAULT_REGISTERED` pins the 16; `MAX_TOOL_COUNT` pins 41. Tests asserting a default of 23 are corrected to 16, and a signed-in test asserts 23. |
| **A2-D2** | A Helix **`404` on a read** is reported as the new **`not_found`** kind, **not** `forbidden_channel`. **R27 is unchanged** for what it actually governs: a broadcaster-id **ownership lookup performed by the guard** still collapses every failure (unknown id, 403, 404, 5xx) into the identical `forbidden_channel` denial, so allowlist membership stays unprobeable. The two paths are distinct: the guard's lookup answers "may this server ask at all", the read's `404` answers "Twitch has no such object". | R44 taxonomy gains `not_found` (non-retryable, not in `PASS_THROUGH_KINDS`). A write that receives a `404` still becomes `unknown_outcome` (R16), never `not_found`. |
| **A2-D3** | Response shapes follow the **Helix documentation**, not the earlier draft models, for the three endpoints the first review flagged: **`GET /chat/chatters`** returns a flat user list (`total` drives `chatter_count`); **`GET /schedule`** returns `data` as an **object** carrying broadcaster fields plus a `segments` array (not a bare array); **`GET /moderation/automod/settings`** returns **one record with integer level fields** (`overall_level`, `disability`, `aggression`, `sexuality_sex_or_gender`, `misogyny`, `bullying`, `swearing`, `race_ethnicity_or_religion`, `sex_based_terms`), not a list of `{category, level}` items. | Corrects `models.ChattersResult`, `models.ScheduleSegment`/`ScheduleCategory` and `models.AutomodSettings`, their projections in `project.py`, and the fakes in `tests/test_sizes.py`. |
| **A2-D4** | Phase 5 review budget is **5 rounds**, superseding the 3-round cap set by **A1-D4**. Anything unresolved at round 5 is escalated to the human. | Overrides A1-D4; restores the `AGENTS.md` §7 default for this feature. |
| **A2-D5** | Live verification stays **operator-run**. `tools/try_live.py` and `docker compose up` are **not** executed by the agent; acceptance criteria **A2, A3 and A7 are recorded UNVERIFIED** — verified only by tests that read the compose file, Dockerfile and scripts statically. | Delivery status of A2/A3/A7, extending A1-D5 to A7. |

A2-D1 and A2-D3 change no requirement's *intent*; they pin behaviour this spec left to "as documented by
Twitch". A2-D2 adds one error kind to the R44 vocabulary and states explicitly that R27 was never meant to
cover direct read `404`s.


---

## 1. Problem / motivation

Agents have no sanctioned way to read Twitch data or to operate a channel's own broadcast tooling. Every existing
option is a hosted third-party integration or a desktop bot, neither of which respects this repo's posture: loopback-only,
self-hosted, default-deny allowlist, one network module, no raw "call any endpoint" escape hatch.

The repo already proves this shape twice over. `../discord-mcp-server` is a REST-only upstream with a hand-rolled
`httpx` client, a fixed tool set pinned by a test, header-derived rate limits and a default-deny guild allowlist.
`../gmail-mcp-server` proves the "human reviews before anything leaves the machine" pattern by stopping at a draft.
Twitch Helix maps onto the same structure: a single documented REST host, opaque string ids, cursor pagination,
and a token-bucket rate limit published in response headers.

Twitch adds two things the siblings do not have: **write tools that act in public and act on third parties** (chat
messages, timeouts, VIP grants), and **a push API (EventSub) that this architecture cannot host**. Both need an
explicit answer before code exists — which is the purpose of this document.

## 2. Goals

- G1 — Read Twitch data (users, channels, streams, games, videos, clips, polls, predictions, moderation state,
  chat state, schedule) for broadcaster ids on a default-deny allowlist.
- G2 — Operate the operator's own channel and channels the operator genuinely moderates: channel settings, polls,
  predictions, chat messages, timeouts/bans, automod terms, VIPs and moderators.
- G3 — Make G2 safe by construction: writes are off until explicitly enabled, gated by a single-use confirmation
  token, authorized against *verified* identity rather than a trusted list, and never automatically retried.
- G4 — Preserve the sibling invariants: loopback bind, non-root read-only container, one `httpx` module, config that
  names fields never values, redacted logs, test-pinned tool set, network-free tests, coverage gate.
- G5 — Degrade honestly: a missing scope returns `kind=permission` naming the scope Twitch asked for, never an
  empty list.

## 3. Non-goals

- **EventSub, in any form.** Webhooks need a publicly reachable HTTPS endpoint, which a `127.0.0.1`-bound container
  cannot provide. EventSub-over-WebSocket (`wss://eventsub.wss.twitch.tv`) is technically reachable but converts a
  request/response server into a stateful daemon with subscriptions, reconnects, challenge verification and a
  subscription quota — a different server than the five siblings. Deferred to its own spec if ever wanted.
- No IRC/TMI chat client, no PubSub, no extensions API, no analytics (`/extensions/live`), no ads/schedule *writes*.
- No multi-account support: exactly one app identity and at most one user identity per process.
- No "call any endpoint" passthrough tool, no prompts, no resources.
- No Twitch-side app registration automation; the human registers the app at the developer console.
- No persistence beyond the user token file. Caches, budgets and confirmation tokens are process-wide and lost on restart.

## 4. Decisions

Phase 1 asked the human four questions. Recorded verbatim:

| # | Question | Chosen |
|---|---|---|
| D1 | Spec scope | **Tier A+B+C** — reads, user OAuth, and write tools |
| D2 | Which write groups | **Everything** — channel, polls, predictions, timeouts/bans, chat send, automod, VIP/moderator |
| D3 | Safety model | **Write groups off by default + confirm-token gate on user-facing tools** |
| D4 | Write-target authorization | **Own channel always; other targets need verified moderator/editor status** |

Derived from repo house style, surfaced here rather than assumed silently — object at the approval gate and they
change like any other requirement:

| # | Item | Choice | Basis |
|---|---|---|---|
| D5 | Stack | Python 3.12, official `mcp` SDK, Starlette + uvicorn, hand-rolled `httpx` client (no SDK) | matches 4 of 5 siblings; Twitch publishes no Python SDK |
| D6 | Port | **8770** (8765–8769 are taken) | root `CLAUDE.md`: keep ports distinct |
| D7 | Coverage gate | 90% branch | matches discord, the closest sibling |
| D8 | User token flow | Authorization-code grant, **no PKCE** | Twitch does not accept `code_challenge`; the client secret is required at exchange |
| D9 | App token | Always minted from `TWITCH_CLIENT_ID` + `TWITCH_CLIENT_SECRET` via `client_credentials` | one code path; ~58-day expiry, re-minted when `expires_in` nears |
| D10 | Required-scope table | **Not hard-coded.** Tool registration depends on group switches + token presence only; the missing scope is read from Twitch's own 401/403 body at call time | a hardcoded scope table rots the day Twitch renames a scope; Twitch already names it |

## 5. Requirements

### 5.1 Transport and container

- **R1** — The server speaks Streamable HTTP on `0.0.0.0:8000` inside the container; `docker-compose.yml` publishes
  `127.0.0.1:8770:8000` and nothing else. A test asserts the compose string carries the `127.0.0.1:` prefix.
- **R2** — The container runs as UID 10001 with a read-only rootfs, all capabilities dropped, no-new-privileges, a
  memory limit, and no host mounts other than `./data` at `/data` (present only when user OAuth is enabled) and
  `./certs` at `/certs` **read-only** (present only when TLS is configured, T11; never writable).
- **R3** — `GET /healthz` requires no bearer token and returns only `{status, version, tool_count, twitch_reachable}`
  plus the `tls` boolean added by T6 of `docs/specs/tls-oauth-redirect` — never tokens, ids, logins, scopes, URLs or a
  certificate path.
- **R4** — Every other route requires `Authorization: Bearer <MCP_AUTH_TOKEN>`. `MCP_AUTH_TOKEN` is ≥32 characters or
  startup fails naming the field.
- **R5** — Host and Origin headers are validated against a loopback allowlist by the `security.py` middleware on every
  request, including `/oauth/*`.

### 5.2 Configuration

- **R6** — `config.py` is the only module that reads the environment. It uses pydantic-settings; every validation
  error names the field and never echoes a value.
- **R7** — Required at startup: `MCP_AUTH_TOKEN`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`,
  `TWITCH_ALLOWED_BROADCASTER_IDS`. A missing or `replace-me*` value aborts startup with a field-named error.
- **R8** — `TWITCH_ALLOWED_BROADCASTER_IDS` holds 1–100 comma-separated Twitch user ids (1–20 digits each). An empty
  or absent value aborts startup — the server cannot run with an empty allowlist.
- **R9** — `TWITCH_API_BASE` must match `https://api.twitch.tv/helix` and `TWITCH_AUTH_BASE` must match
  `https://id.twitch.tv` before any request is issued. Any other value is a startup failure.
- **R10** — All numeric knobs are range-validated at startup with defaults stated in `.env.example`: timeout (1–60 s),
  max response bytes (64 KiB–16 MiB), max rate-limit wait (0–120 s), max 429 replays (0–10), confirm-token TTL
  (30–3600 s), tool calls per minute, cache TTL/entries/MB, log level.

### 5.3 Network

- **R11** — `twitch_client.py` is the only module that imports `httpx`. A test greps every other source file for the
  token `httpx` and fails on a hit.
- **R12** — Only `TWITCH_API_BASE` and `TWITCH_AUTH_BASE` hosts are contacted. Redirects are never followed; a 3xx is
  an error, not a hop.
- **R13** — Every request carries both `Client-Id` and `Authorization: Bearer`. A request would-be-sent without either
  raises before the socket.
- **R14** — Response bodies are streamed with a hard byte cap. A body over `TWITCH_MAX_RESPONSE_BYTES` is refused with
  `kind=too_large` and a hint to lower `first` — results are never silently truncated.
- **R15** — **Reads** retry on 5xx and transport errors, bounded by `TWITCH_MAX_5XX_RETRIES`. **A 503 is retried once**
  per Twitch's own guidance.
- **R16** — **Writes are never retried, under any condition** — not on 5xx, not on transport error, not on 429. A write
  whose outcome is unknown returns `kind=unknown_outcome` with a hint telling the human to check the channel rather
  than re-issue. A test asserts the retry helper is unreachable from every write path.
- **R17** — Only `POST` to `/oauth2/token`, `DELETE`/`PUT`/`PATCH`/`POST` to fixed `/helix/...` path templates, and
  `GET` elsewhere. Path segments derived from arguments must match a validated pattern (user id digits, cursor base64
  charset, login `[A-Za-z0-9_]{1,25}`) and are percent-encoded. No agent-supplied string becomes a URL path segment raw.
- **R18** — Query lists repeat the parameter per value (`&id=1&id=2`), never comma-delimited, per the Helix convention.

### 5.4 Rate limiting and budget

- **R19** — Limits are learned from `Ratelimit-Limit`, `Ratelimit-Remaining`, `Ratelimit-Reset` only. No endpoint's
  point cost is hard-coded, and no limit value is baked into the code.
- **R20** — The app bucket and the user bucket are tracked separately, keyed by token type, because Twitch maintains
  them separately.
- **R21** — A 429 parks that bucket until `Ratelimit-Reset`. If the wait exceeds `TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS`
  the call fails fast with `kind=rate_limited` and `retry_after`, and the request is not made.
- **R22** — A shared/global 429 is never replayed within the same call and is not counted as an invalid request.
- **R23** — Tool-call token buckets (`budget.py`) cap calls per minute overall and per tool. Excess calls are refused,
  not queued.
- **R24** — The first `401` from `TWITCH_API_BASE` opens a circuit that stops all further upstream traffic for the
  process lifetime; subsequent calls return `kind=unauthenticated` without a request. Only a process restart clears it.

### 5.5 Authorization: reads

- **R25** — Every tool taking a `broadcaster_id`, `user_id`, `from_id` or `to_id` passes through `guard.py` **before**
  any request is made. An id not in the allowlist produces `kind=forbidden_channel` and zero network activity.
  A test counts requests and asserts the count is 0.
- **R26** — A `channel_id` or `video_id` whose owner is unknown costs exactly one cached lookup (`GET /videos` or
  `GET /channels`) to learn its broadcaster, and nothing else.
- **R27** — Every lookup failure (unknown id, 403, 404, 5xx) returns the identical `forbidden_channel` message, so the
  response cannot be used to probe which channels are on the allowlist. Auth, rate-limit and unavailable pass through
  unchanged.

### 5.6 Authorization: writes

- **R28** — No write tool is registered unless `TWITCH_ENABLE_WRITES=true` **and** its group appears in
  `TWITCH_WRITE_GROUPS`. Both defaults are off. With both off, the server exposes only read tools and the tool-set test
  asserts the exact default list.
- **R29** — Every write targets either (a) the operator's own `broadcaster_id`, taken from the token's `user_id` per
  `/oauth2/validate` and never from an argument, or (b) a channel where `identity.py` has **verified** the operator
  appears in `GET /moderation/moderators` (moderation groups) or `GET /channels/editors` (channel-management group)
  for the current broadcaster. A target that is neither returns `kind=not_authorized` with no write request.
- **R30** — Verification results are cached (TTL `TWITCH_IDENTITY_TTL_SECONDS`, default 300) and are invalidated on
  token refresh, disconnect, and the first `401`.
- **R31** — A write call lacking a valid `confirm` token performs guard + identity checks, then returns
  `kind=confirmation_required` with the resolved plan (tool, broadcaster, validated argument summary) and a token.
  **No write request is sent.**
- **R32** — Confirmation tokens are single-use, in-memory, TTL-bound, and bound to the tuple
  `(tool, canonical-args-hash, broadcaster_id)`. A token replayed, expired, or presented with differing arguments
  returns `kind=stale_confirmation` and no request.
- **R33** — Confirmation tokens are cleared on disconnect, on token refresh, on the `401` circuit opening, and on
  process exit. They are never logged, never written to disk, and never included in a tool result other than the one
  that minted them.
- **R34** — A write whose `confirm` token is valid still re-runs the guard check before dispatch. The guard is never
  bypassed by a token.

### 5.7 Tool set

- **R35** — The tool set is exactly the list in §6.4, in that order, minus tools omitted by the boot plan.
  `tests/test_server.py` pins the exact names and order for each of the switch combinations.
- **R36** — Registration omits a tool only for: (a) its write group being disabled, (b) a required token type being
  absent, or (c) an explicit `TWITCH_DISABLE_<TOOL>=true`. No tool is ever added at runtime.
- **R37** — There are no write tools beyond the sixteen in §6.4, no prompt, no resource, and no endpoint passthrough.
- **R38** — Each tool returns `Annotated[CallToolResult, Model]` so `outputSchema` derives from the pydantic model, and
  unset keys are omitted (`exclude_unset`) rather than emitted as `null`.
- **R39** — `call_tool` rejects unknown tool names, unknown arguments and schema violations in the R30 error shape
  used by `../discord-mcp-server`, before any service call.

### 5.8 Pagination and results

- **R40** — List tools expose `first` (bounded by the endpoint's documented maximum), `after`, and where supported
  `before`. Mutually exclusive cursors in one call are rejected as a schema error.
- **R41** — A page is returned complete; results are never truncated to fit a size budget. `tests/test_sizes.py`
  asserts every tool's worst-case structured projection at its maximum `first` stays under 4 MiB, and the README
  figures stay in step with it.
- **R42** — A `pagination.cursor` of `{}` is reported as `has_more=false`. Callers are told in the README that Helix
  lists are dynamic views and may repeat or drop entries between pages.

### 5.9 Errors

- **R43** — All tool failures are `isError` tool results carrying `{kind, message, code, http_status, retryable,
  retry_after, hint}`. JSON-RPC errors are reserved for protocol faults and unexpected exceptions (generic message).
- **R44** — `kind` derives from HTTP status plus Twitch's numeric/error code, never from message text.
- **R45** — A `401`/`403` whose body names a missing scope returns `kind=permission`, `retryable=false`, and a `hint`
  containing the scope string Twitch named plus the scopes the token actually holds. The message says
  "not authorized", never "no results" — an empty list always means genuinely empty.

### 5.10 OAuth (user identity)

- **R46** — User OAuth is off by default (`TWITCH_USER_OAUTH=false`). Off: no `/oauth/*` routes, no token file, no
  user-token tools, and reads use the app token only.
- **R47** — On, `GET /oauth/start` and `GET /oauth/callback` are exempt from bearer auth and protected instead by the
  loopback bind, the R5 Host/Origin guard, a single-use random `state`, an `iss` check and a 10-minute TTL.
- **R48** — The token store is written at `0600` under `/data`, is the sole persistent artifact, and is never
  serialized into a log, error, resource or tool result.
- **R49** — Refresh happens under one lock; concurrent callers await a single refresh. A refresh failure forces
  re-auth and clears the store, never a silent app-token fallback for a user-only tool.
- **R50** — Third-party session rule: the boot probe calls `GET /oauth2/validate` for each present token and, if
  either is invalid, the server starts degraded and reports it — it does not crash and does not keep serving with a
  dead token.
- **R51** — `disconnect_twitch_account` deletes only the server's own stored credential and calls
  `POST /oauth2/revoke`. It performs no other account action.

### 5.11 Logging and privacy

- **R52** — Logs, errors and `/healthz` never contain: the MCP bearer token, the client secret, the OAuth code,
  `state`, the refresh token, access tokens, request URLs with query strings, chat message bodies, or response bodies.
- **R53** — Log lines carry the route template (`GET /channels`, `POST /moderation/timeouts`), status, token type
  (app|user), duration and outcome — never argument values. The exception: a write log records the broadcaster id,
  tool name and outcome, for auditability of irreversible actions.
- **R54** — `logging_setup.py` redacts by key name and by regex (Bearer, `client_secret`, `access_token`,
  `refresh_token`, `code=`), and a test asserts each pattern.

### 5.12 Tests

- **R55** — An autouse fixture makes any real network call fail the test run. Fixtures are synthetic; each stays
  under 50 KB.
- **R56** — `make check` runs ruff, `mypy --strict` and pytest with a 90% branch coverage gate. `make audit` runs
  pip-audit and is required when dependencies change. `requirements*.txt` are hash-pinned and regenerated only by
  `make lock`.
- **R57** — Every requirement above maps to at least one named test in the implementation report's traceability table.

## 6. Design

### 6.1 Directory layout

Mirrors `../discord-mcp-server` deliberately (lineage: gmail → vision → sports → discord → **twitch**):

```
twitch-mcp-server/
  src/twitch_mcp/
    __main__.py        uvicorn entry; 0.0.0.0:8000 in-container
    config.py          sole env reader (pydantic-settings)
    server.py          MCPServer subclass, /healthz, /oauth/*, middleware, lifespan boot probe + tool plan
    service.py         TwitchService: one method per tool (guard → identity → client → projection)
    twitch_client.py   ONLY module importing httpx; host pinning, GET/POST/PATCH/PUT/DELETE, headers, 429, 401 circuit
    ratelimit.py       app+user point buckets from headers; injectable clock; no network import
    budget.py          per-minute tool-call buckets
    cache.py           TTL/LRU bounded by entries and bytes (metadata + identity only)
    guard.py           broadcaster allowlist (default-deny, uniform-denial)
    identity.py        own user id from /validate; moderator + editor verification (D4)
    confirmation.py    single-use confirm tokens: mint, consume, invalidate (D3)
    capabilities.py    group switches → ordered tool plan
    oauth.py           authorize URL builder, pending flow, 0600 token store, UserAuth refresh-under-lock
    models.py          pydantic result models
    project.py         raw Helix JSON → models
    errors.py          TwitchMcpError(kind, message, code, http_status, retryable, retry_after, hint)
    security.py        bearer + Host/Origin middleware (copied from sibling, adapted)
    logging_setup.py   redacting JSON logs (copied from sibling, adapted)
  tests/               one file per module + test_server, test_sizes, test_security, test_write_safety
  tools/try_live.py    opt-in real calls with the operator's .env; never run by pytest
  scripts/{setup,start,stop,restart,rebuild,logs,status,smoke}.{sh,ps1} + lib/
  Dockerfile  docker-compose.yml  docker-compose.oauth.yml  Makefile  pyproject.toml
  requirements.txt  requirements-dev.txt  .env.example  README.md  CHANGELOG.md  CLAUDE.md
  docs/SPEC.md  docs/specs/initial-build/  docs/reports/
```

`server.py` deliberately has **no** `from __future__ import annotations` — the SDK reads `Field(...)` metadata at
registration time.

### 6.2 Two credentials, two buckets

| | App token | User token |
|---|---|---|
| Obtained | `POST /oauth2/token` `grant_type=client_credentials` | `/oauth/start` → consent → `GET /oauth/callback` → `POST /oauth2/token` `grant_type=authorization_code` |
| Sent | `Client-Id` + `Bearer` | `Client-Id` + `Bearer` |
| Lifetime | ~58 days (`expires_in`); re-minted below 24 h | ~4 h; refreshed from a `0600` store |
| Bucket | `Ratelimit-*` for app | `Ratelimit-*` per client-id per user |
| Present when | always | `TWITCH_USER_OAUTH=true` and signed in |

Boot probe order: validate `TWITCH_*_BASE` → mint/refresh app token → `GET /oauth2/validate` (app) → if user OAuth on,
load store, validate user token, refresh if needed, resolve `user_id` → compute tool plan → serve. The probe result
sets `tool_count` and `twitch_reachable` for `/healthz`.

### 6.3 Write path (the part with no sibling precedent)

```
call_tool(update_channel, {broadcaster_id, title, confirm?})
  ├─ capabilities: group 'channel' enabled?              else kind=disabled      (no request)
  ├─ schema validated; unknown args rejected (R39)
  ├─ guard: broadcaster_id allowlisted?                   else kind=forbidden_channel (0 requests)
  ├─ identity: is broadcaster_id the token's own user_id,
  │            or is the operator a verified editor/moderator there?  else kind=not_authorized (0 writes)
  ├─ confirmation: token valid, unexpired, args-hash match? else kind=confirmation_required + mint token (0 writes)
  └─ client.patch(...)  ← exactly once, never retried (R16)
       └─ 401 → open circuit (R24) · 429 → kind=rate_limited, no replay · 4xx → kind from code · transport fail → kind=unknown_outcome
```

The `kind=confirmation_required` result is deliberately a **successful** (non-`isError`) tool result: it is the normal
first turn of every write, and its plan text is what the human or the agent reads before deciding.

### 6.4 Fixed tool set

Reads (23) — `capabilities.READ_TOOLS`, this order:

| # | Tool | Helix | Token |
|---|---|---|---|
| 1 | `twitch_account_status` | `GET /oauth2/validate` | app / user |
| 2 | `get_users` | `GET /users` | app |
| 3 | `get_users_follows` | `GET /users/follows` | app |
| 4 | `get_channels` | `GET /channels` | app |
| 5 | `search_channels` | `GET /search/channels` | app |
| 6 | `get_streams` | `GET /streams` | app |
| 7 | `get_top_games` | `GET /games/top` | app |
| 8 | `get_games` | `GET /games` | app |
| 9 | `get_videos` | `GET /videos` | app |
| 10 | `get_clips` | `GET /clips` | app |
| 11 | `get_polls` | `GET /polls` | app |
| 12 | `get_predictions` | `GET /predictions` | app |
| 13 | `get_emotes` | `GET /chat/emotes` (+`/set`, `/global`) | app |
| 14 | `get_channel_chat_settings` | `GET /chat/settings` | app |
| 15 | `get_channel_chat_badges` | `GET /chat/badges` | app |
| 16 | `get_stream_markers` | `GET /streams/markers` | user |
| 17 | `get_broadcaster_subscribers` | `GET /subscriptions` | user |
| 18 | `get_channel_editors` | `GET /channels/editors` | user |
| 19 | `get_moderators` | `GET /moderation/moderators` | user |
| 20 | `get_banned_users` | `GET /moderation/banned` | user |
| 21 | `get_automod_settings` | `GET /moderation/automod/settings` | user |
| 22 | `get_chat_chatters` | `GET /chat/chatters` | user |
| 23 | `get_schedule` | `GET /schedule` | app |

Account (2) — `TWITCH_USER_OAUTH=true`: `connect_twitch_account`, `disconnect_twitch_account`.

Writes (16) — grouped; each group needs `TWITCH_ENABLE_WRITES=true` + membership in `TWITCH_WRITE_GROUPS`:

| Group | Tools | Helix |
|---|---|---|
| `channel` | `update_channel` | `PATCH /channels` |
| `polls` | `create_poll`, `end_poll` | `POST /polls`, `PATCH /polls` |
| `predictions` | `create_prediction`, `end_prediction` | `POST /predictions`, `PATCH /predictions` |
| `chat` | `send_chat_message` | `POST /chat/messages` |
| `moderation` | `timeout_user`, `remove_timeout`, `ban_user`, `unban_user`, `set_automod_term`, `remove_automod_term` | `POST/DELETE /moderation/timeouts`, `POST/DELETE /moderation/banned`, `PUT/DELETE /moderation/automod/terms` |
| `roles` | `add_vip`, `remove_vip`, `add_moderator`, `remove_moderator` | `POST/DELETE /channels/vips`, `POST/DELETE /moderation/moderators` |

Maximum 41 tools (all groups + user OAuth on); default 23 (reads only, user OAuth off, writes off).

### 6.5 Configuration surface (`.env.example`)

Required: `MCP_AUTH_TOKEN`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_ALLOWED_BROADCASTER_IDS`.
Optional, all defaulted and documented: `HOST_PORT` (8770), `TWITCH_API_BASE`, `TWITCH_AUTH_BASE`,
`TWITCH_TIMEOUT_SECONDS`, `TWITCH_MAX_RESPONSE_BYTES`, `TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS`,
`TWITCH_MAX_429_RETRIES`, `TWITCH_MAX_5XX_RETRIES`, `TWITCH_USER_OAUTH`, `TWITCH_OAUTH_SCOPES`,
`TWITCH_OAUTH_TOKEN_FILE`, `TWITCH_ENABLE_WRITES`, `TWITCH_WRITE_GROUPS`, `TWITCH_CONFIRM_TOKEN_TTL_SECONDS`,
`TWITCH_IDENTITY_TTL_SECONDS`, `TWITCH_DISABLE_<TOOL>`, `TTL_METADATA_SECONDS`, `CACHE_MAX_ENTRIES`, `CACHE_MAX_MB`,
`TOOL_CALLS_PER_MINUTE`, `TOOL_CALLS_PER_MINUTE_PER_TOOL`, `LOG_LEVEL`, and the TLS pair
`TWITCH_TLS_CERT_FILE` / `TWITCH_TLS_KEY_FILE` (both or neither, T1; unset means plain HTTP exactly as before, T2).

### 6.6 Scope request list (operator-side, not code)

`TWITCH_OAUTH_SCOPES` is the space-delimited list sent to `/oauth2/authorize`. It is operator configuration, not a
code table (D10): add only the scopes the groups you enabled need. The README lists them per group so a channel
operator can request `channel:manage:broadcast channel:manage:polls` without ever granting `moderator:manage:timeouts`.

## 7. Security and invariants

Preserved from the siblings: loopback-only bind, no TLS, non-root read-only container, `config.py` as sole env reader
naming fields never values, one `httpx` module pinned by grep, network confined to one host pair, redirects refused,
response byte cap, default-deny allowlist enforced before any request, header-derived rate limits, `isError` tool
results, process-wide (never session-keyed) state, network-free tests, hash-pinned deps, bearer on all routes but
`/healthz`.

New or strengthened for this server:

1. **Irreversibility is acknowledged in code, not prose.** Reads retry; **writes never do** (R16). A lost response on a
   `POST /chat/messages` becomes `kind=unknown_outcome`, because a retry could post twice.
2. **Two independent kill switches** must be thrown before a write tool exists at all (R28). Enabling writes is
   never an accident of a single flag.
3. **Third-party actions require proof, not permission from a config file** (R29). `TWITCH_ALLOWED_BROADCASTER_IDS`
   is a *necessary* condition, never a sufficient one for a moderation write — the moderator list from Twitch is the
   authority, and the cache is invalidated on every identity-relevant state change (R30).
4. **Single-use confirmation tokens are the draft-equivalent** Gmail achieves by refusing to send. Unlike Gmail we
   cannot defer to a human in an inbox, so the token forces an explicit second turn with a bound, expiring,
   replay-proof plan (R31–R34).
5. **Chat messages are written to the audit log as an action but never as content** (R52/R53) — auditable without
   hoarding what the operator typed.
6. **No probe oracle on the allowlist** (R27): uniform denial text on every lookup failure.
7. **Deliberate departure:** tool count (up to 41) is ~3× Discord's. Justification: D2 asked for the full write
   surface across six domains, and each domain's read+write pair is more coherent than a generic passthrough. The
   default plan is 23, and the R35 pins-per-combination test is what keeps the surface from drifting.

Attack surface this design rejects: SSRF via `TWITCH_*_BASE` (R9 pinning), path traversal via ids/cursors (R17),
CSRF on the callback (R47), token leak through logs or `/healthz` (R52), allowlist enumeration (R27), scope-table
rot (D10), and double-submit on flaky networks (R16).

## 8. Test plan and acceptance criteria

`make check` green is the gate. Required suites, following the sibling naming:

| File | Proves |
|---|---|
| `test_config.py` | R6–R10 (field-named errors, ranges, base-URL pinning, empty allowlist aborts) |
| `test_twitch_client.py` | R11–R14, R17–R18 (headers, host pinning, no redirects, byte cap, list-repeat encoding) |
| `test_write_safety.py` | **R15/R16 asymmetry** (reads retry, writes never — asserts retry helper unreachable from write paths), R28–R34, R37 |
| `test_ratelimit.py` | R19–R22 with an injected clock, separate app/user buckets, no hard-coded 800 anywhere |
| `test_budget.py` | R23 |
| `test_guard.py` | R25–R27, incl. request-count == 0 and identical-denial-text assertions |
| `test_identity.py` | R29–R30 (own-channel path, moderator verified/unverified, cache invalidation on refresh) |
| `test_confirmation.py` | R31–R33 (mint, single-use, args-hash mismatch, TTL expiry, cleared on disconnect/refresh) |
| `test_server.py` | R1, R3–R5, R35–R39, R43–R45; tool set pinned per switch combination |
| `test_service.py` | per-tool projection, paging, `exclude_unset`, empty-vs-forbidden (R40–R42, R45, R57) |
| `test_oauth_*.py` | R46–R51 (`state`, `iss`, TTL, 0600 store, refresh-under-lock, revoke-only disconnect) |
| `test_security.py` / `test_logging.py` | R52–R54 redaction patterns, bearer exemption limited to `/healthz` (+`/oauth/*` when on) |
| `test_sizes.py` | R41 worst-case < 4 MiB per tool at max `first` |
| `test_network_guard.py` | R55 autouse net-block actually fires |

Acceptance criteria:

- **A1** `make check` passes: ruff clean, `mypy --strict` clean, coverage ≥90% branch.
- **A2** `pytest -m live --no-cov` against the real API with the operator's `.env` completes `twitch_account_status`,
  `get_users`, `get_streams`, `get_channels` for an allowlisted channel, and reports each result's `as_of`.
- **A3** Live write smoke (`tools/try_live.py`, opt-in, never in `make check`): create and end a poll on the
  operator's **own** channel, and confirm `send_chat_message` to a **non-allowlisted** channel produced no request.
- **A4** A test-run transcript shows a write returning `confirmation_required` with zero HTTP calls, then the same
  call with the token performing exactly one write.
- **A5** `grep -rn httpx src/ | grep -v twitch_client.py` is empty.
- **A6** A grep of a full test-run log finds no token-shaped string.
- **A7** `docker compose up` on WSL2 binds `127.0.0.1:8770` only; `curl` from the LAN host fails, from localhost it
  answers `/healthz` unauthenticated and everything else 401.
- **A8** `docs/reports/initial-build/implementation-report.md` carries a requirement→implementation→test table with
  a row for every R1–R57 and no deviations.

## 9. Out of scope

EventSub (webhooks and WebSocket), PubSub, IRC chat, the Extensions API, analytics/insights, ad-break scheduling,
stream-marker *writes*, clip creation, whispers, multi-account, per-MCP-session state, outbound TLS termination,
Twitch app registration automation, auto-generated OpenAPI clients, database persistence, and any write tool not in
§6.4.

## 10. Open questions

*None. All Phase 1 ambiguities are resolved in §4 (D1–D4 by the human, D5–D10 by house style, all open to objection
at the approval gate).*
