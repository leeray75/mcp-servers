# Summary Report — OAuth callback `iss` (Amendment 4)

**Spec:** [`docs/specs/oauth-iss/SPEC.md`](../../specs/oauth-iss/SPEC.md), I1–I6 — Approved (human, 2026-10-31), I-D2 confirmed.

**Problem:** Twitch's authorization-code redirect carries only `code`, `scope` and `state`; the callback refused any
request without `iss`, so live sign-in could never succeed. Tests passed because the fakes always sent `iss`.

| Step | Actor | Output | Result |
| --- | --- | --- | --- |
| Diagnose live failure | orchestrator | chat | `claim()` refused absent `iss` (confirmed against Twitch docs) |
| Spec draft + 3 fix rounds | author (Haiku) | SPEC.md, tasks.md, `spec-fix-round-{1,2,3}.md` | — |
| Spec review rounds 1–4 | reviewer (Sonnet) | `spec-review-round-{1..4}.md` | APPROVED in round 4 of 5 |
| Approval gate | human | spec header | Approve; I-D2 confirmed |
| Implement | implementer (Haiku) | code, tests, `implementation-report.md` | Gate green, new tests failed before the fix |
| Implementation review | reviewer (Sonnet) | `review-round-1.md` | APPROVED (2 cosmetic minors, accepted) |

**Built:** `PendingStore.claim` checks `iss` only when present (exact, constant-time); empty `iss` refused; `state`,
single-use, 10-minute TTL and the code requirement unchanged. Fakes omit `iss` like real Twitch.
**Verification:** `make check` 975 passed, 90.84%. Live: the callback passed the `iss` stage and exposed the next
wire mismatch (see Amendment 5). Nothing committed.
