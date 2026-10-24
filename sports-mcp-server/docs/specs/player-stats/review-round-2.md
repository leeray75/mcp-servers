# Review round 2: player stats tools

Reviewer: independent, read-only (fresh context). Spec `SPEC.md` (approved incl. A1). Working tree on `feature/player-stats-tools`, nothing committed.

## Summary

All nine round-1 findings (M1, M2, m1-m7) are genuinely fixed, each with a meaningful test. I re-ran the gate and exercised the Skattebo, Judge,
LeBron and Darnold fixtures offline. No blockers or majors remain. Three new minors; one spec-amendment recommendation (row capping).

## Gate (my own run, WSL, `make check VENV=~/.venvs/sports-mcp`)

| Step | Result |
|---|---|
| ruff check / format | passed |
| mypy --strict | no issues (47 source files) |
| pytest | 573 passed, 2 deselected (live) |
| coverage (branch) | 98.40% (gate 90%); `tools.py` 99%, `normalize/player_stats.py` 98%, `render.py` 97% |

## Round-1 findings: verification

| # | Verified at | Evidence | Test (meaningful?) |
|---|---|---|---|
| M1 | `tools.py` `build()` (~918-945) computes totals/careers from all games of each season; `_cap_games` (~1014-1058) only slices `games`, sets `games_omitted`, keeps `game_count`; `render.py:637` note is line 2 | Judge `season=2025`: 153 games, AB 543, H 179, AVG 0.330, `games_omitted` 2, line 2 = "2 oldest game rows are not listed ...", heading "totals computed from all 153 games" | `test_row_capping_never_changes_season_totals` compares to an independent full parse, asserts `game_count==153`, note regex on line 2. Yes |
| M2 | `_cap_games` picks `max(..., key=len(games))` (regular block), candidate sized with its note | Judge `season_type=all`: 59,765 B structured; regular 143/153 listed, `games_omitted` 10; postseason 7/7 intact | `test_season_all_on_one_mlb_season...` asserts <= 60,000, post whole, regular omitted, no "of 7 games". Yes |
| m1 | `player_stats.py:597` `visible_specs` | Skattebo text: only Rushing/Receiving/Turnovers (FUM LST TO); Darnold: Rushing/Passing/Turnovers INT TO, no Defense | `test_passing_and_defense_columns_only_...` asserts RB has no Passing/Defense and QB has Passing. Yes |
| m2 | `tools.py` calls_note | Skattebo `all`: "Built from 4 upstream calls: 1 lookup, 1 stats, 2 game logs." | exact-string tests. Yes |
| m3 | fixtures `player_search_cam_skattebu/skatteb.json`, conftest routes | literal "Cam Skattebu" resolves (match fuzzy, id 4696981, 14 games, 182/704/6) | `test_cam_skattebu_resolves_through_the_literal_retry`. Yes |
| m4 | `test_player_render.py` | concrete `@PHI` / `L 20-38` assertions | Yes |
| m5 | `render.py` `_season_heading` | heading "(N games; totals computed from listed games)" or "computed from all N games" | asserted in two tests. Yes |
| m6 | `render.py` `GAMELOG_NOTICE`, `tools.py` sets `truncated` on text cut | notice names `season=YYYY` / `season_type=regular` | `test_text_cut_without_row_drop_sets_truncated` (iff-assertion, weak if neither occurs, but the Judge tests cover the cut path). Acceptable |
| m7 | `render.py` `_short_round` (12 chars) | LeBron playoff rows "West 1st Ro."; table no longer indented ~20 spaces | `test_nba_playoff_round_label_replaces_the_week`. Yes |

Skattebo re-confirmed: 14 games (8 in 2025, 6 in 2026), career 182 CAR, 704 YDS, 3.9 AVG, 6 TD; the 2026 `vs NO L 27-34` zero-stat row is kept
and counted; season totals equal row sums (2025: 101/410, 2026: 81/294). Darnold: TO = 14 (INT only, no fumble category), postseason 3 games
incl. Super Bowl `@NE W 29-13`. Judge MLB: AVG recomputed 0.330, OBP/SLG/OPS `-`, per-game cells for them `-`.

## Regression and invariant review

- Invariants hold: exactly 11 `@server.tool` registrations, all `READ_ONLY`; `httpx` imported only in `espn_client.py`; query regex
  `[\w .'-]{1,60}` and digits-only ids unchanged; no new logging in `tools.py` / `player_stats.py`; `security.py`, `cache.py`,
  `logging_setup.py` untouched; failure messages do not echo input.
- Totals correctness after trimming: correct (see M1). `game_count` stays the full count, so the header "153 games" agrees with totals.
- "Hide categories the player lacks" (m1) vs R12 / R8: consistent. R12 says INT shown "only for players who have a passing category", and R8
  only says the allow-list *selects* columns; hiding absent ones is a refinement, with the union across seasons so `season=all` is stable,
  derived TO always kept, structured data unchanged. No amendment needed; mention in the SPEC changelog on merge.
- **Row capping vs R16/D7: needs a spec amendment (recommended, not a violation).** R16 only describes dropping whole oldest seasons for
  `season=all`, yet states "Structured data <= 60 KB" unconditionally. A single 153-game MLB season cannot meet that without dropping rows, so
  the implementation is the minimal honest way to honour R16's hard limit: it only runs when over budget, drops oldest rows of the largest
  block, never changes totals (stronger than R16's "totals cover only the listed seasons"), sets `truncated`, `games_omitted` and a first-line
  note. The new `SeasonLog.games_omitted` field and the in-season capping rule should be added to SPEC as amendment A2 (R16 + D7 + models).

## New issues

| # | Sev | Location | Evidence | Suggested fix |
|---|---|---|---|---|
| n1 | minor | `render.py` `GAMELOG_NOTICE`, `tools.py` text `fit()` | The 8 KB text cap cuts the *end* of the text. Judge `season=2025`: text ends at 07/11 (newest ~45 games missing) while the note/heading only mention "2 oldest rows not listed". With `season_type=all` the whole postseason block (7 games) is missing from the text, and the notice suggests `season_type=regular`, which is the wrong advice for the lost postseason. The text carries a generic cut notice so it is not silent, but a reader may assume rows are missing only at the start. | Cap text rows (oldest first, like the structured cap) so the newest games and the postseason block survive, or make the notice say "latest games and later blocks are cut; use season_type=post / season=YYYY". |
| n2 | minor | `render.py` `PLAYER_FOOTNOTE` | Footnote always says "Totals marked 'computed from listed games'", but with capped rows the heading says "computed from all N games". Slightly inconsistent. | Footnote: "computed from the games in the log (all of the season's games)". |
| n3 | minor | `tests/test_player_service.py::test_text_cut_without_row_drop_sets_truncated` | Asserts an iff that can pass vacuously if the LeBron regular season never cuts. | Assert the cut actually occurred (use `season_type=all`) so the premise is tested. |

Counts: 0 blockers, 0 major, 3 minor. Spec amendment A2 recommended (row capping; `games_omitted`).

Verdict: APPROVED
