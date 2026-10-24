# Fix round 1: player tools polish

Responds to `review-round-1.md` (Sonnet 5.5, APPROVED, 0 blockers, 0 majors, 4 minors). Each minor is fixed; none accepted
without a fix. `SPEC.md` was not edited.

| # | Minor | Fix | Where | Test |
|---|---|---|---|---|
| 1 | CHANGELOG said "Structured data is unchanged", contradicting A1; `player-stats/SPEC.md` still said totals are `-` | CHANGELOG now says all-missing structured totals are JSON `null` (A1) and that structured data has no `"-"` or `"n/a"` strings. In `player-stats/SPEC.md`, the R9 totals rules (lines ~120-125), D12 (line ~59), the TO note (line ~136), the pointer (line ~298) now say `n/a` in text and `null` in structured data. The R9 and R12 test-plan rows (lines ~243, ~247) still quote `-` as a historical plan line and were left as written. | `CHANGELOG.md`, `docs/specs/player-stats/SPEC.md` | Existing `test_player_normalize.py` `None` assertions and `test_r4_structured_data_has_no_placeholder_strings` |
| 2 | Text-cap cut said "N games listed" (the structured count) and "call with season=YYYY", useless for a single-season request | After a text cut the count reads "games in the data". The advice depends on the request: `season=all` keeps "call with season=YYYY to see them."; `season_type=all` gets "call with season_type=regular or post to see them."; otherwise "no narrower request returns them; the size budget applies." The generic (non-cut) PARTIAL wording is unchanged | `render.py::_completeness_line` | `test_r2_text_cut_count_and_advice_match_the_request` (single season: no `season=YYYY`; season_type=all: the narrower advice); `PARTIAL_JUDGE_ROWS` updated |
| 3 | `_years_text` rendered `[2015, 2017]` as `2015-2017`, implying 2016 | Years are listed exactly; a range is used only for a truly contiguous run (`seasons 2015, 2017`, `seasons 2015-2016, 2018-2019`) | `render.py::_years_text` | `test_r2_years_text_names_only_the_omitted_years` |
| 4 | `completeness.omitted_rows` was absent, not `{}`, when empty; spec lists both keys | `dump()` serialises `completeness` with `model_dump` after pruning, so `omitted_rows: {}` and `omitted_seasons: []` are always present | `models.py::dump` | `test_r2_structured_completeness_keys_are_always_present`; the two exact-dict tests now include `omitted_rows: {}` |

Not changed, noted for the record:

- `_omitted_note` in `tools.py` (player-stats R6 wording, `seasons 2016-2018`) still uses a min-to-max span for omitted
  seasons. It is pre-existing wording from the player-stats spec, and this round did not touch it.
- The README paragraph over-long line (review nit) was not reflowed; the reviewer marked it no action required.

Gate after the fixes: `make check VENV=~/.venvs/sports-mcp`: ruff OK, `mypy --strict` OK, **595 passed**, 2 deselected,
coverage 98.32% (gate 90%). The container was not rebuilt; AC3 is still not run. Nothing committed.
