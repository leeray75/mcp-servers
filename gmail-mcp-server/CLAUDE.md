# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Process: Spec-Driven Development is mandatory.** Changes to this server follow the SDD workflow in
> [`../AGENTS.md`](../AGENTS.md): plan with the human, write a spec, get explicit approval, then implement, review
> (fix loop, max 5 rounds) and write a summary report. This file stays authoritative for this server's technical
> invariants; AGENTS.md governs process.

## What this is

A minimal, self-owned MCP server (Python 3.12, official `mcp` SDK, Google client libs) giving an agent read access to Gmail plus draft creation. Streamable HTTP on 127.0.0.1 behind a bearer token, run in Docker inside WSL2. `docs/SPEC.md` is the authoritative spec; `docs/PLAN.md` and `docs/reports/` hold the build plan and review rounds. Later features each have a folder `docs/specs/<feature>/` (`SPEC.md`, `tasks.md`, `workflow.md`, implementation/review/fix/summary reports), as in the sports server: `docs/specs/reauth/` (server-hosted re-authentication) `docs/specs/agent-auth/` (`auth_status`, resources, URL-mode sign-in elicitation), `docs/specs/agent-auth-hardening/` (guardrails, `ready_to_retry`, `checked_at`, `sign_in_in_progress`), `docs/specs/container-dns-resilience/` (DNS resolvers, healthcheck DNS probe) and `docs/specs/network-failure-diagnostics/` (`NETWORK_UNREACHABLE`, the `network` field; amends the "pure / no network" statements above) and `docs/specs/release-v0.2.0/` (this release).

## Commands

All via the Makefile (uses `.venv`):

- `make install` — create venv from hash-pinned `requirements-dev.txt`, editable install
- `make check` — lint + typecheck + test (what CI would run)
- `make lint` / `make format` — ruff check + format (line length 100)
- `make typecheck` — `mypy --strict`
- `make test` — pytest with a coverage gate (≥ 85%, branch coverage enabled)
- Single test: `.venv/bin/pytest tests/test_tools.py::test_name --no-cov` (`--no-cov` avoids the coverage gate failing on a partial run)
- `make lock` — re-resolve and re-hash `requirements.txt` / `requirements-dev.txt` with uv (never hand-edit them); `make audit` runs pip-audit

Operating the container: `scripts/{setup,start,stop,restart,rebuild,auth,logs,status}.sh`, each with a `.ps1` twin that forwards into WSL2. First run is `setup` (interactive; writes `.env`, reads secrets silently, can generate `MCP_AUTH_TOKEN`) → `auth` (one-time Google sign-in) → `start`. `restart --recreate` is needed after editing `.env`. `.env` holds real credentials and is git-ignored: never print, log or commit its values. `setup.sh` has only had a partial test run; shared helpers live in `scripts/lib/common.sh`.

## Architecture

Package `src/gmail_mcp/`:

- `__main__.py` — entry point: loads `config.Settings` (pydantic-settings, env-driven), sets up logging, builds the app, runs uvicorn.
- `server.py` — builds the `MCPServer` app: registers exactly seven tools and the four static auth/guide resources with schemas/descriptions, wraps the tools with error handling (and, for sign-in failures, URL-mode elicitation when the request allows it), adds `/healthz`, and applies the Starlette middleware. Intentionally has **no** `from __future__ import annotations` because tool signatures embed runtime values (the result cap) in `Annotated` metadata that the SDK reads for JSON schema generation.
- `tools.py` — tool implementations; `gmail_client.py` — thin wrapper over the Gmail API service (the only module calling the Gmail API; OAuth endpoints are contacted only by `auth.py`/`signin.py`/`auth_cli.py`, and `netcheck.py` only resolves DNS); `parsing.py` — MIME/header/body extraction and truncation; `models.py` — pydantic input/output types and validators.
- `netcheck.py` — DNS probe (`resolve_hosts`, stdlib plus `httplib2` for the error types) for `gmail.googleapis.com` and `oauth2.googleapis.com`; resolves names only (no HTTPS, no connect), returns booleans and exception class names, never addresses, never raises. `is_dns_failure(exc)` classifies a Gmail-call exception (cause/context/args chain, bounded to 8, cycle-safe) so `gmail_client._execute` raises `NetworkUnreachableError`. `healthcheck.py` — `python -m gmail_mcp.healthcheck`, the container healthcheck: `/healthz` 200 plus DNS ok; does not import `config`, reads no secrets.
- `security.py` — `BearerAuthMiddleware` (constant-time compare; only `GET`/`HEAD` on the exact paths `/healthz`, `/auth`, `/oauth2callback` are unauthenticated) and `HostGuardMiddleware` (Host/Origin loopback check, DNS-rebinding defense).
- `signin.py` — server-hosted sign-in: `GET /auth` (state + PKCE, bounded expiring in-memory pending store) and `GET /oauth2callback` (exchange off the event loop, scope check, atomic token write, `GmailClient.reset()`); static script-free pages with security headers. The sign-in link in auth errors is built from `HOST_PORT` (`errors.auth_url`, `errors.auth_required_message`).
- `auth_state.py` — sign-in state for `auth_status` and `gmail://auth/status`: `compute_auth_status` is pure and synchronous (reads only token metadata/scopes via `auth.read_token`, does no I/O of its own, never returns secrets). The DNS result is measured by `server.py` (`probe_network`: `netcheck.resolve_hosts(PROBE_HOSTS, timeout=2.0)`, run off the event loop, a timeout or error is `dns_failure`, never raises) and passed in as `network`. States are `signed_in`, `not_signed_in`, `token_unreadable`, `scopes_insufficient`, `rejected` and `sign_in_in_progress` (an unexpired pending sign-in attempt replaces every problem state, never `signed_in`). Fields: `state`, `signed_in`, `network` (`ok` or `dns_failure`), `ready_to_retry` (true only when `signed_in` and `network` is `ok`), `checked_at` (UTC, recomputed on every call and read), `sign_in_url`, `scopes_granted`, `scopes_missing`, `token_age_days`, `rejected_at`, `warnings`, `next_step`, `open_commands`. A `dns_failure` adds a restart instruction to `next_step` and a warning. `resources.py` — text of the guide resources (server-authored, no mailbox data); the guardrails for agents live in `gmail://guide/auth`.
- `auth.py` / `auth_cli.py` — OAuth refresh-token storage (`data/token.json`, 0600, atomic writes; access tokens memory-only) and the one-off sign-in flow.
- `logging_setup.py` — JSON logs with a secret-redaction filter (`register_secret`); `errors.py` — `GmailMcpError` mapped to user-facing tool errors.

## Invariants to preserve

- The tool set is exactly `search_threads`, `get_thread`, `get_message`, `list_labels`, `list_drafts`, `create_draft`, `auth_status` (in that order); a test asserts this. `auth_status` is read-only, never raises an auth error, and its only I/O is the bounded DNS probe of the two Google hostnames (amendment `docs/specs/network-failure-diagnostics/SPEC.md` D2; no request is sent to Google and no address is returned).
- DNS failures are reported as `NetworkUnreachableError` (`NETWORK_UNREACHABLE:` text, at most 500 characters, no URL, host or query, never "permanent", tells the agent not to retry in a loop and to call `auth_status`). Its `ready_to_retry` is always false while `network` is `dns_failure`. Never add send/delete/trash/relabel/settings/attachment-download capability. The `gmail.compose` scope technically allows sending, so safety depends on the absent tool and this server being the sole token holder.
- Resources are exactly four static URIs (`gmail://auth/status`, `gmail://guide/auth`, `gmail://guide/search`, `gmail://guide/limits`), no templates, served through the same bearer + Host/Origin guards. Each has a non-empty one-sentence `description` (pinned by a test). Their content never holds mailbox data or credentials.
- The `AUTH_REQUIRED` text (`errors.auth_required_message`) must stay within 700 characters with exactly one bare sign-in URL followed by a space. It carries the guardrails (shell open command or copy-and-paste; no browser automation; no credential entry; retry only on `ready_to_retry: true`). The `/auth` pending store (`PendingStore.has_pending`) is shared between `/auth` and `auth_status`; it stays in memory and is never persisted.
- Sign-in elicitation (R5 of `docs/specs/agent-auth/SPEC.md`) only uses the SDK-defined URL-mode shapes, only when the request declares `elicitation.url`, only for the server's plain `/auth` URL, and only when `AUTH_URL_ELICITATION` is on. The server stays stateless (`stateless_http=True`, JSON): no per-session state is kept for it. Only the 2026-07-28 revision gets the elicitation (`InputRequiredResult`, amendment A1 / D7). The 2025-11-25 `-32042` mechanism is not implemented. Stateless mode does not retain handshake capabilities, so 2025-11-25 clients and requests with unknown capabilities get the R4 `AUTH_REQUIRED` tool error (pinned by `tests/test_mcp_gate.py`).
- Email content is untrusted input to the model; tool descriptions carry that warning (`UNTRUSTED_NOTE`).
- Logs must never contain bodies, subjects, addresses or credentials.
- Output is bounded (`MAX_RESULTS_CAP`, `MAX_BODY_CHARS`, per-thread budget, 50 messages/thread, 512 KiB request limit).
- The only unauthenticated routes are `GET`/`HEAD` `/healthz`, `/auth`, `/oauth2callback`, matched exactly and still behind the Host/Origin guard; sign-in pages never reflect request data and logs never contain codes/state/tokens. `auth_status` and the resources require the bearer token; do not add a new unauthenticated route for them.
- Docker port mapping stays bound to `127.0.0.1` (no TLS); container is non-root, read-only rootfs.
- On the `/mnt/c` checkout POSIX file modes are not enforced for `data/token.json`.
