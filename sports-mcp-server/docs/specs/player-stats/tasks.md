# Tasks and traceability skeleton: player stats tools

Derived from `SPEC.md`. The implementer completes the traceability table in `implementation-report.md`; this file is the plan, not a record.

## Work breakdown

| # | Task | Requirements | Main files |
|---|---|---|---|
| T0 | Capture fixtures (done; add the athlete profile fixtures for Skattebo and LeBron, A1/D10; Skattebo gamelog 2025+2026, stats, search hit/miss "skattebo"/"skattebu", "smith" multi-league, a QB, a defender with interceptions, an NFL player with postseason games, NBA and MLB players); record upstream findings in `upstream-notes.md` | R5, R7, R8, R12 | `tools/capture_fixtures.py`, `tests/fixtures/` |
| T1 | Config settings + `.env.example` | R14, R6 | `config.py`, `.env.example` |
| T2 | URL builders (search, profile, stats, gamelog) and strict validation | R4, R6, R7, section 7 | `espn_client.py` |
| T3 | Normalization: search, name folding, Levenshtein, resolution rules | R1, R4, R5 | `normalize/player_stats.py` |
| T4 | Normalization: gamelog + stats, allow-lists, reducers, totals, turnovers | R2, R3, R8-R12 | `normalize/player_stats.py` |
| T5 | Models and renderers with size budgets | R13, R16, R17 | `models.py`, `render.py` |
| T6 | Service methods, caching, season fan-out, error mapping | R3, R6, R7, R13-R15 | `tools.py` |
| T7 | Tool registration, descriptions, pinned tool-set test = 11 | D1, section 7 | `server.py`, `resources.py`, tests |
| T8 | Tests for every requirement; coverage >= 90% | all | `tests/` |
| T9 | Docs: README, CHANGELOG `[Unreleased]`, CLAUDE.md invariant text, `docs/SPEC.md` amendment pointer, smoke script | AC4, AC5 | listed files |
| T10 | Gate and report | AC1-AC6 | `implementation-report.md` |

Suggested order: T0 -> T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8 -> T9 -> T10.

## Traceability table (to be completed by the implementer)

| Req | Implementation (file::symbol) | Test (file::name) |
|---|---|---|
| R1 | | |
| R2 | | |
| R3 | | |
| R4 | | |
| R5 | | |
| R6 | | |
| R7 | | |
| R8 | | |
| R9 | | |
| R10 | | |
| R11 | | |
| R12 | | |
| R13 | | |
| R14 | | |
| R15 | | |
| R16 | | |
| R17 | | |
| R18 | | |
| AC1-AC6 | | |
