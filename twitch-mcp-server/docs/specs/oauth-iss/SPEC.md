# SPEC — OAuth `iss` is optional when Twitch omits it (bug fix)

**Title:** Optional `iss` on the OAuth callback (RFC 9207 semantics)
**Status:** Approved (approved by: human, 2026-10-31). Gate confirmation: I-D2 confirmed.
**Date:** 2026-10-30
**Amends:** [`docs/SPEC.md`](../../SPEC.md) R47 (the `iss` clause only). R46 and R48–R51 are unchanged.
This spec adds requirements `I1`–`I6`. It does not renumber any requirement and does not rewrite R47 text in place;
the amended statement is identified by the pointer line in `docs/SPEC.md` (§12).

---

## 1. Problem / motivation

Observed live on 2026-10-30: a real browser sign-in against Twitch ended on the generic sign-in-failed page. The
callback was logged as `400`, outcome `unauthenticated`, and no token exchange was attempted.

Twitch's documented authorization-code redirect carries only `code`, `scope` and `state`
(<https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/>). It has no `iss` parameter. `PendingStore.claim`
(`src/twitch_mcp/oauth.py`) refuses any callback without `iss` (`REFUSED_ISS`), so sign-in cannot succeed against real
Twitch. The unit and HTTP tests passed only because the test drivers always pass `iss`, copied from the pending flow.
No HTTP test of a successful callback exists today.

## 2. Goals

- Let a callback without `iss` complete sign-in, as Twitch's real redirect does.
- Keep `iss` enforced whenever a callback presents it, with an exact, constant-time comparison.
- Keep `state`, the 10-minute TTL, single-use pending flow and the code requirement exactly as they are.
- Make the test drivers mirror real Twitch by default, and cover each `iss` outcome explicitly, at unit and HTTP level.

## 3. Non-goals

- Changing the authorize URL, scopes, token exchange, token store, refresh, or disconnect (R46, R48–R51).
- Requiring `iss` again, or adding a second authorization server.
- Changing what the callback echoes (nothing) or what it logs beyond the existing outcome line.
- Editing source, tests, `CLAUDE.md` or `CHANGELOG.md` in this spec phase (the implementation phase owns them).

## 4. Decisions

| Ref | Decision | Rationale and trade-off |
|---|---|---|
| **I-D1** | **`iss` is optional, per RFC 9207 semantics.** When the callback carries `iss`, it must equal the expected issuer exactly (constant-time compare) or the callback is refused as today (`REFUSED_ISS`). When `iss` is absent, the check is skipped. Recorded from the human's decision of 2026-10-30. | Twitch does not send `iss`, so a mandatory check makes sign-in impossible. Trade-off: a callback without `iss` relies on `state` alone for binding (see §7). |
| **I-D2** *(derived; the human confirms at the approval gate)* | **The unit-test driver omits `iss` by default** through one helper (§6.3), mirroring real Twitch. The HTTP tests (§6.4) omit `iss` by nature. Explicit tests cover (a) absent `iss` → success, (b) correct `iss` → success, (c) wrong `iss` → refused, (d) empty-string `iss` → refused. | Default-correct fakes are how the bug went unseen. An empty value is "present but wrong", not "absent". |

## 5. Requirements

- **I1** — `PendingStore.claim(state, iss)` refuses with `REFUSED_ISS` when `iss` is present (not `None`) and is not
  exactly equal to `flow.iss`. The comparison uses `_same` (constant-time). A wrong `iss` is refused and the flow is
  dropped (a second claim is `REFUSED_NO_FLOW`).
- **I2** — When `iss` is `None` (absent from the callback query), the issuer check is skipped and the flow is claimed,
  provided the other checks pass. The browser callback then completes the code exchange and returns the success page.
- **I3** — An empty-string `iss` (`?iss=`) is treated as present. It is refused with `REFUSED_ISS`, never skipped. A
  unit case is required; an HTTP case with `?iss=` is optional.
- **I4** — The `state` checks are unchanged: a missing or wrong `state` is refused with `REFUSED_STATE`; `state` is
  compared with `_same`; a claimed flow is single-use (any second claim is `REFUSED_NO_FLOW`).
- **I5** — The 10-minute TTL is unchanged: a claim at or after `expires_at` is refused with `REFUSED_EXPIRED`, and an
  expired flow is dropped.
- **I6** — The callback still requires a `code` (R47 and the `complete` path unchanged), still echoes nothing, and still
  logs only the existing `GET /oauth/callback` outcome line. The `iss` value is never logged or echoed.

## 6. Design

### 6.1 Source (`src/twitch_mcp/oauth.py`)

- `PendingStore.claim(self, state: str | None, iss: str | None) -> PendingFlow | str` (class at line ~137): the check
  `if iss is None or not _same(iss, flow.iss):` (line ~183) becomes `if iss is not None and not _same(iss, flow.iss):`.
  Ordering stays: no-flow, expiry, `state`, then `iss`, so a wrong or missing `state` is refused before `iss` is read.
- Comment-only wording edits, from "must carry" or "mandatory" to "checked when present". The `iss` value and the
  function signatures are unchanged:
  - module docstring bullet at line ~11 ("an ``iss`` check");
  - `expected_iss` docstring at line ~94 ("The exact ``iss`` the callback must carry");
  - `PendingFlow` class docstring at line ~128 (names ``iss``);
  - `PendingStore` class docstring at line ~138 ("``iss`` check");
  - `PendingStore.claim` docstring at line ~168, which does not name `iss` today: add "``iss`` is checked when present";
  - `OAuthFlow` property docstring at line ~833 ("The exact ``iss`` this deployment's callback must carry").
- `expected_iss`, `REFUSED_ISS`, `PendingFlow.iss` and the `iss` parameter types are kept.
- `OAuthFlow.complete(code, state, iss)` signature is unchanged; `iss` may already be `None`.
- `src/twitch_mcp/security.py` line 9 contains the exact text "the ``iss`` check and the 10-minute flow TTL". It gets the
  same comment-only wording change: "the ``iss`` check when present".

### 6.2 Source (`src/twitch_mcp/server.py`)

- `oauth_callback` already passes `iss=params.get("iss")` (line ~1783), which is `None` when absent and `""` when
  empty. No behaviour change. This matches I2 and I3.

### 6.3 Tests (unit: `tests/test_oauth_units.py`)

- **One helper** owns the browser callback. The implementer adds
  `async def _callback(flow, pending, *, state: object = _PENDING_STATE, iss: str | None = None, code: str = "code-1")`,
  where `_PENDING_STATE` is a module-level sentinel object. When `state` is left at the sentinel, the helper passes
  `pending.state`. Passing `state=None` sends a missing `state`, so that case stays expressible. The helper calls
  `flow.complete(code=code, state=..., iss=iss)`.
- The `iss` default is `None` (absent), so call sites stop passing `iss=pending.iss`. The one place that passes `iss`
  explicitly is the correct-`iss` case (b). There are **10** `flow.complete(` call sites today, at
  `test_oauth_units.py` lines 321, 343, 362, 382, 407, 432, 455, 475, 479 and 506. Each moves to the helper. The
  `state="wrong"` case at line 475 keeps its `state` argument through the helper.
- New or switched unit tests, each naming its requirement:
  - (a) I2: **the existing happy-path test at `test_oauth_units.py:483–511`** (`test_flow_complete_happy_path_installs_token`)
    is switched to the helper with no `iss`. Its assertions are unchanged, and it is cited with I2 in a comment. Do not
    add a duplicate. This test must fail against the unfixed `oauth.py:183` (see below).
  - (b) I1: `store.claim(state, expected_iss(AUTH_BASE))` returns the flow.
  - (c) I1: `store.claim(state, "https://evil.example")` returns `REFUSED_ISS`; a second claim is `REFUSED_NO_FLOW`.
  - (d) I3: `store.claim(state, "")` returns `REFUSED_ISS`.
- Existing `claim`/`start` tests at lines ~78–115 keep passing (they pass `flow.iss` or a wrong `iss` deliberately).
- **Must fail against the unfixed code (m4):** the (a) test above and the HTTP success test (§6.4, test 1) must fail
  against the current `oauth.py:183` (`iss is None or ...`). The implementation report records one targeted run (or a
  reading of the line) showing this failure before the fix.

### 6.4 Tests (HTTP: `tests/test_server.py`) — two NEW tests

No HTTP test of a successful callback exists today. Add these two next to the R47 test at line ~614. They use
`running_app(fake, fclock, settings)` with the **default `connected=False`**, which registers no token or validate
route, and **do not change `wired()` or `running_app()`**. Each test registers its own handlers before the first
callback. The reason is `FakeTwitch.handle` (`tests/twitch_fakes.py:358–367`): it serves the **first** registered
override that matches, so a handler added after a `connected=True` mint would never run.

Both tests build their settings with a redirected token file, so a successful callback can write only under `tmp_path`
and never reaches the default `/data` path:
`settings = server_settings(twitch_user_oauth=True, twitch_oauth_token_file=str(tmp_path / "twitch_user_token.json"))`.
The path must be absolute, as `config.py:214` requires; the tests run under WSL/Linux, where `tmp_path` is absolute.
(Without this, `TokenStore.save` at `oauth.py:388–411` raises `upstream` and the success callback returns 400.)

1. **`test_oauth_callback_completes_without_iss`** (I2): register one `POST /oauth2/token` handler that reads the form
   body and branches on `grant_type`:
   - `client_credentials` (the boot app-token mint) returns the app-token body;
   - `authorization_code` returns a user-token body with `access_token`, `refresh_token`, `token_type`, `scope`,
     `expires_in`, and **`user_id` and `login`**. The `user_id` field is required: `exchange` at `oauth.py:634` skips
     `_resolve_user`, and so skips any validate call, only when the body carries `user_id`. The same shape as the
     `grant` callable at `tests/test_oauth_units.py:487–503`.

   No `GET /oauth2/validate` handler is registered. Then: `GET /oauth/start`, read `state` from the 302 `Location`, and
   `GET /oauth/callback` with `code` and `state` only. Assert all of:
   - 200, and the signed-in page;
   - **exactly one `authorization_code` POST**, counted by inspecting the form body of each recorded
     `POST /oauth2/token` request (`fake.requests_for("POST", "/oauth2/token")`). Do not count all token POSTs: the boot
     mint is also a `POST /oauth2/token`, with `grant_type=client_credentials`;
   - the file at `tmp_path / "twitch_user_token.json"` exists with mode `0600`;
   - (sanity, m-F) the `authorization_code` POST's form carries **no `iss`** key;
   - (sanity, m-F) the success body contains neither the `code` value nor the `state` value.
2. **`test_oauth_callback_refuses_wrong_iss`** (I1): the same handler registration and the same `tmp_path` settings, then
   start, then `GET /oauth/callback` with `code`, the live `state` and `iss=https://evil.example`. Assert 400, the
   generic failed page (no `state`, `code` or `iss` echoed), **zero** new `authorization_code` POSTs (counted as in
   test 1), no token file written under `tmp_path`, and that the flow is dropped: a second callback with the correct
   `state` and no `iss` returns 400 with no new `authorization_code` POST.

The two tests use their own fake registrations only, so no other test's fake behaviour changes. Test-file changes are
limited to `tests/test_server.py` and `tests/test_oauth_units.py`.

- `tests/test_security.py`: the callback route stays in the bearer-exempt list; no change expected.

### 6.5 Docs

- `CLAUDE.md` OAuth (R46–R51) invariants bullet, phrase "an `iss` check and a 10-minute TTL": becomes "an `iss` check
  when Twitch supplies one, and a 10-minute TTL". Cited by bullet name, not line number. Applied with the code.
  The architecture bullet that lists `iss` in a parenthetical is not changed.
- `CHANGELOG.md`: add a `[Unreleased]` → `Fixed` entry: "OAuth sign-in no longer requires an `iss` query parameter,
  which Twitch does not send; `iss` is still checked when present." Also update the `CHANGELOG.md` line 38 text
  "an `iss` check" to "an `iss` check when Twitch supplies one" (the Optional user identity bullet). The `Fixed`
  subsection is absent today; create it.
- `docs/SPEC.md` R47: pointer only (see §12). The R47 text is not rewritten.
- `README.md` contains no `iss` text; no change expected. The implementer confirms this in the report.
- `docs/specs/oauth-iss/tasks.md`: a checklist only, not user docs.

## 7. Security and invariants

- **CSRF and replay defence (R47) is unchanged.** It is the random single-use `state` bound to one pending flow, the
  10-minute TTL, and the loopback bind with the Host/Origin guard. Any callback, with or without `iss`, must present
  the correct `state` and is consumed on first claim.
- **`iss` stays enforced when present.** A callback that presents any `iss` other than the exact expected issuer,
  including `""`, is refused. The constant-time compare is kept.
- **Residual risk (accepted, I-D1):** with `iss` absent, the server cannot tell from the callback alone which
  authorization server issued the code (the RFC 9207 mix-up defence). This is accepted because the server is
  configured with one authorization server, the code is exchanged only at that server's token endpoint with the
  client secret, and `state` binds the response to the flow this server started.
- No new network path, no new logged value, no change to the token store (R48) or to what the callback returns.
- Loopback-only bind, bearer exemption scope and tool set are unchanged (R5, R46, R47).

## 8. Test plan and acceptance criteria

| Req | Verification |
|---|---|
| I1 | Unit §6.3(b)/(c): wrong `iss` → `REFUSED_ISS`, flow dropped; correct `iss` → flow returned. HTTP: §6.4 test 2. |
| I2 | Unit §6.3(a) (the switched happy-path test): absent `iss` → success. HTTP: §6.4 test 1 (`code` and `state` only → 200, one authorization_code POST, token file 0600). |
| I3 | Unit §6.3(d): `iss=""` → `REFUSED_ISS`. HTTP `?iss=` case optional. |
| I4 | Existing `state` tests (wrong, missing, replay) pass unchanged. |
| I5 | Existing TTL test passes unchanged. |
| I6 | Existing logging and no-echo tests pass; `iss` value absent from logs (`tests/test_logging.py` pattern). §6.4 test 1 also asserts the success body echoes no `code` or `state`. |

Acceptance also requires that §6.3(a) and HTTP test §6.4(1) fail against the unfixed `oauth.py:183` (m4), recorded in
the implementation report.

Gate: `make check` (ruff, `mypy --strict`, pytest) passes. The coverage floor is 90% branch coverage and is not lowered.
Every test keeps the 30-second per-test timeout (`pytest-timeout`, S11–S12); no test raises it. Run a single file
with `--no-cov` only while iterating. The implementation report lists the commands and results, and records that the
I-D2 default-omit applies to every driver of `claim()` or `complete()`.

## 9. Out of scope

- Re-deriving or changing any other R46–R51 behaviour, the authorize URL, or the scope set.
- Adding `iss` to the authorize request, or any change to Twitch's redirect contract.
- Live Twitch sign-in as an automated check (manual verification by the human is outside this spec).

## 10. Process

Per AGENTS.md §2–§8: this spec is approved by the human (Phase 3) before any code. Then an implementer subagent
applies §6, a separate reviewer subagent runs the review loop (max 5 rounds), and a summary report is written under
`docs/reports/oauth-iss/`. Nothing is committed unless the human asks.

## 11. Open questions

None.

## 12. Header pointers to add on approval

- `docs/SPEC.md`, under the Amendment 3 line: "**Amendment 4:** pending — see docs/specs/oauth-iss/SPEC.md (Draft, not yet approved)."
  On approval it becomes "approved by: human", and R47's `iss` clause is marked superseded by I1–I3 in place (text not rewritten).
- `CLAUDE.md` OAuth invariants bullet wording change per §6.5, applied with the code.
