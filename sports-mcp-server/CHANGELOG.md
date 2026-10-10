# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0]

### Added

- Streamable HTTP MCP server (official `mcp` SDK) with eight read-only tools: `list_leagues`, `get_scoreboard`,
  `get_game`, `get_odds`, `get_standings`, `find_team`, `get_team_schedule` and `get_news`, plus secondary resources.
- Coverage of 15 leagues (NFL, NCAA football, NBA, WNBA, NCAA men's and women's basketball, MLB, NHL, EPL, La Liga,
  Bundesliga, Serie A, Ligue 1, MLS, UEFA Champions League) with aliases and per-sport adapters over one normalized schema.
- Compact text output from ESPN's public JSON API, with freshness metadata on every result (`meta.as_of`,
  `meta.cache_age_seconds`, `meta.cached`, `meta.source`) and explicit labeling when betting lines are withdrawn.
- In-memory caching with `refresh` overrides; bounded concurrency, timeouts and an upstream response-size cap. The only
  outbound host is `site.api.espn.com`. No API key is required.
- `python -m sports_mcp --stdio` for clients that launch the server themselves.
- Bearer-token authentication, Host/Origin loopback guard and structured JSON logging with redaction.
- Hardened container: multi-stage build, hash-pinned dependencies, non-root user, read-only root filesystem, dropped
  capabilities, published on `127.0.0.1:8767` only.
- `scripts/` (`setup`, `start`, `stop`, `restart`, `rebuild`, `logs`, `status`, `smoke`) as `.sh` with PowerShell
  wrappers that run them in WSL2.
- Fixture-based test suite (pytest, no network, 90% coverage gate), `ruff`, `mypy --strict` and a `Makefile`.
