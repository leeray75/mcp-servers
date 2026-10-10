# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Search request filter (`src/api-filter.mjs`, spec in `docs/SPEC-search-filter.md`): a loopback-only proxy on
  `127.0.0.1:3001` inside the container that strips the top-level `domainTools` and `toolDetail` keys from
  `POST /v1/search` and `/v2/search`. The self-hosted Firecrawl API rejects these keys with HTTP 400, which broke
  `firecrawl_search`. All other requests stream through unchanged.
- `API_FILTER_ENABLED` (default `true`) and `API_FILTER_PORT` (default `3001`) settings.
- Live search step in `scripts/smoke.sh`.

### Changed

- The bearer token is optional and auth is **off by default**. `scripts/setup.sh --with-token` enables it, and the
  server logs a startup warning while auth is off. See the README's security tradeoff section.

## [1.0.0] - 2026-10-05

### Added

- Persistent Streamable HTTP MCP server that runs the upstream `firecrawl-mcp` (pinned to 3.27.3) against the
  self-hosted Firecrawl API. Over HTTP, client disconnects can no longer crash the server, as they did with the stdio (`EPIPE`) transport.
- Zero-dependency Node gateway: timing-safe bearer-token auth, Host-header guard, 1 MiB body cap, streaming proxy that
  strips the `Authorization` header, and `GET`/`HEAD /healthz`.
- Supervisor that manages the child process and graceful shutdown.
- Hardened container: non-root user, read-only root filesystem, dropped capabilities, published on `127.0.0.1:8768` only.
- `scripts/` (`setup`, `start`, `stop`, `restart`, `rebuild`, `logs`, `status`, `smoke`) as `.sh` with PowerShell
  wrappers that run them in WSL2.
- `node --test` suite with coverage, and a `Makefile`.

### Security

- `npm audit` reports high-severity advisories in `axios`, a transitive dependency of `firecrawl-mcp` 3.27.3. They can't
  be fixed without leaving the upstream pin; re-check on each upstream release.
