# Summary Report — Token volume (Amendment 6)

**Spec:** [`docs/specs/token-volume/SPEC.md`](../../specs/token-volume/SPEC.md), V1–V9 — Approved (human, 2026-10-31);
V-D2..V-D5 confirmed.

**Problem:** with the repo on the Windows drive, the `./data` bind mount made container-created files owned by UID
1000, so `chmod 0600` by UID 10001 failed and the user token could not be saved.

| Step | Actor | Output | Result |
| --- | --- | --- | --- |
| Diagnose | orchestrator | chat | `PermissionError` on `chmod` reproduced in the container |
| Spec draft + 1 fix round (tests deliberately kept minimal) | author (Haiku) | SPEC.md, tasks.md, fix report | — |
| Spec review rounds 1–2 | reviewer (Sonnet) | `spec-review-round-{1,2}.md` | APPROVED in round 2 of 5 |
| Approval gate | human | spec header | Approve; V-D2..V-D5 confirmed |
| Implement | implementer (Haiku) | compose, scripts, docs, tests, `implementation-report.md` | Gate green; new tests failed first |
| Implementation review | reviewer (Sonnet) | `review-round-1.md` | APPROVED (1 style minor, accepted) |
| V9 live check | orchestrator + human | — | Passed (below) |

**Built:** `docker-compose.oauth.yml` mounts the named volume `twitch-mcp-data` at `/data` (no host folder); docs,
scripts and CLAUDE.md updated; `data/.gitkeep` removed, `data/` stays ignored.
**SPEC-GAP (no-op):** `docs/SPEC.md` §6.1 has no mount wording, so it was left unchanged — reviewer confirmed; pending
the human's acceptance.

**Verification:** `make check` 1006 passed, 91.36%. **V9 live (2026-10-31):** `/data` is the named volume owned by
`10001:10001`; browser sign-in → callback `connected` (200); `/data/twitch_user_token.json` is `10001:10001 600`;
after `scripts/restart.sh` the token persisted, `/healthz` reports `tool_count: 25`, and `twitch_account_status`
reports `user_connected: true`. Nothing committed.
