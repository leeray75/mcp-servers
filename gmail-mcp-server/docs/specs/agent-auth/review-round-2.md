# Review round 2: agent-auth

Reviewer: separate read-only pass, fresh context. Spec: `docs/specs/agent-auth/SPEC.md` (Approved). Date: 2026-10-25.
D1, D2, D4, D6 remain pending a human decision and are not raised here.

## Gate (run by reviewer, WSL venv `~/.venvs/gmail-mcp`)

`make check VENV=~/.venvs/gmail-mcp`: ruff lint/format OK, mypy OK, **372 passed**, coverage **99.30%** (gate 85%, branch on).
Uncovered: `config.py:36`, `parsing.py:102,112`, `server.py:283,293,310` (all pre-existing). `auth_state.py`,
`gmail_client.py`, `resources.py` are 100%. No `requirements*`/`pyproject` diff, so `make audit` is unaffected.

## Round-1 findings, verified in code

- **M1 resolved.** Two independent mechanisms exist.
  - `auth_state.py:_effective_rejection` (applied first in `compute_auth_status`) drops a rejection whose timestamp is
    strictly older than the token file mtime. This covers an out-of-process CLI sign-in.
  - `gmail_client.py:121` (`_execute`) clears the record after any successful `operation`.
  - Tests: `test_rejection_recovery.py` covers re-auth after rejection, newer rejection kept, no token file kept,
    success clears, and 401-then-success clears.
- **m1 resolved.** `reset()` (`gmail_client.py:107-109`) now clears the service and the record in one critical section
  under `_lock`. Lock order is `_lock` then `_state_lock` everywhere (`reset`, `_record_rejection`, `_clear_rejection`
  from `_execute`). `rejected_at` takes only `_state_lock`, so `auth_status` / the resource never wait behind a Gmail
  call and cannot deadlock with `_execute`. `_state_lock` is never held while acquiring `_lock`. No cycle, no
  re-entrancy of a non-reentrant lock. The reset-race test genuinely asserts that `reset()` blocks behind an in-flight
  call and that the rejection is gone afterwards.
- **m2 accepted: justification sound.** R1.1 specifies mtime, and the token is only written by sign-in
  (`write_token` is called solely from `signin.py:255` and `auth_cli.py:76`; nothing writes back on refresh), so mtime
  is in practice the sign-in time.
- **m3 accepted: justification sound.** Cannot be verified without the live console; flagged for the human (D5).

## Regression / edge-case analysis

- Equal timestamps: comparison is `rejection < mtime`, so equal keeps the rejection. This errs on the safe side.
- mtime granularity on `/mnt/c` (coarse, up to 2 s on some FS): a rejection recorded within the same granule as, but
  before, a sign-in could be kept for one extra call; the next successful call or the reset hook clears it. Harmless.
- Timezones: both sides are epoch seconds (`datetime.timestamp()` on an aware UTC datetime vs `st_mtime`). No tz bug.
  Host and container share the kernel clock, so no skew issue.
- Token file missing or unreadable: `_token_mtime` returns None and the rejection is kept (tested for missing). An
  unreadable file still reports through `_base_state`. No exception escapes (`OSError` caught).
- False "signed_in" after a dead token: only possible if the token file is rewritten after a rejection without a real
  sign-in (for example restoring a backup). Since the app never rewrites it, and the next failing call re-records the
  rejection, this is not a realistic defect. Recorded as m1 below, informational.
- Success-clears: `_execute` holds `_lock` for the whole call, so calls are serialised and a stale call cannot
  interleave to clear a newer rejection.
- Edited tests: the four tests changed only the rejection timestamp (`_rejection()` = now + 5 s, and
  `now + 5 s` in the resources test) so the rejection is newer than the token the test just wrote. State, `rejected_at`,
  precedence and warnings assertions are intact; nothing was weakened. A 5 s future timestamp is deterministic against
  a token written moments earlier.

## Invariants / scope

- Seven tools unchanged; no new routes (`create_app` unchanged this round); `auth_state.py` and `resources.py` have no
  network imports; status payload holds only booleans, scope names, ages, timestamps, URL and static text; no
  dependency changes. This round touched only `auth_state.py`, `gmail_client.py` and tests (plus the fix report). No
  scope creep.

## Issues

### m1 (minor, informational): a token file touched without a sign-in hides a rejection until the next call
- `auth_state.py:_effective_rejection`. Self-correcting on the next Gmail call; no action required. Could be noted in
  the code comment. Not a blocker.

### m2 (minor): `test_a_successful_call_clears_the_rejection` does not assert the `auth_status` view
- It checks `client.rejected_at` only, which is sufficient for the client contract. The state side is covered by the
  separate tests. Optional.

## Verdict

Blockers: 0. Majors: 0. Minors: 2 (both optional).

VERDICT: APPROVED
