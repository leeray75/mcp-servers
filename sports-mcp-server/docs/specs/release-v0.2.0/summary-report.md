# Summary report: release v0.2.0

Date: 2026-10-25. Branch: `release/v0.2.0` @ `c03b21b`, cut from `feature/team-rank-and-turnovers` @ `88bcbf8`.
Spec: `SPEC.md` (Status: Approved, approved by: human, 2026-10-25). **Review: 1 round used of 5, verdict APPROVED.**
Nothing pushed, tagged or merged (`AGENTS.md` §8; D3).

## Process

| Step | Actor | Output | Result |
|---|---|---|---|
| Investigate release state, find staleness (cross-check `config.py` vs `.env.example` vs README) | orchestrator | findings in chat | 4 stale surfaces; `main` found to lack the team-rank commit |
| Plan: version, branch base, git scope, doc scope, live smoke | orchestrator + human | `SPEC.md` §3 D1-D5 | D1 `0.2.0`, D2 from `88bcbf8`, D3 commit only, D4 all doc fixes, D5 smoke skipped |
| Write spec | orchestrator | `SPEC.md` | R1-R8, acceptance per requirement, Open questions empty |
| Approval gate | human | `SPEC.md` header | **Approved** (first prompt timed out; orchestrator held at the gate and made no edits; approved on the human's return) |
| Cut release branch | orchestrator | `release/v0.2.0` from `88bcbf8` | done before any edit |
| Implement | implementer subagent (fresh context) | 6 files + `implementation-report.md` | R1-R7 done; R8 left to the commit step; no spec defect hit |
| Gate run 1 | orchestrator | `make check` | 642 passed, 98.35% branch coverage; ruff + `mypy --strict` clean |
| Independent doc cross-check | orchestrator | `config.py`/`.env.example`/README diff | no setting missing from either doc surface |
| Review round 1 | reviewer subagent (read-only, fresh context) | `review-round-1.md` | **APPROVED**: 0 blocker, 0 major, 5 minor |
| Fix round 1 | implementer (orchestrator-verified) | `fix-round-1.md` | 1 rejected as false positive, 3 accepted with justification, 1 fixed in the commit |
| Gate run 2 | reviewer, independently | `make check` | same numbers: 642 passed, 98.35% |
| Release commit | orchestrator | `c03b21b` | 10 files, tree clean |

## What was built, against the requirements

| Req | Delivered |
|---|---|
| R1 | `pyproject.toml:7` and `src/sports_mcp/__init__.py:3` both `0.2.0`; `serverInfo.version` (`server.py:274`) and the ESPN `User-Agent` (`espn_client.py:82`) derive from `__version__`, so no other file needed a version edit; no test pins a literal version. |
| R2 | `## [0.2.0] - 2026-10-25` holds the former `[Unreleased]` body **verbatim** (pure 2-line insertion, so the body was never retyped; equality hash-verified twice, independently, by implementer and reviewer); empty `[Unreleased]` above; `[0.1.0]` byte-identical. |
| R3 | README configuration table gained `TTL_PLAYER_SEARCH_SECONDS` `3600`, `TTL_PLAYER_SECONDS` `300`, `PLAYER_MAX_SEASONS` `10`, defaults taken from `config.py:59-62`; reviewer AST-enumerated every `Settings` field and confirmed full coverage of both doc surfaces with no default mismatch in either direction. |
| R4 | Fixtures section now names both capture campaigns (league set 2026-10-04; player set + the four `ncaaf_*` rank fixtures 2026-10-24) and the added NCAA FBS and player-endpoint coverage; Known-limitations date updated. Every count asserted was counted first (44 events / 17 ranked, 762-team directory, 23 player fixtures, largest 265,859 B < 300 KB). |
| R5 | `CLAUDE.md` budget invariant keeps `<= 25 KB` as the scoreboard-fixture budget and records the `< 40 KB` structured guard for the real 44-game college day (team-rank amendment A1), matching `test_team_rank.py:492-493`. |
| R6 | `docs/SPEC.md` §7 TTL table +2 rows and §10 +1 sentence, both attributed to `docs/specs/player-stats/SPEC.md` — extending the base spec without re-deciding an amendment. |
| R7 | No behaviour change; `make check` green twice; nothing outside §5 touched; `tests/`, `scripts/`, `tools/`, container files and every historical report left alone. |
| R8 | One commit `c03b21b` on `release/v0.2.0` containing exactly the §5 files (6 modified + the release spec dir); tree clean; `main` still `9d8b46b`, `feature/team-rank-and-turnovers` still `88bcbf8`; no remote branch created. |

## Verification

- `make check VENV=~/.venvs/sports-mcp` run **twice** (orchestrator, then reviewer independently): 642 passed, 2 live
  tests deselected, **98.35% branch coverage** (gate 90%), ruff "All checks passed!", `mypy --strict` clean on 25 files.
  Identical to the pre-release baseline — the release moved no test.
- Version consistency proven programmatically: `tomllib`-read `pyproject` version == `sports_mcp.__version__` == `0.2.0`
  under the project venv.
- No `0.1.0` string remains in tracked source (`git grep` exit 1). The `0.1.0` inside `__pycache__/*.pyc` is a
  gitignored build artifact and was deliberately left alone.
- Reviewer independently re-hashed the changelog body against `git show HEAD:CHANGELOG.md` and re-counted every fixture
  claim rather than accepting the implementer's numbers.
- Not run, per D5: container rebuild and `scripts/smoke.sh` against live ESPN. `make audit` not required (no
  dependency change).

## Deviations and accepted items

- **No deviations.** The implementer reported none and the reviewer confirmed none; the only scope carve-out was R8
  being executed by the orchestrator at the commit step rather than by the implementer, which is what D3 always intended.
- **m-1 rejected on evidence** (the pass's most valuable catch): the reviewer claimed four resources are registered
  while the docs say three. Checked at the source and the pin: `resources.py` registers exactly 2 static + 1 template,
  `server.py:272` says three, and `test_server.py::test_resources_are_listed_and_readable` pins that set and passes.
  Applying the suggestion would have *introduced* a false claim into the README — the exact defect class this release
  removes. Recorded in `fix-round-1.md` with the evidence.
- m-2 accepted: the fixtures sentence asserts capture date, not game date, so the June NBA Finals fixture is correctly
  under the 2026-10-04 campaign.
- m-3 accepted: `.env.example` shows `ESPN_TIMEOUT_SECONDS=10` for the float default `10.0` — same value; `.env.example`
  is outside the approved file set.
- m-5 accepted: "for NFL, NBA and MLB" is the union across player fixture kinds (profiles exist for NFL and NBA only);
  the sentence claims no per-kind sport coverage.

## Open items for the human

1. **Push and open the release PR.** `release/v0.2.0` exists only on this disk. Suggested: `git push -u origin
   release/v0.2.0`, then PR `release/v0.2.0` -> `main`. Merging it is what finally brings the team-rank feature into
   `main` (which has been missing it since 2026-10-24). Tag `v0.2.0` on merge if you want the tag.
2. **Container rebuild + live smoke** (carried over, still unproven over HTTP): rebuild and run `scripts/smoke.sh`,
   then confirm a live `get_scoreboard(league="ncaaf", date="today", ranked_only=true, sort="rank")` lists ranked games
   first. It is Sunday, so the college slate should have ranked teams to prove it on. Note `smoke.sh` exercises all
   eleven tools but does **not** yet pass `ranked_only`/`sort`, so a green run does not by itself cover AC3 — either
   extend `smoke.sh` (needs its own spec, it is a script change) or check that one call by hand.
3. **Client restart** so Goose picks up the new tool parameters and the `0.2.0` `serverInfo.version`.
4. Optional tidy-ups deliberately left out of this release, listed in `SPEC.md` §7: the `10` vs `10.0` `.env.example`
   nit, per-kind wording for the player fixture sentence, Dockerfile base-image digest pinning, and the 40 KB
   structured-budget follow-up.
