# Summary report: team rank and turnovers

Date: 2026-10-24. Branch: `feature/team-rank-and-turnovers` (cut from `main`; nothing committed). Spec: `SPEC.md` (approved, with amendment A1).

## Outcome

Three agent-friendliness additions prompted by a Goose test of "today's NCAA football results, ranked first, with leaders and turnovers":
poll rank and record on scoreboard and game teams, `ranked_only` and `sort="rank"` on `get_scoreboard`, and a compact turnover line in
football `performers`/`boxscore`. Review: **1 round used of 5**, verdict APPROVED (4 minors; 3 fixed, 1 accepted).

## Process

| Step | Actor | Output | Result |
|---|---|---|---|
| Plan: scope, tools showing rank, text format, params | orchestrator + human | `SPEC.md` section 3 | decisions P1-P4 |
| Spec, tasks, workflow | orchestrator | `SPEC.md`, `tasks.md`, `workflow.md` | approved by human |
| Implement T0-T9 | implementer (Haiku 5.5) | code, tests, fixtures, docs, `implementation-report.md`, `upstream-notes.md` | gate green; flagged two spec gaps |
| Amendment A1 (per-league `has_poll_ranks`; R8 structured budget scoped to existing fixtures; R6 clarifications) | human + orchestrator | `SPEC.md` section 10 | approved by human |
| Apply A1 | implementer | updated code and tests | gate green, 640 tests |
| Review round 1 | reviewer (Sonnet 5.5) | `review-round-1.md` | **APPROVED**: 0 blocker, 0 major, 4 minor |
| Fix round 1 | implementer | `fix-round-1.md` | 3 fixed, 1 accepted, 642 tests |
| Final gate re-run | orchestrator | this report | 642 passed, 98.35% branch coverage |

## What was built

| Req | Delivered |
|---|---|
| R1 | `TeamLine.rank` via `poll_rank()` (integer 1..25 only) from scoreboard `curatedRank.current` and summary header `rank`. |
| R2, R7 | `#6 IU (6-1)` in scoreboard lines, `#6 Indiana` in the game header, records dropped with a note only when text exceeds 6 KB (rank kept), footnote shown only when a rank is printed. |
| R3 | Not applicable: ESPN's team directory (762 teams) and team payload carry no rank, so `find_team` is unchanged and pinned by a test. |
| R4, R5 | `ranked_only` and `sort` (`time`/`rank`, best rank then other rank then start time, games without a start time last); applied before `limit`; empty-result notes chosen by `League.has_poll_ranks` (A1), with a separate status-filter note. |
| R6 | `TO: n (INT thrown n [players], fumbles lost n [players]); takeaways: n` for football game stats, always shown including zeros; structured `turnovers` field added. |
| R8 | Existing outputs unchanged except where required; tool set still eleven; no new endpoint, setting or dependency. |

## Verification

- `make check` (re-run by the orchestrator): 642 passed, 2 live tests deselected, 98.35% branch coverage (gate 90%); baseline 595.
- The reviewer independently re-ran the gate and checked the turnover numbers against the real Miami-Pitt box score fixture.
- Live ESPN check at capture time: 44 games on 2026-10-24, 17 with a ranked team.
- **Not run:** AC3, container rebuild and live smoke. The running container still serves the previous build.

## Deviations and accepted items

- Structured scoreboard output for the real 44-game college day is 36,011 bytes (34,293 before this change), over the 25 KB structured budget. Pre-existing, accepted by A1; guarded at 40 KB. The 6 KB text budget holds (5,076 bytes).
- Two existing assertions changed (`tests/test_round1.py`, `tests/test_tools.py`) because 100+ game NFL boards now trigger the records-omitted note.
- The structured `turnovers` field is beyond the spec's named output; accepted, listed in the CHANGELOG.
- Fixtures added: `ncaaf_scoreboard_ranked` (259 KB), `ncaaf_summary_ranked` (34 KB), `ncaaf_team_miami` (1 KB), `ncaaf_teams_directory` (203 KB).

## Open items for the human

1. **Rebuild the container and run `scripts/smoke.sh`** to confirm a live `get_scoreboard(league="ncaaf", date="today", ranked_only=true, sort="rank")` lists ranked games first (AC3).
2. **Commit, push and merge** `feature/team-rank-and-turnovers` when satisfied; nothing is committed.
3. Clients need a restart or new session to see the new parameters.
