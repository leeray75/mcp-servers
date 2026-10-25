# Workflow: team rank and turnovers

Runs only after the human sets `SPEC.md` to Approved. Process authority: `../../../../AGENTS.md`. Same loop and prompts as
`../player-stats/workflow.md`, with R1-R8 and AC1-AC3 of this spec.

| Role | Agent | Model | Edits code | Writes |
|---|---|---|---|---|
| Orchestrator | main session | session model | no | `summary-report.md` |
| Implementer | subagent | Haiku 5.5 | yes | `implementation-report.md`, `fix-round-N.md`, `upstream-notes.md` |
| Reviewer | fresh subagent per round | Sonnet 5.5 (read-only) | no | `review-round-N.md` |

At most 5 review rounds. The implementer fixes all blockers and majors and each minor (or justifies it); if the spec is wrong or incomplete
it stops and the orchestrator returns to the human. T0 runs first; if it contradicts the spec (for example the rank field differs by league)
the implementer stops after T0. Branch `feature/team-rank-and-turnovers`; WSL venv `~/.venvs/sports-mcp`; never print `.env` values; no
commit or push unless the human asks.
