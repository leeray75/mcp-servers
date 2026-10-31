# Spec review, round 4: docs/specs/oauth-iss

Reviewer: read-only. Verified against SPEC.md, tasks.md and the real code (config.py, oauth.py, server_fakes.py), not the fix report.

**Verdict: APPROVED** (0 blockers, 0 major, 2 minor; both optional text fixes)

## Round 3 findings: status

- M-B (token file): fixed and verified.
  - `server_settings(**overrides)` (server_fakes.py:83-103) forwards overrides to `make_settings`, so `twitch_oauth_token_file=str(tmp_path / ...)` works.
  - config.py:213-216 requires the path to start with `/`. The spec states this, and tests run under WSL/Linux.
  - A successful exchange calls `install` -> `store.save` (oauth.py:569-575, :388-411), which uses `_write_private` at 0600 (oauth.py:333-357). The mode-0600 assertion on the file under `tmp_path` is therefore meaningful on the Linux filesystem.
  - Test 2 refuses at `claim`, before `exchange`, so no file is written. "No token file under tmp_path" is correct.
- m-E (validate handler): dropped, correctly. `exchange` skips `_resolve_user` when the body's `user_id` is a non-empty string (oauth.py:634; `build_token` reads `body["user_id"]` at :279). No validate call occurs on the success path. `install` only invalidates caches (oauth.py:575).
- m-F: the sanity assertions are added. They are cheap and in scope for I6.
- The body field list matches `build_token` (oauth.py:269-278): `access_token`, `refresh_token`, `token_type` equal to "bearer" (case-insensitive), `scope` as a string, and `expires_in` as a positive int. The spec lists all of them.

## Regression and scope check

None. Everything is text-only. Test-file changes stay in test_server.py and test_oauth_units.py. `wired()` and `running_app()` are untouched. I-D1 and I-D2 fidelity is intact (I-D2 is still flagged "derived, human confirms at the gate"). Source, CLAUDE.md and CHANGELOG plans are unchanged from round 3, and the previously verified line refs are still accurate. Section 12 and the docs/SPEC.md:9 pointer match. tests/test_gate.py cannot see I-requirements.

## Minor (optional)

- m-G. The spec says the `authorization_code` body carries `login` (SPEC.md ~line 126, tasks G). `build_token` reads `user_login` (oauth.py:280), so `login` is silently ignored and the stored `user_login` is "". The cited unit grant body (test_oauth_units.py:497) also uses `login`. Harmless for these tests. Either say `user_login`, or note `login` is optional and unused.
- m-H. SPEC.md:119 "the tests run under WSL/Linux, where `tmp_path` is absolute" is fine, but add that the 0600 assertion is Linux-only, as the mode bits are not meaningful on the Windows drive. The existing test_oauth_units.py:511 assertion has the same assumption, so no new risk.

## Before approval

Nothing is required. The human must still confirm I-D2 at the gate and give explicit approval (AGENTS.md section 5).
