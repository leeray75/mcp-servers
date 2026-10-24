# Spec: player tools polish (agent-friendliness)

- **Status:** Approved, 2026-10-24, approved by: human (original version and amendment A1, section 10)
- **Date:** 2026-10-24
- **Server:** `sports-mcp-server`
- **Amends:** `docs/specs/player-stats/SPEC.md` (R3, R9, R13, R17, tool descriptions) and `docs/SPEC.md` section 5. No invariant from
  `CLAUDE.md` changes: the tool set stays exactly eleven, no new endpoint, no new configuration.
- **Companion files:** `tasks.md`, `workflow.md`. Artifacts live in `docs/specs/player-tools-polish/` (same convention as player-stats).
- **Branch:** `feature/player-tools-polish`, cut from `origin/main` (PR #1 is already merged there).

## 1. Problem / motivation

A clean-session Goose test with the plain prompt "What are NY Giants Cam Skattebu's career game stats in the NFL? Itemize them by game and
include turnovers." returned a correct answer, but:

1. It made **three** calls instead of one: a redundant `find_player`, then a verification re-query. It read the description "(the most
   recent seasons, capped)" as possible truncation, although the tool states explicitly when rows are omitted.
2. It had no single line confirming completeness, which is what it was trying to establish with the extra call.
3. It **guessed why** the all-zero game (Skattebo, 2026-10-18 vs NO) had no stats ("soft-benched", "inactive/healthy scratch"). The data
   only says the game was played with no recorded stats.
4. The legend reuses `-` for both "missing" and a negative sign, so `-3` and `-` are easy to confuse.

## 2. Goals and non-goals

**Goals:** fewer wasted calls (G1), a one-line completeness statement (G2), an honest label for games with no recorded stats (G3), an
unambiguous marker for missing values (G4).

**Non-goals:** new tools, new endpoints, new settings, position on name lookups, hockey/soccer allow-lists, changes to totals logic,
changes to size budgets, any change to the non-player tools.

## 3. Decisions (made with the human on 2026-10-24)

| # | Question | Decision |
|---|---|---|
| P1 | Scope | Description wording, completeness line and zero-stat label. **Not** position on name lookups. |
| P2 | Completeness line | **Always present** on `get_player_gamelog`: states complete, or exactly what is missing. |
| P3 | Missing-value marker | Missing and season-to-date cells render as **`n/a`**; `-` then only ever means a negative sign. Existing player-tool output (and its tests) changes accordingly. |
| P4 | Branch | New branch from `origin/main`: `feature/player-tools-polish`. |

Decided by the spec author, flagged for veto at approval:

- S1. Agents: implementer **Haiku 5.5**, reviewer **Sonnet 5.5** (the choice made for the player-stats feature), up to 5 rounds.
- S2. The zero-stat label is a note line under the season table plus a structured flag, not an extra table column (keeps tables narrow).
- S3. "No recorded stats" means every allow-listed stat cell for that game is `0` or missing. It says nothing about why.

## 4. Requirements

- **R1. Description wording.** The `get_player_gamelog` description and the `season` parameter description say: `season="all"` returns the
  player's whole career (up to `PLAYER_MAX_SEASONS` seasons) and the result states explicitly if any seasons or games were omitted; a
  player given by **name works directly**, so `find_player` is only needed to browse candidates. The words "capped" and "the most recent
  seasons" no longer appear as the primary description of `all`. The `get_player_stats` description gets the same name-works-directly
  sentence. Descriptions stay within the existing style (what, when to use, examples) and must still carry the untrusted-content note.
- **R2. Completeness line.** `get_player_gamelog` text always includes, directly under the header line (before any `note`), exactly one line:
  - complete: `Completeness: complete - <N> games (<year>: <n>, ...), <season_type label>, nothing omitted.`
  - partial: `Completeness: PARTIAL - <N> games listed (<year>: <n>, ...); omitted: <what>.` where `<what>` names omitted seasons (years) and/or
    omitted rows per season (count), and ends with how to get them (`season=YYYY`).

  `<season_type label>` is `regular season only`, `postseason only` or `regular season and postseason`. Structured data gains
  `completeness: {status: "complete"|"partial", games: int, by_season: {year: int}, omitted_seasons: [int], omitted_rows: {year: int}}`.
  The line is emitted for every `season` value (`current`, a year, `all`), and survives the text size cap (it sits above the first season block;
  the size-cap tests must prove it).
- **R3. Zero-stat games.** A game whose every allow-listed stat is `0` or missing is a *no-recorded-stats game*. It stays in the table and
  in `game_count`. Under that season's table a line is added: `No recorded stats (played; ESPN gives no reason): Wk 6 vs NO (10/18).`
  listing every such game. Structured game rows gain `no_stats: true` for them. The wording must not state or imply a cause.
- **R4. Missing marker.** In text tables and the legend, missing values and season-to-date cells render as `n/a` instead of `-`. A real
  negative number keeps its minus sign (`-3`). The footnote is updated: `n/a` means missing or season-to-date, not per game. Totals whose inputs
  were all missing render `n/a`. Structured data is unchanged (it never used `-`; verify and keep).
- **R5. No behavioural regression.** Totals, ordering, size budgets (R16 of the player-stats spec), caching, error handling, the tool set (exactly
  eleven) and every invariant are unchanged. All numeric results for the Skattebo reference case are identical: 14 games, 182 CAR, 704 YDS,
  3.9 AVG, 6 TD, 5 fumbles, 2 lost, 2 turnovers.

## 5. Design

| File | Change |
|---|---|
| `src/sports_mcp/server.py` | `_DESC_GAMELOG`, `_DESC_PLAYER_STATS`, `SeasonArg` description (R1). |
| `src/sports_mcp/normalize/player_stats.py` | Detect no-recorded-stats games; build the `completeness` structure from seasons, `games_omitted` and the season-fan-out truncation (R2, R3). |
| `src/sports_mcp/models.py` | `completeness` on `PlayerGameLogResult`; `no_stats` on the game row model. |
| `src/sports_mcp/render.py` | Completeness line, zero-stat note, `n/a` marker, updated `PLAYER_FOOTNOTE` (R2-R4). |
| `src/sports_mcp/tools.py` | Pass the omitted-seasons information (already computed for the `season=all` note) into `completeness`. |
| `tests/`, fixtures | Update affected assertions; new tests below. No new upstream calls, so no new fixtures. |
| `README.md`, `CHANGELOG.md`, `docs/specs/player-stats/SPEC.md` | Document the new line and marker; pointer to this spec. |

## 6. Security and invariants

Unchanged: no new input, endpoint, setting or dependency. Descriptions are static text. The completeness line and zero-stat note contain only
numbers, years and fixed words, plus opponent abbreviations and dates already rendered in rows (third-party text, already treated as data).
Logging, caching, host allow-list and the eleven-tool pin are untouched.

## 7. Test plan and acceptance criteria

| Req | Verification |
|---|---|
| R1 | Test on registered tool descriptions: contain "whole career" and "works directly" (or equivalent) and the untrusted note; do not contain "capped". |
| R2 | Service/render tests: Skattebo `season=all` gives `complete - 14 games (2025: 8, 2026: 6), regular season only, nothing omitted.`; a `PLAYER_MAX_SEASONS` truncation fixture gives PARTIAL naming omitted years; the Judge MLB row-capped season gives PARTIAL with the omitted row count; single year and `current`; the line is above the first season block and survives a lowered text cap; structured `completeness` matches. |
| R3 | Skattebo 2026: note names Wk 6 vs NO (10/18), `no_stats` true on that row only, `game_count` still 14, no causal wording (assert absence of "bench", "inactive", "injur"). Non-zero games unaffected; a game with only some zeros is not flagged. |
| R4 | Render tests: missing/season-to-date cells show `n/a`; `-3` and `-1.5` (Skattebo wk 1 2025) keep minus signs; footnote text; totals with all-missing inputs show `n/a`; structured data has no `n/a`/`-` placeholders. |
| R5 | Existing player test suite passes after updating placeholder expectations only; Skattebo reference numbers asserted; tool-set pin = eleven; size-budget tests unchanged and green. |

- AC1. `make check` passes (ruff, `mypy --strict`, pytest, coverage >= 90%).
- AC2. Every R1-R5 row above has a passing, non-vacuous test.
- AC3. After rebuilding the container, `scripts/smoke.sh` passes and the live Skattebo `season=all` result begins with the complete line.
  (Rebuild only with the human's approval; otherwise reported as not run.)

## 8. Out of scope

Position on name lookups; fixtures for hockey/soccer; any rendering change not listed (column order, widths, headings); any new tool or parameter.

## 9. Open questions

None.

## 10. Amendment log

**A1, 2026-10-24 (decided by the human the same day, in answer to implementer findings D1 and D2).**

- **R4 (amended, D2).** Structured totals that were previously `"-"` when all inputs were missing become **`null`** (JSON null). The text
  rendering maps both `null` and a missing cell to `n/a`. A real negative number is unchanged. The earlier sentence "Structured data is
  unchanged (it never used `-`)" is withdrawn; the R4 test row now reads: structured data contains no `"-"` or `"n/a"` placeholder strings
  (missing values are `null`). Existing tests asserting the old structured `"-"` are updated to `null`; this is a deliberate behaviour change.
- **R2 (amended, D1).** If the text size cap cuts newest rows, the completeness line says `PARTIAL` and names the cause
  (`newest rows cut by the text size cap`), and structured `completeness.status` is `"partial"`; no per-row count is required for a text-only cut.
