# Changelog

All notable repository-level changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the servers use
[Semantic Versioning](https://semver.org/).

Each server keeps its own changelog for its code and behavior:

- [gmail-mcp-server](gmail-mcp-server/CHANGELOG.md)
- [vision-mcp-server](vision-mcp-server/CHANGELOG.md)
- [sports-mcp-server](sports-mcp-server/CHANGELOG.md)
- [firecrawl-mcp-server](firecrawl-mcp-server/CHANGELOG.md)

## [Unreleased]

### Added

- Monorepo combining `gmail-mcp-server`, `vision-mcp-server`, `sports-mcp-server` and
  `firecrawl-mcp-server`. gmail's prior commit history is preserved under its directory.
- Root `.gitignore` as a safety net for `.env` files, `node_modules` and Python virtualenvs.
- Root documentation: `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE` (MIT)
  and `CLAUDE.md`.
- `CHANGELOG.md` for `vision-mcp-server`, `sports-mcp-server` and `firecrawl-mcp-server`.
