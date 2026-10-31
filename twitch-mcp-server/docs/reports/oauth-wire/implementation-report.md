# Implementation report: OAuth wire shapes match Twitch's docs (`docs/specs/oauth-wire/SPEC.md`, W1–W12)

Status: **implementation complete; gate green (`make check` exit 0). Both SPEC-GAPs resolved by amendment W-A1. Nothing committed.**

## Amendment W-A1 (human ruling, resolves G1 and G2)

- **G1, resolved:** `docs/reports/initial-build/implementation-report.md` row R13 now cites
  `test_revoke_sends_client_id_and_no_headers` and `test_revoke_without_client_id_is_refused_before_the_socket`
  instead of the removed `test_only_the_token_endpoint_may_go_without_credentials`. Only that row was changed.
- **G2, resolved:** `docs/SPEC.md` now has in-place "Superseded in part by Amendment 5" markers for R47 (W1–W4), R49
  (W1–W4, W11), R50 (W5) and R51 (W6–W7), alongside the existing R13 marker (W6). Requirement text is not rewritten.

## Gate result (final, after W-A1)

`timeout 900 make check VENV=/home/leeray/venvs/twitch` → exit 0.

| Step | Result |
|---|---|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 94 files already formatted |
| `mypy --strict` | Success: no issues found in 48 source files |
| `pytest` | 1004 passed |
| Coverage (branch, floor 90%) | Total 91.36%, floor met |

## Earlier SPEC-GAP record (resolved above)

- **G1. Traceability row in a historical report.** `tests/test_gate.py::test_every_traceability_row_cites_a_real_test_of_its_own_file`
  reads `docs/reports/initial-build/implementation-report.md`, row `R13` (line 65), which cites
  `test_twitch_client.py::test_only_the_token_endpoint_may_go_without_credentials`. The spec (§6.4, tasks L) replaces that
  test, so the gate fails with `R13: test_twitch_client.py::test_only_the_token_endpoint_may_go_without_credentials`.
  The spec does not list `docs/reports/initial-build/` among its edits and says nothing about the gate's traceability check.
  I did not edit that historical report. **Options for the human:** (1) allow the R13 row to be updated to cite
  `test_twitch_client.py::test_revoke_sends_client_id_and_no_headers` and `::test_revoke_without_client_id_is_refused_before_the_socket`
  (recommended; a build record should not cite a removed test); (2) keep a test with the old name that covers the R13
  exception (contradicts "replace").
- **G2. §12 and §6.5 disagree on R47/R49/R50/R51 markers.** §12 says R47, R49, R50 and R51 are "marked superseded in
  place"; §6.5 and tasks I list only the R13 marker in `docs/SPEC.md` (plus the Amendment 5 pointer, already present).
  I applied the §6.5/tasks I version. Human to confirm whether the four extra markers are wanted.

## Gate result (last run)

| Step | Result |
|---|---|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 93 files already formatted (after formatting the four touched files) |
| `mypy --strict` (`make typecheck`) | Passed (one `var-annotated` error fixed in `test_oauth_units.py`) |
| `pytest` | 1003 passed, 1 failed (G1 above, `tests/test_gate.py`) |
| Coverage (branch, floor 90%) | Total 91.36%, floor met |
| `make check` | Fails only at the `test` step, on G1 |

Commands: `wsl -e bash -c "cd '/mnt/c/Users/hoyen/Documents/Roo Code Workspace/mcp-servers/twitch-mcp-server' && timeout 900 make check VENV=/home/leeray/venvs/twitch"`.
Targeted runs (`--no-cov`): `tests/test_oauth_units.py tests/test_twitch_client.py tests/test_server.py` → 189 passed.

## Fail-before-fix evidence (run once, before the source change, per the brief)

- **W9 HTTP test** `tests/test_server.py::test_oauth_callback_completes_without_iss`: `assert 400 == 200`
  (`done.status_code`). The array `scope` raised `malformed` in `build_token`.
- **W1 unit test** `tests/test_oauth_units.py::test_build_token_joins_a_documented_scope_array`: raised
  `TwitchMcpError` (`upstream`, "Twitch returned an unexpected token response") from `build_token` at the
  `if not isinstance(scope, str): raise malformed` check (pre-fix `oauth.py:276–277`).
- Both were run with `pytest <ids> --no-cov -q` against the unmodified `oauth.py`. No source file was edited back and forth.

## Requirement → implementation → test traceability

| Req | Implementation | Tests |
|---|---|---|
| W1 | `oauth.py` `_normalise_scope` (array of str → space-joined); used by `build_token` | `test_oauth_units.py::test_build_token_joins_a_documented_scope_array`, `::test_refresh_joins_an_array_scope`; `test_server.py::test_oauth_callback_completes_without_iss` (W9) |
| W2 | `_normalise_scope` keeps `str` and `None`; refresh `scope or fallback.scope`; exchange absent → `malformed` (`scope is None`) | `::test_build_token_keeps_a_plain_string_scope_and_an_empty_array_is_empty`, `::test_build_token_exchange_requires_scope`, `::test_build_token_refresh_keeps_fallback_scope_when_absent_or_empty` |
| W3 | `_normalise_scope` raises `upstream` malformed for number, object, bool, mixed array | `::test_build_token_refuses_other_scope_shapes` (4 params) |
| W4 | `_REAUTH_ERRORS` deleted; `_token_or_raise` maps any 400 to `unauthenticated`; `twitch_client._oauth_error` maps 400 by status, 401 unchanged, 5xx `upstream` retryable | `test_oauth_units.py` (fake `(400, …)` tests keep passing); `test_twitch_client.py::test_oauth_status_mapping_and_hints` (revoke 400, token 400 "Bad Request", mint 400, revoke 404, mint 401, revoke 503) |
| W5 | `twitch_client.validate_token` reads `login`; `oauth._resolve_user` reads `login` | `test_oauth_units.py::test_exchange_takes_login_from_validate`, `::test_exchange_ignores_user_login_in_validate` |
| W6 | `twitch_client.post_form`: revoke refuses any keyword, requires non-empty form `client_id`, sends no headers; token path unchanged; docstring and comment rewritten. `oauth.disconnect` sends `client_id` | `test_twitch_client.py::test_revoke_sends_client_id_and_no_headers`, `::test_revoke_without_client_id_is_refused_before_the_socket`, `::test_revoke_with_any_keyword_is_refused_before_the_socket` (3 params, zero requests), `::test_token_endpoint_without_keywords_sends_no_headers`, `::test_token_endpoint_with_a_keyword_needs_both_headers`; `test_oauth_units.py::test_disconnect_revokes_and_touches_nothing_else` (asserts `client_id`) |
| W7 | `oauth.disconnect`: skip revoke when `_client_id` empty (`revoke_skipped`); `revoke_refused` still deletes; other errors re-raise | `test_oauth_units.py::test_disconnect_without_client_id_skips_revoke_and_deletes`, `::test_disconnect_deletes_when_revoke_refuses_a_dead_token`, `::test_disconnect_keeps_the_credential_when_revoke_is_upstream` |
| W8 | Fakes: `server_fakes.VALIDATE_USER` (drop `user_login`, `user_type`); `twitch_fakes.set_validate` (drop `user_login`, `user_type`); `test_oauth_units._GRANT_OK` and happy-path body (array scope, no `user_id`/`login`); `test_server._register_token_exchange` (array scope, no identity). On-disk token-file fake (`server_fakes.py` `user_login`) and `_token()` left as strings | Exercised by all tests above |
| W9 | `test_server.py::test_oauth_callback_completes_without_iss` changed per W9: array scope, no identity in token body, `_register_user_validate` after boot, counts exactly one new validate request | Passes after fix (`validates_during == 1`). `::test_oauth_callback_refuses_wrong_iss` unchanged assertions, passes |
| W10 | No new logging of values. Refusal messages name the field, not its value. `revoke_refused` / `revoke_skipped` are outcome keys only | `tests/test_logging.py` passes unchanged in the gate run |
| W11 | `refresh_now`: grant call and `_token_or_raise` in one `try`; `_force_reauth` only for `unauthenticated` | `test_oauth_units.py::test_refresh_clears_the_store_only_on_unauthenticated` (3 params: raise unauthenticated clears; raise upstream retryable keeps; 500 status keeps) |
| W12 | `_oauth_error` hint by path then grant: mint `client_credentials` → env hint; `authorization_code`/`refresh_token` → connect hint; revoke → no hint. Boot: `server.py` ~1522–1526 decides on `retryable` only, so a mint 400 still degrades (unchanged). Tool-call path: `service.py:527` `app_token_unavailable` treats the mint `unauthenticated` as unavailable, with the env hint | `test_twitch_client.py::test_oauth_status_mapping_and_hints` (mint 400 env hint; token 400 connect hint) |

## Docs (§6.5)

- `CLAUDE.md`: `twitch_client.py` Architecture bullet (R13 exception text, W6) and OAuth invariant bullet clause (W1, W4, W11).
- `docs/SPEC.md`: R13 marker "Superseded in part by Amendment 5 (W6)…". Amendment 5 pointer was already present.
- `CHANGELOG.md` `[Unreleased]` → `Fixed`: entry added per §6.5.
- `README.md`: no change needed (checked: it describes scope tables and sign-in, neither changed).
- `docs/SPEC.md` §12 markers on R47/R49–R51: not applied, see G2.

## Files changed

- `src/twitch_mcp/oauth.py`: `_normalise_scope` (new), `build_token`, `_REAUTH_ERRORS` removed, `_token_or_raise`, `refresh_now`, `disconnect`, `_resolve_user`.
- `src/twitch_mcp/twitch_client.py`: `post_form` (revoke rules, docstring and comment), `_oauth_error` (signature `path`, `grant`), `validate_token` (`login`).
- `tests/test_oauth_units.py`, `tests/test_twitch_client.py`, `tests/test_server.py`, `tests/server_fakes.py`, `tests/twitch_fakes.py`.
- `CLAUDE.md`, `docs/SPEC.md`, `CHANGELOG.md`, this report.

## Deviations (for the reviewer)

1. The generic 400 hint on a token-path form with no `grant_type` is `"Check the OAuth request and retry."`. The spec says "the generic 400 hint" but gives no text. Chosen here; the reviewer may want different wording.
2. `_oauth_error` no longer reads the body (`del body`). W4 requires status-only mapping, so this is the spec, not a change of design.
3. `_token_or_raise` keeps its `hint` argument (used by the 401 and 5xx branches unchanged).
4. The `test_gate` failure (G1) is not fixed here; see the SPEC-GAP.

## Not done / open

- Gate not green until G1 is decided (`make check` fails at `test`).
- Review loop (tasks P), summary report, and any commit are not started, per the brief.
