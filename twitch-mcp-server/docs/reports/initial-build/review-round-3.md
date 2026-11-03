# Review — Round 3 (initial build)

**Date:** 2026-10-30 09:37 -04:00
**Reviewer:** read-only reviewer subagent (Phase 5, reviewer role; fresh context; did **not** edit any code; did **not** take `fix-round-2.md` on trust)
**Round:** 3 of a maximum of 5 (A2-D4)
**Spec:** `docs/SPEC.md` — Status Approved (2026-10-29 19:28), with Amendment 1 (§0) and Amendment 2 (§0b), both approved by: human. Amendments are authoritative.
**Only file written by this reviewer:** `docs/reports/initial-build/review-round-3.md`.

**Commands run (via WSL `Ubuntu`, interpreter `/home/leeray/venvs/twitch/bin/python`, repo at `/mnt/c/.../twitch-mcp-server`):**
- `python -m ruff check src tests tools`
- `python -m ruff format --check src tests tools`
- `python -m mypy`
- `python -m pytest -q`
- Independent Python probes under `/tmp` (never in the repo): a regex/gate-attack harness, a B1 table re-derivation over the *actual* report, a 33-body F2/F3 hostile-body probe, and a coercion/count probe importing `project.py`/`capabilities.py` directly.
- No live calls, no `try_live.py`, no `TWITCH_LIVE_OK`, no docker, no `.env` reads.

---

## 1. Gate results (real output)

| Gate | Result |
|---|---|
| `ruff check src tests tools` | `All checks passed!` |
| `ruff format --check src tests tools` | `50 files already formatted` |
| `mypy` | `Success: no issues found in 49 source files` |
| `pytest -q` | **949 passed**, total coverage **90.80%** (branch gate 90%) |

**Verdict on the gate: GREEN, and it matches the claimed numbers exactly** (949 passed, 90.80%, mypy over 49 files). The "50 files already formatted" vs "49 source files" is not a discrepancy: ruff formats the whole tree (50 files incl. `tools/`), mypy is configured over `src`+`tests` (49). Weak spots unchanged from round 2 and expected: `service.py` 84%, `oauth.py` 86%, `twitch_client.py` 87%, `cache.py` 89%, `project.py` 92% — defensive/edge branches only, and `project.py` has *risen* from the round-2 floor of 90%.

---

## 2. Per-finding verification

| ID (round 2) | Severity | Claimed fix | Verification | Result |
|---|---|---|---|---|
| **B1** | blocker | Regenerated table (awk `NR`→`FNR`); new `test_gate.py` cell→`def` check | Machine-verified **all 57 rows / 154 cells / 136 distinct `(file,test)` pairs** against real `def`s; 0 misattributions. Attacked the enforcement (§3). | **CLOSED** |
| **F2** | major | `_AUTOMOD_LEVEL_KEYS` gate keyed on level keys, not `broadcaster_id` | 6 hostile bodies + `project.py:629` | **CLOSED** |
| **F3** | major | `_build` converts `pydantic.ValidationError`→`upstream` at the one choke point | 33 hostile bodies across **every** projection: **0 leaks**; only one `model_validate` in `src/`; boundary safe | **CLOSED** |
| **F1** | minor | Inline 404 hint dropped; `DEFAULT_HINTS["not_found"]` points only at id/login | `twitch_client.py:801-807`, `errors.py:63`; R27 invariants re-checked | **CLOSED** |
| **F4** | minor | Accepted as documented limitation (schedule accepts `data` w/o `segments`) | Reasoning tested empirically | **ACCEPTED (judged sound)** |
| **F5** | minor | `config.py` `# type: ignore[call-arg]` cleared in round 2 | Unchanged; legitimate | **CLOSED** |

### B1 — traceability (CLOSED)
Independently parsed §2 of `implementation-report.md` (regex `^\| (R\d+) \| (.+?) \|\s*$`) and cross-checked every `test_x.py::test_y` cell against `^(?:async )?def test_y` in `tests/test_x.py`. **All mandated rows verified REAL** (R1, R5, R14, R17, R18, R35, R37, R43, R44, R46, R51, R55, R56, R57 — 33 cells, plus full 154-cell sweep): every cell is a real `def` in the named file. Cross-file misattribution count = **0** (the round-2 "9 cells against the wrong file" are gone). Distinct files cited: 22 real test modules.

### F2 — automod silent-empty (CLOSED)
`project.py:300-311` defines `_AUTOMOD_LEVEL_KEYS` (the 9 level names). `project.py:629` gates on
`recognized = [key for key in _AUTOMOD_LEVEL_KEYS if record.get(key) is not None]`; if empty → `TwitchMcpError("upstream", …)` (`project.py:630-635`). It keys on **levels, not `broadcaster_id`**, so the legacy nested body cannot slip past. Empirical probes:
- `{"data":[{"broadcaster_id":"1","moderator_id":"2"}]}` → **upstream** ✔
- `{"data":[{"overall_level":None}]}` → **upstream** ✔
- legacy `{"data":[{"broadcaster_id":"1","moderator_id":"2","settings":[…]}]}` → **upstream** ✔
- `{"data":[{}]}` → **upstream** ✔
- `{"data":[{"overall_level":0}]}` → **projects** `overall_level=0` (not treated absent) ✔
- `{"data":[{"disability":0}]}` → **projects** `disability=0` ✔
- `{"data":[]}` → **`settings=None`** (genuinely empty) ✔ — the only path to `settings=None` is `if not records:` at `project.py:622`.

### F3 — `ValidationError` → upstream (CLOSED)
`project.py:94-105`: `_build` wraps `model.model_validate` and converts `ValidationError`→`TwitchMcpError("upstream", …) from exc`. Confirmed **`_build` is the only entry point for Helix records into models**: `grep model_validate|model_construct src/` returns exactly one hit (`project.py:95`). The two direct model constructions that bypass `_build` are provably non-leaking:
- `project_chat_badges` `BadgeSet(set_id=str(record.get("set_id","")))` — `str()` coercion of any value.
- `project_markers` `Marker(video_id=str(record["video_id"]) …)` — `str()` coercion (KeyError pre-guarded by `"video_id" in record`).

Probed 33 hostile bodies (dict/list values in typed fields, wrong-typed `tags`/`scopes`/`total`/`expires_in`/levels/`chatter_count`, malformed nested `choices`/`outcomes`/`segments`/`user`/versions) across **every** `project_*`: **0 non-`TwitchMcpError` leaks**. A `dict` in a `str` field is **dropped** (because `_build` filters `record[key] is not None`, `project.py:93`) not coerced — so no R38 schema-widening. Boundary check (`server.py:734-740`): `TwitchMcpError`→`_error_result` (`isError`); any other `Exception`→`MCPError(INTERNAL_ERROR, "Internal server error; see the server logs.")` with the type recorded only in logs (`server.py:738-739`). A projection `ValidationError` reaching `_run` would yield a generic, non-leaking JSON-RPC error — but since `_build` never lets one escape, this is moot. R43 satisfied.

### F1 — `not_found` hint (CLOSED)
`twitch_client.py:801-807` (404 read branch): `TwitchMcpError("not_found", "Twitch has no such object", …)` with **no inline `hint=`, hence no allowlist wording**; the comment at `:799-800` records the A2-D2 rationale. `errors.py:63` default hint = *"Twitch has no such user, channel, video, tag or segment… Check the id or login…"* — allowlist-free. R27 invariants re-confirmed unchanged: `not_found ∈ ErrorKind` (`errors.py:37`) and `∈ DEFAULT_HINTS` (`:63`), **∉** `PASS_THROUGH_KINDS` (`:54-56`, `rate_limited`/`unauthenticated`/`upstream` only); `guard.py:143` (`if exc.kind not in PASS_THROUGH`) collapses `not_found` to `forbidden_channel`; write-404 → `unknown_outcome` (`twitch_client.py:793-797`).

### F4 — schedule `segments` presence (judged: reasoning sound, ACCEPT)
Empirically `project_schedule({"data":{"broadcaster_id":"1"}})` returns a schedule with **absent** `segments`, while `{"segments":[]}` returns `segments=[]` — a real, if cosmetic, silent-empty asymmetry the implementer chose not to close. The justification is **sound, not a rationalisation**: (a) Helix's schedule `data` always carries `segments`, so the trigger is hypothetical; (b) unlike automod's wrong shape, a schedule can legitimately have zero upcoming segments, so a hard presence check risks a **false `upstream` failure on a real operator account** (A3 being operator-run, unobserved); (c) the failure mode is cosmetic (an empty schedule), not the all-`None` "looks like a result" laundering that F2/R45 forbids. This is a genuine trade-off, not a dismissal. I accept it.

### F5 — cleared
`config.py` `# type: ignore[call-arg]` unchanged; legitimate (pydantic-settings injects env). Closed in round 2.

---

## 3. B1 gate-attack results specifically

`tests/test_gate.py::test_every_traceability_cell_names_a_test_defined_in_that_file` is a **material hardening** of round-2's vacuous check and is **not fooled by any realistic bad table**, but it is **not exhaustive** — the same class of gap B1 was. Findings (verified by running the exact regexes on synthetic bodies):

**What it catches (strong):**
- A cell `test_a.py::test_b` where `test_b` is defined **only** in `test_c.py` → **CAUGHT**. `defined` is per-file (`{p.name: set(defs in p)}`), and the guard tests `fn not in defined.get(name, set())`, so a same-name-different-file citation produces `wrong=[...]` → fail. (A *duplicate* test with that name in the cited file would pass — an acceptable limitation; test names are conventionally unique.)
- Cells with trailing `,` / `.` / parenthetical / path prefix (`src/test_a.py::…`) → the `\b(test_\w+\.py)::(\w+)\b` token still matches and is verified correctly.
- Deleting the whole table → caught by `test_the_report_has_a_row_for_every_requirement` (`^\| R(\d+) \|` → 57 missing → fail), **independently** of the cell check.

**Residual gaps (the `>= len(_spec_requirements())` guard is the weak link):**
1. **One valid test cited 57× passes.** `| R1 | test_gate.py::test_check_runs_the_three_gates |` repeated for all R1–R57 → `len(cells)=57 ≥ 57` and every cell is a real `def` → **gate passes on a completely bogus table** that cites a single unrelated test. The guard is a *count*, not a *coverage* assertion; it never requires the 57 cells to span requirements or even be distinct.
2. **A table of 57 rows with empty cell text** (`| R7 |  |`) passes the row test and, if ≥57 valid `file::test` tokens exist **anywhere** in the report (e.g. the §1 module table / prose), still passes the cell check — the check scans the **whole report body**, not just the §2 table.
3. **Row-format brittleness** (benign — fails loud, not silent): `|R7|` (no spaces), `| R7|` (one space), `| R07 |` — none match `^\| R(\d+) \|`; they would be reported as "missing" and fail the run rather than slip through. Acceptable (fail-closed), but worth knowing.
4. **Class-qualified cells** (`file::TestFoo::test_bar`) resolve to `TestFoo` and would fail even if `test_bar` is a real method — brittle if a pytest class is ever used (none is today; not a current defect).

**Conclusion:** the current on-disk table is correct and *would* be caught if corrupted in any normal way (rename, wrong file, deletion). The only way to satisfy the gate with an incorrect table is to **deliberately** stuff it with ≥57 references to a handful of real-but-irrelevant tests. That is a real residual weakness for a mechanical A8 gate, but it requires intent and the delivered table is honest — so it is a **minor**, not a blocker.

---

## 4. New findings

### Minor
**N1 (minor) — B1 cell guard is a count, not a coverage assertion** (`tests/test_gate.py::test_every_traceability_cell_names_a_test_defined_in_that_file`). As §3.1–3.2: one real test cited 57×, or empty §2 cells plus 57 valid tokens elsewhere in the report, both satisfy the gate. **Suggested fix (for the implementer, not done here):** instead of `assert len(cells) >= len(requirements)`, parse the §2 table **row by row** and assert (a) each `| R<n> |` row yields ≥1 cell, and (b) the set of requirements covered by *valid* cells equals `_spec_requirements()`. That makes "one test cited 57×" and "empty cells" both fail.

**N2 (minor) — implementer scratch tooling left in the repo root** (`.gitignore`-visible): `scratch_gate.sh` and `scratch_rmap.awk` (the round-2 table generator; the awk `FNR` fix from B1 lives here). They are throwaway build helpers, not product or test code, and are **not** covered by `.gitignore` (which lists `.coverage`, caches, `data/` — not `scratch_*`). They inflate the ruff-format file count (50 vs the 49-file mypy set) and are clutter outside the approved deliverable set. **Suggested fix:** delete them or move under a gitignored `scripts/scratch/`; no behavior depends on them (their logic is reproduced inside `test_gate.py`).

### Non-findings (checked, no issue)
- **New projection tests are real, not tautologies.** `test_automod_settings_projection_reads_integer_levels` asserts `overall_level==2`, `swearing==3`, and `model_dump(exclude_unset=True)["disability"]==0` / `["race_ethnicity_or_religion"]==0` (the zero-preservation guarantee). The two rejection tests assert `pytest.raises(TwitchMcpError)` **and** `excinfo.value.kind == "upstream"`. Bodies are **documented** (flat `user_id/user_login/user_name` + `total` for chatters; single-object `data`+`segments` for schedule; integer-level record for automod; a real nested `category{id,name}`), not shaped to pass. No `assert True`, no "did not raise" pseudo-tests.
- **New gate tests assert on real Makefile/pyproject/report content**, not on stubs; `test_the_report_has_a_row_for_every_requirement` and `test_the_citation_map_is_not_stale` read the actual files.
- **Docs match code.** `capabilities.py:112 MAX_TOOL_COUNT=41`; measured `READ=23, ACCOUNT=2, WRITE=16, ALL=41, DEFAULT_REGISTERED=16` → 16 default / 25 signed-in / 41 max / 16 writes all correct. README error-kind table has `kind=not_found` (`README.md:315`) with explicit non-allowlist wording; README §Result size (`:220`) and CHANGELOG A2 items (`:61-68`, A2-D1/D2/D3) are present and internally consistent with §2/§4/§5 of `implementation-report.md`.

---

## 5. Verdict

# APPROVED

Round 2's blocker **B1** and majors **F2/F3** are **genuinely fixed, not cosmetic** — verified independently (57/57 rows and 154 cells resolve to real `def`s; every projection returns `kind=upstream` on mistyped/malformed Helix bodies with **0** `ValidationError` leaks; the automod guard keys on level values and preserves a `0`). Minors **F1/F4/F5** are closed or soundly accepted. The gate is green at the claimed 949 passed / 90.80% / mypy-clean, and the documentation matches the code.

The two new items are **minors** and do not gate approval: N1 is a hardening opportunity in an already-delivered-honest traceability gate, and N2 is repo hygiene.

**If the human wishes to tighten before merge** (single most valuable next step, optional): strengthen N1 — make `test_every_traceability_cell_names_a_test_defined_in_that_file` assert *per-requirement coverage of valid cells* rather than a bare `len(cells) >= len(requirements)` count, so a table that cites one real test 57 times (or leaves §2 cells empty while citing valid tests elsewhere in the report) can no longer satisfy the A8 gate. Deleting the two `scratch_*` files (N2) is a one-command cleanup. Neither is required for approval.
