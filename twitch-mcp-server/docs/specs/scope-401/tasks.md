# Tasks: scope-aware 401 and Helix-only per-class circuits (`docs/specs/scope-401/SPEC.md`, C1–C10)

Status: Draft. Nothing here starts until the human approves the SPEC in chat. C-D1(a), C-D1(b), C-D2 and C-D4 (replaced) are
human-decided; C-D3, C-D5, C-D9 and C-D10 must be confirmed at the gate. Tick a box only when its acceptance check has run
and passed.

- [ ] A. Approval: human approves the SPEC and confirms C-D3, C-D5, C-D9 and C-D10; header set to Approved.
- [ ] B. Write failing tests first: N1, N2 (`GET /helix/users` 401/403 with scope text only in `message`; `kind` asserted first),
  N3 (user `get_banned_users` 401 with `message`-only scope text; registers its own `GET /helix/moderation/banned` fake), and
  N7/N8 (real `TwitchClient` and `FakeTwitch`; `UserAuth` wired through `attach_grant`, `attach_validate` (`_validate_pair`) and an
  `on_invalidate` lambda; the circuit tripped by a real Helix `401`; the faked token body has no `user_id`). Record the failures on
  the unfixed code. [C1, C2, C9]
- [ ] C. `twitch_client.py`: `_SCOPE_BEARING_KEYS` (line 79) gains `"message"`; `_scope_phrase` (302–318) replaces control characters
  and newlines; update the module docstring (line 23), the comments at 77–82, the `_error_code` docstring (292–299), and
  `errors.py` line 9. [C1, C2]
- [ ] D. `ratelimit.py`: per-class `_auth_failed` and `_auth_message` (228–230); `auth_failed(token_type)` method (239–241);
  `trip_auth(token_type, *, message)` (255–270); `reset_user_circuit()`; `acquire(..., *, circuit=True)` (352–383), refusing with the
  class hint. [C3, C7]
- [ ] E. `twitch_client.py` `_map` (716–726) gains `trip_circuit`; the `401` branch (757–779) checks the scope first, trips only
  when `trip_circuit`. A read `unauthenticated` gets the class hint (none when `trip_circuit` is false); a write `unknown_outcome`
  keeps `_unknown_outcome`'s R16 hint (700–714). `_permission_error` (852–875) adds "This token holds" only with a `scopes` list.
  [C2, C3, C6, C-D10]
- [ ] F. `twitch_client.py`: `post_form` (967), `validate_token` (1046) and `fetch_validate` (1091) acquire with `circuit=False`;
  `fetch_validate` (1085) passes `trip_circuit=False` at its `_map` call (1107); remove the `auth_failed` property (384–386).
  [C9, C-D9]
- [ ] G. `service.py`: remove the `auth_failed` property (384–386). `twitch_account_status` is unchanged. [C8]
- [ ] H. `server.py`: wiring `auth_failed` (1509–1511) returns `self.service.limiter.auth_failed(APP_CLASS)`; `/healthz` (1756)
  keeps its shape; add `oauth.on_invalidate(lambda: service.limiter.reset_user_circuit())` beside line 1744; `_validate_pair` (1853)
  passes `token_type=USER_CLASS`. [C7, C8]
- [ ] I. Tests: N4 (per-class isolation both directions, client level), N7 and N8 (real wiring, failing-first, see B), N9 (install
  does not clear the app circuit), N10 (per-class circuits and listener once per class), N11 (hint sanitising), N6 (app circuit
  refuses app Helix reads with no request), N13 (write `401`: non-scope → `unknown_outcome`, the R16 "Do not re-issue" hint, and the
  user circuit; scope → `permission`, no circuit), N14 (guard cases, not failing-first), N15 (client level: with both circuits open,
  `fetch_validate` and `post_form` still send requests; a validate `401` on a closed circuit raises `unauthenticated` and trips
  nothing). [C3–C9]
- [ ] J. Tests: N12 (`/healthz` ok with only the user circuit open, degraded with the app circuit open). [C8]
- [ ] K. Update the existing R24/R45 tests named in SPEC §8 (`test_twitch_client.py` 101–116; `test_ratelimit.py` 430–456,
  459–470, 485–511; `test_identity.py` line 546 only, to `service.limiter.auth_failed(USER_CLASS)`; `test_service.py` 957–973
  no-circuit check). No test is weakened. [C3]
- [ ] L. Docs (applied with the code): `README.md` §4 scopes (232–257) with the C10 table and copy-paste line; circuit text
  (161, 315, 368–369, 392); `.env.example` 74–80; `CLAUDE.md` circuit invariant (131–132) and bullets (68, 62, 92);
  `docs/SPEC.md` superseded markers at R24 (~185, with the C-D9 sentence), R44 (~248), R45 (~249), §6.3 (~347–358);
  `CHANGELOG.md` `[Unreleased]` `### Fixed` entry. [C10]
- [ ] M. Gate: `make check` (ruff, `mypy --strict`, pytest) passes at ≥90% branch coverage with the 30 s per-test timeout kept.
  Report commands and results in `docs/reports/scope-401/implementation-report.md`.
- [ ] N. Review loop (reviewer subagent, max 5 rounds) and `docs/reports/scope-401/summary-report.md`. Nothing committed
  unless the human asks.
