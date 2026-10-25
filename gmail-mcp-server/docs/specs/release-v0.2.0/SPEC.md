# Release v0.2.0 (gmail-mcp-server) — Specification

- **Status:** Approved — 2026-10-25, approved by: human
- **Date:** 2026-10-25
- **Amends:** nothing; follows the sports server's `docs/specs/release-v0.2.0/` pattern.

## 1. Problem / motivation

Two reviewed features sit unreleased in `[Unreleased]` of `CHANGELOG.md`: server-hosted re-authentication
(`docs/specs/reauth/`), `auth_status` plus resources and URL-mode sign-in elicitation (`docs/specs/agent-auth/`), and
the auth guardrails and freshness fields (`docs/specs/agent-auth-hardening/`). The package still reports `0.1.0`, so
`serverInfo.version` is stale (open item 4 of the agent-auth-hardening summary).

## 2. Goals and non-goals

**Goals:** cut v0.2.0 metadata and fix documentation drift. **Non-goals:** any behaviour, tool, schema, setting or
dependency change; tagging; merging to `main`; pushing (unless asked).

## 3. Decisions

| # | Decision |
|---|---|
| D1 | Version 0.2.0 (semver minor: backwards-compatible additions: new tool, resources, routes, settings). |
| D2 | Branch `release/gmail-mcp-server/v0.2.0`, cut from `feature/gmail-agent-auth-status-elicitation` (08dbb0c), because that work is not yet on `main`. |
| D3 | Same scope as the sports release: metadata + docs only, one commit, SDD reports in this folder. |

## 4. Requirements

- **R1** `pyproject.toml` `version` and `gmail_mcp.__version__` become `0.2.0`; `serverInfo.version` follows.
- **R2** `CHANGELOG.md`: `[Unreleased]` body moves verbatim under `## [0.2.0] - 2026-10-25`; a fresh empty
  `[Unreleased]` stays above; `[0.1.0]` untouched; add compare links only if the file already uses them.
- **R3** Documentation drift fixed: README, `docs/SPEC.md` and `CLAUDE.md` checked against the code (tool/resource
  counts, config table vs `config.py` and `.env.example`, auth section, doc layout list incl.
  `docs/specs/agent-auth-hardening/`), corrected where stale. No new claims beyond what the code does.
- **R4** A test pins that `__version__` equals the `pyproject.toml` version (so they cannot drift again).
- **R5** `make check` passes (ruff, format, mypy --strict, pytest, coverage >= 85%).
- **R6** Reports: `implementation-report.md`, `review-round-N.md`, `summary-report.md` here. One commit; no push, tag or merge.

## 5. Design

Files: `pyproject.toml`, `src/gmail_mcp/__init__.py`, one test in `tests/test_docs_and_config.py`, `CHANGELOG.md`,
`README.md`, `docs/SPEC.md`, `CLAUDE.md`, and this folder.

## 6. Security and invariants

Unchanged: seven tools, four resources, loopback, bearer and Host/Origin guards, secret handling. No `.env` reads.

## 7. Test plan

R1/R4 by the new version test; R2/R3 by reviewer reading the diff against code; R5 by running the gate in WSL.

## 8. Out of scope

Publishing the OAuth consent screen (user action), git tag, GitHub Release, PR creation, Docker image tag changes.

## 9. Open questions

None.
