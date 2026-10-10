# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Process: Spec-Driven Development is mandatory.** Changes to this server follow the SDD workflow in
> [`../AGENTS.md`](../AGENTS.md): plan with the human, write a spec, get explicit approval, then implement, review
> (fix loop, max 5 rounds) and write a summary report. This file stays authoritative for this server's technical
> invariants; AGENTS.md governs process.

## What this is

A thin Node 22 wrapper that runs the pinned upstream `firecrawl-mcp@3.27.3` as a persistent Streamable HTTP MCP server in
Docker (WSL2), pointed at the self-hosted Firecrawl API (`http://api:3002` on the external `firecrawl_backend` network),
behind a gateway on 127.0.0.1:8768 with OPTIONAL bearer auth (off by default, amendment 1: loopback-only, startup warning; set `MCP_AUTH_TOKEN` to enforce 401s). It replaces Goose's stdio `firecrawl-local` extension (EPIPE crashes).
It mirrors `../sports-mcp-server` (layout, scripts, security posture). `docs/SPEC.md` is authoritative; `README.md` covers
operation; `docs/reports/implementation-report.md` records what was built and verified; the search filter (Amendment 2) is recorded in `docs/reports/search-filter/` with its spec in `docs/SPEC-search-filter.md`. Goose's config is never edited here.

## Commands

- `npm ci --ignore-scripts` - install exact locked deps; `npm test` - node:test with coverage (no Docker/network)
- Single test file: `node --test tests/gateway.test.mjs`; `make check` = syntax check + tests; `make audit` = `npm audit --omit=dev`
- Container (run in WSL; each script has a `.ps1` twin): `scripts/{setup,start,stop,restart,rebuild,logs,status,smoke}.sh`.
  `restart --recreate` after editing `.env`; `smoke.sh --no-live` skips the live scrape and search. `.env` may hold a token: never print, log or commit it. `setup.sh --with-token` generates one.

## Architecture (`src/`)

- `config.mjs` - the ONLY reader of `process.env`; errors name fields, never values.
- `redact.mjs` - secret redactor and tiny JSON logger.
- `gateway.mjs` - zero-dependency auth proxy: `/healthz` (child alive AND probe to child), `/mcp` bearer when a token is set (SHA-256 + `timingSafeEqual`),
  Host guard (421), body cap (413), 502 on child failure, abort propagation, `Authorization` stripped before forwarding, other paths 404.
- `api-filter.mjs` - loopback request filter (127.0.0.1:3001) between the child and the Firecrawl API: strips the cloud-only `domainTools`/`toolDetail` keys from `/v1|v2/search` bodies (the self-hosted API rejects them with 400), streams everything else untouched. See `docs/SPEC-search-filter.md`.
- `main.mjs` - supervisor with injectable spawn/exit: spawns `node_modules/firecrawl-mcp/dist/index.js` (`HTTP_STREAMABLE_SERVER=true`,
  child bound to 127.0.0.1:3000, `FIRECRAWL_API_URL` pointed at the filter unless `API_FILTER_ENABLED=false`), waits for readiness, redacts child output, exits non-zero if the child dies, graceful on SIGTERM.
- `tests/fake-child.mjs` - fake MCP child used by gateway tests.

## Invariants to preserve

- Published port is `127.0.0.1` only; the child port 3000 is never published; the container joins `firecrawl_backend` (external).
- Auth off is the default; the Authorization header is stripped either way. Token and API key never appear in logs, URLs, error bodies or the child's environment (the token is not passed to the child).
- The API filter binds 127.0.0.1 only (never published, port 3001 is container-internal); the upstream `firecrawl-mcp` stays pinned and unpatched (fix the request in the filter, not in `node_modules`).
- Zero runtime dependencies in `src/` besides Node built-ins; `firecrawl-mcp` stays pinned exactly in `package.json` and the lockfile;
  Docker installs with `npm ci --omit=dev --ignore-scripts`.
- Container: UID 10001, read-only rootfs, caps dropped, no host mounts, `init: true`.
- Tests must run without Docker or network. Keep coverage of `src/` at or above 85% lines.
- `.sh` files must stay LF (see `.gitattributes`); on `/mnt/c` POSIX modes are not enforced for `.env`.
