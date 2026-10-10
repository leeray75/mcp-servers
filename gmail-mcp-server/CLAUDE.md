# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Process: Spec-Driven Development is mandatory.** Changes to this server follow the SDD workflow in
> [`../AGENTS.md`](../AGENTS.md): plan with the human, write a spec, get explicit approval, then implement, review
> (fix loop, max 5 rounds) and write a summary report. This file stays authoritative for this server's technical
> invariants; AGENTS.md governs process.

## What this is

A minimal, self-owned MCP server (Python 3.12, official `mcp` SDK, Google client libs) giving an agent read access to Gmail plus draft creation. Streamable HTTP on 127.0.0.1 behind a bearer token, run in Docker inside WSL2. `docs/SPEC.md` is the authoritative spec; `docs/PLAN.md` and `docs/reports/` hold the build plan and review rounds. The server-hosted re-authentication feature has its own plan/spec in `docs/reauth/` and reports in `docs/reports/reauth/`.

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
- `server.py` — builds the `MCPServer` app: registers exactly six tools with schemas/descriptions, wraps them with error handling, adds `/healthz`, and applies the Starlette middleware. Intentionally has **no** `from __future__ import annotations` because tool signatures embed runtime values (the result cap) in `Annotated` metadata that the SDK reads for JSON schema generation.
- `tools.py` — tool implementations; `gmail_client.py` — thin wrapper over the Gmail API service; `parsing.py` — MIME/header/body extraction and truncation; `models.py` — pydantic input/output types and validators.
- `security.py` — `BearerAuthMiddleware` (constant-time compare; only `GET`/`HEAD` on the exact paths `/healthz`, `/auth`, `/oauth2callback` are unauthenticated) and `HostGuardMiddleware` (Host/Origin loopback check, DNS-rebinding defense).
- `signin.py` — server-hosted sign-in: `GET /auth` (state + PKCE, bounded expiring in-memory pending store) and `GET /oauth2callback` (exchange off the event loop, scope check, atomic token write, `GmailClient.reset()`); static script-free pages with security headers. The sign-in link in auth errors is built from `HOST_PORT` (`errors.auth_hint`).
- `auth.py` / `auth_cli.py` — OAuth refresh-token storage (`data/token.json`, 0600, atomic writes; access tokens memory-only) and the one-off sign-in flow.
- `logging_setup.py` — JSON logs with a secret-redaction filter (`register_secret`); `errors.py` — `GmailMcpError` mapped to user-facing tool errors.

## Invariants to preserve

- The tool set is exactly `search_threads`, `get_thread`, `get_message`, `list_labels`, `list_drafts`, `create_draft`; a test asserts this. Never add send/delete/trash/relabel/settings/attachment-download capability. The `gmail.compose` scope technically allows sending, so safety depends on the absent tool and this server being the sole token holder.
- Email content is untrusted input to the model; tool descriptions carry that warning (`UNTRUSTED_NOTE`).
- Logs must never contain bodies, subjects, addresses or credentials.
- Output is bounded (`MAX_RESULTS_CAP`, `MAX_BODY_CHARS`, per-thread budget, 50 messages/thread, 512 KiB request limit).
- The only unauthenticated routes are `GET`/`HEAD` `/healthz`, `/auth`, `/oauth2callback`, matched exactly and still behind the Host/Origin guard; sign-in pages never reflect request data and logs never contain codes/state/tokens.
- Docker port mapping stays bound to `127.0.0.1` (no TLS); container is non-root, read-only rootfs.
- On the `/mnt/c` checkout POSIX file modes are not enforced for `data/token.json`.
