# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Process: Spec-Driven Development is mandatory.** Changes to this server follow the SDD workflow in
> [`../AGENTS.md`](../AGENTS.md): plan with the human, write a spec, get explicit approval, then implement, review
> (fix loop, max 5 rounds) and write a summary report. This file stays authoritative for this server's technical
> invariants; AGENTS.md governs process.

## What this is

A self-hosted, **read-only** MCP server (Python 3.12, official `mcp` SDK 2.3) that gives an agent guild-scoped access
to Discord through a bot token: 14 tools (guilds, channels, messages, search, reactions, pins, threads, members, audit
log). Streamable HTTP on `127.0.0.1:8769` behind a bearer token, in Docker inside WSL2. `docs/SPEC.md` is the
authoritative specification; `README.md` covers operation; `docs/specs/initial-build/` holds the build record.

It mirrors the structure and security posture of the sibling `../sports-mcp-server`. Its own HTTP client is the only
network code (no discord.py, no hikari).

## Commands

Run in WSL/Linux with the venv outside the Windows drive (`VENV=~/.venvs/discord-mcp`, create it with
`uv venv ~/.venvs/discord-mcp --python 3.12`):

- `make install VENV=... PYTHON=python3` - hash-pinned dev install plus editable package
- `make check VENV=...` - ruff + `mypy --strict` + pytest (coverage gate 90%, branch)
- Single test: `pytest tests/test_service.py::test_name --no-cov`
- `make lock` re-resolves and re-hashes `requirements*.txt` with uv (never hand-edit); `make audit` runs pip-audit
- `python tools/try_live.py list_guilds` - opt-in real call with the user's `.env` (not run by tests)

Operating the container: `scripts/{setup,start,stop,restart,rebuild,logs,status,smoke}.sh` (each with a `.ps1` twin).
`restart.sh --recreate` is needed after editing `.env`. `.env` holds the bot token and the MCP token: never print,
log or commit it.

## Architecture (`src/discord_mcp/`)

- `__main__.py` entry (uvicorn on `0.0.0.0:8000` inside the container); `config.py` the only module that reads the
  environment (pydantic-settings; errors name fields, never values).
- `server.py` builds `DiscordServer` (an `MCPServer` subclass), the `/healthz` route and the middleware. Its lifespan runs
  the boot probe, computes the tool plan and registers tools in the fixed order before serving. It deliberately has
  **no** `from __future__ import annotations` (`Field(...)` metadata is read by the SDK). Each tool returns
  `Annotated[CallToolResult, Model]`, so `outputSchema` comes from the model. `call_tool` is overridden to reject unknown
  arguments, unknown tools and schema failures in the R30 shape.
- `service.py` `DiscordService`: one method per tool (guard, permission pre-check, client calls, projection, paging).
- `discord_client.py` **the only module that imports httpx**: GET-only, host pinned, redirects refused, streamed size cap,
  GET retries (5xx and transport), 429 handling, 202/110000, 401 hard-stop, numeric-code error mapping, boot probe.
- `ratelimit.py` bucket store, global pause, global pacing, invalid-request window (no network import; injectable clock).
- `budget.py` tool-call token buckets. `cache.py` TTL/LRU cache bounded by entries and bytes (metadata only).
- `guard.py` allowlist guard. `permissions.py` section-10 algorithm. `capabilities.py` intent gating and tool order.
- `models.py` pydantic result models. `project.py` raw Discord JSON to models. `snowflake.py` ids and time arguments.
- `errors.py` `DiscordMcpError` (kind, message, code, http_status, retryable, retry_after, hint). `security.py` and
  `logging_setup.py` are copied from `sports-mcp-server` and adapted.

## Invariants to preserve

- The tool set is exactly the 14 in `capabilities.TOOL_SPECS` minus boot omissions, in that order; a test pins it. No
  write tools, no prompts, no resources, no raw "call any endpoint" tool.
- Only `discord_client.py` imports httpx (a test greps for it). Only the `DISCORD_API_BASE` host is contacted; redirects
  are never followed. No agent-supplied string becomes a URL path segment without validation (snowflake pattern or the
  emoji rules, percent-encoded).
- Every guild or channel parameter goes through `guard.py` before any other request. A non-allowlisted guild id causes
  no request. A channel id needs one cached channel lookup (the only way to learn its guild) and nothing else.
- Empty versus forbidden: when the bot lacks `READ_MESSAGE_HISTORY` (or `VIEW_CHANNEL`, or `CONNECT` in voice), the tool
  returns `kind=permission` and sends no messages request. Search `202`/`110000` is `index_pending`, never empty.
- Rate limits are learned from headers, never hard-coded. A user or global 429 blocks the bucket for the full
  `retry_after` (no cap); a shared 429 is never replayed and never counted as invalid. The first 401 stops all Discord
  traffic for the process lifetime.
- Errors are `isError` tool results in the R30 shape. JSON-RPC errors only for protocol faults and unexpected exceptions
  (generic message). Kind comes from status and Discord's numeric code, never from message text.
- Logs, errors and `/healthz` never contain tokens, request URLs, Discord response bodies or message content. Logs carry
  route templates (for example `GET /channels/{channel_id}/messages`), status, scope, duration and outcome.
- State is process-wide (cache, buckets, budgets, hard-stop, circuit). Nothing is keyed to an MCP session or connection.
- Tests never touch the network (autouse guard). Fixtures are synthetic. Keep each fixture under 50 KB.
- Container: UID 10001, read-only rootfs, caps dropped, port bound to `127.0.0.1` (no TLS), no host mounts.
- A1 (SPEC section 16) wins over sections 1-15. Results are complete pages (D11): this is a deliberate departure from the
  siblings' per-tool size budgets. `tests/test_sizes.py` asserts every tool's worst-case structured projection at its
  maximum limit is under 4 MiB; keep the figures in the README in step with it.
- Every channel-lookup failure (DM, unknown channel, non-allowlisted guild, 403/404/5xx) returns the identical
  `forbidden_guild` message "Channel is not in an allowlisted guild". Auth, rate-limit and unavailable pass through.
- Intents (A2, SPEC section 17): `flags` and `flags_new` are parsed independently. Each usable field is a non-negative
  integer (`flags` as a JSON integer; `flags_new` as an integer or a 1-30 digit ASCII string). The effective flags are
  the bitwise OR of every usable field, and the boot line reports `flags_source` as `flags`, `flags_new`,
  `flags+flags_new` or `null`. Malformed values are ignored and never raise. Neither usable omits intent tools with
  `flags_missing`. `DISCORD_FORCE_*` registers a tool regardless; `DISCORD_ENABLE_*=false` always wins.
- Keys that do not apply to a tool are omitted (`exclude_unset`), not null. `Message.reactions` is null for search.
- Mirror-the-sibling rule: `security.py` and `logging_setup.py` drifted from `sports-mcp-server` by design; a fix to one
  copy does not propagate. Check the sibling when fixing a shared-pattern bug.
