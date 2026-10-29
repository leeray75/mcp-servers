# Review round 2: user OAuth guild discovery (A3 + A3.1)

- **Reviewer:** independent, read-only (only this file written). **Date:** 2026-10-29. `.env` never read.
- **Inputs re-read from disk:** `SPEC.md` (incl. section 11, D29-D32), `fix-round-1.md`, changed code and tests.
- **Verdict: APPROVED** (0 blockers, 0 majors, 3 minors, 0 spec issues). The three minors are small; fix or accept in `fix-round-2.md`.

## Gate (my own run)

`wsl.exe -e bash -lc 'cd ".../discord-mcp-server" && make check VENV=~/.venvs/discord-mcp'`

| Step | Result |
|---|---|
| ruff format/check, mypy --strict | passed |
| pytest | **617 passed** |
| Branch coverage (gate 90%) | **96.59%** (config 100%, logging_setup 100%, oauth 99%, server 98%, service 95%) |
| Pre-existing tests unmodified | `git diff --stat -- tests` is empty; every test file is untracked/new. Confirmed. The "adapted host-port range test" is in the new `test_user_oauth_config.py`, not an old file. |

## A3.1 verification

| Item | Result |
|---|---|
| **U3 (all five settings)** | Verified. `host_port`, `discord_oauth_url_elicitation`, `discord_oauth_pkce` use a wrap validator (`config.py` `_oauth_ignored_when_off`) that falls back to the default when the switch is off and re-raises when on (`discord_user_oauth` is declared first, so it is in `info.data`). The other two cannot be malformed when off: `discord_oauth_client_secret` is `SecretStr \| None` (any string is valid) and `discord_oauth_token_file` is a plain `str`; `_check_user_oauth` returns immediately when off, so neither is checked or read, and no behavior reads them when off (`create_app` builds the user side only when on). The implementer's report naming only three is accurate and complete; the other two need no validator. Tests cover malformed values off (boots) and on (refused). |
| **U13 / D30** | `_exchange_and_store`: a failed exchange revokes nothing (nothing granted); a malformed response revokes the refresh token, else the access token; scope, `/users/@me`, profile and `install` failures all fall in one `try` that revokes the new refresh token once and re-raises. Correct and tested (4 cases + malformed + refused). |
| **U14 / D29** | Previous-grant revoke removed; `install` replaces the token. Test asserts the old refresh token is not revoked. |
| **U15 / D32** | Behavior unchanged (401 without bearer, 405 with); README and test now say so. |
| **U33** | `smoke.sh` check is "at most 19 and five tools present". Matches. |
| **AC-L1** | Added to spec and README as the human's live step; not run here. |

## Fix-round items

- **m4 `_run_any`:** read side by side with the old `_run`: budget check, rate-limited error, `_ok_result`, error mapping, internal-error path and the `finally` log are identical; the only addition is the `InputRequiredResult` passthrough. `_run` is a `cast` wrapper, so the 14 bot tools behave the same (the untouched existing server tests pass). Fine. `cast` would hide a future misuse; acceptable.
- **m5 judgement (failed delete on disconnect):** `forget()` clears memory first, then `delete()` raises `unavailable` with a hint to delete the file by hand; `disconnect` has already revoked. This **conflicts literally** with U18 ("always deletes ... returns `disconnected`") but U18 cannot be honored when the filesystem refuses, and silently reporting `disconnected` while a token file remains would be worse. Classification: **acceptable deviation, minor (n1)**. Recommendation: accept, and add one sentence to U18 / README ("if the file cannot be deleted the tool returns `unavailable` and the credential is already out of memory; delete the file by hand"). Residual: the error result drops `revoked`, and at the next boot the leftover file would load and then fail on first use (invalid_grant clears it), so the behavior is self-healing.
- **m6 locking:** `disconnect` holds `UserAuth.lock` across `_revoke_value` and `forget`. Inside, only `post_form` (Basic auth, no lock), `forget` and `store.delete` run; none re-acquires the lock. `access_token()` takes the lock and `_refresh_locked`/`refresh_exchange`/`post_form` never do. No cycle, so no deadlock. Side effect only: user GETs that need a refresh wait for an in-flight disconnect (bounded by the revoke's rate-limit wait) and then fail with `auth` because the token is gone, which is correct. A residual unlocked path: `complete_sign_in` calls `install` without the lock, so a sign-in callback racing a refresh could overwrite or be overwritten (see n3).
- **m7 `unregister_secret`:** it is a plain `set.discard` with no reference count. Today it is only called on the replaced flow's random 32-byte state and 64-char verifier, which are never registered by any other code, so it cannot unregister a secret still in use. See n2: the fix is **incomplete**.
- **logging_setup.py:** the diff is one added function, `unregister_secret`, under the same lock; `register_secret`, `redact` and every other path are unchanged. Redaction of everything else is unaffected (existing logging tests pass).
- **m9 `test_oauth_scripts.py`:** offline and safe. It copies `common.sh` into a `tmp_path` project with its own `.env`, puts a stub `docker` first on a minimal `PATH`, and only reads `.gitignore`, `docker-compose.oauth.yml` and `docker-compose.yml` (never the repo `.env`, never real docker). Skips if bash is missing.
- **m1, m2, m3, m8, m10, m11:** fixed as described and spot-checked in code (`setup.sh` chmod removed, stray `.tmp` cleanup on load and before save, README/CLAUDE.md reflowed).

## Issues

### Blockers / majors
None.

### Minors

- **n1 (m5 follow-up) Spec/README wording for a failed local delete.** See judgement above. Fix: one sentence in U18/README; optionally keep `revoked` in the hint text.
- **n2 (m7 incomplete) Redaction set is still unbounded via an unauthenticated path** (`oauth.py` `PendingStore.start` unregisters only the flow it replaces; `claim()` and `clear()` set `_flow = None` without unregistering). Sequence `GET /oauth/start`, `GET /oauth/callback` (any state; the flow is consumed and its two literals stay registered), repeated, leaves one orphaned pair per cycle, and `redact` sorts every literal per log record. The new test only loops `start()` -> `start()`. Fix: unregister the flow's state and verifier in `claim()` (after the comparison, once the code is used or refused; the verifier is only needed by the caller, which already holds the value, so register it again or unregister after the exchange) and in `clear()`; extend the test with a start/claim loop.
- **n3 `complete_sign_in` installs the token outside the refresh lock** (`service.py` `_exchange_and_store` -> `self._auth.install`). A refresh in flight for the old token can finish after the new token is installed and overwrite it with the old grant's refreshed pair. Narrow (reconnect during a refresh), self-healing at the next 401 or reconnect. Fix: take `self._auth.lock` around `install`, or accept with a note.
- Cosmetic: the comment above the new settings in `config.py` still says "HOST_PORT is always bounded", which is now false.

## Spec issues
None new. A3.1 is internally consistent with the code; only the U18 sentence in n1 would be a clarification.

## Verdict

**APPROVED.** All A3.1 changes and m1-m11 fixes are implemented and tested; no regressions found; off-mode behavior and the 14 bot tools are unchanged. n1-n3 are small follow-ups (n2 is the only one with a real, if low-impact, behavior gap) to fix or explicitly accept.
