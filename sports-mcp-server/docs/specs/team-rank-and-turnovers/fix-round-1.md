# Fix round 1: team rank and turnovers

Responds to `review-round-1.md` (verdict APPROVED; no blockers, no majors; four minors).

| Minor | Action | Where |
|---|---|---|
| 1. README example omitted the `#6` prefix | Fixed: the example now reads `#6 IU (6-1) 6`, matching `render.py` | `README.md` (Ranks paragraph) |
| 2. Missing start time sorted before timed games within a tier | Fixed: `_rank_order` puts games without `start_utc` last within their tier (ranked and unranked tiers). Test `test_r5_game_without_start_time_sorts_last_within_its_tier` | `tools.py` `_rank_order`; `tests/test_team_rank.py` |
| 3. `ranked_only` + status filter that empties the list said "No ranked teams play on this date" | Fixed: with a status other than `all` the note is "No ranked teams among the <status> games on this date."; the A1 notes are kept for the no-status case. Test `test_r4_status_filter_that_empties_the_list_gets_its_own_note` | `tools.py` empty-result branch; `tests/test_team_rank.py` |
| 4. Structured `turnovers` field not named in the spec | Accepted as a documented addition. Listed in the CHANGELOG `[Unreleased]` entry and in the implementation report (observation 0) | `CHANGELOG.md`; `implementation-report.md` |

Gate after the fixes: `make check VENV=~/.venvs/sports-mcp` in WSL. All checks passed; mypy strict, 49 files; pytest 642 passed, 2 deselected; coverage 98.35% (gate 90%). Nothing committed.
