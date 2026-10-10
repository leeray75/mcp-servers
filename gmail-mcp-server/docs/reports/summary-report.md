# Gmail MCP Server — Summary Report

## Outcome

Built, reviewed and approved after **2 of a maximum 3 review rounds**. Final
review verdict: **APPROVE** (0 blocker, 0 major, 2 minor). Local commits only:
`878de72` (implementation), `8b4de54` (round 1 fixes). Nothing was pushed.

**Not yet verified by anyone:** a real Google sign-in, a real Goose connection,
and a fresh run of the `.ps1` wrappers beyond `status`/`logs`. The project has
only been exercised with fake credentials.

## What was built

- Python 3.12 MCP server (official `mcp` SDK 2.x, Google client libraries) over
  Streamable HTTP, in a hardened Docker container published on `127.0.0.1` only.
- Six tools: `search_threads`, `get_thread`, `get_message`, `list_labels`,
  `list_drafts`, `create_draft`. No send, delete, modify, or attachment download;
  a test enforces the exact tool set.
- Bearer-token auth (constant-time compare), Host/Origin checks, log redaction,
  atomic token storage, one-time `auth` flow in a separate container.
- Scripts in `scripts/`: `start`, `stop`, `restart`, `rebuild`, `auth`, `logs`,
  `status` (`.sh` plus PowerShell wrappers that call into WSL).
- README, CHANGELOG, SPEC/PLAN, `.env.example`, hash-locked dependencies.

## Process

| Step | Result |
|---|---|
| Implementation | 207 tests passing, 99% coverage, ruff/mypy strict clean, container verified with fake env |
| Review round 1 | CHANGES REQUIRED: 1 blocker, 2 major, 5 minor |
| Fix round 1 | All 8 fixed; 229 tests (Linux), image rebuilt and re-verified |
| Review round 2 | APPROVE: 0 blocker, 0 major, 2 minor; 4 mutation checks killed; pip-audit clean |

Notable round 1 catch: `os.chmod` on the token file would have failed on the
`/mnt/c` mount this repo lives on, so sign-in would have ended in a traceback
with no token saved. Fixed, with the file-mode caveat documented.

## Remaining items (minor, not fixed)

1. `clean_header` caps headers at 1000 characters, so an inherited `References`
   header can be cut mid-ID before the dedicated truncation runs. Cosmetic:
   threading relies on `threadId` and `In-Reply-To`.
2. The `/mnt/` warning prints on every start, rebuild and auth.

## Deviations from the SPEC (documented in the implementation report)

- `mcp` 2.2.0 renamed FastMCP to `MCPServer`; dependency pinned `>=2.2,<3`.
- Goose's config key is documented as `uri`, not `url`. This came from a web
  search, not from running Goose.
- The Host check accepts any numeric port on `127.0.0.1`/`localhost`, since the
  container sees the published port.
- The server runs stateless with JSON responses.
- The token file stores only the refresh token and scopes.

## Known limitations

- Google offers no "drafts only" scope. `gmail.compose` technically allows
  sending, so safety rests on the server exposing no send tool.
- In OAuth "Testing" mode, refresh tokens expire after about 7 days.
- On `/mnt/c`, file modes are not enforced. Cloning into the WSL filesystem is
  recommended.
- `data/` may be owned by UID 10001 on native Linux filesystems.
- Email content is untrusted and could contain prompt injection; tool
  descriptions say so, but the agent must still treat it as data.

## Next steps for the owner

1. Create `.env` from `.env.example` with your own client ID, client secret and
   a random `MCP_AUTH_TOKEN` (32+ characters). Use a fresh client secret, not the
   one pasted into earlier chats.
2. Run `scripts/auth.sh` (or `auth.ps1`) and complete the sign-in. Add yourself
   as a test user first if the consent screen is in Testing mode.
3. Run `scripts/start.sh`, then add the extension to Goose per the README.
4. If the Goose key (`uri` vs `url`) or the sign-in behaves differently from the
   README, that is the first thing to report back.
