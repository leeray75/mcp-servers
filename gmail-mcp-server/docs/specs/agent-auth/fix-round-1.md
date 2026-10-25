# Fix round 1: agent-auth

Responds to `docs/specs/agent-auth/review-round-1.md` (VERDICT: CHANGES REQUESTED; 0 blockers, 1 major, 3 minors).
Date: 2026-10-25. No spec requirement was changed. D1, D2, D4 and D6 stay pending a human decision, as instructed,
and their code is unchanged.

## Triage

| Finding | Decision | What changed (file:symbol) | Tests added |
|---|---|---|---|
| **M1** major: a `rejected` state outlives a later sign-in (including an out-of-process `auth_cli` / `scripts/auth` sign-in that never calls `GmailClient.reset`) or a later successful Gmail call | **Fixed**, both suggested mechanisms | `auth_state.py:_effective_rejection` (new; a rejection older than the token file's mtime is ignored), `_token_mtime` (new), `compute_auth_status` (applies it first, so `rejected_at` reports the effective value). `gmail_client.py:GmailClient._execute` (a successful `operation` calls `_clear_rejection`), `_clear_rejection` (new) | `tests/test_rejection_recovery.py`: `test_sign_in_after_the_rejection_clears_it` (out-of-process re-auth), `test_rejection_newer_than_the_token_still_applies`, `test_rejection_without_a_token_file_still_applies`, `test_a_successful_call_clears_the_rejection`, `test_http_401_then_success_clears_too` |
| **m1** minor: race can re-record a rejection after `reset()` | **Fixed** (structural, not only the mtime check; see note below) | `gmail_client.py:GmailClient.reset`: the service reset and the rejection clear now happen in one critical section under `_lock` (lock order is always `_lock` then `_state_lock`, the same order `_record_rejection` already uses under `_execute`). Docstring explains the ordering | `tests/test_rejection_recovery.py::test_reset_waits_for_an_in_flight_call_and_clears_its_rejection` (holds `_lock` as an in-flight call would, asserts `reset()` blocks behind it, then clears the rejection recorded inside it) |
| **m2** minor: `token_age_days` measures file mtime, not sign-in time | **Accepted with justification.** R1.1 specifies the token file's mtime, and the review says the behaviour matches the spec. A rewrite that is not a sign-in only changes the age warning, which is harmless. No doc change, to keep scope tight | none | none |
| **m3** minor: README console labels ("Audience", "Publish app") unverified | **Accepted with justification.** Verifying them needs access to the live Google Cloud console, which this environment does not have. The reviewer's own recommendation is that the human confirms them on first use. The item stays flagged in the implementation report (D5) | none | none |

### Note on m1 and the reviewer's mtime-only suggestion

The reviewer suggested the mtime comparison "also covers" the race. It does not, on its own. A rejection recorded
after `reset()` by a call that used old credentials carries a timestamp later than the token mtime, so the mtime
check keeps it. The race is closed by making the clear atomic with the service reset under `_lock`, as implemented
above. The mtime check remains for the out-of-process case, where no in-process reset happens at all.

## Behaviour change to existing tests

Four existing tests (in `test_auth_state.py` and `test_auth_status_and_resources.py`) used a fixed rejection time
(2026-10-25 09:30 UTC and 08:00 UTC). The token file they write is stamped with the current time, so under M1 that
rejection is correctly stale. The tests now take the rejection time after the token is written
(`_rejection()` helper, and `datetime.now(UTC) + 5 s`). Their assertions are otherwise unchanged. No test was
weakened: the rejected precedence and reporting are still asserted.

## make check

Run in WSL venv `~/.venvs/gmail-mcp` (`make check VENV=$HOME/.venvs/gmail-mcp`):

- ruff check: all checks passed; ruff format: 63 files already formatted
- mypy --strict: success, no issues in 37 source files
- pytest: **372 passed** (was 366 at the end of the implementation round); coverage **99.30%** (gate 85%, branch on)
- Uncovered lines unchanged from the review: `server.py:283,293,310` (pre-existing tool return lines) and
  `config.py:36`, `parsing.py` (pre-existing)

No dependency files changed, so `make audit` is unaffected. Nothing committed or pushed.

## Files touched this round

| File | Why |
|---|---|
| `src/gmail_mcp/auth_state.py` | M1: `_token_mtime`, `_effective_rejection`; `compute_auth_status` applies the effective rejection |
| `src/gmail_mcp/gmail_client.py` | M1/m1: `_clear_rejection`; successful call clears; `reset` clears atomically under `_lock` |
| `tests/test_rejection_recovery.py` (new) | Tests for M1 (out-of-process re-auth, success clears, 401-then-success) and m1 (reset race) |
| `tests/test_auth_state.py` | Rejection timestamps moved to call time (`_rejection()`) so they are newer than the token written in the test |
| `tests/test_auth_status_and_resources.py` | Same timestamp change for the client-recorded rejection test; `timedelta` import |
| `docs/specs/agent-auth/fix-round-1.md` (new) | This report |

No code or doc change for m2, m3 (accepted). README, CHANGELOG, CLAUDE.md and `docs/SPEC.md` are unchanged:
the user-visible wording is still accurate, and the stale-rejection handling is internal behaviour.

## Not actioned (for the orchestrator)

The reviewer's deviation recommendations are spec amendments, not code fixes, and stay pending the human:
D1 (drop `lastModified` from R3.2), D2 (reword R4.2 to "exactly one bare URL token"), D3 (note the SDK prefix in
R4.1), D4 (state that 2025-11-25 is dormant under stateless mode, or remove the branch), D6 (note the two-line text
rendering). The spec file under `docs/specs/agent-auth/` was not edited.
