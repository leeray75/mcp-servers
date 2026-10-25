# Review round 1: agent-auth-hardening

Verdict: **APPROVED**

Verified by reading the diff and running `make check` in WSL: ruff, ruff format, mypy --strict and 397 tests pass; coverage 99.31% (gate 85%).

## Requirement check
- R1: `errors.py:19-34` covers shell open command or copy-and-paste link, no browser automation, no credential entry, and retry only on `ready_to_retry: true`. Measured length is 691 chars (limit 700) for the rejected reason at port 8765. There is exactly one bare URL, followed by a space; the other occurrences are quoted in the open commands. Tests are in `tests/test_errors.py`.
- R2: `resources.py` guide has the who-opens-the-page rule, the guardrails, the state list, and `ready_to_retry` and `checked_at`.
- R3: `auth_state.py` adds `checked_at` and `ready_to_retry` and the `sign_in_in_progress` state. The precedence rule R3.3 is correct: pending replaces only non-`signed_in` states, and `signed_in` is untouched. `next_step` is split by state. No state value, verifier or timing is exposed. The tool and the resource use the same model.
- R4: all four resources have descriptions (`server.py`).
- R5: `PendingStore.has_pending` purges expired attempts under the existing lock, so it is thread-safe. The store is shared: `create_app` builds one `PendingStore` and passes it to both `create_server` and `SignInService`. `create_server` defaults to a fresh in-memory store. Nothing is persisted.
- R6: README, CHANGELOG `[Unreleased]`, `docs/SPEC.md` and `CLAUDE.md` are updated and consistent.
- Invariants: still seven tools and four resources, no new routes, no new network use, and no extra scope beyond the spec.

## Issues
No blockers or majors.

- minor: `auth_state.py` `compute_auth_status`. The `REJECTED_CAUSES_WARNING` is now keyed on `rejection is not None` instead of `state == "rejected"`. This is a sensible way to handle the warning: it stays visible while a re-sign-in is in progress. It is a small behavioural change; mention it in the summary report. The tests cover it.
- minor: `errors.py`. The message is 691 of 700 chars, so the margin is thin. Keep the parametrized length test (port and reason variants) as the guard against future wording growth.
