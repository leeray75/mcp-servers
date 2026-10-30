# Review round 1 — simplification (S1–S15)

Reviewer: read-only. Method: read the spec, ran `make check` once and `make audit` once (no mutation harness), grepped for removed symbols and stale references, read the changed config/test/doc sites.

## Gate observed
- `make check`: ruff and mypy --strict clean; **969 passed in 63.5 s** (no timeouts); total branch coverage **90.71%** (floor 90%).
- `make audit`: "No known vulnerabilities found".

## Requirement check
- S1/S2: `budget.py` absent from `src/`; no `ToolBudget`/`tool_calls_per_minute` hits in `src` or `tests` source. The only test hits are the intended S2 test (`tests/test_config.py:477-482`: env set, attributes absent).
- S3: ratelimit untouched in scope. S4: cache kept.
- S5/S6: `TLS_KEY_MODE`, probe, `_check_tls_location`, `DEFAULT_TLS_HOST_DIR` gone from src/tests. The 0644 test and the repo-tree test exist. The S6 test (`tests/test_tls.py:406-421`) uses a scratch folder named `.s6-scratch-<pid>` under the repo root and removes it in `finally`. This is acceptable: the pid makes the name unique, cleanup is guaranteed, and nothing is left behind (no leftover folder seen).
- S7: no `PlaintextRefusal`. The only 3xx in src is the R47 302 at `server.py:1773` (A-S8 satisfied).
- S8/S9: `SUPERSEDED_REQUIREMENTS = {"23"}` (`test_gate.py:29`, used at 109, 146, 161). `SUPERSEDED_TLS_REQUIREMENTS = {"5","8"}` and `EXPECTED_TLS_REQUIREMENTS` is T1–T4, T6, T7, T9–T12 (`test_tls.py:799-820`). No orphan T5/T8/R23 citations in src or tests.
- S10: tool-set tests pass unmodified, as far as the green suite shows.
- S11/S12: `pyproject.toml:26,45,46` carry `pytest-timeout>=2.3`, `timeout = 30` and `timeout_method = "signal"`. `requirements-dev.txt:1167` has hash-pinned `pytest-timeout==2.4.0` with a "via twitch-mcp-server" comment. `requirements.txt` has no timeout entry. The `test_gate.py:81-84` assertions are in the existing pyproject test.
- S13: the rule is in CLAUDE.md. The process mentions in `tasks.md` and `workflow.md` were not re-read.
- S14: README (lines 117, 148, 368, 391), `.env.example`, `docker-compose.tls.yml:11`, `scripts/make-cert.sh` (LF; 0 CR bytes, `chmod 600` kept at line 45, S5 note at line 93) and CHANGELOG `[Unreleased]` Removed/Changed all match the code. No live statement describes a probe, budget, mode refusal, location refusal or plaintext 400 as current. README line 358 says there is no local budget, which is correct.
- CLAUDE.md §13: all 16 edits are present (checked the process note, counts, timeout and review bullets, budget bullet deleted, cache, config/TLS, security, service, architecture, container and TLS invariant).
- Extra README lines (S14 scope creep): acceptable. They are doc corrections for removed behaviour and add no new claims.

## Issues
- **Blocker:** none.
- **Major:** none.
- **Minor 1:** stale untracked bytecode in the working directory contains removed symbols and leftover mutation-harness names: `src/twitch_mcp/__pycache__/budget.cpython-312.pyc`; `tests/__pycache__/test_budget*.pyc`, `test_zz_mutate*.pyc` and `test_zz_reviewer_mutate*.pyc`. These come from earlier stalled-review mutation attempts. There are no `.py` sources, so pytest does not collect them, and `__pycache__` is normally ignored. Fix: delete these `__pycache__` files (`find . -name __pycache__ -prune -exec rm -rf {} +`) before commit, so the A-S2 grep over `src tests` is clean.
- **Minor 2:** `tests/test_tls.py:414` writes into the repo root. Acceptable as is. An optional hardening would be a `tmp_path` pair if S6 were reworded; it is not needed for this round.

## Verdict
**APPROVED** — 0 blockers, 0 majors, 2 minors (hygiene only).
