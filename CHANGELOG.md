# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-01

### Added

- Streamable HTTP MCP server (official `mcp` SDK) with six tools: `search_threads`,
  `get_thread`, `get_message`, `list_labels`, `list_drafts` and `create_draft`.
  There is deliberately no send, delete, trash, modify, settings or attachment-download tool.
- Bearer-token authentication (constant-time comparison), Host/Origin loopback guard
  (DNS-rebinding defense), request-size cap and an unauthenticated `GET /healthz`.
- One-time OAuth sign-in (`auth` script / `gmail_mcp.auth_cli`) using the installed-app flow
  with PKCE; refresh token stored atomically with mode 0600 in a git-ignored volume.
- Strict input validation (ID patterns, CR/LF header-injection rejection, size and
  recipient limits) and bounded output (result cap, body truncation, thread budget).
- Structured JSON logging with secret redaction; no message content is logged.
- Hardened container: multi-stage build, hash-pinned dependencies, non-root UID 10001,
  read-only root filesystem, dropped capabilities, loopback-only published port.
- `scripts/` (`start`, `stop`, `restart`, `rebuild`, `auth`, `logs`, `status`) as `.sh` with
  PowerShell wrappers that run them in WSL2.
- Test suite (pytest, fully mocked Google API), `ruff`, `mypy --strict`, and a `Makefile`.
