# Fix round 2: response to review-round-2.md

Review round 2 verdict: APPROVED, with three minors. Each minor is fixed below. SPEC.md is not edited; amendment A2
(section 11, draft) awaits human approval and documents the row cap and `games_omitted` that the implementation already has.
Nothing is committed.

## n1 (minor): the text cut removes the newest rows and later blocks, and the notice advised the wrong narrowing: FIXED (notice)

- Finding verified: `season=2025` on the Judge log cuts the text at its end, and with `season_type=all` the postseason block is
  the part lost. The old notice said "ask for one season (season=YYYY) or season_type=regular", which is wrong advice for a lost
  postseason block.
- Decision: the cut order is kept. The text cap cuts the newest rows last-first and the notice says so, as draft A2 (D7) describes.
  Reordering the text to keep the postseason would change the layout, and the spec's wording for the text cap is not part of the
  row-cap change.
- Fix: the notice now says what is cut and which request shows it: "text cut to fit the size cap: the newest rows and any later
  blocks are not shown. Ask for season=YYYY, or season_type=post for the playoffs alone."
  (`src/sports_mcp/render.py`, `GAMELOG_NOTICE`).
- Tests: `tests/test_player_render.py::test_one_mlb_season_is_truncated_to_its_budget` asserts the new notice text and the
  `season=YYYY` hint.

## n2 (minor): the footnote said "computed from listed games" even when rows were omitted: FIXED

- The footnote now matches the headings: "Totals are 'computed from listed games', or 'computed from all N games' where a
  heading says rows were omitted; '-' means missing (or season-to-date, not per game)."
  (`src/sports_mcp/render.py`, `PLAYER_FOOTNOTE`). The season headings already use the same two phrases
  (`render.py`, `_season_heading`).
- Tests: the existing heading assertions in `tests/test_player_service.py` and `tests/test_player_render.py` still pass, and
  `test_season_all_on_one_mlb_season_keeps_postseason_and_stays_in_budget` asserts the omitted-rows heading wording ("computed from all 153 games").

## n3 (minor): the m6 test's if-and-only-if could pass vacuously: FIXED

- Finding verified: LeBron 2025 with `season_type=all` measures 7,839 characters, under the 8 KB text cap, so the "text cut"
  branch was never exercised and the iff held trivially.
- Fix: the test lowers the single-season text cap with `monkeypatch` (`sports_mcp.tools.LOG_SEASON_CHARS = 4000`). The
  premise is then asserted, not assumed: the notice text must appear in the output, `truncated` must be true, and no season may
  carry `games_omitted`, so the cut is text-only.
  (`tests/test_player_service.py::test_text_cut_without_row_drop_sets_truncated`).

## Gate after the fixes

Run with `make check VENV=~/.venvs/sports-mcp` (WSL):

| Step | Result |
|---|---|
| ruff check and format | All checks passed |
| mypy --strict | Success: no issues found in 47 source files |
| pytest | 573 passed, 2 deselected (live) |
| coverage (branch) | 98.40% (gate 90%) |

The test count is unchanged (573). Only assertions and wording changed in this round.
