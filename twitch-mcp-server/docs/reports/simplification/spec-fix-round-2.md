# Spec fix round 2: docs/specs/simplification (response to spec-review-round-2.md, verdict APPROVED with 5 minors)

Scope: Markdown only. No code, test or config touched; nothing run; nothing committed; no `.env` read. Only the minors
below were changed.

| Minor | Disposition | Where |
|---|---|---|
| 1. Line 92 should be 93 (docs/SPEC.md §2 "Caches, budgets and confirmation tokens" line) | **Fixed** in the three places: `docs/SPEC.md` header pointer (line 8), SPEC §12, tasks D6. The optional §5.4 heading (line 169) is not added, as the change was limited to the reviewer's minimum. | `docs/SPEC.md:8`; `specs/simplification/SPEC.md` §12; `tasks.md` D6 |
| 2. Unlisted T5/T8 citations in retained `test_tls.py` (lines 523, 557, 809) | **Fixed.** Added to the §6.1 `tests/test_tls.py` row: section headers at 523 and 557 (rename the 557 header "T6, T8" to "T6") and the compose-test docstring at 809 ("the exact failure T8 and T-D9"). | SPEC §6.1 `test_tls.py` row |
| 3. `EXPECTED_TLS_REQUIREMENTS` is at `test_tls.py:936`, not line 1 | **Fixed** in SPEC §6.1 and in tasks B10. | SPEC §6.1; `tasks.md` B10 (and the matching tasks line) |
| 4. TLS spec pointer does not name its §6 design rows (about lines 76 and 91) | **Fixed.** Pointer in `docs/specs/tls-oauth-redirect/SPEC.md` header now names those rows. Also reflected in SPEC §6.1 (TLS row), SPEC §12 and tasks D6. | `docs/specs/tls-oauth-redirect/SPEC.md` header; SPEC §6.1, §12; `tasks.md` D6 |
| 5. `timeout_method = "signal"` is an addition to S-D5 | **Noted, not changed in behaviour.** SPEC §7 now states that `signal` applies on Linux/WSL where the gate runs, does not exist on Windows, and is flagged for the human to accept or strike at the approval gate. | SPEC §7 |

Also adopted as the reviewer suggested: S14 and A-S7 now say "outside the lines named by the Amendment 3 pointers" (for S14, "…as current, outside the lines named by the Amendment 3 pointers."; for A-S7, "…as current outside the lines named by the pointers").

Nothing else was changed.
