# Review round 1: team rank and turnovers

Reviewer: independent, read-only (fresh context). Gate run by me in WSL: `make check VENV=~/.venvs/sports-mcp` -> ruff, mypy --strict clean, 640 passed, coverage 98.35% (gate 90%). AC1 and AC2 met. AC3 intentionally not run.

## Requirement check

- R1 implemented (`normalize/common.py:161-181`, `poll_rank` accepts int 1..25 only, bool/float/str/99/0/26 -> None, scoreboard `curatedRank.current` then header `rank`); tested (`tests/test_team_rank.py` r1 tests).
- R2 implemented (`render.py:75-86` score_text, `render.py:220-225` game header `#6 Miami`; records dropped only when text > 6000 bytes with note, rank kept: `tools.py` build/retry); tested.
- R3 not applicable, upstream has no rank (upstream-notes.md); `find_team` unchanged, pinned by a test.
- R4 implemented (`tools.py` ranked_only filter before count/ids; note chosen by `League.has_poll_ranks`, `leagues.py`); registry pin test present.
- R5 implemented (`tools.py` `_rank_order`, sort before limit; invalid value -> InvalidInputError); tie-break and limit-after-sort tested on the real fixture.
- R6 verified by me against the real fixture box score: PITT TO 1 (fumbles lost: Holden Geriner), takeaways 0; MIA TO 0, takeaways 1 (= PITT's fumble lost). Labels/indices correct, takeaways = own INT made + opponent fumbles lost, gated on basis=game and football only, absent tables omit the line, non-football and pre-game unchanged.
- R7 descriptions and footnote present; untrusted note kept.
- R8 tool set still eleven; only `espn_client.py` imports httpx (logging_setup only mentions it to mute it, pre-existing); no new endpoint, setting or dependency; no logging of arguments added; cache untouched. Two existing assertions were updated (`tests/test_round1.py:386`, `tests/test_tools.py:685`) solely because records now trigger the omitted-records note on 100+ game NFL boards, which R2/S3 permits.
- Docs: README, CHANGELOG `[Unreleased]`, `docs/SPEC.md` pointer, tool descriptions match the behaviour.

## Issues

No blockers. No majors.

### Minor

1. README example is inexact. `README.md` (Ranks paragraph, "`IU (6-1) 6`") omits the `#6` prefix that the code emits (`#6 IU (6-1) 6`, `render.py:79-84`). Cosmetic doc fix.
2. `tools.py` `_rank_order` uses `game.start_utc or ""`: a game with no start time sorts before timed games within its tier rather than last. Edge case only (ESPN rarely omits it); optionally sort missing times last.
3. With `ranked_only` and a `status`/`week` filter that empties the list, the note says "No ranked teams play on this date" (`tools.py` empty-note branch) although ranked games may exist under another status. Wording nit; accept or reword.
4. The structured `turnovers` field is new output beyond the text lines described in R6. It is consistent with the text, reasonable and tested, but not named in the spec; accept as a documented addition (mention in the summary report).

## Verdict: APPROVED
