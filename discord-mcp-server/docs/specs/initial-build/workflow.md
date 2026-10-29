# Workflow: implement / review loop for the discord-mcp-server initial build

Runs only after the human sets `../../SPEC.md` to **Approved** (explicit "Approve" in chat). Process authority: `../../../../AGENTS.md`.

## Roles

| Role | Agent | Model | Edits code | Writes |
|---|---|---|---|---|
| Orchestrator | Claude Code main session | session model | no (coordinates, creates the branch, runs the final gate, writes the summary) | `summary-report.md` |
| Implementer | one subagent for the whole build | **Haiku 5.5** (`haiku`) | yes | `implementation-report.md`, `fix-round-N.md` |
| Reviewer | one subagent for the whole build, read-only | **Sonnet 5.5** (`sonnet`) | **no** | `review-round-N.md` |

The **same** implementer and the **same** reviewer are reused for every round and for any spec-amendment restart (resumed with
SendMessage), as the human requested. The reviewer is distinct from the implementer and re-reads the code and spec from disk every
round. The orchestrator never edits feature code.

## Loop (at most 5 review rounds)

1. Orchestrator syncs local `main` with `origin/main` (fast-forward only), then creates `feature/discord-mcp-server` from it.
2. Orchestrator sends the implementer prompt below. It implements T0-T13 and writes `implementation-report.md`.
3. Reviewer round 1 writes `review-round-1.md`: issues classified blocker / major / minor, each with `file:line`, verdict APPROVED or
   CHANGES REQUESTED.
4. If CHANGES REQUESTED: the implementer evaluates every finding, fixes **all legitimate blockers and majors**, fixes or explicitly
   accepts each minor with a written justification, and rebuts any finding it judges invalid with evidence. It records everything in
   `fix-round-N.md` and re-runs the gate.
5. Reviewer round N+1 checks the fixes, the rebuttals and for regressions. Repeat until APPROVED, **max 5 rounds**.
6. If round 5 is not APPROVED, the orchestrator stops and asks the human (more rounds, amend spec, accept known limitations, abandon).
7. On APPROVED the orchestrator re-runs the gate itself and writes `summary-report.md`, then stops. **No commit, push, tag or release
   unless the human asks.**

**Spec issues (either subagent):** if the implementer or reviewer finds the spec wrong, incomplete, contradictory or impossible, it stops
and reports. The orchestrator takes the issue to the human, amends `SPEC.md` (recorded as an amendment), gets explicit re-approval, and
restarts the cycle from step 2 for the affected parts, reusing the same two subagents. Nobody improvises around the spec.

## Environment

Run in WSL with the venv outside the Windows drive: `uv venv ~/.venvs/discord-mcp --python 3.12`, then
`make install VENV=~/.venvs/discord-mcp PYTHON=python3`, `make check VENV=~/.venvs/discord-mcp`. Never read out, print, log or commit
`.env` values (there should be no `.env` in the repo; tests never need one). Tests stay offline; no real Discord calls in this loop.

## Implementer prompt (template)

> You are the implementer for an approved spec. Read, in order: `/mnt/c/Users/hoyen/Documents/Roo Code Workspace/mcp-servers/AGENTS.md`
> (section 6), the root `CLAUDE.md`, `sports-mcp-server/CLAUDE.md` (the sibling you copy patterns from), then
> `discord-mcp-server/docs/SPEC.md` and `docs/specs/initial-build/tasks.md`. Implement exactly what the spec requires and nothing else:
> no extra features, tools or refactors of sibling servers. Follow T0-T13. If the spec is wrong, incomplete or impossible, stop and report
> instead of improvising. Write tests for every requirement and run the gate (`make check`, `make audit`). Complete the traceability table
> in `docs/specs/initial-build/implementation-report.md` (one row per R1-R42 and AC1-AC4), list deviations (should be none), record the
> mcp SDK's supported protocol versions (D16), and list every command run with its result. Never print `.env` values. Do not commit.

## Reviewer prompt (template)

> You are an independent, read-only reviewer. Do not edit any file except your report. Read `AGENTS.md` section 7, the root `CLAUDE.md`,
> `discord-mcp-server/docs/SPEC.md`, then the code. Verify by reading the code and running the gate yourself; do not trust the
> implementer's report. Check: every requirement R1-R42 and AC1-AC4 is implemented and tested; nothing outside the spec was added; the
> invariants of SPEC section 11 hold (allowlist guard on every tool, only `discord_client.py` imports httpx, host pinning, no tokens /
> URLs / content in logs or errors, no state keyed to connections, exact tool set and order); the rate-limit rules of section 9 / R20-R24
> hold exactly (no hard-coded per-route limits, full `retry_after` honoured with no cap, global pause, shared scope not counted, 401
> hard-stop); empty-vs-forbidden and 202/110000 handling; errors are `isError` results with the R30 shape; docs match. Write
> `docs/specs/initial-build/review-round-N.md` with each issue classified blocker / major / minor, a `file:line` for each, and a verdict
> APPROVED or CHANGES REQUESTED. If you find a problem in the spec itself, report it as a spec issue rather than a code issue.

## Artifacts (all in `docs/specs/initial-build/`)

`tasks.md`, `workflow.md`, `implementation-report.md`, `review-round-N.md`, `fix-round-N.md`, `summary-report.md`
(the spec itself is `../../SPEC.md`).
