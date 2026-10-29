# Implementation report: user OAuth guild discovery (amendment A3)

- **Spec:** `SPEC.md` in this folder (Approved, 2026-10-29, approved by: human). Plan: `tasks.md`. Process: `workflow.md`.
- **Branch:** `feature/discord-user-oauth` (nothing committed or pushed).
- **Scope:** implemented exactly U1-U35, the SDK gate (§6.3), AC-U1 and AC-U2. Additions beyond the spec are listed under Deviations. The fix round (A3.1 and review m1-m11) is in `fix-round-1.md`.

## Result

| Gate | Result |
|---|---|
| SDK gate (T0), `tests/test_oauth_sdk_gate.py` | passed (3 of 3). Stop condition not met, so implementation continued |
| `ruff format --check .` | passed (61 files) |
| `ruff check .` | passed |
| `mypy` (strict, `src` and `tests`) | passed (41 files) |
| `pytest` with the 90% branch gate | **622 passed**, total coverage **96.61%** (branch) |
| Existing suite (AC-U2) | 454 pre-existing tests pass **unmodified** (no existing test file changed); 160 new tests added beyond the 454 pre-existing and the 3 gate tests (137 before fix round 1, 23 in it) |
| `bash -n` on every `scripts/*.sh` and `scripts/lib/*.sh` | passed |
| `.sh` line endings (must be LF) | LF, no CR |
| `docker compose` config, base and override | both valid (see command log) |

## Traceability

File paths are relative to `discord-mcp-server/`. `::` names a function or class in the file on its left.

| Req | Implementation (file::symbol) | Test (file::name) |
|---|---|---|
| SDK gate §6.3 | `tests/test_oauth_sdk_gate.py` (gate a: capabilities and version readable; gate b: `InputRequiredResult` URL elicitation; gate c: legacy handshake capabilities not retained) | `tests/test_oauth_sdk_gate.py::test_gate_a_per_request_capabilities_and_version_are_readable_when_stateless`, `::test_gate_b_input_required_url_elicitation_is_produced_from_a_tool_handler`, `::test_gate_c_legacy_handshake_capabilities_are_not_retained` |
| U1 | `config.py::Settings` fields `discord_user_oauth`, `discord_oauth_client_secret`, `host_port`, `discord_oauth_token_file`, `discord_oauth_url_elicitation`, `discord_oauth_pkce`; `DEFAULT_TOKEN_FILE` | `tests/test_user_oauth_config.py::test_switch_on_requires_the_client_secret_and_names_the_field`, `::test_switch_on_requires_the_application_id`, `::test_switch_on_requires_an_absolute_token_path`, `::test_host_port_is_range_checked` |
| U2 | `config.py::Settings._check_user_oauth` (names the field, never the value); `config.py::load_settings` (model-level errors named) | `tests/test_user_oauth_config.py::test_switch_on_rejects_the_placeholder_secret_without_echoing_it`, `::test_load_settings_reports_the_model_error_with_a_field_name` |
| U3 | `config.py::Settings._check_user_oauth` returns early when off; server registers nothing when off | `tests/test_user_oauth_config.py::test_switch_off_ignores_every_oauth_setting`; `tests/test_user_oauth_tools.py::test_the_switch_off_keeps_exactly_the_fourteen_tools`; existing suite unchanged |
| U4 | `capabilities.py::plan_tools` (mismatch rule, unprobed id trusted); `server.py::create_app` lifespan sets `user_routes["live"]` | `tests/test_user_oauth_config.py::test_plan_mismatched_application_id_disables_the_user_tools`, `::test_plan_unprobed_application_id_is_trusted`; `tests/test_user_oauth_routes.py::test_a_mismatched_application_id_turns_the_routes_into_404`; `tests/test_user_oauth_tools.py::test_a_mismatched_application_id_registers_none_of_the_five` |
| U5 | `capabilities.py::USER_OAUTH_TOOLS`; `server.py::_register_user_tools` (registration order) | `tests/test_user_oauth_config.py::test_plan_on_appends_the_five_tools_after_the_bot_tools`; `tests/test_user_oauth_tools.py::test_the_switch_on_registers_the_bot_tools_then_the_five_in_order` |
| U6 | `server.py::USER_READ_ONLY`, `USER_READ_OPEN`, `USER_DISCONNECT` | `tests/test_user_oauth_tools.py::test_the_five_tools_carry_the_annotations_of_the_amendment` |
| U7 | `server.py::_run_user` and `_run` (budget, R30 errors); `DiscordServer.call_tool` (unknown arguments) | `tests/test_user_oauth_tools.py::test_an_unexpected_argument_is_a_validation_error_before_any_request`, `::test_the_budget_applies_to_the_user_tools` |
| U8 | `oauth.py::authorize_url`, `oauth.py::PendingStore.start`; `service.py::UserOAuthService.begin_sign_in`; `server.py::_oauth_handlers` (start: 302, `Cache-Control`, `Referrer-Policy`) | `tests/test_user_oauth_routes.py::test_start_redirects_with_the_exact_authorize_url_and_headers`, `::test_start_without_pkce_has_no_challenge`; `tests/test_user_oauth_units.py::test_authorize_url_is_exact_with_pkce`, `::test_authorize_url_without_pkce_has_no_challenge` |
| U9 | `oauth.py::new_state` (32 bytes), `new_verifier` (64 chars, RFC 7636 set), `PendingStore` (single flow, 600 s, injectable clock and factories) | `tests/test_user_oauth_units.py::test_new_verifier_and_state_come_from_the_right_alphabets`, `::test_start_replaces_the_previous_flow_and_claim_is_single_use`, `::test_a_flow_expires_after_ten_minutes`, `::test_clear_drops_the_pending_flow`; `tests/test_user_oauth_routes.py::test_a_new_start_replaces_the_pending_flow`, `::test_a_pending_flow_expires_after_ten_minutes` |
| U10 | `config.py::Settings.redirect_uri` (from `HOST_PORT`); used by `authorize_url` and the exchange in `service.py::UserOAuthService._exchange_and_store` | `tests/test_user_oauth_config.py::test_redirect_uri_and_sign_in_url_derive_from_host_port`; `tests/test_user_oauth_routes.py::test_a_full_sign_in_stores_the_token_0600_and_reports_connected` (redirect_uri checked on the wire) |
| U11 | `oauth.py::PendingStore.claim` (no flow, expired, state mismatch; always consumes); `service.py::UserOAuthService.complete_sign_in` (error, iss, code) | `tests/test_user_oauth_routes.py::test_a_refused_callback_sends_nothing_and_shows_the_fixed_page` (six refusal cases, no Discord request), `::test_a_callback_without_any_pending_flow_is_refused`, `::test_a_second_callback_with_the_same_state_is_refused`; `tests/test_user_oauth_units.py::test_claim_refuses_a_missing_or_wrong_state_and_consumes_the_flow`, `::test_claim_without_a_flow_is_no_flow` |
| U12 | `service.py::UserOAuthService._exchange_and_store` (Basic auth, `code_verifier` when PKCE, scope check before `/users/@me`); `oauth.py::build_token` (fields and `token_type`) | `tests/test_user_oauth_routes.py::test_a_full_sign_in_stores_the_token_0600_and_reports_connected`, `::test_without_pkce_the_exchange_has_no_verifier`, `::test_a_failed_exchange_stores_nothing_and_records_exchange_failed[missing_scope]`; `tests/test_user_oauth_units.py::test_build_token_rejects_every_malformed_response` |
| U13 (A3.1) | `service.py::UserOAuthService._exchange_and_store` (any failure after the exchange revokes the new grant once via `_revoke_value`; a malformed response is revoked by whichever token it carries via `_revoke_raw_body`; failure stores nothing) | `tests/test_user_oauth_routes.py::test_every_failure_after_the_exchange_revokes_the_new_grant` (4 cases), `::test_a_malformed_token_response_revokes_the_access_token_it_carries`, `::test_a_refused_exchange_grants_nothing_so_nothing_is_revoked`, `::test_a_failed_exchange_stores_nothing_and_records_exchange_failed[identity_unavailable]`, `[bad_identity]` |
| U14 | `server.py::_oauth_handlers` (callback), `SIGNED_IN_PAGE`, `SIGN_IN_FAILED_PAGE`, `PAGE_HEADERS` (no-store, no-referrer, `default-src 'none'`); `service.py::UserOAuthService._exchange_and_store` replaces the previous grant **without revoking it** (A3.1 U14, D29) | `tests/test_user_oauth_routes.py::test_a_full_sign_in_stores_the_token_0600_and_reports_connected` (page text, no echo), `::test_reconnecting_keeps_the_previous_grant_and_replaces_the_token`, `::test_a_refused_callback_sends_nothing_and_shows_the_fixed_page` (headers) |
| U15 | `security.py::BearerAuthMiddleware` (`open_paths`, GET only); `server.py::create_app` (exemption only when on; routes registered `methods=["GET"]`) | `tests/test_user_oauth_routes.py::test_the_sign_in_routes_accept_only_get`, `::test_post_to_a_sign_in_route_without_a_bearer_is_401_and_with_one_405` (A3.1), `::test_a_foreign_host_or_origin_is_still_refused_on_the_routes`, `::test_with_the_switch_off_the_routes_do_not_exist` |
| U16 | `service.py::UserOAuthService.status`; `models.py::AccountStatusResult`; `server.py::_register_user_tools` (`discord_account_status`) | `tests/test_user_oauth_tools.py::test_status_when_not_connected_has_no_account_fields`, `::test_status_when_connected_reports_the_account_without_tokens` |
| U17 | `service.py::UserOAuthService.connect`; `server.py::_url_elicitation_requested` (2026-07-28 and `elicitation.url`, kill switch), `connect_discord_account` returns `InputRequiredResult` with `elicitation/create` url mode | `tests/test_user_oauth_tools.py::test_connect_when_not_connected_returns_the_plain_result_and_sends_nothing`, `::test_connect_when_connected_says_so_unless_reconnect_is_true`, `::test_a_2026_client_with_url_elicitation_gets_the_sign_in_elicitation`, `::test_the_elicitation_kill_switch_returns_the_plain_result`, `::test_a_2025_client_gets_the_plain_result_even_with_url_capability`, `::test_a_2026_client_without_url_capability_gets_the_plain_result`, `::test_a_connected_request_is_not_elicited_without_reconnect` |
| U18 | `service.py::UserOAuthService.disconnect` (under the refresh lock, m6), `_revoke_value` (best effort), `oauth.py::UserAuth.forget` (memory first) | `tests/test_user_oauth_tools.py::test_disconnect_when_not_connected_is_a_no_op`, `::test_disconnect_revokes_then_deletes_the_token_file`, `::test_a_failed_revoke_still_deletes_and_records_revoke_failed`, `::test_an_invalid_token_file_is_removed_by_disconnect`, `::test_a_failed_token_file_delete_on_disconnect_is_unavailable_and_names_the_path` (A3.2) |
| U19 | `oauth.py::TokenStore.save` (`os.open` O_EXCL 0600 temp, fsync, `os.replace`), `TokenStore.load`, `TokenStore.delete`, `UserAuth.load` (invalid file by reason; missing folder warning) | `tests/test_user_oauth_units.py::test_token_store_round_trips_and_writes_mode_0600`, `::test_invalid_token_files_are_reported_by_reason`, `::test_a_token_file_with_bad_types_or_empty_tokens_is_invalid`, `::test_an_unreadable_token_path_is_invalid`, `::test_saving_into_a_missing_directory_is_unavailable`, `::test_load_marks_an_invalid_file_and_logs_only_the_reason`, `::test_a_missing_token_folder_is_a_boot_warning_not_a_crash` |
| U20 | `service.py::UserOAuthService.list_my_guilds`, `_my_guild_pages` (200 per page, 5-page cap, `with_counts` only when asked), `_my_guild` (derived flags); `service.py::DiscordService.bot_guild_ids` and `_guild_pages` (shared bot cache with `list_guilds`) | `tests/test_user_oauth_tools.py::test_list_my_guilds_without_a_connected_account_is_auth_with_the_hint`, `::test_list_my_guilds_derives_the_flags_for_each_guild`, `::test_list_my_guilds_with_counts_adds_the_approximate_counts`, `::test_a_bot_guild_fetch_failure_leaves_bot_present_unknown`, `::test_list_my_guilds_pages_with_the_last_id_and_is_not_truncated`, `::test_five_full_pages_are_truncated_at_one_thousand`, `::test_list_my_guilds_is_not_limited_by_the_guild_allowlist` |
| U21 | `oauth.py::invite_url` (exact parameter order); `service.py::UserOAuthService.get_guild_invite_link` (snowflake check, no request) | `tests/test_user_oauth_tools.py::test_the_invite_link_is_exact_and_makes_no_request`, `::test_the_invite_link_rejects_a_bad_snowflake`; `tests/test_user_oauth_units.py::test_invite_url_is_exact_and_makes_no_request` |
| U22 | `discord_client.py::oauth_urls` (two fixed URLs); `DiscordClient.post_form` (the only POST; form body, Basic auth, no retry, 429 wait only); `DiscordClient._send` (method, form, auth) | `tests/test_user_oauth_client.py::test_only_the_two_oauth_urls_can_receive_a_post`, `::test_post_form_sends_form_encoded_basic_auth_and_no_bearer`, `::test_every_post_targets_only_the_oauth_urls`, `::test_a_post_5xx_is_unavailable_and_never_replayed`, `::test_a_bot_client_cannot_post`, `::test_a_429_on_post_is_waited_out_and_replayed_once` |
| U23 | `discord_client.py::DiscordClient.__init__` (user instance: no bot header, own `RateLimiter`); `_bearer`, `_request_headers`; `_finish` (user 401 never trips the bot hard stop) | `tests/test_user_oauth_client.py::test_user_get_sends_only_the_bearer_token`, `::test_a_bot_hard_stop_never_blocks_the_user_side`, `::test_a_user_401_refreshes_once_and_replays` (`auth_failed` stays False) |
| U24 | `discord_client.py::DiscordClient.get` (one refresh and one replay on user 401; explicit token never refreshed); `post_form` (`invalid_grant` 400 to `auth`, other 4xx to `upstream`, 5xx and transport to `unavailable`, only the OAuth error code read); `_finish` (user 401 to `auth` with the connect hint) | `tests/test_user_oauth_client.py::test_a_user_401_refreshes_once_and_replays`, `::test_a_user_401_after_the_refresh_is_auth_with_the_connect_hint`, `::test_a_user_403_is_permission_not_auth`, `::test_without_a_connected_account_a_user_get_is_auth_and_sends_nothing`, `::test_an_explicit_token_is_used_as_is_and_never_refreshed`, `::test_invalid_grant_is_auth_and_the_description_is_never_read`, `::test_other_4xx_on_the_oauth_endpoint_is_upstream`, `::test_a_transport_failure_on_post_is_unavailable_without_retry`, `::test_a_429_that_cannot_be_replayed_is_rate_limited` |
| U25 | `oauth.py::UserAuth.access_token` (near-expiry check, one `asyncio.Lock`, re-check after waiting, stale-token reuse), `_refresh_locked` (`invalid_grant` clears; outage keeps), `install` (persist before use) | `tests/test_user_oauth_units.py::test_a_token_near_expiry_is_refreshed_once_under_concurrency`, `::test_a_stale_token_already_replaced_is_reused`, `::test_a_stale_token_still_current_is_refreshed`, `::test_a_rejected_refresh_clears_the_token_and_records_it`, `::test_a_refresh_outage_keeps_the_stored_token`, `::test_an_upstream_refresh_error_is_not_treated_as_rejection`, `::test_a_refresh_that_cannot_be_persisted_is_unavailable` |
| U26 | `oauth.py` `register_secret` calls (PKCE verifier and state in `PendingStore.start`; tokens in `build_token` and `_from_file`; code in `complete_sign_in`); `server.py::create_app` (client secret at boot) | `tests/test_user_oauth_routes.py::test_no_oauth_secret_appears_in_any_log_record_during_a_flow` |
| U27 | `server.py::healthz` unchanged | `tests/test_user_oauth_routes.py::test_healthz_is_identical_with_the_switch_on_and_off` |
| U28 | `server.py::_boot_summary` (`user_oauth=` field); `capabilities.py::ToolPlan.user_oauth` | `tests/test_user_oauth_routes.py::test_the_boot_line_reports_the_user_oauth_state` (five cases, no ids); `tests/test_user_oauth_config.py::test_plan_reports_the_account_state_in_the_boot_line` |
| U29 | Fixed error and page texts; results carry only the human's own account fields (`models.py::AccountStatusResult`, `MyGuild`) | `tests/test_user_oauth_client.py::test_invalid_grant_is_auth_and_the_description_is_never_read`; `tests/test_user_oauth_routes.py::test_a_refused_callback_sends_nothing_and_shows_the_fixed_page`; `tests/test_user_oauth_tools.py::test_status_when_connected_reports_the_account_without_tokens` |
| U30 | `docker-compose.oauth.yml` (adds `./data:/data` and `DISCORD_OAUTH_TOKEN_FILE`); `scripts/lib/common.sh::user_oauth_enabled`, `compose` (adds both files when `DISCORD_USER_OAUTH=true`, value compared, never printed); `docker-compose.yml` unchanged | Not a pytest test (shell and compose). Verified with `docker compose config -q` on the base and override files (see command log) and `bash -n` |
| U31 | `Dockerfile` (`RUN mkdir -p /data && chown 10001:10001 /data`); `.gitignore` (`data/`); `scripts/setup.sh` (creates `./data` when enabled) | Inspection; `docker compose config` shows the mount only with the override |
| U32 | `.env.example` (feature documented, placeholders only, commented out); `scripts/setup.sh` (optional prompt, hidden client secret, application id checked) | `bash -n scripts/setup.sh` only. The interactive prompt was not run (needs a terminal) |
| U33 | `scripts/smoke.sh` (when on: five account tools present, `len(tools) <= 19`, `discord_account_status` called; no sign-in) | `bash -n scripts/smoke.sh` only. Not run live (needs the container and live Discord) |
| U34 | `README.md` section 4: the five tools table and "User OAuth guild discovery (optional)" (what it can and cannot do, portal setup, enable, sign-in, token file, lifetime, kill switches, routes) | Documentation |
| U35 | `docs/SPEC.md` §18 (Amendment A3); `CHANGELOG.md` `[Unreleased]` (Added); `CLAUDE.md` (architecture, amended invariants, five tools); root `CLAUDE.md` table cell; root `CHANGELOG.md` entry | Documentation |
| AC-U1 | Gate: `ruff format --check`, `ruff check`, `mypy`, `pytest` at the 90% branch gate | See the command log: 622 passed, 96.61% |
| AC-U2 | Existing 454 tests unmodified and passing; off-mode tool list equals the 14 | `tests/test_server.py::test_registered_tools_are_exact_names_in_fixed_order` (unchanged) and `tests/test_user_oauth_tools.py::test_the_switch_off_keeps_exactly_the_fourteen_tools` |

## Deviations

**None against the approved requirements.** Points where the implementation adds behaviour the spec does not forbid, all recorded
here for review:

1. **Revoke on a failed connect (now spec, A3.1 U13).** Any failure after a successful exchange revokes the new grant best-effort.
   Originally this went beyond U13; the human approved it in A3.1, so it is no longer a deviation.
2. **Empty-body revoke.** Discord answers a successful revoke with an empty body. `post_form("revoke")` treats any 2xx as success
   and returns `{}`. The spec is silent on the body; the change is needed for U18 to report `revoked: true`.
3. **Shared guild cache refactor.** `list_guilds` now collects the raw bot guild pages through `DiscordService._guild_pages`, the same
   code `bot_present` uses. The output and the cache keys are unchanged; all 454 existing tests pass without modification.
4. **Smoke tool-count check (U33).** Implemented as "at most 19 tools, and the five account tools present", which covers "19 minus
   intent omissions".
5. **`disconnect` clears the pending flow on every path** (the spec says "also clears any pending flow" in U18).
6. **Operator-visible boot line** keeps the existing fields and appends `user_oauth=...`; the registered list includes the account
   tools only when they are registered.

## Spec issues

None found that block the implementation. Notes for the human:

- The file-mode check (0600) is verified on the WSL native temp filesystem. `/mnt/c` (drvfs) does not enforce modes, as documented
  for the Gmail token.
- Token-file ownership on `./data` (bind mount, written as UID 10001) depends on the drvfs mount options. This is not testable here;
  AC-L1 (live) covers it.

## Command log

All commands run in WSL with the venv `~/.venvs/discord-mcp` (Python 3.12, `mcp` 2.3.0), from the discord server directory.

1. `pytest tests/test_oauth_sdk_gate.py --no-cov` (first run): 3 failed. Cause: the test client's `Host` header was `testserver`, which the
   DNS-rebinding guard refuses. Fixed by `BASE = "http://127.0.0.1:8769"`. Re-run: **3 passed**. The SDK facts hold, so the gate passed.
2. Baseline before any server change: `ruff check .` passed; `mypy` passed (34 files); `pytest` **457 passed**, coverage 96.35%
   (457 = 454 existing + the 3 gate tests).
3. After the source changes, before the new tests: `pytest` **457 passed**; coverage fell to 79.8% (new code not yet tested). Recorded
   as an interim state.
4. Full run after the tests were added (superseded): `ruff format .`, `ruff check .`: passed; `mypy`: passed (41 files); `pytest`: **594 passed**,
   coverage **96.50%**, gate 90% reached. Superseded by the fix round: see item 9 below.
5. `bash -n` on `scripts/*.sh` and `scripts/lib/*.sh`: all ok. `grep` for CR: none.
6. `docker compose -f docker-compose.yml config -q` and `-f docker-compose.yml -f docker-compose.oauth.yml config -q`: both valid, run in a
   temporary copy with an empty `.env` (never the repository's `.env`), which was deleted afterwards. The override shows
   `DISCORD_OAUTH_TOKEN_FILE=/data/discord_user_token.json` and a bind mount `./data` to `/data`.
7. Not run: `make audit` (no dependency changed); `docker build` (no image change to verify beyond `compose config`); live smoke
   `scripts/smoke.sh` and AC-L1 (need the container and a live Discord account); the interactive `setup.sh` prompt (needs a terminal).

## Files changed

Source: `src/discord_mcp/oauth.py` (new), `config.py`, `discord_client.py`, `service.py`, `models.py`, `capabilities.py`,
`security.py`, `server.py`.

Tests (new): `tests/test_oauth_scripts.py` (fix round), `tests/test_oauth_sdk_gate.py`, `tests/test_user_oauth_config.py`, `tests/test_user_oauth_units.py`,
`tests/test_user_oauth_client.py`, `tests/test_user_oauth_routes.py`, `tests/test_user_oauth_tools.py`, `tests/user_oauth_fakes.py`.
No existing test file was modified.

Container and scripts: `docker-compose.oauth.yml` (new), `Dockerfile`, `.gitignore`, `.env.example`, `scripts/lib/common.sh`,
`scripts/setup.sh`, `scripts/smoke.sh`.

Docs: `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `docs/SPEC.md` (§18); root `../CLAUDE.md` (one table cell) and `../CHANGELOG.md`
(one entry).

## Fix round 1 (A3.1 and review minors)

Changes after review round 1 are recorded in `fix-round-1.md`. Final gate after the fix round: **622 passed, coverage 96.61% (after fix round 2)** (branch), ruff and mypy clean; compose config valid for both file sets.
Items 9 onward in the command log below describe that run.

9. Fix round gate: `ruff format --check .`, `ruff check .`, `mypy`: passed. `pytest`: **617 passed**, coverage 96.59%.
   `bash -n` on all scripts passed. `docker compose -f docker-compose.yml config -q` and the two-file form passed on a temporary
   copy with an empty `.env` (the repository `.env` was not read).
