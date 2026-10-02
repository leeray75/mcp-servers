# Gmail MCP Server — Implementation Plan

## Goal

A small, self-owned MCP server that gives an AI agent (Goose) **read access to
Gmail and the ability to create drafts** — and nothing else. It exists because
Google's hosted Gmail MCP endpoint requires Developer Preview enrollment, and
the owner does not want to hand credentials to unaudited third-party MCP code.
Only Google's official client libraries and the official `mcp` SDK may touch
OAuth tokens or mail content.

## Decisions (confirmed with the owner)

| Topic | Decision |
|---|---|
| Language / SDK | Python 3.12 + official `mcp` Python SDK (FastMCP), `google-api-python-client`, `google-auth`, `google-auth-oauthlib` |
| Transport | Streamable HTTP, container published on `127.0.0.1` only |
| Capabilities | Read + create drafts. **No send tool, no delete/modify/trash tools.** |
| Auth flow | Separate one-time `auth` script; refresh token stored in a git-ignored local volume; server never opens a browser |
| Runtime | Docker (Docker Engine inside WSL2 Ubuntu — not Docker Desktop) |
| Host OS | Windows 11; scripts must work from WSL/Git Bash (`.sh`) with PowerShell (`.ps1`) wrappers |

## Scope caveat to document honestly

The `gmail.compose` OAuth scope technically permits *sending*. Google offers no
"drafts only" scope. Safety therefore rests on (a) the server exposing no send
tool and (b) the server being the only holder of the token. This must be stated
in the README's security section.

## Phases

1. **Scaffold** — repo layout, `pyproject.toml`, tooling config, `.gitignore`,
   `.dockerignore`, `.env.example`, git init.
2. **Core** — config (pydantic-settings), structured logging, Google auth/token
   loading and refresh, Gmail service wrapper.
3. **Tools** — MCP tools per SPEC §5 with strict input validation and bounded outputs.
4. **HTTP hardening** — bearer-token middleware, localhost bind, health endpoint,
   request-size/limit caps.
5. **Container** — multi-stage non-root Dockerfile, `docker-compose.yml`,
   healthcheck, read-only root FS, dropped capabilities.
6. **Scripts** — `start`, `stop`, `restart`, `rebuild`, `auth`, `logs`, `status`
   (`.sh` canonical + `.ps1` wrappers) in `scripts/`.
7. **Tests** — unit tests with mocked Gmail API; MCP-level tests of tool schemas
   and auth middleware; no test touches the network or real credentials.
8. **Docs** — README (setup, Google Cloud steps, Goose config, security model,
   troubleshooting), `docs/SPEC.md`, CHANGELOG.
9. **Review loop** — independent reviewer; implementer fixes; max 3 rounds.
10. **Summary report** — `docs/reports/summary-report.md`.

## Agent workflow

- **Implementer subagent** builds phases 1–8 from `docs/SPEC.md`, runs the test
  suite, linter and type checker, and attempts a container build if Docker is
  reachable. Writes `docs/reports/implementation-report.md`.
- **Reviewer subagent** (independent, read-mostly) reviews against the SPEC and a
  security/quality checklist; writes `docs/reports/review-round-N.md` with
  numbered findings tagged `BLOCKER / MAJOR / MINOR`. It does not edit code.
- **Loop:** if any BLOCKER or MAJOR finding exists, the implementer fixes them
  and the reviewer re-reviews. Maximum **3** review rounds. Remaining findings
  after round 3 are reported, not silently dropped.
- The orchestrator (main session) then writes the summary report.

## Constraints on agents

- Never read, print, or ask for the real client secret or any token. Use fakes
  in tests and `.env.example`.
- Never run `auth`, `start`, or anything that contacts Google with real credentials.
- Never run `git push`. Committing locally is fine.
- Do not weaken security requirements in the SPEC to make tests pass.

## Done criteria

- `pytest`, `ruff check`, `ruff format --check`, and `mypy --strict` pass.
- Image builds (if Docker reachable); container passes its healthcheck with
  fake/absent credentials in "unauthenticated" mode.
- Every SPEC requirement is either implemented or listed as a known gap.
