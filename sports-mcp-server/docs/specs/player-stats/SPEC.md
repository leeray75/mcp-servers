# Spec: player stats tools (`find_player`, `get_player_stats`, `get_player_gamelog`)

- **Status:** Approved, 2026-10-24, approved by: human (original version, amendment A1 and amendment A2, section 11)
- **Date:** 2026-10-24
- **Server:** `sports-mcp-server`
- **Amends:** `docs/SPEC.md` (section 5 tools, section 8 upstream client, section 10 configuration, section 13 acceptance). Where this spec
  conflicts with `docs/SPEC.md`, this spec wins for the three new tools. It also deliberately changes one invariant in `CLAUDE.md`:
  the tool set grows from exactly eight to exactly eleven (see section 7).
- **Companion files:** `upstream-notes.md` (verified ESPN endpoint shapes), `tasks.md` (work breakdown and traceability skeleton),
  `workflow.md` (implement/review loop protocol and agent prompts).
- **Artifact location:** the human asked for `docs/specs/{feature}/`, so every artifact of this feature, including reports, lives in
  `docs/specs/player-stats/`. This overrides the `docs/SPEC-<feature>.md` / `docs/reports/<feature>/` convention in `AGENTS.md` for this
  feature only.

## 1. Problem / motivation

An agent (Goose with a Qwen model) was asked: *"What are NY Giants Cam Skattebu's career game stats in the NFL?"* (note the misspelling).
It struggled. The same question was answerable here only by doing 23 `get_game(detail="boxscore")` calls (one per Giants game across two
seasons), extracting one player's rows from each, and adding them up by hand. That needs: guessing the team's game ids, many sequential
calls, scanning large box scores, and arithmetic, all things small models do badly. It also produced a wrong answer at first: box scores
omit a game in which the player played but recorded no stats (ESPN lists 14 games played, the box-score scan found 13).

ESPN's public API has the right data already, per player: a search endpoint, a per-season game log, and season/career totals, all on the
host this server already allows. The server should expose them as compact, correctly labelled, pre-totalled tool results.

## 2. Goals and non-goals

**Goals**

- G1. One call to find a player from a (possibly misspelled) name.
- G2. One call to get a player's season-by-season and career totals.
- G3. One call to get a player's game-by-game log for one season or for the whole career, with server-computed totals.
- G4. Works for all 15 supported leagues through one label-driven renderer (ESPN supplies stat names and labels per sport).
- G5. Show fumbles, fumbles lost, interceptions and turnovers for football players.
- G6. Keep every result compact (section 5, R16) and honest about freshness, truncation and computed values.

**Non-goals**

- Individual sports (golf, tennis, F1, MMA), which are not in the league registry.
- Splits (home/away, by opponent), advanced metrics, projections, fantasy points, injuries, contracts, bios, player comparisons.
- Roster tools, team-roster fuzzy matching, or any new upstream host.
- Write operations, accounts, caching to disk.

## 3. Decisions (made with the human on 2026-10-24)

| # | Question | Decision |
|---|---|---|
| D1 | Tool shape | **3 tools**: `find_player`, `get_player_stats`, `get_player_gamelog`. Tool set 8 -> 11. |
| D2 | Leagues | **All 15 leagues**, generic label-driven rendering. Fixtures for NFL, NBA and MLB; other leagues share the code path. |
| D3 | Multiple seasons | `season` accepts a 4-digit year or `all`; default is the current season. `all` fans out one cached upstream call per season played (capped). |
| D4 | Player identification | `player` accepts an ESPN id or a name. A single clear match resolves automatically; otherwise a ranked candidate list is returned instead of a guess. |
| D5 | Postseason | `season_type`: `regular` (default) \| `post` \| `all`. `all` shows both with separate subtotals. |
| D6 | Totals | Server computes per-season and career totals from the listed games, labelled "computed from listed games". Football shows fumbles, fumbles lost, interceptions thrown and turnovers. |
| D7 | Size budget | Stats and a one-season game log <= 8 KB text; `season=all` <= 20 KB, trimming the oldest seasons first with an explicit truncation note. |
| D8 | Typos | **Prefix retry + fuzzy rank**: on an empty ESPN search, retry with the longest name token trimmed and rank candidates by edit distance; resolve automatically only if one candidate is clearly closest. |
| D9 | Artifact location | `docs/specs/player-stats/` for spec and reports. |
| D10 (A1) | Id lookups need name/team/position | Add the **athlete profile endpoint** as a fourth URL builder (same host, digits-only id). |
| D11 (A1) | Postseason mechanics (ESPN ignores `seasontype` on the gamelog) | **Filter by block name**: select regular/post blocks from the gamelog response by `displayName`; drop the `season_type` argument from the gamelog URL builder; ignore preseason blocks. |
| D12 (A1) | MLB season-to-date rate columns | **Recompute AVG from H/AB; show `-` in totals for OBP/SLG/OPS**; per-game rate cells are labelled season-to-date or hidden. |

Decisions made by the spec author within the delegated scope (flagged for the human to veto at approval):

- S1. `season` is a **string** parameter with pattern `^(current|all|\d{4})$` (not an int/union) so the JSON schema stays a single simple
  type that small models handle well.
- S2. `league` is a required parameter on all three tools, like every other tool here (no cross-league guessing).
- S3. Turnovers are derived only for the football family (see R12); other sports show ESPN's native columns as-is.

## 4. Requirements

Each requirement is individually testable. "Fixture" means a captured real ESPN payload under `tests/fixtures/` (each < 300 KB).

### Tools and parameters

- **R1. `find_player(league, query, limit=5, refresh=False)`** returns up to `limit` (1-10) players in `league` matching `query`,
  each with `id`, `name`, `team` (if known), `position` (if known), `league`. Results are ranked (exact name match first, then
  ESPN order). An empty result is not an error; it carries a `note` suggesting another spelling.
- **R2. `get_player_stats(league, player, season_type="regular", refresh=False)`** returns, for the resolved player, season-by-season
  rows (year, team, games played, per-category stats) and ESPN's own career totals row, grouped by ESPN stat category (for example
  passing, rushing, receiving, defensive).
- **R3. `get_player_gamelog(league, player, season="current", season_type="regular", refresh=False)`** returns one row per game
  (date, week or round, home/away, opponent, result and score, stats), plus computed totals per season and, when more than one season is
  listed, career totals over the listed seasons.
- **R4. Player resolution (`player` parameter).** A string of 1-12 digits is an ESPN athlete id: the server fetches the athlete
  **profile** (D10; one cached call) to obtain `name`, `team` and `position`; an unknown id (profile 404) yields a not-found `ToolError`.
  Name lookups that resolve via search take name/team/position from the search item and do not call the profile endpoint. Anything else is a name: it is searched (R1), then
  (a) exactly one candidate -> resolved;
  (b) several candidates, but exactly one whose folded name equals the folded query (case, accents and punctuation ignored) -> resolved;
  (c) otherwise unresolved: the tool returns a non-error result with `resolved: false`, a ranked candidate list (id, name, team, position)
  and a note telling the agent to call again with an `id`. The result of a resolved lookup always names the matched player
  (`player: {id, name, team, position}`) and how it matched (`match: "id" | "exact" | "single" | "fuzzy"`).
- **R5. Typo tolerance (D8).** When the ESPN search for a name returns zero players in `league`, the server retries, in order, with the
  longest alphabetic token trimmed by one, then two, then three trailing characters (each retry must keep at least 4 characters), stopping
  at the first non-empty result. Candidates from a retry are ranked by Levenshtein distance between the folded query and the folded
  candidate name (ties by ESPN order) and are marked `match: "fuzzy"`. A fuzzy candidate auto-resolves only if its distance is at most 2
  (or <= 20% of the folded query length, whichever is larger) AND the next candidate's distance is at least 2 greater; otherwise the
  result is unresolved per R4(c). At most 3 retries; retries are cached like any other call. Pure-digit queries never use fuzzy logic.
- **R6. Seasons.** `season="current"` uses the upstream default (the player's current season). `season="YYYY"` requests that season.
  `season="all"` first obtains the seasons the player played from the stats endpoint (distinct years), then fetches one game log per
  season (cached, concurrency bounded by the existing `ESPN_MAX_CONCURRENCY`). At most `PLAYER_MAX_SEASONS` (default 10, range 1-25)
  most recent seasons are fetched; older ones are omitted and named in a `truncated` note ("3 earlier seasons omitted (2016-2018); call
  with season=YYYY"). A year outside 1990-2100 is rejected before any request. A requested season in which the player has no games
  returns an empty result with a note, not an error.
- **R7. `season_type`.** `regular` shows regular-season games only; `post` postseason only; `all` both, shown as separate labelled blocks
  with separate subtotals and no combined grand total. Defaults to `regular`.
  **Mechanics (A1/D11, verified in fixtures; see `upstream-notes.md`):** ESPN ignores `seasontype` on the gamelog endpoint; each season
  response contains regular and postseason blocks, which the server selects by the block `displayName` (for example "2025 Regular Season",
  "2025 Postseason"). Preseason blocks (seen for the NBA) are ignored for every `season_type`. `player_gamelog_url` therefore takes no
  `season_type`. For `get_player_stats`, pass `seasontype=3` to the stats endpoint where ESPN honours it (football, NBA) for `post`; for
  `regular`, drop categories whose `displayName` contains "Postseason" (MLB returns a "Postseason Batting" category by default).

### Normalization, totals and labelling

- **R8. Generic label-driven rendering.** Stat columns, names and labels come from the upstream payload (`names`, `labels`,
  `displayNames`, category `name`). A per-sport-family **column allow-list** (like the existing team-stat allow-lists in
  `normalize/sports.py`) selects which columns appear in the text rendering and their order. Columns not on the allow-list stay in
  `structuredContent` only. A stat name unknown to the allow-list never breaks rendering. A league whose payload is missing
  expected fields yields a clear `UpstreamError`, never a crash or a fabricated value.
- **R9. Computed totals (D6).** Per stat the allow-list declares a reducer: `sum`, `max` (for "longest" stats such as LNG),
  `ratio(numerator, denominator)` (for averages such as yards per carry, completion percentage, yards per reception, batting average),
  `pair` (composite `a/b` values such as completions/attempts, summed component-wise) or `none` (shown as `-` in totals). Rates are
  recomputed from summed components, never averaged or summed. Dash/empty cells (`-`, `""`) are treated as missing, not zero, and a
  total whose inputs were all missing is `-`. A game with all-zero stats still counts as a game played (the Skattebo case).
  **Season-to-date rate columns (A1/D12):** some upstream per-game columns are season-to-date, not per game (MLB `avg`, `obp`, `slg`,
  `ops`). They are shown only if labelled "season-to-date" (or hidden from the text and kept in structured data). In totals, `avg` is
  recomputed as H/AB; `obp`, `slg` and `ops` are `-` because they cannot be rebuilt from game rows.
  Totals rows are labelled **"computed from listed games"** in text and carry `computed: true` in structured data.
- **R10. ESPN totals kept distinct.** `get_player_stats` shows ESPN's own season and career totals unmodified and labelled as ESPN
  totals. They are never merged with, or silently corrected to match, the computed totals from `get_player_gamelog`.
- **R18 (A1). Displayed score.** ESPN's per-event `score` field is inconsistent (sometimes home-first, sometimes winner-first). The
  game row's `result` and `score` are derived from `homeTeamScore`/`awayTeamScore`, the player's team id and `atVs`, always shown from
  the player's team's perspective (player team's points first, for example `L 27-34`). The upstream `gameResult` letter is used only
  if it agrees with the derived result; on disagreement the derived value wins and structured data records no error.
- **R11. Games played.** The gamelog result states the number of games listed per season and in total, counting zero-stat games.
- **R12. Football turnovers (D6, S3).** For the `football` family the game rows and totals include: fumbles (`FUM`), fumbles lost
  (`LST`), interceptions thrown (`INT`, passing category, only for players who have a passing category) and a derived
  **`TO` = interceptions thrown + fumbles lost**. `TO` is computed by the server, labelled as derived, and is `-` for a player with
  neither stat category present. Defensive interceptions (a defender intercepting a pass) are a different stat and must never be
  counted into `TO` or shown under `INT`; they appear, if at all, under their own defensive label.
- **R13. Freshness envelope.** Every result carries `meta` as in `docs/SPEC.md` 5.9 (`source`, `as_of` = the upstream fetch time of
  the oldest contributing response, `cache_age_seconds`, `cached`, `endpoint_kind` in `player_search | player_stats | player_gamelog`).
  `stale` is set when stale-if-error data is served. For `season=all`, `as_of`/`cache_age_seconds` describe the oldest contributing
  response and `note` says how many upstream calls contributed.
- **R14. Caching.** Search results, stats and gamelogs use the existing `TTLCache` (in-flight de-dupe, stale-if-error, byte bound).
  The profile call uses `TTL_PLAYER_SEARCH_SECONDS`. New settings (section 6): `TTL_PLAYER_SEARCH_SECONDS` default 3600, `TTL_PLAYER_SECONDS` default 300. Failed upstream calls are never
  cached. `refresh=true` bypasses the cache for every upstream call the request makes.
- **R15. Errors.** Unknown league -> existing league error. Unknown athlete id -> not found. ESPN 429/5xx/timeouts -> existing
  mapped messages. Malformed or unexpected upstream shape -> `UpstreamError("ESPN returned an unexpected response")`. Error messages
  never echo the query text, upstream bodies or URLs.

### Size and output

- **R16. Size budgets (asserted in `tests/test_render.py`).** `find_player` text <= 2 KB; `get_player_stats` text <= 8 KB;
  `get_player_gamelog` one season <= 8 KB; `season=all` <= 20 KB. If a `season=all` result would exceed 20 KB the oldest seasons are
  dropped first (whole seasons), the totals cover only the listed seasons, and the truncation is stated in the text and in
  `structuredContent.truncated`. Structured data <= 60 KB.
- **R17. Text shape.** Compact, grouped by stat category, one line per game, for example (shape, not exact):

  ```
  NFL | Cam Skattebo, RB, NY Giants (id 4696981) | 2025 + 2026 regular season | 14 games (as of 12:20 EDT, fetched just now)
  2025 Regular Season (8 games)
  Wk Date   Opp      Res  | Rushing: CAR YDS AVG TD LNG | Receiving: REC TGT YDS AVG TD LNG | FUM LST TO
  1  Sep 7  @WSH  L 6-21  |          2  -3 -1.5 0   -1 |            2   2  12 6.0  0   6  | 0   0   0
  ...
  Total (computed from listed games)  101 410 4.1 5 24 | ...
  2026 Regular Season (6 games) ...
  Career, listed seasons (computed from listed games): 14 G | CAR 182 YDS 704 AVG 3.9 TD 6 | ...
  ```

## 5. Design

### 5.1 Files affected

| File | Change |
|---|---|
| `src/sports_mcp/espn_client.py` | Add URL builders `player_search_url(query, limit)`, `player_profile_url(league, athlete_id)` (A1/D10), `player_stats_url(league, athlete_id, season_type)` (`seasontype` passed only for `post`) and `player_gamelog_url(league, athlete_id, season)` (no `season_type`, A1/D11). Same host allow-list, `_digits` for ids, year check for season, fixed mapping for the stats `seasontype`. No new host. New path prefix `/apis/common/v3` (constant next to `SITE_V2`/`V2`). Search requests pass `type=player`. |
| `src/sports_mcp/config.py` | Add `ttl_player_search_seconds`, `ttl_player_seconds`, `player_max_seasons` (+ env names, bounds, `.env.example`). |
| `src/sports_mcp/normalize/player_stats.py` (new) | Search-result normalization, name folding, Levenshtein ranking, stats and gamelog normalization, allow-lists per sport family, reducers and totals. Pure functions, no I/O. |
| `src/sports_mcp/models.py` | New pydantic result models: `PlayerRef`, `PlayerSearchResult`, `PlayerStatsResult`, `GameLogRow`, `SeasonLog`, `PlayerGameLogResult`; extend `dump()` if needed. |
| `src/sports_mcp/render.py` | Text renderers for the three results; `fit()`-based truncation honouring R16. |
| `src/sports_mcp/tools.py` | `SportsService.find_player`, `get_player_stats`, `get_player_gamelog`, shared `_resolve_player`. |
| `src/sports_mcp/server.py` | Register the three tools with `Annotated[..., Field(...)]` constraints (no `from __future__ import annotations`), docstrings written for small models (what it does, when to use it, parameter examples), `_instrumented` wrapper, error mapping. |
| `src/sports_mcp/resources.py` | Update `sports://guide` to mention the three tools. |
| `tests/` | New tests and fixtures (section 8); update the pinned tool-set test to eleven. |
| `tools/capture_fixtures.py`, `tools/try_live.py` | Extend to capture/try player endpoints. |
| `README.md`, `CHANGELOG.md` (`[Unreleased]`), `.env.example`, `CLAUDE.md`, `docs/SPEC.md` | Document the new tools/settings; update the "exactly eight tools" statements to eleven; add an amendment pointer in `docs/SPEC.md`. |
| `scripts/smoke.sh` | Add a live player check. |

### 5.2 Data flow

`get_player_gamelog(league, player, season, season_type)`:
`_resolve_player` (digits -> id; name -> cached search -> R4/R5) -> season list (only for `all`: cached stats call) ->
cached gamelog call(s) -> `normalize/player_stats.py` -> totals -> `render.py` -> `ToolOutput(text, data)`.
`get_player_stats` is the same minus the gamelog fan-out. `find_player` is search + ranking only.

### 5.3 Return shape

Tools return `CallToolResult(content=[text], structured_content=data)` like the existing tools.
Structured data (abridged): `{player:{id,name,team,position,league}, match, resolved, seasons:[{year, season_type, games:[{date, week, home_away, opponent, result, score, stats:{<name>:<value>}}], totals:{<name>:<value>}, game_count}], career_totals, truncated?, note?, meta}`.

## 6. Configuration

| Env var | Default | Bounds | Purpose |
|---|---|---|---|
| `TTL_PLAYER_SEARCH_SECONDS` | 3600 | 0-86400 | player search cache |
| `TTL_PLAYER_SECONDS` | 300 | 0-86400 | player stats and gamelog cache |
| `PLAYER_MAX_SEASONS` | 10 | 1-25 | most recent seasons fetched for `season=all` |

Config errors name fields, never values. `config.py` stays the only module that reads the environment.

## 7. Security and invariants

| Invariant (from `CLAUDE.md`) | Effect of this change |
|---|---|
| Exactly eight read-only tools | **Deliberately changed to exactly eleven, all read-only.** The pinned test, `CLAUDE.md`, `README.md` and `docs/SPEC.md` are updated. No MCP prompts; nothing resource-only. |
| Only `espn_client.py` imports a network library | Unchanged; the grep test still passes. |
| No agent string becomes a URL/path segment without a strict pattern | Athlete id digits-only (`_digits`, also for the profile path); season `\d{4}` within 1990-2100; season_type from a fixed map; league via registry. The **search query is the one free-text value sent upstream**, as a URL-encoded `query` parameter only (never a path segment), length-limited (1-60 chars) to a restricted character class (letters incl. accents, digits, space, `. ' -`). |
| Host must equal `ESPN_BASE_URL` host; no redirects | Unchanged; all three endpoints are on `site.api.espn.com`. |
| `meta.as_of` is the upstream fetch time; never stale without `meta.stale` | Preserved (R13). |
| Failed upstream calls are never cached | Preserved (R14). |
| Logs never contain arguments, results, bodies, URLs or the token | Preserved: the player query and name are arguments and are **not logged**; only tool, league, duration, outcome and cache status. |
| Tests never touch the network; fixtures < 300 KB | Preserved. |
| Memory bound / byte-bounded cache | `season=all` fans out at most `PLAYER_MAX_SEASONS` calls; each response (~60 KB raw) is cached under the existing byte bound. |
| Pre-game stats / odds rules | Not applicable. |
| Container, ports, auth, rootfs | Unchanged. |

New risk: prompt injection through ESPN-returned text. Player and team names come from third parties; tool descriptions already state
returned text is data. No new instruction-bearing fields (such as news bodies) are rendered.

## 8. Test plan and acceptance criteria

All tests are offline; fixtures are real ESPN captures from `tools/capture_fixtures.py`. Coverage gate stays 90% (branch).

| Requirement | Verification |
|---|---|
| R1 | Unit: search normalization from the Skattebo, "smith" (multi-league, multi-result) fixtures; league filtering; ranking; empty result note; `limit` bounds. |
| R2, R10 | Unit: stats fixture -> season rows and ESPN totals preserved unmodified (Skattebo: career GP 14, 182 CAR, 704 YDS, 6 TD). |
| R3, R11 | Unit: gamelog fixtures for 2025 (8 games) and 2026 (6 games, including the all-zero Saints game) -> rows, games counted, zero-stat game kept. |
| R4 | Unit/service: digits id; single match; exact-name tiebreak among several; unresolved list; unknown id -> not found. |
| R5 | Unit: "Skattebu" -> empty search -> retry "skatteb" -> resolves to Skattebo (`match: fuzzy`); an ambiguous typo stays unresolved; retry cap of 3; minimum 4 chars; digits never fuzzy; Levenshtein and the auto-resolve thresholds. |
| R6 | Service with a fake client: `all` fans out per stats-row year, respects `PLAYER_MAX_SEASONS` and notes omitted years; year bounds; empty season -> note. |
| R7 | Unit with a postseason fixture (an NFL player with playoff games): regular / post / all, separate subtotals; block selection by `displayName`; preseason ignored (NBA fixture); MLB "Postseason Batting" dropped for `regular`; URL builder tests: no `season_type` in the gamelog URL, `seasontype=3` only on the stats URL for `post`. |
| R4 (A1) | Add: id lookup calls the profile endpoint (profile fixture for Skattebo and LeBron), unknown id -> not found, name lookups do not call it. |
| R9 (A1) | Add: MLB fixture: `avg` totals = H/AB; `obp`/`slg`/`ops` totals `-`; season-to-date cells labelled or hidden. |
| R18 | Unit: derived score/result from home/away scores and team id for home and away games, including a game where upstream `score` is winner-first (Skattebo vs NO: shown `L 27-34`). |
| R8 | Unit: allow-list column selection for football, basketball, baseball; an unknown stat name and a missing field do not crash; malformed payload -> `UpstreamError`. |
| R9 | Unit: each reducer (sum, max, ratio, pair, none), dashes treated as missing, rates recomputed (Skattebo career computed from fixture rows: 182 CAR, 704 YDS, 3.9 AVG, 6 TD, equal to ESPN's totals). Property-style check: computed total == arithmetic sum of listed rows for every summable stat in every fixture. |
| R12 | Unit: a QB fixture (INT thrown + fumbles lost -> TO), a RB without passing (INT shown as `-`, TO = fumbles lost), a defender with interceptions (not counted into TO). |
| R13, R14 | Service: `meta` fields, `as_of` of the oldest response, cache hit/miss/`refresh`, stale-if-error, failed calls uncached, TTL settings. |
| R15 | Service: error mapping, no echo of query/body/URL in messages. |
| R16, R17 | `tests/test_render.py`: size budgets with real fixtures (including a 10-season fixture or a synthetic expansion), truncation drops oldest first, text matches the documented shape. |
| Section 7 | Existing guard tests: tool-set pin = exactly eleven names; network-import grep; host allow-list tests for the new URL builders (including the query string); log test shows no query text; hostile `player`/`query` values (path characters, long strings, control characters) rejected. |

**Acceptance criteria**

- AC1. `make check` passes (ruff, `mypy --strict`, pytest, coverage >= 90%).
- AC2. With fixtures, `get_player_gamelog(league="nfl", player="Cam Skattebu", season="all")` resolves to Cam Skattebo (id 4696981),
  lists 14 games (8 in 2025, 6 in 2026), and the career line shows 182 CAR, 704 YDS, 3.9 AVG, 6 TD, plus fumbles/lost/turnovers.
- AC3. `season=all` text <= 20 KB, one-season text <= 8 KB, `find_player` text <= 2 KB (asserted in tests).
- AC4. The tool-set test pins exactly the eleven tools; `README.md`, `CLAUDE.md`, `docs/SPEC.md` pointer, `CHANGELOG.md` and `.env.example` match.
- AC5. `scripts/smoke.sh` (live ESPN, through the rebuilt container) shows a player search and a game log with sizes. The implementer
  runs it only if the container can be rebuilt without touching `.env`; otherwise the summary report states it as not run.
- AC6. No file outside the table in section 5.1 changes, except new test files/fixtures.

## 9. Out of scope

Individual-sport athletes, splits, advanced analytics, roster/depth-chart tools, player comparison, injury or contract data, college
stats for NFL players (ESPN's league filter offers `college-football`; a separate call with `league="college-football"` already
covers it), per-game defensive/special-teams breakdowns beyond what the allow-list shows, and any change to existing tools.

## 10. Open questions

None. (All Phase 1 questions are recorded in section 3. Residual upstream-shape unknowns, for example how ESPN exposes postseason
game logs, are explicit verification steps in `upstream-notes.md`; if the implementer finds the spec wrong or impossible it stops and
reports instead of improvising.)

## 11. Amendment log

**A1, 2026-10-24.** Raised by the implementer after fixture capture (T0), who stopped instead of improvising. Decided by the human the
same day. Changes: D10 (profile endpoint, R4, section 5.1), D11 (postseason mechanics, R7, section 5.1), D12 (season-to-date rate
columns, R9), new R18 (displayed score), test-plan rows for each, and a fourth upstream URL builder. Also noted from the fixtures:
preseason blocks (NBA) are ignored. Requirement range is now R1-R18. Needs re-approval before T1 starts.

**A2, 2026-10-24 (approved by the human, 2026-10-24).** Raised by the reviewer (round 2) and the implementer: R16 requires structured data
<= 60 KB but describes only dropping whole seasons, and a single 153-game MLB season cannot fit otherwise. The implementation adds an
in-season row cap. Proposed spec text, documenting what was built:

- **R16 (amended).** If a result's structured data would exceed 60 KB after whole-season trimming, **rows** (never totals) are dropped
  from the largest season block, proportionally and in the minimum number of steps needed, oldest games first, until it fits. Season
  and career **totals are always computed from every game before any row is dropped.** Each affected season records
  `games_omitted` (new field `SeasonLog.games_omitted`); `truncated` is set, and the first text line under the header states how many
  games were omitted and how to see a narrower slice (`season=YYYY`, `season_type=regular`).
- **D7 (clarified).** The text cap (8 KB per season, 20 KB for `season=all`) still cuts the newest rows last-first and states it.
- **Columns (R8/R12 clarification).** Text tables show a stat category's columns only if the player's payload has that category
  (a running back has no Passing columns; a non-defender has no Defense columns); structured data is unaffected.
- **Wording.** The totals label says "computed from all N games" when rows are omitted and "computed from listed games" otherwise.
