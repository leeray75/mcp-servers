# Spec: team rank, ranked-first scoreboard and turnover lines

- **Status:** Approved, 2026-10-24, approved by: human
- **Date:** 2026-10-24
- **Server:** `sports-mcp-server`
- **Amends:** `docs/SPEC.md` section 5 (scoreboard/game/team output) and `docs/specs/player-stats/SPEC.md` is untouched. No invariant from
  `CLAUDE.md` changes: the tool set stays exactly eleven, no new endpoint, no new setting, no new dependency.
- **Companion files:** `tasks.md`, `workflow.md`. Artifacts live in `docs/specs/team-rank-and-turnovers/`.
- **Branch:** `feature/team-rank-and-turnovers`, cut from `main` (PR #2 is merged).

## 1. Problem / motivation

A clean-session Goose test, "current results of all of today's NCAA football games, identify team ranks, order by rank, include leading
players' stats including turnovers", produced a correct report but needed workarounds the MCP should have made unnecessary:

1. **Ranks are not exposed.** The poll rank is the ordering key of the request, yet no tool returned it. Goose probed five ESPN URLs with
   curl (all 400/404) and then scraped ESPN's rankings web page. ESPN already sends it: live college-football scoreboard competitors carry
   `curatedRank.current` (verified 2026-10-24: `MIA` 1, `IU` 6, `TTU` 8, `LSU` 18, `SMU` 23, 19 ranked competitors on a 44-game day) and the
   game-summary header competitors carry `rank`. `TeamLine` already has `record`, and nothing renders either.
2. **"Ranked teams first" needs client-side sorting** over every game.
3. **Turnovers force full boxscores.** `detail=performers` shows a passer's INT only when non-zero and never fumbles, so Goose fetched 12
   boxscores (about 5 KB each) for one column.

## 2. Goals and non-goals

**Goals:** rank on teams where ESPN supplies it (G1); a server-side ranked filter and ordering on the scoreboard (G2); an explicit, compact
turnover line per team in `performers` (G3).

**Non-goals:** a poll/rankings tool, poll metadata (week, release time, points, votes), bulk or inline-leaders scoreboards, snapshot
pinning, JSON output mode, derived fields (upset flags), team ids on scoreboard rows, rank in `get_team_schedule` or `get_news`, rank for
non-college leagues beyond what ESPN already sends, any change to existing totals, size budgets or caching.

## 3. Decisions (made with the human on 2026-10-24)

| # | Question | Decision |
|---|---|---|
| P1 | Scope | Rank + record on teams; scoreboard `ranked_only` and `sort`; INT + fumbles lost in `performers`. Team ids on scoreboard rows: **not** included. |
| P2 | Tools showing rank | `get_scoreboard`, `get_game`, `find_team` (schedule unchanged). |
| P3 | Text format | `#6 IU` prefix only when ranked, plus structured `rank` (int or absent). |
| P4 | Scoreboard params | `ranked_only` (bool) and `sort="rank"`; default behaviour unchanged. |

Decided by the spec author, flagged for veto at approval:

- S1. Agents: implementer **Haiku 5.5**, reviewer **Sonnet 5.5**, up to 5 rounds (same as the previous two cycles).
- S2. **`find_team` is conditional (see R3).** ESPN's team-detail payload has no rank field (verified for team 2390). The requirement only
  holds if the team directory payload carries one; otherwise `find_team` is unchanged and the report says so. No half-working output.
- S3. Records are shown in scoreboard text only while the result fits the existing 6 KB text budget; rank is never dropped for size.

## 4. Requirements

- **R1. Rank on game teams.** `TeamLine` gains `rank: int | None`. Source: scoreboard competitor `curatedRank.current`; game-summary
  header competitor `rank`. A value is accepted only when it is an integer in 1..25 (ESPN uses 99 and absent for unranked); anything else is
  `None`. `None` is omitted from structured output like every other empty field. Applies to every league that carries the field; no league
  is special-cased.
- **R2. Text rendering.** In `get_scoreboard` lines, and in the `get_game` header, a ranked team renders as `#6 IU` (scoreboard, abbr) or
  `#6 Indiana` (game header, name); unranked teams are unchanged. In scoreboard lines the team's record is added after the abbreviation as
  `IU (6-1)` when `record` is known and the whole text stays within the 6 KB scoreboard budget; otherwise records are dropped (rank stays)
  and the result `note` says "records omitted to fit the size budget". Existing score text and column layout are otherwise unchanged.
- **R3. `find_team`.** If, and only if, ESPN's team directory (or the team payload already fetched for `find_team`) carries a poll rank,
  `TeamMatch` gains `rank` and the text shows `#6` before the name. T0 decides this from a real capture and records the result in
  `upstream-notes.md`. If no rank is available, `find_team` is not changed and the implementation report states "R3: not applicable, upstream
  has no rank". The tool set and `find_team` arguments never change.
- **R4. `ranked_only`.** `get_scoreboard(ranked_only: bool = False)`. When true, only games where at least one team has a rank (R1) remain.
  `count`, the status counts in the header and the `ids:` line describe the filtered list. If the filter removes everything the result says
  so in `note` ("no ranked teams play on this date"), not as an error. In leagues where ESPN sends no ranks (e.g. NFL), the result is empty
  with a `note` stating that this league's scoreboard carries no ranks.
- **R5. `sort`.** `get_scoreboard(sort: "time" | "rank" = "time")`. `"time"` is today's order (unchanged). `"rank"` orders by the better
  (lower) rank of the two teams, then by the other team's rank, then by start time; games with no ranked team follow in start-time order.
  Combines with `ranked_only` and `status`. An invalid value is an `InvalidInputError` naming the allowed values (same style as `status`).
  The `limit` is applied **after** filter and sort.
- **R6. Turnover line in `performers`.** For football, `detail="performers"` and `detail="boxscore"` add, under each team's performers, one
  line `TO: <n> (INT thrown <n> [players], fumbles lost <n> [players]); takeaways: <n>` built from the boxscore tables the endpoint already
  returns. Always shown for football when box score data exists, including zeros (`TO: 0`), so an agent can tell "none" from "not reported".
  Player names are limited to those with a non-zero value; `takeaways` counts interceptions made plus fumbles recovered from the opponent.
  If the needed tables are missing, the line is omitted and `performers` budget is unchanged. Non-football sports are unchanged.
- **R7. Descriptions.** `_DESC_SCOREBOARD` documents `ranked_only` and `sort="rank"` and that ranks are ESPN's poll rank (1-25) where the
  league supplies it; `_DESC_GAME` mentions the turnover line. Both keep the untrusted-content note. A short footnote on the scoreboard text
  says `#n = poll rank (ESPN), shown only for ranked teams` when at least one rank is shown.
- **R8. No behavioural regression.** Existing outputs for unranked games, other sports and all other tools are byte-identical except where a
  requirement above says otherwise. Size budgets (scoreboard text <= 6 KB, structured <= 25 KB, game summary <= 8 KB, performers <= 12 KB)
  hold for the real fixtures. The tool set is still exactly eleven; no new endpoint, host, setting or dependency.

## 5. Design

| File | Change |
|---|---|
| `src/sports_mcp/models.py` | `TeamLine.rank`; `TeamMatch.rank` if R3 applies. |
| `src/sports_mcp/normalize/common.py` | `team_line`: parse `curatedRank.current` / `rank` through one helper `poll_rank()` (1..25 else `None`). |
| `src/sports_mcp/normalize/summary.py` | Game header teams get the rank (R1); turnover line input (R6). |
| `src/sports_mcp/normalize/players.py` | Football turnover aggregation for R6. |
| `src/sports_mcp/normalize/teams.py` | `find_team` rank if present (R3). |
| `src/sports_mcp/render.py` | `score_text` / `game_line` / `_team_header` rank and record (R2); turnover line (R6); footnote (R7). |
| `src/sports_mcp/tools.py` | `ranked_only`, `sort` handling in `get_scoreboard`, applied before `limit` (R4, R5). |
| `src/sports_mcp/server.py` | New parameters and descriptions (R4, R5, R7). |
| `tests/`, fixtures | Capture a ranked college-football scoreboard and summary (T0); new tests per requirement; update affected assertions. |
| `README.md`, `CHANGELOG.md`, `docs/SPEC.md` pointer | Document the parameters and output changes. |

## 6. Security and invariants

No new input beyond one boolean and one enum validated against a fixed set; neither reaches a URL. Rank is parsed to an integer in 1..25;
team and player names in the turnover line are third-party text already rendered elsewhere and remain data, not instructions. Logging,
caching, host allow-list and the eleven-tool pin are untouched. No new upstream request.

## 7. Test plan and acceptance criteria

| Req | Verification |
|---|---|
| R1 | Normalizer tests on the ranked-college fixtures: `MIA` 1, `IU` 6; unranked (absent, 99, 0, string, 26) gives `None`; structured omits the key. |
| R2 | Render tests: ranked and unranked mix, `#6 IU`; `get_game` header; records shown when they fit and dropped with the note when forced over 6 KB; score and status text unchanged for unranked games. |
| R3 | T0 fixture decides; test asserts rank when present, otherwise a test pins that `find_team` output is unchanged. |
| R4 | Service tests: filter keeps only games with a ranked team; counts and `ids:` follow the filtered list; empty result note; NFL fixture gives the "no ranks" note. |
| R5 | `sort="rank"` order on the fixture (best rank first, tie by other rank, unranked after, in time order); combined with `ranked_only`, `status` and `limit` (limit after sort); invalid value rejected. |
| R6 | Football fixtures: INT-thrown and fumble-lost players listed, zeros shown as `TO: 0`, takeaways counted, missing tables omit the line; non-football untouched; performers <= 12 KB. |
| R7 | Description test: contains `ranked_only`, `sort`, "poll rank" and the untrusted note; footnote present only when a rank is shown. |
| R8 | Existing suite passes with assertions updated only where a requirement changes output; size-budget tests green; tool-set pin = eleven. |

- AC1. `make check` passes (ruff, `mypy --strict`, pytest, coverage >= 90%).
- AC2. Every R1-R8 row has a passing, non-vacuous test.
- AC3. After rebuilding the container (only with the human's approval), `scripts/smoke.sh` passes and a live
  `get_scoreboard(league="ncaaf", date=today, ranked_only=true, sort="rank")` lists ranked games first with `#n` prefixes.

## 8. Out of scope

A `get_poll` tool, poll week or release time, AFCA vs AP selection (the rank is whatever ESPN's `curatedRank` carries), rank in
`get_team_schedule`, bulk/inline leaders, snapshot pinning, JSON mode, derived fields, hockey/soccer allow-lists.

## 9. Open questions

None. The one upstream unknown (R3) is resolved by T0 and handled by the "no half-working output" rule.

## 10. Amendment log

**A1, 2026-10-24 (decided and approved by the human).**

- **R4 (amended).** `League` in `leagues.py` gains a boolean `has_poll_ranks` (true for `ncaaf`, `ncaam`, `ncaaw`, and any other college
  league in the registry; false for the pro leagues). The empty `ranked_only` note is chosen by that flag, not by inspecting the payload:
  flag false -> "This league's scoreboard carries no ranks."; flag true and no ranked game -> "No ranked teams play on this date.".
  `curatedRank` 99 stays "unranked" under R1 in every league. A test pins the flag for every league in the registry.
- **R8 (amended).** The structured scoreboard budget of 25 KB is measured on the existing scoreboard fixtures, as before. The real 44-game
  college-football day was already 34,293 bytes before this change and is 36,011 bytes after (+1.7 KB from rank/record), so it is a
  pre-existing overage that this cycle does not fix. The new college fixture asserts the 6 KB text budget (5,076 bytes) and a loose 40 KB
  structured guard. Trimming structured output is out of scope.
- **R6 (clarified, from the implementer's interpretations):** `takeaways` = the team's interceptions made + the opponent's fumbles lost;
  the TO line appears only when player stats are game stats (never for pre-game season totals); zero counts render as
  `TO: 0 (INT thrown 0, fumbles lost 0); takeaways: 0`; the line follows each team's performer rows or box-score groups.
