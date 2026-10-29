# Workflow: implement / review loop for user OAuth mode (amendment A3)

Runs only after the human sets `SPEC.md` (this folder) to **Approved** with an explicit "Approve" in chat. Process authority:
`../../../../AGENTS.md`.

## Roles

| Role | Agent | Model | Edits code | Writes |
|---|---|---|---|---|
| Orchestrator | Claude Code main session | session model | no (coordinates, creates the branch, runs the final gate, writes the summary) | `summary-report.md` |
| Implementer | one subagent for the whole feature | **Haiku 5.5** (`haiku`) | yes | `implementation-report.md`, `fix-round-N.md` |
| Reviewer | one subagent for the whole feature, read-only | **Sonnet 5.5** (`sonnet`) | **no** | `review-round-N.md` |

The **same** implementer and the **same** reviewer are reused for every round and for any spec-amendment restart (resumed with
SendMessage). The reviewer re-reads the spec and code from disk every round. The orchestrator never edits feature code.

## Loop (at most 5 review rounds)

1. Orchestrator fast-forwards local `main` to `origin/main` and creates `feature/discord-user-oauth` from it.
2. Implementer does T0 first; if the SDK gate fails it stops and reports. Otherwise T1-T12 and `implementation-report.md`.
3. Reviewer round 1 writes `review-round-1.md`: issues classified blocker / major / minor with `file:line`, verdict APPROVED or
   CHANGES REQUESTED.
4. If CHANGES REQUESTED: the implementer fixes all legitimate blockers and majors, fixes or explicitly accepts each minor with a
   justification, rebuts invalid findings with evidence, records it in `fix-round-N.md`, and re-runs the gate.
5. Reviewer round N+1 checks fixes, rebuttals and regressions. Repeat until APPROVED, **max 5 rounds**; if round 5 is not APPROVED
   the orchestrator stops and asks the human.
6. On APPROVED the orchestrator re-runs the gate itself and writes `summary-report.md`, then stops. **No commit, push or PR unless the
   human asks.**

**Spec issues (either subagent):** stop and report. The orchestrator takes the issue to the human, records an amendment in `SPEC.md`,
gets explicit re-approval, and restarts from step 2 for the affected parts with the same two subagents.

## Environment

WSL with the venv outside the Windows drive: `make install VENV=~/.venvs/discord-mcp PYTHON=python3`, `make check
VENV=~/.venvs/discord-mcp`. Never read out, print, log or commit `.env` values; tests never need a `.env`. Tests stay offline; no real
Discord calls. Synthetic secrets in tests that look like real Discord tokens must be assembled at runtime (GitHub push protection).

## Implementer prompt (template)

> You are the implementer for an approved spec. Read, in order: `AGENTS.md` section 6, the root `CLAUDE.md`,
> `discord-mcp-server/CLAUDE.md`, `discord-mcp-server/docs/SPEC.md` (§§16-17 win over §§1-15), then
> `discord-mcp-server/docs/specs/user-oauth/SPEC.md` (wins over all of them) and `tasks.md`. For the elicitation and token-file
> patterns read `gmail-mcp-server/src/gmail_mcp/server.py` (`_sign_in_response`) and `auth.py` (atomic 0600 write); copy, do not
> import. Implement exactly the spec, nothing else. Do T0 first and stop if it fails. If the spec is wrong, incomplete or impossible,
> stop and report. Tests for every requirement; run `make check` (and `make audit` if dependencies change). Fill the traceability
> table in `implementation-report.md` (one row per U1-U35, the SDK gate, AC-U1, AC-U2), list deviations (should be none) and every
> command run with its result. Never print `.env` values. Do not commit.

## Reviewer prompt (template)

> You are an independent, read-only reviewer. Edit nothing except your report. Read `AGENTS.md` section 7, the root and server
> `CLAUDE.md`, `docs/SPEC.md`, then `docs/specs/user-oauth/SPEC.md`, then the code. Run the gate yourself; do not trust the
> implementer's report. Check every U-requirement is implemented and tested and nothing outside the spec was added; the SPEC §7
> invariant table holds exactly (off mode identical to today; POST only to the two OAuth URLs; only `discord_client.py` imports httpx;
> user and bot limiters and hard-stops isolated; bearer exemption only for the two OAuth GET routes and only when on; state/iss/expiry
> single-use checks; no OAuth secret in logs, errors, results or pages; atomic 0600 token file; allowlist still gates every bot tool);
> refresh is serialized and POSTs are never replayed; docs match. Write `docs/specs/user-oauth/review-round-N.md` with blocker / major /
> minor issues, `file:line` each, and a verdict.
