# Implementation report: player stats tools

- **Spec:** `SPEC.md` (approved 2026-10-24, amendment A1 in section 11; requirements R1-R18, acceptance AC1-AC6).
- **Branch:** `feature/player-stats-tools`. Nothing committed, pushed or tagged.
- **Scope:** tasks T0-T10 in `tasks.md`. Tool set 8 -> 11 (`find_player`, `get_player_stats`, `get_player_gamelog`).
- **Gate (last run):** `make check` passes: ruff lint and format, `mypy --strict` on 47 files, pytest **573 passed, 2 deselected**
  (live), branch coverage **98.40%** (gate 90%). The pre-feature baseline was 435 passed.

## 1. Traceability (requirement -> implementation -> test)

Implementation paths are under `src/sports_mcp/`. Test paths are under `tests/`; `pls` = `test_player_service.py`,
`pn` = `test_player_normalize.py`, `pr` = `test_player_render.py`, `ps` = `test_player_server.py`, `pc` = `test_player_client.py`,
`pcfg` = `test_player_config.py`.

| Req | Implementation (file::symbol) | Test (file::name) |
|---|---|---|
| R1 find_player | `tools.py::SportsService.find_player`, `normalize/player_stats.py::parse_search`, `rank_for_listing`, `models.py::PlayerSearchResult` | pls::test_find_player_exact_single_match, pls::test_find_player_empty_result_is_not_an_error, pls::test_find_player_limit_is_applied; pn::test_parse_search_keeps_only_the_requested_league, pn::test_rank_for_listing_exact_first_then_order_and_fuzzy_by_distance; pr::test_find_player_text_fits_the_two_kilobyte_budget |
| R2 get_player_stats | `tools.py::get_player_stats`, `normalize/player_stats.py::parse_stats`, `render.py::render_player_stats`, `_stat_block_lines` | pn::test_stats_skattebo_rows_and_espn_career_totals_unmodified, pn::test_stats_traded_season_has_a_combined_row; pls::test_stats_regular_rows_and_espn_career_totals, pls::test_stats_postseason_and_all; pr::test_stats_text_shape_and_budget |
| R3 get_player_gamelog | `tools.py::get_player_gamelog`, `normalize/player_stats.py::parse_gamelog`, `career_log`, `render.py::_season_lines`, `_career_lines` | pn::test_gamelog_skattebo_2025_matches_espn_and_spec, pn::test_gamelog_skattebo_2026_includes_the_all_zero_game; pls::test_gamelog_one_season, pls::test_gamelog_all_seasons_with_career_totals; pr::test_gamelog_text_groups_and_career_line |
| R4 resolution (id via profile, name via search) | `tools.py::_resolve_player`, `_player_name`; `normalize/player_stats.py::resolve`, `parse_profile`; `espn_client.py::player_profile_url` | pls::test_id_lookup_uses_profile_for_name_team_and_position, pls::test_name_lookup_does_not_call_the_profile, pls::test_ambiguous_name_is_unresolved_with_candidates, pls::test_unknown_athlete_id_is_not_found, pls::test_unresolved_gamelog_lists_candidates; pn::test_resolve_single_exact_and_unresolved, pn::test_parse_profile_for_skattebo_and_lebron; ps::test_unknown_athlete_error_does_not_echo_the_id |
| R5 typo tolerance | `normalize/player_stats.py::retry_queries`, `name_distance`, `resolve(fuzzy=True)`, `rank_for_listing`; `tools.py::_search` (max 3 retries) | pn::test_retry_queries_trim_longest_alpha_token_keeping_four_chars, pn::test_name_distance_uses_full_name_or_any_word, pn::test_resolve_fuzzy_auto_resolves_only_when_clear, pn::test_resolve_fuzzy_needs_a_clear_gap_to_the_next_candidate; pls::test_typo_resolves_to_skattebo_as_fuzzy, pls::test_find_player_fuzzy_typo_is_marked; pr::test_find_player_marks_fuzzy_and_empty_results |
| R6 seasons | `tools.py::get_player_gamelog` (season list from stats, `PLAYER_MAX_SEASONS`), `_season_arg`, `_omitted_note`, `_no_games_note`; `normalize/player_stats.py::seasons_played` | pls::test_gamelog_all_respects_player_max_seasons, pls::test_gamelog_empty_season_is_a_note_not_an_error, pls::test_gamelog_season_and_type_validation, pls::test_gamelog_year_bounds_are_accepted_by_the_server; pn::test_gamelog_empty_season_is_not_an_error, pn::test_seasons_played_most_recent_first |
| R7 season_type (A1/D11) | `normalize/player_stats.py::_season_kind` (block by displayName, preseason ignored), `tools.py::_season_type_kinds`, `espn_client.py::player_stats_url` (`seasontype=3` only for `post`), `parse_stats` drops "Postseason" categories for `regular`, `espn_client.py::player_gamelog_url` (no season_type) | pn::test_gamelog_postseason_blocks_and_round_labels_and_r18_score, pn::test_nba_gamelog_flattens_month_blocks_and_ignores_preseason, pn::test_stats_regular_drops_postseason_categories_and_post_keeps_them; pc::test_stats_url_maps_season_type_to_seasontype_3_only_for_post, pc::test_gamelog_url_takes_no_season_type; pls::test_stats_postseason_and_all, pls::test_gamelog_post_and_all_for_darnold |
| R8 label-driven rendering and allow-lists | `normalize/player_stats.py::columns_of` (category slices, not labels), `Spec`, `_FOOTBALL`, `_BASKETBALL`, `_BASEBALL`, `specs_for` (generic fallback); `render.py::_table` | pn::test_columns_of_uses_category_slices_not_labels, pn::test_columns_of_rejects_malformed_maps, pn::test_specs_for_generic_fallback_and_allow_lists, pn::test_gamelog_malformed_payloads_raise_upstream_error, pn::test_stats_nba_and_empty_and_malformed; pls::test_stats_without_any_categories_says_so |
| R9 computed totals, reducers, dashes, season-to-date rates (A1/D12) | `normalize/player_stats.py::compute_totals`, `to_number`, `pair_parts`, `format_number`; MLB `avg` ratio(hits, atBats), `onBasePct`/`slugAvg`/`OPS` reducer none, `season_to_date` flag; `render.py::_game_cells` hides season-to-date cells | pn::test_compute_totals_each_reducer, pn::test_compute_totals_ratio_for_rates_and_missing_inputs, pn::test_to_number_and_pairs, pn::test_gamelog_totals_equal_arithmetic_sums_of_rows (property), pn::test_mlb_rates_recomputed_and_season_to_date_hidden; pr::test_mlb_gamelog_hides_season_to_date_cells_and_recomputes_avg |
| R10 ESPN totals kept distinct | `normalize/player_stats.py::parse_stats` (`career` = ESPN's totals row, not recomputed; `"21,464"` kept as written) | pn::test_stats_postseason_rows_and_comma_totals_kept_as_espn_wrote_them; pn::test_stats_skattebo_rows_and_espn_career_totals_unmodified; pls::test_career_totals_equal_espn_career_and_spec_line (computed career equals ESPN career 182/704/6) |
| R11 games played counts zero-stat games | `parse_gamelog` (`game_count = len(games)`, every event row counted) | pn::test_gamelog_skattebo_2026_includes_the_all_zero_game; pls::test_gamelog_current_season_default (6 games in 2026) |
| R12 football turnovers | `_FOOTBALL` (`passing.interceptions` = INT thrown; `fumbles.fumblesLost`; `derived.TO`; defensive `interceptions.interceptions` = DINT), `_derived_turnovers` | pn::test_turnovers_derived_for_qb_rb_and_defender (QB, RB without passing, defender); pn::test_turnovers_absent_when_no_component_category_present |
| R13 freshness envelope | `tools.py::_meta_for` (oldest `as_of`, max age, `stale`, note with call count for `season=all`), `models.py::Meta` | pls::test_meta_fields_present_on_every_player_result, pls::test_cache_hit_refresh_and_stale_if_error |
| R14 caching and settings | `tools.py::_fetch` via `TTLCache`; search and profile use `ttl_player_search_seconds`; stats and gamelog use `ttl_player_seconds`; `config.py` fields | pls::test_cache_hit_refresh_and_stale_if_error, pls::test_failed_upstream_is_not_cached; pcfg::test_player_settings_defaults, pcfg::test_player_settings_read_from_the_environment, pcfg::test_player_settings_bounds_are_enforced |
| R15 errors | `errors.py` types; `tools.py` not-found text "Athlete not found for league X"; `espn_client.py` 400/404 -> NotFound for id endpoints | pls::test_upstream_errors_do_not_echo_query_or_body, pls::test_unknown_athlete_id_is_not_found, pls::test_player_name_validation_sends_no_request; ps::test_invalid_player_input_is_rejected_before_any_upstream_call |
| R16 size budgets | `tools.py` constants `PLAYER_SEARCH_CHARS` 2000, `STATS_CHARS` 8000, `LOG_SEASON_CHARS` 8000, `LOG_ALL_CHARS` 20000, `STRUCTURED_CHARS` 60000; `get_player_gamelog` drops oldest seasons first (`season=all`), then only game rows, largest block first, if structured data is over budget (`_cap_games`; totals computed before any row is dropped; `SeasonLog.games_omitted`); `render.fit` | pr::test_find_player_text_fits_the_two_kilobyte_budget, pr::test_one_mlb_season_is_truncated_to_its_budget, pr::test_gamelog_one_season_budget_and_home_away_markers, pr::test_stats_text_shape_and_budget; pls::test_gamelog_all_truncates_oldest_seasons_to_fit_the_text_budget, pls::test_single_large_season_stays_within_the_structured_budget, pls::test_stats_text_respects_the_stats_budget |
| R17 text shape | `render.py::_table` (group headings once, aligned columns), `_season_lines` (totals row first), `_career_lines`, `PLAYER_FOOTNOTE` | pr::test_table_aligns_columns_and_repeats_group_headings_once, pr::test_gamelog_text_groups_and_career_line, pr::test_nba_playoff_round_label_replaces_the_week, pr::test_fit_adds_the_truncation_notice |
| R18 displayed score (A1) | `normalize/player_stats.py::_score_and_result` (team id vs homeTeamId/awayTeamId, atVs fallback, player's team first, derived result wins over the upstream letter), `_game_row` | pn::test_r18_shows_the_player_team_first_for_a_loss_with_winner_first_upstream_score (Skattebo vs NO: `L 27-34`), pn::test_derived_result_wins_over_upstream_letter_without_error, pn::test_score_and_result_fallbacks, pn::test_gamelog_postseason_blocks_and_round_labels_and_r18_score (Super Bowl `W 29-13`) |
| AC1 gate | `Makefile check` | `make check` (see section 3) |
| AC2 Skattebo acceptance | `tools.py::get_player_gamelog` (season=all), `normalize/player_stats.py::career_log` | pls::test_gamelog_all_seasons_with_career_totals (14 games; 182 CAR, 704 YDS, 6 TD); pn::test_career_totals_equal_espn_career_and_spec_line; pls::test_typo_resolves_to_skattebo_as_fuzzy |
| AC3 size budgets | constants in `tools.py` (section R16 above) | pr and pls budget tests listed under R16 |
| AC4 tool set and docs | `server.py::create_server` registers 11 tools; docs updated | test_server.py::test_registered_tool_set_is_exactly_the_spec (EXPECTED_TOOLS has 11); ps::test_player_tool_schemas_carry_the_constraints; README, CLAUDE.md, CHANGELOG, `.env.example`, `docs/SPEC.md` pointer updated |
| AC5 live smoke | `scripts/smoke.sh` extended (11-tool check, player calls) | **Not run.** The running container was not rebuilt or restarted in this session (section 4) |
| AC6 no unlisted changes | files listed in section 5 | review of `git status` (section 5) |
| Section 7 invariants | `espn_client.py` (`_digits`, search query regex, host check), `_player_name` | pc::test_search_query_is_restricted, pc::test_profile_url_is_digits_only, pc::test_every_player_url_stays_on_the_configured_host; ps::test_invalid_player_input_is_rejected_before_any_upstream_call |
| T0 fixtures | `tools/capture_fixtures.py` (player entries), `tests/fixtures/player_*.json` (21 files, each under 90 KB) | `upstream-notes.md` (verified findings); fixture-based tests above |

## 2. Deviations and interpretations (for the reviewer)

No requirement was dropped. These are the places where the code goes beyond or interprets the approved text:

1. **R4, position from search.** The search endpoint has no position field (verified on a 10-item search). Name lookups therefore
   return `position` absent; only id lookups (profile) carry a position. R1 says "if known", so this is within the text, but R4
   reads as if search supplies position.
2. **R5, fuzzy distance.** The distance is the smaller of the full folded name and each name word. The spec's own example (`Skattebu`
   -> `Cam Skattebo`, distance 1 to the word, 5 to the full name) cannot resolve against the full name alone. The reviewer should
   confirm this reading.
3. **R16, structured trimming.** The spec says to drop the oldest seasons. A single MLB season (151-160 games) can still exceed
   60 KB of structured data, so the oldest game rows of the largest block are also dropped. Totals and career totals are computed
   from every game before any row is dropped, so they never change. The result carries `truncated: true`, a note placed first in the
   text, and `games_omitted` per season. This is an addition to the spec text. Fix round 1 (M1, M2) corrected the first version,
   which recomputed totals from the surviving rows and trimmed the postseason block (see `fix-round-1.md`).
4. **R8, hockey and soccer.** No fixtures exist, so these families use a generic column list built from the upstream labels
   (reducers chosen by name: rate names -> none, long names -> max, others -> sum), not a curated allow-list. Basketball and
   baseball use allow-lists; football uses its own.
5. **R6, season list.** For `season=all` the list of seasons comes from the regular-season stats call only. A season with postseason
   games but no regular-season row cannot occur in observed data, so this is not expected to matter.
6. **Search limit.** Searches send ESPN `limit=50` because results mix leagues and the league filter runs locally. The caller's
   `limit` (1-10) is applied after ranking. Not visible in the tool surface.
7. **R2 with `season_type=all`.** Two stats calls (regular and `seasontype=3`) are made; the spec does not say how many.
8. **R9 per-game season-to-date cells.** Per-game `avg`, `obp`, `slg` and `ops` are shown as `-` in text rows (hidden), and kept
   in structured rows. Totals show AVG recomputed from H/AB, and `-` for OBP, SLG and OPS, as the spec requires.
9. **NBA percentages.** NBA shooting percentages are not on the text allow-list (structured data only). The spec does not name
   them, and the allow-list must hold only columns the text can total.
10. **Pitching (MLB).** The captured MLB payloads have batting only. Pitching columns are structured data only.

## 3. Commands run and results

Run in WSL with the venv `~/.venvs/sports-mcp`, from the server directory. Git Bash has no `python3`, so helper scripts ran in WSL.

| Command | Result |
|---|---|
| `python3 tools/capture_fixtures.py player_` (T0, live ESPN) | 19 player fixtures captured; largest 87 KB (MLB gamelog) |
| `python3 tools/capture_fixtures.py player_profile` | 2 profile fixtures (Skattebo, LeBron); name, position and team verified |
| `python3 tools/capture_fixtures.py player_search_skatteb` | 1 fixture (retry query for `skattebu`) |
| Fixture checks (python3 in WSL) | Skattebo 2025 101/410/5; 2026 81/294/1; combined career 182/704/6; zero-stat week 6 present |
| `make check VENV=~/.venvs/sports-mcp` (baseline after T0) | ruff fixed one formatting issue in `capture_fixtures.py`; then 435 passed, coverage 98.33% |
| `make check VENV=~/.venvs/sports-mcp` (final) | "All checks passed!"; mypy "Success: no issues found in 47 source files"; **573 passed, 2 deselected**; coverage **98.40%** |
| `pytest tests/test_player_normalize.py --no-cov` | 48 passed (first run) |
| `pytest tests/test_player_service.py --no-cov` | 37 passed (after fixes described in section 4) |
| `pytest tests/test_player_render.py`, `test_player_server.py`, `test_player_client.py`, `test_player_config.py --no-cov` | 13, 17, 12 and 5 passed |
| `ruff format .`, `ruff check --fix .` (several runs) | formatting and import-order fixes only |

## 4. Issues found and fixed during implementation

- Search query `skattebu` ranked the candidate by full name only, which failed the spec's own example. Fixed by the word-distance rule (section 2.2).
- `season=all` returned seasons newest-first. Seasons are now listed chronologically.
- A single large MLB season was 60.5 KB of structured data. Fixed by the trimming rule (section 2.3).
- A season's totals row sat below the rows and was lost when the text was cut. The totals row now comes first in each season.
- The spec's `skattebu` retry needed the `skatteb` search fixture (captured).

## 5. Files changed (AC6)

Source: `src/sports_mcp/config.py`, `espn_client.py`, `models.py`, `render.py`, `resources.py`, `server.py`, `tools.py`,
new `normalize/player_stats.py`. Tests: new `tests/test_player_{normalize,service,render,server,client,config}.py`, updated
`tests/conftest.py` (routes and env names) and `tests/test_server.py` (tool set). Fixtures: new `tests/fixtures/player_*.json`.
Tools and config: `tools/capture_fixtures.py`, `tools/try_live.py`, `.env.example`, `scripts/smoke.sh`. Docs: `README.md`,
`CHANGELOG.md`, `CLAUDE.md`, `docs/SPEC.md` (pointer and tool count), `docs/specs/player-stats/upstream-notes.md`
and this report. Not changed: `.env` (never read), `Dockerfile`, `docker-compose.yml`, `requirements*.txt`, any other server.

## 6. Not done in this session

- **AC5 live smoke.** Not run: the container was not rebuilt or restarted, so the running server is still the previous version.
  The command is `scripts/smoke.sh` after `scripts/rebuild.sh` and `scripts/restart.sh` (no `.env` edit needed).
- No commit, push or tag (as instructed).
