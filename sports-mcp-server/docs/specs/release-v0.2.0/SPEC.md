# Release v0.2.0 — version bump and documentation refresh

- **Status:** Approved, 2026-10-25, approved by: human (D1-D5 as written; live smoke not opted into)
- **Date:** 2026-10-25
- **Amends:** none (release packaging). It does not change any requirement of `docs/SPEC.md` or the three feature
  specs; it only makes the documentation match what those specs already delivered.
- **Branch under release:** `feature/team-rank-and-turnovers` @ `88bcbf8` (clean tree)
- **Gate state at time of writing:** `make check VENV=~/.venvs/sports-mcp` → 642 passed, 2 deselected, 98.35% branch
  coverage (gate 90%). No dependency change, so `make audit` is not required by `CLAUDE.md`.

## 1. Problem / motivation

Three approved, reviewed features are sitting unreleased on a feature branch: player tools
(`docs/specs/player-stats/SPEC.md`), player-tools polish (`docs/specs/player-tools-polish/SPEC.md`) and team rank +
turnovers (`docs/specs/team-rank-and-turnovers/SPEC.md`). The distributed version is still `0.1.0` and the changelog
still describes all of that work as `[Unreleased]`, so there is no release identity for the container image, the MCP
`serverInfo.version`, or the upstream `User-Agent`.

A cross-check of `config.py` → `.env.example` → `README.md` also shows the user-facing docs drifted while the code
moved ahead: the three player settings are implemented and in `.env.example` but missing from the README
configuration table, and the README still says every test fixture was captured on 2026-10-04 when ~30 player fixtures
and four NCAA-football fixtures were captured on 2026-10-24. Shipping v0.2.0 with those gaps would make the release
documentation wrong on the two things an operator checks first (settings and fixture provenance).

## 2. Goals and non-goals

**Goals**

1. Give the release a version: `0.2.0` (minor bump from `0.1.0`, as instructed by the human).
2. Turn `CHANGELOG.md` `[Unreleased]` into a dated `0.2.0` entry.
3. Make every documentation surface match the shipped code — no stale setting list, fixture provenance, budget or
   tool-count claim.
4. Record the release on its own branch with a single commit, and leave the tree clean for a release PR.

**Non-goals**

- No behaviour, tool, schema, endpoint, setting or dependency change.
- No version tag, no merge to `main`, no push, no container redeploy — unless the human explicitly asks (see D3-D5).

## 3. Decisions (proposed; confirmed by the human at the approval gate)

| # | Question | Proposed answer |
|---|---|---|
| D1 | Version number for a minor bump from `0.1.0` | `0.2.0` — new tools and new tool parameters are backwards-compatible additions (semver minor). Not a question in dispute; recorded for the audit trail. |
| D2 | Where to cut `release/v0.2.0` | **From `feature/team-rank-and-turnovers` @ `88bcbf8`** (the only commit that contains all three features). `main` does not contain the team-rank commit, so cutting from `main` would release without it. |
| D3 | How far to go in git | Create the branch and **commit only**. No push, no tag, no merge to `main` — matching `AGENTS.md` §8 ("do not commit, push, tag or release unless the human asks") and the pattern of PRs #1 and #2 (feature branch → GitHub PR → merge). |
| D4 | Documentation scope | All four stale surfaces (R3-R6 below), plus release metadata (R1-R2). Historical spec/report artifacts are **not** rewritten (see §9). |
| D5 | Live verification (AC3 left open by the team-rank summary report) | **Not run by default.** The running container (image `990eddc2458f`, built ~10 h ago, healthy) is not proven to include the team-rank build, and rebuilding/restarting it changes a running service. Offered as an opt-in at the approval gate; if declined it stays a documented follow-up. |

## 4. Requirements

Each is individually checkable; the acceptance commands are in §8.

- **R1 (version).** `pyproject.toml` `project.version` and `src/sports_mcp/__init__.py` `__version__` both read
  exactly `0.2.0`, and no other tracked file pins a release version. The MCP `serverInfo.version` and the ESPN
  `User-Agent` (`sports-mcp/<version> (+local)`) derive from `__version__` and need no separate edit.
- **R2 (changelog).** `CHANGELOG.md` has `## [0.2.0] - 2026-10-25` holding exactly the content currently under
  `[Unreleased]` (same Added/Changed bullets, same spec references, no rewording), and a fresh empty `## [Unreleased]`
  above it. The `## [0.1.0]` section is byte-identical to before.
- **R3 (settings documented).** Every setting in `config.py` appears in the README configuration table and in
  `.env.example`: adds `TTL_PLAYER_SEARCH_SECONDS` (3600), `TTL_PLAYER_SECONDS` (300) and `PLAYER_MAX_SEASONS` (10).
  Defaults in the README match `config.py` exactly, not `.env.example`.
- **R4 (fixture provenance).** The README "Fixtures" section states both capture campaigns — the league set on
  2026-10-04 and the player set plus the four `ncaaf_*` rank fixtures on 2026-10-24 — and its "Covered:" list names
  NCAA FBS (ranked scoreboard/summary/team directory) and the player endpoints (search, profile, stats, game log for
  NFL, NBA, MLB). The matching "Known limitations" claim about which shapes tests cover says both dates.
- **R5 (invariants match tests).** `CLAUDE.md`'s size-budget invariant states the asserted facts: scoreboard text
  ≤ 6 KB and structured ≤ 25 KB on the fixtures, with the real 44-game college-football day guarded at < 40 KB per
  amendment A1 of the team-rank spec (asserted at `tests/test_team_rank.py:492-493`).
- **R6 (base spec complete).** `docs/SPEC.md` §7 TTL table gains the three player TTL rows and §10 gains the player
  settings, each attributed to `docs/specs/player-stats/SPEC.md` so the base spec stays consistent with its existing
  amendment pointers rather than silently redefining them.
- **R7 (no drift, gate green).** No file outside the seven in §5 changes; `make check` still passes with the same
  642 tests and ≥ 90% coverage; `ruff` and `mypy --strict` clean; no `tests/` file touched.
- **R8 (release commit).** One commit on `release/v0.2.0`, containing exactly the §5 files, message
  `chore(release): v0.2.0 — release notes and documentation refresh`; working tree clean afterwards; `main` and
  `feature/team-rank-and-turnovers` unchanged.

## 5. Design — files affected

| File | Change |
|---|---|
| `pyproject.toml` | `version = "0.1.0"` → `"0.2.0"` |
| `src/sports_mcp/__init__.py` | `__version__ = "0.1.0"` → `"0.2.0"` |
| `CHANGELOG.md` | `[Unreleased]` content → `## [0.2.0] - 2026-10-25`; new empty `[Unreleased]` header |
| `README.md` | Configuration table +3 rows (R3); Fixtures section + Known-limitations bullet reworded for both capture dates and added coverage (R4); scoreboard section gains the `docs/specs/team-rank-and-turnovers/SPEC.md` design pointer, matching the game-log section's existing pointer style |
| `CLAUDE.md` | Size-budget invariant reworded to state the 40 KB structured guard (R5) |
| `docs/SPEC.md` | §7 TTL table +3 rows, §10 config list + player settings, both attributed to the player-stats spec (R6) |
| `docs/specs/release-v0.2.0/` | This spec, plus `implementation-report.md`, `review-round-1.md` and `summary-report.md` from the SDD phases |

No source module under `src/` changes other than the `__version__` literal. No test, script, Dockerfile, compose
file or requirements file changes.

**Version string is not duplicated anywhere else** (verified): `espn_client.py:82` and `server.py:274` import
`__version__`; no test asserts a literal version.

## 6. Security and invariants

Nothing in this release touches the security posture. Explicitly preserved, because the release edits the files that
state them:

- Eleven read-only tools, exact set pinned by `tests/test_server.py::test_registered_tool_set_is_exactly_the_spec`.
- Only `espn_client.py` imports a network library; host allow-list; no agent-supplied URL/path segment.
- `meta.as_of` semantics; failed upstream calls never cached; no stale data without `meta.stale`.
- Loopback-only port, bearer auth, container hardening, no TLS (documented).
- Logs never carry arguments, results, upstream bodies, URLs or the token.
- `.env` values are never read out, printed or committed; this release reads no `.env` (the optional D5 smoke run
  reads it only through `scripts/smoke.sh`, which never prints it).

Deliberate doc-only deviation from the letter of `docs/SPEC.md` §7/§10 (R6): the base spec's TTL and configuration
lists are incomplete for the player settings. Fixed by attribution to the governing amendment, not by re-deciding it.

## 7. Out of scope

- Rewriting historical artifacts: `docs/reports/implementation-report.md` (states `User-Agent: sports-mcp/0.1.0`,
  true of the build it describes) and the per-feature `review-round-*` / `fix-round-*` / `summary-report.md` files.
  These are the decision trail for their own rounds and are correct as written; editing them would falsify the audit
  trail. Their `0.1.0` mentions are dated, not stale claims about the current build.
- The three "Open items for the human" in the per-feature summary reports (container rebuild + live smoke, client
  restart) except where D5 opts in.
- Digest-pinning the Dockerfile base image, the 40 KB structured-budget follow-up, Phase 2 stretch items
  (individual sports, odds snapshots).
- Any GitHub action (PR creation, merge, release publication).

## 8. Test plan and acceptance criteria

| Req | Verification |
|---|---|
| R1 | `grep -rn "0\.1\.0" --include='*' pyproject.toml src/` → empty. `python -c "import sports_mcp; assert sports_mcp.__version__=='0.2.0'"`. `python -c` check that `tomllib`-read pyproject version equals `__version__`. |
| R2 | Diff of the `[0.2.0]` section body against the pre-release `[Unreleased]` body is empty apart from the header; `## [Unreleased]` present and empty above it; `## [0.1.0]` section unchanged per `git diff -U0`. |
| R3 | Cross-check script (`config.py` fields vs `.env.example` vs README table) reports no missing key beyond the two documented container-internal ones (`HOST`, `PORT`, which the README documents as in-container); README defaults compared against `config.py` field defaults. |
| R4 | README contains both `2026-10-04` and `2026-10-24` in the Fixtures section and in the Known-limitations bullet; the fixture names listed match `git ls-files tests/fixtures` for the `ncaaf_*` and `player_*` sets. |
| R5 | `CLAUDE.md` budget sentence matches the assertions in `tests/test_render.py:53-54` and `tests/test_team_rank.py:492-493`. |
| R6 | `docs/SPEC.md` §7 lists the three player TTLs with the player-stats spec attributed; §10 names the three settings. |
| R7 | `make check VENV=~/.venvs/sports-mcp` → 642 passed, ≥ 90% coverage, ruff + `mypy --strict` clean. `git diff --name-only` before commit equals the §5 list (excluding this spec's own report files). |
| R8 | `git show --stat HEAD` lists exactly the §5 files; `git status` clean; `git log --oneline main..release/v0.2.0` shows `88bcbf8` plus the release commit; `git rev-parse main` and `feature/team-rank-and-turnovers` unchanged from their pre-release values. |

Acceptance = all rows pass. `make audit` is not required (no dependency change); if D5 is opted into, the smoke
output is captured into `implementation-report.md` instead.

## 9. Open questions

None. D1-D5 are decided above; the human confirms or overrides them at the approval gate, and D5 in particular is an
explicit opt-in.
