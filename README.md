# mcp-servers

A collection of small, self-hosted [Model Context Protocol](https://modelcontextprotocol.io) (MCP) servers that give
AI agents (for example [Goose](https://goose-docs.ai)) narrowly scoped, auditable capabilities: reading Gmail,
seeing images, looking up live sports data and scraping the web through a self-hosted Firecrawl.

Each server is independent: its own toolchain, container, configuration, tests and documentation. They share a
deliberate house style: least privilege, loopback-only networking, bearer-token authentication and hardened containers.

## Servers

| Server | Stack | Host port | What it gives an agent |
|---|---|---|---|
| [`gmail-mcp-server`](gmail-mcp-server/README.md) | Python 3.12, `mcp` SDK | `8765` | Read access to Gmail and draft creation. No send, delete or modify tools exist |
| [`vision-mcp-server`](vision-mcp-server/README.md) | Python 3.12, `mcp` SDK | `8766` | `describe_image`: lets text-only models see images through an OpenAI-compatible vision backend (e.g. LiteLLM + Qwen) |
| [`sports-mcp-server`](sports-mcp-server/README.md) | Python 3.12, `mcp` SDK | `8767` | Live, compact, timestamped scores, schedules, standings, box scores, odds and news for 15 leagues (ESPN public API) |
| [`firecrawl-mcp-server`](firecrawl-mcp-server/README.md) | Node 22 | `8768` | A persistent HTTP gateway around the pinned upstream `firecrawl-mcp`, pointed at a self-hosted Firecrawl API |

Each server's own README is the full reference for its tools, configuration and troubleshooting.

## Architecture

```
            ┌──────────────── Windows host ────────────────┐
 MCP client │  127.0.0.1:8765 ─▶ gmail-mcp     ─▶ Gmail API │
 (Goose …)  │  127.0.0.1:8766 ─▶ vision-mcp    ─▶ LiteLLM   │
  ──HTTP──▶ │  127.0.0.1:8767 ─▶ sports-mcp    ─▶ ESPN      │
  + Bearer  │  127.0.0.1:8768 ─▶ firecrawl-mcp ─▶ Firecrawl │
            │      (Docker Engine inside WSL2)              │
            └───────────────────────────────────────────────┘
```

Every server:

- Speaks MCP over **Streamable HTTP**. Each container listens on `:8000` and is published on `127.0.0.1` only, so it
  is never reachable from other machines.
- Requires a **bearer token** (`MCP_AUTH_TOKEN`) and guards `Host`/`Origin` headers against DNS rebinding. Firecrawl's
  token is optional; read its [security tradeoff](firecrawl-mcp-server/README.md#security-tradeoff-auth-is-off-by-default).
- Runs as a **non-root** user on a **read-only root filesystem** with capabilities dropped.
- Emits structured JSON logs with secret redaction, and reads configuration from `.env` through one config module.
- Pins its exact tool set with a test, so a new tool can't appear by accident.

## Prerequisites

- Windows 10/11 with **WSL2** (Ubuntu) and **Docker Engine** (with the Compose plugin) installed inside WSL2.
- An MCP client that supports Streamable HTTP with custom headers.
- Per server: a Google Cloud OAuth client (gmail), an OpenAI-compatible vision endpoint (vision), or a self-hosted
  Firecrawl instance (firecrawl). Sports needs no API key.
- For development only: Python 3.12 and [`uv`](https://docs.astral.sh/uv/) for the Python servers, and Node 22 for firecrawl.

## Quick start

Each server is set up the same way. Run these commands from inside its directory in WSL2. On Windows, every `.sh` script has a
`.ps1` twin that forwards into WSL2 (for example `.\scripts\start.ps1`).

```bash
cd sports-mcp-server
scripts/setup.sh      # writes .env interactively (port, generated bearer token, ...)
scripts/start.sh      # builds the image, starts the container, waits for health, prints a client snippet
scripts/smoke.sh      # optional end-to-end check (vision, sports, firecrawl)
```

gmail additionally needs a one-time Google sign-in: run `scripts/auth.sh` (see its README).

Common operations scripts:

| Script | Purpose |
|---|---|
| `status.sh` | Container and health status |
| `logs.sh [-n N] [--no-follow]` | Tail container logs |
| `stop.sh` | Stop the container |
| `restart.sh [--recreate]` | Restart. `--recreate` is **required after editing `.env`** |
| `rebuild.sh [--keep-cache]` | Rebuild the image from scratch |

Then add the server to your MCP client with `Authorization: Bearer <MCP_AUTH_TOKEN>`. `start.sh` prints the snippet;
the scripts never write your client's configuration for you.

## Repository layout

```
mcp-servers/
├── README.md            # this file
├── CHANGELOG.md         # repository-level changes
├── CONTRIBUTING.md      # development workflow and conventions
├── SECURITY.md          # security model and vulnerability reporting
├── AGENTS.md            # mandatory SDD workflow for AI agents (any harness)
├── LICENSE
├── CLAUDE.md            # guidance for AI coding agents
└── <name>-mcp-server/
    ├── README.md        # user guide for this server
    ├── CHANGELOG.md     # this server's release history
    ├── CLAUDE.md        # authoritative engineering notes for this server
    ├── docs/            # SPEC.md and review/fix reports under docs/reports/
    ├── src/  tests/
    ├── scripts/         # setup/start/stop/... (.sh + .ps1)
    ├── Dockerfile  docker-compose.yml  Makefile
    └── .env.example     # copy to .env; never commit .env
```

There is no root build or shared library. The servers began as copies of one another and are maintained independently.

## Development

Run everything from inside a server's directory.

| Task | Python servers | firecrawl |
|---|---|---|
| Install | `make install` | `make install` / `npm ci` |
| Lint / format | `make lint`, `make format` | `make lint` |
| Type check | `make typecheck` (`mypy --strict`) | n/a |
| Test (with coverage gate) | `make test` | `make test` / `npm test` |
| Dependency audit | `make audit` | `make audit` |
| Everything | `make check` | `make check` |
| Refresh pinned deps | `make lock` (uv, hash-pinned) | `npm install` + commit `package-lock.json` |

Coverage gates are 85% (sports: 90%). Tests never touch the network. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
full workflow, including the spec → review → fix documentation cycle.

## Development process: Spec-Driven Development

All changes, including AI-assisted ones, follow a mandatory **Spec-Driven Development** workflow. It replaces vibe
coding with: planning with a human → written spec → explicit human approval → implementation → independent review with
a fix loop (max 5 rounds) → summary report. AI agents: read [AGENTS.md](AGENTS.md) before changing anything.

## Security

These servers handle credentials and personal data. Read [SECURITY.md](SECURITY.md) for the threat model and how to
report a vulnerability, and each server's README "Security model" section before exposing it to an agent.

## License

[MIT](LICENSE). `firecrawl-mcp-server` runs the third-party [`firecrawl-mcp`](https://www.npmjs.com/package/firecrawl-mcp)
package, which is distributed under its own license.
