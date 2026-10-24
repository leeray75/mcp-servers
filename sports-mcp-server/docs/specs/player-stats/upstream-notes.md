# Upstream notes: ESPN player endpoints (verified 2026-10-24, T0 captures)

Observed with live requests on `site.api.espn.com` (the host this server already allows). `site.web.api.espn.com` is not used
and must not be added. Fixtures under `tests/fixtures/player_*.json` are trimmed real captures (`tools/capture_fixtures.py player_`).

Path prefix for the three player endpoints: `/apis/common/v3`. Results were checked against the fixtures; the findings below
are what the code must rely on.

## Search

`GET /apis/common/v3/search?query=<text>&limit=<n>&type=player` -> `{count, pageIndex, pageSize, pageCount, items:[...]}`.

- Item fields: `id` (athlete id, string), `displayName`, `shortName`, `type` ("player"), `sport` (e.g. `football`, `basketball`,
  `baseball`), `league` (e.g. `nfl`, `college-football`, `nba`, `mens-college-basketball`, `mlb`), `teamRelationships[]` where
  `[0].displayName` is the team name and `[0].core.abbreviation` the team abbreviation. Wrapper keys `uuid`, `guid`, `links`, `image`,
  `headshot`, `leagueRelationships` are not needed.
- **No position field** on search items (checked on every item of a 10-result search). Position is unknown from search.
- Results mix leagues and sports; the server filters client-side to the requested league's `league` value.
- Observed: `query=skattebo` -> 1 item (4696981); `query=skattebu` -> **count 0** (no fuzzy matching upstream; this is why R5 exists);
  `query=smith` with `limit=10` -> 10 items across NFL, college football, NBA, MLB and men's college basketball (fixture
  `player_search_smith.json`).
- **Searching by athlete id returns nothing**: `query=4696981` -> `count 0`. There is no upstream id lookup through search.

## Athlete profile (A1, D10: the fourth URL builder `player_profile_url`)

`GET /apis/common/v3/sports/<sport>/<slug>/athletes/<id>` -> `{athlete:{id, displayName, fullName, position:{abbreviation, name},
team:{displayName, abbreviation, id}, ...}, season, league, ...}`. Verified: Skattebo (`football/nfl`, 4696981) -> 200 with
`displayName` "Cam Skattebo", `position.abbreviation` "RB", `team.displayName` "New York Giants". Basketball `nba/1966` -> 200.
Captured as fixtures `player_profile_nfl_skattebo.json` and `player_profile_nba_lebron.json` (A1 requires them).

## Game log

`GET /apis/common/v3/sports/<sport>/<slug>/athletes/<id>/gamelog[?season=<year>]`.

- **Season year**: `season=2025` for NFL means the 2025 season. For NBA `season=2025` means `2024-25` (the END year, the same convention
  as the stats endpoint's `season.year`).
- **Default (no `season`)** returns the current season (Skattebo, 2026: 6 events). A season with no games returns only
  `{filters:[...]}` (no `names`, `events` or `seasonTypes`): this is the "empty season" case (Skattebo, `season=2015`, fixture
  `player_gl_nfl_skattebo_2015.json`). Treat it as an empty result, not an error.
- Top-level keys: `labels`, `names`, `displayNames`, `categories`, `events`, `seasonTypes`, `filters`, `glossary`. The athlete's name,
  position and team are **not** in the gamelog payload (checked: no `athlete` or `position` key anywhere).

### Columns

- `names` is the unique column key (use it). `labels` repeats (`YDS` appears in both rushing and receiving) and must not be used as a key.
- `categories[]` = `{name, displayName, count}`; the column slice of each category is `count` long and categories are concatenated in
  order. Football: `rushing`(5) + `receiving`(6) + `fumbles`(4) = 15 for Skattebo; `passing`(11) + `rushing`(5) = 16 for Darnold/Maye;
  `tackles`(6) + `fumbles`(5) + `interceptions`(6) = 17 for defenders (Byard, Witherspoon). The defensive interception column is the
  **`interceptions` name under the `interceptions` category**, not under `passing`.
- **Column identity must be (category, name)**, not name alone: a defender's `interceptions` (defensive) and a QB's `interceptions`
  (thrown) are the same name in different categories.
- NBA (`categories` at top level is empty): `names` has 14 columns: `minutes`, `fieldGoalsMade-fieldGoalsAttempted`, `fieldGoalPct`,
  `threePointFieldGoalsMade-threePointFieldGoalsAttempted`, `threePointPct`, `freeThrowsMade-freeThrowsAttempted`, `freeThrowPct`,
  `totalRebounds`, `assists`, `blocks`, `steals`, `fouls`, `turnovers`, `points`.
- MLB: `names` has 16 batting columns: `atBats`, `runs`, `hits`, `doubles`, `triples`, `homeRuns`, `RBIs`, `walks`, `hitByPitch`,
  `strikeouts`, `stolenBases`, `caughtStealing`, `avg`, `onBasePct`, `slugAvg`, `OPS`. (Two-way Ohtani's gamelog shows batting only.)

### Events

- `events` is an object keyed by event id: `{id, week, atVs ("vs" home | "@" away), gameDate (UTC ISO), gameResult ("W"|"L"|"T"),
  homeTeamId, awayTeamId, homeTeamScore, awayTeamScore, score, team:{id, abbreviation, displayName, ...}, opponent:{id, abbreviation,
  displayName}, eventNote?, leagueAbbreviation, leagueName, ...}`. MLB and NBA events have no `week`.
- `eventNote` appears on playoff games: `"NFC Divisional Playoffs"`, `"Super Bowl LX"`, `"West 1st Round - Game 5"` (NBA). Use it as the
  round label.
- **Score perspective quirk (confirmed)**: `score` is not always in the same order. Skattebo 2025 @ PHI: `score "38-20"`, `homeTeamScore 38`,
  `awayTeamScore 20` (home-first). Darnold Super Bowl (SEA, the player, is the away team): `score "29-13"`, `homeTeamScore 13` (NE),
  `awayTeamScore 29` (SEA), so the player's team is shown first. **Derive the display score from `homeTeamScore`/`awayTeamScore` and
  the player's `team.id` (or `homeTeamId`/`awayTeamId`), never from `score`.** `gameResult` is reliable.
- **Order**: event rows inside a category are **most recent first** (verified on Darnold, Judge, Ohtani). Sort ascending by `gameDate`.
- Team per game: `events[id].team` gives the player's team for that game (used for traded players). Traded-player gamelogs were not
  seen as separate team rows; the team per game is the source.

### Postseason (answer to spec item 1)

- **`season_type` is NOT a gamelog query parameter.** `seasontype=2`, `seasontype=3` and `seasonType=3` returned the same response
  (verified on Darnold 2025). Every gamelog response for a season includes **all** season types.
- Postseason is a separate `seasonTypes[]` entry whose `displayName` ends in `Postseason` (`"2025 Postseason"`, `"2024-25 Postseason"`),
  next to `"<YEAR> Regular Season"` (football) or `"<YEAR-YY> Regular Season"` (NBA). Preseason blocks (`"... Preseason"`) also appear
  (NBA: `"2024-25 Preseason"`, 3 games); the spec has no preseason option, so the server ignores them.
- Select a season type by the `displayName` suffix. Do **not** rely on `week`: postseason weeks are small numbers (2, 3, 5 observed)
  that overlap regular-season week numbers.
- Postseason counts observed for season 2025: Darnold 3 (SB, NFC Championship, NFC Divisional), Maye 4, Byard 2, Witherspoon 3,
  Charbonnet 1, Adams 3, Skattebo 0 (NYG did not play in 2025 playoffs).

### Stat strings

- Missing values are `"-"`. Football per-game rows are all integers or decimals (e.g. Skattebo `"-3"` yards, `"-1.5"` average on a
  negative-yard game: negative values are legal).
- NBA shooting cells are composite `"made-attempted"` strings (`"9-21"`, `"8.6-17.6"` in aggregates). Percent cells are plain numbers
  (`"42.9"`). Use the `-` separator for the `pair` reducer.
- **Thousands separators**: ESPN writes some totals with a comma (`"4,048"` passing yards on the season row, `"21,464"` on Darnold's
  career row). Strip commas before parsing; the normalizer must handle this.
- **MLB rates in gamelog rows are season-to-date, not per game.** Ohtani's most recent postseason row shows `avg ".265"` after `hits 2`
  in `atBats 5` (a per-game .400). Do not show or sum MLB gamelog rate columns as per-game values. `avg` can be recomputed as
  `hits/atBats` from components; `onBasePct`, `slugAvg` and `OPS` have no components in the gamelog (use reducer `none`).
- **NBA `totals` inside monthly blocks are per-game averages** (e.g. april block `totals` begin `33.5` minutes). Use the events only.
- Football per-game `completionPct`, `yardsPerRushAttempt` etc. ARE per-game values (e.g. `"76.9"` = 20/26): recomputing from components
  gives the same number.

### Shape by sport (answer to spec item 2)

- **Football (NFL)**: `seasonTypes[].categories[]` has one `type:"event"` category per season type (`displayName`
  "Regular Season Stats" / "Postseason"); its `totals` are the season row ESPN computes.
- **NBA**: each season type has **monthly** `type:"event"` categories (`april`, `march`, ..., or the round names in the postseason,
  e.g. "Conference Quarterfinals", `splitType` "conference_quarterfinals") whose `events` are the game rows; the postseason also has a
  `type:"total"` category (`displayName:"Postseason"`, 0 events, `totals` = postseason total). The regular season has no total
  category. Preseason has one `type:"event"` category with no totals.
- **MLB**: each season type has monthly `type:"event"` categories (`july`, `august`, ...) or a single `Postseason` event category
  (Judge/Ohtani 2025 postseason: 17 rows for Ohtani). No `total` category in the gamelog.

## Stats (season and career)

`GET /apis/common/v3/sports/<sport>/<slug>/athletes/<id>/stats[?seasontype=3]` (~10-25 KB).

- `categories[]` = `{name, displayName, labels, names, displayNames, descriptions, statistics:[...], totals:[...]}`.
  `totals` is ESPN's career row for that category; `statistics[]` = `{teamId, teamSlug, season:{year, displayName}, position, stats:[...]}`.
- **`season.year` is the END year** for NBA (`{year: 2004, displayName: "2003-04"}`) and the football year otherwise. Use `displayName`
  for the label. The distinct `season.year` values are the "seasons played" list for `season=all`.
- **Football**: default response = regular season only (Darnold: 2018-2026 regular rows). `?seasontype=3` = postseason rows only, with its
  own career-postseason `totals` (Darnold: 4 games, 2024 MIN and 2025 SEA rows). Categories present depend on the player (passing,
  rushing, receiving, defensive, scoring, kicking, fumbles...). Defensive categories are absent for an RB who never recorded one.
- **Traded players (answer to spec item 4)**: a season with several teams has one row per team **plus a combined row** with
  `teamSlug` = `"<YEAR> Totals"` (Davante Adams 2024: `"2024 Totals"` next to his team row). Season rows must show the team rows and
  must not double-count the `Totals` row. The Totals row carries no team id.
- **NBA**: default categories `averages` ("Regular Season Averages", per-game), `totals` ("Regular Season Totals") and `miscellaneous`
  ("Regular Season Misc Totals"). `?seasontype=3` renames them "Postseason Averages/Totals/Misc Totals". So NBA season totals can
  come from the `totals` category; averages are per game.
- **MLB**: default categories `career-batting` ("Career Batting"), `postseason-batting` ("Postseason Batting", 3 rows),
  `expanded-batting`, `advanced-batting`. The default response therefore **already contains postseason data** in its own category.
  `?seasontype=3` returns 3 rows per category (postseason only). For `season_type=regular` the server must drop categories whose
  `displayName` contains "Postseason" (this is a server implementation rule, not an upstream flag). Ohtani's stats payload has no
  pitching category in the captured response.
- **Players with no stats**: the gamelog returns `{filters}` only (see above); the stats payload may still have an empty `categories`.
- `GP` (`gamesPlayed`) counts games with zero stats; the Saints game in 2026 (week 6) is counted by ESPN (`gamesPlayed` 14 career).

### Position

- Stats rows carry `position` (e.g. `"RB"` for Skattebo, `"QB"` for Darnold). Search items do not. The athlete profile endpoint carries
  `position.abbreviation`.

## Fixtures captured (T0)

| Fixture | Source | What it covers |
|---|---|---|
| `player_search_skattebo.json` | search `skattebo` | 1 hit, id 4696981 |
| `player_search_skattebu.json` | search `skattebu` | count 0 (R5 retry case) |
| `player_search_skatteb.json` | search `skatteb` | R5 first retry for `skattebu` (1 hit, id 4696981) |
| `player_profile_nfl_skattebo.json` | athlete profile | name, team and position for an id (A1, D10) |
| `player_profile_nba_lebron.json` | athlete profile | name, team and position for an NBA id (A1, D10) |
| `player_search_smith.json` | search `smith`, limit 10 | multi-league results |
| `player_gl_nfl_skattebo_2025.json` | gamelog `season=2025` | 8 regular-season games, no playoffs; sums 101 CAR / 410 YDS / 5 TD |
| `player_gl_nfl_skattebo_2026.json` | gamelog (current) | 6 games incl. week 6 vs NO with all-zero stats |
| `player_gl_nfl_skattebo_2015.json` | gamelog `season=2015` | empty season (`filters` only) |
| `player_stats_nfl_skattebo.json` | stats | season rows 2025 + 2026, career 14/182/704/6 |
| `player_gl_nfl_darnold_2025.json` | gamelog `season=2025` | QB, 17 regular + 3 postseason (SB with `eventNote`), passing + rushing |
| `player_stats_nfl_darnold.json` / `_post.json` | stats / stats `seasontype=3` | regular vs postseason stats, comma-formatted totals |
| `player_gl_nfl_maye_2025.json` | gamelog `season=2025` | QB with no fumble columns (TO = INT only), 17 + 4 games |
| `player_gl_nfl_byard_2025.json` | gamelog `season=2025` | defender: `interceptions` category (7 defensive INT in regular season) |
| `player_gl_nfl_adams_2025.json` | gamelog `season=2025` | WR, no passing category, 14 + 3 games |
| `player_stats_nfl_adams.json` | stats | traded season: `"2024 Totals"` row next to team rows |
| `player_gl_nba_lebron_2025.json` | gamelog `season=2025` (2024-25) | monthly blocks, postseason round blocks + total, preseason block |
| `player_stats_nba_lebron.json` | stats | averages / totals / misc categories |
| `player_gl_mlb_judge_2025.json` | gamelog `season=2025` | MLB monthly blocks + postseason |
| `player_stats_mlb_ohtani.json` | stats | `postseason-batting` category in the default response |

Every fixture is below 90 KB (the largest is the MLB gamelog at ~87 KB).

## Findings and their resolution (amendment A1, 2026-10-24)

1. **Resolved by A1 / D10 (R4)**: R4 requires `player: {id, name, team, position}` for every resolved lookup, including an athlete id. The gamelog
   and stats payloads carry no name, and search does not resolve an id (verified). The only upstream source of the name and
   position for an id is the athlete profile endpoint above, which the spec does not list. The approved spec needs either an
   amendment adding that endpoint (same host, digits-only id) or a change to R4 (id lookups return no name).
2. **Resolved by A1 / D11 (R7, section 5.1)**: ESPN ignores `seasontype` on the gamelog, so `player_gamelog_url(..., season_type)` has
   no upstream effect. Season-type selection has to happen client-side by `displayName` suffix. The stats endpoint does accept
   `seasontype=3`.
3. **Resolved by A1 / D11 and R7 (MLB default stats include a postseason category)** (see Stats). R2's "season-by-season rows" needs a rule to avoid showing
   postseason batting under the regular-season view.

## Reference snapshot (for acceptance tests; as of 2026-10-24)

Cam Skattebo, NFL id 4696981, Giants. 2025: 8 games, 101 CAR, 410 YDS, 5 TD. 2026 (through week 6): 6 games (week 6 vs NO all zeros),
81 CAR, 294 YDS, 1 TD. Career: GP 14, 182 CAR, 704 YDS, 3.9 AVG, 6 TD. Verified against the fixtures on 2026-10-24.
Fixtures are snapshots; tests assert against the fixture, not live data.
