# SPEC — Simplification (remove the per-minute budget, the TLS mode checks and the plaintext refusal; gate hangs)

**Title:** Simplification of twitch-mcp-server
**Status:** Approved (approved by: human, 2026-10-30). Gate confirmations: S-D4a cache scope confirmed; pytest-timeout `timeout_method = "signal"` confirmed; superseded text recorded by header pointers only.
**Date:** 2026-10-30
**Amends:** [`docs/SPEC.md`](../../SPEC.md) (R23 only; R19–R22 are unchanged; plus the in-place inventory lines listed in §6.1) and
[`docs/specs/tls-oauth-redirect/SPEC.md`](../tls-oauth-redirect/SPEC.md) (T4 mode clause, T5, T8, T-D7 mode clause, T-D9, T-D10).
This spec adds requirements `S1`–`S15`. It removes the behaviour of R23 and the listed T items; it does not renumber any
requirement, and it does not rewrite their text (see §12).

---

## 1. Problem / motivation

The server has grown checks that protect little and cost a lot to keep correct:

- **Per-minute tool-call budgets (R23, `budget.py`).** Twitch's own rate limits are already enforced through the
  `Ratelimit-*` header learning (R19–R22). A locally configured per-minute budget adds two env variables, a module, a
  parameter threaded through every tool, and its own tests, while only refusing calls the header limiter would park anyway.
- **TLS file-mode probing (T4 mode clause, T-D10, `_modes_are_expressible`).** The server writes and reads a probe file next
  to the private key to learn whether the filesystem holds Unix modes, and that answer only decides whether one permission
  check runs. On a Windows drive under WSL the check is skipped anyway; on Linux the operator controls the key file.
- **Repo-tree location check (T5)** and **plaintext refusal middleware (T8, T-D9).** The `.gitignore` entry for `./certs`
  protects the key location. uvicorn already refuses plaintext HTTP on a TLS listener at the handshake, so the middleware
  adds only a 400 text message and a second code path.

A review round stalled for an hour on this codebase: a reviewer built a self-made mutation-testing harness, one mutant (a
bearer check that accepted a missing header) made `tests/test_server.py::test_mcp_and_every_other_route_require_bearer` hang
forever on an unauthenticated `GET /mcp` event stream, and no test has a per-test timeout.

## 2. Goals

- Remove the code, configuration and tests that exist only for R23 and for the removed TLS checks.
- Keep the write safety model (R16, R28–R34), the tool set (R35–R37), the bearer and Host/Origin guards, the header-driven
  limiter (R19–R22) and TLS termination (T1–T4, T6, T7, T9–T12) as they are.
- Make a hanging test fail in 30 seconds instead of blocking the gate.
- Bring review back to `make check` plus reading the code against the spec.

## 3. Non-goals

- Dropping TLS (S-D1), trimming the write tools, or removing confirmation tokens or identity verification.
- Changing the header-driven limiter, the 401 circuit, or any Helix endpoint.
- Adding EventSub, a new tool, or any new network path.
- Editing the root `../AGENTS.md` (the human asked for it to stay untouched).
- Editing `CLAUDE.md` now. Its edits are a deliverable of the implementation phase (§13).

## 4. Decisions

Recorded from the human's chat of 2026-10-30 (S-D1 to S-D7). S-D4a is a consequence of S-D3 and S-D4, stated so the human
confirms it at the approval gate.

| Ref | Decision | Rationale and trade-off |
|---|---|---|
| **S-D1** | **TLS stays.** The Twitch console rejects `http://127.0.0.1` redirect URLs ("Redirect URIs must use HTTPS protocol", screenshot of 2026-10-30). Keep uvicorn TLS termination, `scripts/make-cert.sh` and `.ps1`, the read-only `./certs:/certs` mount, the `https` `redirect_uri` and `sign_in_url` derived from the TLS mode, both-or-neither configuration, and secret-free errors. Keep T1, T2, T3, T6, T7, T9–T12, and T4 without its mode clause. | The console is the constraint. Only the parts below are removed. |
| **S-D2** | **Slim TLS.** Remove **T-D10** (the `0600` probe, `_modes_are_expressible`, the probe file, the skip warning), the `0600` refusal in **T4** and **T-D7**, **T5** (repo-tree check), **T8** and **T-D9** (`PlaintextRefusalMiddleware`). | Trade-offs: (a) a plaintext request to the TLS port fails at the TLS handshake inside uvicorn. The connection closes, no request is parsed, so no bearer token travels in the clear, but the client gets no readable 400. (b) Without the mode rule, **key permissions are the operator's responsibility**; the server starts with a world-readable key and warns nothing. The README and make-cert scripts say so. (c) Without T5, the `.gitignore` entry for `./certs` is the only protection against committing a key elsewhere. No broader ignore pattern is added (not requested). Recorded as residual risk in §7. |
| **S-D3** | **All 16 write tools and their safety model stay unchanged**: R28–R34, R16 (writes never retry), R27 (uniform denial). | Writes are the product. |
| **S-D4** | **One header-driven limiter.** Keep `ratelimit.py`'s `Ratelimit-*` learning with separate app and user buckets (R19–R22). **Remove** R23, `budget.py`, the env variables `TOOL_CALLS_PER_MINUTE` and `TOOL_CALLS_PER_MINUTE_PER_TOOL`, their `config.py` fields and every parameter carrying the budget. | Twitch's limits are still enforced by bucket parking (R21). Lost: a local per-minute refusal, so a runaway caller reaches Twitch's limits sooner. The human accepted this. |
| **S-D4a** *(derived; the human confirms at the approval gate)* | **`cache.py` stays, because the writes stay.** Its three consumers after the change: (1) **identity verification** (R30, `identity.py`, `TWITCH_IDENTITY_TTL_SECONDS`), required by the kept writes; (2) **metadata** reads (`service.py`, `TTL_METADATA_SECONDS`); (3) the **R26 owner lookup** (`server.py`, key `owner:<id>`, used by the guard for object-keyed checks), which the kept object-keyed writes need. Nothing is added to the cache. | If rejected, the cache and its three uses need their own spec, because identity verification (R30) is the write path's guard. |
| **S-D5** | **Review and test process.** The reviewer runs `make check` and reads the code against this spec. No self-built mutation-testing harness and no scratch-copy mutation matrix. Add **`pytest-timeout`** to the dev extra (regenerated only by `make lock`) with a default per-test timeout of **30 seconds** in `pyproject.toml`. | Directly addresses the stall in §1. |
| **S-D6** | **Form.** This spec at `docs/specs/simplification/SPEC.md` (Draft), plus `tasks.md` and `workflow.md`. It amends `docs/SPEC.md` and `docs/specs/tls-oauth-redirect/SPEC.md` by **header pointer lines only**: no requirement text is rewritten or deleted, and the superseded statements are identified by the pointer (§12). | Follows AGENTS.md §4 and the initial-build workflow shape. |
| **S-D7** | **`CLAUDE.md` is updated to match, with the code.** Not edited now: it is authoritative for the current invariants. The exact edits are listed in §13. | Keeps the code and its governing document in step. |

## 5. Requirements

"Removed" means the symbol, file, config key or behaviour no longer exists.

- **S1** — Budget removed. `src/twitch_mcp/budget.py` is deleted. No module imports `twitch_mcp.budget`. The `ToolBudget`
  parameter, the `budget` argument of `_run`, `_register_tools`, `wired` and the boot path, and the charging in
  `TwitchService._charge` are removed. Every tool call reaches the guard directly.
- **S2** — Budget configuration removed. `Settings` has no `tool_calls_per_minute` or `tool_calls_per_minute_per_tool` field.
  `TOOL_CALLS_PER_MINUTE` and `TOOL_CALLS_PER_MINUTE_PER_TOOL` are not read. A process started with either set still boots,
  because `Settings` uses `extra="ignore"` (config.py:168). A test asserts both halves.
- **S3** — Header-driven limiter unchanged. R19–R22 behaviour is unchanged. `tests/test_ratelimit.py` changes by one edit only:
  `IO_MODULES` drops `"budget.py"` (line 29).
- **S4** — Cache scope. `cache.py` is kept. Its consumers are exactly the three in S-D4a. No caching is added, changed or
  removed for any other reason. `CACHE_MAX_ENTRIES`, `CACHE_MAX_MB` and `TTL_METADATA_SECONDS` keep their meaning.
- **S5** — Key-mode check removed. `TLS_KEY_MODE`, `MODE_PROBE_NAME`, `SYNTHETIC_MODE`, `_modes_are_expressible`, the probe file,
  the skip warning and the `tls_key_mode_rule_skipped` outcome are removed from `config.py`. A key whose mode is not `0600`
  that parses and matches its certificate is accepted. A test pins this with a `0644` key.
- **S6** — Repo-tree check removed. `_check_tls_location` and its call are removed. A cert or key path inside the repository is
  accepted at configuration time. `DEFAULT_TLS_HOST_DIR` is removed if nothing else reads it.
- **S7** — Plaintext refusal removed. `PlaintextRefusalMiddleware` is deleted from `security.py` and not added in `server.py`.
  With TLS configured, an ASGI request whose scheme is `http` to `/healthz` is answered `200` by the app (test). The app emits
  no redirect other than the R47 `302` from `GET /oauth/start` to Twitch's consent page, which carries no bearer token
  (checked by reading, §8.3).
- **S8** — Retained TLS behaviour. T1, T2, T3, T4 (missing, unreadable, non-PEM and key-does-not-match-certificate refusal,
  secret-free), T6 (`tls` flag on `/healthz`, no bearer), T7, T9, T10, T11, T12 and T-D8 behave as the TLS spec says. Their
  tests pass without modification, except where S9 names an edit.
- **S9** — Test changes and the superseded-requirement mechanism.
  (a) Delete the tests in §8.1 and add the tests in §8.2.
  (b) `tests/test_gate.py` gains `SUPERSEDED_REQUIREMENTS = {"23"}` beside `SELF_CITING`. The R57 sweep
  (`test_every_specification_requirement_is_named_by_a_test`) subtracts it from the missing set. The traceability test
  (`test_every_traceability_row_cites_a_real_test_of_its_own_file`) skips rows whose label is in that set. The R23 row in
  `docs/reports/initial-build/implementation-report.md` is historical and is not edited.
  (c) `tests/test_tls.py` gains `SUPERSEDED_TLS_REQUIREMENTS = frozenset({"5", "8"})`. The T-map test
  (`test_every_tls_requirement_is_named_by_a_test_in_this_module`) computes `required` as the declared T set minus this
  set, and expects exactly T1–T4, T6, T7, T9–T12. Any remaining citation of T5 or T8 in `test_tls.py` is an orphan and
  fails the test, so stale citations must be removed.
- **S10** — Tool set unchanged. The tool names and order `tests/test_server.py` pins for every switch combination are
  unchanged. The 16 write tools, R28–R34, R16 and R27 are unchanged. Counts are unchanged: 16 default, 25 signed in, 41 maximum
  (A1-D1, A2-D1).
- **S11** — Dev gate. `pyproject.toml` lists `pytest-timeout>=2.3` in `[project.optional-dependencies] dev`, and
  `[tool.pytest.ini_options]` sets `timeout = 30` and `timeout_method = "signal"`. The existing pyproject test in
  `tests/test_gate.py` (`test_pyproject_holds_the_strict_and_coverage_gates`) gains one assertion for these three settings;
  no new test is added. `requirements-dev.txt` is regenerated by `make lock` only. The runtime lock `requirements.txt`
  changes by no pinned runtime version.
- **S12** — Hang guard. A test that exceeds 30 seconds fails with pytest-timeout's failure instead of blocking the gate.
  `timeout_method = "signal"` is required (justified in §7). If an existing test needs more than 30 seconds, the implementer
  stops with a `SPEC-GAP:` line. The value is not raised silently and no test is marked to skip the timeout.
- **S13** — Review process. The reviewer runs `make check` and reads the code against this spec. No mutation-testing harness,
  scratch-copy matrix or self-built test driver is created by any agent. The rule is recorded in `workflow.md`, `tasks.md`
  and `CLAUDE.md` (§13). The root `../AGENTS.md` is not edited.
- **S14** — Documentation in step. `README.md`, `.env.example`, `CHANGELOG.md` (`[Unreleased]`), `scripts/make-cert.sh`,
  `docker-compose.tls.yml`, and the in-place inventory lines of `docs/SPEC.md` (§6.1) are updated or annotated as §6.1 states.
  No live document describes a probe, a budget, a key-mode refusal, a repo-tree refusal or a plaintext refusal as current, outside the lines named by the Amendment 3 pointers.
- **S15** — Gate. `make check` passes: ruff lint and format clean, `mypy --strict` clean, pytest with branch coverage at or
  above 90%.

## 6. Design

### 6.1 Files affected

| File | Change |
|---|---|
| `src/twitch_mcp/budget.py` | **Delete.** (S1) |
| `src/twitch_mcp/config.py` | Remove the `tool_calls_per_minute*` fields and the R23 comment block (≈lines 215–217). Remove `TLS_KEY_MODE`, `MODE_PROBE_NAME`, `SYNTHETIC_MODE`, `_modes_are_expressible`, the mode branch and its `tls_key_mode_rule_skipped` outcome in `_check_tls` (≈lines 394–408), `_check_tls_location` (line 123) and its call (line 390). Remove `DEFAULT_TLS_HOST_DIR` (line 47) if unused. Keep both-or-neither, path and PEM checks and the key-to-certificate match. (S2, S5, S6, S8) |
| `src/twitch_mcp/server.py` | Remove the `ToolBudget` import (line 50) and the `budget` parameter from `_run`, `_register_tools`, `wired`, the boot path and the ≈40 `budget,` call sites. Remove the `PlaintextRefusalMiddleware` import (line 95) and its `add_middleware` (line 1889). Update the R23 mentions in the module docstring (line 8) and the `_run` comment (≈723). Keep `/healthz`'s `tls` key and the R47 `302`. (S1, S7) |
| `src/twitch_mcp/service.py` | Remove the `budget` constructor parameter (338), `self._budget` (364), `_charge` (≈451), the `try_consume` call (453) and the budget references in the module docstring. **Renumber the module docstring's ordered list (lines 11+)** after item 1 (`budget (R23)`) is removed. Method docstring (331) loses the word "budget". Keep guard → identity → confirmation → client → projection. (S1) |
| `src/twitch_mcp/security.py` | Delete `PlaintextRefusalMiddleware` (lines 113–141) and remove its paragraph from the module docstring (lines 11–14). The Origin check, bearer and Host guard are unchanged. (S7) |
| `src/twitch_mcp/__main__.py` | Replace the comment at lines 58–63 that cites "mode 0600 (T4)" with a sentence that the key is not mode-checked and is the operator's to keep private. The `uvicorn.run` arguments are unchanged. (S5, S14) |
| `src/twitch_mcp/ratelimit.py`, `cache.py`, `guard.py`, `identity.py`, `twitch_client.py`, `oauth.py`, `capabilities.py`, `confirmation.py` | **No change.** The unrelated word "budget" in these files (for example `cache.py:120` byte budget, `twitch_client.py:528` 5xx budget, `ratelimit.py:72`) is not touched. |
| `tests/test_budget.py` | **Delete.** |
| `tests/test_tls.py` | See §8.1 for deletions. Other edits: remove the imports of `DEFAULT_TLS_HOST_DIR`, `TLS_KEY_MODE`, `_check_tls_location` (lines 52, 53, 55). In `_write_pair` (line 72) remove the `key_mode` parameter and the `key_path.chmod(key_mode)` call at line 108 becomes `chmod(0o600)`. Line 480 `key.chmod(TLS_KEY_MODE)` becomes `key.chmod(0o600)`. Docstrings: module docstring (lines 7–14, the sentence about the T8 seam); `_write_pair` docstring (lines 75–76, "the requirement under test (T4) *is* the mode"); fixture docstring line 191 ("owner-only key" becomes "key"); `https_client` docstring (about line 200, the `PlaintextRefusalMiddleware` reference). Test at 613 (the T2 docstring that mentions the "refusal middleware") is reworded to "T2: plain HTTP is unchanged without TLS". Module constant `EXPECTED_TLS_REQUIREMENTS` (test_tls.py:936) becomes the explicit set T1–T4, T6, T7, T9–T12; add `SUPERSEDED_TLS_REQUIREMENTS` (S9c). Also: the section headers at test_tls.py lines 523 and 557 (rename the 557 header "T6, T8" to "T6"), and the retained compose-test docstring at line 809 ("the exact failure T8 and T-D9"), which drop T5/T8 citations. Inside the retained T9 test `test_no_certificate_detail_reaches_the_log_or_the_health_response` (line 676), the plaintext `400` assertion becomes `await https.get("/healthz")` returning `200`, using `https_client(app)`. |
| `tests/test_gate.py` | Add `SUPERSEDED_REQUIREMENTS = {"23"}` (beside `SELF_CITING`, line 26) and the two subtractions in S9b. Add the S11 assertions to the existing pyproject test. |
| `tests/test_service.py` | Delete `test_budget_refusal_costs_no_request` (≈lines 309–312) only. The harness keyword arguments live in `service_fakes.py`. |
| `tests/test_server.py` | Delete `test_budget_exhaustion_is_a_retryable_tool_error_that_sent_nothing` (line 788) and the `ToolBudget` import (line 50). Pinned tool-name tests unchanged. |
| `tests/service_fakes.py` | Remove `budget_per_minute` and `budget_per_tool_per_minute` keyword arguments (lines 145–146), the `tool_calls_per_minute=`/`tool_calls_per_minute_per_tool=` settings kwargs (lines 167–168), the `ToolBudget` import and construction (lines 40, 211–216), the `budget` field and argument (lines 76, 243, 263), and the module docstring word "budget" (line 7). |
| `tests/server_fakes.py` | Remove the `budget` parameters and constructions (lines 143, 157, 214, 257, 283, 292, 300) and the `ToolBudget` import (line 50). |
| `tests/test_identity.py` | Remove the `ToolBudget` import (line 50) and its construction argument (line 717). |
| `tests/test_ratelimit.py` | `IO_MODULES` drops `"budget.py"` (line 29). Nothing else. |
| `tests/test_config.py` | Delete the `TOOL_CALLS_PER_MINUTE*` cases (lines 277–278, 394). Add the S2 test (§8.2). |
| `tests/twitch_fakes.py` | Remove `TOOL_CALLS_PER_MINUTE` and `TOOL_CALLS_PER_MINUTE_PER_TOOL` from the env list (lines 472–473). |
| `scripts/make-cert.sh` (and `make-cert.ps1` if it carries the same text) | Remove the block at lines 94–103 that stats the key and warns that the server "probes for that and skips the check". Keep `chmod 600` on generated keys (generation hygiene, not a check). Add one line that the server does not check key permissions. |
| `docker-compose.tls.yml` | Lines 11–12 ("The key must be mode 0600 on the host or config.py refuses to start (T4, T-D7).") are rewritten: the server does not check key permissions, so keep the key owner-only on the host. Line 1's decision reference is kept (T11 is retained). |
| `README.md` | Line 117: "refused with `400`" is replaced by "not answered: the TLS handshake fails, and the client is never redirected". Line 148 (`TWITCH_TLS_KEY_FILE` row): drop "Must be mode `0600` … or startup refuses (T4, T5)"; state the key is the operator's responsibility. Line 391 (troubleshooting row "must have mode 0600"): remove. Line 394 (row "`400` from a client that still uses `http://...`"): replace with the handshake-failure symptom. Lines 156–157 (`TOOL_CALLS_PER_MINUTE*` rows): remove. Line 370 ("Caches, budgets and confirmation tokens"): change to "Caches and confirmation tokens". |
| `.env.example` | Remove the "Tool-call budget" block and its two variables. Rename "Cache and budget" to "Cache". In the TLS block, state the server does not check key permissions. |
| `CHANGELOG.md` | Under `[Unreleased]`, add `### Removed` (R23 budget, `budget.py`, the two env variables, the probe, the key-mode and repo-tree refusals, the plaintext refusal middleware) and `### Changed` (T4 and T8 wording; the test timeout). |
| `pyproject.toml` | Add `pytest-timeout>=2.3` to the dev extra. Add `timeout = 30` and `timeout_method = "signal"` to `[tool.pytest.ini_options]`. (S11, S12) |
| `requirements-dev.txt` | Regenerated by `make lock` only. (S11) |
| `requirements.txt` | Regenerated by `make lock`. No pinned runtime version changes. (S11) |
| `CLAUDE.md` | Apply the edits in §13 with the code. |
| `docs/SPEC.md` | Header pointer (already added): extend it to name the superseded items (R23; the in-place inventory lines at §5.4 heading and R23 text, lines 92, 147, 298, 405 and the §8 row at 455). No rewrite (§12). |
| `docs/specs/tls-oauth-redirect/SPEC.md` | Header pointer (already added): extend it to name T4's mode clause, T5, T8, T-D7's mode clause, T-D9, T-D10 and the §6 design rows that mention them (about lines 76 and 91). No rewrite (§12). |

### 6.2 Interfaces and data flow

- `TwitchService` is constructed without a budget. Every path: guard → identity → confirmation (writes) → client → projection.
- `config.py` still reads the environment. `_check_tls` validates both-or-neither, the paths trimmed and present, readable,
  PEM-parseable, and the key matching the certificate. It does not look at the key's mode or location.
- uvicorn receives `ssl_certfile` and `ssl_keyfile` exactly as before.

## 7. Security and invariants

**Preserved unchanged:** loopback-only publication (R1/R2), bearer on every route except `/healthz` (R4), the Host/Origin guard
(R5), default-deny allowlist and uniform denial (R25–R27), the two kill switches (R28), writes never retry (R16), verified
identity (R29–R30), single-use bound confirmation tokens (R31–R34), the header-driven limiter and parking (R19–R22), the `401`
circuit (R24), the one-httpx-module rule (R11), `/healthz` secret-freedom (R3, T6, T9), the read-only `/certs` mount (T11), and
the `./certs` git-ignore.

**Deliberately removed, with the consequence:**

| Removed | Consequence |
|---|---|
| R23 per-minute budget | No local per-minute refusal. Twitch's limits still apply through header learning and parking (R19–R22). |
| T4 mode clause, T-D7 mode clause, T-D10 probe | Key permissions are the operator's job. The server starts with a world-readable key and warns nothing. `make-cert.sh` still writes the key `0600`. Residual risk: on a shared host a readable key exposes the local listener's TLS identity. The README says so. |
| T5 repo-tree check | The only protection against committing a key outside `certs/` is the `.gitignore` entry for `./certs`. Residual risk recorded. |
| T8, T-D9 plaintext refusal | A plaintext request to the TLS port fails at the handshake inside uvicorn. It is never parsed, so no bearer travels in the clear, and the app never answers it with a redirect. The only loss is a readable 400 text. |

**Why `timeout_method = "signal"`.** The reported hang is an `await` on an event stream inside the asyncio loop. The `signal`
method raises a timeout in the main thread via SIGALRM, which interrupts the loop's blocking select, so the test fails. The
`thread` method would call `os._exit`, ending the whole pytest run and its report. The gate runs in WSL/Linux, where `signal`
is available; `signal` does not exist on Windows, so the setting applies to the WSL gate only. The `signal` setting is an addition to S-D5 and is flagged for the human to accept or strike at the approval gate.

**Redirect rule.** The app's only `3xx` is the R47 `302` from `GET /oauth/start`, whose `Location` is Twitch's consent page and
which carries no bearer. The `twitch_client.py` handling of 3xx from Twitch (refusing to follow) is not an app response.

## 8. Test plan and acceptance criteria

`make check` is the gate (S15). Branch coverage stays at or above 90%. The reviewer runs `make check` and reads; no mutation
harness is used (S13).

### 8.1 Tests deleted

- `tests/test_budget.py` (whole file).
- `tests/test_service.py::test_budget_refusal_costs_no_request`.
- `tests/test_server.py::test_budget_exhaustion_is_a_retryable_tool_error_that_sent_nothing`.
- `tests/test_config.py` budget cases (lines 277–278, 394).
- `tests/test_tls.py`:
  - `test_a_key_that_is_not_owner_only_is_refused`
  - `test_an_unwritable_folder_alone_does_not_excuse_a_world_readable_key`
  - `test_a_key_mode_is_enforced_only_where_the_filesystem_can_express_it`
  - `test_the_mode_probe_reports_the_filesystem_rather_than_the_platform`
  - `test_a_skipped_key_mode_rule_says_so_without_naming_the_key`
  - `test_a_key_inside_the_repository_is_refused_except_in_the_certs_directory`
  - `test_a_path_outside_the_repository_is_always_allowed`
  - `test_a_plaintext_request_to_a_tls_server_is_refused_and_never_redirected`

### 8.2 Tests added

- **S2** (`tests/test_config.py`): with `TOOL_CALLS_PER_MINUTE` and `TOOL_CALLS_PER_MINUTE_PER_TOOL` set, `Settings` loads and
  neither attribute exists on it.
- **S5** (`tests/test_tls.py`): a `0644` key that parses and matches its certificate is accepted, with no skip warning.
- **S6** (`tests/test_tls.py`): a key and certificate inside the repository tree, outside `certs/`, are accepted.
- **S7** (`tests/test_tls.py`): with TLS configured, an `http`-scheme request to `/healthz` is answered `200`.

No test is added for the redirect rule (§8.3 A-S8). The pyproject timeout settings are asserted by one line in the existing
`tests/test_gate.py` pyproject test (S11).

### 8.3 Acceptance criteria

- **A-S1** `make check` passes (S15).
- **A-S2** `grep -rnE "ToolBudget|twitch_mcp\.budget|budget\.py|tool_calls_per_minute|TOOL_CALLS_PER_MINUTE|PlaintextRefusal|_modes_are_expressible|MODE_PROBE_NAME|SYNTHETIC_MODE|TLS_KEY_MODE|_check_tls_location" src tests`
  returns no hits, except the `SUPERSEDED_*` sets in `tests/test_gate.py` and `tests/test_tls.py` if they name identifiers.
  Unrelated uses of the word "budget" (byte budget, 5xx retry budget, read-retry tests) are not part of this check and stay.
- **A-S3** `tests/test_server.py`'s pinned tool-name and order tests pass unmodified (S10).
- **A-S4** `make lock` yields `requirements-dev.txt` with `pytest-timeout` and no changed runtime pin (S11).
- **A-S5** Every T-requirement test S8 keeps passes, with only the edits named in §6.1.
- **A-S6** No test takes longer than 30 seconds (S12). A timeout failure is a `SPEC-GAP:` item.
- **A-S7** Each document in §6.1 matches the code; no live statement describes removed behaviour as current outside the lines named by the pointers (S14).
- **A-S8** Redirect rule (S7): by reading, `src/twitch_mcp` contains no `3xx` response except the `/oauth/start` `302` in
  `server.py`, checked with a grep for `status_code=30` and `RedirectResponse`. Not a unit test.

## 9. Out of scope

Dropping TLS or changing the port; trimming or renaming the 16 write tools; changing the confirmation, identity or kill-switch
model; EventSub, a new tool or a new network path; editing `../AGENTS.md`; any change to the Helix transport; a broader `*.key`
ignore pattern (residual risk in §7, not requested); changing cache sizes or TTLs.

## 10. Open questions

None.

---

## 11. Superseded requirements

| Ref | Status | What replaces it |
|---|---|---|
| **R23** (`docs/SPEC.md` §5.4) | Removed (S1, S2); `SUPERSEDED_REQUIREMENTS` in test_gate (S9b) | Nothing local. Twitch's limits are enforced through header learning and parking (R19–R22). |
| **T4**, mode clause | Removed (S5) | Key permissions are the operator's responsibility; README and `.env.example` say so. `make-cert.sh` still writes `0600`. |
| **T5** (repo-tree check) | Removed (S6); `SUPERSEDED_TLS_REQUIREMENTS` (S9c) | The `.gitignore` entry for `./certs`. Residual risk in §7. |
| **T8** (plaintext refused with 400, never 3xx) | Removed (S7); `SUPERSEDED_TLS_REQUIREMENTS` (S9c) | uvicorn refuses plaintext at the handshake. No app redirect except R47's `302`. |
| **T-D7**, mode clause | Removed (S5) | The read-only `/certs` mount (T11) is kept. |
| **T-D9** (plaintext rejected, not redirected) | Removed (S7) | Same as T8. |
| **T-D10** (probe, `_modes_are_expressible`, skip warning) | Removed (S5) | None. The probe is gone. |

Requirements not listed keep their wording and their tests.

## 12. Amendment record

- Requirement text in `docs/SPEC.md` and the TLS spec is **not rewritten**. Each file gets a header pointer that names the
  superseded items; inventory lines in `docs/SPEC.md` (§5.4 R23 text at line 178, §2 line 93, §5.2 line 147, §6.1 line 298,
  §6.5 line 405, §8 row line 455) are covered by that pointer and are not edited; the TLS spec's §6 design rows (about lines 76 and 91) are covered by its pointer in the same way. This is the reading of S-D6 this spec adopts,
  and the human confirms it at the gate.
- The root `../AGENTS.md` is not edited. `CLAUDE.md` changes only with the code (§13).

**Amendment S-A1 (2026-10-30, approved by: human):** S9 also covers `docs/reports/initial-build/implementation-report.md`
row R43, which cited `tests/test_server.py::test_budget_exhaustion_is_a_retryable_tool_error_that_sent_nothing` (deleted
by §8.1). That single citation is removed; the row keeps its two other valid citations. Raised as a SPEC-GAP by the
implementer; accepted by the human.

## 13. Deliverable: exact `CLAUDE.md` edits (applied with the code, not now)

1. **Process paragraph (lines 5–9).** Add: "Amendment 3 (`docs/specs/simplification/SPEC.md`, S1–S15) removes R23 and parts of
   the TLS spec; `S1`–`S15` refer to it."
2. **What this is (lines 15–16).** Keep the counts. Add "(TLS optional, see Architecture)" after `127.0.0.1:8770`.
3. **Commands (after line 36).** Add: "pytest runs with a 30-second per-test timeout (`pytest-timeout`, `timeout_method = signal`,
   S11–S12). Do not raise the value silently."
4. **Commands (line 37).** Add "(this regenerates `requirements-dev.txt` with `pytest-timeout`)."
5. **Review rule (new bullet under Commands).** "Review runs `make check` and reads the code against the spec. Do not build
   mutation-testing harnesses or scratch-copy matrices (S13)."
6. **`ratelimit.py` bullet (lines 66–67).** Keep.
7. **`budget.py` bullet (line 68).** Delete.
8. **`cache.py` bullet (line 69).** "TTL/LRU cache bounded by entries and bytes, used for identity verification (R30), metadata
   reads and the R26 owner lookup only (S4)."
9. **`config.py` bullet (lines 46–49).** Add: "TLS is opt-in and both-or-neither (`TWITCH_TLS_CERT_FILE`,
   `TWITCH_TLS_KEY_FILE`). The key's permissions and location are not checked (S5–S6)."
10. **`security.py` bullet (lines 90–91).** Add: "There is no plaintext-refusal middleware (S7); uvicorn refuses plaintext at
    the TLS handshake."
11. **`server.py` bullet (lines 50–52).** Remove any reference to a budget (S1). Keep the rest.
12. **`service.py` bullet (line 59).** "guard → identity → confirmation (writes) → client → projection."
13. **Architecture.** Add: "uvicorn terminates TLS when both TLS variables are set; the certificate material is mounted read-only
    at `/certs`. The app never redirects a plaintext request (S7)."
14. **Invariants.** Keep the listed invariants (lines 95–122). Remove "budgets" from "State is process-wide" (line 150).
15. **Container (lines 155–157).** Add: "When TLS is configured, `./certs` is mounted read-only at `/certs` (T11)."
16. **New invariant, TLS.** "Both TLS variables or neither (T1); plain HTTP unchanged when neither is set (T2); `redirect_uri` and
    `sign_in_url` use `https` when TLS is on (T3); the key's mode is not checked and key permissions are the operator's
    responsibility (S5); `/healthz` reports `tls` and needs no bearer (T6)."

## 14. Trace to the decisions

S-D1 → S8, §6.1. S-D2 → S5, S6, S7, §7. S-D3 → S10. S-D4 → S1, S2, S3. S-D4a → S4. S-D5 → S11, S12. S-D6 → this file, the
pointers, §12. S-D7 → §13.
