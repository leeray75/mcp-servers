# SPEC — Scope-aware 401 and per-token-class Helix circuits (Amendment 7)

**Title:** A missing scope is a permission answer, not an auth failure; the 401 circuit stops Helix calls of the affected token class only
**Status:** Approved (approved by: human, 2026-10-31). Gate confirmations: C-D3, C-D5, C-D9 and C-D10 confirmed; in-flight old-token 401 race accepted as a known limitation.
**Date:** 2026-10-31
**Amends:** [`docs/SPEC.md`](../../SPEC.md) R24 (the circuit stops Helix API calls of the affected token class; token, validate
and revoke endpoints are never gated), R44 (one bounded exception: Twitch's `message` field is read only to recognise a missing
scope), R45 (the missing-scope check runs before any circuit decision and also reads `message`), §6.3 (the write-path `401` branch
keeps its kind). It also amends the CLAUDE.md invariant "The `401` circuit (R24)" and the CLAUDE.md bullets for
`twitch_client.py` (line 68), `ratelimit.py`, `service.py` (line 62) and `errors.py` (line 92) that describe the circuit or the
message rule. R50 is unchanged. D10 (no scope table in code) is **unchanged**. This spec adds requirements `C1`–`C10`. It renumbers nothing.

---

## 1. Problem / motivation

**Live incident (2026-10-31).** A signed-in user token holding only `user:read:email` called `get_banned_users`. Helix
answered `401` for a missing scope. The server opened the process-wide `401` circuit (`twitch 401 circuit open`,
`outcome=auth_circuit`). Every later call, including app-token reads and `twitch_account_status`, then failed at once with
"Twitch rejected this server's token" until a restart.

Three defects cause this:

1. **The circuit opens before the scope check.** `src/twitch_mcp/twitch_client.py` `_map` calls
   `self.limiter.trip_auth(...)` at line 760 and only then `_scope_phrase(body)` at line 762.
2. **The scope check never reads `message`.** `_SCOPE_BEARING_KEYS` (line 79) is `("error_description", "error")`. Twitch's
   missing-scope text is in `message` (incident body: `{"error": "Unauthorized", "status": 401, "message": "Missing scope: <scope>"}`),
   so `kind=permission` (R45) never fires on real Twitch. The tests passed because their fakes put the text in `error`.
3. **One circuit for two credentials.** A user-token `401` blocks app-token calls. The sign-in validate path makes it worse:
   `server.py:1853` (`_validate_pair`) calls `client.fetch_validate(bearer)`, which bills a user token to the app bucket
   (`twitch_client.py:1091`, `:1107`).

**What Twitch's docs do and do not say** (checked 2026-10-31): the Helix API reference describes a missing scope only in
prose under a `401` row. It does not document a JSON error body, the fields `error`/`status`/`message`, or an exact message
string. The scopes page does not document the error a request receives when a scope is missing. The `message` recognition in
C1 therefore rests on the observed incident body, not on a documented schema, and is kept narrow.

## 2. Goals

- A `401` or `403` whose body names a missing scope returns `kind=permission` with Twitch's own scope wording, and opens no circuit.
- The circuit stops Helix API calls of the affected token class only, and is kept per class: a non-scope `401` on a user Helix
  call opens the user circuit; on an app Helix call, the app circuit.
- Token exchange, refresh, revoke, the client-credentials mint and `validate` are never gated by a circuit (C-D9).
- A new user token (sign-in or refresh) clears the user circuit. The app circuit clears only on restart, as today.
- README and `.env.example` document each read tool's required scope, with a copy-paste `TWITCH_OAUTH_SCOPES` line (D10 holds).

## 3. Non-goals

- A boot-time scope whitelist or warning (contradicts D10; rejected).
- Enriching `twitch_account_status` with a registered-tools-versus-scopes check (possible later spec).
- Scopes for write tools (the README write-group lines are unchanged).
- A hint length cap (rejected; only control characters and newlines are removed, C2).
- Any `twitch_account_status` change: a rejected user token still makes it raise `unauthenticated`, as today.
- Changing the mint, the boot probe (R50), R16, the token-file format (R48), or the tool set (R35–R37).
- Committing, pushing or releasing.

## 4. Decisions

### 4.1 Human decisions (2026-10-31, and the fix rounds)

| Ref | Decision | Consequence |
|---|---|---|
| **C-D1(a)** | Scope-aware `401`/`403`: a missing-scope body returns `kind=permission`, `retryable=false`, with Twitch's scope wording in `hint`, and opens no circuit. `message` is read only to recognise the phrase. No scope table (D10). | C1, C2 |
| **C-D1(b)** | Per-token-class circuit (app and user), stopping Helix calls of that class. | C3, C4, C5 |
| **C-D2** | README and `.env.example` document per-tool scopes and a copy-paste `TWITCH_OAUTH_SCOPES` line. Docs only. | C10 |
| **C-D4 (replaced)** | A **missing-scope `401` on a write returns `kind=permission`**, the current behaviour (`twitch_client.py:762–764`), matching a scope `403` on a write. Writes still never retry (R16). A **non-scope** `401` on a write stays `kind=unknown_outcome`. | C6 |

### 4.2 Derived decisions (confirm at the gate)

| Ref | Decision | Rationale and trade-off |
|---|---|---|
| **C-D3** *(brief-recommended)* | The user circuit clears when a new user token is stored: through the existing `oauth.on_invalidate` listener, which `install()` runs (`oauth.py:589`). The same listener runs on `forget()` (`:604`), which is harmless. The app circuit clears only by restart. | One existing hook covers sign-in and refresh with no change to `oauth.py`. Known limitation, see §7. |
| **C-D5** | `/healthz` is `degraded` only when the **app** circuit is open or the boot probe was unreachable. A user-circuit state does not degrade it. | The app token drives server health. A dead user token is reported by `twitch_account_status` as today. |
| **C-D9** | **R24's circuit stops Helix API calls of the affected token class.** Token exchange, refresh, revoke, the client-credentials mint and `validate` (`post_form`, `fetch_validate`, boot `validate_token`) are **never gated** by any circuit; they still take their token class's bucket (R20). R50 is unchanged. The mint therefore works under an open app circuit. | Removes the sign-in deadlock (a user validate refused by the open user circuit blocked the only path that clears it) and the refresh force-reauth under an open app circuit, which deleted the user's token file. It also removes the earlier C-D7 (an open app circuit blocking the mint). **Flag prominently at the gate.** |
| **C-D10** | `_permission_error` adds the "This token holds" sentence only when the body carries a `scopes` list. It never says "holds no scopes" otherwise. | Real Twitch bodies carry no `scopes` list, so the old sentence was false (the token held `user:read:email`). |

## 5. Requirements

Scope recognition (R44 and R45 amended):

- **C1** — `_SCOPE_BEARING_KEYS` gains `"message"`, read in the order `error_description`, `error`, `message`. A field is a
  missing-scope phrase only when its lowercased text contains `scope` and one of `_SCOPE_PHRASES` (`missing`, `requires`,
  `does not have`, `missing scope`). `message` is read only by `_scope_phrase`, only on a `401` or `403`. It never sets `kind`
  otherwise.
- **C2** — A `401` or `403` with a missing-scope phrase returns `kind=permission`, `retryable=false`, with `http_status` and
  `code` set. Its `hint` contains the scope sentence with every control character and newline replaced by one space, plus the
  scopes held when the body carries a `scopes` list (C-D10). It opens no circuit and is never retried.

Circuits (R24 amended):

- **C3** — The circuit stops Helix API calls only (C-D9) and is kept per token class (`APP_CLASS`, `USER_CLASS`). A class's first
  non-scope Helix `401` opens that class's circuit; later Helix calls of that class return `kind=unauthenticated` without a
  request. The class hint applies **only to the read `unauthenticated` raise**: the first-call read `401` in `_map` and the
  later-call refusal in `acquire`. Its text is class-correct: user class, "Sign in again with the connect_twitch_account tool. A
  successful sign-in clears this block."; app class, "Restart the container; the app-token circuit never resets itself." A write
  never receives the class hint (C6). Ungated paths (C9) never carry a circuit hint. The circuit listener
  (`set_auth_failure_listener`, R30 trigger 3) fires once per class on that class's first open, and a raising listener does not
  stop the trip. The listener clears identity verdicts and confirm tokens for either class (R30/R33).
- **C4** — A non-scope `401` on a user-token Helix call opens only the user circuit. App-token Helix calls keep working. A
  user circuit opens only when a user token exists.
- **C5** — A non-scope `401` on an app-token Helix call opens only the app circuit. User-token Helix calls keep working.
  `twitch_account_status` is unchanged: its validate calls are never gated (C9), so it reports the user block when validate
  answers `200`, and a rejected user token still makes it raise `unauthenticated`, as today.
- **C6** — A missing-scope `401` on a write returns `kind=permission` and opens no circuit (C-D4). A non-scope `401` on a write
  returns `kind=unknown_outcome` (R16), keeps `_unknown_outcome`'s existing hint ("Do not re-issue this call: it may already have
  taken effect. Check the channel ..."), and opens that write's token-class circuit. Writes are never re-issued.
- **C7** — `RateLimiter.reset_user_circuit()` clears only the user circuit. It is wired as a listener on the existing
  `UserAuth` invalidation hook at `server.py:~1744` (`oauth.on_invalidate(lambda: service.limiter.reset_user_circuit())`, beside
  `on_identity_change`), so it runs when `install()` stores a token (sign-in and refresh) and on `forget()` (C-D3). It never
  clears the app circuit. No change to `oauth.py` is needed.
- **C8** — `/healthz` status is `ok` only when the boot probe reached Twitch and the app circuit is closed (C-D5). The response
  keys stay exactly those of R3. The wiring's `auth_failed` property returns `self.service.limiter.auth_failed(APP_CLASS)`.
- **C9** — Token exchange, refresh, revoke, the mint and `validate` are never gated by a circuit (C-D9), and an open app circuit
  never triggers `_force_reauth`. Their `401` is not a circuit trip: a validate `401` raises `unauthenticated` with no circuit hint.

Docs:

- **C10** — The README per-group section and `.env.example` list the scopes below and give the copy-paste line (C-D2). The code
  holds no scope table (D10). Verified against <https://dev.twitch.tv/docs/authentication/scopes/> on 2026-10-31:

| Read tool | Scope(s) Twitch's scopes page lists for the endpoint |
|---|---|
| `get_stream_markers` | `user:read:broadcast` |
| `get_broadcaster_subscribers` | `channel:read:subscriptions` |
| `get_channel_editors` | `channel:read:editors` |
| `get_moderators` | `moderation:read` (or `channel:manage:moderators`) |
| `get_banned_users` | `moderation:read` (or `moderator:manage:banned_users`) |
| `get_automod_settings` | `moderator:read:automod_settings` |
| `get_chat_chatters` | `moderator:read:chatters` |
| `get_channel_chat_settings` (optional fields) | `moderator:read:chat_settings` |

Copy-paste line (read-tool scopes plus `user:read:email`):

```
TWITCH_OAUTH_SCOPES=user:read:email user:read:broadcast channel:read:subscriptions channel:read:editors moderation:read moderator:read:automod_settings moderator:read:chatters moderator:read:chat_settings
```

The write-group scopes stay in the README as they are.

## 6. Design

### 6.1 Files and symbols (verified 2026-10-31)

**`src/twitch_mcp/ratelimit.py`**

- Lines 228–230: `_auth_failed` and `_auth_message` become per-class dicts keyed by `TokenType`.
- Lines 239–241 `auth_failed` property becomes the method `auth_failed(self, token_type: TokenType) -> bool`.
- Lines 243–253 `set_auth_failure_listener`: docstring says the listener fires once per class.
- Lines 255–270 `trip_auth(self, token_type: TokenType, *, message: str | None = None)`: per-class latch; the log line keeps
  `outcome="auth_circuit"` and adds the class label. No credential material.
- New `reset_user_circuit(self) -> None` (C7): clears `USER_CLASS` only, logs one line, does not call the listener.
- Lines 352–383 `acquire`: gains keyword `circuit: bool = True`. The check at lines 365–370 runs only when `circuit` is true and
  refuses with the class hint (C3). Callers that pass `circuit=False` still take their bucket.

**`src/twitch_mcp/twitch_client.py`**

- Line 23 (module docstring) and lines 77–82 (the `_SCOPE_BEARING_KEYS` comment and the `_SCOPE_PHRASES` comment, which says
  "`message` is never consulted"): update.
- Line 79 `_SCOPE_BEARING_KEYS`: add `"message"` (C1).
- Lines 292–299 `_error_code` docstring ("Nothing is ever read from `message`"): update.
- Lines 302–318 `_scope_phrase`: docstring updated; the returned text has control characters and newlines replaced (C2).
- Lines 384–386 `auth_failed` property: removed. Callers use `self.limiter.auth_failed(...)`.
- Lines 716–726 `_map`: gains keyword `trip_circuit: bool = True`.
- Lines 757–779 (the `401` branch): compute `_scope_phrase` first. A scope hit raises `_permission_error` for reads and writes alike
  (C2, C6), with no trip. Otherwise, if `trip_circuit`, call `self.limiter.trip_auth(kind, ...)`. For a **read**, the raised
  `unauthenticated` carries the class hint from C3 when `trip_circuit` is true, and no hint when it is false (validate). The
  current "restart the container" text at lines 775–778 is the read hint and is replaced by the class hint. For a **write**, the
  `unknown_outcome` raised by `_unknown_outcome` (lines 700–714) keeps its existing R16 hint unchanged.
- Lines 852–875 `_permission_error`: the "This token holds" sentence only when the body has a `scopes` list (C-D10).
- Lines 967 (`post_form`), 1046 (`validate_token`) and 1091 (`fetch_validate`) acquire with `circuit=False` (C9).
- Line 1085 `fetch_validate(bearer, *, client_id=None, token_type=APP_CLASS)`: `token_type` selects the bucket only. Its `_map`
  call at line 1107 passes `trip_circuit=False`.

**`src/twitch_mcp/service.py`**

- Lines 384–386 `auth_failed` property: removed (C8 reads the limiter directly).
- `twitch_account_status` (lines 513–545): unchanged.

**`src/twitch_mcp/server.py`**

- Lines 1509–1511 `auth_failed` on the wiring: returns `self.service.limiter.auth_failed(APP_CLASS)` (C8).
- Line 1744 beside `oauth.on_invalidate(service.on_identity_change)`: add `oauth.on_invalidate(lambda: service.limiter.reset_user_circuit())` (C7).
- Line 1853 `_validate_pair`: `client.fetch_validate(bearer, token_type=USER_CLASS)` so the user bucket is used (the circuit ignores
  validate either way, C9).

**`src/twitch_mcp/oauth.py`, `models.py`, `errors.py`, `capabilities.py`**: no change. `oauth.py` already runs `_invalidate` on
`install()` (`:589`) and `forget()` (`:604`).

**`src/twitch_mcp/errors.py` line 9**: its "never from message text" wording names the C1 exception.

### 6.2 Circuit scope and state (C3–C9)

```
Helix call, non-scope 401, class = user  -> user circuit OPEN   (user Helix calls refused; cleared by the invalidate listener on install/forget)
Helix call, non-scope 401, class = app   -> app  circuit OPEN   (app Helix calls refused; restart only)
Helix call, scope 401/403 (read or write)-> no state change, kind=permission
token exchange, refresh, revoke, mint, validate -> never checked, never tripped
```

### 6.3 Documentation edits (applied with the code)

- `README.md`: §4 "Per-group OAuth scopes" (lines 232–257) gets the C10 table and the copy-paste line, and says which line is for
  read tools and which for write groups. Lines 161 and 315 ("first `401`", R30): "first non-scope `401` of a token class". Lines
  368–369 (the auth-circuit paragraph): Helix-only, per class; the user circuit clears on sign-in or refresh; the app circuit clears
  on restart. Line 392 (troubleshooting row): add the user-class reset.
- `.env.example` lines 74–80: the commented `TWITCH_OAUTH_SCOPES=` example gets the C10 line.
- `CLAUDE.md`: the "The `401` circuit (R24)" invariant (lines 131–132) and the bullets for `twitch_client.py` (line 68),
  `service.py` (line 62; the first-`401` wording at line 79 is `identity.py`), and `errors.py` (line 92): updated to match this spec.
- `docs/SPEC.md`: the pointer line (done) and superseded markers at R24 (~line 185), R44 (~248), R45 (~249), and §6.3 (~347–358).
  The R24 marker reads: "R24's circuit stops Helix API calls of the affected token class; token, validate and revoke endpoints are
  exempt, and the mint works under an open app circuit. R50 unchanged." R44 names the C1 exception.
- `CHANGELOG.md` `[Unreleased]` (line 5): a `### Fixed` entry.

## 7. Security and invariants

- **R44 exception, bounded.** `message` is read only by `_scope_phrase`, only on `401`/`403`, and only to recognise a missing-scope
  phrase. It never sets `kind` otherwise. Its text reaches the agent only inside `hint`, with control characters and newlines
  removed (C2). It is never logged (R52).
- **Logs.** Circuit and reset lines carry the token-class label only. No scope text, `message`, token or body is logged.
- **Credential separation (R20).** A user-class failure never blocks app-token calls; an app-class failure never blocks user-token
  calls. Each class keeps its own bucket and circuit.
- **Writes (R16).** No write is retried. A missing-scope write `401` is `permission`; a non-scope write `401` is `unknown_outcome`
  with its R16 hint (C6).
- **Ungated endpoints.** Token exchange, refresh, revoke, the mint and validate are never gated (C9). This narrows R24 to Helix
  (C-D9) and is recorded for the gate. R50 is unchanged.
- **Reset after persistence (R48).** `install()` stores the token before `_invalidate` runs the reset.
- **Known limitation (accepted, no mechanism).** A user Helix call already in flight with the old token can return `401` after a
  refresh has installed a new token and reset the user circuit. That `401` re-opens the user circuit while the new token is valid.
  It clears on the next sign-in or refresh, or on restart. The rarity of this race is accepted; no extra guard is added.
- **Network confinement (R11–R13).** No new endpoint, host, method or header. `fetch_validate` gains only a bucket selector.
- **Tool set (R35–R37).** Unchanged.
- **Empty means empty (R45).** A missing scope still returns `permission`, never an empty list.

## 8. Test plan and acceptance criteria

`make check` green is the gate: ruff, `mypy --strict`, pytest, branch coverage at least 90%, the 30-second per-test timeout kept.
A single test runs with `pytest <path>::<name> --no-cov`.

**Failing-first tests (must fail against the unfixed code; record the failure in the implementation report).** Assertions are
ordered so the `kind` check fails first on the unfixed code (old API: `client.auth_failed` property, unfixed kind).

- **N1** `tests/test_twitch_client.py` (new): `GET /helix/users` answers `401` with
  `{"error": "Unauthorized", "status": 401, "message": "Missing scope: user:read:email"}`. Assert in this order:
  `kind == "permission"`, `retryable is False`, `"user:read:email" in hint`, `"holds no scopes" not in hint`, then
  `client.limiter.auth_failed(APP_CLASS) is False` and `(USER_CLASS) is False`, then a second call makes a request.
- **N2** `tests/test_twitch_client.py` (new): N1 with `403`.
- **N3** `tests/test_service.py` (new): a user-token `get_banned_users` (`broadcaster_id` read case, line 95) with a
  `GET /helix/moderation/banned` fake registered in the test, answering `401` with a `message`-only scope body. The write-table
  rows at lines 169–172 do not register this route. Same assertion order as N1.
- **N7** Sign-in while the user circuit is open (C9, C7). A real `TwitchClient` over `FakeTwitch` (`make_client`,
  `tests/core_fakes.py:84`). Steps:
  1. Trip the user circuit with a real non-scope user Helix `401`.
  2. Build `UserAuth(TokenStore(tmp_path / "t.json"), clock=fclock.clock)`; `set_credentials`.
  3. `attach_grant(lambda form: client.post_form(OAUTH_TOKEN_PATH, form))`; `attach_validate(lambda b: _validate_pair(client, b))`
     (`server.py:1849`); `on_invalidate(lambda: client.limiter.reset_user_circuit())`. The lambda makes a missing method fail on
     behaviour rather than on attribute lookup, and `_invalidate` suppresses exceptions at install time.
  4. The faked token response is Twitch's documented shape, with `scope` as an array and **no `user_id`**, so `exchange` runs
     `_resolve_user` and a validate call. The faked `/oauth2/validate` answers `200` with `user_id` and `login`.
  5. Run `exchange`. Assert, in this order: the token file exists; then `client.limiter.auth_failed(USER_CLASS) is False`.
  On the unfixed code the single process-wide circuit also refuses `post_form` (an app-class acquire), so `exchange` fails with
  `unauthenticated` before the token file is written. That is the first, behavioural failure. The test lives in
  `tests/test_oauth_units.py`.
- **N8** Refresh under an open circuit, failing-first (C9, no force-reauth). The same real wiring. Trip the **app** circuit with a
  real app Helix `401`. Store a token, call `refresh_now`; the grant answers `200` with a refresh body. Assert the new token is
  installed and the token file still exists. A second variant trips the **user** circuit and asserts the same. On the unfixed code
  the grant is refused under the open circuit, `_force_reauth` runs, and the file is deleted (`tests/test_oauth_units.py`).

**Behaviour tests (new unless marked).**

- **N4** Per-class isolation, client level (`tests/test_twitch_client.py`): a non-scope `401` (`"message": "Invalid OAuth token"`)
  on a user Helix read opens only the user circuit, and the next app Helix read still makes its request. The mirror holds: a
  non-scope `401` on an app Helix read opens only the app circuit, and the next user Helix read still makes its request. This
  covers the user-circuit-leaves-app-reads and app-circuit-leaves-user-reads cases in one test.
- **N6** `tests/test_service.py`: with the app circuit open, an app Helix read is refused with no request. The user read path is
  covered by N4.
- **N9** App circuit is not cleared by `install` (C7): after `install`, `limiter.auth_failed(APP_CLASS)` stays `True`
  (`tests/test_oauth_units.py`).
- **N10** Per-class circuits and listener (C3): trip each class; each class refuses only its own Helix calls; the listener fires
  once per class, and a second trip of the same class does not fire it again (`tests/test_ratelimit.py`).
- **N11** Hint sanitising (C2): a scope `message` containing a newline and a control character yields a hint with neither
  (`tests/test_twitch_client.py`).
- **N12** `/healthz` (C8): `ok` while only the user circuit is open; `degraded` while the app circuit is open
  (`tests/test_server.py`, beside `test_healthz_is_degraded_when_the_boot_probe_cannot_reach_twitch`).
- **N13** Write path (C6): a non-scope write `401` gives `unknown_outcome`, opens the user circuit, and sends one request. Its hint
  contains "Do not re-issue" and does not contain the class sign-in or restart text. A scope `401` on a write gives `permission`,
  opens no circuit, and sends one request. Existing test `tests/test_identity.py::test_identity_is_invalidated_on_the_first_401`
  already posts a non-scope body (`{"error": "Unauthorized"}`, line 536); only its circuit assertion at line 546 changes.
- **N14** Guard (not failing-first; passes on the unfixed code): a `500` whose `message` says "Missing scope" stays `upstream`. A `401`
  with `message` "Missing token" (no `scope`) stays `unauthenticated`. A `403` with `message` "missing" and no `scope` stays
  `upstream` (`tests/test_twitch_client.py`, extending `test_kinds_map_from_status_not_message_text`).
- **N15** Client level, both circuits open (C9), `tests/test_twitch_client.py`: trip both classes with real Helix `401`s. Then
  `fetch_validate(bearer, token_type=USER_CLASS)` and `post_form(OAUTH_TOKEN_PATH, form, client_id=...)` each still send a request
  (the fake's request count rises). In the same test, on a fresh client with closed circuits, a validate `401` raises
  `unauthenticated`, and both circuits stay closed. The second half is failing-first: on the unfixed code the validate `401`
  trips the circuit.

**Existing tests that change (each named, with the reason).**

- `tests/test_twitch_client.py::test_first_401_trips_the_process_wide_circuit` (lines 101–116): asserts `client.auth_failed` (property,
  removed). Becomes `client.limiter.auth_failed(APP_CLASS)` and a per-class check.
- `tests/test_twitch_client.py::test_missing_scope_becomes_permission_carrying_twitchs_own_string` (lines 119–142): stays; gains the
  no-circuit assertion.
- `tests/test_twitch_client.py::test_kinds_map_from_status_not_message_text` (line 145): stays; extended by N14.
- `tests/test_ratelimit.py::test_401_circuit_stops_all_upstream_traffic` (lines 430–442): loops over both classes after one trip;
  becomes per-class (a trip refuses its own class only).
- `tests/test_ratelimit.py::test_circuit_survives_every_attempt_and_logs_once` (lines 445–456) and `limiter.auth_failed is True` at
  lines 432–434, 456 and 493: move to per-class calls; the log count is per class.
- `tests/test_ratelimit.py::test_circuit_opens_before_any_wait_and_never_sleeps` (line 459): per-class call.
- `tests/test_ratelimit.py::test_the_auth_listener_fires_exactly_once` (line 470): becomes "fires once per class" (N10).
- `tests/test_ratelimit.py::test_a_raising_listener_never_masks_the_401` and `test_the_listener_cannot_recurse_through_the_circuit`
  (lines 485–511): keep their intent; calls take a class.
- `tests/test_identity.py::test_identity_is_invalidated_on_the_first_401` (lines 518–560): only the circuit assertion at line 546
  changes, to `service.limiter.auth_failed(USER_CLASS)`. The identity and confirmation clearing assertions stay.
- `tests/test_service.py::test_r45_missing_scope_surfaces_as_permission` (lines 957–973): stays; gains the no-circuit check (N3).
- `tests/test_server.py` healthz tests (lines 494–504): unchanged; N12 is added beside them.
- `tests/test_oauth_units.py` (line 293, `on_invalidate`): unchanged; N7–N9 are added beside it.

**Acceptance criteria.**

1. N1–N3, N7 and N8 fail against the unfixed code (N1–N3 on the `kind` assertion; N7 and N8 on the sign-in and refresh outcomes)
   and pass after the change.
2. Every existing R24/R45 test listed above is updated and passes; none is weakened.
3. `make check` passes with branch coverage at least 90%, and the 30-second per-test timeout is kept.
4. The incident sequence (scope `401` on `get_banned_users`, then an app read) gives the expected results (N1/N3 with N4).

## 9. Out of scope

- A boot-time scope whitelist or warning (rejected; contradicts D10).
- Enriching `twitch_account_status` with a registered-tools-versus-scopes check (possible later spec).
- Any `twitch_account_status` change, including a degraded block for a rejected user token.
- Scopes for write tools.
- Changing the mint, the boot probe (R50), R16, the token-file format (R48), or the tool set (R35–R37).
- A hint length cap.
- Any guard for the in-flight old-token `401` race (§7 records it as accepted).
- Any logging change beyond the token-class label on circuit lines.

## 10. Open questions

None. Every choice is recorded in §4. C-D3, C-D5, C-D9 and C-D10 are derived and listed for confirmation at the gate, with the
human-decided items C-D1(a), C-D1(b), C-D2 and C-D4 (replaced).
