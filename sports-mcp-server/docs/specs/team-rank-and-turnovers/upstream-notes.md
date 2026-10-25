# Upstream notes: team rank and turnovers (T0)

- **Captured:** 2026-10-24 (ESPN site API, unofficial). Fixtures are static after capture; ESPN data is live, so
  the ranked games in the fixtures were in progress at capture time.
- **Capture command:** `python tools/capture_fixtures.py <name>` (names below). Trimming follows the existing rules;
  one extra reduction applies to `ncaaf_scoreboard_ranked` (see "Fixture sizes").

## Fixtures

| Fixture | Source | Size | Purpose |
|---|---|---|---|
| `ncaaf_scoreboard_ranked` | `football/college-football/scoreboard?dates=20261024` | 259 KB | 44 games, 17 with a ranked team |
| `ncaaf_summary_ranked` | `football/college-football/summary?event=401858274` (MIA 1 vs PITT, in progress) | 34 KB | header `rank`, football boxscore tables |
| `ncaaf_team_miami` | `football/college-football/teams/2390` | 1 KB | single-team payload used by `find_team` for record/standing |
| `ncaaf_teams_directory` | `football/college-football/teams?limit=1000` | 203 KB | full directory, 762 teams (not truncated) |

## Observed rank fields

- **Scoreboard competitor:** `curatedRank` object with `current`. Ranked teams carry 1..25 (`MIA` 1, `IU` 6, `TTU` 8,
  `LSU` 18, `SMU` 23 on the fixture day). Unranked teams carry `{"current": 99}`. 17 of 44 games have at least one ranked team.
- **Summary header competitor:** `rank` integer (`MIA` 1, `IU` 6 in the IU game). Unranked competitors have no `rank` key
  (`PITT`, `MICH` observed). The header competitor has no `curatedRank` key.
- **Team directory:** scanned all 762 teams; no `rank` or `curatedRank` key on any team object. Team object keys:
  `abbreviation, alternateColor, color, displayName, id, isActive, isAllStar, links, location, logos, name, nickname,
  shortDisplayName, slug, uid`.
- **Single team payload** (`teams/2390`): keys include `record`, `standingSummary`, `groups`, `nextEvent`; no rank key.

## R3 decision

**R3: not applicable, upstream has no rank.** The team directory and the single-team payload carry no poll rank, so
`find_team` is unchanged: no `TeamMatch.rank`, no `#n` in its text. The rank exists only on scoreboard competitors and
summary header competitors. This matches S2 in the spec; the implementation report states the same.

## Football boxscore tables (R6)

`boxscore.players[team].statistics[]`, one entry per category, each with `labels` and `athletes[].stats` (strings):

- `passing`: labels `C/ATT, YDS, AVG, TD, INT`. Index 4 = `INT` (interceptions thrown by that team's passers).
- `fumbles`: labels `FUM, LOST, REC`. Index 1 = `LOST` (fumbles lost by that team), index 2 = `REC` (fumbles recovered by that team).
- `interceptions`: labels `INT, YDS, TD`. Index 0 = `INT` (interceptions made by that team's defence).
- In the captured fixtures, `fumbles` and `interceptions` list only players with a non-zero value; `defensive` lists
  every player. R6 reads only the categories above.

Takeaways interpretation (spec R6 says "fumbles recovered from the opponent"): this is computed as the opponent's
`fumbles` `LOST` total, since every fumble lost by one side is recovered by the other. This avoids counting a team's
own fumble recovery as a takeaway. Recorded as an interpretation in the implementation report.

## Fixture sizes

- `ncaaf_scoreboard_ranked` was 309 KiB with the standard trim, over the 300 KB budget. The scoreboard normalizer does
  not read per-game `leaders` (about 1.2 KB per game), so the capture script drops that block for this fixture only.
  Nothing else in the event or competition is changed.
