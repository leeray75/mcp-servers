# Fix round 1: response to review-round-1.md

Each finding from `review-round-1.md` is verified against the code and marked **fixed**, **accepted** (with justification)
or **rebutted** (with evidence). Nothing is committed. The gate after the fixes is in section 4.

## 1. Majors and blockers

### M1 (major): trimming changed season totals and hid its explanation: FIXED

- Cause: `build(...)` recomputed each season's totals from the rows that survived the trim, and the explanatory note was appended
  last, where the 8 KB `fit()` cut removes it.
- Fix:
  - Totals and careers are computed from every game of each season before any row is dropped
    (`src/sports_mcp/tools.py:906-960`, `build`), and no later step recomputes them.
  - The structured budget drops rows only, through `_cap_games` (`src/sports_mcp/tools.py:1014`). It records the dropped count
    per season as `SeasonLog.games_omitted` (`src/sports_mcp/models.py:361`), keeps `game_count` as the full count, and states
    the omission in `note`.
  - The note is rendered first, directly under the header (`src/sports_mcp/render.py:637`). A season heading says how many
    rows are not listed and that totals cover all games (`src/sports_mcp/render.py:592`, `_season_heading`).
- Tests: `tests/test_player_service.py::test_row_capping_never_changes_season_totals` (Judge 2025: 153 games, totals equal
  the uncapped parse, first text line is the omission note).

### M2 (major): `season_type=all` trimmed the postseason block to "0 of 7", and structured data stayed over 60 KB: FIXED

- Cause: the old loop always trimmed `chosen[-1]`, which is the 7-game postseason block, and gave up at 400 passes.
- Fix: `_cap_games` trims the block with the most rows (the regular season), one proportional step per pass, and each pass
  drops at least one row so the loop ends (`src/sports_mcp/tools.py:1014-1058`). The candidate is sized with its note
  included, so the note cannot push the output over budget (the first version of the fix missed that; caught by the test
  below and corrected).
- Tests: `tests/test_player_service.py::test_season_all_on_one_mlb_season_keeps_postseason_and_stays_in_budget` (MLB 2025,
  `season_type=all`: structured <= 60,000 B, postseason 7 of 7 listed, regular block has `games_omitted`, no "of 7 games" text).

## 2. Minors

### m1 (minor): all-dash Passing and Defense columns for non-QBs and non-defenders: FIXED

- Fix: text columns follow the stat categories the payload actually has (`visible_specs`,
  `src/sports_mcp/normalize/player_stats.py:597`). The derived TO and categoryless (NBA/MLB) columns always stay. The
  union over all seasons in the result decides, so a season-all result keeps a column that any season has
  (`src/sports_mcp/tools.py:906-912`). Structured data is unchanged.
- Consequence: a running back's table has no Passing or Defense columns. This matches R12's "INT ... only for players who have
  a passing category" and is applied to every category.
- Tests: `tests/test_player_service.py::test_passing_and_defense_columns_only_for_players_with_those_categories`.

### m2 (minor): season=all call-count note inconsistent: FIXED

- Cause: the count included two lookups but the breakdown did not.
- Fix: the note now reads exactly "Built from N upstream calls: L lookup, 1 stats, G game logs."
  (`src/sports_mcp/tools.py:906, 922`).
- Tests: `tests/test_player_service.py::test_gamelog_all_seasons_with_career_totals` and
  `test_call_count_note_is_exact_for_season_all` (both assert the exact string).

### m3 (minor): AC2's literal query "Cam Skattebu" had no fixture or test: FIXED

- Verified live first: ESPN returns 0 results for `Cam Skattebu` and 1 result (4696981, Cam Skattebo) for `Cam Skatteb`, so
  the path is the R5 retry on the longest alphabetic token. The `Cam Skatteb` retry was not in the earlier fixtures.
- Fix: captured `tests/fixtures/player_search_cam_skattebu.json` (0 items) and `player_search_cam_skatteb.json` (1 item)
  through `tools/capture_fixtures.py` (`:111-112`), and routed them in `tests/conftest.py:142-143`.
- Tests: `tests/test_player_service.py::test_cam_skattebu_resolves_through_the_literal_retry` (match "fuzzy", id 4696981,
  14 games, 182 CAR, 704 YDS, 6 TD).

### m4 (minor): precedence-weak assertion in a render test: FIXED

- Replaced with concrete rows: `"@PHI"` and `"L 20-38"` (Skattebo 2025 at PHI) in
  `tests/test_player_render.py::test_gamelog_one_season_budget_and_home_away_markers`.

### m5 (minor): totals rows lacked the "computed" wording inside the table: FIXED

- Each season heading now states how its totals were computed: "(N games; totals computed from listed games)", or
  "totals computed from all N games" when rows are omitted (`src/sports_mcp/render.py:592-600`). Career headers keep their
  own wording. The footnote stays as well.
- Tests: `tests/test_player_service.py::test_gamelog_one_season` (heading text) and
  `tests/test_player_render.py::test_nba_playoff_round_label_replaces_the_week` (heading text for the postseason block).

### m6 (minor): truncation notice named parameters that do not exist; `structured.truncated` not set on a text-only cut: FIXED

- The gamelog cut now uses its own notice: "ask for one season (season=YYYY) or season_type=regular"
  (`src/sports_mcp/render.py:651`, `GAMELOG_NOTICE`; `fit` takes the notice as a parameter, `:657`).
- When the text cap cuts the output, the result is marked `truncated: true` before the dump (`src/sports_mcp/tools.py:968-971`).
- Tests: `tests/test_player_service.py::test_text_cut_without_row_drop_sets_truncated` (the notice and the flag agree),
  plus the existing `tests/test_player_render.py::test_one_mlb_season_is_truncated_to_its_budget`.

### m7 (minor): NBA tables indented by a long playoff label: FIXED

- The Wk column shows the round label shortened to 12 characters (`src/sports_mcp/render.py:585-589`, `_short_round`). The
  structured `round` field keeps the full ESPN label.
- Tests: `tests/test_player_render.py::test_nba_playoff_round_label_replaces_the_week` (text "West 1st Ro.", structured
  "West 1st Round - Game 5").

## 3. Reviewer notes on the implementer's deviations

- **Note 1 (R5 word distance): accepted, no code change.** The reviewer agreed that the spec's own example needs the word
  distance. Per the reviewer's request, the reading is recorded here for the spec on merge: `docs/specs/player-stats/SPEC.md`
  is approved text and is not edited in this round.
- **Note 2 (R16 structured trimming): fixed.** See M1 and M2. The trimming now drops rows only, from the largest block, with the
  note first and totals untouched.
- **Note 3 (position absent on name lookups): accepted.** Already disclosed in `README.md` (known limitations) and in the
  implementation report. Nothing further to change.
- **Note 4 (season list, search limit, two stats calls, NBA % columns, pitching): accepted.** Unchanged, documented in the
  implementation report.

## 4. Gate after the fixes

Run in WSL with `make check VENV=~/.venvs/sports-mcp`:

| Step | Result |
|---|---|
| ruff check and format | All checks passed |
| mypy --strict | Success: no issues found in 47 source files |
| pytest | 573 passed, 2 deselected (live) |
| coverage (branch) | 98.40% (gate 90%) |

Net test change: +6 tests (the new M1, M2, m1, m2, m3, m6 tests), with the old trim-related expectations updated to the new
wording.
