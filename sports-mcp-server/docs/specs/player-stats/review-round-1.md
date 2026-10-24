# Review round 1: player stats tools

Reviewer: independent, read-only (fresh context). Spec: `SPEC.md` (approved, incl. A1). Working tree on `feature/player-stats-tools`, nothing committed.

## Summary

The implementation is broadly faithful and well tested. All eleven tools are registered read-only, URL building is strict, the
numbers check out (Skattebo career 14 games / 182 CAR / 704 YDS / 3.9 AVG / 6 TD; TO = INT thrown + fumbles lost; zero-stat
Saints game kept as `L 27-34`; computed totals equal row sums in every fixture, verified by an independent script). The one
substantive problem is the structured-budget trimming (implementer deviation #2): on a full MLB season it silently makes the
totals wrong, trims the wrong block when `season_type=all`, and can still exceed 60 KB. Two majors, several minors.

## Gate (run by me, WSL, `make check VENV=~/.venvs/sports-mcp`)

| Step | Result |
|---|---|
| ruff check | All checks passed |
| ruff format --check | 63 files already formatted |
| mypy --strict | Success: no issues found in 47 source files |
| pytest | 567 passed, 2 deselected (live) |
| coverage (branch) | 98.42% (gate 90%); `normalize/player_stats.py` 98%, `tools.py` 99% |

AC1 met. Tests use the autouse network guard and `FakeEspn`; I found none touching the network.

## Requirement and acceptance check (evidence)

- R1/R4/R5: `tools.py:688-780` (`_search`, `_resolve_player`, `find_player`), `normalize/player_stats.py:71-190`. Retry order, min 4 chars, max 3
  retries, digits never fuzzy (`retry_queries`, `isalpha`), auto-resolve rule (`resolve`: threshold `max(2, 0.2*len)` and gap >= 2). OK.
- R2/R10: `parse_stats` keeps ESPN `totals` verbatim; MLB "Postseason" categories dropped for `regular`, `post` keeps only them. OK.
- R3/R6/R7/R11/R18: `parse_gamelog`, `_season_kind` (block by `displayName`, preseason ignored), `_score_and_result`. Exercised: Skattebo
  8 + 6 games, `vs NO` shows `L 27-34`, Darnold Super Bowl `W 29-13`. OK.
- R8/R9/R12: allow-lists `_FOOTBALL/_BASKETBALL/_BASEBALL`, reducers in `compute_totals`; defender `interceptions.interceptions` shown as `DINT`, never in TO;
  QB with no fumbles category gets TO = INT only (Darnold 14). MLB AVG recomputed H/AB, OBP/SLG/OPS `-`, per-game season-to-date cells hidden. OK.
- R13/R14/R15: `_meta_for` (oldest `as_of`, stale flag), TTLs from settings, failures uncached (test), errors do not echo id/query/body. OK.
- Invariants: exactly eleven tools (`server.py:395-430`, pinned test); only `espn_client.py` imports httpx; ids go through `_digits`
  (profile, stats, gamelog), season range check at `espn_client.py` `player_gamelog_url`, query regex `[\w .'-]{1,60}` without `_`; fixed
  season_type map; no player/query in logs (`_instrumented` untouched). OK.
- AC2: with the fixtures the gamelog resolves via the fuzzy path and prints the exact career line (see m3 for a caveat). AC4/AC6: docs and
  file list match SPEC 5.1 (README, CHANGELOG [Unreleased], `.env.example`, CLAUDE.md, `docs/SPEC.md` pointer, smoke.sh, try_live.py updated).
  AC5 reported not run; acceptable per spec.

## Issues

| # | Sev | Location | Evidence | Suggested fix |
|---|---|---|---|---|
| M1 | major | `tools.py:972-976` (trim loop), `tools.py:920-940` (`build(..., trim)`), `tools.py:1017-1022` | Judge 2025 `season_type=regular`: structured 59,686 B only after dropping 2 oldest games; text header says "151 games" and totals (AB 533, H 174, AVG .326) differ from the true season (153 games, AB 543, H 179, AVG .330). The explanatory note is appended last, so the 8 KB `fit()` cut removes it: the text carries no statement that games were dropped, only the generic cut notice. Totals now silently describe a subset, contradicting the point of R3/R9/R11. | Compute totals and `game_count` from the full season; for the structured size budget drop row detail instead (e.g. cap `games` and add `games_omitted`), keep totals untouched, and put the omission note first (before the table) so `fit()` cannot remove it. |
| M2 | major | `tools.py:972-976` | Judge 2025 `season_type=all` (single season): trim always targets `chosen[-1]`, which is the 7-game postseason block, not the 153-game regular block. The loop spins to `MAX_TRIM_ROWS` (400 re-serialisations), ends with "Only the most recent 0 of 7 games in 2025 Postseason", an empty block, and structured data is still 60,790 B (> 60,000, R16/AC3 violated). Existing test only covers `season_type=regular`. | Trim the block that is largest (or iterate blocks), stop when no rows are left to drop, and add a test with `season_type="all"` for the MLB fixture asserting `len(json.dumps(data)) <= 60000`. |
| m1 | minor | `normalize/player_stats.py` `_FOOTBALL`, `render.py:_log_columns` | Every football log prints Passing, Turnovers INT and Defense columns even when the payload has none (RB output: `Passing: CMP ATT YDS TD`, `Defense: TCK SCK DINT PD`, all `-`), roughly 40% of each row is dashes. Hard for a small model and wastes the 8 KB budget (NBA/MLB truncate early). | Drop allow-list columns whose category is absent from the payload (`present` keys), keep them only in the derived TO rule. |
| m2 | minor | `tools.py:913-916` | `season=all` note reads "Built from 5 upstream calls (1 stats, 2 game logs)": the 5 includes 2 search calls and the breakdown omits them. | Report exact counts per kind or only the total. |
| m3 | minor | `tests/test_player_service.py:129`, conftest routes | AC2 names `player="Cam Skattebu"`, but the fixtures only cover the single-word query `skattebu`. The multi-word retry (`Cam Skatteb`) has no fixture and no test; I had to inject one. Real ESPN behaviour for the two-token query is unverified. | Capture a `cam skatteb` fixture (or verify live) and test AC2 with the literal string. |
| m4 | minor | `tests/test_player_render.py:110` | `assert "Res" in out.text and "L " in out.text or "W " in out.text` is precedence-weak and nearly tautological. | Parenthesise and assert a concrete row, e.g. `@WSH  L 6-21`. |
| m5 | minor | `render.py:582-585,456` | Totals rows are labelled just "Total"/"Career"; the "computed from listed games" wording only appears in a footnote at the end of the text, which `fit()` can cut. R9 asks for the label on the totals rows. | Label season totals "Total (computed from listed games)" or put the season header "(N games; totals computed from listed games)". |
| m6 | minor | `render.py:628-637` | Truncation notice says "smaller top_n / detail / limit", none of which exist on the gamelog tool; `structured.truncated` is not set when only the text was cut (e.g. 153-game MLB, 70+ game NBA with postseason: postseason table lost at 7,917 chars). | Tool-specific wording ("use season=YYYY / season_type=regular") and set `truncated: true` when `fit()` cut. |
| m7 | minor | `render.py` `_game_cells` / NBA | The Wk column takes the width of the longest `eventNote`; NBA tables are indented about 20 spaces on every row, costing budget. | Cap the round label width (abbreviate) or move it to its own line. |

## Notes on the implementer's deviations

1. **R5 distance to word vs full name (report section 2.2): sound.** The spec's own example (`Skattebu` -> `Cam Skattebo`) has distance 5 to the full
   name and 1 to the word, so the literal full-name reading makes the example unresolvable; word distance (min of full name and each word) is the only
   reading consistent with the spec. Gap rule is applied with the same metric. Risk is slight over-matching on first names, bounded by the clear-gap rule.
   Accept; note it in SPEC on merge.
2. **R16 structured-row trimming (2.3): the need is real, the implementation is not acceptable.** Dropping oldest rows to fit 60 KB is a reasonable addition
   for 150+ game seasons, but it must not change totals or lose its note (M1) and must target the right block (M2). Request changes.
3. **Position absent on name lookups (2.1) and hockey/soccer generic columns (2.4): acceptable.** Within "if known" in R1; generic columns for
   unfixtured leagues are documented. Mention in the README that name lookups carry no position.
4. **Remaining interpretations (season list from regular-stats only, search `limit=50`, two stats calls for `all`, NBA % columns structured-only,
   pitching structured-only): acceptable**, all documented and bounded.

Total issues: 0 blockers, 2 major, 7 minor.

Verdict: CHANGES REQUESTED
