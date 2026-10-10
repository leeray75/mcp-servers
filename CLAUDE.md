# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Mandatory: Spec-Driven Development.** Every change follows the SDD workflow in [AGENTS.md](AGENTS.md): plan with
> the human (choice questions with a recommended option and "Other"), write a spec, **wait for explicit human approval**,
> then implement with an implementer subagent, review with a separate reviewer subagent (fix loop, max 5 rounds) and
> finish with a summary report. Never write code before the spec is approved. Use AskUserQuestion for choices and the
> Agent tool for the implementer and reviewer subagents.

@AGENTS.md

## What this is

A monorepo of four independent, self-hosted MCP servers. Each lives in its own directory with its own toolchain,
Makefile, Dockerfile, `docker-compose.yml`, `scripts/`, tests and **its own `CLAUDE.md`, which is authoritative for that
server** — read it before working in a subproject. There is no root build, shared package or workspace tooling; run
commands from inside the subproject directory.

| Directory | Stack | Host port | Purpose |
|---|---|---|---|
| `gmail-mcp-server/` | Python 3.12, `mcp` SDK | 8765 | Gmail read + draft creation (OAuth) |
| `vision-mcp-server/` | Python 3.12, `mcp` SDK | 8766 | `describe_image` via an OpenAI-compatible vision backend (LiteLLM + Qwen) |
| `sports-mcp-server/` | Python 3.12, `mcp` SDK | 8767 | Read-only ESPN sports data |
| `firecrawl-mcp-server/` | Node 22 | 8768 | Gateway/supervisor around pinned upstream `firecrawl-mcp` against self-hosted Firecrawl |

All containers publish `127.0.0.1:<port> -> 8000`. Keep ports distinct when adding a server.

## Lineage and shared patterns

The servers are copies, not a shared library: gmail → vision → sports → firecrawl, each mirroring its predecessor's
layout and security posture. `security.py` (bearer + Host/Origin guard middleware), `logging_setup.py` (redacting JSON
logs) and `scripts/lib/` were copied forward and have since drifted per project — a fix to one copy does not propagate;
check siblings when fixing a shared-pattern bug.

Common to every server:

- Streamable HTTP on loopback, run in Docker inside WSL2; non-root, read-only rootfs, port bound to `127.0.0.1` only (no TLS).
- `config` module is the only reader of the environment; config errors name fields, never values.
- A test pins the exact tool set; network access is confined to one module (Python servers) and tests never touch the network.
- Coverage gates enforced by `make test` / `npm test` (85%; sports is 90%). Run a single Python test with `--no-cov` so the gate doesn't fail a partial run.
- `requirements*.txt` are hash-pinned and regenerated with `make lock` (uv) — never hand-edit them.
- Operations scripts: `scripts/{setup,start,stop,restart,rebuild,logs,status[,smoke][,auth]}.sh`, each with a `.ps1` twin that forwards into WSL2. `restart --recreate` is required after editing `.env`.
- `.env` holds real credentials/tokens (git-ignored here and per project): never print, log or commit its values.
- `.sh` files must stay LF (`.gitattributes`). The checkout is on `/mnt/c`, so POSIX file modes aren't enforced; for Python servers prefer a venv outside the Windows drive (e.g. `make install VENV=~/.venvs/<name>-mcp PYTHON=python3`).

## Docs workflow

Each server has `docs/SPEC.md` as the authoritative spec, and features are built through a spec → implementation report →
review rounds → fix rounds → summary cycle recorded under `docs/reports/` (feature-specific subfolders such as
`docs/reports/reauth/` and `docs/reports/search-filter/`). This is the SDD workflow defined in [AGENTS.md](AGENTS.md);
follow it for every change.
