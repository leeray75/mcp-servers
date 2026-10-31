# Review round 1 - oauth-wire (Amendment 5)

Reviewer: read-only. Scope: W1-W12, W-A1; oauth-iss checked only for regression.

## Gate
`make check` (ruff, mypy --strict, pytest): 1004 passed, coverage 91.36% (floor 90%). Green.

## Findings
- W1-W3: `_normalise_scope` joins an all-string array, keeps str/None, refuses everything else as `malformed` without naming a value; exchange requires scope, refresh keeps fallback on absent/null/""/[]. Tests cover array, string, [] and bad shapes on build_token plus refresh. OK.
- W4/W12: `_REAUTH_ERRORS` removed; `_token_or_raise` and `_oauth_error` map 400 by status; hint chosen by path then grant (revoke none, mint env hint, auth-code/refresh sign-in hint). Body is not read. OK.
- W5: `login` read in `validate_token` and `_resolve_user`; tests for login and ignoring `user_login`. OK. (`build_token` still reads undocumented `user_login`/`user_id` from the token body as tolerated fallback, per A2.)
- W6: revoke path refuses any keyword or empty form `client_id` before the socket, sends no headers; token path rule unchanged. Docstring/comment updated as required. OK.
- W7: empty client id logs `revoke_skipped` and deletes locally; dead-token 400 deletes; non-unauthenticated re-raises and keeps the credential. OK.
- W11: grant call is inside the try; only `unauthenticated` calls `_force_reauth`. Tests for raise-unauthenticated, raise-upstream, 500 return. OK.
- W8/W9/W10: fakes use documented shapes (user_login/user_type dropped from validate fakes); HTTP callback test with array scope present; no new value logging in the diff. Implementer reports fail-before-fix evidence.
- W-A1: initial-build report R13 row repointed to the replacement tests; docs/SPEC.md carries superseded markers on R13, R47, R49, R50, R51. Minimal and correct.
- Docs: CLAUDE.md (both bullets), CHANGELOG `Fixed`, docs/SPEC.md pointer all match spec.
- Invariants: single httpx module, pinned hosts/paths, writes not retried, tests network-free, R13 narrowed only for revoke. oauth-iss claim() logic intact.
- Deviation (generic 400 hint "Check the OAuth request and retry." for a token-path form with no grant_type): spec left the text open; acceptable, no value leaked.

## Counts
Blocker: 0. Major: 0. Minor: 0.

## Verdict
APPROVED
