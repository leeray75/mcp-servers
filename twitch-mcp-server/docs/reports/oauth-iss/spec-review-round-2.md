# Spec review, round 2: docs/specs/oauth-iss

Reviewer: read-only. Verified against SPEC.md, tasks.md and the real code on disk (not the fix report).

**Verdict: CHANGES REQUESTED** (0 blockers, 1 major, 4 minor)

## Round 1 findings: status

- M1 (`PendingStore`): fixed. oauth.py:137 confirmed; no `PendingFlows` left in SPEC.md or tasks.md.
- M2 (CLAUDE.md citation): fixed. It is now cited by bullet name, and the phrase is as quoted there.
- M3 (HTTP tests): the two new tests are now described correctly, but the fake-Twitch instructions are wrong (see M-A below).
- m1, m2, m3, m4: addressed. The helper mechanism, CHANGELOG, comment edits and "must fail on old code" are all in the spec.
- CHANGELOG.md:38: confirmed. Line 37 is the "Optional user identity" bullet, and line 38 reads "`GET /oauth/start` and `GET /oauth/callback` with single-use `state`, an `iss` check and a 10-minute". The `[Unreleased]` section has Removed, Changed and Added but no `Fixed` subsection, so "create if absent" is right.
- Fidelity to I-D1 and I-D2: faithful. I1-I3 implement the human decision exactly. `""` is present and is refused. Ordering and logging are unchanged. I-D2 is flagged as derived and is to be confirmed at the gate. The HTTP tests omit `iss` by nature, so narrowing I-D2's default to "the unit-test driver" is not a regression. No scope creep: the extra comment edits, CHANGELOG and fake changes are all supporting work.

## Major

**M-A. The fake-Twitch references point at the wrong function and the described change is not actionable as written.**
SPEC.md:111-118 and tasks.md G cite `tests/server_fakes.py:174-178` (`mint_app_token`) and "~185-200" (`serve_validate`).
- Those lines are inside `wired()` (lines 136-266), which builds a service for the tool-plan tests. The HTTP tests use `running_app()` (line 290). Its equivalents are lines 331-341, and they are registered only when `connected=True`.
- The new tests call `running_app(fake, fclock, settings)` with the default `connected=False` (as at test_server.py:619). That registers neither `/oauth2/token` nor `/oauth2/validate`. The default `FakeTwitch._default` (twitch_fakes.py:370-380) serves validate only after `set_validate` and returns 404 for token POSTs.
- `FakeTwitch.handle` (twitch_fakes.py:358-367) takes the first registered match. A branch added after a `connected=True` mint handler would never run.

Fix: reword the "Fake Twitch requirement" and tasks G.
- Name `running_app` (lines 331-341) and `wired` (164-178) as the only existing token/validate registrations. Both answer every `POST /oauth2/token` with an app token.
- Prefer that the two new tests register their own handlers with `fake.add` before the first callback, using the default `connected=False`. The token handler branches on `grant_type` in the form body: `authorization_code` returns a user-token body, anything else returns the app token. The validate handler answers the user bearer, or the test calls `fake.set_validate(user_id=OPERATOR, ...)`.
- If a shared helper in `server_fakes.py` is preferred instead, say that it must be registered before any other `/oauth2/token` handler, because first match wins.
- Note that the boot app-token mint is also a `POST /oauth2/token` with `grant_type=client_credentials`. So test 1 must count `authorization_code` exchanges by form body, not all token POSTs.

## Minor

- m-A. SPEC.md:68-69 repeats itself: "the `PendingStore` class docstring at line ~138 ... and the `PendingStore` docstring at line ~138 of the same block." It is one docstring (oauth.py:138). Delete the duplicate clause.
- m-B. More docstrings now misdescribe `iss`. oauth.py:94 ("the exact iss the callback must carry") and oauth.py:833 ("must carry") are inaccurate once the check is optional, and oauth.py:128 also names `iss`. Add them to the comment-only list in 6.1 and to tasks B. The security.py:9 text is "the ``iss`` check", not "an `iss` check" as quoted at SPEC.md:73. Quote it accurately.
- m-C. SPEC.md:87 says about 11 call sites. There are 10 `complete(` call sites (oauth units lines 321, 343, 362, 382, 407, 432, 455, 475, 479, 506). Say "about 10", or just "every". Also, the helper's `state=None` sentinel means "use pending.state", so it cannot express a missing `state`. This is harmless for current tests, but note it, or use a distinct sentinel.
- m-D. The unit test §6.3(a) overlaps the existing happy path test (test_oauth_units.py:483-511), which is the natural test to switch to the helper. State that (a) may be that test, renamed or cited with `I2`, so the implementer does not add a duplicate.

## Other checks

- tests/test_gate.py cannot see I-requirements: it matches only `R\d+`. The report-row count test is unaffected. No meta-test changes are needed.
- No existing test asserts that a missing `iss` is refused. None breaks.
- The security reasoning is unchanged and sound. Test 2 asserts nothing is echoed, which covers the no-echo property.
- Header pointer in docs/SPEC.md:9 is present and matches section 12.

## Required before approval

Fix M-A, and preferably m-A to m-D (all text-only). I-D2 still needs the human's confirmation at the gate.
