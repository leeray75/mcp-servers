# Summary Report — Simplification (Amendment 3)

**Spec:** [`docs/specs/simplification/SPEC.md`](../../specs/simplification/SPEC.md), S1–S15 — Status: Approved (human, 2026-10-30), amended by S-A1 (human, 2026-10-30)
**Amends:** `docs/SPEC.md` (R23) and `docs/specs/tls-oauth-redirect/SPEC.md` (T4 mode clause, T5, T8, T-D7 mode clause, T-D9, T-D10)
**Date:** 2026-10-30

## 1. Bottom line

**APPROVED in review round 1 of 5. Gate green.** The per-minute tool budgets, the TLS key-mode probe, the repo-tree
check and the plaintext-refusal middleware are gone; all 16 write tools and their safety model, the header-driven rate
limiter and the opt-in TLS termination are unchanged. A 30-second per-test timeout now stops a hanging test from
stalling the gate, and the review process is limited to `make check` plus reading.

## 2. Process

| # | Step | Actor | Output | Result |
| --- | --- | --- | --- | --- |
| 1 | Diagnose the stalled Goose reviewer and the over-engineering | orchestrator | chat | Stall = hand-built mutation harness, mutant S-09 hung an unauthenticated `GET /mcp` stream with no test timeout |
| 2 | Decisions put to the human (choice format) | orchestrator → human | chat | S-D1..S-D7: keep TLS but slim it, keep all 16 writes, one header-driven limiter, no mutation harnesses + pytest-timeout, new spec folder, CLAUDE.md edits with the code |
| 3 | Spec drafted | author (Haiku) | `docs/specs/simplification/{SPEC,tasks,workflow}.md`, pointer lines | Draft |
| 4 | Spec review round 1 | reviewer (Sonnet) | `spec-review-round-1.md` | CHANGES REQUESTED — 3 blockers (meta-tests), 3 majors, 6 minors |
| 5 | Spec fix round 1 | author (Haiku) | `spec-fix-round-1.md` | All fixed |
| 6 | Spec review round 2 | reviewer (Sonnet) | `spec-review-round-2.md` | APPROVED, 5 minors |
| 7 | Spec fix round 2 | author (Haiku) | `spec-fix-round-2.md` | Minors fixed |
| 8 | **Approval gate** | human | spec header | **Approve**; S-D4a cache scope, `timeout_method = "signal"`, pointer-only trail confirmed |
| 9 | Implement | implementer (Haiku) | code, tests, docs, `implementation-report.md` | Gate green; one SPEC-GAP raised |
| 10 | SPEC-GAP (initial-build report R43 row cited a deleted test) | human | SPEC §12 S-A1 | Accepted |
| 11 | Implementation review round 1 | reviewer (Sonnet) | `review-round-1.md` | **APPROVED**, 0 blockers, 0 majors, 2 minors |
| 12 | Minor 1 (stale `__pycache__`, incl. old budget/mutation `.pyc`) | orchestrator | — | Deleted (git-ignored, regenerated on demand) |

Review rounds used: spec 2 of 5, implementation **1 of 5**.

## 3. What was built

| Req | Result |
| --- | --- |
| S1–S2 | `budget.py`, `tests/test_budget.py`, `ToolBudget` plumbing and the `TOOL_CALLS_PER_MINUTE*` settings removed; stray env values are ignored (`extra="ignore"`) |
| S3–S4 | Ratelimit-* header limiter (R19–R22) unchanged; `cache.py` kept for identity verification, metadata and the R26 owner lookup |
| S5–S6 | TLS key-mode probe/refusal and repo-tree check removed; a `0644` key or an in-repo path is accepted |
| S7 | `PlaintextRefusalMiddleware` removed; the only 3xx in the app is the `/oauth/start` 302 |
| S8 | Retained TLS behaviour (T1–T4 minus mode, T6, T7, T9–T12, T-D8) keeps its tests |
| S9 | Obsolete tests removed; `SUPERSEDED_REQUIREMENTS = {"23"}` and `SUPERSEDED_TLS_REQUIREMENTS = {5, 8}` keep the traceability meta-tests honest; S-A1 citation fix |
| S10 | Tool names/order, 16 writes, R16, R27, R28–R34 unchanged |
| S11–S12 | `pytest-timeout` 2.4.0 via `make lock`; `timeout = 30`, `timeout_method = "signal"` |
| S13 | Review = `make check` + reading, recorded in `workflow.md` and `CLAUDE.md` |
| S14 | README, `.env.example`, CHANGELOG `[Unreleased]`, `docker-compose.tls.yml`, `make-cert.sh`, CLAUDE.md §13 edits |
| S15 | Gate green |

## 4. Verification (reviewer-run)

```
make check   ruff + format clean, mypy --strict clean, 969 passed in 63 s, 90.71% branch coverage (gate 90%)
make audit   no known vulnerabilities
```
Before the change: 990 tests, 90.96%. The net −21 is the deleted budget and TLS-probe tests minus the new S2/S5/S6/S7 tests.

## 5. Deviations and accepted minors

- S-A1: one stale citation removed from the historical `docs/reports/initial-build/implementation-report.md` (human-accepted).
- Extra README lines corrected under S14 (rate-limit sentence, plaintext-400 troubleshooting row, process-state line) — reviewer judged them in scope.
- Minor 2 accepted: the S6 test writes a `.s6-scratch-<pid>` folder under the repo root and removes it in `finally`.

## 6. Open items for the human

- Live TLS sign-in (make-cert → trust CA → register `https://127.0.0.1:8770/oauth/callback` → `connect_twitch_account`) is still operator-run and unverified, as recorded in the TLS summary report.
- Optional: try registering `http://localhost:8770/oauth/callback`; Twitch exempts the literal `localhost` from the HTTPS rule, which could make TLS unnecessary.
- Leftover scratch from the stopped Goose reviewer: `%TEMP%\zz_r2_*.py` and WSL `/tmp/rr2_*`, `/tmp/zz_r2_*` — safe to delete.
- Nothing is committed; `twitch-mcp-server/` is still untracked.
