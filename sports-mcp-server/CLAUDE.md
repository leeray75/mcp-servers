# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Process: Spec-Driven Development is mandatory.** Changes to this server follow the SDD workflow in
> [`../AGENTS.md`](../AGENTS.md): plan with the human, write a spec, get explicit approval, then implement, review
> (fix loop, max 5 rounds) and write a summary report. This file stays authoritative for this server's technical
> invariants; AGENTS.md governs process.

## What this is

A self-owned MCP server (Python 3.12, official `mcp` SDK) giving agents live, compact sports data from ESPN's
unofficial public JSON API: exactly eleven read-only tools (`list_leagues`, `get_scoreboard`, `get_game`, `get_odds`,
`get_standings`, `find_team`, `get_team_schedule`, `get_news`, `find_player`, `get_player_stats`,
`get_player_gamelog`) plus three read-only resources, for 15 team-sport
leagues. Streamable HTTP on 127.0.0.1:8767 behind a bearer token, run in Docker inside WSL2; `--stdio` also works.
It mirrors the structure and security posture of the sibling `../vision-mcp-server` (which mirrors `../gmail-mcp-server`).
`docs/SPEC.md` is the authoritative specification; `README.md` covers operation and configuration;
`docs/reports/implementation-report.md` records what was built, deviations and measured sizes.

## Commands

Run in WSL/Linux with the venv outside the Windows drive (`VENV=~/.venvs/sports-mcp`, create it with
`uv venv ~/.venvs/sports-mcp --python 3.12`):

- `make install VENV=... PYTHON=python3` - hash-pinned dev install plus editable package
- `make check VENV=...` - ruff + `mypy --strict` + pytest (coverage gate 90%, branch); `make format` fixes style
- Single test: `pytest tests/test_tools.py::test_name --no-cov`
- `make lock` re-resolves and re-hashes `requirements*.txt` with uv (never hand-edit); `make audit` runs pip-audit
- `pytest -m live --no-cov` - opt-in checks against real ESPN (deselected by default)
- `python tools/try_live.py scoreboard nfl` - real rendering and sizes for one call; `python tools/capture_fixtures.py`
  re-captures `tests/fixtures/` from live ESPN

Operating the container: `scripts/{setup,start,stop,restart,rebuild,logs,status,smoke}.sh` (each with a `.ps1` twin that
forwards into WSL2). `restart --recreate` is needed after editing `.env`. `smoke.sh` hits live ESPN through the running
container and prints response sizes. `.env` holds the bearer token and is git-ignored: never print, log or commit it.

## Architecture (`src/sports_mcp/`)

- `__main__.py` entry (uvicorn / `--stdio`); `config.py` the only module that reads the environment (errors name fields, never values).
- `server.py` builds the `MCPServer`, registers the eleven tools, `/healthz` and the Starlette middleware. It deliberately has **no**
  `from __future__ import annotations`: `Field(...)` constraints live in `Annotated` metadata the SDK reads for the JSON schema.
  Tools return `CallToolResult(content=[text], structured_content=data)`; the `_instrumented` wrapper maps `SportsMcpError` to
  `ToolError` and logs tool/league/duration/outcome/cache only.
- `tools.py` - `SportsService`: transport-independent logic (validation, cache, normalise, render). Returns `ToolOutput(text, data)`.
- `espn_client.py` - the ONLY module that imports httpx. URL builders per endpoint, host allow-list, retry, size cap, error mapping.
- `cache.py` - TTL/LRU cache bounded by entry count AND approximate bytes (`CACHE_MAX_MB`), in-flight de-dupe, stale-if-error; TTL is decided from the fetched value.
- `leagues.py` registry (single source of ESPN sport/league slugs); `dates.py` date parsing; `models.py` pydantic results + `dump()`;
  `render.py` compact text + `fit()`; `resources.py` the three resources; `security.py` / `logging_setup.py` copied from vision-mcp.
- `normalize/` - `common.py` defensive accessors, `scoreboard.py` events, `odds.py`, `summary.py` (game header, scoring plays,
  leaders, injuries), `players.py` (per-sport performers and box tables), `sports.py` (situation + team-stat allow-lists),
  `standings.py`, `teams.py` (directory, matching, schedule, news).

## Invariants to preserve

- The tool set is exactly the eleven above (a test asserts it; amended by `docs/specs/player-stats`, A1); all read-only. No MCP prompts. Nothing is reachable only via a resource.
- Only `espn_client.py` imports a network library (a test greps for it). No agent-supplied string becomes a URL or path segment
  without a strict pattern: league via the registry, game/team ids digits-only, dates parsed, team/group text a restricted character class.
  Every request host must equal the `ESPN_BASE_URL` host; redirects are never followed.
- Every result carries `meta` with `as_of` = the upstream fetch time (not build time), `cache_age_seconds`, `cached`, `endpoint_kind`.
  Never present stale data without `meta.stale`. Failed upstream calls are never cached.
- Odds: ESPN drops lines at kickoff. Spread is the favorite's handicap (negative), derived from the spread side, not ESPN's `details`.
  `lines_basis` is `current` / `closing` / `unavailable`; never label a retained pre-game line as live.
- Pre-game player stats are omitted unless clearly season totals, then `basis: "season"`; never present season totals as game stats.
- Size budgets (asserted in `tests/test_render.py`): scoreboard text <= 6 KB (structured <= 25 KB), game summary <= 8 KB,
  performers <= 12 KB, full-league standings <= 8 KB. Boxscore text is capped by `max_chars`.
- Memory: the container limit is 512 MiB; the cache must stay byte-bounded (parsed ESPN JSON is several times its raw size).
- Soccer team schedules need a second `?fixture=true` call (ESPN omits upcoming matches otherwise); standings season comes from the table leaf, not ESPN's top level.
- Logs never contain request arguments, results, upstream bodies, URLs (httpx logging is muted) or the token.
- Tests never touch the network (autouse guard); fixtures are real ESPN captures. Keep new fixtures under 300 KB.
- Container: UID 10001, read-only rootfs, caps dropped, port bound to 127.0.0.1 (no TLS), no host mounts.
- Unfinished stretch items must be absent, not half-working (individual-sport scoreboards and the odds snapshot are not implemented).
- On the `/mnt/c` checkout POSIX file modes are not enforced for `.env`.
