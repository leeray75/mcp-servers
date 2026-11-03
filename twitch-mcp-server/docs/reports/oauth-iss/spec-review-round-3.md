# Spec review, round 3: docs/specs/oauth-iss

Reviewer: read-only. Verified against SPEC.md, tasks.md and the real files (server_fakes.py, twitch_fakes.py, oauth.py, config.py), not the fix report.

**Verdict: CHANGES REQUESTED** (0 blockers, 1 major, 2 minor)

## Round 2 findings: status

- M-A: mostly fixed. `wired()` is no longer referenced, `running_app()`'s `connected=False` default is acknowledged, and the first-match ordering reason is correct (twitch_fakes.py:358-367). Registering handlers inside the test body works. The app boots (degraded, as the existing test at test_server.py:619 already does) before the test registers anything, and later overrides serve the callback's token POST. Counting `authorization_code` POSTs by form body is correct. A new gap found while checking workability is M-B below.
- m-A to m-D: all fixed in the text. The 10 `complete(` sites match the real file. The `_PENDING_STATE` sentinel is fine. The security.py:9 quote is exact. Unit case (a) is the existing happy-path test.
- Precision: leaving the exact `authorization_code` body and handler code to the implementer, with a pointer to test_oauth_units.py:487-503, is acceptable for a spec. The field list is given and the shape is checkable. See m-E for one correction.

## Major

**M-B. Test 1 will fail unless the token file is redirected to a writable path, and the spec does not say so.**
`server_settings()` (server_fakes.py:83-103) leaves `twitch_oauth_token_file` at its default (config.py:127, an absolute path under `/data`). A successful callback runs `exchange` -> `install` (oauth.py:637) -> `TokenStore.save`. A failed write raises `upstream` "token file could not be written" (oauth.py:406-411), so the callback would return 400 instead of 200. `running_app(connected=True)` avoids this with a temp file, but the spec's tests use `connected=False`.
Fix: in SPEC §6.4 (and tasks G), require test 1 to pass `twitch_oauth_token_file=str(tmp_path / "twitch_user_token.json")` through `server_settings(...)`. The path must be absolute (config.py:214), and tests run under WSL/Linux. The test should also assert the file exists with mode 0600, or at least not touch the default path. Test 2 does not write a token, but should use the same settings so a regression cannot reach `/data`.

## Minor

- m-E. The spec has the validate handler (§6.4 test 1, tasks G) as required. If the `authorization_code` body includes `user_id`, as the spec specifies, `exchange` skips `_resolve_user` (oauth.py:634) and never calls validate. Either drop the validate handler and say so, or note it is only needed when `user_id` is omitted. Also, `fake.set_validate(...)` is not "the same effect": it serves one static body for every validate call, including any app-token validate. Prefer the per-bearer handler or omit it.
- m-F. Test 1 asserts a 200 "signed-in page" and exactly one exchange. Add that the token POST's form carries no `iss` and that the test checks `code`/`state` are not echoed in the success body. The second is already a goal of I6, and the first is just a sanity check. Optional.

## Regression and scope check

- No scope creep. All changes remain text-only supporting work for I1-I6. Test-file changes are limited to test_server.py and test_oauth_units.py (§6.4 final paragraph), and `wired()`/`running_app()` are untouched.
- I-D1 and I-D2 fidelity is intact. The security reasoning and the gate analysis are unchanged: tests/test_gate.py only matches `R\d+`.
- Source, CLAUDE.md and CHANGELOG plans are unchanged from round 2, and the line refs I re-checked (oauth.py 11, 94, 128, 138, 168, 183, 833; security.py:9; CHANGELOG.md:38) are correct.

## Required before approval

Fix M-B (one sentence in §6.4 plus tasks G). Preferably also m-E. I-D2 still needs the human's confirmation at the gate.
