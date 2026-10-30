# Implementation Report — twitch-mcp-server (initial build)

**Date:** 2026-10-30
**Spec:** [`docs/SPEC.md`](../../SPEC.md) — Status: Approved (2026-10-29 19:28 -04:00), with
[Amendment 1](../../SPEC.md) (2026-10-30 00:55) and [Amendment 2](../../SPEC.md) (2026-10-30 08:41), both approved by: human.
**Process:** [`AGENTS.md`](../../../../AGENTS.md) §7 (implementer), §8 (summary).
**Implementer:** orchestrating agent (Phase 4), after one delegated implementer subagent for the pre-authorized
mechanical test-harness batch (A1-D3). No second implementer subagent was used.
**Gate at time of writing:** ruff clean · ruff format clean · mypy strict clean over 49 files · **949 passed**, coverage
**90.80%** (gate 90% branch).

**Phase 5 status:** round 1 CHANGES REQUESTED → round 2 CHANGES REQUESTED → **round 3 APPROVED** (3 of 5 rounds used).
Fixes in [`fix-round-2.md`](fix-round-2.md); verification in [`review-round-3.md`](review-round-3.md).

---

## 1. What was built

A Streamable-HTTP MCP server for the Twitch Helix API, modelled on the sibling servers in this repository.

| Module | Responsibility | Requirements |
|---|---|---|
| `config.py` | The only reader of the environment; range validation, startup refusal, no value echo | R6–R10 |
| `twitch_client.py` | The only `httpx` module: routes, headers, streaming byte cap, retries, rate-limit learning | R11–R22 |
| `ratelimit.py` | Header-derived buckets per token type, 429 parking, the 401 circuit and its auth listener | R19–R24 |
| `budget.py` | Per-minute tool-call buckets; excess refused, not queued | R23 |
| `guard.py` | Default-deny broadcaster allowlist; uniform denial so membership cannot be probed | R25–R27 |
| `identity.py` | Verified-identity lookups (own channel / moderator / editor) with TTL cache and invalidation | R29–R30 |
| `confirmations.py` | Single-use write-confirmation tokens bound to (tool, canonical args, broadcaster) | R31–R34 |
| `capabilities.py` | The tool plan: names, order, groups, token type, `DEFAULT_REGISTERED`, `MAX_TOOL_COUNT` | R35–R37 |
| `models.py` · `project.py` | Pydantic result models and the projections that produce them from Helix bodies | R38, R40–R42 |
| `service.py` | Tool implementations, write safety, pagination, envelope accessors | R25–R42 |
| `server.py` | ASGI app, bearer/Host/Origin middleware, `/healthz`, OAuth routes, tool dispatch, `isError` mapping | R1–R5, R43–R45, R46–R51 |
| `oauth.py` | Authorization-code flow (no PKCE), single-use `state`, atomic 0600 token store, single-flight refresh | R46–R51 |
| `logging_setup.py` | Key-name and regex redaction; third-party loggers silenced | R52–R54 |
| `errors.py` | The `kind` taxonomy and its hints | R43–R45 |
| `deployment` (`Dockerfile`, `docker-compose*.yml`) | Loopback publish, UID 10001, read-only rootfs, dropped capabilities | R1–R2 |
| `scripts/`, `tools/try_live.py` | Operator scripts with PowerShell twins; opt-in live smoke | A1-D5 |

Tool surface: **16** app-token reads by default, **25** with a signed-in account, **41** maximum (23 reads + 2 account
tools + 16 writes), per A1-D1 and A2-D1.

---

## 2. Requirement traceability (R1–R57)

One row per requirement, as A8 demands. Each cell lists named tests, taken from the tests that cite the requirement
in their own docstrings; `tests/test_gate.py` re-derives this map from the repository and fails the run if any
requirement loses its citation or a test invents a requirement the spec does not define.

| Req | Named tests |
|---|---|
| R1 | test_deployment.py::test_compose_publishes_on_host_loopback_only; test_gate.py::test_the_report_has_a_row_for_every_requirement; test_main.py::test_the_app_is_handed_to_uvicorn_on_the_container_bind |
| R2 | test_deployment.py::test_compose_applies_every_hardening_switch; test_deployment.py::test_the_container_runs_as_the_documented_unprivileged_uid |
| R3 | test_deployment.py::test_the_image_starts_the_same_entry_point_the_tests_cover; test_server.py::test_healthz_is_degraded_when_the_boot_probe_cannot_reach_twitch; test_server.py::test_healthz_reports_exactly_five_keys_without_credentials |
| R4 | test_security.py::test_a_valid_token_reaches_the_application; test_security.py::test_every_route_but_health_requires_the_bearer_token; test_security.py::test_health_is_reachable_without_a_token_for_get_and_head |
| R5 | test_deployment.py::test_compose_publishes_on_host_loopback_only; test_main.py::test_the_app_is_handed_to_uvicorn_on_the_container_bind; test_security.py::test_a_foreign_origin_is_rejected |
| R6 | test_config.py::test_allowlist_value_is_never_echoed_even_when_it_looks_harmless; test_config.py::test_config_imports_pydantic_settings; test_config.py::test_invalid_values_are_rejected_without_echoing_them |
| R7 | test_config.py::test_replace_me_placeholders_abort_startup; test_main.py::test_a_bad_configuration_names_the_field_and_never_the_value; test_main.py::test_the_environment_is_read_only_through_config |
| R8 | test_config.py::test_empty_allowlist_aborts_startup; test_guard.py::test_an_empty_allowlist_refuses_everything |
| R9 | test_config.py::test_allowlist_accepts_one_to_twenty_digits; test_config.py::test_bases_default_to_exactly_the_twitch_hosts |
| R10 | test_config.py::test_authority_case_is_normalised_and_query_free; test_config.py::test_invalid_values_are_rejected_without_echoing_them; test_config.py::test_numeric_boundaries_are_inclusive |
| R11 | test_guard.py::test_guard_construction_never_touches_the_network; test_network_guard.py::test_twitch_client_is_the_only_module_that_imports_httpx; test_ratelimit.py::test_module_has_no_network_or_io_import |
| R12 | test_config.py::test_redirect_guard_blocks_a_hopping_client |
| R13 | test_twitch_client.py::test_a_helix_call_missing_only_the_client_id_is_refused_before_the_socket; test_twitch_client.py::test_client_stores_no_credential; test_twitch_client.py::test_only_the_token_endpoint_may_go_without_credentials |
| R14 | test_twitch_client.py::test_oversized_response_is_refused_without_leaking_body |
| R15 | test_twitch_client.py::test_a_5xx_is_retried_up_to_the_documented_bound; test_twitch_client.py::test_a_read_recovers_when_a_retry_succeeds |
| R16 | test_errors.py::test_unknown_outcome_hint_forbids_a_re_issue; test_twitch_client.py::test_a_read_that_cannot_reach_twitch_is_a_retryable_upstream; test_twitch_client.py::test_a_write_answered_with_an_unreadable_body_is_undetermined |
| R17 | test_guard.py::test_malformed_or_unknown_ids_are_refused_without_a_request; test_server.py::test_schema_violation_is_refused_before_any_service_call; test_service.py::test_r17_malformed_cursor_never_reaches_a_url |
| R18 | test_twitch_client.py::test_a_read_that_cannot_reach_twitch_is_a_retryable_upstream |
| R19 | test_ratelimit.py::test_capacity_is_learned_not_assumed; test_ratelimit.py::test_no_endpoint_point_cost_table_exists; test_ratelimit.py::test_no_limit_value_appears_anywhere_in_ratelimit_source |
| R20 | test_ratelimit.py::test_app_and_user_buckets_are_independent; test_ratelimit.py::test_route_key_includes_the_token_type |
| R21 | test_ratelimit.py::test_429_parks_only_the_bucket_that_was_told_to_block; test_ratelimit.py::test_remaining_zero_parks_until_the_documented_reset; test_ratelimit.py::test_wait_longer_than_max_fails_fast_without_a_request |
| R22 | test_ratelimit.py::test_learn_after_a_429_cannot_undo_the_replay_block; test_ratelimit.py::test_learning_from_a_response_clears_spent_after_the_window; test_ratelimit.py::test_spent_lease_marks_the_route_so_it_is_not_replayed |
| R23 | test_budget.py::test_a_refusal_by_the_overall_bucket_charges_no_tool_bucket; test_budget.py::test_a_refusal_charges_neither_bucket; test_budget.py::test_a_tool_bucket_never_exceeds_its_capacity |
| R24 | test_errors.py::test_unauthenticated_is_never_retryable_and_carries_the_401; test_ratelimit.py::test_circuit_survives_every_attempt_and_logs_once; test_ratelimit.py::test_route_key_includes_the_token_type |
| R25 | test_guard.py::test_allowlisted_broadcaster_is_accepted_without_a_request; test_guard.py::test_an_empty_allowlist_refuses_everything; test_guard.py::test_denial_never_echoes_the_refused_id |
| R26 | test_guard.py::test_each_object_costs_its_own_single_lookup; test_guard.py::test_integer_owner_ids_are_normalised; test_guard.py::test_malformed_object_id_costs_no_lookup |
| R27 | test_errors.py::test_pass_through_kinds_are_the_r27_set; test_guard.py::test_a_5xx_that_reveals_a_non_allowlisted_owner_is_still_a_uniform_denial; test_guard.py::test_a_denial_reports_no_upstream_detail |
| R28 | test_capabilities.py::test_default_boot_registers_only_app_reads; test_config.py::test_valid_environment_loads_with_documented_defaults; test_config.py::test_write_groups_and_writes_enabled_need_both_switches |
| R29 | test_identity.py::test_a_malformed_roster_body_refuses_the_write; test_identity.py::test_argument_cannot_make_a_foreign_channel_the_operators; test_identity.py::test_channel_group_authorizes_through_the_editors_roster |
| R30 | test_identity.py::test_a_failed_lookup_never_becomes_an_authorization; test_identity.py::test_an_auth_or_rate_limit_failure_surfaces_unchanged; test_identity.py::test_each_channel_and_roster_has_its_own_entry |
| R31 | test_errors.py::test_confirmation_required_hint_describes_the_second_turn; test_models.py::test_write_result_default_is_a_confirmation_request; test_server.py::test_a_genuine_write_failure_is_an_isError_result |
| R32 | test_confirmation.py::test_args_hash_is_key_order_independent_and_sensitive_to_values; test_models.py::test_write_result_default_is_a_confirmation_request; test_service.py::test_r32_broadcaster_drift_is_refused |
| R33 | test_confirmation.py::test_validate_only_does_not_consume; test_deployment.py::test_compose_applies_every_hardening_switch; test_main.py::test_the_app_is_handed_to_uvicorn_on_the_container_bind |
| R34 | test_service.py::test_r34_guard_runs_again_after_the_token_is_consumed; test_write_safety.py::test_roster_recheck_refusal_sends_nothing_before_dispatch |
| R35 | test_server.py::test_all_six_groups_plus_oauth_register_exactly_41; test_server.py::test_authorised_tools_list_over_http_returns_the_planned_surface; test_server.py::test_default_boot_registers_exactly_the_23_read_minus_user_token_reads |
| R36 | test_capabilities.py::test_default_boot_registers_only_app_reads; test_capabilities.py::test_explicit_disable_omits_any_tool_by_name; test_capabilities.py::test_token_requirements_match_the_spec_table |
| R37 | test_capabilities.py::test_all_tools_and_max_count; test_capabilities.py::test_token_requirements_match_the_spec_table; test_models.py::test_account_status_result_shape |
| R38 | test_models.py::test_nested_poll_projection_projects_helix_json; test_models.py::test_r38_every_entity_model_round_trips; test_models.py::test_r38_every_result_model_accepts_an_empty_envelope |
| R39 | test_server.py::test_every_planned_tool_is_callable_with_its_documented_arguments; test_server.py::test_missing_required_argument_is_refused_before_any_service_call; test_server.py::test_schema_violation_is_refused_before_any_service_call |
| R40 | test_service.py::test_r17_malformed_cursor_never_reaches_a_url; test_service.py::test_r40_first_above_the_documented_bound_is_refused; test_service.py::test_r40_mutually_exclusive_cursors_are_refused |
| R41 | test_sizes.py::test_every_list_tool_is_covered_by_the_size_matrix; test_sizes.py::test_first_is_bounded_by_the_endpoint_maximum; test_sizes.py::test_worst_case_page_stays_under_4_mib |
| R42 | test_models.py::test_pagination_envelope_shape; test_project.py::test_malformed_body_is_upstream_error_never_empty_list; test_project.py::test_pagination_empty_cursor_means_no_more |
| R43 | test_guard.py::test_non_allowlisted_broadcaster_raises_the_documented_kind; test_project.py::test_automod_settings_mistyped_field_is_a_tool_error_not_an_internal_error |
| R44 | test_oauth_units.py::test_flow_complete_marks_an_oauth_server_error_retryable; test_oauth_units.py::test_flow_complete_reports_an_unexpected_oauth_rejection |
| R45 | test_errors.py::test_permission_hint_names_both_sides_of_the_scope_gap; test_project.py::test_automod_settings_rejects_the_nested_shape_it_replaced; test_service.py::test_r45_empty_result_is_a_success_not_a_permission_problem |
| R46 | test_config.py::test_valid_environment_loads_with_documented_defaults; test_server.py::test_each_write_group_adds_exactly_its_own_tools |
| R47 | test_security.py::test_a_stranger_path_is_never_exempted_by_open_paths; test_security.py::test_health_stays_public_even_when_oauth_paths_are_opened; test_security.py::test_oauth_exempt_paths_are_still_host_guarded |
| R48 | test_deployment.py::test_compose_mounts_no_host_directory_by_default; test_deployment.py::test_the_data_directory_belongs_to_the_service_user; test_oauth_units.py::test_flow_complete_forces_reauth_on_a_refused_grant |
| R49 | test_identity.py::test_unsigned_in_operator_refuses_before_any_roster_lookup; test_oauth_units.py::test_build_token_refuses_malformed_body_without_echoing; test_oauth_units.py::test_flow_complete_forces_reauth_on_a_refused_grant |
| R50 | test_deployment.py::test_the_image_starts_the_same_entry_point_the_tests_cover; test_server.py::test_healthz_is_degraded_when_the_boot_probe_cannot_reach_twitch; test_server.py::test_healthz_reports_ok_when_the_app_token_mints |
| R51 | test_models.py::test_disconnect_result_reports_revocation; test_service.py::test_r51_disconnect_revokes_only_this_credential |
| R52 | test_confirmation.py::test_mint_binds_tool_args_and_channel_without_storing_values; test_identity.py::test_refusal_is_logged_without_roster_content; test_logging.py::test_configure_logging_installs_one_json_handler_and_quiets_httpx |
| R53 | test_guard.py::test_denial_is_logged_without_the_refused_id; test_identity.py::test_refusal_is_logged_without_roster_content; test_logging.py::test_json_formatter_emits_one_json_object_per_line |
| R54 | test_logging.py::test_each_documented_pattern_scrubs_its_own_shape; test_logging.py::test_key_name_redaction_replaces_values_in_a_json_blob; test_logging.py::test_secret_key_names_are_in_the_registry |
| R55 | test_network_guard.py::test_direct_httpx_call_fails_the_run |
| R56 | test_gate.py::test_check_runs_the_three_gates; test_gate.py::test_lint_is_ruff_check_and_format_check; test_gate.py::test_pyproject_holds_the_strict_and_coverage_gates |
| R57 | test_gate.py::test_every_specification_requirement_is_named_by_a_test; test_gate.py::test_every_traceability_row_cites_a_real_test_of_its_own_file; test_gate.py::test_the_citation_map_is_not_stale |

---

## 3. Deviations from the approved spec

**None.** No requirement was implemented differently from the spec, and no feature, refactor or tool outside the spec
was added. Every divergence found during the build turned out to be a defect in the *spec text* or a place where the
spec was silent; each was escalated and recorded as a human-approved amendment rather than patched over:

| Item | What happened | Resolution |
|---|---|---|
| Tool count prose (`Writes (15)` / `Maximum 40`) contradicted the authoritative §6.4 table | Code and tests disagreed with each other because the prose disagreed with the table | **A1-D1** — keep 16 writes / 41 tools, prose corrected |
| Where code and test disagree | Needed a tie-breaker rule | **A1-D2** — spec is authority; if silent, stop and escalate |
| Default registered tool count described as 23 | The spec's full read set was described as if it were the default; the code registers 16 until an account signs in | **A2-D1** — default 16, 25 signed in, 41 maximum |
| Helix `404` on a read | Spec silent on which `kind` a read `404` carries | **A2-D2** — new `not_found` kind; R27 unchanged for guard lookups |
| Three Helix wire shapes (chatters, schedule, automod settings) | Spec silent; the draft models guessed wrong | **A2-D3** — follow the Helix documentation |
| Review round budget | Set to 3 by A1-D4, later superseded | **A2-D4** — 5 rounds |
| Live acceptance | Requires the operator's real credentials | **A1-D5 / A2-D5** — written, not executed, recorded UNVERIFIED |

Two product defects were found in the implementation (not the spec) and fixed with tests:

1. **The app-token mint was refused by its own invariant** (R13). `_headers` demanded `Client-Id` *and*
   `Authorization: Bearer` on every request, including `POST /oauth2/token`, which legitimately carries neither. The
   exemption is now scoped to exactly that route; every other path still refuses a bearer-less request, and a test
   pins the boundary.
2. **A stale verified-identity verdict could survive a rejected token** (R30). `RateLimiter.trip_auth` opened the 401
   circuit but nothing invalidated the identity and confirmation caches. `TwitchService.on_identity_change` is now
   registered as the limiter's auth-failure listener, so the first 401 invalidates identity and clears confirmations,
   and a raising listener can neither mask the 401 nor recurse through the circuit.

---

## 4. Review findings and disposition

Phase 5 ran with a **read-only reviewer subagent** (fresh context, did not edit code, did not take this report on
trust). [`review-round-1.md`](review-round-1.md) returned **CHANGES REQUESTED**: 4 blockers, 6 majors, 6 minors.

| Finding | Disposition |
|---|---|
| B1–B3 — three response shapes and the read-404 kind did not match the Helix documentation | Escalated as spec-silent (A1-D2); ruled by the human as **A2-D2/A2-D3**, then implemented |
| B4 — no test pinned "only `twitch_client.py` imports httpx" (A5) | **Closed.** `tests/test_network_guard.py` now walks the AST of every module under `src/` and asserts the set of modules importing `httpx` is exactly `{twitch_client.py}` |
| README/CLAUDE/CHANGELOG counted 15 writes / 40 tools / 3 retries / default 23 tools | **Closed** — corrected against §6.4 and A1-D1/A2-D1, and the `TWITCH_MAX_5XX_RETRIES` doc now matches `config.py` (2) |
| Missing tests for the auth listener, `try_live` gate, app-token mint, boot probe, entry point | **Closed** — added in `test_ratelimit.py`, `test_try_live.py`, `test_twitch_client.py`, `test_server.py`, `test_main.py` |
| Test layer not mypy-clean (71 errors) | **Closed** — `make typecheck` runs mypy over `src` **and** `tests`; now `Success: no issues found in 49 source files` |
| Coverage below the gate | **Closed** — 90.69% branch coverage, above the 90% floor |
| `tests/twitch_fakes.load()` could not read fixtures named with an explicit `.json` | **Closed** — accepts both spellings; duplicate `.json.json` fixtures removed |
| Boot made credential calls that broke `request_count == 0` assertions | **Closed** — `FakeTwitch.helix_request_count` distinguishes "no Helix call" from "no call at all" |

**Round 2** ([`review-round-2.md`](review-round-2.md), independent read-only subagent, re-ran the gate itself and
independently re-measured the README size table) returned **CHANGES REQUESTED**: 1 blocker, 2 majors, 3 minors. All
were fixed against the existing spec — no amendment was needed or sought — and are itemised with their root causes in
[`fix-round-2.md`](fix-round-2.md):

| Finding | Disposition |
|---|---|
| **B1** — §2 of this report cited 9 tests against the wrong file, and `test_gate.py`'s R57 check could not notice | **Closed.** The generator's file-boundary bug (`NR` instead of `FNR`) was found and fixed, the table regenerated, and `tests/test_gate.py` now parses *this report* and fails if any `file::test` cell is not a real `def` in that file, or if any R1–R57 loses its row |
| **F2** — the automod projection accepted an unreadable record and returned all-absent levels (R45/G5) | **Closed.** `_AUTOMOD_LEVEL_KEYS` now gate the projection, keyed on the levels rather than on `broadcaster_id` so the legacy nested body cannot slip past |
| **F3** — a mistyped Helix value escaped `pydantic` as a JSON-RPC internal error instead of an `isError` result (R43) | **Closed.** `_build` converts `ValidationError` to `kind=upstream` at the single choke point every entity passes through |
| **F1** — the `not_found` hint named the allowlist, reintroducing the ambiguity A2-D2 removed | **Closed.** Inline hint dropped; the default hint points only at the id/login |
| **F4** — `project_schedule` accepts `data` without `segments` | **Accepted as a documented limitation.** An empty schedule is legitimate and whether Twitch ever omits `segments` is unobserved (A3 is operator-run); a hard check would trade a cosmetic silent-empty risk for a false failure on a real account |
| **F5** — `config.py` `# type: ignore[call-arg]` | Inspected and cleared by the reviewer; no action |

The reviewer also confirmed closed, independently: B1–B4 of round 1, the M1–M6 majors, and the m1–m6 minors.

**Round 3** ([`review-round-3.md`](review-round-3.md), independent read-only subagent) returned **APPROVED**. It
re-derived §2 from disk (57 rows, 154 cells, 0 misattributions), probed the automod projection with hostile bodies
(identity-only record, all-`None` levels, the legacy nested body, `{}` — all `upstream`; a `0` level still projects),
confirmed `_build` is the only path Helix JSON takes into a model (`grep model_validate src/` returns exactly one
site) and threw 33 malformed bodies at every projection with no non-`TwitchMcpError` escape, and re-checked the
R27/`not_found` boundary. It raised two minors, both taken:

| Finding | Disposition |
|---|---|
| **N1** — the cell check was a count over the whole report, so one real test cited 57 times, or 57 empty rows plus citations in prose, could satisfy it | **Fixed.** `test_every_traceability_row_cites_a_real_test_of_its_own_file` now walks the table row by row: exactly one row per requirement, every cited cell resolving to a `def` in the file it names, and no requirement left without a resolvable citation |
| **N2** — generator scripts left in the repository root | **Fixed.** Removed from the repo; the table generator now lives outside the tree (`%LOCALAPPDATA%\Temp\twitch_rtable.sh`), since it is build tooling and not a deliverable |

---

## 5. Commands run, with results

```
$ ruff check src tests tools                All checks passed!
$ ruff format --check src tests tools       50 files already formatted
$ mypy                                      Success: no issues found in 49 source files
$ pytest -q                                 949 passed in 90.50s
                                            Required test coverage of 90% reached.
                                            Total coverage: 90.80%
```

`make check` is exactly those three gates (`lint`, `typecheck`, `test`), pinned by `tests/test_gate.py` so a future
weakening of any of them fails a test rather than passing silently (R56). That file also pins the requirement map
itself (R57): every requirement named by at least one test, no test naming a requirement the spec does not define,
every row of §2 present, and every `file::test` cell resolving to a real function in the file it claims.

Coverage floor detail: 3,636 statements, 246 missed, 808 branches, 145 partial.

---

## 6. Acceptance criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| **A1** | `make check` passes: ruff clean, mypy strict clean, coverage ≥90% branch | **MET** | Section 5 |
| **A2** | Live read smoke against the real API (`twitch_account_status`, `get_users`, `get_streams`, `get_channels`) | **UNVERIFIED** | A1-D5/A2-D5 — operator-run. `tools/try_live.py` refuses to run without `TWITCH_LIVE_OK=yes`; the gate itself is pinned by `tests/test_try_live.py` |
| **A3** | Live write smoke: create/end a poll on the operator's own channel; `send_chat_message` to a non-allowlisted channel makes no request | **UNVERIFIED** | Same gate. The non-allowlisted no-request half is verified in-process: `test_guard.py` and `test_service.py` assert zero requests and zero token spend |
| **A4** | A write returns `confirmation_required` with zero HTTP calls, then exactly one write with the token | **MET** (in-process) | `test_service.py::test_r32_valid_token_dispatches_exactly_one_request` and the R31/R32/R34 group |
| **A5** | `grep -rn httpx src/ \| grep -v twitch_client.py` is empty | **MET in intent, see note** | No module other than `twitch_client.py` imports `httpx`, pinned by AST in `tests/test_network_guard.py`. The literal grep still prints three *prose* mentions (a comment in `logging_setup.py` explaining why the `httpx` logger is silenced, a docstring reference in `ratelimit.py`, a comment in `server.py`) plus compiled `__pycache__/*.pyc` artifacts. See Open items |
| **A6** | No token-shaped string in a full test-run log | **MET** | 940 tests run with `--log-file`; grep for 28+ character base62 runs and `Bearer <20+ chars>` returned **0** matches across 649 log lines |
| **A7** | `docker compose up` binds `127.0.0.1:8770` only; LAN fails, localhost answers `/healthz` | **UNVERIFIED** | A2-D5 — container not started by the agent. Verified statically by `test_deployment.py` (loopback publish, UID 10001, read-only rootfs, dropped caps, no-new-privileges, entry point) |
| **A8** | This report, with a row for every R1–R57 and no deviations | **MET** | Section 2 (57 rows) and Section 3 (no deviations) |

---

## 7. Open items for the human

1. **A5's literal grep** (Section 6) prints three prose mentions and compiled artifacts. The binding invariant (R11)
   concerns *imports* and is pinned by AST. Either the wording of A5 should be tightened to "no module other than
   `twitch_client.py` imports `httpx`", or the three comments should be reworded. This is spec text, so it is left
   untouched pending your ruling.
2. **A2/A3/A7 stay UNVERIFIED by your ruling** (A1-D5/A2-D5). To close them: fill `.env` from `.env.example`, then
   `scripts/setup.sh && scripts/start.sh`, `curl -fsS http://127.0.0.1:8770/healthz`, and
   `TWITCH_LIVE_OK=yes python tools/try_live.py`. Report the output and this report can be updated.
3. **`make audit`** was not run in this session (it needs network access to the vulnerability databases). It is a
   separate gate from `make check` and should be run once before the first deployment.
