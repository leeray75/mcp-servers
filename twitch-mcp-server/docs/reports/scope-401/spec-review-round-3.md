# Spec review, round 3: scope-401 (Amendment 7)

Reviewed from disk: `SPEC.md`, `tasks.md`, `docs/reports/scope-401/spec-fix-round-2.md`, against `oauth.py`, `twitch_client.py`,
`ratelimit.py`, `tests/core_fakes.py`, `tests/test_oauth_units.py`, `tests/test_twitch_client.py`. Read-only; nothing run.

**Verdict: CHANGES REQUESTED** (0 blockers, 1 major, 4 minor)

## Round 2 dispositions verified

- M1 (N7/N8): implementable. `make_client` (`core_fakes.py:84`) builds a real `TwitchClient` on `FakeTwitch`; `UserAuth.attach_grant`/`attach_validate`
  exist (`oauth.py:525,539`); `OAUTH_TOKEN_PATH` is exported by `twitch_client.py:64`; `_validate_pair` is importable from `server.py`; the
  oauth unit tests already use `UserAuth(TokenStore(tmp_path/...), clock=fclock.clock)` and the `_token()` helper. `FakeTwitch` already serves
  `/oauth2/token` in `test_twitch_client.py:299`. N8 is genuinely failing-first on the current code: the app Helix `401` trips the process-wide
  circuit, `post_form` (`:967`, `acquire(APP_CLASS)`) is refused with `unauthenticated`, `refresh_now` calls `_force_reauth`, and the token file is
  deleted; it uses only existing API until the assertions. N15's second half (validate `401` trips on the unfixed code) is failing-first too.
- m1-m5 are all resolved: class hint (C3, tasks E), C-D9 wording (4.2, 7, docs marker), C5 block dropped (C5, 3, 9), race recorded in 7, N13
  wording and N5 split. No leftover `user_token_rejected`, `on_install` or C-D7 text.
- No new scope creep: `twitch_account_status` unchanged, no cap, no grep test, no wrappers.

## Major

**M1. C3 / 6.1 replace the write's `unknown_outcome` hint with the circuit hint.** SPEC C3 ("first-call `401` in `_map` ... use the same
hint") and 6.1 ("The raised `unauthenticated` (read) or `unknown_outcome` (write) hint is the class hint from C3 ... The current 'restart the
container' hint at lines 775-778 is replaced") would drop the R16 guidance that `_unknown_outcome` (`twitch_client.py:700-714`) carries: "Do not
re-issue this call: it may already have taken effect. Check the channel ...". That hint is the safety message for the non-scope write `401` (C6),
and the lines 775-778 being replaced are only the *read* `unauthenticated` hint. A sign-in hint on an `unknown_outcome` would tell the agent to
retry. Fix: state that only the read `unauthenticated` raise gets the class hint; the write keeps `_unknown_outcome`'s hint unchanged (optionally
append one class sentence), and add an assertion to N13 that the non-scope write `401` hint still contains "Do not re-issue".

## Minor

- m1. N7 is failing-first only through missing API: `on_invalidate(client.limiter.reset_user_circuit)` and `auth_failed(USER_CLASS)` do not exist
  on the unfixed code, so the test errors with `AttributeError`/`TypeError` before reaching the sign-in outcome. Wire with
  `on_invalidate(lambda: client.limiter.reset_user_circuit())` (`_invalidate` suppresses exceptions at install time) and assert the token
  file before `auth_failed(USER_CLASS)`, so the first failure is the behavioural one. Also the spec says that on the unfixed code
  "`_resolve_user` is refused"; it is the grant (`post_form`) that is refused first. Reword.
- m2. N7 must say the faked token response carries **no** `user_id` (Twitch's does not); otherwise `_resolve_user` and the validate call are not
  exercised and the deadlock path is not covered.
- m3. Test machinery overlap: N5a duplicates N4 (and N6 overlaps N4 at service level), and N5b duplicates the second half of N15. Fold N5a/N5b
  into N4/N15 to keep the plan minimal.
- m4. Counting drift: the SPEC header ("adds requirements C1-C9") and tasks.md title ("C1-C9") omit C10, which exists in section 5.

## Notes

- C7 via `oauth.on_invalidate` and the clearing on `forget()`/R49 remain acceptable (confirmed in round 2). The in-flight race is recorded in 7.
- Scope table (C10) unchanged since round 1 and still matches the Twitch scopes page.
