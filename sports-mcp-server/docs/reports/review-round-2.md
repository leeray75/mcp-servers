# Review round 2: sports-mcp-server

Reviewer: independent re-audit, 2026-10-04 (evening, live ESPN). Read-only except this file. I did not read `fix-round-1.md` until my own checks were done.

## Verdict: PASS

No BLOCKER, no MAJOR. 0 of the 19 round-1 findings remain open (17 fixed, 2 deliberately left as in R1-19 and judged acceptable). 5 new findings, all NIT/MINOR.

## Gates and deployment

- `ruff check` clean; `ruff format --check` 48 files clean; `mypy --strict` 40 files clean.
- pytest run twice: 435 passed, 2 live deselected, no flakiness (16 s with coverage, 8.5 s without). Coverage 98.33 % (branch).
- `pytest -m live`: 2 passed. `pip-audit -r requirements.txt --no-deps --disable-pip`: no known vulnerabilities. `scripts/smoke.sh`: "smoke test passed".
- Container: image `sha256:3053de5e...`, started 20:32Z (after the last source edit at 20:28Z), healthy, ReadonlyRootfs true, CapDrop ALL, no-new-privileges, pids 128, 512 MiB, user 10001. SHA-256 of every `src/sports_mcp/**/*.py` on the host equals the file inside the container (`docker exec sha256sum`): running code matches source. Logs: 0 "Cannot create schema" lines, 0 ESPN URL/host lines.

## Round-1 findings re-verified

| ID | Status | Evidence (live unless noted) |
|---|---|---|
| R1-01 | Fixed | `get_standings(mlb)` -> "MLB standings 2026", season 2026 (raw top-level is 2027, leaf is 2026). NBA/NHL show "2026-27" (season 2027), `season=2026` for NBA gives DET 60-22 labelled "2025-26". Test uses a fixture where top-level year is 2027, so it fails without the fix. |
| R1-02 | Fixed | `get_team_schedule(epl, arsenal)` -> 38 games with `Next: Sat 2026-10-10 vs LEE` (raw played-only is a subset; `?fixture=true` adds the rest, merge de-duplicated by id). MLS (Inter Miami) also 34 games with Next. Failure of the second call yields a note (code and test reviewed). |
| R1-03 | Fixed | Byte-bounded LRU (`CACHE_MAX_MB`, default 96, x4 estimate). Load test: 119 distinct game summaries (NFL/CFB/NHL/MLB) with 8 concurrent clients, then 3 big standings: container 118 MiB -> 166 -> 190 MiB (was 187 -> 259 for far fewer calls). Oldest entry was evicted (refetched, `cached:false`), newest still cached. Direct cache script: bytes stay <= budget under 50 concurrent distinct keys, a payload larger than the budget is served but not kept, stale-if-error still returns `stale:true` with "STALE COPY" in text when upstream fails. |
| R1-04 | Fixed | BUF Week 18 now `TBD [Week 18]` (was 12:00 AM EST). |
| R1-05 | Fixed | `season_type=3` without `week` -> note "season_type only applies together with week and was ignored." |
| R1-06 | Fixed | `week=1, season_type=1` -> `season.type = "Preseason"`; plain week 3 -> "Regular Season". |
| R1-07 | Fixed | MLB header "schedule, 2026 Postseason"; new `season_type=2` returns Regular Season games (raw has 163 events); `season_type=9` rejected by schema (max 4). See R2-01 for a wording nit. |
| R1-08 | Fixed | Fake transport: postponed game renders `Postponed A @ B` (no 0-0), header "1 final, 1 postponed/canceled". Test builds a postponed event and would fail with the old `counts`. |
| R1-09 | Fixed | WSH top defender is now Amik Robertson 9 tot (raw top TOT 9); lines carry `pass:/rush:/rec:/def:/K:` prefixes; "1 sack"/"1 shot" singular. Same player may still appear twice but now with distinct labelled lines (CFB: Berlowitz pass and rush), which is clear. NHL and MLB performers re-checked against raw: Guentzel 1G 2A, Vasilevskiy 20/21, Murakami 2-4 HR, all match. |
| R1-10 | Fixed | CFB table shows `record` and `conf` (FAU 4-1 / 1-0); header "2026-27" for NBA/NHL; season argument documented as end year. |
| R1-11 | Fixed | NHL OT goal now `OT 1:33 PIT Tommy Novak ... (5-6)`. |
| R1-12 | Fixed | WNBA lines end with `[Semifinals - Game 1]`. |
| R1-13 | Fixed | `limit=500` returns all 148 NCAAM games (text 8.5 KB); without limit the note reads "Pass limit=<n> (max 500)". `limit=501` rejected. |
| R1-14 | Fixed | EPL pass accuracy `81% \| 78%` (raw 384/474 = 81 %). |
| R1-15 | Fixed (one residue, R2-02) | Summary with no competitors -> error, id-less scoreboard rows skipped. A row with a single competitor still renders (see R2-02). |
| R1-16 | Fixed | 0 schema warnings in container logs; `outputSchema` absent by design (tests assert). |
| R1-17 | Fixed | `get_game(game_id="abc")` -> `Invalid arguments for get_game: game_id: String should match pattern '^[0-9]{1,20}$'` (one line, no URL). |
| R1-18 | Fixed | Arabic-digit date now rejected; team-query message no longer lists "/"; `_digits` raises `UpstreamError` (code reviewed). |
| R1-19 | Fixed / accepted | `now_label` and `find` removed; labels now "Sacks-yds lost"/"Penalties-yds"; pre-game text says "starts". Unchanged items assessed below. |

Regression tests: I read the tests for R1-01, 03, 08, 15, 16, 17, 18, 19; each asserts the new behaviour on data that the old code could not satisfy (2027 top-level year, over-budget cache, postponed status, empty competitors, 4xx message format, ASCII digits, removed attributes), so they would fail without the fixes. The R1-19 label test only checks the label set, which is adequate.

## Items left unchanged under R1-19

- Dockerfile base image not digest-pinned: acceptable. The file explains how to pin, dependencies are hash-pinned with `--require-hashes --only-binary`, and the container is hardened. Recommend pinning for reproducibility but not a defect.
- Soccer draw shown as `T 2-2`: acceptable (spec says T/ties; consistent across sports). A `D` would be friendlier to soccer readers but is optional.

## New findings

| ID | Sev | Location | What | Evidence | Fix |
|---|---|---|---|---|---|
| R2-01 | NIT | `tools.get_team_schedule:625`, `render_schedule` | Empty result with default `status="all"` reads "No all games found for ..."; header omits the season name when the result is empty. | `get_team_schedule(nfl, BUF, season_type=3)` -> `Buffalo Bills schedule (...) - 0 games` / `No all games found for Buffalo Bills in this season.` | Word as "No games found ..." when status is `all`; mention the requested season type. |
| R2-02 | NIT | `render_scoreboard` ids line, `normalize_event` | An event with one competitor and no `shortName` is still listed (`TBD  X`) and gives `ids: None=3`. Only reachable with malformed ESPN data. | Fake transport: event `{id:"3", competitors:[home X]}` -> `ids: None=3`. | Skip events with fewer than two competitors, or fall back to `name`/`id` in the ids line. |
| R2-03 | NIT | `get_scoreboard(limit=500)` | Structured content for 148 games is ~100 KB (spec's 25 KB budget is stated for 16 NFL games, so within spec, but clients that forward structuredContent pay for it). | Response 106,694 B for NCAAM 2026-02-21, text 8.5 KB. | Note in README; acceptable. |
| R2-04 | NIT | `normalize/players.py` interleave | NHL performers list the goalie second, ahead of skaters with more points (WSH: Sourdif 1G, Lindgren G, Jenner 1A). Values are correct; only the ordering is arbitrary. | `get_game(nhl, 401891826, performers, top_n=3)`. | Document "one goalie per team is always included", or order skaters first. |
| R2-05 | NIT | `cache.estimate_bytes` | Each cache fill serialises the full payload once (`json.dumps`) to estimate size; for a 6 MB college standings payload this is extra CPU on each miss. Observed latency is fine (0.2 s). | Code review; timing from round 1. | None needed; could use `Content-Length`. |

## Commands run (abridged)

- WSL: `ruff check`, `ruff format --check`, `mypy --strict src tests`, `pytest -q` (twice, with and without `--no-cov`), `pytest -m live --no-cov`, `pip-audit`, `bash scripts/smoke.sh`, `docker inspect`, `docker images`, per-file `sha256sum` host vs `docker exec`, `docker logs | grep`.
- Windows Python scripts in `%TEMP%\rv` (token read inside the script, never printed) against `http://127.0.0.1:8767/mcp` and raw ESPN: R1-01..R1-14, R1-17, R1-18 probes, raw-vs-tool comparison of 6 leagues (MLB 10-03, NHL 10-03, CFB 10-03, EPL 09-20, NFL and WNBA 10-04: 0 mismatches in teams, scores, winner flags, states), NHL/MLB/CFB performers vs raw box scores, memory load test (119 summaries, concurrency 8, `docker stats`).
- WSL throwaway script with `httpx.MockTransport` for postponed games, stale-if-error, malformed shapes and direct `TTLCache` byte-budget tests.
- Scratch files removed afterwards. No docker state, `.env`, other project or Goose config touched.
