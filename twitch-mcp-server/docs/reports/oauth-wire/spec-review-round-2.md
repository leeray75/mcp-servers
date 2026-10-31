# Spec review, round 2: `docs/specs/oauth-wire/SPEC.md`, `tasks.md`

Reviewer: read-only. Checked against SPEC.md, tasks.md, `spec-fix-round-1.md`, and the code/tests on disk
(`oauth.py`, `twitch_client.py`, `server.py` boot, existing tests).

**Verdict: CHANGES REQUESTED** (0 blockers, 1 major, 5 minors). Round-1 majors M1, M2 (design), M3 (docs list) and M4
are resolved apart from the points below.

## Focus checks

**M2 / W11 + W-D6 (refresh grant inside the `try`): correct.** Today `self._grant(...)` is outside the `try`
(`oauth.py:662-675`), so a raised `unauthenticated` from `post_form` bypasses `_force_reauth`. Moving the grant call into
the `try` and clearing only on `kind == "unauthenticated"` is sound. `_lock` is held throughout, `forget()` then
`_invalidate()` run as before, transport/5xx/429 (`_oauth_error` last branch or `upstream` retryable) re-raise
without clearing. Existing refresh tests are unaffected: `test_refresh_rejection_forces_reauth_and_clears_store`
(`test_oauth_units.py:205`) returns `(400, invalid_grant)`, still `unauthenticated` by status; the concurrency test
(`:190-201`) returns 200. No other test drives a refresh rejection (grep). W-D6 states the destructive consequences
(wrong `client_secret` 401, malformed-request 400 clear the store) honestly. One addition worth a sentence: this is the
first time the production path will delete the token file on a refresh rejection (previously only unit fakes did), so
it is a real behaviour change, not a clarification. It is already covered by the gate item and the CHANGELOG line.

**M3 / R13 exception wording: safe, with one inaccuracy (major below).** The two paths are fixed by the allowed-path
check (`twitch_client.py:941`), the host comes from the pinned `_auth_base` via `_url`, and the only callers
(`server.py:1438`, `:1701`, `:1722`) pass server-built forms; no agent-supplied value reaches the path, host or form.
The headerless branch is gated on both keywords being `None`, which no production caller changes. The CLAUDE.md and
`docs/SPEC.md` R13 edits are now listed.

**M4 / W12:** a grant-specific hint is workable, and the mint test is stated at `post_form` level. See m1 and m2 for
accuracy points. `_oauth_error` has a single caller (`post_form`, `:980`), so the signature change is contained.

**Scope creep:** none beyond OAuth wire shapes. W7's "skip revoke when `_client_id` is empty" is small and justified
(a doomed request would be refused anyway).

## Findings

### Major

**R2-1. W6 / W-D5 / section 6.2 say a revoke call with a `client_id` or `bearer` keyword "is refused as today". That is
wrong when both keywords are passed.** In the current code the guard is
`{} if path == OAUTH_TOKEN_PATH and client_id is None and bearer is None else self._headers(client_id, bearer)`
(`twitch_client.py:950-952`). With only one keyword, `_headers` raises; with both, `_headers` succeeds and the request
is sent with `Client-Id` and `Authorization` headers. So "refused" is only true for one keyword, and the W6/W8 test
("a revoke with a `client_id` or `bearer` keyword is refused before the socket") would fail if the test passes both.
This is the invariant the coordinator asked to be exact.
Fix: either state the true rule ("if either keyword is passed, the existing `_headers` rule applies: refused unless both
are present, in which case both headers are sent") and test one-keyword and both-keyword cases separately, or (preferred,
simpler and safer) add "on the revoke path any keyword makes `post_form` raise `unauthenticated` before the socket" and
test it with each of: client_id only, bearer only, both.

### Minor

- **m1.** `_oauth_error(status, body, grant)` with `grant = form.get("grant_type", "revoke")` (section 6.2) mislabels any
  token-path form lacking `grant_type` as revoke. Key the choice on `path` (`OAUTH_REVOKE_PATH` vs token) and then on
  `grant_type`.
- **m2.** W-D4 / W12 / the CHANGELOG say a failed mint "degrades the boot" because it becomes `unauthenticated`. The boot
  already degrades on any mint `TwitchMcpError` (`server.py:1522-1526`, which decides on `exc.retryable`, not the kind).
  What changes is the tool-call path (`service.py` ~520 treats `unauthenticated` from the mint as "degraded"
  `app_token_unavailable`) and the hint. Reword the effect accordingly; the W12 "implementer confirms degraded path"
  check should name both places. The CHANGELOG phrase "reports sign-in-independent hints" is unclear; say "an app-token
  mint rejection now points at `TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET`".
- **m3.** The CLAUDE.md Architecture wording says the two paths "carry their credentials in the form body". Revoke
  carries only `client_id` and the token to revoke (no secret, by W6/section 7). Use "carry their identifiers in the
  form body" or name `client_secret` for the token path only.
- **m4.** W7 logs `revoke_refused` when revoke is skipped for an empty `_client_id`, but no revoke was refused by
  Twitch. Use a distinct outcome (e.g. `revoke_skipped`) or say explicitly that the outcome name is reused.
- **m5.** Section 4.2 A11 still says "`_oauth_error` reads `error` only, so 400 becomes `upstream`"; correct. A13's
  statement that the refusal "becomes `upstream`" is also correct. No change needed; noting that the audit rows were
  re-verified and nothing regressed. Line references re-checked on disk: `refresh_now` `oauth.py:641`,
  `_token_or_raise` `:683`, `_resolve_user` `:716`, `disconnect` `:593`, `post_form` `:927`, `_oauth_error` `:984`. All
  correct.

## Test-design check (unchanged conclusions)
W11 raising-grant fakes, W7 raising-revoke fake, post_form-level 400 tests, W9 new-request counting and W8 fake lists
(file-format fake unchanged, `VALIDATE_USER` and `set_validate` updated) are workable. `test_service.py:1067` is
unaffected. tasks.md covers every requirement W1-W12 and the R13/CLAUDE.md edits; no change needed there except to add
the R2-1 revoke-keyword test cases to step L.
