# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-25

### Added

- Team rank and turnovers (spec `docs/specs/team-rank-and-turnovers/SPEC.md`): ESPN's poll rank (1-25) on game teams
  (`rank`; text `#6 IU`), team records shown in scoreboard text within its 6 KB budget (dropped with a note when over),
  `get_scoreboard` parameters `ranked_only` and `sort="rank"` (applied before `limit`), and a football turnover line
  (`TO`, takeaways) in `performers` and `boxscore`. The same turnover totals are also returned as a structured
  `turnovers` field keyed by team abbreviation: an addition beyond the named output, consistent with the text line.
  `find_team` is unchanged: ESPN's team payloads carry no rank.
- Player tools `find_player`, `get_player_stats` and `get_player_gamelog` (tool set 8 -> 11; spec
  `docs/specs/player-stats/SPEC.md`, amendment A1). Names get typo retries; athlete ids use the profile endpoint.
  Game logs pick regular or postseason blocks by display name, compute totals from the listed games and show football
  turnovers (TO = INT thrown + fumbles lost).
- Settings `TTL_PLAYER_SEARCH_SECONDS` (default 3600), `TTL_PLAYER_SECONDS` (300) and `PLAYER_MAX_SEASONS` (10).
- Player tools polish (spec `docs/specs/player-tools-polish/SPEC.md`): `get_player_gamelog` always states completeness in
  one line under the header (`complete` or `PARTIAL`, with the omitted seasons or rows) and in structured
  `completeness`; a game with no recorded stats is kept, noted under its season and flagged `no_stats`.

### Changed

- Player tool descriptions say `season=all` is the whole career (up to `PLAYER_MAX_SEASONS`) and that a player given by
  name works directly; the "capped" and "most recent seasons" wording is gone.
- Missing and season-to-date cells in player text tables render `n/a` instead of `-`, which now only means a minus sign.
- Structured player totals whose inputs were all missing are now JSON `null` instead of `"-"` (spec amendment A1).
  Structured data has no `"-"` or `"n/a"` placeholder strings.

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
