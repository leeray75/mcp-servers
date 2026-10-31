# Spec review, round 1: docs/specs/oauth-iss/SPEC.md and tasks.md

Reviewer: read-only pass. Verified against src/twitch_mcp/oauth.py, server.py, tests/, CLAUDE.md, docs/SPEC.md.

**Verdict: CHANGES REQUESTED** (0 blockers, 3 major, 4 minor)

## What checks out

- AGENTS.md section 4 is complete: header with Status Draft, problem, goals, non-goals, decisions, numbered
  requirements, design, security, test plan, out of scope, and Open questions "None".
- I1-I6 are testable and faithful to I-D1 and I-D2, with no scope creep.
- The one-line change in the spec is correct: the `iss is None or` clause at oauth.py:183 becomes `iss is not None and`.
  `state` stays checked first at oauth.py:180, so a callback with a wrong or missing state is refused before `iss` is looked at.
- Empty-string handling is sound. `params.get("iss")` at server.py:1783 returns `""` for `?iss=` and `None` when
  absent, and `_same("", flow.iss)` is false, so the callback is refused.
- No `iss` value is logged or echoed. `_log_oauth` takes only the route, status and kind.
- The Amendment 4 pointer at docs/SPEC.md:9 matches section 12.
- test_gate.py is not affected. It only sweeps `^- \*\*R(\d+)\*\*` and `\bR(\d+)\b`, so `I1`-`I6` and `I-D1` are
  invisible to it. The report-row count test is also unaffected, because R47 keeps its row.
- No existing test asserts that a missing `iss` is refused, so none breaks. The call sites that pass an `iss`:
  - test_oauth_units.py:79-106 passes `flow.iss`, which still works.
  - test_oauth_units.py:88 passes a wrong `iss`, which still gives REFUSED_ISS.
  - test_oauth_units.py:321-506 pass `pending.iss`, which still works.
  - test_server.py:630 fails on `state`, with no `iss` involved.

## Major

**M1. Wrong class name: `PendingFlows` does not exist.** SPEC.md:45 (I1), :61 (section 6.1) and tasks.md:7 say
`PendingFlows.claim`. The class is `PendingStore` (oauth.py:137), and the tests use `PendingStore`
(test_oauth_units.py:77). Fix: replace `PendingFlows` with `PendingStore` throughout both files.

**M2. CLAUDE.md line citation is wrong.** SPEC.md:88 and :148 and tasks.md:13 say "line 11". Line 11 is the SDD
process note and contains no `iss` text. The phrase "an ``iss`` check and a 10-minute TTL" is in the OAuth invariants bullet at about
CLAUDE.md:155. Line 85 (the architecture bullet) lists `iss` in a parenthetical and does not need changing. Fix: cite the
"**OAuth** (R46-R51)" invariants bullet by name, not by line number.

**M3. Section 6.3 misdescribes the HTTP tests, and no success test exists to modify.** SPEC.md:83 says the
"success callback" test sits around test_server.py:614-630 and drives `code` and `state` only. The only callback test there,
test_server.py:630, is a refused call (`code=x&state=y`). No HTTP test of a successful callback exists anywhere
(grep for `oauth/callback` in tests/ finds nothing else). So the spec's HTTP row for I2 ("callback with code and state
only -> success page, 200") and tasks.md E ("callback success") both describe a new test, which is larger than "stop
passing iss". Fix: say these are two new tests in test_server.py. State what each needs: a live pending flow from
`GET /oauth/start`, the `state` read back from the 302 Location, and the fake Twitch handling the
`authorization_code` token exchange and validate calls (the unit test at test_oauth_units.py:487 shows the pattern).
The wrong-`iss` test must also assert 400, the failed page, no new `POST /oauth2/token`, and that the flow is dropped.
Without that detail the implementer may under-deliver or ask for a spec amendment.

## Minor

**m1. Docs the change touches are missing from section 6.5.** CHANGELOG.md:38 states "an `iss` check", and AGENTS.md
section 7 makes the reviewer check `CHANGELOG.md` under `[Unreleased]`. The spec lists neither. Fix: add a CHANGELOG
`[Unreleased]` "Fixed" entry for this bug. Also list the doc and comment lines that say `iss` is mandatory:
docs/SPEC.md:254 (the R47 pointer already covers it), oauth.py:11, :138 and :168, and security.py:9. The README has no `iss` text.

**m2. Say explicitly that no existing test breaks, and which cases are new.** Section 6.3 says "every
`complete(..., iss=pending.iss)` call stops passing iss". That is a large, optional edit across about 11 tests
(test_oauth_units.py:321-506), and it is the substance of I-D2. State the intended mechanism, for example a helper default
of `iss=None` in the unit-test driver, so the change is one edit and not eleven. Also name the one place that must keep
passing `iss`: the correct-`iss` case (b).

**m3. I3's verification row is vague.** SPEC.md:118 says "HTTP or unit". `?iss=` yields `""` only after
Starlette query parsing, so the HTTP path matters. Fix: require a unit case, and optionally an HTTP case with `iss=`
that returns 400 and mints nothing.

**m4. Acceptance does not require a test that fails against the old code.** Add: tests (a) and the HTTP
success test must fail against the unfixed oauth.py:183. This is the lesson in section 1, where fakes that always supply `iss`
hid the bug. Also record, in the implementation report, that I-D2's default-omit applies to every driver of `claim()` or `complete()`.

## Security reasoning

Sound. The residual risk (no RFC 9207 mix-up defence when `iss` is absent) is stated and justified: a single
configured authorization server, a client-secret exchange at its fixed token endpoint, and `state` binding. Constant-time
compare, ordering, single-use consumption on any claim (including a refused `iss`) and the TTL are unchanged.

## Required before approval

Fix M1-M3 (and ideally m1-m4), then re-present the spec at the gate. Confirm I-D2 with the human at that point.
