# Spec fix round 3: docs/specs/oauth-iss (finding → fix)

Responds to `docs/reports/oauth-iss/spec-review-round-3.md` (CHANGES REQUESTED). Markdown only. No source, test,
config, CLAUDE.md or CHANGELOG edits; nothing run; nothing committed.

| Finding | Fix | Where |
|---|---|---|
| M-B: test 1 writes the token file to the default `/data` path, so `TokenStore.save` (oauth.py:388–411) fails and the callback returns 400 | Both new tests build settings with `server_settings(twitch_user_oauth=True, twitch_oauth_token_file=str(tmp_path / "twitch_user_token.json"))`. The path is absolute (config.py:214). Test 1 asserts the file exists with mode `0600`. Test 2 uses the same settings and asserts no token file is written | SPEC §6.4, §8; tasks G |
| m-E: validate handler not needed; `set_validate` is not the same effect | Validate handler dropped. The `authorization_code` body must include `user_id` and `login`, so `exchange` (oauth.py:634) skips `_resolve_user`. `fake.set_validate` is not used | SPEC §6.4; tasks G |
| m-F: optional sanity checks | Added to test 1: the `authorization_code` POST form carries no `iss` key; the success body echoes neither the `code` nor the `state` value. §8 I6 row cites the echo check | SPEC §6.4, §8 |

Unchanged, as the reviewer confirmed: I1–I6 scope, I-D1 and I-D2 wording, the security reasoning, the `docs/SPEC.md`
pointer, the CHANGELOG plan, and the line references (oauth.py 11, 94, 128, 138, 168, 183, 833; security.py:9;
CHANGELOG.md:38).

Open for the human: I-D2 (derived) is still to be confirmed at the approval gate. Open questions: None.
