# Spec review, round 3: `docs/specs/oauth-wire/SPEC.md`, `tasks.md`

Reviewer: read-only; read SPEC.md and tasks.md from disk and re-checked the cited code (`twitch_client.py:927-952`,
`:984`, `server.py:1522`, `oauth.py`).

**Verdict: APPROVED** (0 blockers, 0 majors, 2 minors). All round-2 findings are resolved.

## Verification of round-2 dispositions

- **R2-1 (revoke keywords): resolved and consistent.** W6, W-D5, section 6.2, section 7, the section 8 W6 row, the W8
  client-test bullet and tasks.md steps G and L all say the same rule: on `OAUTH_REVOKE_PATH`, any keyword (`client_id`
  only, `bearer` only, both) raises `unauthenticated` before the socket; otherwise no headers and no `_headers` call;
  the form must carry a non-empty `client_id`. The token path keeps the existing both-`None` rule
  (`twitch_client.py:950-952`), so the headerless mint is unchanged. Three tests are specified with zero recorded
  requests. Safety: both paths are fixed by the allowed-path check, the host is pinned via `_url`, the only production
  callers pass no keywords (`server.py:1438`, `:1701`, `:1722`), and now no keyword combination can send headers to
  revoke. This is strictly tighter than before.
- **m1 (`_oauth_error` keyed on path first): resolved.** Signature `_oauth_error(status, body, path, grant)`, label by
  path, then `form.get("grant_type")` without default; a token-path form lacking `grant_type` gets the generic 400
  hint. Single caller (`post_form:980`), so the change is contained.
- **m2 (boot wording): resolved.** W-D4/W12/section 8 now say the boot is unchanged (`server.py:1522-1526` decides on
  `retryable`) and that the tool-call path (`service.py` ~520) and the hint are what change. CHANGELOG line reworded
  accurately.
- **m3 (R13 wording in CLAUDE.md): resolved.** Token request carries client id and secret; revoke carries client id and
  the token only.
- **m4 (`revoke_skipped`): resolved** in W7, the section 8 W7 row and tasks.md step K. `outcome` is a free-form logged
  key (`logging_setup.py`), so a new value needs no schema change.
- **m5:** no change needed; confirmed.

No regressions found. No scope creep: all changes remain OAuth wire shapes, their error mapping, and the R13/R49
documentation they touch. Section 11 still lists no open questions; W-D2 to W-D6 are marked for confirmation at the gate
with the W-D4 mint effect and W-D6 consequences called out first.

## Minor

- **m1.** Section 7, R13 bullet: the sentence is a comma splice ("...may go without `Client-Id`/`Authorization`,
  `/oauth2/token` goes without them only when..."). Use a semicolon or split into two sentences.
- **m2.** W8 client-test bullet reads "a revoke with no `client_id` in the form, is refused" (stray comma), and W6's
  first sentence mentions only `OAUTH_TOKEN_PATH` before the revoke rule follows. Tidy while applying; no behavioural
  ambiguity remains.
