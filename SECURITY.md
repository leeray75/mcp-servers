# Security Policy

These servers give AI agents access to personal data (email, local images) and to network-capable tools (web scraping).
Security is a primary design goal, not an afterthought.

## Supported versions

Only the latest commit on `main` is supported. Fixes are not backported.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through the repository's **Security → Report a vulnerability** (GitHub private vulnerability
reporting), or contact the maintainer directly. Please include:

- the affected server and version or commit,
- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- any suggested fix.

You can expect an acknowledgement within 7 days. Please allow a reasonable period for a fix before disclosing publicly.

## Threat model

The servers are designed to run on a single user's workstation and be called by a local MCP client. In scope:

- **Other local processes and web pages** reaching the loopback port. Mitigated by bearer-token auth (constant-time
  comparison) and `Host`/`Origin` validation against DNS rebinding.
- **A misbehaving or prompt-injected agent.** Mitigated by minimal, fixed tool sets: gmail can create drafts but has no
  send/delete/modify tools; vision reads only one read-only images folder; sports is read-only. Strict input
  validation and bounded output apply throughout.
- **Secret leakage.** Credentials live only in git-ignored `.env` files and token volumes. Logs are structured and
  redacted, and config errors name fields, never values.
- **Container escape and tampering.** Containers run non-root on a read-only root filesystem with capabilities dropped,
  from multi-stage builds with hash-pinned dependencies.

Out of scope:

- Exposing a server beyond `127.0.0.1` (for example by changing the published address or adding a reverse proxy).
  The servers do not terminate TLS and are not designed for remote access.
- A compromised host OS, WSL2 distribution or Docker daemon.

## Known tradeoffs

- **firecrawl-mcp-server runs with auth OFF by default.** Without `MCP_AUTH_TOKEN`, any local process or browser page can
  call it, and the self-hosted Firecrawl can fetch internal/LAN URLs. Enable the token as described in its
  [README](firecrawl-mcp-server/README.md#security-tradeoff-auth-is-off-by-default).
- **gmail-mcp-server** stores a Google refresh token (mode `0600`) in a git-ignored volume. Anyone with access to that
  file can read your mail; revoke it from your Google account if it is exposed.

## Hardening checklist for operators

- Generate tokens with `openssl rand -hex 32`, and don't reuse them across servers.
- Keep ports bound to `127.0.0.1`.
- Run `scripts/restart.sh --recreate` after editing `.env` so new values take effect.
- Run `make audit` periodically and rebuild images to pick up base-image patches.
