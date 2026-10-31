# Spec review, round 1: scope-401 (Amendment 7)

Reviewed: `docs/specs/scope-401/SPEC.md`, `tasks.md`, the Amendment 7 pointer in `docs/SPEC.md`. Read-only review against the
code at HEAD; nothing was run.

**Verdict: CHANGES REQUESTED** (1 blocker, 5 major, 7 minor)

## What checks out

- AGENTS.md section 4: all ten sections present, Status Draft, Open questions empty. (C-D3..C-D8 are "confirm at gate", which is acceptable.)
- Defect 1 is real: `twitch_client.py:760` trips before `_scope_phrase` at `:762`. Defect 2 is real: `_SCOPE_BEARING_KEYS` at `:79` has no `message`.
- The "third defect" is real: `server.py:1853` calls `client.fetch_validate(bearer)`, which hard-codes `acquire(APP_CLASS, ...)` at `twitch_client.py:1091` and `_map(..., APP_CLASS, ...)` at `:1107`. A user-token validate is billed to the app bucket and, on 401, trips the circuit under the app label.
- Cited lines/symbols are accurate (client 79, 302-318, 384-386, 757-790, 1036, 1085-1107; ratelimit 228-270, 365-370; service 360, 384-386, 410, 513-545; oauth 488, 583-590, 660, 699, 941; server 86, 1509-1511, 1681, 1744, 1756, 1853). README 232-257/161/315/368/392, `.env.example` 74-80, CHANGELOG line 5 and CLAUDE.md 68/92/131 are correct. Small drift: CLAUDE.md "service.py (line 79)" is really the `service.py` bullet at line 62 (line 79 is the `identity.py` bullet); docs/SPEC.md markers are 185/248/249/347-358, not 184/247/248/357 (spec says "~").
- The C12 scope table matches https://dev.twitch.tv/docs/authentication/scopes/ exactly (fetched today). The page says nothing about the missing-scope error body, which confirms the spec's "undocumented" claim. I did not re-fetch the Helix reference prose for the quoted `moderator:read:followers` sentence (unverified).
- App-token reads and `twitch_account_status` do keep working after a user-token 401 once `acquire` is keyed per class and the roster/user reads carry `USER_CLASS` (`server.py:1683`). `_map`'s `kind` is the token class for reads and writes.

## Blocker

**B1. The design makes sign-in unable to clear the user circuit (deadlock), contradicting C-D3.**
`UserAuth.exchange` (`oauth.py:~650`) calls `_resolve_user` (`oauth.py:~713`) *before* `install()`. Twitch's token response carries no `user_id`, so this always runs. `_resolve_user` calls `_validate` = `_validate_pair` -> `fetch_validate`. With C9 that call uses `USER_CLASS`, so `acquire` (C4) refuses it while the user circuit is open; `_validate_pair` turns the exception into `(401, None)` (`server.py:1855`) and `_resolve_user` raises "Twitch refused the new token". `install()` never runs, so `on_install` never fires and the circuit stays open until restart. Same trap for `UserAuth.probe`. Only the refresh path (token already has `user_id`) can clear it, and it runs only near expiry.
Also `fetch_validate`'s docstring says "no circuit" but `_map` trips on 401, and R24/R50 scope the circuit to Helix.
Fix: the circuit applies to `TWITCH_API_BASE` (Helix) calls only. `fetch_validate` (and `post_form`, see M1) neither check nor trip it; a 401 from `/oauth2/validate` just raises/returns unauthenticated. Then C9's `token_type` parameter is only a bucket choice (keep it for the bucket, drop it from the circuit story), C-D7 disappears, and C6 shrinks (see M5). Add a test: user circuit open, `exchange` still installs the token and clears the circuit.

## Major

**M1. An open app circuit wipes the stored user token (C-D7 / C5 claim "user calls keep working" is false).**
`post_form` acquires `APP_CLASS` (`twitch_client.py:967`). With the app circuit open, `refresh_now`'s grant is refused with `unauthenticated`, and `refresh_now` treats that as a rejection: `_force_reauth` -> `forget()` deletes the token file (`oauth.py:~690`). Revoke on disconnect is also refused. This exists today but the spec explicitly promises the opposite and ratifies it in C-D7. Fix as in B1 (oauth2 endpoints exempt from the circuit), or at minimum make `refresh_now` not force re-auth for a circuit refusal; state which in the spec and add a test.

**M2. C7 `on_install` is redundant and adds surface.**
`UserAuth.install()` already calls `self._invalidate()` (`oauth.py:589`), which runs every `on_invalidate` listener; exchange (660) and refresh (699) both reach it. Wiring `oauth.on_invalidate(service.limiter.reset_user_circuit)` at `server.py:1744` does the job with no change to `oauth.py` (drop task G, `OAuthFlow.on_install`, N7's `auth.on_install`). Safety: the listener is synchronous, takes no lock, and `_invalidate` suppresses exceptions, so running inside `refresh_now`'s lock is safe. It also fires on `forget()` (R49 force-reauth, disconnect); resetting the user circuit then is harmless (no user token to protect) and arguably right. State this in the spec.

**M3. C-D4 / C11 is framed as "required by the brief" but silently changes current behaviour and creates an inconsistency; present it as a human choice.**
Today the scope check precedes the write branch (`twitch_client.py:762-764`), so a scope-401 on a write already returns `kind=permission`; C11 downgrades it to `unknown_outcome`. Meanwhile a scope-403 on a write stays `permission` (`:781-787` raises before the write branch), so the same missing scope gives different kinds by status.
Trade-off for the human (do not decide here):
- (a) Keep write scope-401 `unknown_outcome` (spec): never claims "nothing happened" when Twitch's behaviour is undocumented; cost: the agent is told the write "may have taken effect" and to check the channel for what is almost certainly a pre-action auth refusal, and the actionable scope fix is buried in the hint; inconsistent with 403.
- (b) Write scope-401 -> `permission` (current behaviour, matches 403 and reads): accurate and actionable if Twitch rejects at the auth layer before acting (very likely, but undocumented); R16 still holds because nothing is retried either way (`permission` is non-retryable); risk: a (hypothetical) 401 after a partial effect would be reported as a definite refusal.
Either is compatible with "writes never retry". The spec should list both options in 4.2 and cite that (b) is today's behaviour.

**M4. The new `permission` hint will state something false on real Twitch bodies.**
`_permission_error` (`twitch_client.py:852-875`) says "This token holds no scopes." when the body has no `scopes` list. Real Twitch bodies (the incident body) have none, so every real missing-scope hint would claim the token holds no scopes, while it held `user:read:email`. C2 repeats "plus the scopes the token holds". Fix: omit the sentence when the body gives no list, or take the held scopes from the stored `UserToken.scope`/validate (a second source, so weigh against complexity); the minimal fix is omission. Update N1/N3 to assert the hint has no false "holds no scopes".

**M5. Scope creep beyond C-D1/C-D2 (repo goal: reduce complexity).**
Strictly needed: C1, C2, C3, C4, C5 (as per-class `acquire`/`trip_auth`), C7 (via M2), C9's bucket fix, C10 (healthz must choose a circuit), C12, docs. Not needed or collapsible:
- C8 is a free consequence of C3 (per-class latch fires per class); fold into C3, keep one test.
- C-D6 / C6 (new `degraded_reason` values, partial results, no-raise) goes beyond the decisions. After B1 (validate bypasses the circuit) the status tool is no longer bricked by a circuit; a minimal version is "catch `unauthenticated` from each validate and report `degraded`". Note C6 as written still raises on the *first* user-token 401 from validate (circuit not yet open) and the early return at `service.py:~523` skips the user block, which C6 says it keeps; either simplify or specify both.
- C-D8 / N11 (200-char cap): optional hardening; `error_description`/`error` were already uncapped. Make it a human call or drop.
- N13 (new grep test for scope literals): D10 is unchanged and no scope literal exists outside `config.py` today; drop.
- Per-class `app_auth_failed`/`user_auth_failed` wrappers on client, service and wiring: keep only what a caller needs (`limiter.auth_failed(token_type)`, wiring app check for `/healthz`).

## Minor

- m1. Stale text not listed: `twitch_client.py:23` module docstring, `_error_code` docstring ("Nothing is ever read from `message`", ~296), the `_SCOPE_PHRASES` comment at lines 80-81 ("`message` is never consulted"), `errors.py:9` ("never from message text"), CLAUDE.md line 92 is listed but README/SPEC R44 line 248 wording must name the exception.
- m2. N1 failing-first: `auth_failed(APP_CLASS)` raises `TypeError` against unfixed code (property, not method). Order assertions so `kind == "permission"` fails first, or write N1-N3 with the pre-existing API and extend after.
- m3. N14 passes on unfixed code (`_scope_phrase` is only called for 401/403); label it a guard, not failing-first. Also add one 401/403 body with `message` that contains "missing" but not "scope" (stays `unauthenticated`/`upstream`).
- m4. R44 narrowing is otherwise minimal and safe (message read only inside `_scope_phrase`, requires the word `scope` plus a phrase, no table). Consider stripping control characters/newlines from the Twitch string placed in `hint` (it is free text from a third party reaching the agent).
- m5. `tests/test_service.py:169` (N3's cited route) is a table row of write cases with `unban_user`; confirm the read `get_banned_users` fake exists at `/helix/moderation/banned` before citing it.
- m6. Task H/I and tasks.md B need the new B1/M1 tests (exchange with open user circuit; refresh under open app circuit does not delete the token file).
- m7. Spec cites hint wordings "Sign in again with ... A successful sign-in clears this block." (C4) without saying `.env`/OAuth-off deployments (user circuit can only open when a user token exists, so fine) and R30: the limiter listener (`on_identity_change`) fires for either class; say so explicitly.

## Requirement traceability (summary)

C1, C2 testable via N1-N3, N11, N14. C3-C5 via N4-N6, N10. C7 via N7/N8 (re-point per M2). C9 via N5/N7. C10 via N12. C11 via N9 (pending M3). C12 documentation only, verified. No requirement is untraceable; C8 and C6 are the weakest/least necessary.
