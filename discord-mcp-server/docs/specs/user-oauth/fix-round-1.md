# Fix round 1: user OAuth guild discovery (A3)

- **Inputs:** `SPEC.md` section 11 (amendment A3.1, approved by the human, 2026-10-29: D29-D32 and the replaced U3, U13,
  U14, U15, U33, plus AC-L1) and `review-round-1.md` (APPROVED, minors m1-m11, spec issues S1-S4).
- **Scope:** A3.1 exactly, then every minor fixed or accepted with a reason. Nothing outside those items was changed.
  No commit. Paths are relative to `discord-mcp-server/`.

## A3.1 changes

| Item | Change | Where | Tests |
|---|---|---|---|
| **D31 / U3** new settings ignored when off | Wrap validator on `host_port`, `discord_oauth_url_elicitation`, `discord_oauth_pkce`. A malformed value falls back to its default when the switch is off and is re-raised when it is on. Defaults in `_OAUTH_DEFAULTS` | `src/discord_mcp/config.py:179` (`_oauth_ignored_when_off`), `:~60` (`_OAUTH_DEFAULTS`) | `tests/test_user_oauth_config.py::test_malformed_a3_settings_do_not_stop_an_off_mode_boot`, `::test_the_same_malformed_values_are_refused_when_the_switch_is_on`, `::test_host_port_is_range_checked[0\|65536]` (now with the switch on) |
| **D30 / U13** revoke on every post-exchange failure | Every failure after a successful exchange (scope, `/users/@me`, profile, install) revokes the new grant once, best-effort, and stores nothing. A malformed response is revoked by whichever token it carries (refresh, else access). A revoke failure is ignored; the outcome is `last_error=exchange_failed` | `src/discord_mcp/service.py:~1095-1132` (`_exchange_and_store`, `_revoke_raw_body`, `_revoke_value`) | `tests/test_user_oauth_routes.py::test_every_failure_after_the_exchange_revokes_the_new_grant` (4 cases), `::test_a_malformed_token_response_revokes_the_access_token_it_carries`, `::test_a_refused_exchange_grants_nothing_so_nothing_is_revoked` |
| **D29 / U14** no revoke on reconnect | Removed the revoke of the previous grant. The new token replaces the old one in the store. The README says that old grants can be removed under Discord User Settings → Authorized Apps and that AC-L1 records Discord's behaviour | `src/discord_mcp/service.py:~1105` (comment: never revoked on reconnect), README "Token file" and "Sign-in" sections | `tests/test_user_oauth_routes.py::test_reconnecting_keeps_the_previous_grant_and_replaces_the_token` (asserts `REF-1` is **not** revoked) |
| **D32 / U15** 401 vs 405 | Documented and tested. The bearer middleware runs first: a non-GET without a bearer gets 401; with a bearer it gets 405. README corrected | `src/discord_mcp/security.py` (unchanged behaviour), README "Routes" paragraph | `tests/test_user_oauth_routes.py::test_post_to_a_sign_in_route_without_a_bearer_is_401_and_with_one_405`, `::test_the_sign_in_routes_accept_only_get` |
| **D32 / U33** smoke tool count | `scripts/smoke.sh` already checks "at most 19 and all five account tools present" (the A3.1 wording). The implementation report now says so | `scripts/smoke.sh` (user OAuth block) | `bash -n` only; live run not done (needs the container) |
| **AC-L1 (added)** | Live check: reconnect once with `reconnect=true` and confirm `list_my_guilds` works with the new token. Recorded as the human's live step | README "Token file" note; implementation report | Live, not run here |

## Minors

| # | Disposition | Justification and change | Where | Tests |
|---|---|---|---|---|
| m1 | **Fixed** (A3.1 D31) | See U3 above | `config.py:179` | as above |
| m2 | **Fixed** (A3.1 D30 / S4) | Spec now enumerates the revoking failures (U13). Implementation report corrected (deviation 1 reworded) | `service.py:~1095-1121`; `implementation-report.md` deviation 1 | U13 tests above |
| m3 | **Fixed** (A3.1 D29 / S1) | The previous grant is no longer revoked after the new token exists | `service.py:~1105` | `test_reconnecting_keeps_the_previous_grant_and_replaces_the_token` |
| m4 | **Fixed** | One runner. `_run_any` carries the budget, R30 error mapping, logging and the InputRequiredResult passthrough. `_run` is a typed wrapper (`cast`) for the other tools. `_run_user` is deleted. The connect tool calls `_run_any` | `server.py:438` (`_run_any`), `:482-488` (`_run`), `:354` (connect) | existing tool tests plus `tests/test_user_oauth_tools.py` elicitation tests (unchanged behaviour) |
| m5 | **Fixed** | `TokenStore.delete` maps `OSError` to `DiscordMcpError("unavailable")`. `UserAuth.forget` clears memory **before** deleting, so a failed delete never leaves the token in use. `_refresh_locked` tolerates a failed delete (logged). A failed delete on disconnect is returned as `unavailable` with a hint to delete the file by hand; it is not an internal error. Justification for the non-silent result: the file still holds the token, so the user must be told | `oauth.py:332-343` (delete), `:409-414` (forget), `:~452-462` (refresh path) | `tests/test_user_oauth_units.py::test_a_failed_delete_is_unavailable_and_the_token_leaves_memory` |
| m6 | **Fixed** | `UserAuth.lock` property. `disconnect` runs under the refresh lock: an in-flight refresh finishes first, and no refresh can re-create the file after the delete | `oauth.py:405-407` (`lock`), `service.py:994-1006` (`disconnect`) | `tests/test_user_oauth_units.py::test_disconnect_under_the_refresh_lock_cannot_resurrect_the_file` |
| m7 | **Fixed** | `PendingStore.start` unregisters the replaced flow's state and verifier before registering the new pair, so the redaction set holds at most two pairs. `logging_setup.unregister_secret` added | `oauth.py:131-143`, `logging_setup.py:41-44` | `tests/test_user_oauth_units.py::test_redaction_literals_stay_bounded_under_repeated_flow_starts` |
| m8 | **Fixed** | README: OAuth block moved after the result-size section (it had been wedged between the tools table and "Result size"); 401/405 wording corrected; Authorized Apps note. Implementation report: test counts corrected (160 new, not 137; 617 total), the "nothing outside the spec" claim reworded, deviations and command log updated. Server `CLAUDE.md`: long bullets reflowed | `README.md` section 4; `implementation-report.md`; `CLAUDE.md` | documentation; the count is checked by the gate run |
| m9 | **Fixed** | (a) no-caching test for user results: `list_my_guilds` twice gives two user requests. (b) Compose selection helper tested for real: `common.sh` copied into a temp project, a stub `docker` echoes its argv; covers `true`/`TRUE`/`True`, `false`, missing `.env`. (c) Override file has exactly one `./` mount and the token env var. (d) `.gitignore` contains `data/`. (e) base `docker-compose.yml` has no `volumes:` | `tests/test_user_oauth_tools.py` (end), `tests/test_oauth_scripts.py` | `test_user_guild_results_are_never_cached`; `test_user_oauth_on_adds_both_compose_files[true\|TRUE\|True]`; `test_user_oauth_off_or_missing_uses_the_base_file_only[...]`; `test_the_override_mounts_only_the_token_folder_and_sets_the_token_path`; `test_gitignore_excludes_the_token_folder`; `test_the_base_compose_file_is_unchanged_by_the_feature` |
| m10 | **Fixed** | `setup.sh` no longer runs `chmod 700 ./data` (it could lock UID 10001 out). It prints the requirement and, on native Linux, the `sudo chown 10001:10001 data` command. The token file itself is always 0600 | `scripts/setup.sh:206-214` | `bash -n`; README "Token file" note |
| m11 | **Fixed** | Stray `.<token file>.*.tmp` files are deleted at load and before each save (they hold tokens). README notes it | `oauth.py:279-284` (`_remove_stray_temps`), `:287` (load), `:314` (save) | `tests/test_user_oauth_units.py::test_a_stray_temp_file_is_removed_on_load_and_save` |

## Spec issues (S1-S4)

| # | Resolution |
|---|---|
| S1 (revoke on reconnect) | Decided as D29. Implemented; the live AC-L1 check remains the human's |
| S2 (U15 405 vs bearer) | Decided as D32. Documented and tested |
| S3 (U1 vs U3) | Decided as D31. Implemented: validation applies only when the switch is on |
| S4 (U13 enumeration, U33 "at most 19") | Decided as D30 and D32 |

## Gate (this round)

Run in WSL with `~/.venvs/discord-mcp`, on the discord server directory:

| Step | Result |
|---|---|
| `ruff format --check .` | passed (64 files) |
| `ruff check .` | passed |
| `mypy` (strict) | passed (42 files) |
| `pytest` (branch coverage, 90% gate) | **617 passed**, coverage **96.59%** |
| `bash -n` on `scripts/*.sh` and `scripts/lib/*.sh` | passed |
| `docker compose -f docker-compose.yml config -q` | valid (temporary copy, empty `.env`, `.env` not read) |
| `docker compose -f docker-compose.yml -f docker-compose.oauth.yml config -q` | valid (same temporary copy) |
| Existing test files | unchanged (only the host-port range test was adapted, to run with the switch on, as A3.1 U1 requires; the reconnect test was flipped to D29) |

New tests in this round: 23 (617 total, from 594). Test counts in `implementation-report.md` are corrected to match.

Not run: `make audit` (no dependency change), live `smoke.sh` and AC-L1, the interactive `setup.sh`.
