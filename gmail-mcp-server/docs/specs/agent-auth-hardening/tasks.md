# Agent auth hardening: tasks

Spec: `docs/specs/agent-auth-hardening/SPEC.md` (Approved). Status: all tasks done, `make check` passes.

- [x] R5: `PendingStore.has_pending()` with expiry purge (`src/gmail_mcp/signin.py`).
- [x] R3: `AuthStatus` gains `ready_to_retry`, `checked_at`; `sign_in_in_progress` state; `sign_in_pending` parameter; new `next_step` wording (`src/gmail_mcp/auth_state.py`).
- [x] R5/R4: share the pending store between `/auth` and the tool/resource; pass the flag; add resource descriptions (`src/gmail_mcp/server.py`).
- [x] R1: new `AUTH_REQUIRED` wording, 691 characters at worst (`src/gmail_mcp/errors.py`).
- [x] R2: guardrails, who-opens rule, and field descriptions in `gmail://guide/auth` (`src/gmail_mcp/resources.py`).
- [x] Tests: update `test_errors.py`, `test_auth_state.py`, `test_auth_status_and_resources.py`, `test_signin.py`, `test_docs_and_config.py`; add R1 to R6 coverage.
- [x] Docs: README auth section, CHANGELOG `[Unreleased]`, `docs/SPEC.md`, `CLAUDE.md`.
- [x] Gate: `make check` (ruff, format, mypy --strict, pytest, 85% branch coverage).
- [ ] Container rebuild and live `auth_status` check (outside this implementation pass; see the report).
