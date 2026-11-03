# Spec review, round 1: `docs/specs/oauth-wire/SPEC.md` (+ `tasks.md`, Amendment 5 pointer)

Reviewer: read-only. Twitch docs fetched on this review (validate, revoke, refresh, getting-tokens). Code lines read at HEAD plus working tree.

**Verdict: CHANGES REQUESTED** (0 blockers, 4 majors, 6 minors)

## Structure check
- AGENTS.md section 4 sections present: header (title, Status Draft, date, amends), problem, goals/non-goals, decisions, numbered requirements W1-W10, design, security, test plan, out of scope, open questions (empty). OK.
- `docs/SPEC.md:10` pointer says "pending ... Draft". OK. tasks.md is consistent with the spec.
- No scope creep beyond OAuth wire shapes, except the R13 change (see M3) which is in scope but under-declared.

## Audit rows verified against Twitch docs and code

| Row | Result |
|---|---|
| A1 exchange `scope` array | CONFIRMED (getting-tokens example `"scope": ["channel:moderate","chat:edit","chat:read"]`; `oauth.py:276` rejects non-str) |
| A2 | CONFIRMED (no identity fields documented) |
| A3 refresh request | CONFIRMED |
| A4 refresh `scope` array | CONFIRMED. Note: the refresh page says its own initial-token example shows a string, so "handle both" (W2) is right |
| A5, A6 client_credentials | CONFIRMED (no `scope`, no `refresh_token`) |
| A7 validate | CONFIRMED: docs field is `login` (null for app tokens); `user_login` is never documented. `scopes` String[], `expires_in` Int, `user_id` null for app tokens. Code lines: `scopes` read is `:1045`, `user_id` is `:1051` (the spec's `:1057` is the `user_id=` argument line, fine), `expires_in` `:1052`, `user_login` `:1059`. OK |
| A8 validate 401 | CONFIRMED (`{"status":401,"message":"invalid access token"}`); code at `:1035` |
| A9 revoke request | CONFIRMED: form `client_id` (required) + `token` (required), `Content-Type: application/x-www-form-urlencoded`, no auth header documented. Code claim CONFIRMED: `oauth.py:604` sends token only; `_headers` refuses with no client_id before the socket |
| A10 revoke 200 no body | CONFIRMED |
| A11 revoke 400 `{"status":400,"message":"Invalid token"}` | Doc CONFIRMED (client id valid, token invalid). Code claim imprecise: see M2 |
| A12 revoke 404 `client does not exist` | CONFIRMED |
| A13 refresh 400 `{"error":"Bad Request","status":400,"message":"Invalid refresh token"}` | Doc CONFIRMED, plus doc says 401 for a dead refresh token. **Code claim DISPUTED**: see M2 |
| A14 token_type | CONFIRMED |

No audit row has a wrong Twitch-doc claim. Disputed: A13 (and the A11 consequence) on the code side.

## Findings

### Major

**M1. W8 lists `tests/server_fakes.py:355-358` as a token *body* fake to switch to an array `scope`. It is the on-disk token file.**
`server_fakes.py:350-364` writes the `0600` store JSON (`"version": _FILE_VERSION`, `"scope": " ".join(...)`). `_from_file` (`oauth.py:298,313-317`) requires `scope` to be a `str` and raises `invalid_fields` otherwise (R48, which W-D2 and section 6.1 say is unchanged). Following W8 literally would break the connected `running_app()` boot.
Fix: remove `server_fakes.py:355-358` from W8's list and say the stored file keeps a string `scope`. The server_fakes change that is actually needed is `VALIDATE_USER` (`server_fakes.py:65-75`): drop the undocumented `user_login` (and `user_type`) so the fake stops masking W5. Add that to W8 and 6.4. `twitch_fakes.py:298-308` (`set_validate`) likewise sets both `login` and `user_login`; the spec says "carry `login`" but should say "and drop `user_login`".

**M2. W4/W7/A11/A13 misdescribe the production error path, so the claim "the reauth path never fires ... fixed by W4/W7" is not delivered.**
In production `post_form` raises on every non-2xx (`twitch_client.py:979-980`, `raise self._oauth_error(...)`). `_token_or_raise`'s non-2xx branches (`oauth.py:690+`) are reached only by unit fakes that *return* `(400, body)`. Consequences:
1. The production 400 mapping lives in `_oauth_error` (`twitch_client.py:984-994`), which already maps `invalid_grant|invalid_request|unsupported_grant_type`; A13's code cell should cite it, not (only) `_token_or_raise`.
2. In `refresh_now` the `self._grant(...)` call (`oauth.py:662-669`) is *outside* the `try` that calls `_force_reauth` (`oauth.py:670-675`). So in production a refresh 400 (mapped `unauthenticated` by W4) propagates without `_force_reauth("refresh_rejected")`: the dead credential is not cleared and R49 "a refresh failure forces re-auth and clears the store" is still not met. This is also true today for `invalid_grant`.
3. `disconnect` (`oauth.py:600-608`) also receives a raised `TwitchMcpError`, never a returned status. The W7 unit test must therefore use a revoke fake that *raises* `TwitchMcpError("unauthenticated", http_status=400)`; the 400-to-`unauthenticated` mapping itself must be tested at `post_form` level in `test_twitch_client.py`. The test plan row W7 ("Unit: dead-token revoke (400)") is ambiguous on this.
Fix: either (a) add a requirement that `refresh_now` wraps the grant call so an `unauthenticated` raised from it also forces re-auth (state it as a behaviour change to R49 and add a test that raises from the grant fake), or (b) drop the claim that W4 restores the reauth path. Correct A13/A11 code cells and the W7 test description either way. Option (a) is what the human's intent (dead refresh token means sign in again) needs.

**M3. W6/W-D5 changes the R13 invariant but R13 is not in "Amends" and the CLAUDE.md edit list does not cover it.**
`docs/SPEC.md:157` R13: "Every request carries both Client-Id and Authorization ... raises before the socket." `CLAUDE.md` Architecture bullet for `twitch_client.py` says "`Client-Id` + `Bearer` asserted before the socket (R13)" and the "One httpx module" invariant says "`POST` goes only to `/oauth2/token` (and `POST /oauth2/revoke`)". The spec (section 7) says the "R13 exception is narrowed to two exact paths" but the header lists R47, R49, R50, R51 only, and 6.5/12 change only the OAuth bullet. (R13 is already excepted for `/oauth2/token` in code, undocumented in R13's text, so the doc is already stale; this spec widens it.)
Fix: add R13 to the "Amends" line and to the section 12 superseded list; in 6.5 list the exact CLAUDE.md edits: the `twitch_client.py` Architecture bullet (R13 phrase becomes "Client-Id + Bearer asserted before the socket, except `POST /oauth2/token` and `POST /oauth2/revoke`, which authenticate with form fields (R13)") and the post_form docstring/comment (`twitch_client.py:927-951`, which states "the only endpoint"). Also specify the exact condition: today it is `path == OAUTH_TOKEN_PATH and client_id is None and bearer is None`; say whether revoke follows the same "both None" rule (recommended: yes, keep the guard, so a caller passing one header is still refused) and that revoke additionally requires non-empty `client_id` in the form.

**M4. Section 4.1 W-D4 / W4 widen the 400 mapping for the app-token mint without saying so.**
`AppTokenSource` (`server.py:1438-1443`) uses the same `post_form`/`_oauth_error`. A 400 on client_credentials (invalid client id) currently becomes `upstream`; under W4 it becomes `unauthenticated` with hint "Use connect_twitch_account to re-authorize", which is wrong for the mint and which `service.py` treats as "app_token_unavailable" degraded (`service.py` ~520). Also a 400 on refresh will now wipe the stored credential if M2(a) is adopted, including for a malformed-request 400 (unencoded refresh token, per the refresh page).
Fix: state these effects in W-D4's trade-off, and either give `_oauth_error` a hint that is correct for all three callers (e.g. check `TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET` or sign in again) or restrict the sign-in hint by path/grant. Add a test for the mint 400 mapping. If kept as is, the human must confirm it at the gate (flag it under W-D4).

### Minor

- **m1.** Section 4.2 header says "the five Twitch pages" but lists four; fix the count.
- **m2.** `_REAUTH_ERRORS` removal is safe: grep shows only `oauth.py:67` and `:690`; no test imports it. State this in the spec (the question was asked, answer: nothing else breaks). Existing tests that return `(400, {"error":"invalid_grant"})` (`test_oauth_units.py:210,363`) keep passing under status-only mapping.
- **m3.** W9: the callback test uses `running_app(..., connected=False)`, which registers no OAuth routes; `FakeTwitch._default` (`twitch_fakes.py:370-378`) answers `/oauth2/validate` only from `fake.validate` and 404s otherwise. Boot therefore already issues one validate request (404, degraded) before the test's handler is added. "Validate is called exactly once" must be stated as exactly one *new* request after the handler is registered (count before and after), not `len(fake.requests_for(...)) == 1`. Also state that the validate handler is added after boot and that the user-bearer vs app-bearer split is keyed on the `Authorization` header value (`fake-user-access`). `tmp_path`-based `twitch_oauth_token_file` is already used by the existing test; keep it.
- **m4.** W6 unit test "form carries `client_id`" in `test_disconnect_revokes_and_touches_nothing_else` (`test_oauth_units.py:277`) needs `auth.set_credentials(CLIENT_ID, CLIENT_SECRET)`; that test never sets them, so `_client_id` is `""`. Also, `disconnect` with an empty `_client_id` should not send a doomed revoke; specify (skip revoke and just forget, or let `post_form` refuse and be swallowed as `unauthenticated`).
- **m5.** The refresh page documents 401 for a dead refresh token; `_oauth_error`'s 401 message is "Twitch rejected this server's credentials" with an env-check hint. Behaviour is unchanged by this spec, but note it in section 4.2 A13 as a known wording gap so the reviewer does not re-raise it.
- **m6.** tasks.md step B says to record the failure as "`malformed`, 400"; the callback returns 400 for the `upstream` malformed error, fine, but add the unit-level expected failure too. tasks.md also lacks a step for the CLAUDE.md Architecture/R13 edits (M3) and the `docs/SPEC.md` R13 marker.

## Tests that will break, completeness
Listed correctly: `test_twitch_client.py:316-330`, grant/validate bodies in `test_oauth_units.py`, `test_server.py:660-690`. Missing from the spec: `server_fakes.py:65-75` `VALIDATE_USER` (only if `user_login` is removed; no test asserts it, so low risk), `twitch_fakes.py:298-308` precision (see M1). `test_service.py:1067` (`disconnect`) is unaffected: `make_connected_flow` attaches no revoke. `test_logging.py` is unaffected. CHANGELOG `### Fixed` exists in `[Unreleased]` (line 116); the proposed entry is accurate. CLAUDE.md OAuth bullet wording is fine once M3 is added.
