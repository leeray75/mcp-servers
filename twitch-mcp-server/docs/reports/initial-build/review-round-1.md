# Phase 5 Review — Round 1

**Feature:** twitch-mcp-server initial build
**Spec:** `docs/SPEC.md` (Status: Approved 2026-10-29; Amendment 1 approved 2026-10-30, rulings A1-D1..A1-D7)
**Round:** 1 of 3 (A1-D4 caps this feature at 3 rounds)
**Date:** 2026-10-30
**Reviewer:** read-only reviewer subagent (distinct from the implementer; made no edits to `src/`, `tests/` or `docs/SPEC.md`)

**How this review was performed.** Every finding below was produced by reading the code from disk and by running
the checks. Nothing was taken from the implementer's report (which was not read before the findings were formed).
Commands run, from the server directory, with `/home/leeray/venvs/twitch/bin/python`:

- `python -m ruff check .`
- `python -m ruff format --check .`
- `python -m mypy` (config already sets `strict = true`, `files = ["src","tests"]`)
- `python -m pytest -q --tb=short`
- `grep -rn httpx src/`, `grep -rn '\b800\b' src/ --include=*.py`, plus targeted AST/grep scans

Per instructions: no `make` (no repo-local `.venv` here), no `pytest -m live`, and **no network call was made**.
Items that need the real Twitch API or a running container are therefore recorded as **unverified** with the reason.

---

## 1. Gate results (verbatim tails)

### 1.1 `ruff check .`

```
All checks passed!
EXIT_RUFF=0
```

### 1.2 `ruff format --check .`

```
55 files already formatted
EXIT_FMT=0
```

### 1.3 `mypy` (`strict = true` in `pyproject.toml`)

```
Success: no issues found in 47 source files
EXIT_MYPY=0
```

### 1.4 `pytest -q --tb=short` — **4 failed, 915 passed in 48.75s** (`EXIT_PYTEST=1`)

```
=================================== FAILURES ===================================
_________ test_upstream_404_is_an_isError_tool_result_with_its_status __________
tests/test_server.py:760: in test_upstream_404_is_an_isError_tool_result_with_its_status
    assert error["kind"] in {"upstream", "not_found"}
E   AssertionError: assert 'forbidden_channel' in {'not_found', 'upstream'}
_________ test_worst_case_page_stays_under_4_mib[get_automod_settings] _________
tests/test_sizes.py:366: in test_worst_case_page_stays_under_4_mib
    assert lists, f"{tool} projected no list at first=100"
E   AssertionError: get_automod_settings projected no list at first=100
E   assert []
__________ test_worst_case_page_stays_under_4_mib[get_chat_chatters] ___________
tests/test_sizes.py:357: in test_worst_case_page_stays_under_4_mib
    assert result.is_error is not True, result.content[0].text
E   AssertionError: upstream: Twitch returned a response body this server does not understand Hint: This is not an empty result: retry, and report it if it repeats.
E   assert True is not True
_____________ test_worst_case_page_stays_under_4_mib[get_schedule] _____________
tests/test_sizes.py:357: in test_worst_case_page_stays_under_4_mib
    assert result.is_error is not True, result.content[0].text
E   AssertionError: upstream: Twitch returned a response body this server does not understand Hint: This is not an empty result: retry, and report it if it repeats.
E   assert True is not True
```

Coverage tail:

```
Name                              Stmts   Miss Branch BrPart  Cover   Missing
-----------------------------------------------------------------------------
src/twitch_mcp/__init__.py            2      0      0      0   100%
src/twitch_mcp/__main__.py           21      0      2      0   100%
src/twitch_mcp/budget.py             40      0      6      0   100%
src/twitch_mcp/cache.py              81      6     14      4    89%
src/twitch_mcp/capabilities.py       82      3     32      3    95%
src/twitch_mcp/config.py            174      7     38      4    95%
src/twitch_mcp/confirmation.py       92      1     14      1    98%
src/twitch_mcp/errors.py             20      0      0      0   100%
src/twitch_mcp/guard.py              70      1     22      1    98%
src/twitch_mcp/identity.py          111      0     22      0   100%
src/twitch_mcp/logging_setup.py      90      5     32      4    93%
src/twitch_mcp/models.py            366      0      0      0   100%
src/twitch_mcp/oauth.py             459     52    106     23    86%
src/twitch_mcp/project.py           187     18     54     14    85%
src/twitch_mcp/ratelimit.py         214     16     76      9    91%
src/twitch_mcp/security.py           98      2     34      0    98%
src/twitch_mcp/server.py            470     34     50      9    91%
src/twitch_mcp/service.py           573     62    160     46    84%
src/twitch_mcp/twitch_client.py     483     53    148     29    87%
-----------------------------------------------------------------------------
TOTAL                              3633    260    810    147    90%
Required test coverage of 90% reached. Total coverage: 90.34%
=========================== short test summary info ============================
FAILED tests/test_server.py::test_upstream_404_is_an_isError_tool_result_with_its_status
FAILED tests/test_sizes.py::test_worst_case_page_stays_under_4_mib[get_automod_settings]
FAILED tests/test_sizes.py::test_worst_case_page_stays_under_4_mib[get_chat_chatters]
FAILED tests/test_sizes.py::test_worst_case_page_stays_under_4_mib[get_schedule]
4 failed, 915 passed in 48.75s
```

**Gate verdict: RED.** A1 / R56 ("`make check` green is the gate") is not met: 4 failing tests. Coverage 90.34%
clears the 90% branch floor, but two modules sit below it in isolation (`service.py` 84%, `project.py` 85%,
`oauth.py` 86%) — acceptable under an aggregate gate, worth noting because `project.py`'s uncovered lines are
precisely the three disputed projections (455-462, 630-637, 658-670).

---

## 2. R1..R57 coverage table

Verdicts: **covered** = implemented and a test would fail if it broke; **weak** = implemented but the test is
indirect, partial, or would not catch a plausible regression; **missing** = no implementation or no test.

| R | Requirement (abridged) | Implementation | Test | Verdict |
|---|---|---|---|---|
| R1 | Container `0.0.0.0:8000`; compose publishes `127.0.0.1:8770:8000` only | `__main__.py:1-73`; `Dockerfile:52` (`EXPOSE 8000`); `docker-compose.yml:31-32` | `test_deployment.py::test_compose_publishes_on_host_loopback_only`; `::test_the_data_directory_belongs_to_the_service_user`; `test_main.py` (uvicorn host/port) | covered |
| R2 | UID 10001, read-only rootfs, cap_drop ALL, no-new-privileges, mem limit, only `./data` mounted | `Dockerfile:38-49`; `docker-compose.yml:16-24`; `docker-compose.oauth.yml:11-15` | `test_deployment.py::test_the_container_runs_as_the_documented_unprivileged_uid`, `::test_compose_applies_every_hardening_switch`, `::test_compose_mounts_no_host_directory_by_default` | covered |
| R3 | `/healthz` bearer-free, exactly `{status, version, tool_count, twitch_reachable}` | `server.py:1802-1812` | `test_server.py::test_healthz_reports_exactly_four_keys_without_credentials`, `::test_healthz_still_reports_four_keys_when_the_plan_is_empty` | covered |
| R4 | Bearer everywhere else; token ≥32 chars or startup fails | `security.py:106-155`; `config.py:50-59` | `test_server.py::test_mcp_and_every_other_route_require_bearer`, `::test_post_to_healthz_requires_bearer_while_get_does_not`; `test_security.py`; `test_config.py` (min length) | covered |
| R5 | Host+Origin guard on every request incl. `/oauth/*` | `security.py:83-101`, applied in `server.py` middleware stack | `test_server.py::test_host_must_be_loopback`, `::test_origin_must_be_loopback_when_present`, `::test_oauth_routes_stay_host_guarded_while_bearer_exempt`; `test_security.py` | covered |
| R6 | `config.py` sole env reader; pydantic-settings; errors name fields not values | `config.py` (whole) | `test_config.py::test_only_config_reads_the_environment` + `::test_r6_detector_catches_a_real_environ_read` + `::test_r6_detector_fires_on_an_injected_module` (detector proven to fire) + `::test_no_module_outside_config_names_environ_in_code` | covered |
| R7 | 4 required values; missing or `replace-me*` aborts naming the field | `config.py:50-100` | `test_config.py` (required/placeholder suite); `test_main.py` | covered |
| R8 | Allowlist 1-100 ids of 1-20 digits; empty aborts | `config.py` (`twitch_allowed_broadcaster_ids` validator) | `test_config.py`; `test_guard.py::test_an_empty_allowlist_refuses_everything` | covered |
| R9 | Bases pinned to `api.twitch.tv/helix` + `id.twitch.tv` before any request | `config.py` base validators | `test_config.py` (base-URL suite) | covered |
| R10 | All numeric knobs range-validated | `config.py:108-137` (`ge=`/`le=` on every knob) | `test_config.py` (range suite) | covered (see minor M3: `twitch_max_5xx_retries` default 2 vs README "3") |
| R11 | `twitch_client.py` is the only httpx importer; **pinned by a grep test** | Verified: `grep -rn httpx src/` → only `twitch_client.py` has `import httpx` (`:43`) | **No such test exists.** `test_ratelimit.py:71-130` AST-checks ratelimit/cache/budget/guard for network names; `test_service.py:281` checks service for `os`/`httpx`; `test_config.py` checks env reads. None sweeps `src/` for `httpx` | **missing** (see B4) |
| R12 | Only the two pinned hosts; redirects never followed | `twitch_client.py:427-470` (`follow_redirects=False`), `_url` host pinning | `test_twitch_client.py` (redirect + host pinning); `conftest.py:56-77` `_no_silent_redirects` autouse guard | covered |
| R13 | Both `Client-Id` + `Bearer` or refused **before the socket** | `twitch_client.py:409-423`; `post_form:947-951` | `test_twitch_client.py::test_the_app_token_mint_goes_out_without_credential_headers`, `::test_only_the_token_endpoint_may_go_without_credentials` (asserts `fake.requests == []`) | covered (M5: no test for `bearer` present / `client_id` empty) |
| R14 | Streamed byte cap; over cap → `kind=too_large` + lower-`first` hint | `twitch_client.py` `_TooLarge` (`:1028-1042`) | `test_twitch_client.py` (byte-cap suite) | covered |
| R15 | Reads retry on 5xx/transport; 503 retried **once** | `twitch_client.py:490-540` | `test_write_safety.py::test_read_503_is_retried_exactly_once` (with `max_5xx_retries=0` to isolate) | covered |
| R16 | Writes **never** retried; unknown outcome → `kind=unknown_outcome` + check-the-channel hint; retry helper provably unreachable from write paths | `twitch_client.py:740-800`; `_unknown_outcome` | `test_write_safety.py::test_retry_helper_is_unreachable_from_every_write_path` (AST reachability over `_write, post, put, patch, delete`) + transport-failure and 5xx write tests asserting `request_count == 1` | covered |
| R17 | Method/path whitelist; validated + percent-encoded path segments | `twitch_client.py` `Route.build`, `_url`, `OAUTH_TOKEN_PATH`/`OAUTH_REVOKE_PATH` | `test_twitch_client.py` (path/segment suite); `test_server.py`/`test_service.py` path assertions | covered |
| R18 | Query lists repeat per value | `twitch_client.py` query encoding | `test_twitch_client.py` (list-repeat) + `core_fakes.query_of` | covered |
| R19 | Limits learned from the 3 headers only; no baked-in value | `ratelimit.py:63-70, 118-200` | `test_ratelimit.py` (learned-window suite) + `test_ratelimit.py:34` "800" forbidden-literal scan. Independently confirmed: `grep -rn '\b800\b' src/ --include=*.py` → **no hits** | covered |
| R20 | App and user buckets tracked separately | `ratelimit.py:76-80, 100-103` (`route_key` includes token type) | `test_ratelimit.py::test_route_key_includes_the_token_type` + separate-bucket tests | covered |
| R21 | 429 parks the bucket; over-max wait fails fast with `retry_after`, no request | `ratelimit.py:300-330` | `test_ratelimit.py` (fail-fast + `fclock.sleeps == []`) | covered |
| R22 | Shared/global 429 never replayed in-call, not counted as invalid | `ratelimit.py:113-117, 191-200`; `twitch_client._apply_429` | `test_ratelimit.py` + `test_twitch_client.py` (429 suite) | covered |
| R23 | Tool-call buckets overall + per tool; refused not queued | `budget.py`; `service._charge:449-460` | `test_budget.py`; `test_server.py::test_budget_exhaustion_is_a_retryable_tool_error_that_sent_nothing` | covered |
| R24 | First 401 opens a process-lifetime circuit; later calls return `unauthenticated` with no request | `ratelimit.py:255-273, 360-370`; `twitch_client.py:756-760` | `test_ratelimit.py::test_401_circuit_stops_all_upstream_traffic`, `::test_circuit_survives_every_attempt_and_logs_once`, `::test_circuit_opens_before_any_wait_and_never_sleeps`; `test_twitch_client.py:105-110` | covered |
| R25 | Guard before any request; refusal costs **0** requests | `guard.py:84-96` | `test_guard.py::test_non_allowlisted_broadcaster_costs_zero_requests`, `::test_malformed_or_unknown_ids_are_refused_without_a_request`; `test_server.py::test_write_on_an_unallowlisted_channel_is_refused_with_zero_requests` | covered |
| R26 | `channel_id`/`video_id` owner lookup costs exactly one cached lookup | `guard.resolve_owner:98-127` | `test_guard.py::test_owner_resolution_costs_exactly_one_lookup_across_repeats`, `::test_each_object_costs_its_own_single_lookup` | covered |
| R27 | Identical denial across unknown/403/404/5xx; auth/rate-limit/unavailable pass through | `guard.py:138-155` (`_is_outage`) | `test_guard.py::test_every_lookup_failure_gives_the_identical_denial`, `::test_denial_text_is_byte_identical_across_every_failure`, `::test_auth_rate_limit_and_unavailable_pass_through_unchanged` | covered |
| R28 | Two kill switches; both default off; default list pinned | `capabilities.py:176-215`; `config.py` defaults | `test_server.py::test_one_kill_switch_off_registers_no_writes`, `::test_default_boot_registers_exactly_the_23_read_minus_user_token_reads`; `test_capabilities.py`; `test_write_safety.py` | covered |
| R29 | Own channel from the token's `user_id`, or verified moderator/editor; else `not_authorized`, no write | `identity.py` (whole); `service._write` | `test_identity.py` (own-channel, verified/unverified roster, "broken roster denies"); `test_service.py` write cases | covered |
| R30 | Verification cached; invalidated on refresh, disconnect, **first 401** | `identity.py:207-218`; `service.py:363` + `on_identity_change:413-421`; `oauth.py:474-482` | `test_identity.py::test_identity_is_invalidated_on_token_refresh`, `::test_identity_is_invalidated_on_disconnect`, `::test_identity_is_invalidated_on_the_first_401` (asserts cache count → 0 **and** circuit open **and** exactly one request) | covered — see M4 (listener itself untested at unit level) |
| R31 | Missing `confirm` → plan + token, `confirmation_required`, **no write request**, and it is a **success** (non-`isError`) result | `service._write`; `confirmation.py` | `test_server.py::test_write_without_confirm_is_a_success_and_sends_nothing`; `test_service.py` | covered |
| R32 | Tokens single-use, TTL-bound, bound to (tool, canonical-args-hash, broadcaster_id); replay/expiry/arg-change → `stale_confirmation` + no request | `confirmation.py:97-185` | `test_confirmation.py` (mint, single-use, arg-hash mismatch, TTL expiry) | covered |
| R33 | Cleared on disconnect, refresh, circuit open, process exit; never logged/persisted/echoed | `service.on_identity_change`; `server.py:1605-1608` (`aclose`); `confirmation.py` (in-memory only) | `test_service.py::test_r33_clearing_or_identity_change_kills_outstanding_tokens` (per write tool, 0 requests), `::test_r33_clear_confirmations_reports_the_count`; `test_confirmation.py`; `test_server.py::test_no_result_ever_echoes_a_credential` | covered |
| R34 | A valid token still re-runs the guard before dispatch | `service._write` ordering | `test_service.py::test_r34_guard_runs_again_after_the_token_is_consumed` (channel removed mid-flow, 0 requests) | covered |
| R35 | Exact tool list, exact order, per switch combination | `capabilities.compute_tool_plan` | `test_server.py::test_default_boot_...`, `::test_user_oauth_on_registers_25`, `::test_each_write_group_adds_exactly_its_own_tools`, `::test_all_six_groups_plus_oauth_register_exactly_41`, `::test_nothing_outside_the_41_is_ever_registered` (also pins `names[22] == "get_schedule"`) | covered |
| R36 | Omission only for group-disabled / token-absent / explicit disable; nothing added at runtime | `capabilities.py:184-199` (`OmitReason` is a 3-value Literal) | `test_server.py::test_a_tool_is_omitted_only_for_the_three_allowed_causes`, `::test_an_unreadable_disable_flag_can_never_add_a_tool`, `::test_nothing_is_ever_added_at_runtime`; `test_capabilities.py` | covered |
| R37 | No writes beyond the 16; no prompt, resource, or passthrough | `capabilities.WRITE_TOOLS` (16 names); `server.py` registers only `@tool(...)` handlers | Independently counted: `server.py` has exactly **41** `@tool(...)` decorators (23 reads at `:796-1086`, 2 account at `:1110-1122`, 16 writes at `:1128-1435`), and `grep -n '@app.route\|prompt\|resource'` finds no MCP prompt/resource registration. Test: `test_server.py::test_registered_union_is_exactly_the_40_capability_names`, `::test_nothing_outside_the_41_is_ever_registered`; `test_capabilities.py` | covered |
| R38 | `Annotated[CallToolResult, Model]`; `exclude_unset` | `server.py` every handler (e.g. `:1064-1100`) | `test_models.py` (exclude_unset), `test_server.py::test_tool_schemas_are_closed_titles_descriptions_and_annotations_present` | covered |
| R39 | Unknown tool / unknown arg / schema violation refused before any service call, in the sibling R30 error shape | `server.py:687-760` | `test_server.py::test_unknown_tool_is_refused_before_any_service_call`, `::test_unknown_argument_...`, `::test_schema_violation_...`, `::test_missing_required_argument_...` | covered |
| R40 | `first` bounded per endpoint; `after`/`before`; mutually exclusive cursors rejected | `server.py` arg models (`First200Arg` etc.); `service` paging | `test_sizes.py::test_first_is_bounded_by_the_endpoint_maximum` (resolves `$ref` to `$defs`, plus a 1001 rejection asserting 0 requests); `test_service.py` (paging) | covered |
| R41 | Complete pages; every tool's worst case < 4 MiB; README in step | `test_sizes.py` | **3 of 23 read tools fail** (`get_automod_settings`, `get_chat_chatters`, `get_schedule`) and the README size table is still the unfilled placeholder (`README.md:190-196`). R41's second clause fails even for the passing tools | **missing** (B2, B3) |
| R42 | `pagination.cursor` of `{}` → `has_more=false` | `project.py:111-132` (`has_more`) | `test_project.py`, `test_models.py`, `test_service.py` | covered |
| R43 | All tool failures are `isError` with exactly the 7 fields | `server.py` error envelope; `errors.py` | `test_server.py::test_every_error_result_carries_exactly_the_seven_fields`, `::test_protocol_fault_is_a_jsonrpc_error_not_a_tool_result`, `::test_tool_crash_is_a_generic_jsonrpc_error_without_detail` | covered |
| R44 | `kind` from status + Twitch's numeric/error code, never message text | `twitch_client._classify` / `_oauth_error:982+` | `test_errors.py`, `test_twitch_client.py` | covered (but see B1 — 404 is not decided by the code) |
| R45 | Missing scope → `permission`, `retryable=false`, hint names scope + scopes held; never "no results" | `twitch_client._scope_phrase`, `_permission_error:772-780` | `test_errors.py`, `test_service.py`, `test_twitch_client.py` (scope suite) | covered |
| R46 | User OAuth off by default: no routes, no file, no account tools, app-token reads only | `capabilities.compute_tool_plan`; `server.py` route registration gated on `routes_live` | `test_server.py::test_oauth_routes_are_open_only_with_user_oauth_on`, `::test_user_oauth_on_registers_25`; `test_oauth_units.py`; `test_config.py` | covered |
| R47 | `/oauth/*` bearer-exempt, protected by loopback + Host/Origin + single-use `state` + `iss` check + 10-min TTL | `security.py:139-141` (GET-only exemption); `oauth.py` pending flow | `test_oauth_units.py` (state/iss/TTL), `test_server.py::test_oauth_routes_stay_host_guarded_while_bearer_exempt` | covered |
| R48 | Token store at `0600` under `/data`; sole persistent artifact; never serialized | `oauth.py` store writer | `test_oauth_units.py` (mode 0600); `test_deployment.py::test_compose_mounts_no_host_directory_by_default`; `test_server.py` | covered |
| R49 | Refresh under one lock; concurrent callers share it; failure forces re-auth, clears store, never an app-token fallback | `oauth.py:440-600` (`UserAuth`) | `test_oauth_units.py` (concurrent refresh under one lock; no-fallback) | covered |
| R50 | Boot probe validates each present token; starts degraded, does not crash | `server.py` `Wiring.boot:1561-1596`, `_probe_user:1598-1604` | `test_server.py::test_healthz_is_degraded_when_the_boot_probe_cannot_reach_twitch`; `test_oauth_units.py` | covered |
| R51 | Disconnect deletes only the server's own credential + `POST /oauth2/revoke`; nothing else | `oauth.py` `disconnect`; `twitch_client.post_form(REVOKE_FORM_PATH)` | `test_oauth_units.py`, `test_identity.py::test_identity_is_invalidated_on_disconnect`, `test_service.py` | covered |
| R52 | No token/secret/code/state/refresh/access token/URL-with-query/chat body/response body in logs, errors or `/healthz` | `logging_setup.py:60-135`; `server.py` `/healthz`; `service` log helpers | `test_logging.py` (14 tests incl. end-to-end `configure_logging`), `test_server.py::test_no_result_ever_echoes_a_credential`; `register_secret` helpers used across suites | covered |
| R53 | Lines carry route template, status, token type, duration, outcome; never arg values; write log adds broadcaster + tool + outcome | `logging_setup._EXTRA_FIELDS`; `log_request`/`log_write` | `test_logging.py::test_log_request_line_shape_has_no_argument_values`, `::test_log_write_line_records_broadcaster_and_tool_only`, `::test_log_write_never_records_the_write_content`, `::test_json_formatter_whitelists_only_the_r53_fields` | covered |
| R54 | Redacts by key name and by regex (Bearer, `client_secret`, `access_token`, `refresh_token`, `code=`), each pattern asserted | `logging_setup._PATTERNS:60-86` — contains exactly `bearer`, `client_secret`, `access_token`, `refresh_token`, `code`, plus `state` | `test_logging.py::test_each_documented_pattern_scrubs_its_own_shape` (parametrised), `::test_the_pattern_registry_is_exactly_the_required_set`, `::test_secret_key_names_are_in_the_registry` | covered |
| R55 | Autouse fixture fails any real network call; synthetic fixtures < 50 KB | `conftest.py:38-49` (patches `httpx.AsyncHTTPTransport.handle_async_request`) | `test_network_guard.py::test_direct_httpx_call_fails_the_run` + `::test_direct_call_to_the_auth_host_also_fails` (guard **proven to fire**), `::test_mock_transport_is_still_usable` (guard is not a blanket no-op). Fixture sizes confirmed: largest `tests/fixtures/streams.json` = **815 bytes** | covered |
| R56 | `make check` = ruff + mypy --strict + pytest @90% branch; `make audit`; hash-pinned requirements via `make lock` | `Makefile:29-36` (`check: lint typecheck test`), `audit`, `lock`; `pyproject.toml:42,45-50` | Gate itself is the check — and it is **RED** (4 failures). Hash pinning confirmed: 509 `--hash=` lines in `requirements.txt`, 1301 in `requirements-dev.txt` | **missing** (B1-B3 cause the red) |
| R57 | Every requirement maps to a named test | — | 55/57 requirements have concrete named tests (see table). **R11 has no grep test**; R56 has no test beyond the gate. Also: `docs/reports/initial-build/implementation-report.md` (acceptance A8) is **absent** — the directory holds only `summary-report.md` | **weak** (see B4, M6) |

**Coverage summary:** 50 covered, 0 weak at requirement level beyond those noted, **4 missing/blocked** (R11, R41,
R56, R57-in-part). No test in the suite was found that trivially passes regardless of behaviour; the guard tests
(`test_network_guard.py`, `test_config.py::test_r6_detector_*`, `test_write_safety.py::test_retry_helper_is_unreachable_*`)
all include a "detector fires" proof, which is the right pattern.

---

## 3. Issues

### Blockers

**B1 — Every Helix 404 on a read is reported as `forbidden_channel`; the suite is red. (R43/R44) — ALREADY ESCALATED.**
Evidence: `src/twitch_mcp/twitch_client.py:792-800` maps `status == 404` (non-write) to
`TwitchMcpError("forbidden_channel", "Twitch has no such object for this server", …)`. The failing assertion is
`tests/test_server.py:760`. I independently confirm the defect and add an observation the escalation did not record:
this is also a **security** regression, not only a `kind` mismatch. `guard._is_outage` (`guard.py:138-155`) exists
specifically to stop a non-allowlist answer being laundered into a denial; here the reverse happens — a genuine
"object does not exist" from Twitch is laundered **into** an allowlist denial, telling the caller to
"add its owning broadcaster to the broadcaster allowlist" for an object that simply does not exist. It is also
decided by status alone with no consultation of Twitch's `status`/`error` code, which is what R44 requires.
Verified by reading the classifier and by running the suite. The SPEC pins neither `upstream/not_found` nor
`forbidden_channel` for a bare read 404, so **per A1-D2 the implementation is frozen pending a human ruling**;
this review does not propose which side to change.

**B2 — `get_chat_chatters` and `get_schedule` cannot work against the real API; both size tests fail. (R41) — ALREADY ESCALATED.**
Evidence: `project.py:618-646` (`project_chatters`) and `project.py:648-670` (`project_schedule`) both raise
`kind=upstream` "Twitch returned a response body this server does not understand" unless the body has a very
specific non-Helix shape. Test failures at `tests/test_sizes.py:357`.
**Blast radius (independently assessed).** These are the *only* consumers: `grep -rn 'project_chatters|project_schedule|ChattersResult|ScheduleResult'`
returns exactly `project.py`, the `models.py` declarations, one `Annotated[CallToolResult, …]` annotation each
(`server.py:1079`, `server.py:1094`) and one call site each in `service.py` (`:1019`, `:1048`). Nothing else in
`src/` depends on those shapes, so the blast radius is **exactly two tools — `get_chat_chatters` and
`get_schedule` — both of which can never return data**. `get_schedule` is an app-token read, so it is in the
*default* 16-tool surface that ships with all switches off. Confirmed there is no hidden coupling.
The precise disagreement: `project_chatters` requires `body["data"]` to be a **Mapping** (`:622-623`); Helix
returns `"data": [ … ]` (a list) with `total` at top level. `project_schedule` requires a bare body with
`broadcaster_id` at top level (`:651-652`); Helix wraps it as `{"segments": [...], "pagination": {...}}` and the
broadcaster fields are **inside each segment**. `models.py:324-328` (`Schedule` with `broadcaster_id/name/login`
siblings of `segments`) matches the first, wrong, reading. SPEC is silent on all three response shapes →
**implementation frozen pending human ruling**.

**B3 — `get_automod_settings` cannot work against the real API; its size test fails. (R41) — ALREADY ESCALATED.**
Evidence: `project.py:588-615` reads `records[0]["settings"]` as a list of `{level:int, title:str}`
(`models.py:293-303`). Helix returns per-category integer `*__level` fields directly on the record (`denied_terms_level`,
`keepable_possibly_offensive_level`, …) plus `broadcaster_id`/`moderator_id` — there is no nested `settings` array.
With a real response, `inner` is not a list, `settings` stays `None`, and the tool returns
`AutomodSettingsResult(settings=None)`: **a silently empty result, not an error**. That is the more dangerous of the
three failure modes, because R45's promise ("an empty list always means genuinely empty") is broken *without any
signal to the caller*. Test failure at `tests/test_sizes.py:366`. SPEC silent → frozen pending ruling.

**B4 — R11's required grep test does not exist.**
R11: "`twitch_client.py` is the only module that imports `httpx`. **A test greps every other source file for the
token `httpx` and fails on a hit.**" No such test exists anywhere under `tests/`. The closest are
`test_ratelimit.py:71-130` (AST scan of *four* modules for network-call names, not an `httpx` scan of `src/`) and
`test_service.py:281` (the service alone). SPEC §7 and CLAUDE.md both call this invariant "pinned by a grep test",
and acceptance A5 states the grep must be empty. The invariant **currently holds** — I verified
`grep -rn httpx src/` returns `import httpx` only at `twitch_client.py:43`; the other hits are docstrings and
`logging_setup.py:205-206`, which silences the third-party `httpx`/`httpcore` loggers and is not an import — but
nothing fails if someone adds `import httpx` to `service.py` tomorrow. This is a spec-mandated test that was never
written; **A5 is unverifiable as a gate**. Adding the test is a test-only change and does not touch frozen code.

### Majors

**M1 — README still says 15 write tools / "Maximum 40 tools"; contradicts the code, its own table, and SPEC §6.4 as amended by A1-D1.**
Evidence: `README.md:142` (`### Writes (15) — six groups…`) and `README.md:156` (`Maximum 40 tools (all groups +
user OAuth on)`). Yet `README.md:146-154`, immediately above line 156, lists **16** write tools
(1+2+2+1+6+4), and the "Reads (23)" + "Account (2)" sections make 41. `capabilities.py:103` says
`MAX_TOOL_COUNT: Final = 41  # 23 reads + 2 account + 16 writes`; `test_server.py::test_all_six_groups_plus_oauth_register_exactly_41`
enforces 41. SPEC A1-D1 explicitly ordered this prose corrected. Verified by counting the README table rows and by
running the test. Reviewer-mandated check: "README figures (tool counts…) must match the code and SPEC" — **fails**.

**M2 — CHANGELOG `[Unreleased]` says "Fifteen write tools"; same A1-D1 contradiction.**
Evidence: `CHANGELOG.md:26` `- Fifteen write tools across six groups …`. The tool names listed in that same entry
number 16. Same root cause as M1, same required fix.

**M3 — CLAUDE.md contradicts the amended SPEC on the maximum tool count.**
Evidence: `CLAUDE.md:14` "and **15 write tools** across six groups" and `CLAUDE.md:97`
"Default plan is 23 reads; **maximum is 40**." CLAUDE.md is this server's authoritative invariant file
(AGENTS.md §1) and an invariant stated there that contradicts the code will mislead the next implementer.
Also in this class, lower-value: `.env.example:33` documents `TWITCH_TIMEOUT_SECONDS=10` (code default 10.0 — fine)
but `TWITCH_MAX_5XX_RETRIES` is `3` in `.env.example`/README and `2` in `config.py:112` — R10 says "All numeric
knobs are range-validated at startup **with defaults stated in `.env.example`**", so the example file is the
documented default and the code disagrees with it.

**M4 — The 401→cache-invalidation listener has no direct test of its own.**
The wiring is real and I traced it end to end: `service.py:363`
`limiter.set_auth_failure_listener(self.on_identity_change)` in `TwitchService.__init__` (so no composition path can
omit it); `ratelimit.trip_auth:255-273` fires it **inside** `if not self._auth_failed:` so it cannot fire twice,
cannot recurse (`_auth_failed` is set to `True` before the call, and nothing in `on_identity_change` →
`identity.invalidate`/`confirmations.clear` → `trip_auth` closes a loop), and the `except Exception` around the call
logs and swallows so a throwing hook cannot prevent the circuit from opening nor alter the `TwitchMcpError` the
caller receives — **the 401 cannot be masked**. `tests/test_identity.py:516-551` proves the end-to-end behaviour
(circuit open + identity cache count → 0 + exactly one request). But there is **no test anywhere** for
`set_auth_failure_listener` itself: `grep -rn set_auth_failure_listener tests/` → **zero hits**, and the
listener-raising branch (`ratelimit.py:270-273`) is uncovered. So "fires exactly once per process, cannot recurse,
cannot mask the 401" is verified by reading and by the integration test, but three of those three properties have no
unit test pinning them. `test_ratelimit.py::test_circuit_survives_every_attempt_and_logs_once` proves log-once for
the circuit but not listener-once.

**M5 — README's R41 size table is still an unfilled placeholder, which R41 explicitly requires to be in step.**
Evidence: `README.md:190-196` — an HTML comment `SIZE-TABLE-PLACEHOLDER … FILL FROM tests/test_sizes.py OUTPUT
(do not hand-estimate)` followed by "**Sizes are not yet measured.** Run `PYTHONPATH=src pytest tests/test_sizes.py -q`
… after the implementation lands". The implementation has landed, so this is a live R41 gap, not a staged one.
It cannot honestly be filled yet anyway, because three tools' size tests fail (B2/B3) — the correct fix order is
B2/B3 first, then measure, then fill.

**M6 — `tools/try_live.py` tells the operator to run a script that does not exist.**
Evidence: `tools/try_live.py:161` `needs a connected account: run scripts/auth first`. `scripts/auth.sh` was
deliberately **removed** from the spec by ruling **A1-D6** ("`scripts/auth.{sh,ps1}` are removed from the spec — no
requirement ever defined their behaviour, so they are not built"), and `ls scripts/` confirms there is no `auth.*`.
CLAUDE.md's command section also references it for the live flow. `src/twitch_mcp/errors.py:58` has the same stale
reference in the `permission` hint ("Re-run scripts/auth.sh to grant it") — that one is pre-existing implementation
text rather than a recent change, but it is the same defect. Minor in isolation; grouped here with M1-M3 as the
"docs point at a world that no longer exists" cluster.

### Minors

**m1 — Dead duplicate fixtures.** `tests/fixtures/streams.json.json` and `tests/fixtures/users.json.json` are
byte-identical to their non-doubled twins (both 815 B and both 892 B respectively, per `ls -la tests/fixtures/`).
They are load-bearing for no test. Harmless (still under the R55 50 KB cap) but they are clutter in a security-
reviewed repo and suggest a botched rename.

**m2 — `capabilities.DEFAULT_REGISTERED` contradicts R28's stated default.**
`capabilities.py:245-248` defines `DEFAULT_REGISTERED` as "the 23 reads minus the seven that need a user token,
i.e. the sixteen app-token reads", while SPEC §6.4 says "default 23 (reads only, user OAuth off, writes off)" and
CLAUDE.md repeats "Default plan is 23 reads". `test_server.py::test_default_boot_registers_exactly_the_23_read_minus_user_token_reads`
implements the 16-tool reading, so `compute_tool_plan` — the value actually used by `server.py`, `/healthz` and the
pinned tests — omits user-token tools when no user token is present, which follows R36(b) ("a required token type
being absent"). The constant and the spec prose disagree. Behaviour is defensible; the documentation is not, and a
reader of CLAUDE.md §"Tool set" would be wrong.

**m3 — Stale count literals in two test names.** `test_server.py:221` `test_registered_union_is_exactly_the_40_capability_names`
while the neighbouring test asserts 41 (`:206`), and `capabilities.py:100` comment "at most 40". Cosmetic, but R35
makes the tool count the single most load-bearing number in the repo and these names will mislead the next reviewer.

**m4 — `tools/try_live.py` has no test, and `TWITCH_LIVE_OK` gating is therefore unverified by the suite.**
Read verification passed: `main()` at `try_live.py:183-186` returns 2 unless `os.environ["TWITCH_LIVE_OK"] == "yes"`,
there is no argument that bypasses it (only `--writes`, and unknown args are rejected at `:179-182`), and pytest can
never collect it (`pyproject.toml:39` `testpaths = ["tests"]`, plus `--strict-markers -m 'not live'`). But "no test
opens a real socket" for this file rests on inspection alone. A2/A3 are **UNVERIFIED by ruling A1-D5** and I could
not verify them either, since I made no network call.

**m5 — Missing test for the `client_id`-empty half of R13.**
`_headers` (`twitch_client.py:411-416`) has two independent refusals; only the bearer-empty branch is exercised by
`test_twitch_client.py::test_only_the_token_endpoint_may_go_without_credentials` (which calls
`post_form("/oauth2/revoke", …)` with **both** omitted and so hits the `client_id` branch — good) but no Helix-verb
test omits only `client_id`. Low risk, one-line test.

**m6 — `docs/reports/initial-build/implementation-report.md` is missing (acceptance A8).**
`ls docs/reports/initial-build/` → only `summary-report.md`. A8 requires a requirement→implementation→test
traceability table with a row for every R1-R57 and no deviations. R57's "maps to at least one named test in the
implementation report" is therefore not satisfiable as written.

---

## 4. Targeted assessments requested by the orchestrator

**4.1 The 401 circuit → cache-invalidation listener (`ratelimit.py` + `service.py`).**
Verified by reading `ratelimit.py:243-273` and `service.py:349-421`, and by running
`test_identity.py::test_identity_is_invalidated_on_the_first_401` (passes).
- **Fires exactly once per process:** `trip_auth` body is wholly inside `if not self._auth_failed:` and
  `self._auth_failed = True` is set **before** the listener is invoked (`ratelimit.py:257-272`). Any re-entrant
  `trip_auth` is a no-op. `set_auth_failure_listener` stores a single optional callable, and the only caller is
  `TwitchService.__init__`, so there is exactly one listener per limiter.
- **Cannot recurse:** `on_identity_change` (`service.py:413-421`) calls `identity.invalidate()` and
  `confirmations.clear()`. Neither reaches `trip_auth` — `grep -rn trip_auth src/` yields exactly two hits
  (`ratelimit.py:255` the definition, `twitch_client.py:760` the sole call site), so no cycle exists.
- **Cannot mask the 401:** the listener call is wrapped in `try/except Exception` that logs
  (`logger.exception`) and continues (`ratelimit.py:269-273`); `trip_auth` returns `None` and its caller in
  `twitch_client.py:760` proceeds to raise the normal 401 classification. A broken hook therefore cannot downgrade,
  delay or swallow the 401. Additionally `acquire()` raises `unauthenticated` before any bucket work once
  `_auth_failed` is set (`ratelimit.py:365-370`), which the circuit test confirms makes no request and never sleeps.
- **Gap:** no unit test exercises `set_auth_failure_listener` or the listener-raising branch (M4).

**4.2 `post_form`'s bearer-less exception (`twitch_client.py:925-980`).**
Verified sound. The exception is
`{} if path == OAUTH_TOKEN_PATH and client_id is None and bearer is None else self._headers(client_id, bearer)`
(`:947-951`) — keyed on the **exact literal path**, not on "the caller passed nothing". Defence in depth:
`if path not in {OAUTH_TOKEN_PATH, OAUTH_REVOKE_PATH}: raise` (`:939-940`) means `post_form` can only ever address
two fixed paths on `TWITCH_AUTH_BASE`, and `/oauth2/revoke` always goes through `_headers`. No Helix verb can borrow
the exception: `get/post/put/patch/delete` each call `_headers(client_id, bearer)` unconditionally. R13 therefore
still refuses anything else without both credential headers, before the socket — `test_twitch_client.py:318-328`
asserts `kind == "unauthenticated"` **and** `fake.requests == []`.

**4.3 `tools/try_live.py`.**
Refuses without `TWITCH_LIVE_OK=yes` (`try_live.py:183-186`, exit 2); no flag bypasses it. Never collected by pytest:
`testpaths = ["tests"]` and the file lives in `tools/`, and `-m 'not live' --strict-markers` (`pyproject.toml:41-42`)
excludes any `@pytest.mark.live` item — and no test in `tests/` carries that marker at all, so nothing marked can
silently run under `make check`. Stale `scripts/auth` reference: **M6**. Live behaviour A2/A3: **UNVERIFIED** per
A1-D5 (no credentials, and this review made no network call).

**4.4 No test opens a real socket; the guard actually fires.**
`tests/conftest.py:38-49` is autouse and patches `httpx.AsyncHTTPTransport.handle_async_request` to raise
`AssertionError("test attempted a real network request: …")`, which catches any client including one built inside a
module under test. It is proven to fire by `tests/test_network_guard.py::test_direct_httpx_call_fails_the_run` and
`::test_direct_call_to_the_auth_host_also_fails` (both against real `api.twitch.tv` / `id.twitch.tv` URLs), and proven
not to be a blanket no-op by `::test_mock_transport_is_still_usable`. A second autouse guard
(`conftest.py:56-77`) makes any `follow_redirects=True` path fail. I additionally scanned for bypasses: no test
uses `socket`, `urllib.request`, `aiohttp`, `subprocess` or `requests` (`grep -rn` over `tests/` → no hits; the only
`subprocess` mention is `test_ratelimit.py:95`, which asserts its *absence* from `ratelimit.py`). The 915 passing
tests completed in 48.75 s, consistent with a fully offline run. **Assessment: the network block is real, autouse,
and demonstrated to fire.**

---

## 5. Nothing outside the spec

Verified clean. `server.py` registers exactly 41 `@tool(...)` handlers, whose names I counted against SPEC §6.4 and
which match `capabilities.READ_TOOLS` (23) + `ACCOUNT_TOOLS` (2) + `WRITE_TOOLS` (16) exactly, with the §6.4 order
enforced by `test_server.py` (including the `names[22] == "get_schedule"` position pin). No MCP **prompt** and no
MCP **resource** is registered anywhere (`grep` over `src/` for the SDK registration decorators returns nothing);
no passthrough/"call any endpoint" tool exists; no extra HTTP route beyond `/healthz` and the two `/oauth/*` paths
(`server.py` route setup, `security.py:25-28`). `pyproject.toml` dependencies are exactly the six SPEC D5 names
(mcp, httpx, pydantic, pydantic-settings, starlette, uvicorn) plus the dev extras — no new dependency. The only
scripts are the eight spec'd operations scripts with their `.ps1` twins plus `scripts/lib/`; `scripts/auth.*` is
correctly **absent** per A1-D6.

One caveat that is *not* a spec violation: `tools/try_live.py:9` and `src/twitch_mcp/errors.py:58` tell the operator
to run `scripts/auth.sh`, which does not exist (M6).

---

## 6. Security invariants (server CLAUDE.md) — status

| Invariant | Status | Evidence |
|---|---|---|
| Loopback-only bind + loopback-only docker publish | PASS | `docker-compose.yml:31-32` `127.0.0.1:${HOST_PORT:-8770}:8000`; `test_deployment.py` regex-asserts the `127.0.0.1:` prefix and rejects `0.0.0.0`/bare mapping |
| Host/Origin guard | PASS | `security.py:83-101`; `test_server.py::test_host_must_be_loopback`, `::test_origin_must_be_loopback_when_present`, `::test_oauth_routes_stay_host_guarded_while_bearer_exempt` |
| Bearer everywhere except `/healthz` (+`/oauth/*` GET when OAuth on) | PASS | `security.py:106-155` (GET-only exemption, constant-time compare always executed); `test_server.py::test_mcp_and_every_other_route_require_bearer` |
| Default-deny allowlist, zero traffic on refusal | PASS | `guard.py:84-96` pure set test; `test_guard.py::test_non_allowlisted_broadcaster_costs_zero_requests`, `test_server.py::test_write_on_an_unallowlisted_channel_is_refused_with_zero_requests` |
| `TWITCH_ENABLE_WRITES` + `TWITCH_WRITE_GROUPS` kill switches | PASS | `capabilities.compute_tool_plan`; `test_server.py::test_one_kill_switch_off_registers_no_writes` |
| Single-use confirmation bound to (tool, canonical args hash, broadcaster_id) | PASS | `confirmation.py:97-185`; `test_confirmation.py` |
| Writes never retried; interrupted write → `unknown_outcome` | PASS | `twitch_client.py:740-760`; AST reachability test + `request_count == 1` assertions |
| Secrets never logged / surfaced | PASS | `logging_setup.py:60-135`; `test_logging.py` (14 tests); `test_server.py::test_no_result_ever_echoes_a_credential` |
| `httpx` only in `twitch_client.py` | PASS **by inspection**, FAIL **as a gate** | `grep -rn httpx src/` → import only at `twitch_client.py:43`; **but the R11-mandated grep test does not exist** (B4) |
| Headers-only rate limits, no baked-in limit | PASS | `grep -rn '\b800\b' src/ --include=*.py` → no hits; `test_ratelimit.py` forbidden-literal scan |
| Redirects refused, byte cap, path/query validation | PASS | `twitch_client.py:427-470, 749-757`; `conftest.py:56-77` |
| Process-wide (never session-keyed) state | PASS | limiter/cache/budget/confirmations are single instances built once in `server.py`'s wiring; `test_server.py::test_nothing_is_ever_added_at_runtime` |
| Network-free tests | PASS | `conftest.py:38-49` + `test_network_guard.py` |

No new security hole was found beyond B1's allowlist-denial laundering (which is part of an already-escalated item)
and B3's silent-empty result, which violates the "empty means empty" promise (R45) without any signal.

---

## 7. Verdict

The gate is red (4 failing tests), one spec-mandated test (R11's grep test) does not exist, R41's README half is
unfilled, and the README/CHANGELOG/CLAUDE.md tool counts still contradict the approved amendment A1-D1. The four
known escalated defects (a–d) are **independently confirmed** as real and are reported as blockers; no fix is
proposed for them here because they are spec-silent and the implementation is frozen pending human authorization
(A1-D2). B4, M1, M2, M3 and M6 are outside that freeze: they are test-only or documentation-only changes that the
approved spec already mandates.

Blockers: **4** (B1, B2, B3, B4) — of which B1/B2/B3 are the already-escalated, frozen items and B4 is actionable now.
Majors: **6** (M1..M6). Minors: **6** (m1..m6).

Review round 1 of 3 used (A1-D4). Per AGENTS.md §7, the implementer should fix all blockers and majors that are not
frozen (B4, M1, M2, M3, M6, and M5 once B2/B3 are ruled on), and fix or explicitly accept each minor in writing.

VERDICT: CHANGES REQUESTED
