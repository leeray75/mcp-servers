# Review round 3: user OAuth guild discovery (A3, A3.1, A3.2)

- **Reviewer:** independent, read-only (only this file written). **Date:** 2026-10-29. `.env` never read.
- **Inputs re-read from disk:** `SPEC.md` section 12, `fix-round-2.md`, `oauth.py`, `service.py`, `config.py`, README, tests.
- **Verdict: APPROVED** (0 blockers, 0 majors, 0 minors, 0 spec issues; two non-blocking observations).

## Gate (my own run)

`wsl.exe -e bash -lc 'cd ".../discord-mcp-server" && make check VENV=~/.venvs/discord-mcp'`

| Step | Result |
|---|---|
| ruff format/check, mypy --strict | passed |
| pytest | **622 passed** |
| Branch coverage (gate 90%) | **96.61%** |
| Pre-existing tests unmodified | `git diff --stat -- tests` is empty; all test files are untracked additions. Confirmed. |

## Verification

- **n1 / A3.2:** `TokenStore.delete` maps `OSError` to `unavailable`; the hint is "The token file <path> is still on disk and holds the token: delete it by hand." It contains only the configured path, never contents or secrets. `UserAuth.forget` clears `token` and `token_file_invalid` before calling `delete`, and `disconnect` sets `last_error` before `forget`, so memory is clear and the revoke result stands. README carries the same sentence (lines ~181-183). Test asserts kind, path in hint, no token values, status `not_connected`.
- **n2 redaction lifecycle:** secrets are unregistered on replacement (`start`), on any refused claim (no flow is a no-op; expired and state mismatch call `_drop`), on `clear`, on `release`, and the authorization code in the `finally` of `complete_sign_in`. A successful claim keeps state and verifier registered until `release` runs in that `finally`, after the exchange, so the verifier in flight is never unregistered early. A concurrent `/oauth/start` during an exchange cannot touch the claimed flow because `claim()` already set `_flow = None`; `start()` then only drops a (nonexistent) pending flow and registers the new pair. A concurrent callback during the exchange claims only the new flow and drops only that one. The `finally` also covers non-`DiscordMcpError` exceptions. `unregister_secret` is a plain discard, safe because every unregistered value is a random per-flow string or the one-shot code. At most one unclaimed pair plus one in-flight pair is ever registered; the start/callback/expiry/clear loop test covers the earlier gap.
- **n3 install under the lock:** `_exchange_and_store` takes `UserAuth.lock` only around the synchronous `install`, after the `/users/@me` call. `complete_sign_in` holds no lock on entry. The callback's `/users/@me` GET passes an explicit token, which short-circuits `_bearer` and the 401-replay branch, so it never waits on the lock. The refresh path holds the lock across `post_form` and `install` but never calls the sign-in path; `disconnect` holds the lock across `post_form` (Basic auth, no lock) and `forget`. `asyncio.Lock` is non-reentrant but nothing re-enters it, and an `install` failure raises after the `async with` exits, so the revoke runs without the lock. No cycle, no deadlock. Waiting callers just serialize behind an in-flight refresh or disconnect.
- **Cosmetic:** the `config.py` comment now says HOST_PORT is bounded only when the switch is on.
- **Regressions:** none found. Callback refusal semantics (`callback_refused`, no Discord request on refusal, flow always consumed) are preserved; the 14 bot tools, off-mode behavior and `/healthz` are untouched this round.

## Observations (no action required)

- The main U18 text in SPEC section 5 is not edited in place; the sentence lives in section 12 per the amendment convention, and the README repeats it. Fine.
- A reconnect callback that waits behind an in-flight disconnect will install afterwards and reconnect the account; that is the expected outcome of the user's last action.

## Issues

None.

## Verdict

**APPROVED.** All n1-n3 fixes and the cosmetic item are correct, A3.2 is implemented as specified, and no regression was found. Ready for the summary report; AC-L1 (live sign-in, reconnect, PKCE acceptance) remains the human's step.
