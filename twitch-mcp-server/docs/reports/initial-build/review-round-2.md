# Phase 5 Review — Round 2

**Spec:** `docs/SPEC.md` (Status: Approved; Amendment 1 A1-D1..A1-D7; Amendment 2 A2-D1..A2-D5)
**Date:** 2026-10-30
**Reviewer:** read-only subagent (Phase 5, distinct from implementer). I edited no file except this report.
**What I ran:** `ruff check`, `ruff format --check`, `mypy`, `pytest -q` (with coverage) inside WSL via the venv interpreter; plus targeted `grep`/AST probes and two throwaway scripts under `/tmp` + a temp dir (one empirically exercising the three A2-D3 projections, one re-measuring the R41 size table). I did **not** run `try_live.py`, set `TWITCH_LIVE_OK`, run docker, or make any network call. A2/A3/A7 are UNVERIFIED by ruling A2-D5 and are **not** counted as defects below.

---

## 1. Gate results (real output)

All commands run from the repo root via `wsl.exe -d Ubuntu`, interpreter `/home/leeray/venvs/twitch/bin/python`.

**`python -m ruff check src tests tools`**
```
All checks passed!
```

**`python -m ruff format --check src tests tools`**
```
50 files already formatted
```

**`python -m mypy`** (config: `strict = true`, `files = ["src", "tests"]`)
```
Success: no issues found in 49 source files
```

**`python -m pytest -q`**
```
940 passed in 77.43s (0:01:17)
...
Required test coverage of 90% reached. Total coverage: 90.69%
```

**Verdict on the gate: GREEN, and it matches the implementer's claimed numbers exactly** (940 passed, 90.69%). Per-module weak spots from the coverage report: `service.py` 84%, `oauth.py` 86%, `twitch_client.py` 87%, `cache.py` 89%, `project.py` 90%. These are the genuinely risk-bearing modules (write dispatch, OAuth, HTTP) — the uncovered lines are overwhelmingly defensive/edge branches, not whole paths, and the total still clears the 90% branch gate. Note `project.py` is at the floor (90%) and is exactly where I found two real defects (see F2, F3).

The gate is genuinely enforced, not cosmetic: `tests/test_gate.py::test_pyproject_holds_the_strict_and_coverage_gates` pins `strict = true`, `files = ["src","tests"]`, `--cov-fail-under=90`, `branch = true`, `--strict-markers`, and the live-test opt-in marker. That enforcement is real (I read the regexes; they are anchored and would fail on a weakened `check:` line or a dropped `--cov-fail-under`).

---

## 2. Traceability spot-check (A8 / R57)

`docs/reports/initial-build/implementation-report.md` §2 carries a row for **every** R1–R57 (verified mechanically). I spot-checked 12 rows across the range for (a) the named test existing and (b) the named test actually asserting the requirement rather than passing on the token "R42":

| Row | Named test | Exists? | Actually asserts? |
|---|---|---|---|
| R15 | `test_twitch_client.py::test_a_5xx_is_retried_up_to_the_documented_bound` | yes | yes — read-retry bound |
| R16 | `test_errors.py::test_unknown_outcome_hint_forbids_a_re_issue` | yes | yes — hint text pins "do not re-issue" |
| R21 | `test_ratelimit.py::test_429_parks_only_the_bucket_that_was_told_to_block` | yes | yes — per-bucket park |
| R25 | `test_guard.py::test_allowlisted_broadcaster_is_accepted_without_a_request` | yes | yes — request_count == 0 |
| R27 | `test_guard.py::test_a_5xx_that_reveals_a_non_allowlisted_owner_is_still_a_uniform_denial` | yes | yes — identical denial text |
| R4 | `test_security.py::test_a_valid_token_reaches_the_application` | yes | yes — bearer accept path |
| R14 | `test_twitch_client.py::test_oversized_response_is_refused_without_leaking_body` | yes | yes — byte cap + no leak |
| R32 | `test_confirmation.py::test_args_hash_is_key_order_independent_and_sensitive_to_values` | yes | yes — hash binding |
| R34 | `test_service.py::test_r34_guard_runs_again_after_the_token_is_consumed` | yes | yes — guard re-run post-consume |
| R42 | `test_project.py::test_malformed_body_is_upstream_error_never_empty_list` | yes | yes — upstream≠empty |
| R41 | `test_sizes.py::test_worst_case_page_stays_under_4_mib` | yes | yes — measured, see §6 |
| R44 | `test_errors.py::test_pass_through_kinds_are_the_r27_set` | yes | yes — PASS_THROUGH set |

The spot-checked rows are honest. **But the table is not wholly honest**, and the mechanical R57 gate that is supposed to guarantee it is **vacuous** — see **B1**.

---

## 3. Round-1 finding disposition

**Closed (verified by me):**
- **B1/B2/B3 (the three frozen Helix-shape/404 blockers)** — resolved per Amendment 2 rulings A2-D2/A2-D3. All three size tests now pass; I independently exercised the projections (§4 area 5) and confirmed the new shapes are accepted and wrong shapes rejected. **However B1's ruling is only partially honored** — see **F1/F2/F3** and B1 below.
- **B4 (missing R11 grep test)** — CLOSED. `tests/test_network_guard.py::test_twitch_client_is_the_only_module_that_imports_httpx` now exists and is a genuine AST-based import scan (reads `ast.Import`/`ast.ImportFrom`, asserts the offender set == `{"twitch_client.py"}`). A stray `import httpx` in `service.py` would fail it.
- **M1/M2/M3 (doc counts 15/40/3-retries/23)** — CLOSED. README:142 "Writes (16)", README:156 "Maximum **41**… default is **16**"; CHANGELOG:26 "Sixteen write tools"; CLAUDE.md:15 "16 write tools", :98 "default… 16… signed-in… 25… maximum is 41"; `TWITCH_MAX_5XX_RETRIES` default `2` now agrees across README:81, `.env.example:48`, and code. Not_found is in the README error table (README:315) with the correct "not an allowlist answer" caveat.
- **M4 (no direct test for the 401 listener)** — CLOSED. `test_ratelimit.py` now has `test_the_auth_listener_fires_exactly_once`, `test_a_raising_listener_never_masks_the_401`, and `test_the_listener_cannot_recurse_through_the_circuit`. I read all three; they genuinely pin once-fire, non-masking (circuit opens + caller still gets `unauthenticated`/401 even when the hook raises), and non-recursion (latch set before hook). `trip_auth` (ratelimit.py:263-268) sets `_auth_failed=True` before the hook and wraps it in `except Exception` — the 401 cannot be masked or looped.
- **M6 (`scripts/auth` stale reference)** — CLOSED. `findstr scripts/auth` over `src/` and `tools/` returns nothing; the A1-D6 removal is reflected.
- **m1 (duplicate `.json.json` fixtures)** — CLOSED. `tests/fixtures/` holds exactly 6 clean JSON files; no doubled twins.
- **m5 (client_id-only R13 test)** — CLOSED. `test_twitch_client.py::test_a_helix_call_missing_only_the_client_id_is_refused_before_the_socket` now exists.
- **m6 (implementation report missing)** — CLOSED. The report exists with an R1–R57 table — but see B1 for its integrity failure.

**Not fully closed / regressed into new form:**
- **M5 (R41 size table placeholder)** — the placeholder is gone and the table is filled with real measured numbers; I re-measured independently and they match (see §6). Closed.
- **m2/m3 (stale count literals)** — partially: `MAX_TOOL_COUNT = 41  # 23 reads + 2 account + 16 writes` (capabilities.py:112) is correct; I did not re-find the "at most 40"/"40 capability names" literals, so treat as closed.

---

## 4. Adversarial verification of the 8 assigned areas

**1. A8/traceability integrity — FAILS.** See **B1**. `test_gate.py::_spec_requirements`/`_cited_requirements` enforce R57 by scanning tests for the bare token `\bR\d+\b` anywhere in the file (docstrings included), and cross-checking only against SPEC's own `^- **R(\d+)\*\*` list. It never validates the **implementation-report table** and never checks that a *named* test exists. Deleting a citation from a docstring, or citing a fabricated test name in the report, passes. Separately, I machine-verified every `file::test` cell in the report table against the real test files: **9 cells cite functions that do not exist** (wrong file — the function lives in a different test file). See B1.

**2. `not_found` ruling (A2-D2) — implemented but R27-adjacent hints leak; two projection defects.** (a) `not_found` is in `ErrorKind` (errors.py:41) and `DEFAULT_HINTS` (errors.py:67), and is **not** in `PASS_THROUGH_KINDS` (errors.py:56) — correct. (b) Guard-path collapse holds: `guard.resolve_owner` (guard.py:94-121) funnels every non-outage lookup failure — including `upstream`/404 and `permission`/403 — into the identical `forbidden_channel`/`OWNER_REFUSAL` with `http_status=None`/`code=None` (test_guard.py:222-238 asserts no status/code leak). `_is_outage` (guard.py:141-155) only passes through 5xx/no-status `upstream`, `rate_limited`, `unauthenticated` — a guard 404/403 cannot leak `not_found`. (c) A WRITE 404 → `unknown_outcome` (twitch_client.py:793-797), pinned by `test_write_safety.py` param `(404, {"unknown_outcome"})`. Good. **(d) BUT** the direct-read `not_found` hint (twitch_client.py:802-803) says *"If it names a channel, that channel must also be in TWITCH_ALLOWED_BROADCASTER_IDS…"* — this **reintroduces the probe-oracle ambiguity A2-D2 was meant to end**: a caller receiving `not_found` on a path where the guard never ran is told the failure might be an allowlist problem, blurring "does not exist" vs "not allowlisted" for the very distinction the amendment draws. See **F1**.

**3. Write safety — PASSES.** Writes never retried (test_write_safety.py:157-206 asserts `request_count == 1` on 500/502/503/404/transport-error, `retryable is False`, hint says check channel); the static `test_retry_helper_is_unreachable_from_every_write_path` walks the client AST. Two kill switches enforced (R28, two independent defaults). Confirmation tokens single-use, TTL, bound to `(tool, args-hash, broadcaster_id)` with `hmac.compare_digest` on the hash (confirmation.py:159) and cleared on disconnect/refresh/401 (`service.on_identity_change` → `_identity.invalidate()` **and** `_confirmations.clear()`, service.py:414-422). R34 re-check after consume: service.py:1607-1608 (`consume(...)` then `_guard_broadcaster(...)` again) with `test_r34_guard_runs_again_after_the_token_is_consumed` and the roster-recheck no-send tests. Solid.

**4. Security posture — PASSES.** Loopback bind via compose (`127.0.0.1:8770:8000`); Host/Origin guard in `security.py` on every route. Bearer required everywhere except `GET/HEAD /healthz` (security.py:142) and, only when `TWITCH_USER_OAUTH=true`, `GET` on the two `/oauth/*` paths passed explicitly by the caller (security.py:145; the default open-path set is empty). The R13 credential exemption is scoped to `POST /oauth2/token` **only**, and keyed on the exact path *and* both-headers-None (twitch_client.py:947-953) inside a function that first refuses any path not in `{OAUTH_TOKEN_PATH, OAUTH_REVOKE_PATH}` (:941). No other endpoint can borrow it. First-401 invalidation is the listener verified under M4 above.

**5. Three Helix shapes (A2-D3) — implemented; projections reject the *wrong* shapes but two accept *degenerate* ones.** I exercised `project_chatters`, `project_schedule`, `project_automod_settings` directly:
- chatters: accepts `data` list + `total`→`chatter_count`; rejects `data` as Mapping / missing → `upstream`. ✔
- schedule: accepts `data` as an object holding broadcaster fields + `segments`, nested `category` object preserved; rejects bare-array `data`, the old flat top-level `broadcaster_id` body, and `{}` → `upstream`. ✔
- automod: accepts the single integer-level record; `data: []` → `settings=None` (legit empty). **But** it **ACCEPTS the old wrong shape** (`data:[{settings:[…]}]`) and silently returns `AutomodSettings(settings=<only broadcaster_id, all levels None>)` — the exact "empty-but-not-really-empty" failure R45/G5 forbids, which A2-D3 was raised to fix. **And** a non-integer level (e.g. `overall_level:"high"`) raises an **uncaught pydantic `ValidationError`**, not an `upstream` error — the service call path does not wrap it (service.py wraps only `TwitchMcpError`), so it becomes a JSON-RPC INTERNAL_ERROR (server.py:737) instead of a tool error. See **F2/F3**. The `tests/test_sizes.py` fakes (test_sizes.py:303-330) **match the documented Helix shapes**, not shapes engineered to pass — the schedule fake builds `data` as an object with `segments`, the chatters fake is flat with `total`, the automod fake is the integer record. **No product/test circularity found.**

**6. Docs match code — PASSES.** R41 table: I re-measured every tool at max `first`. `get_stream_markers` = **1,294,798 B**, README "1.29 MB" (1.23 MiB — README uses decimal MB, internally consistent with badges "842 KB" vs my measured 862,306 B = 842.1 KiB). The largest really is `get_stream_markers`, badges next. The README explicitly says "the assertion is the contract; these numbers are its by-product" and `test_worst_case_page_stays_under_4_mib` measures the same path I did. Consistent. Error-kind table includes `not_found`. Counts (16/25/41, 16 writes, 6 groups) match code and spec across all three docs. `TWITCH_MAX_5XX_RETRIES` default 2 consistent.

**7. A5 literal-vs-intent — classify honestly.** The literal acceptance command `grep -rn httpx src/ | grep -v twitch_client.py` is **NOT empty**: it returns prose mentions in `logging_setup.py:205-206`, `ratelimit.py:4`, `server.py:1673` plus `__pycache__/*.pyc` binary hits. **None is an import.** The AST import test (§B4-closed) is a genuine substitute that pins the *intent* (only `twitch_client.py` imports httpx). Classification: **the literal A5 wording is unsatisfiable by construction** (the repo legitimately mentions `httpx` in prose and silences its third-party logger); the AST test satisfies R11's real requirement. Recommend the human amend A5's literal to "no non-`twitch_client.py` module *imports* httpx," which the AST test already enforces. Not a code defect.

**8. Dead code / spec creep / rot — minor only.** No `cast` misuse. The `# type: ignore[call-arg]` (config.py:295) and `noqa` set are all legitimate (S105 on non-secret path/class literals, S104 on container binds documented as compose-restricted, SLF001 on SDK registry, BLE001 on the listener guard, S311 on jitter) — none silences a real type hole. `DEFAULT_REGISTERED` (capabilities.py:251) correctly derives the 16 app-token reads. No extra tools, modules or config knobs beyond spec. No test asserts nothing or skips silently (live tests are marker-gated out via `--strict-markers -m 'not live'`).

---

## 5. Findings

### Blocker

**B1 — R57/A8 traceability enforcement is vacuous, and the implementation-report table cites 9 nonexistent tests.**
`tests/test_gate.py` enforces "every requirement maps to a named test" by (1) extracting `R\d+` tokens from anywhere in each test file's text and (2) checking only against SPEC's own requirement list — never against the implementation-report table, and never verifying a cited function exists. Consequences: (a) removing a `R42` citation from a docstring silently drops that requirement's coverage signal with no test failing (I confirmed by reasoning about the regexes: they match docstring prose, so the "citation" is not tied to any assertion); (b) a fabricated `file.py::test_that_does_not_exist` in the report passes. I mechanically validated **every** `file::test` cell in `implementation-report.md` §2 and found **9 cells pointing at the wrong file** (function exists elsewhere): e.g. R1/R2/R5 → `test_deployment.py::test_prune_removes_only_expired` (actually `test_confirmation.py`), R3/R5/R33 → `test_main.py::test_redaction_survives_a_non_utf8_shaped_message` (actually `test_logging.py`), R17/R18 → `test_twitch_client.py::test_the_only_accepted_argument_is_the_write_switch` (actually `test_try_live.py`), R35/R37 → `test_capabilities.py::test_a_tool_bucket_never_exceeds_its_capacity` (actually `test_budget.py`), R43/R44 → `test_errors.py::test_compose_mounts_no_host_directory_by_default` (actually `test_deployment.py`), R46/R51 → `test_oauth_units.py::test_unregistered_request_is_counted_and_visible` (actually `test_network_guard.py`), R55 → `test_network_guard.py::test_write_confirmation_rejects_unknown_plan_keys` (actually `test_models.py`), R56/R57 → `test_gate.py::test_message_never_contains_the_kind_or_hint_prefix` (actually `test_errors.py`). Each cited test *does* exist — just in a different file than the report claims — so the tests are real and the requirements are covered; the defect is that A8's "requirement→implementation→test" traceability is **demonstrably wrong in 9 rows and mechanically unverifiable**. AGENTS.md §7 makes reviewer-independently-verifiable traceability a hard gate; why it matters: a reviewer who trusts the gate (as an orchestrator will) never notices the table rotting, and a future regression that renames a test breaks the map with no signal. **Fix:** have `test_gate.py` parse the report table and assert each `file::test` cell resolves to a real `def` in the named file (a ~20-line addition), and correct the 9 mis-filed rows.

### Majors

**F2 — `project_automod_settings` silently accepts the wrong (pre-A2-D3) shape and returns an all-`None` record, the exact R45/G5 violation A2-D3 was created to eliminate.** `project.py:598-615` calls `_build(AutomodSettings, records[0], _AUTOMOD_SETTINGS_KEYS)`; `_build` (project.py:86-94) keeps only keys present and non-None, and `AutomodSettings` (models.py:293-304) has **every field optional**. A `GET /moderation/automod/settings` response whose one record lacks the integer level fields — including the *old* `{data:[{broadcaster_id, settings:[…]}]}` shape — yields `AutomodSettingsResult(settings=<record with only broadcaster_id, all levels None>)`: a successful-looking, effectively-empty result with no error. Verified empirically (§4 area 5). The tool's own docstring (project.py:602-606) states reading for a nested list "would answer 'no settings' to every real response, and R45 forbids an empty result that does not actually mean empty" — but the projection now has the *same* silent-empty failure mode it set out to prevent, just triggered by a malformed/level-less record rather than a nested list. **Fix:** after building, if the record has no `broadcaster_id`/`moderator_id` **and** no recognized level key present, raise `upstream` "response body this server does not understand" (mirroring the chatters/schedule guards), so a wrong shape is never laundered into an empty result.

**F3 — `project_automod_settings` (and any `_build` of an int/bool-typed model field) leaks pydantic `ValidationError` as an INTERNAL_ERROR instead of an `isError` tool result (R43).** `overall_level` etc. are `int | None`; a response carrying `overall_level:"high"` (Twitch/agent-visible variation, or a hostile/mis-shaped body) raises `pydantic.ValidationError` inside `_build` (project.py:94 `model_validate`). The service call path wraps only `TwitchMcpError` (service.py:444, :540); the generic `except Exception` in `server._call` (server.py:737) turns it into `MCPError(INTERNAL_ERROR, "Internal server error…")`. R43 says tool failures must be `isError` tool results carrying `{kind,…}` and reserves JSON-RPC errors for protocol faults/unexpected exceptions — a malformed upstream body is an expected upstream fault and belongs in `kind=upstream`. The chatters/schedule projections guard their `isinstance` shape but none of them guard value types; the same class of leak exists for any model field whose type is violated. **Fix:** catch `ValidationError` in the projection (or `_build`) and re-raise as `TwitchMcpError("upstream", …)`.

### Minor

**F1 — The direct-read `not_found` hint re-raises the allowlist ambiguity A2-D2 removed.** `twitch_client.py:802-803`: the `not_found` hint reads *"Check the id. If it names a channel, that channel must also be in TWITCH_ALLOWED_BROADCASTER_IDS before this server may ask about it."* A direct read `404` only reaches this branch **after** the guard already let the id through, so the allowlist is provably not the cause; yet the hint tells the operator to go add the channel to the allowlist. This is not a live probe oracle (the guard runs first and the id is already allowlisted), so it is not a blocker — but it re-imports the "maybe it's the allowlist" confusion A2-D2 exists to prevent and invites operators to widen the allowlist for a non-existent object. **Fix:** drop the allowlist clause from the `not_found` hint; `not_found` should point only at the id/login.

**F4 — `project_schedule` accepts a `data` object that carries `broadcaster_id` but no `segments` and returns an empty schedule with no signal** (project.py:655 only requires `broadcaster_id`). Lower risk than F2 because Helix's schedule `data` always carries `segments`, and an empty schedule can be legitimately empty; but combined with the R42 `has_more` semantics it is worth a `segments`-key presence check analogous to the shape guards above. (Listed for completeness; acceptable to leave with a documented justification.)

**F5 — `config.py:295` `# type: ignore[call-arg]`** is legitimate (pydantic-settings injects env, mypy can't see the kwargs) and carries an explanatory message; noted only so the reviewer record shows it was inspected and cleared, not overlooked.

---

## 6. Re-measurement of the R41 table (evidence for §4 area 6)

Independent run of the same code path `test_worst_case_page_stays_under_4_mib` uses:

| tool | max first | structuredContent (my measure) | README claim | consistent? |
|---|---|---|---|---|
| `get_stream_markers` | 100 | 1,294,798 B (1.23 MiB) | "1.29 MB" (largest) | ✔ (decimal MB) |
| `get_channel_chat_badges` | n/a | 862,306 B (842 KiB) | "842 KB" (next) | ✔ |
| `get_chat_chatters` | 1000 | 294,200 B | table "294,200 B" | ✔ |

Largest really is `get_stream_markers`; README table and test agree. No size-table defect.

---

## 7. Verdict

# CHANGES REQUESTED

The gate is green, the three A2-D3 shapes are correctly projected (fakes are honest, no circularity), write safety, the two kill switches, single-use confirmation binding, R34 re-check, R13's `/oauth2/token`-only exemption, the loopback/bearer/Host-Origin posture, and the round-1 M4 401-listener coverage are all genuinely implemented and tested. **B1 is the blocker:** A8's core deliverable — a mechanically verifiable requirement→test traceability table — is both wrong in 9 rows and enforced by a test that cannot catch it, and AGENTS.md §7 makes exactly this the reviewer's hard gate.

**Single most important thing to fix:** make `tests/test_gate.py` actually validate the implementation-report table — assert every `file::test` cell resolves to a real `def` in the named file — and correct the 9 mis-filed rows. After B1, the two majors **F2** and **F3** (the automod projection's silent-empty wrong-shape acceptance and its `ValidationError`→INTERNAL_ERROR leak) should be fixed in the same pass, since they are the concrete R43/R45 regressions inside the very A2-D3 fix that closed round-1's B3.
