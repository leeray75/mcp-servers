# Spec review, round 2: scope-401 (Amendment 7)

Reviewed from disk: `docs/specs/scope-401/SPEC.md`, `tasks.md`, `docs/reports/scope-401/spec-fix-round-1.md`, against
`oauth.py`, `twitch_client.py`, `ratelimit.py`, `service.py`, `server.py` and the tests. Read-only; nothing run.

**Verdict: CHANGES REQUESTED** (0 blockers, 1 major, 5 minor). The design is now correct; the one major is a test that cannot detect the
regression it is meant to guard.

## Verified resolved from round 1

- **B1 / M1 (C9, C-D9) is correct and does break the deadlock.** The only paths that acquire with a class are `get` (`twitch_client.py:510`),
  `_write` (`:633`), `post_form` (`:967`), `validate_token` (`:1046`), `fetch_validate` (`:1091`). `post_form` and `validate_token` never call
  `_map`; only `fetch_validate` (`:1107`) does, so `circuit=False` on the three acquires plus `trip_circuit=False` at `:1107` covers every
  non-Helix path. With the user circuit open, `exchange` -> `_resolve_user` -> `_validate_pair` -> `fetch_validate` is no longer refused, so
  `install()` runs and the reset fires. `refresh_now`'s grant goes through `post_form`, so an open app circuit no longer yields
  `unauthenticated` -> `_force_reauth` -> `forget()`. No other circuit-gated call exists in `twitch_client.py`.
- **C7 via `oauth.on_invalidate` is safe.** `_invalidate()` is called only from `install()` (`oauth.py:589`) and `forget()` (`:604`),
  runs listeners synchronously with `contextlib.suppress(Exception)`, takes no lock, and `reset_user_circuit` is synchronous, so running
  inside `refresh_now`'s lock cannot deadlock. R48 order holds (store saved before `_invalidate`). Clearing the user circuit on
  `forget()` (disconnect, boot-probe rejection, R49 force-reauth) is acceptable: the user token is gone, so user Helix calls are
  `unauthenticated` for "not connected" anyway, and a later sign-in would clear it regardless. `oauth.py` really needs no edit.
- Round 1 M2-M5 and the write decision (scope `401` on a write -> `permission`, matching today's `twitch_client.py:762-764`; non-scope
  write `401` -> `unknown_outcome`) are reflected consistently in C6, N13, 4.1 and the 6.2 diagram. Trimming (C8 fold, hint cap and grep
  test dropped, wrappers removed) introduced no regression. Cited lines are still accurate.
- N1-N3 are failing-first as claimed: on the unfixed code a message-only scope `401` gives `unauthenticated` (N1, N3) and a message-only
  `403` gives `upstream` (N2), and `kind` is asserted before any new-API call, so no `TypeError` precedes it.
- Scope table in C10 still matches the Twitch scopes page (checked in round 1).

## Major

**M1. N7 and N8 as specified cannot detect the deadlock or the token wipe.** They run `UserAuth.exchange` / `refresh_now` in
`tests/test_oauth_units.py` with a "fake grant" and "validate answering 200". Those fakes are plain callables that never touch
`RateLimiter.acquire`, so they pass even if `post_form`/`fetch_validate` are still circuit-gated (the real bug lived in
`TwitchClient`, between `UserAuth` and the limiter). The "failing-first" intent of the fix is therefore not enforced.
Fix: define N7/N8 with a real `TwitchClient` and `FakeTwitch` (as `make_wiring` in `tests/identity_fakes.py:128` does), with
`attach(grant=client.post_form..., validate=_validate_pair...)`, trip the circuit through a real Helix `401`, then run `exchange` / `refresh_now`
and assert the token is installed, the token file exists and the user circuit is `False`. Add a client-level test: with both circuits
open, `fetch_validate` and `post_form(TOKEN_FORM_PATH, ...)` still make a request, and a validate `401` raises `unauthenticated` without
tripping. Record N7/N8 as failing-first on the unfixed code (circuit trip via a user Helix `401` is possible there too, so they can fail).

## Minor

- m1. The read-path `unauthenticated` raise in `_map` (`twitch_client.py:768-779`) keeps the hint "restart the container: this circuit is
  process-wide and never resets itself". With this spec that is wrong for the user class (clears on sign-in) and for `fetch_validate`
  (no circuit at all). The spec edits lines 757-779 but does not say what this hint becomes; state per-class/validate wording
  (the later-call hint in `acquire` is covered by C3, the first-call hint is not).
- m2. C-D9's rationale ("R24 and R50 read Helix only, so this narrows R24") is inexact: R24 (docs/SPEC.md:185) says "stops all further
  upstream traffic", while its trigger is the API base. The real change is that sign-in, refresh, revoke, validate and the mint are no
  longer stopped by the circuit; R50 (boot probe degrades, no circuit) is not narrowed, it is already that way. Reword C-D9 / the
  docs/SPEC.md R24 marker so the gate sees one clear sentence: "R24's circuit stops Helix calls only; token endpoints and validate are
  exempt; the mint works under an open app circuit."
- m3. C5's `user_token_rejected` block is not needed for the incident: with C9 the status validate is ungated, so a scope-deficient
  but valid user token already yields a normal user block. It only matters for a genuinely rejected user token, where status today
  raises `unauthenticated`. It is small (one `except` for `unauthenticated` on the user validate, no other kind swallowed), and
  `degraded_reason` is already a free `str`, so keeping it is defensible, but it is beyond C-D1/C-D2; offer the human drop-or-keep at the
  gate. If kept, state that only `kind == "unauthenticated"` is caught.
- m4. Race (low impact): a user Helix call in flight with the old token can return `401` after a refresh has installed the new token and run
  the reset, re-opening the user circuit with a valid new token until the next install. Note it in 7 or accept it explicitly.
- m5. N13 says the existing identity test is "re-pointed to the non-scope case"; it already posts a non-scope `{"error": "Unauthorized"}` body
  (`test_identity.py:536`), so only the circuit assertion at `:546` changes. N5 conflates two states (circuit opened by a read, then a validate
  `401`); fine, but name the validate `401` as the cause of the degraded block.

## C-D9 gate statement

Stated twice (4.2 and 7, "Flag at gate") and in tasks.md A, but see m2 for the wording. Everything else in 4.2 (C-D3, C-D5, C-D10) is
clear.
