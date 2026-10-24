# Review round 1: player tools polish

- **Reviewer:** Sonnet 5.5, fresh context, read-only (only this file written). Spec: `SPEC.md` incl. amendment A1.
- **Scope checked:** `git diff` of src/tests/docs vs `SPEC.md` section 5; offline probes on Skattebo, Darnold, LeBron, Judge fixtures
  (throwaway script outside the repo, deleted).

## Summary

The implementation matches the approved spec. R1-R5, A1 and the Skattebo reference numbers hold; the files touched equal the section 5
table (plus tests and the three docs). Rendered output is clear for a small model, with a few wording/documentation nits.
No blocker, no major issue. 4 minor findings.

## Gate results (run by me, WSL, `make check VENV=~/.venvs/sports-mcp`)

- ruff check / format: OK. `mypy --strict`: OK.
- pytest: **592 passed**, 2 deselected. Coverage **98.31%** (gate 90%).
- Not run (by design): AC3 live smoke after container rebuild (needs the human). No container touched.

## Per-requirement verification

| Req | Result | Evidence |
|---|---|---|
| R1 | OK | `server.py:112-128` (`_DESC_PLAYER_STATS`, `_DESC_GAMELOG`) say "whole career (up to PLAYER_MAX_SEASONS)", "result states explicitly if any seasons or games were omitted", "works directly; find_player only to browse". `SeasonArg` at `server.py:149-155` likewise. `UNTRUSTED_NOTE` kept. grep: no "capped"/"most recent seasons" in descriptions. Test `test_player_polish.py::test_r1_*` reads real registered descriptions, asserts presence and absence, non-vacuous. |
| R2 | OK (see minors) | `render.py::_completeness_line`, called in `render_player_gamelog` as `lines[1]`, before the note. Probe output: Skattebo all = `Completeness: complete - 14 games (2025: 8, 2026: 6), regular season only, nothing omitted.` (exact spec string); `2026` and `current` give the line; Darnold `season_type=all` gives "regular season and postseason"; post-only gives "postseason only". PARTIAL for `PLAYER_MAX_SEASONS=1` names `season 2025`; Judge row cap gives `2 oldest rows of 2025`. Text-cap path `tools.py:968-981` re-renders with `text_cut=True` and flips structured status to partial (A1); test lowers the cap and asserts line 2 survives (`test_r2_line_survives_a_lowered_text_cap`). `_cap_games` (`tools.py:1030-1053`) recomputes completeness after each row drop, so counts equal listed rows. I found no path that says "complete" while data is omitted (season-fan-out drop, structured row cap, text cap all covered). |
| R3 | OK | `player_stats.py::_is_zeroish/no_recorded_stats` (all allow-listed cells 0, missing, or `0-0`; negatives and any non-zero cell not flagged), applied in `parse_gamelog`. Probe: only Skattebo 2026 wk 6 flagged; note `No recorded stats (played; ESPN gives no reason): Wk 6 vs NO (10/18).` under the 2026 table; `game_count` 14 unchanged. Tests assert no causal words, row-only flag, partial-zero rows unflagged. `no_stats` is `None` (absent in JSON) unless true, as spec ("gain `no_stats: true`"). |
| R4 + A1 | OK | `render.py::_na` maps None/""/"-" to `n/a`; used in `_game_cells`, `_log_totals_cells`, `_stat_row_cells`, `get_player_stats` Team cell. Probe: MLB rows and totals show `n/a`, `-3 -1.5 -1` stay (Skattebo 2025 wk 1), stats tables show `QBR n/a`; columns remain aligned (right-aligned `_table`). Footnote updated (`PLAYER_FOOTNOTE`). Structured: `compute_totals/_sum_totals` return `None`; `models.py` types `dict[str, str | None]`. Grep of `src/` shows no other reader of totals (only render, normalize, models). Walker test `test_r4_structured_data_has_no_placeholder_strings`. |
| R5 | OK | Skattebo career row: 182 CAR, 704 YDS, 3.9 AVG, 6 TD, 5 FUM, 2 LST, 2 TO (probe and test). Tool-set pin test passes (eleven). Size-budget tests green. No new imports of network libs, no logging change, no new input. |
| AC1 | OK | Gate above. |
| AC2 | OK | Every R row has passing assertions that fail on regression (exact-string checks). |
| AC3 | Not run | Needs human approval for a rebuild. |

## Deviations D4-D6 and "no line on unresolved"

- D4: acceptable. Both edits follow from R2/R4 (line 1 became line 2; Wk right-aligned header).
- D5: acceptable and good (one marker everywhere). `get_player_stats` has no footnote, but `n/a` there is self-explanatory.
- D6: acceptable; section 5 table does not list `docs/SPEC.md`.
- No completeness line on unresolved results: correct, there is no log to describe (`render.py` returns the candidates block first).

## Issues

### Blocker
None.

### Major
None.

### Minor

1. **CHANGELOG contradicts A1.** `CHANGELOG.md` (Changed section) ends "Structured data is unchanged." After A1, all-missing structured totals changed from `"-"` to `null`. Fix: say so ("structured totals that were all missing are now `null`, not `"-"`"). Also `docs/specs/player-stats/SPEC.md` lines ~120-136 still say totals are `-`; the pointer at line ~296-298 only mentions the text marker. Add "structured: null" to that pointer.
2. **Advice and count wording on a text-only cut.** `render.py::_completeness_line`: for a text-cap cut the line says "151 games listed" (it is the structured count; fewer rows are in the text) and ends "call with season=YYYY to see them", which does not help when the request was already a single season (Judge `season=2025` probe). Fix: when `text_cut` and no omitted seasons, drop the `season=YYYY` tail or say "ask for season_type=post / a narrower request"; optionally avoid "listed" for the text. No test currently pins this tail for the cut case, so it is cheap to change.
3. **`_years_text` implies a contiguous range** (`render.py::_years_text`): omitted `[2015, 2017]` renders `seasons 2015-2017`, which suggests 2016 was omitted too (a player can have a non-played year). Fix: join the actual years when few (`2015, 2017`) and use a range only when contiguous.
4. **Structured shape differs slightly from the spec.** `completeness.omitted_rows` is absent (not `{}`) when empty, while `omitted_seasons` is `[]` (`dump()` drops empty dicts). Spec R2 lists both keys. Either document that empty means absent or force the key; tests (`test_r2_career_completeness_line_is_exact_and_structured`) pin the absent form.

Nits (no action required): the README paragraph at `README.md:~256-257` has one over-long line; the NBA game log now shows `n/a` in the Wk column on almost every row (was `-`), which is noisy but accurate; a season with zero games reports "complete - 0 games" together with the existing no-games note, which is fine.

Verdict: APPROVED
