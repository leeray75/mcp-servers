# Tasks and traceability skeleton: twitch-mcp-server initial build

Derived from `../../SPEC.md` (approved 2026-10-29). The implementer completes the traceability table in
`docs/reports/initial-build/implementation-report.md`; this file is the plan, not a record.

Two workstreams run concurrently: the **scaffolding/ops/docs layer** (T0, T11, T12 and the gate tooling) and the
**leaf modules + tests** (`src/twitch_mcp/`, `tests/`). They do not overlap in files.

## Work breakdown

| # | Task | Requirements | Main files |
|---|---|---|---|
| T0 | Scaffold: `pyproject.toml` (`mcp>=2.3,<3`, httpx, pydantic, pydantic-settings, starlette, uvicorn; dev: pytest, pytest-asyncio, pytest-cov, ruff, mypy, pip-audit, uv; `--cov-fail-under=90`, `asyncio_mode=auto`, `--strict-markers`, `-m 'not live'`, marker `live`, ruff line-length 100/py312, mypy strict over src+tests, coverage branch), Makefile, `.gitignore`, `.dockerignore`, `.gitattributes`, `make lock` to generate hash-pinned `requirements*.txt` | D5, D6, D7, R56 | project root |
| T1 | `config.py`, `.env.example` (full §6.5 surface); copy + adapt `logging_setup.py`, `security.py` | R3–R10, R52–R54 | `config.py`, `.env.example` |
| T2 | `errors.py` (`TwitchMcpError`), `guard.py` (allowlist, uniform denial) | R25–R27, R43–R45 | |
| T3 | `ratelimit.py` (app+user buckets from headers, injectable clock), `budget.py`, `cache.py` | R19–R23 | |
| T4 | `twitch_client.py` (only httpx importer: host pin, headers-before-socket, no redirects, byte cap, read retries incl. single 503 retry, **no write retries**, 429 parking, 401 circuit, path/query validation, error mapping, boot probe, the two fixed OAuth form calls) | R11–R18, R21–R24 | |
| T5 | `identity.py` (own user id from `/validate`; moderator/editor verification + TTL cache + invalidation), `confirmation.py` (single-use bound tokens) | R29–R34 | |
| T6 | `capabilities.py` (group switches + token presence + `TWITCH_DISABLE_<TOOL>` → ordered plan; no scope table) | R28, R35–R37, D10 | |
| T7 | `models.py`, `project.py` | R38, R40–R42 | |
| T8 | `service.py`: one method per tool per §6.4; write path per §6.3 | §6.3, §6.4, R25–R34 | |
| T9 | `oauth.py` (authorize URL, pending flow `state`/`iss`/TTL, 0600 store, `UserAuth` refresh-under-lock, revoke-only disconnect) | R46–R51 | |
| T10 | `server.py`, `__main__.py`: registration in fixed order per switch combination, `/healthz`, `/oauth/*`, middleware, lifespan boot probe + tool plan | R1, R3–R5, R35–R39, R43–R45, R50 | |
| T11 | Dockerfile, `docker-compose.yml`, `docker-compose.oauth.yml`, `scripts/` (+ `.ps1` twins, `lib/`), `data/.gitkeep`, `tools/try_live.py` | R1, R2, R46–R48 | |
| T12 | Docs: README (tool tables verbatim from §6.4, write model, confirm flow, per-group scopes §6.6, port 8770, troubleshooting, size placeholder), CHANGELOG, server `CLAUDE.md`, `docs/specs/initial-build/{tasks,workflow}.md` | R57, A8 | |
| T13 | Tests for every requirement (spec §8 file list), synthetic fixtures, network guard; coverage ≥ 90%; gate (`make check`, `make audit`, `docker compose config`) and `implementation-report.md` with a row per R1–R57 | all, A1–A8 | `tests/`, `docs/reports/` |

Suggested order: T0 → T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T13 (tests written alongside each task) →
T11 → T12 (docs figures filled from `tests/test_sizes.py` once it runs).

## Traceability table (to be completed by the implementer in `docs/reports/initial-build/implementation-report.md`)

| Req | Implementation (file::symbol) | Test (file::name) |
|---|---|---|
| R1 … R57 (one row each) | | |
| A1 … A8 | | |
