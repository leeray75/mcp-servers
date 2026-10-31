# Spec fix round 1: docs/specs/simplification (response to spec-review-round-1.md)

Scope: Markdown only. Files edited: `docs/specs/simplification/SPEC.md`, `tasks.md`, `workflow.md`; header pointers in
`docs/SPEC.md` and `docs/specs/tls-oauth-redirect/SPEC.md`. No code, test, script or config was changed; no tests were run;
nothing was committed; no `.env` value was read.

## Blockers

| ID | Disposition | Where |
|---|---|---|
| B1 (R57 sweep fails once R23 loses its tests) | **Fixed.** Mechanism: `SUPERSEDED_REQUIREMENTS = {"23"}` in `tests/test_gate.py` beside `SELF_CITING`, subtracted from the R57 sweep. Not a test-citation in the S2 docstring (that would keep a citation of a removed behaviour alive). | SPEC §5 S9b, §6.1 `test_gate.py` row; tasks C1 |
| B2 (traceability row for R23 cites deleted tests) | **Fixed.** Chosen: the traceability test skips rows whose label is in `SUPERSEDED_REQUIREMENTS`. The historical `initial-build/implementation-report.md` row 75 is not edited. | SPEC S9b; tasks C1 |
| B3 (TLS T-map cannot pass: T5 and T8 still declared in the TLS spec) | **Fixed.** `SUPERSEDED_TLS_REQUIREMENTS = {"5", "8"}` in `tests/test_tls.py`; `required` = declared minus superseded; `EXPECTED` becomes {1,2,3,4,6,7,9,10,11,12}. Remaining T5/T8 citations are orphans and must be removed, so stale citations cannot hide. T4 stays required and must still be cited by a retained test. | SPEC S9c; tasks B10 |

## Majors

| ID | Disposition | Where |
|---|---|---|
| M1 (acceptance greps unachievable) | **Fixed.** Grep narrowed to exact identifiers (`ToolBudget`, `twitch_mcp.budget`, `budget.py`, `tool_calls_per_minute`, `TOOL_CALLS_PER_MINUTE`, plus the removed mode and middleware symbols). The unrelated word "budget" (cache byte budget, 5xx retry budget, read-retry tests) is stated as untouched. | SPEC A-S2, §6.1 unrelated-files row; tasks A9 |
| M2 (test_tls.py edits under-listed) | **Fixed, and extended.** Listed: imports at lines 52, 53, 55; `_write_pair` `key_mode` parameter (72) and its chmod (108); line 480; docstrings at module lines 7–14, `_write_pair` 75–76, fixture 191, `https_client` ≈200, test at 613. **Also found:** the retained T9 test `test_no_certificate_detail_reaches_the_log_or_the_health_response` (676) asserts a plaintext `400`, so it must move to the https client and assert `200`. | SPEC §6.1 `test_tls.py` row; tasks B7–B9 |
| M3 (S14 misses live statements) | **Fixed.** Rows added for README 117, 148, 391, 394 (and 156–157, 370), `docker-compose.tls.yml` 11–12, and the `docs/SPEC.md` inventory lines 92, 147, 298, 405, 455. Live-document lines are edited; requirement text is not rewritten, and the in-place inventory lines in `docs/SPEC.md` are covered by the extended header pointer (decision stated in SPEC §12 for the human to confirm). | SPEC §6.1, §12; tasks D2–D3, D6 |

## Minors

| ID | Disposition | Where |
|---|---|---|
| m1 (6.1 line references wrong for service_fakes vs test_service) | **Fixed.** `test_service.py` row now deletes only the one test. `service_fakes.py` row lists lines 145–146, 167–168, 40, 76, 211–216, 243, 263 and the docstring word. | SPEC §6.1; tasks A5–A6 |
| m2 (speculative "make-cert assertions" row) | **Fixed.** Row removed. Task B-series verifies nothing pins the removed warning; the make-cert tests (687–757) are not expected to change. | SPEC §6.1 (row removed) |
| m3 (header "R19–R23" reads as if R19–R22 change) | **Fixed.** Header now says "R23 only; R19–R22 are unchanged". | SPEC header |
| m4 (S11 new pyproject-reading unit test is new machinery) | **Fixed.** No new test. Three assertions are added to the existing `test_pyproject_holds_the_strict_and_coverage_gates`. | SPEC S11, §8.2; tasks C2 (D5 removed) |
| m5 (signal vs thread for the async hang) | **Fixed.** `timeout_method = "signal"` stated explicitly in `pyproject.toml`, S11, S12, §7, and the CLAUDE.md edit list, with the one-line justification: SIGALRM interrupts the loop's blocking select and fails the test; `thread` would `os._exit` the whole run. The gate runs in WSL/Linux, where `signal` is available. | SPEC S11, S12, §6.1, §7; tasks E1; workflow "Hang handling" |
| m6 (S7 route-level 302 test not meaningful; service.py docstring numbering) | **Fixed.** The route-level 302 test is dropped; the redirect rule is an acceptance check by grep and reading (A-S8). The `/healthz`-over-http 200 test is kept. `service.py` docstring list renumbering added. | SPEC S7, §8.2, §8.3 A-S8, §6.1 service row; tasks A4 |

## Accepted (none)

No minor is accepted without a fix. Every item above has a change in the named file.

## Open points for the human at the approval gate

- S-D4a (cache stays, derived from keeping writes) is still a decision to confirm.
- §12: the reading of S-D6 that adopts header pointers, with the in-place inventory lines covered by the pointer and not
  edited, is new in this round. It is the one place the spec reads a rule for the human to confirm.
