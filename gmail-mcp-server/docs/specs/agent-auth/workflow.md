# Workflow: agent-auth

Runs only on the Approved `SPEC.md` (approved 2026-10-25). Process authority: `../../../../AGENTS.md`. Layout follows the sports
server (`sports-mcp-server/docs/specs/<feature>/`).

| Role | Agent | Model | Edits code | Writes |
|---|---|---|---|---|
| Orchestrator | main session | session model | no | `summary-report.md` |
| Implementer | subagent | Haiku | yes | `implementation-report.md`, `fix-round-N.md` |
| Reviewer | fresh subagent per round | Sonnet (read-only) | no | `review-round-N.md` |

At most 5 review rounds. The implementer triages each finding: it fixes all blockers and majors, and each minor (or accepts it with a
written justification). If the spec is wrong or incomplete it stops and the orchestrator returns to the human; deviations that change
a requirement (see implementation report section 4: D1, D2, D4, D6) need the human's decision, not the implementer's. WSL venv
`~/.venvs/gmail-mcp`; never print `.env` values; no commit or push unless the human asks.
