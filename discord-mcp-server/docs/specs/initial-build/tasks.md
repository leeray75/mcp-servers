# Tasks and traceability skeleton: discord-mcp-server initial build

Derived from `../../SPEC.md`. The implementer completes the traceability table in `implementation-report.md`; this file is the plan,
not a record.

## Work breakdown

| # | Task | Requirements | Main files |
|---|---|---|---|
| T0 | Scaffold from `sports-mcp-server`: `pyproject.toml` (`mcp>=2.3,<3`, httpx, pydantic, pydantic-settings, starlette, uvicorn; dev: pytest, pytest-asyncio, pytest-cov, ruff, mypy, pip-audit, uv), Makefile (coverage 90%), `.gitignore`, `.dockerignore`, `make lock` to generate hash-pinned `requirements*.txt` | D1, D15 | project root |
| T1 | `config.py`, `.env.example`; copy + adapt `logging_setup.py`, `security.py` | R1-R3, R38, section 12 | `config.py`, `.env.example` |
| T2 | `errors.py`, `snowflake.py` (pattern, created_at, ISO <-> synthetic snowflake) | R25-R27, R30-R31 | |
| T3 | `ratelimit.py` (bucket store, global pause/pacing, invalid-request window, injectable clock) | R20-R24, section 9 | |
| T4 | `discord_client.py` (only httpx importer: headers, host pin, no redirects, size cap, retries, 429/202 handling, error mapping, auth hard-stop, boot probe) | R4, R16-R19, R31 | |
| T5 | `cache.py` (adapt from sports), `budget.py` | R36-R37 | |
| T6 | `guard.py`, `permissions.py`, `capabilities.py` | R5-R8, R12-R15, R28-R29, section 10 | |
| T7 | `models.py`, `project.py` | R34-R35, section 8 | |
| T8 | `service.py`: 14 tool methods per section 7 | section 7, R10, R29 | |
| T9 | `server.py`, `__main__.py`: registration in fixed order, descriptions, annotations, success/error `CallToolResult`, `/healthz` states | R5, R8-R11, R30, R32-R33 | |
| T10 | Tests for every requirement (section 13), synthetic fixtures, network guard; coverage >= 90% | all | `tests/` |
| T11 | Dockerfile, `docker-compose.yml`, `scripts/` (+ `.ps1`, `lib/`), `tools/try_live.py` | R39-R40 | |
| T12 | Docs: README, CHANGELOG, server `CLAUDE.md`, root `CLAUDE.md` table and root `README.md` server list | R41-R42 | |
| T13 | Gate (`make check`, `make audit`, `docker compose config`) and `implementation-report.md` | AC1-AC4 | |

Suggested order: T0 -> T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8 -> T9 -> T10 (tests written alongside each task) -> T11 -> T12 -> T13.

## Traceability table (to be completed by the implementer in `implementation-report.md`)

| Req | Implementation (file::symbol) | Test (file::name) |
|---|---|---|
| R1 ... R42 (one row each) | | |
| AC1 ... AC4 | | |
