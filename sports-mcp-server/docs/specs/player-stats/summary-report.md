# Summary report: player stats tools

Date: 2026-10-24. Branch: `feature/player-stats-tools` (nothing committed). Spec: `SPEC.md` (approved, with amendments A1 and A2).

## Outcome

Three new read-only tools, `find_player`, `get_player_stats` and `get_player_gamelog`, so an agent can answer
"What are NY Giants Cam Skattebu's career game stats in the NFL?" in a single call, misspelling included. The tool set is now exactly
eleven. All 15 leagues are supported through one label-driven renderer. Review: **2 rounds used of 5**, final verdict APPROVED.

## Process

| Step | Actor | Output | Result |
|---|---|---|---|
| Plan: design questions (tool shape, leagues, seasons, lookup, postseason, totals, size, typos) | orchestrator + human | `SPEC.md` section 3 | 9 decisions D1-D9 |
| Spec, upstream notes, tasks, workflow | orchestrator | `SPEC.md`, `upstream-notes.md`, `tasks.md`, `workflow.md` | approved by human |
| Implement T0 (fixtures) | implementer (Haiku 5.5) | 19 fixtures, `upstream-notes.md` | stopped: spec gap (R4 profile endpoint), plus findings on postseason, MLB rates, score field |
| Spec amendment A1 (D10 profile endpoint, D11 postseason by block name, D12 MLB rates, new R18 score) | human + orchestrator | `SPEC.md` section 11 | approved by human |
| Implement T1-T10 | implementer (Haiku 5.5) | code, tests, docs, `implementation-report.md` | gate green, 567 tests |
| Review round 1 | reviewer (Sonnet 5.5) | `review-round-1.md` | CHANGES REQUESTED: 0 blocker, 2 major, 7 minor |
| Fix round 1 | implementer | `fix-round-1.md` | all 9 fixed, 573 tests |
| Review round 2 | reviewer (Sonnet 5.5, fresh) | `review-round-2.md` | **APPROVED**: 0 blocker, 0 major, 3 minor; recommended amendment A2 |
| Amendment A2 (document the in-season row cap and `games_omitted`) | human + orchestrator | `SPEC.md` section 11 | approved by human |
| Fix round 2 (3 minors) | implementer | `fix-round-2.md` | all 3 fixed |
| Final gate re-run | orchestrator | this report | 573 passed, 98.40% branch coverage |

## What was built (requirement map)

| Requirements | Delivered |
|---|---|
| R1, R4, R5 | `find_player`; `player` accepts id or name; id lookups use the athlete profile (A1); typo handling retries the search with the name trimmed and ranks by edit distance, resolving only when one candidate is clearly closest. "Cam Skattebu" resolves to Cam Skattebo (id 4696981). |
| R2, R10 | `get_player_stats`: season rows and ESPN's own career totals, unmodified and labelled. |
| R3, R6, R7, R11, R18 | `get_player_gamelog`: one row per game, `season` = year / `current` / `all` (fan-out capped by `PLAYER_MAX_SEASONS`), `season_type` regular / post / all selected by ESPN block name, zero-stat games counted, score shown from the player's team's side. |
| R8, R9, R12 | Label-driven columns with per-sport allow-lists; totals computed server-side (sum / max / ratio / pair); MLB AVG recomputed, OBP/SLG/OPS `-`; football fumbles, fumbles lost, INT thrown and `TO` (defensive interceptions never counted). |
| R13-R15 | Freshness envelope, `TTL_PLAYER_SEARCH_SECONDS`, `TTL_PLAYER_SECONDS`, `PLAYER_MAX_SEASONS`, error mapping with no echo of input. |
| R16, R17 | Size budgets asserted with real fixtures; in-season row cap (A2) that never changes totals and always reports `games_omitted`. |

Reference check (fixtures, as of 2026-10-24): Skattebo career 14 games (8 + 6), 182 CAR, 704 YDS, 3.9 AVG, 6 TD, with the all-zero Saints game of 2026-10-18 kept and shown as `L 27-34`.

## Verification

- `make check` (re-run by the orchestrator): 573 passed, 2 live tests deselected; branch coverage 98.40% against the 90% gate. Baseline before the feature: 435 tests, 98.33%.
- The reviewer independently re-ran the gate, confirmed computed totals equal the arithmetic row sums in every gamelog fixture, and exercised Skattebo, Judge (MLB), LeBron (NBA) and Darnold (postseason) offline.
- Invariants confirmed by review: exactly eleven read-only tools; only `espn_client.py` imports httpx; strict patterns on every upstream-bound value; no new host; no logging of queries; `security.py`, `cache.py` and `logging_setup.py` untouched.
- **Not run:** AC5 live smoke through the container. The container was not rebuilt, so the running `sports-mcp` still serves the previous eight tools.

## Deviations and accepted items

- R5 distance is measured to the closest name word as well as the full name; required by the spec's own "Skattebu" example. Accepted by the reviewer.
- Name lookups return no `position` (not in ESPN's search payload); id lookups get it from the profile. Documented in the README.
- Hockey and soccer have no fixtures, so they use a generic column list rather than an allow-list. Same code path; lower confidence than NFL, NBA and MLB.
- In-season row cap and "hide categories the player lacks" (R12/R8 reading): both documented in amendment A2.
- Text cap still cuts the newest rows last; the notice says so and names the narrower requests (n1, fixed by wording rather than reordering).

## Open items for the human

1. **Rebuild the container** so Claude Code, Claude Desktop and Goose can use the new tools: `scripts/rebuild.sh` (or `.ps1`) then `scripts/smoke.sh` (AC5). Clients may need a restart to see the new tool list.
2. Review the diff and **commit** when satisfied; nothing is committed. The branch also holds new fixtures (about 21 files, up to 87 KB each) and the full spec/report trail in `docs/specs/player-stats/`.
3. Optional follow-ups: fixtures for hockey and soccer to promote them to allow-lists; position on name lookups via an extra profile call; the earlier note that `claude_desktop_config.json` shows the server under Settings > Developer, not Connectors.
