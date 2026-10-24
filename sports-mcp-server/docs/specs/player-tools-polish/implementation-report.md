# Implementation report: player tools polish

- **Spec:** `docs/specs/player-tools-polish/SPEC.md` (Status: Approved, 2026-10-24, approved by: human)
- **Branch:** `feature/player-tools-polish` (not committed, not pushed)
- **Implementer:** subagent, Haiku 5.5. The reviewer and fix rounds are not part of this report.

## What was built

- **R1 (descriptions):** `get_player_gamelog` and `get_player_stats` say a player given by name works directly, and
  `get_player_gamelog` says `season=all` is the whole career, up to `PLAYER_MAX_SEASONS`, with omissions stated in the
  result. "capped" and "most recent seasons" are gone. The untrusted-content note stays.
- **R2 (completeness):** every resolved game log has one `Completeness:` line directly under the header, before any
  note. It reads `complete - N games (year: n, ...), <label>, nothing omitted.` or `PARTIAL - N games listed (...); omitted:
  ...; call with season=YYYY to see them.` Structured data has `completeness` (status, games, by_season, omitted_seasons,
  omitted_rows). Counts are recomputed after the structured row cap, so they match the rows actually listed.
- **R3 (no-recorded-stats games):** a game whose allow-listed stats are all zero, a `0-0` pair, or missing stays in the
  table and in `game_count`. Its season gets the note `No recorded stats (played; ESPN gives no reason): Wk 6 vs NO
  (10/18).`, and its row gets `no_stats: true`. The note gives no reason.
- **R4 (missing marker, amended by A1):** text cells that are missing or season-to-date render `n/a`. A real negative
  keeps its minus (`-3`, `-1.5`). `PLAYER_FOOTNOTE` says `'n/a' means missing or season-to-date, not per game.` Structured
  totals whose inputs were all missing are JSON `null` (were `"-"`); structured data has no `"-"` or `"n/a"` string.
- **R5:** no change to totals, ordering, budgets, caching or the tool set. The Skattebo reference numbers are asserted
  and pass (14 games, 182 CAR, 704 YDS, 3.9 AVG, 6 TD, 5 fumbles, 2 lost, 2 turnovers).

## Traceability

Each requirement and acceptance criterion is traced in `tasks.md` (the completed traceability table). Summary:

| Req | Implementation (file::symbol) | Tests (tests/...) |
|---|---|---|
| R1 | `server.py::_DESC_GAMELOG`, `_DESC_PLAYER_STATS`, `SeasonArg` | `test_player_polish.py::test_r1_descriptions_say_whole_career_and_name_works_directly` |
| R2 | `normalize/player_stats.py::completeness_of`; `models.py::Completeness`; `tools.py::get_player_gamelog` (build, text-cap path), `_cap_games`; `render.py::_completeness_line` | `test_player_polish.py::test_r2_*` (7 tests); `test_player_service.py::test_row_capping_never_changes_season_totals` (layout updated, see deviation D4) |
| R3 | `normalize/player_stats.py::no_recorded_stats` (in `parse_gamelog`); `models.py::GameLogRow.no_stats`; `render.py::_season_lines`, `_no_stats_item` | `test_player_polish.py::test_r3_*` (4 tests) |
| R4 | `render.py::_na`, `_mm_dd`, `_game_cells`, `_log_totals_cells`, `_stat_row_cells`, `PLAYER_FOOTNOTE`; A1: `normalize/player_stats.py::compute_totals` and `_sum_totals` return `None` (JSON null) for all-missing totals | `test_player_polish.py::test_r4_*` (6 tests, including `test_r4_structured_data_has_no_placeholder_strings`, which walks the whole structured payload for `-` or `n/a`); `test_player_normalize.py` (11 structured `"-"` assertions changed to `None`, A1); `test_player_render.py::test_mlb_gamelog_hides_season_to_date_cells_and_recomputes_avg` (placeholder assertion updated, see D4) |
| R5 | Unchanged logic; text and new fields only | `test_player_polish.py::test_r5_skattebo_reference_numbers_are_unchanged`; `test_player_service.py::test_gamelog_all_seasons_with_career_totals`; `test_server.py::test_registered_tool_set_is_exactly_the_spec`; `test_player_render.py::test_one_mlb_season_is_truncated_to_its_budget`; `test_player_service.py::test_single_large_season_stays_within_the_structured_budget` |
| AC1 | `make check` | Final run: lint OK, mypy --strict OK, 595 passed, coverage 98.32% (gate 90%), after review round 1 fixes |
| AC2 | All rows above | Each R1-R5 row has at least one passing, non-vacuous test |
| AC3 | Live `scripts/smoke.sh` after container rebuild | **NOT RUN.** The rebuild needs the human's approval. |

## Deviations and interpretations (for the reviewer and the human)

- **D1. Text-cap cut marks the line PARTIAL. Resolved: approved as A1 (R2 amended).** When the text size cap cuts the
  newest rows, the line says PARTIAL and adds `newest rows cut by the text size cap`. Structured
  `completeness.status` is `partial`; a text-only cut has no per-row count, so `omitted_rows` is not set for it.
- **D2. Structured all-missing totals `"-"` changed to JSON `null`. Resolved: approved as A1 (R4 amended).** Behaviour
  change, deliberate. `compute_totals` and `_sum_totals` return `None`; the renderer maps `None` and missing cells to
  `n/a`; negative numbers are unchanged. Existing tests that asserted structured `"-"` now assert `None`.
- **D3. Unresolved game logs have no completeness line.** An unresolved response has no game log to describe, so I read
  R2's "always" as applying to resolved results.
- **D4. Two existing assertions were changed for reasons outside the "placeholder only" rule. Accepted by the human.** (a)
  `test_player_service.py::test_row_capping_never_changes_season_totals` looked at line 1 for the omitted-rows note. R2
  puts the completeness line directly under the header, so the note is now line 2. (b)
  `test_player_render.py::test_mlb_gamelog_hides_season_to_date_cells_and_recomputes_avg` looked for a line starting with
  `Wk`. One MLB game has no week and no round, so its Wk cell is now `n/a`, which widens the column and right-aligns
  the header. The lookup now uses `lstrip()`. Both changes follow directly from the spec's own requirements.
- **D5. `n/a` applies to every player text table, not only the game log.** `get_player_stats` tables and the Wk/Team
  cells use the same marker, so that `-` means only a minus sign everywhere in player output. The footnote is printed only
  under the game log. `get_player_stats` has no footnote, so its `n/a` is not explained in-line.
- **D6. `docs/SPEC.md` section 5 not edited.** The spec's header lists it as amended, but the design table does not list
  it as a file to change. I followed the design table (README, CHANGELOG, player-stats spec pointer).

## Commands run and results

Run in WSL with `~/.venvs/sports-mcp` from the server directory.

1. `make check VENV=~/.venvs/sports-mcp` (baseline, before any edit): lint OK, mypy OK, **573 passed**, 2 deselected,
   coverage 98.40%.
2. `make check` after the source edits: `ruff check` OK; **2 failed** (`test_mlb_gamelog_hides_season_to_date_cells...`
   placeholder assertion; `test_row_capping_never_changes_season_totals` layout assertion), 571 passed. Both were updated
   as described in D4.
3. Two temporary probe tests (`tests/test_zz_scratch_probe.py`, run with `pytest -s --no-cov`) printed real renders to
   confirm the spec's expected strings (Skattebo `season=all`, `2025`, Judge 2025). Both were deleted before the final
   gate; the file is not in the repo.
4. `make check`: `ruff format --check` failed on one line of the new test file. Fixed with `make format`.
5. `make check`: **3 failed** in the new tests: a Darnold profile missing from that module's fixtures (switched to
   Skattebo), the R4 total-line assertion (only AVG is computed, so the line ends `0.330 n/a n/a n/a`), and the MLB
   header lookup (D4b). All fixed.
6. `make format` then `make check` (first pass after the source edits): `ruff check` OK, `ruff format` clean, `mypy --strict`
   OK (48 files), **592 passed, 2 deselected** at that point, coverage 98.31% (gate 90%); superseded by entry 9 below.
7. A1 round (D2 structured `null`): `make check` after the change to `compute_totals`/`_sum_totals`, the models and the
   tests: first run **1 lint error** (E501 on a comment in `models.py`, fixed), then **1 mypy error** (a `.count` on a
   now-optional total in `test_player_normalize.py`, replaced by a `re.fullmatch` pair check), then **1 failed test** (my
   new R4 test looped over `season=all` for the MLB player, who has no fixture for that; removed the case).
8. Final `make check VENV=~/.venvs/sports-mcp` after the A1 round: `ruff check` OK, `ruff format --check` OK (73 files),
   `mypy --strict` OK (48 files), **595 passed, 2 deselected**, coverage **98.32%** (gate 90%), exit 0.

Not run: AC3 (container rebuild and `scripts/smoke.sh`), by design, pending the human's approval. No `.env` value was read or
printed. No commit, no push. SPEC.md was not modified.

## Open items

- AC3 smoke check after a rebuild, with the human's approval.
- Live ESPN was not called; all tests use the fixtures.

## Review round 1 fixes

Reviewer: Sonnet 5.5, verdict APPROVED with 0 blockers, 0 majors, 4 minors (`review-round-1.md`). Each minor is fixed; the
record is in `fix-round-1.md`.

9. `make format` then `make check` after the review fixes: `ruff check` OK, `ruff format --check` OK, `mypy --strict` OK (48
   files), **595 passed, 2 deselected**, coverage **98.32%** (gate 90%), exit 0.
