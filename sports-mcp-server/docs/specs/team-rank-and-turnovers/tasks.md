# Tasks and traceability skeleton: team rank and turnovers

| # | Task | Requirements | Files |
|---|---|---|---|
| T0 | Capture fixtures (ranked ncaaf scoreboard + summary + team directory), write `upstream-notes.md`, decide R3 | R1, R3, R6 | `tests/fixtures/`, `tools/capture_fixtures.py`, `upstream-notes.md` |
| T1 | `poll_rank()` + `TeamLine.rank`, summary header rank | R1 | `normalize/common.py`, `normalize/summary.py`, `models.py` |
| T2 | Scoreboard text: rank prefix, records within budget, footnote | R2, R7 | `render.py` |
| T3 | `ranked_only`, `sort`, applied before `limit` | R4, R5 | `tools.py`, `server.py` |
| T4 | Football turnover line | R6 | `normalize/players.py`, `render.py` |
| T5 | `find_team` rank if T0 finds it upstream, else record "not applicable" | R3 | `normalize/teams.py`, `models.py`, `render.py` |
| T6 | Descriptions | R7 | `server.py` |
| T7 | Tests for every requirement; update changed assertions only | all | `tests/` |
| T8 | Docs: README, CHANGELOG `[Unreleased]`, pointer in `docs/SPEC.md` | - | listed files |
| T9 | Gate and `implementation-report.md` (traceability table) | AC1-AC3 | report |

| Req | Implementation | Test |
|---|---|---|
| R1 | T1 | to be filled by the implementer |
| R2 | T2 | |
| R3 | T5 | |
| R4 | T3 | |
| R5 | T3 | |
| R6 | T4 | |
| R7 | T2, T6 | |
| R8 | all | existing suite, size-budget tests, tool-set pin |
| AC3 | live check after rebuild | NOT RUN until the human approves the rebuild |
