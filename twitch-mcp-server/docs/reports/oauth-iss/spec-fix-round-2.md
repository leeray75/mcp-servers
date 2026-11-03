# Spec fix round 2: docs/specs/oauth-iss (finding → fix)

Responds to `docs/reports/oauth-iss/spec-review-round-2.md` (CHANGES REQUESTED). Markdown only. No source, test,
config, CLAUDE.md or CHANGELOG edits; nothing run; nothing committed.

| Finding | Fix | Where |
|---|---|---|
| M-A: fake references point into `wired()`; `running_app()` default `connected=False` registers no token or validate route; first registered match wins (`twitch_fakes.py:358–367`) | Removed the `server_fakes.py:174–178` and `~185–200` instructions. `wired()` and `running_app()` are not changed. Each new HTTP test registers its own `fake.add` handlers before the first callback: a `POST /oauth2/token` handler branching on the form `grant_type` (`client_credentials` → app token, `authorization_code` → user-token body), and a `GET /oauth2/validate` handler for the user bearer (or `fake.set_validate`). Test 1 counts `authorization_code` POSTs by form body, because the boot mint is also a `POST /oauth2/token` | SPEC §6.4, §8; tasks G |
| m-A: duplicated `PendingStore` docstring clause | Deleted; one docstring at line ~138 | SPEC §6.1 |
| m-B: `oauth.py:94`, `:128`, `:833` misdescribe `iss`; `security.py:9` quoted wrongly | Added to the comment-only list: 94 (`expected_iss`), 128 (`PendingFlow`), 833 (`OAuthFlow` property), plus 11, 138 and 168 (the 168 claim docstring does not name `iss`, so "``iss`` is checked when present" is added). `security.py:9` quoted exactly: "the ``iss`` check and the 10-minute flow TTL" → "the ``iss`` check when present" | SPEC §6.1; tasks B, C |
| m-C: "about 11" call sites; helper cannot express missing `state` | Counted and listed: **10** `flow.complete(` call sites (lines 321, 343, 362, 382, 407, 432, 455, 475, 479, 506). Helper uses a module-level `_PENDING_STATE` sentinel: default passes `pending.state`, and `state=None` sends a missing `state` | SPEC §6.3; tasks E |
| m-D: unit case (a) overlaps the existing happy-path test | Case (a) is the existing `test_flow_complete_happy_path_installs_token` (`test_oauth_units.py:483–511`), switched to the helper with no `iss` and cited with I2. No duplicate is added | SPEC §6.3, §8; tasks E |

Unchanged, as the reviewer confirmed: I1–I6 scope, I-D1 and I-D2 wording, the security reasoning, the `docs/SPEC.md`
pointer, the CHANGELOG plan, and that `tests/test_gate.py` cannot see I-requirements.

Open for the human: I-D2 (derived) is still to be confirmed at the approval gate. Open questions: None.
