# Tasks and traceability skeleton: agent-auth

| # | Task | Requirements | Files |
|---|---|---|---|
| T1 | Auth state model and rejection record | R1 | `auth_state.py`, `gmail_client.py` |
| T2 | `auth_status` tool | R2 | `server.py` |
| T3 | Resources and guides | R3 | `resources.py`, `server.py` |
| T4 | `AUTH_REQUIRED` error contract | R4 | `errors.py` |
| T5 | URL-mode elicitation, gate and kill switch | R5 | `server.py`, `config.py` |
| T6 | Sign-in success copy | R6 | `signin.py` |
| T7 | Tests for every requirement | all | `tests/` |
| T8 | Docs: README, CHANGELOG, `.env.example`, CLAUDE.md, `docs/SPEC.md` | R7 | listed files |
| T9 | Gate and `implementation-report.md` | acceptance | report |

The requirement -> implementation -> test table is section 2 of `implementation-report.md`.
