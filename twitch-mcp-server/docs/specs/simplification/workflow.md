# Workflow: implement / review loop for the simplification

Runs only after the human sets `SPEC.md` in this folder to **Approved** (an explicit "Approve" in chat). Until then,
no code, test, script, dependency or configuration file may change. Process authority: `../../../../AGENTS.md`
(untouched by this change). Technical authority for the server: `../../../CLAUDE.md`, as amended with the code.

## Roles

| Role | Agent | Edits code | Writes |
|---|---|---|---|
| Orchestrator | main session | no (coordinates, runs the final gate, writes the summary) | `summary-report.md` |
| Implementer | one subagent, owns all files in `SPEC.md` §6.1 | yes | `implementation-report.md`, `fix-round-N.md` |
| Reviewer | one subagent, read-only, distinct from the implementer | **no** | `review-round-N.md` |

One implementer is enough: the change touches one codebase, and the removals overlap across `config.py`, `server.py`,
`service.py` and the test fakes, so splitting it would only create merge conflicts. The reviewer is distinct and
re-reads the code and spec from disk every round.

## Review method (S13)

The reviewer does two things and nothing else:

1. Runs the server's gate: `make check VENV=~/.venvs/twitch-mcp` (ruff, `mypy --strict`, pytest with the 90% branch
   gate and the 30-second per-test timeout).
2. Reads the changed code and tests against `SPEC.md` (S1–S15), `CLAUDE.md` and §8 acceptance.

**Forbidden for every agent:** building a mutation-testing harness, a scratch-copy mutation matrix, a driver that
patches the code to see whether tests fail, or any test-generation harness outside the repo's own test suite. The
stall on this codebase came from exactly that. If a reviewer believes a test is too weak, it records the concern as a
finding and the implementer strengthens the test by reading the requirement, not by running a mutant.

## Loop (at most 5 review rounds)

1. Orchestrator confirms the SPEC is Approved (header line with the human's approval), and that the working tree is
   clean except the spec folder.
2. Implementer applies §6.1 in the order of `tasks.md` (A → B → C → D → E). It runs the gate. It writes
   `implementation-report.md`: traceability S1–S15, deviations (should be none), commands run and their results.
3. Reviewer writes `review-round-1.md`: issues classified blocker / major / minor, each with `file:line`, and a verdict
   APPROVED or CHANGES REQUESTED.
4. If CHANGES REQUESTED: the implementer fixes all blockers and majors. Each minor is fixed or accepted in writing.
   It records everything in `fix-round-N.md` and re-runs `make check`.
5. Reviewer round N+1 checks the fixes and for regressions. Repeat until APPROVED, **at most 5 review rounds**.
6. If round 5 is not APPROVED, the orchestrator stops and asks the human: more rounds, amend the spec, accept the
   remaining issues, or abandon.
7. On APPROVED the orchestrator re-runs `make check` itself and writes `summary-report.md`, then stops. **No commit,
   push, tag or release unless the human asks.**

## Hang handling

A test that hangs is a finding, not a reason to wait. With `timeout = 30` and `timeout_method = "signal"` (S12) it fails in 30 seconds. A timeout
failure is classified as a major unless the reviewer can show the test is wrong. An existing test that needs more than
30 seconds is a `SPEC-GAP:` item, not a reason to raise the value.

## Spec issues (any agent)

If an implementer or the reviewer finds the spec wrong, incomplete, contradictory or impossible, it stops on that item
and reports it verbatim as a `SPEC-GAP:` line with the requirement number. The orchestrator takes it to the human,
amends the SPEC (recorded as an amendment), gets re-approval, and restarts from step 2 for the affected parts.
Nobody improvises around the spec.

## Environment

Run in WSL with the venv outside the Windows drive: `VENV=~/.venvs/twitch-mcp` (create with
`uv venv ~/.venvs/twitch-mcp --python 3.12`). The lock is regenerated only by `make lock`. `.sh` files stay LF
(`.gitattributes`). Never read, print, log or commit `.env` values.

## Artifacts

`docs/specs/simplification/SPEC.md`, `tasks.md`, `workflow.md` (this file); reports in
`docs/reports/simplification/`: `implementation-report.md`, `review-round-N.md`, `fix-round-N.md`, `summary-report.md`.
