# Implementation report: scope-aware 401 and per-token-class Helix circuits (Amendment 7, C1–C10)

Spec: `docs/specs/scope-401/SPEC.md` (Status: Approved). Tasks: `docs/specs/scope-401/tasks.md`.
Nothing is committed.

## 1. Requirement traceability (C1–C10)

| Req | Implementation | Test(s) |
|---|---|---|
| C1 `message` read for scope only | `twitch_client.py`: `_SCOPE_BEARING_KEYS = ("error_description", "error", "message")`; `_scope_phrase` is called only from the 401 and 403 branches of `_map`. `message` never sets `kind` otherwise. | N1, N2 (`test_twitch_client.py::test_missing_scope_in_message_is_permission_and_opens_no_circuit[401/403]`), N3 (`test_service.py::test_c1_user_banned_users_message_only_scope_is_permission_and_no_circuit`), N14 (`test_message_is_read_only_for_a_scope_phrase_on_401_or_403[500/401/403]`) |
| C2 permission, no circuit, sanitised hint | `_map` 401 branch checks `_scope_phrase` first and returns `_permission_error` with no trip. `_scope_phrase` replaces each control character and newline (`_CONTROL_CHARS_RE`) with one space. | N1, N2, N3, N11 (`test_scope_hint_drops_control_characters_and_newlines`); existing `test_missing_scope_becomes_permission_carrying_twitchs_own_string` (unchanged; its no-circuit check is in N1/N2, not added to this test, a deviation from tasks.md K) |
| C3 per-class circuit, class hint on read refusals only | `ratelimit.py`: `_auth_failed` and `_auth_message` are dicts keyed by token class; `auth_failed(token_type)`; `trip_auth(token_type, *, message)`; `acquire(..., circuit=True, class_hint=True)` refuses with `circuit_hint(token_type)` (`_CIRCUIT_HINTS`). The `_map` read `401` carries the class hint. Writes refused by `acquire` use `class_hint=False` (see deviation D1). | N4 (`test_user_and_app_circuits_are_isolated_at_the_client`), N6 (`test_service.py::test_open_app_circuit_refuses_app_reads_without_a_request`), N10 (`test_ratelimit.py::test_401_circuit_stops_its_own_token_class_only`, `test_circuit_survives_every_attempt_and_logs_once`, `test_the_auth_listener_fires_once_per_class`), `test_first_401_trips_its_token_class_circuit` (asserts the app-class hint text) |
| C4 non-scope user Helix 401 opens only user circuit | Same `_map` branch with `kind=USER_CLASS`; `acquire` gates by class. | N4 |
| C5 non-scope app Helix 401 opens only app circuit; `twitch_account_status` unchanged | Same. `service.twitch_account_status` not modified. | N4 (mirror half), N6 |
| C6 write: scope → permission, non-scope → unknown_outcome + class circuit, never re-issued | `_map` 401 branch: scope returns permission for reads and writes; non-scope write raises `_unknown_outcome` (R16 hint unchanged) after `trip_auth(kind)`. `_write` acquires with `class_hint=False`. | N13 (`test_non_scope_write_401_is_unknown_outcome_and_trips_only_the_user_class`, `test_scope_401_on_a_write_is_permission_and_opens_no_circuit`), existing `test_identity.py::test_identity_is_invalidated_on_the_first_401` (circuit assertion changed to `service.limiter.auth_failed(USER_CLASS)`) |
| C7 user circuit cleared on stored or forgotten token; app never | `ratelimit.py::reset_user_circuit` clears `USER_CLASS` only and logs one line. `server.py` (beside `oauth.on_invalidate(service.on_identity_change)`): `oauth.on_invalidate(lambda: service.limiter.reset_user_circuit())`. No `oauth.py` change. | N7 (`test_oauth_units.py::test_sign_in_clears_the_user_circuit_it_was_blocked_by`), N8 (`test_refresh_under_an_open_circuit_renews_and_keeps_the_file[app/user]`), N9 (`test_install_never_clears_the_app_circuit`), `test_ratelimit.py::test_reset_user_circuit_clears_only_the_user_class` |
| C8 `/healthz` follows app circuit only | `server.py` `Wiring.auth_failed` returns `self.service.limiter.auth_failed(APP_CLASS)`. `service.auth_failed` property removed. Response keys unchanged. | N12 (`test_server.py::test_healthz_follows_only_the_app_class_circuit`); existing healthz tests unchanged and passing |
| C9 token exchange, refresh, revoke, mint and validate never gated; no `_force_reauth` from an open circuit | `post_form` (mint, token exchange, refresh), `validate_token` and `fetch_validate` acquire with `circuit=False`. `fetch_validate(bearer, *, client_id=None, token_type=APP_CLASS)` takes a bucket selector only; its `_map` passes `trip_circuit=False`, so a validate `401` gives `unauthenticated` with the neutral hint and trips nothing. `server.py::_validate_pair` passes `token_type=USER_CLASS`. | N7, N8 (both above), N15 (`test_ungated_paths_still_send_with_both_circuits_open`) |
| C10 docs: README and `.env.example` scope list and copy-paste line | `README.md` section 4 "Per-group OAuth scopes": C10 table and copy-paste line added; first-401 and auth-circuit text (lines 161, 338, 391) and troubleshooting rows updated. `.env.example` commented scope example gets the copy-paste line. The line matches the spec byte-for-byte (verified with `grep -cF`). | Manual verification (no code test); `grep -cF` matched in README, `.env.example` and SPEC. |

Also updated per tasks.md L: `CLAUDE.md` (the circuit invariant, the `twitch_client.py`, `identity.py` and `errors.py` bullets; the verified-identity invariant), `docs/SPEC.md` superseded markers at R24, R44, R45 and the §6.3 write path, and `CHANGELOG.md` `[Unreleased]` `### Fixed`.

## 2. Fail-before-fix evidence

The failing-first tests (N1, N2, N3, N7, N8) were written and run once against the unfixed code, with this selection:

```
pytest tests/test_twitch_client.py tests/test_service.py tests/test_oauth_units.py \
  -k 'missing_scope_in_message or c1_user_banned or sign_in_clears or refresh_under_an_open_circuit' --no-cov -q
```

Result: `6 failed, 290 deselected`. Each failed for the stated reason:

- N1 `[401]`: `assert 'unauthenticated' == 'permission'` (`kind` assertion, first).
- N2 `[403]`: `assert 'upstream' == 'permission'` (403 body read only via `error`, so `message` was ignored).
- N3: `assert 'unauthenticated' == 'permission'`.
- N7 (sign-in under an open user circuit): `TwitchMcpError: Twitch rejected this server's token`, raised by `ratelimit.acquire` inside `post_form`, because the single circuit refused the app-class grant before the token file was written.
- N8 `[app]` and `[user]` (refresh under an open circuit): the same refusal inside `refresh_now`.

After the implementation, all six pass. The full gate below includes them.

## 3. Deviations and interpretations

- **D1 (acquire keyword).** §6.1 lists `acquire(..., *, circuit=True)`. A second keyword, `class_hint: bool = True`, was added so that a write refused by an open circuit carries no class hint (C3 and C6 require this, and `acquire` cannot otherwise tell a read from a write). Behaviour matches the spec; the signature gained one keyword.
- **D2 (neutral hint).** `TwitchMcpError` treats `hint=None` as "use the kind's default", and the default `unauthenticated` hint names a process stop and a restart. So a validate `401` and a circuit-refused write use a new neutral constant, `NEUTRAL_AUTH_HINT` ("Check the credentials in .env and re-authorize the account if needed."), which claims no circuit. The spec's "no circuit hint" is met.
- **D3 (log outcome).** A missing-scope `401` now logs outcome `permission` (as a `403` already does) instead of `auth`. It is still `kind=permission` and no circuit line is written.
- **D4 (reset log).** `reset_user_circuit` writes one `info` line on each call, as §6.1 says, even when no circuit was open. The line carries no token material.
- **D5 (validate with scopes).** `_permission_error` says "This token holds: …" when the body carries a `scopes` list, even an empty one (then the sentence reads "holds: ."). This follows C-D10 literally. No test covers the empty-list case.
- **D6 (CLAUDE.md service bullet).** Spec §6.3 names the `service.py` bullet (line 62). That bullet has no circuit or message-rule text, so it was left unchanged.
- **Existing test updated, not weakened:** `test_ratelimit.py::test_circuit_survives_every_attempt_and_logs_once` keeps its name (it is cited by the historical R24 row in `docs/reports/initial-build/implementation-report.md`, which `test_gate.py` checks). Its body moved to per-class calls and counts two log lines.
- No SPEC-GAP was found. Nothing was improvised beyond D1 and D2, which are mechanism details the spec's outcomes fix.

## 4. Commands and results

Gate, final run (after the last test addition and formatting):

```
wsl -e bash -c "cd '/mnt/c/Users/hoyen/Documents/Roo Code Workspace/mcp-servers/twitch-mcp-server' && timeout 900 make check VENV=/home/leeray/venvs/twitch"
```

- ruff check: `All checks passed!`
- ruff format --check: clean (114 files already formatted after the final `ruff format tests`).
- mypy --strict: `Success: no issues found in 48 source files`.
- pytest: `1024 passed`, 30-second per-test timeout kept.
- Coverage (branch): `Total coverage: 91.55%`, gate 90% reached.

Earlier gate runs, for the record: run 1 failed at lint (12 issues, mostly line length and import order), fixed with `ruff format` and `ruff check --fix` plus manual wraps. Run 2 failed on two tests: `test_gate` (the renamed test name in the historical traceability row; fixed by restoring the name) and `test_healthz_follows_only_the_app_class_circuit` (the boot probe needed the `_answer_app_token` and validate setup; fixed). Run 3 (`EXIT=0`) and the final run above both pass.

## 5. Files changed

Source: `src/twitch_mcp/ratelimit.py`, `src/twitch_mcp/twitch_client.py`, `src/twitch_mcp/service.py`, `src/twitch_mcp/server.py`, `src/twitch_mcp/errors.py`.

Tests: `tests/test_twitch_client.py`, `tests/test_ratelimit.py`, `tests/test_service.py`, `tests/test_oauth_units.py`, `tests/test_identity.py`, `tests/test_server.py`.

Docs: `README.md`, `.env.example`, `CLAUDE.md`, `docs/SPEC.md`, `CHANGELOG.md`, and this report.

Not touched: `.env` (never read or edited), `oauth.py`, `models.py`, `capabilities.py`, requirements files, the running container.
