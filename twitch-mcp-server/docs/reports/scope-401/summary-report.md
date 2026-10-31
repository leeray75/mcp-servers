# Summary Report — Scope-aware 401 and per-token circuits (Amendment 7)

**Spec:** [`docs/specs/scope-401/SPEC.md`](../../specs/scope-401/SPEC.md), C1–C10 — Approved (human, 2026-10-31);
C-D3, C-D5, C-D9, C-D10 confirmed; write scope-401 → `permission` (human decision replacing C-D4); in-flight
old-token 401 race accepted as a known limitation.

**Problem (incident reported by the Goose agent, verified):** a user token holding only `user:read:email` called
`get_banned_users`; Helix answered 401 for a missing scope and the single process-wide circuit bricked every tool,
including app reads and `twitch_account_status`, until restart. Causes: the circuit tripped before the scope check, and
the scope check never read Twitch's `message` field, where Twitch puts "Missing scope: …". A third defect found while
specifying: the user token was validated through the app rate bucket.

**Operational fix done first (no code):** `.env` `TWITCH_OAUTH_SCOPES` replaced with the verified read-only list; the
operator re-signed in and all 8 scopes were granted; `get_banned_users`/`get_moderators` succeeded.

| Step | Actor | Output | Result |
| --- | --- | --- | --- |
| Verify incident + scope names | orchestrator | chat | Confirmed both defects; Goose's scope list partly wrong |
| Decisions | human | chat | Both fixes (scope-aware 401 + per-class circuit); docs in this amendment |
| Spec draft + 3 fix rounds | author (Haiku) | SPEC.md, tasks.md, `spec-fix-round-{1,2,3}.md` | — |
| Spec review rounds 1–4 | reviewer (Sonnet) | `spec-review-round-{1..4}.md` | Round 1 found a sign-in deadlock (blocker) → circuits made Helix-only; APPROVED in round 4 of 5 |
| Approval gate | human | spec header | Approve; derived decisions confirmed |
| Implement | implementer (Haiku) | code, tests, docs, `implementation-report.md` | Gate green; 6 failing-first tests failed for the stated reason |
| Implementation review | reviewer (Sonnet) | `review-round-1.md` | **APPROVED**, 3 minors |

**Built:** missing-scope 401/403 → `kind=permission` with Twitch's own scope wording, no circuit (reads and writes);
`message` read only for that recognition (D10 unchanged — no scope table); separate app and user circuits on Helix
calls only; token, validate, revoke and mint never gated; user circuit cleared on a new sign-in/refresh or forget, app
circuit only by restart; `/healthz` degrades on the app circuit only; non-scope write 401 keeps `unknown_outcome` and
the R16 "Do not re-issue" hint; user validate uses the user bucket; README and `.env.example` document per-tool scopes
with a copy-paste line.

**Accepted minors (review round 1):** M1 an empty `scopes` list renders "This token holds: ." — cosmetic, Twitch's
observed error bodies carry no `scopes` list; M2 scope names from a `scopes` list are not control-character sanitised —
they only reach the tool hint, never logs; M3 a stale `_permission_error` docstring. None affects behaviour; candidates
for a future tidy-up.

**Verification:** `make check` 1024 passed, 91.55% branch coverage. Live after rebuild: `/healthz` ok with 25 tools;
`twitch_account_status` `user_connected: true`; `get_banned_users` (user token) and `get_users` (app token) succeed; no
circuit lines in the log. A live missing-scope 401 was not re-provoked (the token now holds every needed scope); that
path is covered by tests N1–N3. Nothing committed.
