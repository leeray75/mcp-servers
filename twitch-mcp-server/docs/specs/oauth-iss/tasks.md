# Tasks: optional OAuth `iss` (`docs/specs/oauth-iss/SPEC.md`, I1–I6)

Status: Draft. Nothing here starts until the human approves the SPEC in chat. Tick a box only when its acceptance
check has run and passed.

- [ ] A. Approval: human approves SPEC (I-D1, I-D2) in chat; header set to Approved.
- [x] B. `oauth.py`: change the `iss` check in `PendingStore.claim` to `if iss is not None and not _same(...)`
  (line ~183). Comment-only wording to "checked when present" at lines ~11, ~94, ~128, ~138, ~168 (add), ~833. [I1, I2, I3]
- [x] C. `security.py` line 9: "the ``iss`` check" becomes "the ``iss`` check when present". [R47]
- [x] D. `server.py`: confirm `oauth_callback` passes `params.get("iss")` unchanged; no behaviour change. [I6]
- [x] E. `tests/test_oauth_units.py`: add `_callback(flow, pending, *, state=_PENDING_STATE, iss=None, code="code-1")`
  helper with a sentinel for `state` and `iss` omitted by default; move all 10 `flow.complete(` call sites to it. Switch
  the happy-path test (lines ~483–511) to no `iss` and cite I2. Add cases (b) correct `iss`, (c) wrong `iss`,
  (d) empty-string `iss`. [I1–I5]
- [x] F. Confirm the happy-path unit test and HTTP test 1 fail against the unfixed `oauth.py:183` (one targeted run or
  reading). [m4]
- [x] G. `tests/test_server.py`: two NEW tests next to the R47 test (~line 614), using `running_app(...)` with the default
  `connected=False` and no change to `wired()` or `running_app()`. Settings via `server_settings(twitch_user_oauth=True,
  twitch_oauth_token_file=str(tmp_path / "twitch_user_token.json"))` (absolute path, config.py:214). Each registers its own
  `fake.add` handler before the first callback: a `POST /oauth2/token` handler branching on the form `grant_type`
  (`client_credentials` returns the app token; `authorization_code` returns a user-token body that includes `user_id`
  and `login`). No validate handler (exchange skips `_resolve_user` when `user_id` is present, oauth.py:634). Count
  `authorization_code` POSTs by form body, not all token POSTs.
  - `test_oauth_callback_completes_without_iss` (I2): 200, one `authorization_code` POST.
  - `test_oauth_callback_refuses_wrong_iss` (I1): 400, zero new `authorization_code` POSTs, flow dropped.
- [x] H. `CLAUDE.md` OAuth (R46–R51) invariants bullet: "an `iss` check" becomes "an `iss` check when Twitch supplies one". [R47]
- [x] I. `CHANGELOG.md`: create `[Unreleased]` → `Fixed` entry; update line 38 "an `iss` check" wording.
- [x] J. `docs/SPEC.md`: Amendment 4 pointer line (after Amendment 3) and R47 superseded pointer. Remove "pending" on approval.
- [x] K. `make check` passes (ruff, `mypy --strict`, pytest, 90% branch, 30 s per-test timeout).
- [ ] L. Implementation report, review rounds (max 5), summary report under `docs/reports/oauth-iss/`.
