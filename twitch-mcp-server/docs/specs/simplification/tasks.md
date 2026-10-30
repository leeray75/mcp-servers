# Tasks: simplification (`docs/specs/simplification/SPEC.md`, S1–S15)

Status: Draft. Nothing here starts until the human approves the SPEC in chat. Each task maps to requirement IDs in
brackets. Tick a box only when its acceptance check has run and passed.

## A. Remove the per-minute budget (S1–S4)

- [ ] A1. Delete `src/twitch_mcp/budget.py`. [S1]
- [ ] A2. Remove `tool_calls_per_minute*` fields and the R23 comment block from `config.py` (≈215–217). [S2]
- [ ] A3. `server.py`: remove the `ToolBudget` import (50), the `budget` parameter of `_run`, `_register_tools`, `wired`,
      boot, and the ≈40 `budget,` call sites; update docstring (8) and the `_run` comment (≈723). [S1, S10]
- [ ] A4. `service.py`: remove the `budget` constructor parameter (338), `self._budget` (364), `_charge` (≈451), the
      `try_consume` call (453), the docstring word "budget" (331); **renumber the module docstring list (lines 11+)**
      after item 1 is removed. [S1]
- [ ] A5. `tests/service_fakes.py`: remove lines 145–146, 167–168, 40, 76, 211–216, 243, 263, and "budget" in the
      docstring (7). `tests/server_fakes.py`: remove the budget parameters and constructions (143, 157, 214, 257, 283,
      292, 300) and the import (50). `tests/test_identity.py`: remove import (50) and argument (717). [S1]
- [ ] A6. Delete `tests/test_budget.py`; delete `test_budget_refusal_costs_no_request` in `tests/test_service.py`
      (≈309–312) only; delete `test_budget_exhaustion_is_a_retryable_tool_error_that_sent_nothing` in
      `tests/test_server.py` (788) and its import (50). [S9a]
- [ ] A7. `tests/test_ratelimit.py`: `IO_MODULES` drops `"budget.py"` (29). `tests/twitch_fakes.py`: remove the two env
      names (472–473). `tests/test_config.py`: delete the budget cases (277–278, 394). [S3, S9a]
- [ ] A8. Confirm `cache.py` is untouched and its three consumers are unchanged. [S4]
- [ ] A9. Acceptance: the identifier grep in SPEC A-S2 returns no hits under `src/` and `tests/`. Unrelated "budget"
      words (`cache.py:120`, `twitch_client.py:528`, `ratelimit.py:72`, `test_write_safety.py` retry tests) are untouched.
      [S1, S2]

## B. Remove the TLS mode checks, the repo-tree check and the plaintext refusal (S5–S8)

- [ ] B1. `config.py`: remove `TLS_KEY_MODE`, `MODE_PROBE_NAME`, `SYNTHETIC_MODE`, `_modes_are_expressible`, the mode
      branch and its `tls_key_mode_rule_skipped` outcome (≈394–408). Keep both-or-neither, path, PEM and key match. [S5, S8]
- [ ] B2. `config.py`: remove `_check_tls_location` (123) and its call (390); remove `DEFAULT_TLS_HOST_DIR` (47) if unused. [S6]
- [ ] B3. `security.py`: delete `PlaintextRefusalMiddleware` (113–141) and its module-docstring paragraph (11–14). [S7]
- [ ] B4. `server.py`: remove the import (95) and `add_middleware` (1889). Keep `/healthz` `tls` and the R47 `302`. [S7, S8]
- [ ] B5. `__main__.py`: replace the comment at 58–63. [S5]
- [ ] B6. `tests/test_tls.py` deletions: the eight tests in SPEC §8.1. [S9a]
- [ ] B7. `tests/test_tls.py` imports and helpers: remove `DEFAULT_TLS_HOST_DIR`, `TLS_KEY_MODE`, `_check_tls_location`
      (lines 52, 53, 55); in `_write_pair` (72) remove `key_mode` and use `chmod(0o600)` (108); line 480 `key.chmod(0o600)`. [S5, S9]
- [ ] B8. `tests/test_tls.py` docstrings: module docstring (7–14, the T8 seam sentence); `_write_pair` docstring (75–76);
      fixture (191, "owner-only key" becomes "key"); `https_client` (≈200, the middleware reference); test at 613 reworded
      to "T2: plain HTTP is unchanged without TLS". [S8, S9]
- [ ] B9. `tests/test_tls.py` T9 test `test_no_certificate_detail_reaches_the_log_or_the_health_response` (676): replace the
      plaintext `400` assertion with `https_client(app)` `/healthz` `200`. [S7, S8]
- [ ] B10. `tests/test_tls.py` T-map (S9c): `SUPERSEDED_TLS_REQUIREMENTS = frozenset({"5", "8"})`; `required` is declared
      minus this set; `EXPECTED_TLS_REQUIREMENTS` (test_tls.py:936) becomes explicit {1, 2, 3, 4, 6, 7, 9, 10, 11, 12}. Remove any
      remaining T5/T8 citation in the file. [S9c]
- [ ] B11. Add the `test_tls.py` tests in SPEC §8.2 (S2 in test_config, S5, S6, S7). [S5, S6, S7]
- [ ] B12. Acceptance: the grep in SPEC A-S2 is clean under `src/` and `tests/`, apart from the `SUPERSEDED_*` sets. [S5–S7]

## C. Gate-test superseded mechanism (S9b, S11)

- [ ] C1. `tests/test_gate.py`: add `SUPERSEDED_REQUIREMENTS = {"23"}` beside `SELF_CITING` (26). In
      `test_every_specification_requirement_is_named_by_a_test` subtract it from `missing` (100). In
      `test_every_traceability_row_cites_a_real_test_of_its_own_file` skip rows whose label is in the set. Do **not** edit
      `docs/reports/initial-build/implementation-report.md` row 75 (historical). [S9b]
- [ ] C2. `tests/test_gate.py`: add the three pyproject assertions (`pytest-timeout` in the dev extra, `timeout = 30`,
      `timeout_method = "signal"`) to `test_pyproject_holds_the_strict_and_coverage_gates`. No new test. [S11]

## D. Scripts, compose and documentation (S14)

- [ ] D1. `scripts/make-cert.sh` (and `.ps1` if the same text): remove lines 94–103 (stat-and-warn block); keep `chmod 600`;
      add the "server does not check key permissions" line. [S14]
- [ ] D2. `docker-compose.tls.yml` lines 11–12: rewrite the "must be mode 0600 … config.py refuses (T4, T-D7)" comment. [S14]
- [ ] D3. `README.md`: line 117 (the "refused with `400`" bullet); 148 (`TWITCH_TLS_KEY_FILE` row); 156–157 (budget rows, remove);
      370 ("budgets", change); 391 (mode troubleshooting row, remove); 394 (400 row, replace with the handshake-failure
      symptom). [S14]
- [ ] D4. `.env.example`: remove the budget block and rename "Cache and budget" to "Cache"; the TLS block states key
      permissions are the operator's responsibility. [S14]
- [ ] D5. `CHANGELOG.md` `[Unreleased]`: `### Removed` and `### Changed` entries per SPEC §6.1. [S14]
- [ ] D6. `docs/SPEC.md` header pointer: extend it to name R23 and the in-place inventory lines (93, 147, 298, 405, 455).
      `docs/specs/tls-oauth-redirect/SPEC.md` header pointer: extend it to name T4 mode, T5, T8, T-D7 mode, T-D9, T-D10 and the §6 design rows (about lines 76 and 91).
      No requirement text is rewritten. [S14]
- [ ] D7. Apply the 16 `CLAUDE.md` edits in SPEC §13 with the code. [S13, S14]

## E. Dev gate and hang guard (S11–S13)

- [ ] E1. `pyproject.toml`: add `"pytest-timeout>=2.3"` to the dev extra; add `timeout = 30` and `timeout_method = "signal"`
      under `[tool.pytest.ini_options]`. [S11, S12]
- [ ] E2. `make lock VENV=~/.venvs/twitch-mcp` (uv). Never hand-edit `requirements*.txt`. [S11]
- [ ] E3. Diff the regenerated `requirements.txt` against the committed file: no pinned runtime version may change. If one
      does, stop with a `SPEC-GAP:` line. [S11]
- [ ] E4. `make install` so `pytest-timeout` is present in the venv. [S11]
- [ ] E5. Confirm no existing test runs longer than 30 s under `make check`. If one does, stop with a `SPEC-GAP:` line; do
      not raise the value. [S12]

## F. Gate and report (S15)

- [ ] F1. `make check VENV=~/.venvs/twitch-mcp`: ruff lint and format, `mypy --strict`, pytest with branch coverage at or
      above 90%. All green. [S15]
- [ ] F2. Confirm the tool-name and order tests in `tests/test_server.py` pass unmodified. [S10]
- [ ] F3. Implementation report at `docs/reports/simplification/implementation-report.md` (traceability S1–S15, deviations,
      commands with results). Do not commit.
