# Summary Report — OAuth wire shapes (Amendment 5)

**Spec:** [`docs/specs/oauth-wire/SPEC.md`](../../specs/oauth-wire/SPEC.md), W1–W12 — Approved (human, 2026-10-31);
W-D2..W-D6 confirmed; amendment W-A1 (SPEC-GAPs G1/G2) approved.

**Problem:** live sign-in failed after the token exchange: Twitch returns `scope` as a JSON array, the server required
a string. An audit of every OAuth response against Twitch's docs found four more mismatches (validate `login`,
revoke `client_id`, revoke 400 blocking local delete, token-endpoint 400 not mapped to re-auth).

| Step | Actor | Output | Result |
| --- | --- | --- | --- |
| Diagnose | orchestrator | chat | `build_token` rejected array `scope` |
| Spec draft (with 14-row audit) + 2 fix rounds | author (Haiku) | SPEC.md, tasks.md, fix reports | — |
| Spec review rounds 1–3 (audit independently re-verified) | reviewer (Sonnet) | `spec-review-round-{1,2,3}.md` | APPROVED in round 3 of 5 |
| Approval gate | human | spec header | Approve; W-D2..W-D6 confirmed |
| Implement; SPEC-GAPs G1/G2 ruled by human (W-A1) | implementer (Haiku) | code, tests, `implementation-report.md` | Gate green |
| Implementation review | reviewer (Sonnet) | `review-round-1.md` | APPROVED, no issues |

**Built:** array/string `scope` on exchange and refresh; validate reads `login`; revoke sends `{client_id, token}`
with no headers (only R13 exception); dead-token revoke still deletes the local credential (`revoke_skipped` when no
client id); 400s mapped by status with path-keyed hints; refresh grant inside the re-auth `try` (only
`unauthenticated` clears the store). Fakes use Twitch's documented shapes.
**Verification:** `make check` 1004 passed, 91.36%. Live: exchange and validate succeeded; the token save then failed
on the host filesystem (see Amendment 6). Nothing committed.
