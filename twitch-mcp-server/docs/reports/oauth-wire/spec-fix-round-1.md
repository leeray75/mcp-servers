# Spec fix round 1: `docs/specs/oauth-wire/SPEC.md` and `tasks.md`

Responds to `docs/reports/oauth-wire/spec-review-round-1.md` (CHANGES REQUESTED: 4 majors, 6 minors). Markdown only. No
source, test, CLAUDE.md or CHANGELOG edits. Nothing run, nothing committed.

## Majors

| Ref | Resolution | Where |
|---|---|---|
| **M1** | Accepted. `tests/server_fakes.py:350–364` (the on-disk token file) keeps a string `scope` (R48). The fakes that change are `VALIDATE_USER` (`server_fakes.py:65–75`, drop `user_login` and `user_type`) and `set_validate` (`twitch_fakes.py:291–310`, drop `user_login` and `user_type`). The token-endpoint grant fakes switch to an array `scope`. | SPEC W8, §6.4 |
| **M2** | Accepted. The production error path is `post_form` raising `_oauth_error` (`twitch_client.py:979–980`, `:984`). `refresh_now` moves the grant call inside the `try` (W11). Derived decision **W-D6**: only `unauthenticated` (400 or 401) clears the store and forces reauth. Transport, 5xx and other upstream errors re-raise without clearing. Unit tests use raising fakes. A13 and A11 cite the production path. | SPEC §4.1 W-D6, §4.2 A11/A13, W7, W11, §6.1 |
| **M3** | Accepted. R13 is added to Amends and to the §12 pointers. The `CLAUDE.md` `twitch_client.py` Architecture bullet and the `docs/SPEC.md` R13 marker are listed. The `post_form` docstring and comment (`twitch_client.py:927–951`) are rewritten. The exact exception is stated in W6: the both-`None` keyword guard stays. Revoke sends no headers only when neither `client_id` nor `bearer` is passed as a keyword. The form must carry a non-empty `client_id`, or the call is refused before the socket. | SPEC header, W-D5, W6, §6.2, §6.5, §7, §12 |
| **M4** | Accepted. W-D4 states that the 400 mapping also covers the client-credentials mint, which changes from `upstream` to `unauthenticated` (intended, R50 degrade). W12 gives the mint its own env hint. `_oauth_error` takes the grant and selects the hint. A `post_form`-level test is specified. W-D6 makes a malformed-request 400 on refresh clear the store; this is accepted under R49 and flagged at the gate. | SPEC §4.1 W-D4, W12, §6.2, §6.4, §8 |

## Minors

| Ref | Resolution |
|---|---|
| m1 | §4.2 now says four Twitch pages (getting-tokens, refresh-tokens, validate-tokens, revoke-tokens), and the client-credentials response is on getting-tokens. |
| m2 | Stated in W4: `_REAUTH_ERRORS` is referenced only at `oauth.py:67` and `:690`, and no test imports it. The existing `(400, invalid_grant)` tests at `test_oauth_units.py:210` and `:363` keep passing under status-only mapping. |
| m3 | W9 counts **new** validate requests: it records the count after the handler is registered and before the callback, and asserts a rise of exactly one. The validate handler is registered after boot and keyed on the `Authorization` value. `connected=False` boot still yields one 404 validate, which the spec now accounts for. `tmp_path` token file is kept. |
| m4 | `test_disconnect_revokes_and_touches_nothing_else` calls `set_credentials` first. A second case with no credentials asserts no revoke is sent. W7 states that an empty `_client_id` skips the revoke call, logs `revoke_refused`, and still deletes the local credential. |
| m5 | A13 and §9 record the refresh-401 wording gap ("Twitch rejected this server's credentials" with an env hint) as known and out of scope. Behaviour is kept by W4. |
| m6 | tasks.md step B now records both the HTTP and the unit-level failing runs. Steps I (R13 and `CLAUDE.md` edits, `docs/SPEC.md` R13 marker) and E/G/H (the `refresh_now`, `post_form` and `_oauth_error` edits) are added. |

## Gate items for the human

- **W-D4 mint effect:** a `400` from the client-credentials mint changes from `upstream` to `unauthenticated`, with its own env hint.
- **W-D6 consequences:** a `401` caused by a wrong server `client_secret` also clears the user's stored credential, and so does a malformed-request `400` on refresh. Both follow R49's literal wording.

## Files changed

- `docs/specs/oauth-wire/SPEC.md` (rewritten: W1–W12, W-D1–W-D6, §6 file and symbol edits, §8 table)
- `docs/specs/oauth-wire/tasks.md` (steps A–P)
- `docs/reports/oauth-wire/spec-fix-round-1.md` (this file)
