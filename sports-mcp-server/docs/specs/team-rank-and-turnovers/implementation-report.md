# Implementation report: team rank, ranked-first scoreboard and turnover lines

- **Spec:** `docs/specs/team-rank-and-turnovers/SPEC.md` (Approved, 2026-10-24)
- **Tasks:** `tasks.md` (T0-T9). **Branch:** `feature/team-rank-and-turnovers`. Not committed, not pushed.
- **Upstream facts and R3 decision:** `upstream-notes.md`.
- **Status:** gate green (AC1). AC2 met for every requirement; deviations: none. AC3 not run (no container rebuild).

## Deviations

None. Amendment A1 (SPEC.md section 10, approved by the human) resolved the two items reported earlier:
R4's empty-result note now follows `League.has_poll_ranks`, and R8's structured budget is handled as below.

## Observations and clarifications (not deviations)

0. **Structured `turnovers` field (review round 1, minor 4).** `GameDetail.turnovers` is returned in the structured
   output in addition to the text line. It is not named in R6 and is accepted as a documented addition, listed in
   the CHANGELOG entry.

1. **R8 structured budget: pre-existing overage.** The 44-game college-football day (`ncaaf_scoreboard_ranked`) exceeds
   the spec's 25 KB structured budget before this change. Without any rank or record field it is 34,293 bytes; with
   them 36,011 bytes (+1.7 KB from this change). Per A1 the 6 KB text budget (5,076 bytes, records kept) and a loose
   40 KB structured guard are asserted on this fixture, and structured output is not trimmed.
2. **R6 clarifications (A1 item 3), pinned by tests:**
   - takeaways = the team's interceptions made + the opponent's fumbles `LOST` (fumbles recovered from the opponent);
   - the TO line appears only for basis `game`, never for pre-game season totals;
   - zero counts render unbracketed: `TO: 0 (INT thrown 0, fumbles lost 0); takeaways: 0`;
   - the TO line follows each team's performer rows (performers) or box-score groups (boxscore).
3. **R4 empty results (A1).** `has_poll_ranks` is true for `college-football`, `mens-college-basketball` and
   `womens-college-basketball` and false for all other registry leagues (pinned by a registry-wide test). NFL and NHL
   send `curatedRank` with 99 but are not ranked leagues, so NFL gets "This league's scoreboard carries no ranks.".

## Requirement traceability

| Req | Implementation | Test(s) in `tests/test_team_rank.py` (plus the existing suite) |
|---|---|---|
| R1 rank on game teams | `models.TeamLine.rank`; `normalize/common.py` `poll_rank()` (int 1..25, bool/float/str rejected) and `team_line()` reading `curatedRank.current`, falling back to the summary header `rank` | `test_r1_poll_rank_*` (parametrized: 1, 6, 25 kept; None, 0, 26, 99, "6" to None; bools and floats rejected); `test_r1_team_line_*`; `test_r1_summary_header_rank_*`; `test_r1_ranks_on_the_captured_scoreboard` (MIA 1, IU 6, TTU 8, LSU 18, SMU 23, 17 ranked games); `test_r1_structured_omits_absent_rank`; `test_r1_summary_header_carries_rank`; `test_r1_summary_model_reads_header_rank` |
| R2 text rendering | `render.py`: `score_text()` (`#6 IU (6-1) 6`), `game_line()`, `_header_name()` (`#6 Indiana` in the game header), `render_scoreboard()` with `show_records`; `tools.get_scoreboard` drops records when the text exceeds 6,000 bytes and adds `RECORDS_OMITTED_NOTE` | `test_r2_scoreboard_text_prefixes_rank_and_adds_record`; `test_r2_unranked_league_keeps_its_layout`; `test_r2_records_dropped_with_note_when_over_budget` (132 games, ranks kept, records gone, note present); `test_r2_game_header_uses_rank_and_name` |
| R3 find_team | Not applicable: `upstream-notes.md` shows the team directory (762 teams) and the single-team payload carry no rank field. `TeamMatch`, `find_team` and its text are unchanged | `test_r3_find_team_is_unchanged_because_upstream_has_no_rank` |
| R4 ranked_only | `tools.get_scoreboard(ranked_only)` filters before sort and limit; empty-result note chosen by `League.has_poll_ranks` (`leagues.py`, A1) | `test_r4_ranked_only_keeps_games_with_a_ranked_team` (count 17, ids follow the filter); `test_r4_ranked_only_with_no_ranked_games_gives_note`; `test_r4_league_without_rank_field_says_it_carries_no_ranks` (NBA); `test_r4_nfl_gets_the_no_ranks_note`; `test_r4_flag_pins_every_league_in_the_registry` |
| R5 sort | `tools._rank_order()` key `(0, better rank, other rank or 99, start)` for ranked games and `(1, 0, 0, start)` for the rest; `sort` validated against `("time", "rank")` with `InvalidInputError`; applied before `limit` | `test_r5_sort_rank_orders_by_better_rank_then_other_then_time` (first six ids exact, unranked tail in start order); `test_r5_default_sort_is_espn_order`; `test_r5_limit_applies_after_filter_and_sort`; `test_r5_sort_combines_with_status`; `test_r5_invalid_sort_is_rejected` |
| R6 turnover line | `normalize/players.py`: `turnovers()` and `_team_turnovers()` (passing `INT`, fumbles `LOST`, interceptions `INT`; missing table or label omits the line); `models.Turnovers` and `GameDetail.turnovers`; `render.turnover_text()`; `tools.get_game` sets it for football with `basis == "game"` | `test_r6_counts_and_takeaways_from_two_blocks`; `test_r6_missing_table_omits_both_teams_lines`; `test_r6_needs_exactly_two_teams`; `test_r6_performers_show_turnover_line_per_team` (captured Miami game: `TO: 1 (... fumbles lost 1 [Holden Geriner]); takeaways: 0` and `TO: 0 ...; takeaways: 1`); `test_r6_boxscore_shows_turnover_line_under_team`; `test_r6_synthetic_interception_is_listed` (synthetic: one passer's INT set to 2, labelled in the test); `test_r6_pre_game_football_has_no_turnover_line`; `test_r6_non_football_is_unchanged`; performers <= 12 KB asserted |
| R7 descriptions and footnote | `server.py`: `_DESC_SCOREBOARD` (ranked_only, sort, poll rank, untrusted note), `_DESC_GAME` (TO line), new parameters with descriptions; `render.RANK_FOOTNOTE` shown only when a rank is printed | `test_r7_scoreboard_description_documents_ranks_and_keeps_the_note`; `test_r7_game_description_mentions_the_turnover_line`; `test_r7_schema_exposes_new_arguments_and_eleven_tools`; `test_r7_footnote_only_when_a_rank_is_shown` |
| R8 no regression, budgets, tool set | Only the changed outputs differ (records and ranks in scoreboard text, rank key on ranked teams, TO lines). Tool set still eleven. No new endpoint, host, setting or dependency. Two existing assertions updated (see below) | `test_r8_scoreboard_text_budget_holds_on_the_ranked_board` (text 6,000 bytes or less; structured under a 40 KB guard, see observation 1); `test_r8_summary_budget_holds_on_the_ranked_game` (8,000 bytes or less); existing budget tests (`test_nfl_scoreboard_budget`) still pass |
| AC1 | `make check` | Passed (see commands) |
| AC2 | Every row above has at least one passing, non-vacuous test | 43 cases in the new module |
| AC3 | `scripts/smoke.sh` and live `get_scoreboard(... ranked_only, sort="rank")` after a rebuild | **Not run.** Container not rebuilt (instruction). Live data was checked directly against ESPN during T0 (see below) |

### Existing tests changed (R2 output change only)

- `tests/test_round1.py::test_r1_13_limit_can_raise_the_default_cap` and `tests/test_tools.py::test_huge_scoreboards_are_capped_with_a_note`:
  the NFL boards of 150 and 100 games now exceed 6 KB with records, so they carry `RECORDS_OMITTED_NOTE`. The assertions
  were `"note" not in ...`; they now assert that exact note. The cap behaviour itself is unchanged.
- `tests/conftest.py`: routes for the four new `ncaaf_*` fixtures (no existing route changed).

## Fixtures (T0)

| Fixture | Size | Notes |
|---|---|---|
| `ncaaf_scoreboard_ranked` | 259 KB | 44 games, 17 with a ranked team. `leaders` dropped per game (not read by the scoreboard reader); first capture was 309 KB |
| `ncaaf_summary_ranked` | 34 KB | Miami game 401858274 (in progress at capture): header `rank` MIA 1 |
| `ncaaf_team_miami` | 1 KB | Single-team payload (no rank field) |
| `ncaaf_teams_directory` | 203 KB | All 762 teams, untrimmed to 60 (the name does not match the `_teams` cut rule) |

## Commands run and results

Environment: Git Bash for file access; WSL Ubuntu for Python tools (`/mnt/c/...`, venv `~/.venvs/sports-mcp`, Python 3.12.14).
Scratch scripts lived outside the repo (`C:\Users\hoyen\t0scratch`) and were not committed. No `.env` file was read or printed.

| # | Command (abridged) | Result |
|---|---|---|
| 1 | `git status`, `git branch --show-current` | On `feature/team-rank-and-turnovers`; only the spec folder untracked at start |
| 2 | Fetch the five raw ESPN payloads (scoreboard, teams directory, team 2390, two summaries) via a stdlib script | All returned (724 KB, 1.86 MB, 17 KB, 434 KB, 377 KB). Rank keys inspected with a stdlib script |
| 3 | `python tools/capture_fixtures.py ncaaf_scoreboard_ranked` (and the other three names) | Written. Scoreboard re-captured after dropping `leaders` (309 KB to 259 KB) |
| 4 | `make check VENV=~/.venvs/sports-mcp` (1st run) | ruff E501 on two lines; ruff format on two files; mypy: `Need type annotation` in one test; 2 existing assertions failed because the NFL boards now carry the records note. All fixed (see "Existing tests changed") |
| 5 | `make check VENV=~/.venvs/sports-mcp` (first run after A1) | `All checks passed!`; mypy: `Success: no issues found in 49 source files`; pytest: **640 passed, 2 deselected** (live tests); coverage **98.35%** (gate 90%) |
| 6 | Earlier run (before A1) of `pytest tests/test_team_rank.py --no-cov` | 43 passed; the A1 changes add the registry-flag, NFL and R6 placement tests |
| 7 | Structured size probe (scratch test, deleted afterwards) | Ranked day: 36,011 bytes structured; 34,293 without rank or record (observation 1) |

Not run: `make audit` (no dependency change), `pytest -m live` (opt-in, network), container rebuild and `scripts/smoke.sh` (AC3, excluded by instruction).

## Changed files

- Source: `src/sports_mcp/models.py`, `normalize/common.py`, `normalize/players.py`, `normalize/scoreboard.py`, `render.py`, `server.py`, `tools.py`.
- Tests: `tests/test_team_rank.py` (new), `tests/conftest.py` (routes), `tests/test_round1.py`, `tests/test_tools.py` (one assertion each).
- Fixtures: `tests/fixtures/ncaaf_scoreboard_ranked.json`, `ncaaf_summary_ranked.json`, `ncaaf_team_miami.json`, `ncaaf_teams_directory.json`.
- Tools: `tools/capture_fixtures.py` (four plan entries; `leaders` drop for the scoreboard fixture only).
- Docs: `README.md`, `CHANGELOG.md` (`[Unreleased]`), `docs/SPEC.md` (pointer), `docs/specs/team-rank-and-turnovers/upstream-notes.md`, this report.
- Not touched: dependencies, `pyproject.toml`, Docker and scripts, `.env`.

## Summary for the orchestrator

Amendment A1 applied: `has_poll_ranks` flag, `carries_ranks` removed, registry-wide flag test, R6 clarifications pinned, R8 observation recorded. Deviations: none. Gate green (642 passed, coverage 98.35%). Not committed.

## Summary for the orchestrator (earlier)

(superseded by the A1 summary above)
