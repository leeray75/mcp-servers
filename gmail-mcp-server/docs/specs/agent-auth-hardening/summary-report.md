# Summary report: agent-auth-hardening

- **Date:** 2026-10-25
- **Spec:** `SPEC.md` (Approved 2026-10-25, approved by: human)
- **Result:** Review round 1 **APPROVED** (0 blockers, 0 majors, 2 minors). Review rounds used: **1 of 5**.
- **Not committed, pushed or tagged.** Changes are in the working tree on `feature/gmail-agent-auth-status-elicitation`.

## 1. Process

| Step | Actor | Output | Result |
|---|---|---|---|
| Incident analysis (Goose report) | Orchestrator | triage of P0-P2 recommendations | Adopted guardrails, freshness, in-progress state; declined server-side browser launch, form elicitation, renewal |
| Plan | Orchestrator + human | decisions D1-D4 | 4 choice questions answered |
| Live check of `resources/list` | Orchestrator | read-only probe of the container | 4 resources returned; only defect: empty descriptions |
| Spec | Orchestrator | `SPEC.md` | Approved |
| Implementation | Implementer (Haiku) | code, tests, docs, `tasks.md`, `implementation-report.md` | `make check` green; 397 tests, 99.31% coverage |
| Review round 1 | Reviewer (Sonnet) | `review-round-1.md` | APPROVED |
| Container rebuild and live check | Orchestrator | rebuilt `gmail-mcp`, probed `resources/list` and `auth_status` | Healthy; new fields and descriptions present |
| Summary | Orchestrator | this file | |

## 2. What was built (by requirement)

| Req | Delivered |
|---|---|
| R1 | `AUTH_REQUIRED` text (691 of 700 chars worst case): run the open command in a shell or give the user the URL to copy and paste; no browser automation; no credential entry; retry only when `ready_to_retry: true`. |
| R2 | `gmail://guide/auth` carries the guardrails, the "who opens the page" rule and the new fields. |
| R3 | `auth_status` adds `checked_at`, `ready_to_retry` and the `sign_in_in_progress` state (replaces only non-`signed_in` states). |
| R4 | One-sentence `description` on all four resources. |
| R5 | `PendingStore.has_pending()` (expiry purge, in memory only); one store shared by `/auth` and `auth_status`. |
| R6 | README, CHANGELOG `[Unreleased]`, `docs/SPEC.md`, `CLAUDE.md` updated. |

## 3. Verification

- `make check` in WSL (ruff, format, `mypy --strict`, pytest, coverage): **397 passed, 99.31%** (gate 85%), run by the implementer and independently by the reviewer.
- Live, after `scripts/rebuild`: `/healthz` 200; `resources/list` returns four non-empty descriptions; `auth_status` returns `state: signed_in`, `ready_to_retry: true`, `checked_at` set, `warnings: []`. `.env` values were never printed.

## 4. Deviations and accepted minors

- No deviations from the spec.
- Minor: the rejected-cause warning stays visible while `sign_in_in_progress` replaces the state (the spec was silent; reviewer judged it sensible).
- Minor: only 9 characters of margin under the 700-character error limit; the parametrized length test guards it.

## 5. Open items for the human

1. The container is currently signed in (`token_age_days` 0.02). The 7-day expiry will recur until the OAuth consent screen is published to Production.
2. Whether Goose declares `elicitation.url` is still unknown; the agent-run or copy-paste path is the working route.
3. The Goose report that `list_resources` was empty could not be reproduced server-side; if it persists, it is a Goose client behaviour.
4. `serverInfo.version` reports `0.1.0` (`pyproject.toml`) while the release is v0.2.0; left out of scope.
5. Changes are uncommitted.

## 6. Files created or updated (`gmail-mcp-server/`)

| File | Change | Why |
|---|---|---|
| `src/gmail_mcp/auth_state.py` | updated | R3: new state and fields, guardrail `next_step` |
| `src/gmail_mcp/signin.py` | updated | R5: `has_pending()` |
| `src/gmail_mcp/server.py` | updated | R4, R5: shared store wiring, resource descriptions |
| `src/gmail_mcp/errors.py` | updated | R1: new `AUTH_REQUIRED` wording |
| `src/gmail_mcp/resources.py` | updated | R2: guide guardrails |
| `tests/test_errors.py`, `test_auth_state.py`, `test_auth_status_and_resources.py`, `test_signin.py`, `test_docs_and_config.py` | updated | R1-R6 coverage |
| `README.md`, `CHANGELOG.md`, `docs/SPEC.md`, `CLAUDE.md` | updated | R6 |
| `docs/specs/agent-auth-hardening/` | created | `SPEC.md`, `tasks.md`, `implementation-report.md`, `review-round-1.md`, `summary-report.md` |
