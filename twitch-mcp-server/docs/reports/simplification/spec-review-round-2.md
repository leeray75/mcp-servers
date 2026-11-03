# Spec review, round 2: docs/specs/simplification (SPEC.md, tasks.md, workflow.md, two header pointers)

Reviewer: read-only (grep/read only). Everything below was re-read from disk, not taken from spec-fix-round-1.md.
**Verdict: APPROVED** (0 blockers, 0 majors, 5 minors). The minors are cheap to fix at the approval gate or by the implementer.

## Round-1 findings, re-verified

| Finding | Status | Evidence |
|---|---|---|
| B1 R57 sweep fails (R23 uncited) | Resolved | S9b adds `SUPERSEDED_REQUIREMENTS={"23"}` to test_gate.py and subtracts it in `test_every_specification_requirement_is_named_by_a_test` (test_gate.py:84-101 builds `missing` from `_spec_requirements() - cited - SELF_CITING`, so one more subtraction works). Listed in 6.1 and tasks C1. |
| B2 traceability row for R23 cites deleted tests | Resolved | S9b skips superseded labels in `test_every_traceability_row_cites_a_real_test_of_its_own_file` (test_gate.py:116-145). The row-count assertion still sees the R23 row, so it stays satisfied. initial-build report row 75 stays unedited, which fits decision 6. |
| B3 T-map vs T5/T8 still in TLS spec | Resolved | S9c: `required` = declared minus `{"5","8"}`, expected T1-T4,T6,T7,T9-T12; leftover T5/T8 citations are orphans and fail the test (test_tls.py:936-961 logic). See minor 2 for citations the spec does not list. |
| M1 unachievable grep | Resolved | A-S2 now greps exact identifiers; unrelated "budget" uses (cache.py:120, twitch_client.py:528, ratelimit.py:72, test_write_safety) are explicitly untouched. I confirmed none of the grepped identifiers appear in those files. |
| M2 test_tls.py edits under-listed | Resolved | 6.1 now lists the imports (test_tls.py:52,53,55 verified), `_write_pair` default (72) and chmod (108), line 480, docstrings 75-76/191/200, the T2 docstring at 613 and the T9 test's `400` assertion (~675). |
| M3 docs inventory | Resolved | README 117/148/391/394/156-157/370, docker-compose.tls.yml:11-12 (verified text), service_fakes lines, docs/SPEC.md:455 are all in 6.1/S14. |
| Minors 1-6 | Resolved | Line refs corrected (service_fakes), speculative make-cert row dropped, header says "R23 only", pyproject check folded into the existing test_gate test (no new test), `timeout_method="signal"` justified in 7, redirect rule moved to a grep acceptance (A-S8) and the service.py docstring renumbering added. |

## Fidelity to the seven decisions
All encoded; nothing dropped. Additions beyond the decisions are small and defensible: `timeout_method = "signal"` (decision 5 said only a 30 s default; pytest-timeout defaults to signal on Linux anyway, so this pins it; flag for the human), the `SUPERSEDED_*` sets (a consequence of decision 6), and the docker-compose.tls.yml/README wording edits. No new machinery. S-D4a is still presented for human confirmation. Open questions is empty; Status is Draft; all AGENTS.md section 4 sections present.

## Decision 6 and the "in-place lines covered by the pointer" approach (SPEC 12)
Consistent. Decision 6 says existing requirement text is not rewritten; an amendment pointer naming the superseded lines is the least invasive form and matches how Amendments 1 and 2 are recorded. The cost is that docs/SPEC.md:93, 147, 169, 178, 298, 405, 455 and the TLS spec keep stating removed behaviour as current text, so S14's "no live document describes it as current" is true only if a pointed-at line counts as annotated (6.1 says "annotated"; S14 says "updated or annotated"). Acceptable, and 12 asks the human to confirm that reading at the gate. Suggest S14/A-S7 say "outside the lines named by the Amendment 3 pointers" to remove the apparent contradiction.

## Minor issues
1. **Wrong line in the docs/SPEC.md pointer and SPEC 6.1/12.** The "line 92" inventory entry (docs/SPEC.md:8 pointer, simplification SPEC 6.1 and 12) is "No Twitch-side app registration automation"; the line that says "Caches, budgets and confirmation tokens ... " is docs/SPEC.md:93. The section heading at 169 ("Rate limiting and budget") is not named. Fix: change 92 to 93 in the three places; optionally add 169.
2. **Unlisted T5/T8 citations in retained test_tls.py text.** Orphan check scans the whole file with `\bT(\d+)\b`; besides the lines the spec lists, T5/T8 also appear at test_tls.py:523 (section header "T5"), 557 (section header "T6, T8"; rename to "T6") and 809 (retained compose test docstring "the exact failure T8 and T-D9"). Fix: add these three to the 6.1 test_tls.py row.
3. **Wrong location for `EXPECTED_TLS_REQUIREMENTS`.** 6.1 says "(line 1)"; it is at test_tls.py:936.
4. **TLS spec's own in-place design lines are not named.** The TLS spec pointer names T items only; TLS SPEC section 6 rows (around lines 76 and 91, "beside the key (T4/T-D10)", `_filesystem_carries_unix...`) also describe removed behaviour. Add "and the section 6 design rows that mention them" to the pointer and 12.
5. **`timeout_method="signal"` is an addition to decision 5.** Harmless, but call it out in the approval summary so the human can accept or strike it; also note `signal` does not exist on Windows hosts (the gate runs in WSL, as stated).

## Consistency check
SPEC.md, tasks.md (A-E, B10/B12, C1) and workflow.md agree on S1-S15, `make check` plus reading only, the 30 s timeout with signal method, and the 5-round loop. The 13 CLAUDE.md edit list matches the removals and leaves the tool set, R16, R27, R28-R34, loopback, one-httpx-module, network-free tests and the 90% gate untouched. Security trade-offs are stated honestly (7 table plus residual-risk notes).
