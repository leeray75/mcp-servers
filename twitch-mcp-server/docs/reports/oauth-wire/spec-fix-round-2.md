# Spec fix round 2: `docs/specs/oauth-wire/SPEC.md` and `tasks.md`

Responds to `docs/reports/oauth-wire/spec-review-round-2.md` (CHANGES REQUESTED: 1 major, 5 minors). Markdown only. No
source, test, CLAUDE.md or CHANGELOG edits. Nothing run, nothing committed.

## Major

| Ref | Resolution |
|---|---|
| **R2-1** | Adopted the reviewer's preferred rule. On the revoke path, passing **any** keyword (`client_id` only, `bearer` only, or both) makes `post_form` raise `unauthenticated` before the socket. Revoke never carries `Client-Id` or `Authorization`. The token path keeps the both-`None` rule. Updated: W6, W-D5, §6.2 (the `post_form` header branch, `:944–952`), §7, §8 (W6 row), the §6.4 client-test bullet, and tasks.md steps G and L. The tests are: revoke with `client_id` only, `bearer` only, and both, each raising `unauthenticated` with zero requests recorded. |

## Minors

| Ref | Resolution |
|---|---|
| m1 | `_oauth_error` is keyed first on `path` (revoke vs token), then on `form.get("grant_type")` with no default. Signature is `_oauth_error(status, body, path, grant)`. A token-path form without `grant_type` gets the generic 400 hint. Updated in §6.2, W4, W12, tasks.md step H. |
| m2 | The boot already degrades on any mint error (`server.py:1522–1526`, which decides on `exc.retryable`). What changes is the tool-call kind (`service.py` ~520, `app_token_unavailable`) and the hint. Reworded in W-D4 (effect on the mint), W12, the §8 W12 row, and the CHANGELOG line. The CHANGELOG phrase is now: "an app-token mint rejection now points at `TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET`." |
| m3 | The CLAUDE.md wording now says the token request carries `client_id` and `client_secret`, and revoke carries `client_id` and the token being revoked, nothing else. Updated in §6.5 and in the §6.2 docstring text. |
| m4 | The skipped revoke (empty `_client_id`) logs `revoke_skipped`, not `revoke_refused`. Updated in W7, the §8 W7 row, and tasks.md step K. The `revoke_refused` outcome stays for a Twitch refusal of a dead token. |
| m5 | No change needed, as the reviewer noted. The audit rows and line references were re-verified. |

## Files changed

- `docs/specs/oauth-wire/SPEC.md`
- `docs/specs/oauth-wire/tasks.md`
- `docs/reports/oauth-wire/spec-fix-round-2.md` (this file)
