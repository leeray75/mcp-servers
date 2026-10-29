# Implementation report: discord-mcp-server initial build

- **Spec:** `../../SPEC.md` (Approved, 2026-10-28, approved by human). **Tasks:** `tasks.md`. **Workflow:** `workflow.md`.
- **Branch:** `feature/discord-mcp-server` (not changed, nothing committed).
- **Implementer:** Haiku 5.5 subagent, T0 to T13 completed; fix round 1 (`fix-round-1.md`, amendment A1) also done. Reviewer and fix rounds are not part of this report.

## Gate results (final tree)

| Gate | Command | Result |
|---|---|---|
| Lint and format | `make check` (`ruff check`, `ruff format --check`) | All checks passed; 37 files already formatted |
| Types | `make check` (`mypy --strict`, files `src` and `tests`) | Success: no issues found in 30 source files |
| Tests and coverage | `make check` (`pytest`, branch coverage, gate 90%) | **414 passed**; **total coverage 96.25%** (after fix round 1) (gate 90% reached) |
| Dependency audit | `make audit` (`pip-audit --require-hashes` on `requirements.txt`) | No known vulnerabilities found |
| Compose | `docker compose config` on a copy with an empty `.env` placeholder | Config valid; `127.0.0.1` binding, `read_only`, `cap_drop`, `pids_limit`, `mem_limit`, `init` present |
| Image | `docker build -t discord-mcp-server:local .` | Built successfully (`71330c86ab55`) |
| Offline run | `docker run --network none …` with dummy tokens, `docker exec … /healthz` | `{"status":"degraded","reason":"discord_unreachable_at_boot"}`; boot log lists 12 registered and 2 omitted tools with reasons, no ids |
| Scripts | `bash -n scripts/*.sh scripts/lib/*.sh` | No syntax errors (shellcheck not installed on this host) |

No `.env` was created or read in the repository. No real Discord request was made: every test is offline, and the
container check was run with `--network none`. AC5 (live smoke with a real token) is not run, as the spec allows.

## Traceability (R1-R42, AC1-AC4)

Test names are as in `tests/`. "Reviewer check" rows are the docs and container requirements that the spec maps to a
file and a reviewer check.

| Req | Implementation (file::symbol) | Test (file::name) |
|---|---|---|
| R1 | `config.py::Settings`, `load_settings`; no other module reads the environment | `test_config.py::test_valid_environment_loads_with_defaults`, `test_invalid_values_are_rejected_without_echoing_them` |
| R2 | `config.py::Settings._check_auth_token`, `_check_bot_token`, `_check_allowlist`, `_check_secret` (placeholder and length rules) | `test_config.py::test_required_fields_are_named_when_missing`, `test_invalid_values_are_rejected_without_echoing_them` |
| R3 | `config.py::Settings._check_api_base` (https, host, `v<N>` with N >= 10, no port, credentials or query), `_check_user_agent` | `test_config.py::test_api_base_accepts_newer_versions_and_strips_slash`, `test_invalid_values_are_rejected_without_echoing_them` |
| R4 | `discord_client.py::DiscordClient.probe`, `_probe_call` (3 retries, 1/2/4 s); `server.py::create_app` lifespan runs `service.boot()` before serving | `test_discord_client.py::test_probe_ok_reads_bot_id_and_flags`, `test_probe_unreachable_after_retries`; `test_server.py::test_boot_auth_keeps_every_tool_and_each_returns_kind_auth` |
| R5 (A1-R5) | `server.py::create_app.healthz` (ok / discord_auth / discord_unreachable_at_boot), lifespan warning; `service.py::DiscordService.boot`; `capabilities.py::plan_tools` (after a boot 401 every tool not disabled by config is registered) | `test_server.py::test_healthz_is_open_and_reports_ok`, `test_healthz_degraded_on_auth`, `test_healthz_degraded_when_unreachable_at_boot_and_leaks_nothing`, `test_unreachable_boot_logs_the_reason_once`, `test_boot_unreachable_omits_only_intent_tools`, `test_boot_auth_keeps_every_tool_and_each_returns_kind_auth`; `test_permissions_caps_project.py::test_plan_auth_keeps_every_tool_so_each_returns_kind_auth` |
| R6 (A1-R6, A2-R6a) | `discord_client.py::_usable_flags` and `_application_flags` (each of `flags` and `flags_new` parsed independently; effective flags are the bitwise OR of every usable field; never raises; `_FLAGS_NEW_RE`); `capabilities.py::plan_tools`, `intents_from_flags`; `config.py::discord_force_message_content`, `discord_force_guild_members` (default false); `DISCORD_ENABLE_*=false` always wins | `test_flags_a2.py::test_flags_only_is_used`, `::test_flags_new_integer_only_is_used`, `::test_flags_new_decimal_string_only_is_used`, `::test_both_present_with_disjoint_bits_enables_both_intents`, `::test_both_present_and_equal`, `::test_neither_present_is_unknown`, `::test_malformed_flags_new_is_ignored_and_probe_completes[*]`, `::test_negative_boolean_or_unusable_flags_is_ignored[*]`; `test_permissions_caps_project.py::test_intent_flags_matrix`, `test_plan_config_disable_wins_over_intents`; `test_fix_round1.py::test_plan_with_flags_missing_and_force_flags[*]` |
| R7 (A1-R7, A2-R7a) | `server.py::_boot_summary` (registered; omitted with reasons intent_disabled, flags_missing, config_disabled, probe_failed; forced; effective application flags integer or null and `flags_source` one of flags, flags_new, flags+flags_new, or null shown as `none`; no ids) | `test_flags_a2.py::test_boot_line_reports_the_effective_flags_and_source[*]`, `::test_boot_line_for_a_forced_tool_with_unknown_flags`; `test_server.py::test_boot_log_lists_registered_and_omitted_without_ids`; `test_fix_round1.py::test_boot_line_records_forced_tools_and_observed_flags` |
| R8 | `capabilities.py::TOOL_SPECS` (fixed order); `server.py::_register_tools` | `test_permissions_caps_project.py::test_registration_order_is_the_section_7_order`; `test_server.py::test_registered_tools_are_exact_names_in_fixed_order`, `test_tools_list_is_identical_across_requests` |
| R9 | `server.py::_register_tools` (title, description, `READ_ONLY` annotations, `additionalProperties` false on the stored schema); `models.py` (outputSchema from `Annotated[CallToolResult, Model]`) | `test_server.py::test_every_tool_has_title_annotations_schema_and_output_schema` |
| R10 | `server.py::DiscordServer.call_tool` (unknown and unexpected arguments, schema `ValidationError` as `kind=validation`, before budget, guard or network) | `test_server.py::test_unexpected_argument_is_a_validation_error_before_any_request`, `test_schema_violation_names_the_field_and_sends_nothing` |
| R11 | `server.py::_ok_result` (`structuredContent` plus one compact-JSON text block) | `test_server.py::test_success_has_structured_content_and_identical_text_block` |
| R12 | `guard.py::Guard.require_guild`, `Guard.resolve_channel` (DM refusal); every tool in `service.py` calls them first | `test_service.py::test_non_allowlisted_guild_is_refused_with_no_request`, `test_dm_channel_is_refused`, `test_channel_in_other_guild_is_refused_after_only_the_channel_lookup` |
| R13 (A1-R13) | `guard.py::Guard.resolve_channel` (every channel-lookup failure except auth, rate_limited and unavailable becomes `forbidden_guild` with the identical message "Channel is not in an allowlisted guild"); `guard.py::GUILD_REFUSAL` for guild ids; `service.py::_check_search_channel` and `list_threads` parent check use the same message | `test_fix_round1.py::test_every_channel_lookup_failure_gets_one_identical_refusal`, `test_auth_and_rate_limit_still_surface_from_the_channel_lookup`; `test_server.py::test_dm_and_unknown_channels_are_refused_identically_for_every_channel_tool[*]`; `test_service.py::test_non_allowlisted_guild_is_refused_with_no_request` |
| R14 | `service.py::DiscordService.list_guilds` (allowlisted only; `missing_allowlisted_guild_ids`) | `test_service.py::test_list_guilds_returns_only_allowlisted_and_reports_missing`; `test_server.py::test_list_guilds_has_no_id_and_never_returns_other_guilds` |
| R15 (A1-R15) | `test_server.py::GUARD_CASES`, `CHANNEL_TOOLS`: asserts the exact request list per tool (none for guild tools, exactly one channel lookup for channel tools); `list_guilds` filters its output | `test_server.py::test_guard_cases_cover_every_registered_tool`, `test_non_allowlisted_scope_is_refused_with_no_guild_request[*]`, `test_search_channel_scope_outside_the_allowlist_is_refused`, `test_list_guilds_has_no_id_and_never_returns_other_guilds` |
| R16 (A1-R16) | `discord_client.py::DiscordClient._send` (streamed cap); `config.py::discord_max_response_bytes` default 4_194_304; `discord_client.py::SIZE_CAP_HINT` (retry with a smaller limit) | `test_fix_round1.py::test_default_response_cap_is_four_mebibytes`, `test_oversized_response_hint_asks_for_a_smaller_limit`; `test_discord_client.py::test_oversized_body_is_refused_and_not_retried` |
| R17 | `discord_client.py::Route.build` (percent-encodes values); `service.py` validates snowflakes and emoji before any path use | `test_snowflake.py::test_snowflake_pattern_and_range`; `test_service.py::test_reaction_emoji_is_percent_encoded_in_the_path`, `test_invalid_emoji_is_rejected` |
| R18 (review M1) | `discord_client.py::DiscordClient.get`: the bucket lease is released in a `finally` on every path, including cancellation; `httpx.RequestError` (for example DecodingError) maps to `kind=upstream` | `test_fix_round1.py::test_cancelled_in_flight_request_does_not_wedge_its_bucket`, `test_decoding_error_is_upstream_and_the_next_call_works`; `test_fix_round1.py::test_bucket_stays_blocked_for_the_full_duration_on_later_calls` |
| R19 | `ratelimit.py::RateLimiter.trip_auth`, `check_open` (logged once at ERROR); `discord_client.py::DiscordClient._finish` (401) | `test_discord_client.py::test_unauthorised_trips_the_hard_stop_and_later_calls_send_nothing`; `test_ratelimit.py::test_auth_hard_stop_refuses_everything_and_logs_once` |
| R20 | `ratelimit.py::RateLimiter._route_buckets`, `_buckets`, `_state_for`, `Lease.learn` (learned bucket hash plus major parameter) | `test_ratelimit.py::test_bucket_is_learned_from_headers_and_pre_emptive_wait_applies`, `test_different_major_parameters_do_not_share_a_bucket`, `test_same_bucket_requests_are_serialised` |
| R21 | `ratelimit.py::RateLimiter._wait_bucket` (pre-emptive wait, or `rate_limited` above the in-call limit) | `test_ratelimit.py::test_pre_emptive_wait_refuses_when_longer_than_the_in_call_limit` |
| R22 | `discord_client.py::DiscordClient._rate_limited` (user block plus jitter, global pause, shared block without counting or replay), `_retry_after_seconds`, `_scope_of`, the 429 branch of `get` | `test_discord_client.py::test_user_429_is_replayed_once_after_the_full_wait`, `test_user_429_beyond_the_in_call_limit_returns_rate_limited`, `test_user_429_replays_are_capped`, `test_global_429_pauses_unrelated_routes`, `test_shared_429_blocks_the_bucket_is_not_replayed_or_counted`, `test_429_without_body_uses_retry_after_header`; `test_ratelimit.py::test_block_is_full_retry_after_and_is_not_capped` |
| R23 | `ratelimit.py::RateLimiter._take_token` (global pacing, `DISCORD_GLOBAL_REQUESTS_PER_SECOND`) | `test_ratelimit.py::test_global_pacing_spaces_requests` |
| R24 | `ratelimit.py::RateLimiter.record_invalid` (sliding 10 minutes, WARNING at 50%), `circuit_retry_after`, `check_open` | `test_ratelimit.py::test_invalid_budget_warns_at_half_and_opens_circuit`, `test_circuit_closes_once_the_window_drains`; `test_discord_client.py::test_invalid_responses_open_the_circuit` |
| R25 | `snowflake.py::is_snowflake`, `validate_snowflake` (pattern and < 2^64); ids kept as strings in `project.py` | `test_snowflake.py::test_snowflake_pattern_and_range`, `test_validate_snowflake_names_the_field_only`; `test_permissions_caps_project.py::test_message_projection_snowflakes_are_strings` |
| R26 | `snowflake.py::parse_time_argument`, `datetime_to_snowflake` (`(ms - epoch) << 22`, naive, pre-epoch and future rejected), `time_argument_to_snowflake`, `time_argument_to_datetime`; `service.py::list_pins` sends ISO | `test_snowflake.py::test_datetime_becomes_synthetic_snowflake`, `test_time_arguments_reject_naive_and_malformed`, `test_datetime_before_epoch_or_future_or_naive_is_rejected`, `test_time_argument_iso_with_offset_converts`; `test_service.py::test_list_messages_datetime_argument_becomes_snowflake`, `test_list_pins_projection_and_cursor` |
| R27 | `snowflake.py::snowflake_created_at`, `to_iso` (ms, Z), `discord_timestamp`; `project.py` `created_at` fields | `test_snowflake.py::test_created_at_is_derived_from_the_snowflake`, `test_discord_timestamp_normalises`, `test_to_iso_requires_aware_datetimes` |
| R28 | `permissions.py::effective_permissions` (section 10 steps 1 to 4), `base_permissions`; `service.py::DiscordService._channel_permissions`, `_guild_context` (thread uses parent overwrites) | `test_permissions_caps_project.py::test_owner_gets_everything`, `test_administrator_role_gets_everything`, `test_everyone_deny_then_role_allow_restores_the_permission`, `test_role_deny_and_allow_are_combined_then_applied`, `test_member_overwrite_for_the_bot_is_applied_last`, `test_without_view_channel_every_other_permission_is_absent`; `test_service.py::test_thread_permission_uses_its_parent_overwrites` |
| R29 | `service.py::DiscordService._require_channel`, `_require_guild_permission`, `_history_permissions` (VIEW, READ_MESSAGE_HISTORY, CONNECT for voice and stage) | `test_service.py::test_empty_versus_forbidden_no_messages_request_when_history_is_denied`, `test_voice_channel_needs_connect`, `test_genuinely_empty_channel_with_permission_is_success`, `test_list_pins_without_history_is_permission`, `test_reactions_need_history_permission`; `test_server.py::test_list_messages_end_to_end_with_permission_error` |
| R30 | `errors.py::DiscordMcpError.to_text`, `to_payload` (exact `error` fields); `server.py::_error_result` (`isError: true`); JSON-RPC `MCPError` only in `DiscordServer.call_tool` and `_run` | `test_errors.py::test_structured_payload_has_exactly_the_r30_fields`, `test_text_is_kind_message_and_hint`; `test_server.py::test_error_result_has_the_r30_shape`, `test_unexpected_exception_is_generic_jsonrpc_error_without_detail`, `test_unknown_tool_is_a_jsonrpc_protocol_error` |
| R31 | `errors.py::ERROR_KINDS`, `DEFAULT_HINTS`; `discord_client.py::DiscordClient._finish` (status first, numeric code sets, never message text); `ratelimit`, `unavailable` on connect failure | `test_errors.py::test_error_kinds_are_the_r31_vocabulary`; `test_discord_client.py::test_error_mapping_uses_status_and_numeric_code_only`, `test_message_text_is_ignored_when_it_contradicts_the_code`, `test_read_timeout_is_an_upstream_error`, `test_index_pending_carries_retry_after_and_documents` |
| R32 | `logging_setup.py::redact`, `RedactionFilter`, `JsonFormatter` (route, status, scope, duration_ms, outcome only); `discord_client.py::DiscordClient._log`; `server.py::_run` (tool, duration, outcome); `register_secret` for both tokens in `create_app` | `test_security_logging.py::test_registered_secrets_are_scrubbed_everywhere`, `test_redaction_filter_scrubs_message_and_extra_fields`, `test_json_formatter_emits_only_the_allowed_fields`, `test_tool_call_logs_hold_no_token_url_or_response_body`, `test_refused_call_logs_do_not_echo_the_refused_id` |
| R33 | `server.py::UNTRUSTED_NOTE` (appended to every description and to `INSTRUCTIONS`) | `test_server.py::test_every_tool_has_title_annotations_schema_and_output_schema`, `test_instructions_carry_the_untrusted_content_note` |
| R34 | `project.py` projections (only section 8 fields; `Attachment` has no URL field; embed `url` kept as author-supplied) | `test_permissions_caps_project.py::test_message_projection_drops_cdn_urls_and_keeps_full_content`, `test_guild_projection_keeps_only_section_8_fields_and_sorts_roles` |
| R35 | `models.py::Meta`; `service.py::_meta_of` (upstream fetch time, cached flag, age), `_meta_now` (response time); endpoint is the route template | `test_service.py::test_get_guild_returns_projection_and_caches_metadata` (cached flag), `test_list_guilds_returns_only_allowlisted_and_reports_missing` (endpoint), `test_get_channel_checks_view_permission_and_projects` (endpoint) |
| R36 | `budget.py::ToolBudget.try_consume` (overall and per-tool token buckets, both checked before either is charged); `server.py::_run` (runs after schema validation, before the service and guard) | `test_budget_cache.py::test_budget_refuses_overall_then_refills`, `test_budget_refuses_per_tool_without_charging_the_overall_bucket`; `test_server.py::test_budget_refusal_is_rate_limited_and_sends_nothing`, `test_budget_is_global_across_tools` |
| R37 | `cache.py::TTLCache` (entries and bytes bounds, in-flight de-duplication, TTL 0 disables, failures not stored, no stale-if-error); `service.py` caches only guild, guild channels, channel, bot member and user guild pages | `test_budget_cache.py::test_cache_stores_and_serves_fresh_values`, `test_zero_ttl_disables_storage`, `test_failed_fetch_is_never_cached`, `test_in_flight_requests_are_deduplicated`, `test_byte_bound_evicts_least_recently_used`; `test_service.py::test_messages_are_never_cached`, `test_metadata_ttl_zero_disables_the_service_cache` |
| R38 | `security.py` (copied from sports: `HostGuardMiddleware`, `BearerAuthMiddleware`, `HEALTH_PATH`); `server.py::create_app` (64 KiB body limit, loopback `TransportSecuritySettings`, middleware order) | `test_server.py::test_healthz_is_open_and_reports_ok`, `test_mcp_requires_bearer_token`, `test_host_header_must_be_loopback`, `test_origin_must_be_loopback`, `test_request_body_is_limited_to_64_kib`; `test_security_logging.py::test_websocket_is_refused`, `test_lifespan_passes_through`, `test_healthz_skips_bearer_but_other_paths_require_it` |
| R39 | `Dockerfile` (non-root 10001, hashed install, read-only runtime), `docker-compose.yml` (loopback `${HOST_PORT:-8769}:8000`, hardening, `env_file: .env`, healthcheck, json-file rotation), `.dockerignore` (excludes `.env`) | Reviewer check. `docker compose config` and `docker build` ran; offline container run verified healthz |
| R40 | `scripts/{setup,start,stop,restart,rebuild,logs,status,smoke}.sh`, `.ps1` twins, `scripts/lib/common.sh`, `scripts/lib/Invoke-WslScript.ps1`; `setup.sh` prompts for the bot token and allowlist (hidden, never echoed); `smoke.sh` prints sizes only | Reviewer check; `bash -n` run on all scripts |
| R41 | `README.md` (Developer Portal setup, permissions 66688 and 1115264, invite URL, checklist, configuration table, operations, client snippets, tools, troubleshooting) | Reviewer check |
| R42 | `CHANGELOG.md` (`[Unreleased]`), `.env.example` (every setting, placeholders only), `CLAUDE.md` (server), root `CLAUDE.md` table row, root `README.md` server list row | Reviewer check |
| A1-R43 | `server.py::_ok_result` (exclude_unset: keys that do not apply are omitted); worst-case figures measured by `tests/test_sizes.py` | `test_sizes.py::test_worst_case_projection_is_under_four_mebibytes[*]` (14 tools; measured sizes in the README and below) |
| AC1 | `make check` (gate 90%, branch) and `make audit` | `make check`: 414 passed, coverage 96.25%; `make audit`: no known vulnerabilities |
| AC2 | This table | One row per R1 to R42 |
| AC3 | `docker-compose.yml`, `Dockerfile` | `docker compose config` valid; `docker build` succeeded |
| AC4 | README, CHANGELOG, `.env.example`, `CLAUDE.md`, root `CLAUDE.md`, root `README.md` | Reviewer check |
| AC5 | `tools/try_live.py` (opt-in, not run) | Not run: needs the human's token; the spec marks it optional |

## Deviations and interpretations

The spec has no deviation that changes a requirement. The following are interpretations the reviewer should confirm:

1. **R15, channel-id tools.** A non-allowlisted *channel* can only be identified by fetching it, so each channel-scoped
   tool sends one `GET /channels/{id}` before the refusal (the cached lookup the spec requires in R12). No other request
   is sent. The test asserts exactly that. Guild-id tools send zero requests.
2. **`list_guilds` has no id argument.** R15 is applied as output filtering: the test asserts it never returns a
   non-allowlisted guild.
3. **R5, auth probe.** With a `401` at boot every tool is registered (the spec says every tool returns `kind=auth`), so
   intent-dependent tools are not omitted in that case.
4. **Boot placement.** The probe and tool registration run in the ASGI lifespan (before uvicorn serves), not in
   `__main__`, so the HTTP client and the limiter's locks are created in the serving event loop.
5. **Missing upstream values.** `User.bot` defaults to `false` when Discord omits it (Discord omits the field for humans).
   Every other missing value is null, as specified.
6. **R31, unlisted statuses.** `400` maps to `validation`. Other `4xx` (not 401, 403, 404, 429) map to `upstream` with
   `retryable=false`, since the spec does not list them.
7. **`get_channel` flags.** `bot_can_view` and `bot_can_read_history` are null on `get_channel` (the spec lists them for
   `list_channels` only).
8. **`list_threads.bot_joined`.** Derived from the response's `members` (entries whose `id` is the thread id), since that
   array holds the bot's own thread memberships.
9. **Guild list ceiling.** `list_guilds` reads at most 5 pages of 200 (1000 guilds). If the cap is hit, `missing_allowlisted_guild_ids`
   can be incomplete. A warning is logged. Bots in more than 1000 guilds are not expected.
10. **Audit action names.** `AUDIT_ACTIONS` covers the documented action types as recalled; unknown types return `action: null`.
11. **Health.** `/healthz` also reports `discord_auth` after a runtime `401`, which is a superset of the boot rule.
12. **Search `has_more`.** Counted per hit group (one hit per group), so `next_offset` always advances.

## Protocol versions (D16)

Recorded from the pinned `mcp==2.3.0` SDK (`mcp_types.version`):

- `KNOWN_PROTOCOL_VERSIONS`: 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25, 2026-07-28.
- Handshake (`initialize`): 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25 (`LATEST_HANDSHAKE_VERSION` 2025-11-25).
- Stateless per-request envelope: 2026-07-28 (`MODERN_PROTOCOL_VERSIONS`, `LATEST_PROTOCOL_VERSION`).

The server keeps no state keyed to a session or connection, so both families behave the same. `DEFAULT_NEGOTIATED_VERSION`
is 2025-03-26.

## SDK behaviour verified (no spec gap)

- An `isError: true` result returned by a tool is passed through with its `structuredContent` (the SDK validates only
  successful results against `outputSchema`). The R30 shape is therefore delivered unchanged.
- `Annotated[CallToolResult, Model]` return annotations publish `outputSchema` from the model.
- The SDK does not add `additionalProperties: false`; the server sets it on the stored schema. Unknown arguments are
  rejected in `DiscordServer.call_tool`, because the SDK's argument model ignores extra keys.

## Commands run

| Command (abbreviated) | Result |
|---|---|
| `uv venv ~/.venvs/discord-mcp --python 3.12` | Created |
| `uv pip install -p ~/.venvs/discord-mcp/bin/python -e ".[dev]"` (first try) | Failed: `README.md` referenced by `pyproject.toml` did not exist; stub added |
| `uv pip install … -e ".[dev]"` (second try) | Installed; `mcp` 2.3.0 confirmed |
| `make lock VENV=~/.venvs/discord-mcp` | Regenerated `requirements.txt` (619 lines) and `requirements-dev.txt` (1508 lines); pins include `mcp==2.3.0`, `httpx==0.28.1`, `pydantic==2.14.1`, `starlette==1.10.0`, `uvicorn==0.54.0` |
| `make install VENV=~/.venvs/discord-mcp PYTHON=~/.local/bin/python3.12` | Hash-verified install succeeded |
| `ruff format src` / `ruff check --fix` (iterations) | Applied; a final `ruff check` passes |
| `mypy src` (iterations) | Clean at the end |
| `pytest` (iterations, `--no-cov`) | Final (fix round 1): 414 passed |
| `make check VENV=~/.venvs/discord-mcp` (initial build, superseded by fix round 1) | All checks passed; 37 files formatted; mypy no issues in 30 files; 282 passed; coverage 96.22% |
| `make audit VENV=~/.venvs/discord-mcp` | No known vulnerabilities found |
| `bash -n scripts/*.sh scripts/lib/*.sh` | No errors |
| `docker compose config --quiet` (validated copy, empty `.env`) | Valid |
| `docker build -t discord-mcp-server:local .` | Built |
| `docker run --network none … discord-mcp-server:local` + `docker exec … /healthz` | `degraded` / `discord_unreachable_at_boot`; container removed afterwards |
| `python3 tests/…` patch scripts (edits in temp, not the repo) | Applied; verified by grep and by the gates above |

## Open items

- AC5 (live smoke with a real token) is not run.
- `shellcheck` is not installed on this host; only `bash -n` was run.
- The reviewer should check R39 to R42 and the interpretations above.
- `CHANGELOG.md` and the server README describe the initial build; they will need an entry per later change.

## Fix round 1 update

Amendment A1 (SPEC section 16) and review round 1 were applied. Every finding and every A1 item, with its file:line and test, is in [`fix-round-1.md`](fix-round-1.md). The traceability rows above for R5, R6, R7, R13, R15, R16, R18 and the new A1-R43 row reflect the amended behaviour. A1 resolves review spec issues S1-S6. The deviations listed above that A1 changed are superseded: channel-id tools make one channel lookup (A1-R15); `get_channel` carries `bot_can_view` and `bot_can_read_history` (A1-8); `list_guilds` filters output (A1-R15).
