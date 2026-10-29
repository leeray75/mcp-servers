# Fix round 1: discord-mcp-server initial build

- **Inputs:** `review-round-1.md` (verdict CHANGES REQUESTED: M1, m1-m11, spec issues S1-S6), `../../SPEC.md` section 16
  (amendment A1, approved 2026-10-29; A1 wins over sections 1-15), `external-review-triage.md` (F1-F11, verified).
- **Result:** every finding fixed or accepted with justification; every A1 item implemented. No rebuttals were needed.
  A1 is consistent with itself and with the review; no further spec issue was found. The gate passes.
- **Scope:** only `discord-mcp-server/` and the root docs listed in A1 (root `CLAUDE.md`, `README.md`, `CHANGELOG.md`).
  No sibling server changed. Nothing committed. No `.env` read or created.

## Gate results (final)

| Command | Result |
|---|---|
| `make check VENV=~/.venvs/discord-mcp` | `ruff check` and `ruff format --check` clean (42 files); `mypy --strict` clean (32 files); pytest **414 passed**; total branch coverage **96.25%** (gate 90%) |
| `make audit VENV=~/.venvs/discord-mcp` | No known vulnerabilities found |
| `make lock` | Not run: no dependency changed (`requirements*.txt` untouched) |
| Mutation check (M1) | Disabling the `finally` lease release made both M1 tests fail with `TimeoutError`; restored, both pass |
| `tests/test_sizes.py -s` | 14 of 14 tools under 4 MiB structured; measured figures in the README and implementation report |

Before this round the gate was 282 passed at 96.22%; the change adds 132 tests (`test_fix_round1.py`: 108, `test_sizes.py`: 14,
per-tool DM cases: 10). Counts: `pytest --co -q` at the time of the final run.

## Review findings

| ID | Finding | Status | Where (file:line) | Test(s) |
|---|---|---|---|---|
| M1 | Lease leaked on cancellation / non-transport request errors; `httpx.RequestError` unmapped | **Fixed** | `discord_client.py:301` (`finally: lease.release()`, idempotent); `:268-273` (`except httpx.RequestError` -> `kind=upstream`); `:246` (`try:` wraps the request) | `test_fix_round1.py::test_cancelled_in_flight_request_does_not_wedge_its_bucket`, `::test_decoding_error_is_upstream_and_the_next_call_works`; mutation-verified |
| m1 | Search `channel_ids` did up to 500 sequential lookups | **Fixed** (A1-6, A1-7.7) | `service.py:68` (`SEARCH_MAX_CHANNELS = 25`); `service.py:511-521` (`_check_search_channels`, `asyncio.gather` with `return_exceptions`, first failure in `channel_ids` order) | `test_fix_round1.py::test_search_channel_ids_are_capped_at_25`, `::test_search_looks_up_every_channel_and_sends_negated_has` |
| m2 | Channel-mismatch message differed from R13 | **Fixed** (A1-R13) | `service.py:508`, `:704` (identical message); `guard.py:20` (`CHANNEL_REFUSAL`) | `test_fix_round1.py::test_every_channel_lookup_failure_gets_one_identical_refusal`; `test_server.py::test_dm_and_unknown_channels_are_refused_identically_for_every_channel_tool[*]` |
| m3 | `Lease.learn` overwrote a live bucket state | **Fixed** | `ratelimit.py:61-69` (`setdefault`: the first learned state wins; `blocked_until` merged with max) | `test_fix_round1.py::test_shared_bucket_keeps_the_first_learned_state` |
| m4 | Non-finite / negative `retry_after` accepted | **Fixed** | `discord_client.py:98-114` (`_float_or_none` rejects non-finite and negative; falls back to the default) | `test_fix_round1.py::test_non_finite_or_negative_retry_after_is_treated_as_absent[*]`, `::test_non_finite_float_is_treated_as_absent` |
| m5 | Malformed 2xx bodies became internal errors | **Fixed** | `service.py:143-150` (`_as_list`, `_as_dict`) used in every list path; `project.py:137-141` (`_require_object`; non-object or no id -> `upstream`) | `test_fix_round1.py::test_list_body_of_the_wrong_shape_is_upstream`, `::test_projection_of_a_non_object_is_upstream` |
| m6 | `get_audit_log` could not send `after="0"`; dead ISO path | **Fixed** (A1-7.14) | `service.py:793` (`action_type >= 1`); `service.py:805-812` (`after="0"` literal, else snowflake or ISO); `server.py` (`get_audit_log` schema, `before`/`after` strings, `action_type` ge 1, no upper bound) | `test_fix_round1.py::test_audit_after_accepts_zero_iso_and_any_positive_action_type` |
| m7 | Channel carried tool-specific fields as null | **Fixed** (A1-5, A1 projections) | `models.py:78-93` (per-tool fields optional); `project.py:268-292` (`channel()` sets only applicable keys); `server.py:214` and `service.py:131` (`exclude_unset`) | `test_fix_round1.py::test_inapplicable_channel_keys_are_omitted_not_null` |
| m8 | `type_id` was a `-1` sentinel | **Fixed** | `models.py:81` (`type_id: int \| None`); `project.py:272-273` (no sentinel; `type` is `"unknown"`) | `test_fix_round1.py::test_type_id_is_null_when_discord_omits_the_type` |
| m9 | Children of equal-position categories interleaved | **Fixed** | `service.py:161-177` (`_sort_channels`: key `(parent position, parent id, kind, position, id)`) | `test_fix_round1.py::test_children_group_under_their_own_parent_when_categories_share_a_position` |
| m10(a) | No test: bucket stays blocked after a 429 beyond the limit, and after a shared 429 | **Fixed** | (tests only) | `test_fix_round1.py::test_bucket_stays_blocked_for_the_full_duration_on_later_calls`, `::test_shared_429_bucket_stays_blocked_and_later_calls_are_refused_locally` |
| m10(b) | Guard test vacuous; DM not per channel tool | **Fixed** | `test_server.py` (`CHANNEL_TOOLS`; the guard loop asserts the exact request list: none for guild tools, one channel lookup for channel tools) | `test_server.py::test_non_allowlisted_scope_is_refused_with_no_guild_request[*]`; `::test_dm_and_unknown_channels_are_refused_identically_for_every_channel_tool[*]` (5 tools x 2 channels) |
| m10(c) | No cancellation test | **Fixed** (see M1) | `discord_client.py:301` | `test_fix_round1.py::test_cancelled_in_flight_request_does_not_wedge_its_bucket` |
| m10(d) | No key-set test for Role, Attachment, Embed, Reaction, Poll, ThreadInfo | **Fixed** | (tests only) | `test_fix_round1.py::test_projection_key_sets_match_section_8` |
| m11 | Root docs (`four` servers, lineage, coverage line, diagram, changelog) | **Fixed** (A1 docs) | root `CLAUDE.md` (line 15 "five", line 32 lineage with discord, line 42 coverage "sports and discord are 90%"); root `README.md` (diagram row `127.0.0.1:8769 -> discord-mcp -> Discord`, server table row present); root `CHANGELOG.md` (server link and `[Unreleased]` entry) | Reviewer check |

Accepted with justification (review "also noted", not in m1-m11):

- **Poll `votes: null` when an answer is absent from `answer_counts` although `results` is present.** Accepted. The
  payload does not state a count for that answer, so the projection does not assert `0`; `project.py:_poll` keeps null.
  Cosmetic, as the reviewer also judged.

## A1 items (SPEC section 16)

| A1 item | Status | Where (file:line) | Test(s) |
|---|---|---|---|
| A1-1 / S1 (R15 wording) | Fixed | `test_server.py` `CHANNEL_TOOLS` and exact request lists | `test_server.py::test_non_allowlisted_scope_is_refused_with_no_guild_request[*]` |
| A1-2 / S2 (R5 after a 401) | Fixed | `capabilities.py:84-115` (`plan_tools`: auth registers every non-config-disabled tool) | `test_permissions_caps_project.py::test_plan_auth_keeps_every_tool_so_each_returns_kind_auth`; `test_server.py::test_boot_auth_keeps_every_tool_and_each_returns_kind_auth` |
| A1-3 / S3 (guard leaks existence) | Fixed | `guard.py:40-52` (every lookup failure except auth, rate_limited, unavailable -> `forbidden_guild`, one message) | `test_fix_round1.py::test_every_channel_lookup_failure_gets_one_identical_refusal`, `::test_auth_and_rate_limit_still_surface_from_the_channel_lookup` |
| A1-4 / S4 (audit `after="0"`) | Fixed | `service.py:805-812`; `server.py` `get_audit_log` | `test_fix_round1.py::test_audit_after_accepts_zero_iso_and_any_positive_action_type` |
| A1-5 / S5 (Channel fields) | Fixed | `models.py:78-93`; `project.py:268-292`; `server.py:214`; `service.py:131` | `test_fix_round1.py::test_inapplicable_channel_keys_are_omitted_not_null` |
| A1-6 / S6 (fan-out) | Fixed | `service.py:68`, `:511-521` | `test_fix_round1.py::test_search_channel_ids_are_capped_at_25` |
| A1-R5 | Fixed | `capabilities.py:84-115` | (as A1-2) |
| A1-R6 (flags, `flags_new`, force flags) | Fixed | `discord_client.py:134-143` (`_application_flags`); `discord_client.py:440-465` (`probe` keeps `ok` with unknown flags); `capabilities.py:46-55` (force flags), `:84-115` (`flags_missing`, `forced`); `config.py:70-71` (defaults false) | `test_fix_round1.py::test_flags_new_is_used_when_flags_is_absent`, `::test_no_usable_flags_is_ok_with_unknown_flags`, `::test_plan_with_flags_missing_and_force_flags[*]`; `test_permissions_caps_project.py::test_intent_flags_matrix`; `test_server.py::test_config_force_disable_removes_intent_tools` |
| A1-R7 (reasons, `forced`, flags in boot line) | Fixed | `server.py:531-541` (`_boot_summary`) | `test_fix_round1.py::test_boot_line_records_forced_tools_and_observed_flags`; `test_server.py::test_boot_log_lists_registered_and_omitted_without_ids` |
| A1-R13 | Fixed | `guard.py:20-52` | (as A1-3) |
| A1-R15 | Fixed | `test_server.py` `GUARD_CASES`, `CHANNEL_TOOLS` | `test_server.py::test_guard_cases_cover_every_registered_tool`; `test_list_guilds_has_no_id_and_never_returns_other_guilds` |
| A1-R16 (4 MiB default, hint) | Fixed | `config.py:75` (`4_194_304`); `discord_client.py:` `SIZE_CAP_HINT`, `:251-255` | `test_fix_round1.py::test_default_response_cap_is_four_mebibytes`, `::test_oversized_response_hint_asks_for_a_smaller_limit` |
| A1-R43 (worst-case size test, recorded sizes) | Fixed | `tests/test_sizes.py` (14 tools at max limit); `server.py` `_ok_result` (exclude_unset) | `test_sizes.py::test_worst_case_projection_is_under_four_mebibytes[*]` (14 cases); sizes in README "Result size" and below |
| A1-7.3 (no `obfuscated`) | Fixed | `models.py` (field removed); `project.py` (flag removed); `server.py` `list_channels` description | `test_fix_round1.py::test_list_channels_has_no_obfuscated_field` |
| A1-7.7 (channel_ids 25, concurrent, `-` negation, reactions null, description) | Fixed | `server.py` schema (`max_length=25`, `HAS_PATTERN`) and description; `service.py:68`, `:511-521`, `:554-562` (`has` with leading `-`); `project.py:385` (`reactions=None if search`) | `test_fix_round1.py::test_search_looks_up_every_channel_and_sends_negated_has`, `::test_search_results_carry_null_reactions`, `::test_search_channel_ids_are_capped_at_25` |
| A1-7.13 (search_members hint) | Fixed | `service.py:753-775` (`permission` -> hint `HINT_GUILD_MEMBERS`, line 83); `server.py` description | `test_fix_round1.py::test_search_members_forbidden_hints_at_the_guild_members_intent` |
| A1-7.14 (audit before/after, `"0"`, action_type >= 1) | Fixed | `service.py:793`, `:805-812`; `server.py` `get_audit_log` | `test_fix_round1.py::test_audit_after_accepts_zero_iso_and_any_positive_action_type` |
| A1 projection: Channel | Fixed | `models.py:78-93`; `project.py:268-292` | `test_fix_round1.py::test_inapplicable_channel_keys_are_omitted_not_null` |
| A1 projection: Message `reactions` null for search | Fixed | `project.py:385`; `models.py:148` | `test_fix_round1.py::test_search_results_carry_null_reactions` |
| A1 projection: AuditEntry.action table (69 events, 1-193) | Fixed | `project.py:56-126` (`AUDIT_ACTIONS`, source comment with URL and fetch date 2026-10-29, transcribed from a WebFetch of the page); unknown -> null | `test_fix_round1.py::test_audit_table_has_every_documented_event_and_nothing_else`, `::test_each_audit_event_name_is_projected[69 cases]`, `::test_unknown_audit_value_maps_to_null`, `::test_source_urls_are_recorded_in_the_projection_module` |
| A1 projection: channel type source comment | Fixed | `project.py:36` (URL comment, verified 2026-10-28 per A1) | `test_fix_round1.py::test_source_urls_are_recorded_in_the_projection_module` |
| A1 docs changes | Fixed | root `CLAUDE.md`, `README.md`, `CHANGELOG.md`; server `README.md`, `CLAUDE.md`, `.env.example`, `CHANGELOG.md` | Reviewer check |

## Notes for the reviewer

1. **Audit table provenance.** The 69 rows were taken from a WebFetch of
   `https://docs.discord.com/developers/resources/audit-log` on 2026-10-29 (the date of this round, not 2026-10-28 as A1's text
   says). The fetch returned exactly 69 rows with values 1-193, and the values and names match the triage's F11 claims
   (146 is `AUTO_MODERATION_QUARANTINE_USER`; 147 absent; 150/151 monetization; 130-132, 163-167 and 190-193 present). The
   test embeds the same 69 rows. The source comment says 2026-10-29. Please confirm against the page if the fetch date matters.
2. **Worst-case sizes exceed 4 MiB on the wire.** A1-R43 asserts the structured projection, which is under 4 MiB for every tool
   (largest: `list_messages` 2.83 MB). R11's duplicate text block doubles the total, so `list_messages` at 100 messages is 5.66 MB
   and `get_audit_log` at 100 entries is 4.54 MB. The README states both figures. This is a documented consequence of R11 and
   A1-R43 as written, not a fix-round change. Raise it with the human if clients cannot take 5 MB responses.
3. **Fixture realism.** The first worst-case `list_messages` run exceeded the 4 MiB upstream cap because the fixture gave one
   message about 20 KB of embed text, more than Discord allows (6,000 characters of embed text per message). The fixture was
   corrected to one embed with three fields (about 5.6 KB). This was a test-data fix, not a server change.
4. **Mutation check.** M1's tests fail when the `finally` release is removed (verified with `sed`, then the file was restored
   from a backup; the restored file has no mutation marker).
5. **Not live-verified.** The `search_members` GUILD_MEMBERS hint and the real `/applications/@me` `flags_new` body are only
   covered by fixtures (A1 open item, AC5).

## Commands run

| Command | Result |
|---|---|
| WebFetch `https://docs.discord.com/developers/resources/audit-log` (prompt: list every Audit Log Events row) | 69 rows, values 1-193 |
| `ruff check --fix` / `ruff format` (several iterations) | Final: clean |
| `make check VENV=~/.venvs/discord-mcp` (final) | exit 0; 414 passed; coverage 96.25% |
| `make audit VENV=~/.venvs/discord-mcp` (final) | exit 0; no known vulnerabilities |
| `pytest -q --no-cov tests/test_fix_round1.py -k "cancelled or decoding"` (mutation: `finally` release disabled) | 2 failed (`TimeoutError`) |
| same test after restoring `discord_client.py` from backup | 2 passed |
| `pytest -q -s --no-cov tests/test_sizes.py` | 14 passed; sizes printed (recorded in README and implementation report) |
| `docker build` / offline container run | Not repeated this round (no Dockerfile or runtime change; last run recorded in the implementation report) |
