# Tasks: OAuth wire shapes match Twitch's docs (`docs/specs/oauth-wire/SPEC.md`, W1–W12)

Status: Draft. Nothing here starts until the human approves the SPEC in chat (W-D1 to W-D6 confirmed). Tick a box only
when its acceptance check has run and passed.

- [ ] A. Approval: human approves SPEC and confirms W-D2 to W-D6 in chat (the W-D4 mint effect and W-D6 consequences
  first); header set to Approved.
- [ ] B. Write the failing tests first: (1) `tests/test_server.py` `test_oauth_callback_completes_without_iss` with array
  `scope`, no `user_id`/`login` in the token body, and a validate handler registered after boot (W9); (2) a unit test
  with an array `scope` in `tests/test_oauth_units.py`. Run both against the unfixed code and record the failures in the
  implementation report: the HTTP test returns 400, and the unit test raises `upstream`/`malformed`. [W9]
- [ ] C. `oauth.py` `build_token`: normalise `scope` (array of str → space-joined; str kept; other → malformed; falsy
  refresh scope falls back). [W1, W2, W3]
- [ ] D. `oauth.py`: delete `_REAUTH_ERRORS` (`:67–69`); `_token_or_raise` maps any 400 to `unauthenticated`. Confirm
  no test imports the constant. [W4]
- [ ] E. `oauth.py` `refresh_now`: move the grant call and `_token_or_raise` into one `try`; `_force_reauth` only for
  `unauthenticated`. [W11]
- [ ] F. `oauth.py` `UserAuth.disconnect`: send `client_id` in the revoke form; skip revoke when `_client_id` is empty.
  `_resolve_user` reads `login`. [W5, W6, W7]
- [ ] G. `twitch_client.py` `post_form`: on revoke, any keyword raises `unauthenticated` before the socket (R2-1); no headers otherwise; refuse a revoke without non-empty `client_id` before the socket. Rewrite the docstring and comment at `:927–951`. [W6]
- [ ] H. `twitch_client.py` `_oauth_error(status, body, path, grant)` keyed first on path: status-only 400 with a grant-specific hint (mint: env
  check; sign-in grants: connect hint; revoke: none). `validate_token` reads `login` (`:1059`). [W4, W5, W12]
- [ ] I. R13 documentation edits: `CLAUDE.md` `twitch_client.py` Architecture bullet (the R13 exception for token and
  revoke); `CLAUDE.md` OAuth bullet clause; `docs/SPEC.md` R13 marker at `~157`; `docs/SPEC.md` Amendment 5 pointer
  (already present). [§6.5, R13]
- [ ] J. Tests: switch fakes to documented shapes (`tests/test_oauth_units.py:350–354, 539–547`,
  `tests/test_server.py:671–679`, `tests/server_fakes.py:65–75` `VALIDATE_USER`, `tests/twitch_fakes.py:291–310`
  `set_validate`); keep the on-disk token-file fake at `tests/server_fakes.py:350–364` as a string. [W8]
- [ ] K. Tests: explicit array, string and malformed scope cases on exchange and refresh; `test_disconnect_revokes_and_touches_nothing_else`
  calls `set_credentials` and asserts `client_id` in the form, plus a no-credentials case that sends no revoke and logs `revoke_skipped`. [W1–W3, W7]
- [ ] L. Tests: replace `test_only_the_token_endpoint_may_go_without_credentials` (`tests/test_twitch_client.py:316–330`);
  add the `post_form`-level W4 and W12 cases (revoke 400, token 400, mint 400 env hint, 404, 401, 503); add the W7
  raising-revoke unit test; add the R2-1 keyword cases (revoke with `client_id` only, `bearer` only, both: each `unauthenticated`, zero requests); add the W11 raising-grant tests (`unauthenticated` clears, `upstream` keeps, `500` keeps). [W4, W6, W7, W11, W12]
- [ ] M. Tests: W9 HTTP test passes after the fix, counting new validate requests (exactly one); the wrong-`iss` HTTP test
  still passes. [W9]
- [ ] N. Docs: `CHANGELOG.md` `[Unreleased]` `Fixed` entry (§6.5); confirm `README.md` needs no change. [§6.5]
- [ ] O. Gate: `make check` (ruff, `mypy --strict`, pytest) passes at ≥90% branch coverage; 30 s per-test timeout kept.
  Report the commands and results. [§8]
- [ ] P. Review loop (reviewer subagent, max 5 rounds) and `docs/reports/oauth-wire/summary-report.md`. Nothing committed
  unless the human asks.
