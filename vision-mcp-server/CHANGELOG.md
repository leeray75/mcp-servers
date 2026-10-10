# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0]

### Added

- Streamable HTTP MCP server (official `mcp` SDK) with one read-only tool, `describe_image`: reads image files from a
  single read-only folder, sends them with a question to a vision model behind any OpenAI-compatible endpoint
  (built for self-hosted LiteLLM + Qwen) and returns the answer as text.
- `python -m vision_mcp --stdio` for clients that launch the server themselves.
- Bearer-token authentication, Host/Origin loopback guard and an unauthenticated health endpoint.
- Structured JSON logging with secret redaction.
- Hardened container: multi-stage build, hash-pinned dependencies, non-root user, read-only root filesystem, dropped
  capabilities, published on `127.0.0.1:8766` only.
- `scripts/` (`setup`, `start`, `stop`, `restart`, `rebuild`, `logs`, `status`, `smoke`) as `.sh` with PowerShell
  wrappers that run them in WSL2.
- Test suite (pytest, no network), `ruff`, `mypy --strict` and a `Makefile`.
