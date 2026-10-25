# Review round 1: agent-auth

Reviewer: separate read-only pass. Spec: `docs/specs/agent-auth/SPEC.md` (Approved). Date: 2026-10-25.

## Summary

The implementation is complete, well tested and respects the security invariants. One real behavioural defect
(stale `rejected` state, see M1) should be fixed before approval; the rest are minor. Deviations D1, D2, D4, D6 are
reasonable and need spec amendments (below), not code changes.

## Gate (run by reviewer, WSL venv `~/.venvs/gmail-mcp`)

`make check`: ruff lint/format OK, mypy OK, **366 passed**, coverage **99.29%** (gate 85%, branch on).
Uncovered: `config.py:36`, `parsing.py:102,112`, `server.py:283,293,310` (pre-existing tool return lines).
No dependency files changed (`git status` shows no `requirements*`/`pyproject` diff), so `make audit` is unaffected.

## Per-requirement verdicts

| Req | Verdict | Notes |
|---|---|---|
| R1.1-R1.2, R1.4-R1.6 | Pass | `auth_state.py`: frozen model, metadata-only via `read_token`, no network, no secrets, age warning at >= 6, OS commands. |
| R1.3 | Pass with defect | Thread-safe record under a dedicated lock, cleared by `reset()`, precedence matches. But see M1. |
| R2 | Pass | Seventh tool last, correct annotations, title, outputSchema, text rendering, instrumented, never raises auth error. |
| R3 | Pass except `lastModified` (D1) | Four static URIs, metadata, guide contents, bearer-guarded. |
| R4 | Pass (D2, D3) | Shape matches; length well under 700. |
| R5 | Pass (D4) | Gate tests present; elicitation only when `elicitation.url` declared; plain `/auth` URL; kill switch; unknown revision falls back. |
| R6 | Pass | Success copy changed; pages remain static. |
| R7 | Pass | README, CHANGELOG, `.env.example`, CLAUDE.md, docs/SPEC.md updated. |

## Invariants / security checks

- Seven tools, order fixed, no send/delete/modify capability added. OK.
- No new routes; only `/healthz`, `/auth`, `/oauth2callback` unauthenticated. `create_app` unchanged except resources on `/mcp`. OK.
- `auth_state.py` and `resources.py` do no network I/O; output holds only booleans, scope names, ages, timestamps and
  server-authored text. Bearer and client secret never referenced. OK.
- Elicitation URL is `auth_url(host_port)`, no query/state; no undeclared mode sent (`server.py:124-139`). Stateless
  preserved (uuid per response, nothing stored). OK.
- Sign-in pages static; only one copy string changed (`signin.py`). OK.
- Docs: grep finds no stale `docs/reauth`, `docs/reports/reauth` or `SPEC-agent-auth` paths. OK.
- Scope creep: none found. `ctx` added to every tool is an implementation necessity (kept out of input schemas; tests pass).

## Issues

### M1 (major): `rejected` state is never cleared by a successful call or an out-of-process sign-in
- Evidence: `gmail_client.py:_execute` (about lines 107-125) records rejection but nothing clears it on success;
  `reset()` is only called from `signin.py` (server-hosted `/auth` callback). `auth_cli.py` / `scripts/auth.*` run in
  a separate process and never reach the server's `GmailClient`.
- Impact: a user who re-authenticates with the CLI script (still documented in the README) gets working tools, yet
  `auth_status` and `gmail://auth/status` keep reporting `rejected` with `signed_in=false` and "ask the user to
  sign in" until the container restarts. An agent obeying `next_step` would loop the user through sign-in. This breaks
  the intent of R1.3 ("since the last successful sign-in").
- Fix: in `compute_auth_status`, ignore a rejection older than the token file's mtime (treat as stale, state from
  `base`); and/or clear `_rejected_at` after any successful `operation(...)` in `_execute`. Add tests for both.

### m1 (minor): race can re-record a rejection after `reset()`
- `_execute` holds `_lock`, `reset()` clears `_service` under `_lock` first, then `_rejected_at` under `_state_lock`;
  an in-flight call that fails with the old credentials could record a rejection after the clear. Window is tiny and
  the mtime comparison from M1's fix also covers it.

### m2 (minor): `token_age_days` measures file mtime, not sign-in time
- `auth_state.py:_token_age_days`. Token rewrites that are not sign-ins would reset the warning. Acceptable, matches
  spec R1.1; note it in docs if desired.

### m3 (minor): D5 console labels unverified
- README publish steps ("Audience", "Publish app") were written from memory; the human should confirm on first use.

## Deviation assessments

- **D1 (`lastModified` omitted): reasonable.** The SDK fixes annotations at registration. Recommend amending R3.2 to
  drop `lastModified` (token age is already in the payload). Hides no defect.
- **D2 (URL "exactly once"): reasonable.** Recommend rewording R4.2 to "exactly one bare URL token; the URL may also
  appear inside the quoted OS commands from the R4.1 template".
- **D3 (SDK prefix `Error executing tool <name>:`):** unavoidable and harmless; add a note to R4.1 that the text
  follows the SDK prefix.
- **D4 (2025-11-25 branch unreachable): reasonable and safe**, since R5.4 forces the fallback. Recommend amending
  R5.2/R5.6 to state that stateless mode only supports the 2026-07-28 path and that the 2025 branch is retained
  only as dormant code (or remove it to avoid dead code; either is acceptable). No hidden defect.
- **D6 (two-line text rendering): reasonable.** Matches "concise". No spec change needed beyond a clarifying note.

## Verdict

Blockers: 0. Majors: 1 (M1). Minors: 3.

VERDICT: CHANGES REQUESTED
