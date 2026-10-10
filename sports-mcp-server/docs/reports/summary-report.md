# Sports MCP server: summary report

Date: 2026-10-04. Process: spec -> implementer agent -> reviewer agent -> fixes -> re-review (stopped at round 2 of a maximum of 3 because the verdict was PASS).

## Outcome

| Item | Result |
|---|---|
| Spec | `docs/SPEC.md` (8 tools, 3 resources, caching, security, tests, ops) |
| Implementation | Phase 1 complete; Phase 2 stretch (individual sports, odds snapshots) deliberately absent |
| Review round 1 | CHANGES REQUIRED: 0 blocker, 3 major, 12 minor, 4 nit |
| Fix round 1 | All 19 findings fixed, none declined (2 sub-items under R1-19 left as is: Dockerfile digest pin, soccer draws shown as `T`) |
| Review round 2 | **PASS**: 0 blocker, 0 major, 5 new nits |
| Final gates (re-run by the coordinator) | ruff clean; `mypy --strict` clean (40 files); 435 tests passed, 98.33% branch coverage (gate 90%); live smoke test passed |
| Container | `sports-mcp` healthy on 127.0.0.1:8767; UID 10001, read-only rootfs, all capabilities dropped |

## What was built

Project: `C:\Users\hoyen\Documents\Roo Code Workspace\mcp-servers\sports-mcp-server\` (Python 3.12, official `mcp` SDK, httpx, Docker in WSL2, same house style as `vision-mcp-server`).

Tools (exactly 8, read-only): `list_leagues`, `get_scoreboard`, `get_game` (summary / performers / boxscore detail levels, includes player stats), `get_odds`, `get_standings`, `find_team`, `get_team_schedule`, `get_news`.
Resources: `sports://guide`, `sports://leagues`, `sports://leagues/{league}/teams` (duplicate tool data for clients that expose resources; nothing is resource-only).
Leagues: NFL, college football, NBA, WNBA, men's and women's college basketball, MLB, NHL, EPL, La Liga, Bundesliga, Serie A, Ligue 1, MLS, Champions League.
Every result carries freshness metadata (`as_of`, `cache_age_seconds`, `cached`, `stale`) and honest odds labelling (ESPN removes lines at kickoff).
Safeguards: TTL cache with in-flight de-duplication, stale-if-error, and a byte-bounded cache (`CACHE_MAX_MB`, default 96); host allow-list so no input can alter the upstream URL; bearer auth; Host/Origin guard.

Measured sizes on live data (text returned to the model): NFL slate about 1.5 KB; game summary 1-2.2 KB; performers up to about 2.6 KB; full box score about 7 KB; standings up to about 1.8 KB. Compare with the 200-400 KB raw ESPN payloads that overran the agent earlier.

## Review findings and fixes (highlights)

- Wrong season label: MLB standings said 2027 but held the 2026 table (fixed, tested).
- Soccer schedules omitted upcoming fixtures because ESPN needs `fixture=true` (fixed).
- Cache bounded by entry count could exceed the 512 MiB container limit (replaced with a byte budget; round 2 load test: memory 118 to 190 MiB across 119 distinct summaries at concurrency 8).
- Minor items included TBD kickoff times shown as 12:00 AM, `season_type` ignored without `week`, preseason type mislabelled, schedule header omitting season type, and performer-selection quirks. All fixed with regression tests (435 tests now versus 387 before).

Round 2 checked raw ESPN against tool output across six leagues on different games from round 1: zero mismatches.

## Remaining items (all NIT, not fixed)

R2-01 an empty schedule reads "No all games found"; R2-02 a malformed one-competitor event still shows `ids: None=3`; R2-03 a 500-game scoreboard yields about 100 KB structured content; R2-04 NHL performer ordering is arbitrary among ties; R2-05 the cache size estimate serialises each payload once. Also: Dockerfile base image is not digest-pinned.

## Not verified / limitations

- ESPN's API is unofficial and can change without notice.
- No live in-game odds exist from this source.
- Live paths for NBA, MLB, NHL and soccer were tested with constructed data and fixtures, since no such game was live at capture time (NFL and WNBA live payloads were real).
- Goose and Claude Code client snippets in the README are untested; Goose has not been connected to this server.
- Nothing is committed to git (`git init` only), and no client configuration was changed.

## Reports

`docs/reports/`: `implementation-report.md`, `review-round-1.md`, `fix-round-1.md`, `review-round-2.md`, and this file.

## Next steps (not done)

1. Add it to Goose: an `extensions` entry for `http://127.0.0.1:8767/mcp` with the bearer token from `.env` (same method as the `vision` extension).
2. Optionally fix the five nits and commit.
3. Phase 2: individual sports (F1, golf, UFC, tennis) and pre-game odds snapshots.
