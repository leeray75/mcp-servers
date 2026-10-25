# Implementation report: release v0.2.0 (gmail-mcp-server)

| Req | Implementation | Test / check |
|---|---|---|
| R1 | `pyproject.toml` version and `src/gmail_mcp/__init__.py` `__version__` set to `0.2.0` (`serverInfo.version` reads `__version__`) | `test_package_version_matches_pyproject` |
| R2 | `CHANGELOG.md`: `[Unreleased]` body now under `## [0.2.0] - 2026-10-25`, fresh empty `[Unreleased]` above, `[0.1.0]` untouched (file has no compare links) | `test_changelog_records_the_additions`, `test_changelog_records_the_hardening` (now read the 0.2.0 section) |
| R3 | Docs layout lines in `README.md` and `docs/SPEC.md` now list `specs/<feature>/` (reauth, agent-auth, agent-auth-hardening, release-v0.2.0); `CLAUDE.md` intro lists the two missing spec folders. Config table, tool and resource counts checked against `config.py`, `.env.example` and `server.py`: already correct | reviewer reads diff |
| R4 | New test pins `__version__` == pyproject version == `0.2.0` | itself |
| R5 | `make check` (WSL): ruff, format, mypy --strict, pytest | 398 passed, 99.31% coverage |
| R6 | This folder; one commit; nothing pushed, tagged or merged | |

Deviations: none. Branch `release/gmail-mcp-server/v0.2.0` was cut from `feature/gmail-agent-auth-status-elicitation` (08dbb0c).
