# Workflow: implement / review loop for the player stats feature

Runs only after the human sets `SPEC.md` to **Approved** (explicit "Approve" in chat). Process authority: `../../../../AGENTS.md`.

## Roles

| Role | Agent | Model | Edits code | Writes |
|---|---|---|---|---|
| Orchestrator | Claude Code main session | session model | no (coordinates, runs the final gate, writes the summary) | `summary-report.md` |
| Implementer | subagent | **Haiku 5.5** (`haiku`) | yes | `implementation-report.md`, `fix-round-N.md` |
| Reviewer | separate fresh subagent each round | **Sonnet 5.5** (`sonnet`) | **no (read-only)** | `review-round-N.md` |

The implementer also fixes the valid issues the reviewer raises. The orchestrator never edits feature code itself.

## Loop (at most 5 review rounds)

1. Orchestrator spawns the implementer with the prompt below. It implements T0-T10 and writes `implementation-report.md`.
2. Orchestrator spawns a fresh reviewer (round 1). It writes `review-round-1.md` with issues classified blocker / major / minor
   and a verdict APPROVED or CHANGES REQUESTED.
3. If CHANGES REQUESTED: the implementer (resumed with its context via SendMessage, or a new one given the reports) fixes
   **all blockers and majors**; each minor is fixed or explicitly accepted with a written justification; each finding judged invalid is
   rebutted with evidence. It records everything in `fix-round-N.md` and re-runs the gate.
4. A fresh reviewer does round N+1, checking the fixes and regressions. Repeat until APPROVED, **max 5 rounds**.
5. If round 5 is not APPROVED, the orchestrator stops and asks the human (more rounds, amend spec, accept known limitations, abandon).
6. On APPROVED the orchestrator re-runs the gate itself and writes `summary-report.md`, then stops. **No commit, push, tag or release
   unless the human asks.**

Escape hatch: if the implementer finds the spec wrong, incomplete or impossible, it stops and reports. The orchestrator returns to the
human for a spec amendment and re-approval; nobody improvises.

## Branch and environment

Work happens on branch `feature/player-stats-tools`. Run commands in WSL with the venv outside the Windows drive, as in the server
`CLAUDE.md` (`VENV=~/.venvs/sports-mcp`). Never read out, print, log or commit `.env` values. Live ESPN is used only for fixture capture
(T0) and the optional smoke check (AC5); tests stay offline.

## Implementer prompt (template)

> You are the implementer for an approved spec. Read, in order: `/mnt/c/Users/hoyen/Documents/Roo Code Workspace/mcp-servers/AGENTS.md`
> (section 6), the root and server `CLAUDE.md`, then `sports-mcp-server/docs/specs/player-stats/SPEC.md`, `upstream-notes.md`,
> `tasks.md`. Implement exactly what the spec requires and nothing else: no extra features, refactors or dependency changes.
> Follow tasks T0-T10. Capture real fixtures (T0) before coding against assumed shapes and update `upstream-notes.md` with what you
> find. If the spec is wrong, incomplete or impossible, stop and report instead of improvising. Write or update tests for every
> requirement and run the server gate (`make check VENV=...`; `make audit` only if dependencies change). Complete the traceability
> table in `docs/specs/player-stats/implementation-report.md`, listing deviations (should be none) and every command run with its
> result. Never print `.env` values. Do not commit.

## Reviewer prompt (template)

> You are an independent, read-only reviewer with fresh context. Do not edit any file except your report. Read `AGENTS.md` section 7,
> the server `CLAUDE.md`, and `docs/specs/player-stats/SPEC.md`. Verify by reading the code and running the tests and gate yourself;
> do not trust the implementer's report. Check: every requirement R1-R18 and AC1-AC6 is implemented and tested; nothing outside the spec
> was added; the server invariants and section 7 of the spec hold (strict patterns, host allow-list, no logging of queries, byte-bounded
> cache, exactly eleven tools); the numbers are right (totals equal the arithmetic sum of listed rows, turnovers exclude defensive
> interceptions, zero-stat games counted, size budgets asserted); docs (`README.md`, `CHANGELOG.md`, `.env.example`, `CLAUDE.md`,
> `docs/SPEC.md` pointer) match. Write `docs/specs/player-stats/review-round-N.md` with each issue classified blocker / major / minor,
> a file:line for each, and a verdict: APPROVED or CHANGES REQUESTED.

## Artifacts (all in `docs/specs/player-stats/`)

`SPEC.md`, `upstream-notes.md`, `tasks.md`, `workflow.md`, `implementation-report.md`, `review-round-N.md`, `fix-round-N.md`,
`summary-report.md`.
