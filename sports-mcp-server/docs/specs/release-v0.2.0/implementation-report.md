# Implementation report — Release v0.2.0

- **Spec:** `docs/specs/release-v0.2.0/SPEC.md` (Status: Approved, 2026-10-25, approved by: human)
- **Role:** Implementer (AGENTS.md §6). Orchestrator runs the quality gate; `make check` was **not** run here.
- **Date:** 2026-10-25
- **Branch / base:** `feature/team-rank-and-turnovers` @ `88bcbf8` (working tree was clean apart from the untracked
  spec directory `docs/specs/release-v0.2.0/`).
- **Scope executed:** R1-R7 (documentation + version). **R8 (the release commit) is intentionally not executed** — the
  orchestrator's brief forbids any state-changing git command, and spec D3 leaves commit/branch creation to the human
  gate. Listed under *Deviations* as a scope carve-out, not a failure.

## 1. Files changed

Exactly the six documentation/metadata files of spec §5 (verified with `git diff --name-only`, §4 below):

| File | Change |
|---|---|
| `pyproject.toml` | `project.version` `0.1.0` → `0.2.0` (1 line) |
| `src/sports_mcp/__init__.py` | `__version__` `0.1.0` → `0.2.0` (1 line) |
| `CHANGELOG.md` | New `## [0.2.0] - 2026-10-25` header + blank line inserted above the existing (untouched) body; fresh empty `## [Unreleased]` remains above it |
| `README.md` | Configuration table +3 rows; Fixtures section rewritten for both capture campaigns; known-limitations bullet names both dates; scoreboard ranks paragraph gains the team-rank design pointer |
| `CLAUDE.md` | Size-budgets invariant bullet extended with the 40 KB structured guard (one bullet, nothing else touched) |
| `docs/SPEC.md` | §7 TTL table +2 rows + attribution sentence; §10 env list +1 attribution sentence |

Plus the new file this report: `docs/specs/release-v0.2.0/implementation-report.md`.

No file under `tests/`, `scripts/`, `tools/`, `docs/reports/`, `Dockerfile`, `docker-compose.yml`, `Makefile` or
`requirements*.txt` was touched. Nothing under `src/` changed except the single `__version__` literal. No `.env` was
read, printed, logged or written.

## 2. Requirement → implementation → verification traceability

| Req | Implementation | Verification (evidence in §4) | Result |
|---|---|---|---|
| **R1** version | `pyproject.toml:7` `version = "0.2.0"`; `src/sports_mcp/__init__.py:3` `__version__ = "0.2.0"`. No other tracked file was edited. `espn_client.py:82` (`User-Agent: sports-mcp/<version> (+local)`) and `server.py:274` (`serverInfo.version`) import `__version__`, so both derive from it — confirmed by grep, not edited. | `findstr /S /N /C:"0.1.0" pyproject.toml src\*.py` → no match (exit 1). `git grep -n "0\.1\.0" -- pyproject.toml src/` → no match (exit 1). `python -c` → `__version__ = 0.2.0`, `pyproject = 0.2.0`. | PASS |
| **R2** changelog | Moved the whole `[Unreleased]` body by inserting only two lines (`## [0.2.0] - 2026-10-25` + a blank line). No byte of any bullet was rewritten; `## [0.1.0]` and below were not touched. | SHA-256 of the moved `[0.2.0]` body == SHA-256 of the `[Unreleased]` body from `git show HEAD:CHANGELOG.md` (LF-normalized), unified diff empty; 7 bullets → 7; `### Added`/`### Changed` preserved; `git diff --numstat` = `2 0`; `git diff -U0` shows only the two added lines. See §3. | PASS |
| **R3** settings documented | Three rows inserted after `TTL_NEWS_SECONDS`, before `STALE_IF_ERROR_SECONDS`, in the existing 3-column style; table not reflowed. | Rows present verbatim and correctly ordered; defaults compared against `config.py:59-62` (`ttl_player_search_seconds = 3600`, `ttl_player_seconds = 300`, `player_max_seasons = 10`, `Field(ge=1, le=25)`). `.env.example:38,39,41` already carries all three (no edit needed). | PASS |
| **R4** fixture provenance | Fixtures section states both campaigns with dates, names the four `ncaaf_*` files, and extends the "Covered:" sentence with NCAA FBS + player endpoints. Kept the `capture_fixtures.py` strip list, the 300 KB cap and the autouse-guard/`MockTransport` sentences. | Every asserted number counted from the real files: 44 events and 17 ranked games in `ncaaf_scoreboard_ranked.json`; 762 teams in `ncaaf_teams_directory.json`; 23 `player_*.json` (search 6 / profile 2 / stats 6 / gamelog 9) covering NFL, NBA, MLB for stats and game logs; profile fixtures are NFL + NBA. All four `ncaaf_*` names confirmed in `git ls-files tests/fixtures`. Largest fixture 265,859 B (< 300 KB) and no tracked fixture ≥ 300 KB. | PASS |
| **R5** invariants match tests | `CLAUDE.md` size-budgets bullet keeps `structured <= 25 KB` and qualifies it as the scoreboard-fixture budget, then adds the 44-game college-football guard at < 40 KB structured, citing amendment A1 of `docs/specs/team-rank-and-turnovers/SPEC.md` and `tests/test_team_rank.py`. Still one bullet. | Values cross-checked at `tests/test_team_rank.py:492-493` (`<= 6_000` text, `< 40_000` structured) and `tests/test_render.py:53-54` (`<= 6_000` text, `<= 25_000` structured); A1's 34,293 B pre-change figure supports "already 34 KB before rank/record". No other bullet changed (`git diff` shows a 3-line → 5-line replacement of that bullet only). | PASS |
| **R6** base spec complete | §7 TTL table gains `Player name-search results 3600 s (TTL_PLAYER_SEARCH_SECONDS)` and `Player stats and game logs 300 s (TTL_PLAYER_SECONDS)`; one sentence follows the table attributing both TTLs and `PLAYER_MAX_SEASONS` (default 10, range 1-25, bounds `get_player_gamelog(season="all")`) to `docs/specs/player-stats/SPEC.md`. §10 gains a sentence naming the three settings with the same attribution. Additive only. | `docs/SPEC.md` contains each new row/sentence exactly once; §7 and §10 render as valid markdown (rows contiguous with the existing table, no stray blank line inside it). | PASS |
| **R7** no drift | Only the six files changed. | `git diff --name-only` → exactly the six paths (§4). `git status --porcelain` → the same six as ` M` plus the untracked spec directory that holds this spec and this report. No `tests/` file touched. Quality gate (`make check`, 642 tests, coverage, ruff, mypy) is the orchestrator's step. | PASS (docs-level; gate deferred to orchestrator) |
| **R8** release commit | Not executed by the implementer. | See *Deviations*. | DEFERRED to orchestrator/human |

## 3. How R2's "verbatim move" was proven

`CHANGELOG.md` is CRLF in the Windows working tree and LF in the git blob (`.gitattributes`: `* text=auto eol=lf`;
`git check-attr` reports `eol: lf`). The canonical comparison is therefore made on the LF-normalized form — i.e. on
what actually gets committed — and the raw working-tree byte state is reported separately.

The move was implemented as a **pure insertion** (the original body slice was reused byte-for-byte by the editing
script, which asserted `text[i:j]` equality before writing), so the check is not merely cosmetic:

| Check | Result |
|---|---|
| SHA-256 `[Unreleased]` body, `git show HEAD:sports-mcp-server/CHANGELOG.md` | `9fe6cb521a63f70fbde0f688466e05ae64a442c6207aaa423945af66220dec0d` |
| SHA-256 `[0.2.0]` body, worktree (LF-normalized) | `9fe6cb521a63f70fbde0f688466e05ae64a442c6207aaa423945af66220dec0d` |
| `difflib.unified_diff(head_body, new_body)` | `(empty)` |
| lines / `- ` bullets / `###` subheaders / `SPEC.md` references | 28 / 7 / `['### Added','### Changed']` / 3 — unchanged on both sides |
| `## [Unreleased]` empty above `## [0.2.0]` | True (only `\n\n` between the headers) |
| `## [0.1.0]` section to EOF | LF-normalized identical to HEAD |
| `git diff --numstat -- CHANGELOG.md` | `2 0 sports-mcp-server/CHANGELOG.md` (2 insertions, 0 deletions) |
| `git diff -U0 -- CHANGELOG.md` | the only added lines are `+## [0.2.0] - 2026-10-25` and `+` (blank) |
| `git diff --ignore-cr-at-eol -- CHANGELOG.md` | same two-line addition, in context under `## [Unreleased]` |

Note on the earlier raw-byte comparison: comparing the worktree body against `git show` output byte-for-byte returns
`False` purely because of the file's pre-existing CRLF working-copy convention (`git show HEAD:CHANGELOG.md` has 0
CR bytes; the checked-out file had 52 CRLF before this change too). That is the checkout's normal state, not a change
introduced here — `git diff` on the unmodified parts of the file stays empty.

## 4. Commands run, with results (verbatim)

All run from the server directory `sports-mcp-server/` (repo root is its parent).

### 4.1 R7 — changed-file set

```
$ git diff --name-only
[exit 0]
sports-mcp-server/CHANGELOG.md
sports-mcp-server/CLAUDE.md
sports-mcp-server/README.md
sports-mcp-server/docs/SPEC.md
sports-mcp-server/pyproject.toml
sports-mcp-server/src/sports_mcp/__init__.py
```

Exactly the six files of spec §5. (`git diff --name-only` prints repo-root-relative paths; the three `CRLF will be
replaced by LF` lines on stderr are git's normal `.gitattributes` notice for the three files that were already CRLF
in this Windows checkout before the change — `git status` shows no other modification.)

```
$ git status --porcelain
[exit 0]
 M sports-mcp-server/CHANGELOG.md
 M sports-mcp-server/CLAUDE.md
 M sports-mcp-server/README.md
 M sports-mcp-server/docs/SPEC.md
 M sports-mcp-server/pyproject.toml
 M sports-mcp-server/src/sports_mcp/__init__.py
?? sports-mcp-server/docs/specs/release-v0.2.0/
```

The only untracked path is this feature's own spec directory, which spec §5 assigns to this change
(`docs/specs/release-v0.2.0/`: the spec plus the SDD reports). Nothing else exists outside the six files.

### 4.2 R1 — no `0.1.0` left in version sources

```
$ findstr /S /N /C:"0.1.0" pyproject.toml src\*.py
[exit 1]        <- no match

$ git grep -n 0\.1\.0 -- pyproject.toml src/
[exit 1]        <- no match

$ python -c "import sys; sys.path.insert(0,'src'); import sports_mcp, tomllib; print('__version__ =', sports_mcp.__version__); print('pyproject =', tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
[exit 0]
__version__ = 0.2.0
pyproject = 0.2.0
```

About `__pycache__`: `/S` also descends into `src\sports_mcp\__pycache__\*.pyc`. It produced no match above, and in
any case it is irrelevant — it is untracked and gitignored, so it is not a distributed "tracked source" file:

```
$ git check-ignore -v src/sports_mcp/__pycache__
[exit 0]
sports-mcp-server/.gitignore:7:__pycache__/	src/sports_mcp/__pycache__
```

No attempt was made to "fix" `__pycache__` (it is regenerated stale-free by the interpreter). Per spec §7, the
dated `0.1.0` mentions inside `docs/reports/*` and the per-feature reports are deliberately left alone; they are
out-of-scope historical artifacts and are not version pins.

### 4.3 R2 — changelog

See §3 for the body-hash/diff table. Additional verbatim output:

```
$ git diff --ignore-cr-at-eol -- CHANGELOG.md
[exit 0]
diff --git a/sports-mcp-server/CHANGELOG.md b/sports-mcp-server/CHANGELOG.md
index d0994b2..6090d30... 
@@ -6,6 +6,8 @@ All notable changes to this project are documented here. The format follows
 
 ## [Unreleased]
 
+## [0.2.0] - 2026-10-25
+
 ### Added
 
 - Team rank and turnovers (spec `docs/specs/team-rank-and-turnovers/SPEC.md`): ESPN's poll rank (1-25) on game teams
```

Resulting header order in the worktree: `['## [Unreleased]', '## [0.2.0] - 2026-10-25', '## [0.1.0]']`.

### 4.4 R3 — README defaults vs `config.py`

```
config.py:59:     ttl_player_search_seconds: Annotated[int, Field(ge=0, le=86_400)] = 3600
config.py:60:     ttl_player_seconds: Annotated[int, Field(ge=0, le=86_400)] = 300
config.py:61:     # Most recent seasons fetched for get_player_gamelog(season="all") (older ones are omitted).
config.py:62:     player_max_seasons: Annotated[int, Field(ge=1, le=25)] = 10
```

README rows (each found verbatim exactly once):

```
| `TTL_PLAYER_SEARCH_SECONDS` | `3600` | `find_player` name-search results |
| `TTL_PLAYER_SECONDS` | `300` | Player season stats and game logs |
| `PLAYER_MAX_SEASONS` | `10` | Seasons fetched by `get_player_gamelog(season="all")` (1-25); oldest dropped first |
```

Ordering check (`TTL_NEWS_SECONDS` < the three new rows < `STALE_IF_ERROR_SECONDS`): `True`.
README defaults `3600` / `300` / `10` and the documented range `1-25` match `config.py` exactly. `.env.example`
already listed all three (`:38` `# TTL_PLAYER_SEARCH_SECONDS=3600`, `:39` `# TTL_PLAYER_SECONDS=300`,
`:41` `# PLAYER_MAX_SEASONS=10`) and needed no change; R3 requires README defaults to track `config.py`, which they do.

### 4.5 R4 — fixture counts verified before asserting them

```
$ git ls-files tests/fixtures | find /c ".json"
[exit 0]
65

ncaaf_scoreboard_ranked events: 44
ncaaf_scoreboard_ranked games with a ranked team (curatedRank 1-25): 17
  (matches tests/test_team_rank.py docstring: '44 games, 17 with a ranked team')
ncaaf_teams_directory teams: 762
  exists: True ncaaf_summary_ranked
  exists: True ncaaf_team_miami
  exists: True ncaaf_teams_directory
  exists: True ncaaf_scoreboard_ranked

player fixtures total 23 {'gl': 9, 'profile': 2, 'search': 6, 'stats': 6}
  name search (typo retries): 6 files
  athlete profile: 2 files, sports ['nba', 'nfl']
  season stats: 6 files, sports ['mlb', 'nba', 'nfl']
  game logs: 9 files, sports ['mlb', 'nba', 'nfl']

largest fixture: ncaaf_scoreboard_ranked.json 265859 bytes (< 300 KB: True)
tracked fixtures: 65
fixtures >= 300 KB: none (claim holds)
```

Provenance cross-check for the two capture campaigns:

```
$ git log --oneline -1 --diff-filter=A --name-only -- tests/fixtures/ncaaf_teams_directory.json
[exit 0]
88bcbf8 feat(sports): add team rank, ranked_only/sort on scoreboard and football turnover line
sports-mcp-server/tests/fixtures/ncaaf_teams_directory.json

$ git log --oneline -1 --diff-filter=A --name-only -- tests/fixtures/player_search_skattebo.json
[exit 0]
c28a740 feat(sports): add find_player, get_player_stats and get_player_gamelog tools
sports-mcp-server/tests/fixtures/player_search_skattebo.json
```

`tools/capture_fixtures.py` itself groups the four `ncaaf_*` entries under the comment
"Team rank and turnovers (spec: docs/specs/team-rank-and-turnovers). Captured 2026-10-24." and the `player_*` entries
under "Player endpoints (spec: docs/specs/player-stats)", and `docs/specs/team-rank-and-turnovers/SPEC.md:18` records
the rank capture as verified 2026-10-24 — the dates asserted in the README come from these, not from assumption.

Judgement call recorded: the README says the player fixtures cover "name search with typo retries, athlete profile,
season stats, game logs" for **NFL, NBA and MLB** — the union of sports present across the four player fixture kinds
(profile is NFL + NBA only, which the sentence does not claim sport-by-sport). The 44/17/762 figures are counted from
the fixtures themselves.

### 4.6 R5 — budget assertions read from the tests before restating them

```
test_team_rank.py:488: async def test_r8_scoreboard_text_budget_holds_on_the_ranked_board(...) -> None:
test_team_rank.py:489:     # The 25 KB structured budget is NOT asserted here: the 44-game day is already 34 KB
test_team_rank.py:490:     # structured without any rank or record field (see implementation-report.md, deviations).
test_team_rank.py:491:     out = await service.get_scoreboard("ncaaf", date=DAY)
test_team_rank.py:492:     assert len(out.text.encode()) <= 6_000
test_team_rank.py:493:     assert len(json.dumps(out.data).encode()) < 40_000

test_render.py:50: async def test_nfl_scoreboard_budget(service: SportsService) -> None:
test_render.py:53:     assert len(out.text.encode()) <= 6_000
test_render.py:54:     assert len(json.dumps(out.data).encode()) <= 25_000
```

`CLAUDE.md` checks: contains `< 40 KB structured` True · still contains `structured <= 25 KB` True · contains
`amendment A1` and `tests/test_team_rank.py` True. Amendment A1 of the team-rank spec gives the exact pre/post figures
(34,293 B → 36,011 B) that back the "already 34 KB before rank/record" clause.

### 4.7 Line endings preserved

Read before and after each edit; every file kept its pre-existing convention (byte-safe latin-1 round-trip edits, no
whole-file rewrites, no newline translation):

```
{'pyproject.toml': (1874, 0, 71), 'src/sports_mcp/__init__.py': (109, 0, 3), 'CHANGELOG.md': (3802, 52, 2),
 'README.md': (26147, 405, 0), 'CLAUDE.md': (6644, 0, 76), 'docs/SPEC.md': (24383, 311, 0)}
   (bytes, CRLF count, bare-LF count)
```

`pyproject.toml`, `__init__.py` and `CLAUDE.md` are LF-only before and after; `CHANGELOG.md`, `README.md` and
`docs/SPEC.md` keep the checkout's CRLF convention with no lone-LF introduced inside the file. Final CHANGELOG.md
state: 3,802 bytes, 52 CRLF + 2 bare LF, where the 2 bare LF are the separators of the two inserted lines
(`## [0.2.0] - 2026-10-25\n` and the following blank `\n`); the file's final byte is CRLF as before, and
`git diff --numstat` deletion count is 0, proving no existing line was rewritten. Git normalizes all six to LF on
commit per `.gitattributes`, so the committed result is LF content as required.

## 5. Deviations

**From the code/documentation scope: none.** No requirement was implemented differently from the spec, no extra
feature or opportunistic rewording was added, and no file outside spec §5 was modified.

**Scope carve-out (not a spec defect): R8 was not executed.** The implementer brief states as a hard rule "Do NOT run
any git command that changes state (no add/commit/branch/checkout/push)", while R8 asks for one commit on
`release/v0.2.0`. Per AGENTS.md §6 the orchestrator (not the implementer) returns to the human for such gates, and
spec D3 keeps commit/branch creation with the human-approved release step. R8's own verification
(`git show --stat HEAD`, `git status` clean, `git log --oneline main..release/v0.2.0`) therefore remains for the
orchestrator/human after the commit is made. The review-round and summary-report files named in spec §5 are also the
orchestrator's SDD artifacts, not the implementer's.

**Deferred by assignment:** the quality gate (`make check VENV=~/.venvs/sports-mcp`: 642 passed, ≥ 90% branch
coverage, ruff, `mypy --strict`) is run by the orchestrator; it was not run here. The changes are documentation and
version-string only — no import, signature, or behaviour changed — so no test update was required and none was made
(spec §5: "No test … file changes"). Spec §5 also records that no test asserts a literal version string, so R1 does
not break `tests/`.

**Not run:** D5 live smoke (spec: not opted into), `make audit` (no dependency change).

## 6. Judgement calls

1. **R2 comparison basis.** The verbatim-move proof is done on the LF-normalized (committed) form because the file is
   CRLF in this Windows checkout and LF in the blob; a raw-byte comparison against `git show` output would differ
   solely on the checkout's own pre-existing convention. Documented in §3 with both results.
2. **R2 mechanics as pure insertion.** Rather than retyping the bullets, the move was performed by slicing the exact
   body bytes out of the file and reusing them unchanged, so "not one word reworded, no bullet added or dropped" is
   structurally guaranteed and then hash-verified.
3. **R3 defaults source.** README values taken from `config.py:59-62` (as R3 requires), not from `.env.example`; the
   two agree today.
4. **R4 wording.** "Player endpoints … for NFL, NBA and MLB" is the union across the four player fixture kinds; the
   profile kind alone is NFL + NBA. Every named file and every number was counted from disk before being asserted
   (§4.5). The `2026-10-24` date for the `ncaaf_*` set is corroborated by `capture_fixtures.py`'s own comment and the
   team-rank spec; for the player set it is corroborated by `docs/specs/player-stats/SPEC.md` amendment A1 (2026-10-24)
   and the `player_*` capture commit `c28a740`.
5. **R5 kept to one bullet** (as instructed), which made it a 3-line → 5-line replacement of that single bullet; the
   original clause text was restored verbatim except for the added qualifier, so nothing was reworded for style.
6. **`docs/SPEC.md` additions are marked as amendments, not new rules** — both new §7/§10 passages attribute
   themselves to `docs/specs/player-stats/SPEC.md`, matching spec §6's "fixed by attribution, not by re-deciding it".
7. **README line-length.** The appended design pointer makes one scoreboard line longer than the file's usual wrap
   (mirroring the existing long lines in that section, e.g. the `get_player_gamelog` design-pointer line). R4d asked
   for the pointer at the END of the paragraph in the same style, so no rewrap was done ("do not restructure the
   section"). `ruff` does not lint markdown, so this cannot affect the gate.
