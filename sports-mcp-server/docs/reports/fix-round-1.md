# Fix round 1

All 19 findings fixed (3 major, 12 minor, 4 nit); none declined. Regression tests are in `tests/test_round1.py` (49 tests) unless noted.

| ID | Status | Change (file) | Covering test |
|---|---|---|---|
| R1-01 | fixed | season read from the table leaf (`normalize/standings.py:standings_season`) | `test_r1_01_mlb_standings_use_the_table_season_not_next_years` |
| R1-02 | fixed | soccer schedules also fetch `?fixture=true` and merge; note if that call fails (`espn_client.schedule_url`, `tools.get_team_schedule`) | `test_r1_02_*` (4) |
| R1-03 | fixed | byte-bounded LRU (`cache.py`, `CACHE_MAX_MB`, default 96, max 400; size = 4x compact JSON; oversize payloads served but not kept); config, README, .env.example | `test_r1_03_*` (6) |
| R1-04 | fixed | `timeValid:false` gives `time_tbd`, no `start_local`, text "TBD" (`normalize/scoreboard.py`, `teams.py`, `render.py`, `models.py`) | `test_r1_04_*` |
| R1-05 | fixed | note when `season_type` is given without `week` (`tools.py`) | `test_r1_05_*` |
| R1-06 | fixed | `season.type` mapped from the returned season's numeric type (`scoreboard.season_info`) | `test_r1_06_*` |
| R1-07 | fixed | header names season type; new `season_type` argument on `get_team_schedule` (`render.py`, `server.py`, `tools.py`) | `test_r1_07_*` |
| R1-08 | fixed | only completed games count as final, placeholder scores dropped (`render.counts`, `scoreboard.py`) | `test_r1_08_*` |
| R1-09 | fixed | defenders ranked by tackles; lines prefixed `pass:/rush:/rec:/def:/K:`; plurals (`normalize/players.py`) | `test_r1_09_*` (3) |
| R1-10 | fixed | college `conf` record; `season_name` such as 2026-27; end-year convention documented (`standings.py`, `server.py`, `render.py`) | `test_r1_10_*` (3) |
| R1-11 | fixed | `render.period_label` gives OT/OT2/OT2/SO | `test_r1_11_*` |
| R1-12 | fixed | notes rendered in scoreboard lines and game header (`render.py`) | `test_r1_12_*` |
| R1-13 | fixed | `limit` max 500; note wording corrected (`server.py`, `tools.py`) | `test_r1_13_*` |
| R1-14 | fixed | pass accuracy derived from accurate/total passes (`normalize/sports.py`) | `test_r1_14_*` |
| R1-15 | fixed | summary with fewer than two competitors raises `UpstreamError`; events without id skipped | `test_r1_15_*` (2) |
| R1-16 | fixed | wrapper signature returns `CallToolResult`; startup schema warnings gone (verified 0 in container logs) | `test_r1_16_*` |
| R1-17 | fixed | `SportsServer.call_tool` turns validation errors into one short line | `test_r1_17_*` |
| R1-18 | fixed | `re.ASCII` on dates; message corrected; `_digits` raises `UpstreamError` | `test_r1_18_*`, `test_unexpected_upstream_id_*`, updated `test_ids_must_be_digits` |
| R1-19 | fixed | removed `now_label` and `find`; labels `Sacks-yds lost`, `Penalties-yds`; "starts" wording. Not changed: Dockerfile digest pin (acknowledged in a comment), soccer draw shown as T (spec wording) | `test_r1_19_*` |

By severity: major 3/3 fixed, minor 12/12 fixed, nit 4/4 fixed (R1-19 partly by design, as noted), 0 declined.

Fixtures: added `epl_schedule_ars_fixtures`; standings fixtures recaptured to keep `vs. Conf.`. The Bills schedule fixture was captured mid-game, so `conftest.make_buf_game_live` restores the live state for live-schedule tests.

## Results

- ruff check, ruff format --check, mypy --strict (40 files): clean; pip-audit: no known vulnerabilities.
- pytest: 435 passed (was 387), 2 live deselected; coverage 98.33%.
- Container `sports-mcp` rebuilt, healthy, user 10001, read-only rootfs, cap_drop ALL, no-new-privileges, bound to 127.0.0.1:8767, 65 MiB resident at idle; no schema warnings in logs. `scripts/smoke.sh` passed against live ESPN.
- Sizes (live, text/structured): NFL scoreboard 1.1 KB / 10.9 KB; NFL performers 2.6 KB / 6.6 KB; Liverpool schedule (38 games incl. fixtures) 2.2 KB / 11.4 KB; college football standings 4.4 KB / 20.8 KB. Other sizes unchanged within a few bytes (performer lines gained a group prefix).
