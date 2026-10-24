# Tasks and traceability skeleton: player tools polish

| # | Task | Requirements | Files |
|---|---|---|---|
| T1 | Description wording | R1 | `server.py` |
| T2 | No-recorded-stats detection + model fields | R3 | `normalize/player_stats.py`, `models.py` |
| T3 | Completeness structure | R2 | `normalize/player_stats.py`, `models.py`, `tools.py` |
| T4 | Rendering: completeness line, zero-stat note, `n/a`, footnote | R2-R4 | `render.py` |
| T5 | Tests for every requirement; update placeholder expectations only | all | `tests/` |
| T6 | Docs: README, CHANGELOG `[Unreleased]`, pointer in the player-stats spec | - | listed files |
| T7 | Gate and `implementation-report.md` (traceability table) | AC1-AC3 | report |

| Req | Implementation | Test |
|---|---|---|
| R1 | `server.py`: `_DESC_GAMELOG`, `_DESC_PLAYER_STATS`, `SeasonArg` description (T1) | `test_player_polish.py::test_r1_descriptions_say_whole_career_and_name_works_directly` |
| R2 | `normalize/player_stats.py::completeness_of`; `models.py::Completeness`; `tools.py` build + `_cap_games` + text-cap path (T3); `render.py::_completeness_line` (T4) | `test_r2_career_completeness_line_is_exact_and_structured`, `test_r2_single_year_and_current_season_have_the_line`, `test_r2_season_type_label_names_the_blocks`, `test_r2_partial_names_omitted_seasons`, `test_r2_partial_names_omitted_rows_and_the_text_cut`, `test_r2_structured_rows_omitted_by_the_budget_are_counted`, `test_r2_line_survives_a_lowered_text_cap`; `test_player_service.py::test_row_capping_never_changes_season_totals` (layout updated) |
| R3 | `normalize/player_stats.py::no_recorded_stats`, applied in `parse_gamelog`; `models.py::GameLogRow.no_stats` (T2); `render.py::_season_lines`, `_no_stats_item` (T4) | `test_r3_zero_game_is_listed_with_a_neutral_note`, `test_r3_flag_is_on_that_row_only`, `test_r3_no_note_for_a_season_without_zero_games`, `test_r3_detection_rules_on_the_allow_list` |
| R4 | `render.py::_na`, `_mm_dd`, `_game_cells`, `_log_totals_cells`, `_stat_row_cells`, `PLAYER_FOOTNOTE` (T4) | `test_r4_marker_helper_keeps_negatives_and_maps_missing`, `test_r4_season_to_date_and_missing_cells_render_n_a`, `test_r4_no_bare_dash_cell_in_table_rows`, `test_r4_negative_numbers_keep_their_minus_sign`, `test_r4_footnote_explains_n_a`, `test_r4_structured_data_has_no_placeholder_strings` (A1: structured missing totals are null; `test_player_normalize.py` asserts `None`); `test_player_render.py::test_mlb_gamelog_hides_season_to_date_cells_and_recomputes_avg` (placeholder updated) |
| R5 | No change to totals, ordering, budgets, caching or tool set (T1-T4 touch only text and completeness/flag fields) | `test_r5_skattebo_reference_numbers_are_unchanged`; existing `test_player_service.py::test_gamelog_all_seasons_with_career_totals`; `test_server.py::test_registered_tool_set_is_exactly_the_spec`; size-budget tests `test_player_render.py::test_one_mlb_season_is_truncated_to_its_budget`, `test_player_service.py::test_single_large_season_stays_within_the_structured_budget` |
| AC1 | `make check` (ruff, mypy --strict, pytest, coverage gate 90%) | 595 passed, coverage 98.32% (after amendment A1 and review round 1), see `implementation-report.md` |
| AC2 | Every R1-R5 row above | Each row has at least one passing test; list above |
| AC3 | Live Skattebo `season=all` after container rebuild | NOT RUN: rebuild needs human approval |
