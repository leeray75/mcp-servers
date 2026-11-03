# Implementation review, round 1: docs/specs/oauth-iss

Reviewer: read-only. Verified against SPEC.md I1-I6, the git diff and a fresh `make check`.

**Verdict: APPROVED** (0 blockers, 0 major, 2 minor)

## Verification
- oauth.py: the only logic change is `claim`: `if iss is not None and not _same(iss, flow.iss)`. Order stays no-flow, expiry, state, iss. `_same` is kept, so the compare is constant-time. Empty string is "present" and refused (I1, I2, I3). State, TTL and single-use code are untouched (I4, I5). No new log or echo of iss/code/state (I6). The other edits (oauth.py, security.py) are docstring/comment only.
- Unit tests: helper `_callback` omits iss by default (all 10 call sites moved; wrong-state case keeps `state=`). Cases for absent, correct, wrong (+ second claim is NO_FLOW) and empty iss exist. The happy-path test is switched with an I2 comment.
- HTTP tests: both new tests are as specified (tmp_path token file, exactly one authorization_code POST, no iss in the exchange, 0600 file with a Linux-only comment (m-H), no echo, zero exchanges and no file on wrong iss, dropped flow on retry). They use `user_login` (m-G applied).
- Fail-before-fix evidence is recorded in the implementation report (2 tests fail against the old code).
- Docs: CLAUDE.md, CHANGELOG (Fixed entry and line-38 text), docs/SPEC.md Amendment 4 header and R47 pointer. All minimal and correct. Nothing outside the spec; server.py unchanged, as the spec says.
- Gate: `make check` green (ruff, mypy --strict, 975 passed, coverage 90.84% vs 90% floor).

## Deviations
- Extra store-level absent-iss unit case: acceptable, within I2.
- `login` left in the unit body: harmless; that test does not depend on it.
- Handler registered after boot, so the boot mint gets 404 and boot is degraded: acceptable. It matches the spec's "register before first callback" ordering, the callback does not use the app token, and the tests assert the right outcomes. (Registering the handler before boot would be cleaner but is not required.)

## Minor
- m1. CLAUDE.md OAuth bullet line now exceeds the wrapped width (cosmetic reflow).
- m2. The `connected=False` boot-degraded side effect is only documented in the implementation report; consider a one-line comment in `_register_token_exchange`.
