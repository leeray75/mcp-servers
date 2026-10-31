# Review round 1: scope-aware 401 and per-class Helix circuits (C1-C10)

Reviewer: read-only, fresh context. Read SPEC, tasks.md, CLAUDE.md, AGENTS.md section 7, the implementation report and the full source diff.

## Gate
`make check` (once): ruff, mypy --strict clean; 1024 passed; branch coverage 91.55% (gate 90%). Exit 0.

## Requirement verification (by code reading)
- C1: `_SCOPE_BEARING_KEYS` adds `message` in the required order; `_scope_phrase` is called only in the 401 and 403 branches of `_map`; `message` never sets `kind` elsewhere. No scope table (D10). OK.
- C2: scope 401 checked before any trip, raises `_permission_error` for reads and writes; control chars replaced by one space. OK.
- C3/C4/C5: per-class dicts, `auth_failed(token_type)`, `trip_auth(token_type)`, listener fires once per class, raising listener swallowed. Class hints from `circuit_hint` are used only on the read 401 raise (when `trip_circuit`) and the `acquire` refusal for reads; the write acquire passes `class_hint=False`. OK.
- C6: non-scope write 401 -> `_unknown_outcome` with its unchanged R16 hint after tripping the write's class; scope write 401 -> permission, no trip. OK.
- C7: `reset_user_circuit` clears USER only, no listener call; wired via `oauth.on_invalidate` in `create_app`; app circuit never reset. OK.
- C8: wiring `auth_failed` reads APP_CLASS only; `service.auth_failed` and client property removed. OK.
- C9: `post_form`, `validate_token`, `fetch_validate` acquire with `circuit=False` (still take their bucket); `fetch_validate` uses `trip_circuit=False`; `_validate_pair` uses USER_CLASS bucket. No path leads from an open circuit to `_force_reauth`. OK.
- C10: README table and copy-paste line, and `.env.example` line, match the spec byte for byte (grep matched 2 files). OK.
- C-D10: "This token holds" only when the body has a `scopes` list. OK.
- Invariants: writes still never retry; httpx only in `twitch_client.py`; no new host/method/route; tests network-free; logs carry only the class label and outcome; tool set unchanged.
- Tests: N1-N15 each present per the report's traceability (N1/N2/N3/N7/N8 recorded failing first); changed existing tests keep intent.

## Deviations
- D1 (extra `class_hint` keyword on `acquire`): acceptable; required to satisfy C3/C6 and the spec cannot otherwise be met. 
- D2 (`NEUTRAL_AUTH_HINT` for validate 401 and circuit-refused writes): acceptable; spec says "no circuit hint" and the error class defaults to a restart hint, so a neutral text is the faithful reading.
- D3 (log outcome `permission` for scope 401): acceptable, consistent with 403.
- D4 (reset logs every call): acceptable, matches spec section 6.1.
- D5 (empty `scopes` list renders "holds: ."): follows C-D10 literally; cosmetic, see minor M1.
- D6 (CLAUDE.md service bullet untouched): acceptable; the bullet has no circuit text.
- Skipped tasks.md K assertion (no-circuit check in `test_missing_scope_becomes_permission_carrying_twitchs_own_string`): acceptable; N1/N2 assert exactly this on the same path, and the report discloses it. Not a blocker.

## Findings
Blockers: 0. Majors: 0. Minors: 3.
- M1 (minor): an empty `scopes` list yields the odd sentence "This token holds: ." Consider omitting when the list is empty or saying "no scopes". Not covered by a test.
- M2 (minor): scope names from the body `scopes` list are not control-character-sanitised, unlike the `message` text (C2 covers the scope sentence only; low risk since Twitch supplies it).
- M3 (minor): the `_permission_error` docstring still says the hint is lifted from `error` / `error_description` only; should mention `message`.

## Verdict
APPROVED
