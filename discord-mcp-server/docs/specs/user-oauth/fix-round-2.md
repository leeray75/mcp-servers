# Fix round 2: user OAuth guild discovery (A3)

- **Inputs:** `SPEC.md` section 12 (amendment A3.2, D33, approved by the human 2026-10-29) and `review-round-2.md`
  (APPROVED; minors n1-n3 and one cosmetic item). Both re-read from disk.
- **Scope:** A3.2 and the review items only. No commit. Paths are relative to `discord-mcp-server/`.
  No spec change was needed: A3.2 already states the U18 sentence, and the n2 and n3 fixes stay within U9, U11, U25
  and U18.

## Items

| Item | Disposition | Change | Where | Tests |
|---|---|---|---|---|
| **n1 / A3.2 (D33)** failed token-file delete on disconnect | **Fixed** (behaviour kept, U18 sentence added) | The `unavailable` hint now names the configured token path and says it still holds the token. Contents are never included. Memory is already cleared and the revoke result still applies (`last_error` set before the delete). README gets the A3.2 sentence | `src/discord_mcp/oauth.py:350-357` (`TokenStore.delete`), `README.md` ("Token file" paragraph) | `tests/test_user_oauth_tools.py::test_a_failed_token_file_delete_on_disconnect_is_unavailable_and_names_the_path` (asserts `kind=unavailable`, the path in the hint, no token values in the hint, status `not_connected`, and `revoked` still performed) |
| **n2** redaction set unbounded via start, callback and expiry | **Fixed** | `PendingStore` now unregisters the flow's state and verifier on replacement (`start`), on refused claims (`claim`), on `clear`, and on `release`. A **successful** claim keeps its secrets registered, because the verifier is still needed for the exchange; `complete_sign_in` calls `release(claimed)` in a `finally`, and also unregisters the authorization code. Nothing in use is unregistered early. The helper `_drop` is shared | `src/discord_mcp/oauth.py:131-176` (`start`, `claim`, `release`, `clear`, `_drop`), `src/discord_mcp/service.py:1039-1073` (`complete_sign_in`, `finally`) | `tests/test_user_oauth_units.py::test_redaction_literals_stay_bounded_across_start_callback_expiry_and_clear` (200 loops of start+callback, start+expiry, start+clear), `::test_a_claimed_verifier_stays_redactable_until_released`, `::test_a_refused_claim_unregisters_its_secrets_at_once` |
| **n3** token install outside the refresh lock | **Fixed** | The sign-in install (`_exchange_and_store`) runs under `UserAuth.lock`. Deadlock check: `complete_sign_in` holds no lock on entry; the locked block contains only `install`, which is a synchronous save plus assignment and takes no other lock; `refresh` holds the lock across its own `install` and never calls the sign-in path, so there is no cycle. A refresh in flight therefore finishes before the new grant is stored | `src/discord_mcp/service.py:1130-1132` (locked `install`) | `tests/test_user_oauth_routes.py::test_the_sign_in_install_waits_for_the_refresh_lock` (the callback reaches `/users/@me`, then waits on a lock the test holds: no token file and no response until release) |
| **Cosmetic** config.py comment | **Fixed** | "HOST_PORT is always bounded" replaced: it is bounded only when the switch is on (A3.1 U3) | `src/discord_mcp/config.py:76-78` | covered by `tests/test_user_oauth_config.py` (off-mode and on-mode tests) |

## Review-round-2 checks that were not minors

- The `unregister_secret` implementation (`logging_setup.py`) remains a plain discard; with n2 fixed, it is only ever
  called on a flow's own state and verifier, or on the authorization code, while those values are not in use.
- A claimed flow whose exchange is in progress keeps its verifier registered; a second `start()` during that exchange
  does not touch it, because the claimed flow is no longer `_flow`.

## Gate (this round)

Run in WSL with `~/.venvs/discord-mcp`, on the discord server directory:

| Step | Result |
|---|---|
| `ruff format --check .` | passed (66 files) |
| `ruff check .` | passed |
| `mypy` (strict) | passed (42 files) |
| `pytest` (branch coverage, 90% gate) | **622 passed**, coverage **96.61%** |
| Scripts and compose files | not changed in this round; no re-run needed. Earlier rounds passed `bash -n` and both compose configs |

New tests this round: 5 (622, from 617). `test_user_oauth_tools.py` and `test_user_oauth_routes.py` gained one test each
for n1 and n3 (both in the file list below); `test_user_oauth_units.py` gained three for n2.

Pre-existing test files: unchanged. `git diff` on `tests/` is empty.

Not run: `make audit` (no dependency change), live smoke and AC-L1, and the interactive `setup.sh` prompt.
