# Easy Re-authentication — Summary Report

Date: 2026-10-10 · Spec: [../../reauth/SPEC.md](../../reauth/SPEC.md) · Plan: [../../reauth/PLAN.md](../../reauth/PLAN.md)

## Outcome

**Approved after 4 review rounds (limit 5). 0 open findings.**

When the Google token expires or is revoked, re-authentication no longer needs a terminal
and the URL no longer has to be copied by hand:

1. **From Goose:** every Gmail tool's auth error now reads roughly
   *"Gmail is not authorised. Open http://127.0.0.1:8765/auth in your browser to sign in
   (or run scripts/auth.ps1 ...)"*. Click the link, approve in Google, and the page says
   "Gmail is connected". The next tool call works without restarting the server.
2. **From a terminal:** `.\scripts\auth.ps1` (or `scripts/auth.sh`) opens the browser tab
   itself. If the server is running, it uses `/auth` and waits for the token file to change.
   Otherwise it runs the one-off container and opens the Google URL automatically.
   `--no-browser` restores the old print-only behaviour.

No browser-automation MCP tools are involved, and the MCP tool set is still exactly the
six tools.

## Decisions (from the planning Q&A)

| Topic | Decision |
|---|---|
| Trigger | Both: a link served by the running server, plus a script that opens the browser |
| Route protection | Loopback Host/Origin guard, single-use expiring `state`, PKCE (S256), scope check before saving |
| MCP tools | Unchanged (six) |
| Reducing how often re-auth is needed | Out of scope |

## What changed

| Area | Change |
|---|---|
| `src/gmail_mcp/signin.py` (new) | `GET /auth` → 302 to Google; `GET /oauth2callback` exchanges the code off the event loop, checks the refresh token and scopes, writes `token.json` atomically under a lock, then calls `GmailClient.reset()`. Pending store: max 8 attempts, 10 min TTL, state consumed on first use. Static, script-free pages with `no-store`, `no-referrer`, `nosniff` and a CSP with `frame-ancestors 'none'`. `HEAD /auth` has no side effects. Cross-site `Sec-Fetch-Site` requests are refused unless they are a user-activated top-level navigation. |
| `security.py` | The bearer exemption is now exact-path GET/HEAD on `/healthz`, `/auth` and `/oauth2callback`; the Host/Origin guard still runs first |
| `config.py`, `docker-compose.yml`, `.env.example` | `HOST_PORT` (default 8765) is now also read by the server, for the redirect URI and the link in error messages; the port mapping is still bound to 127.0.0.1 |
| `errors.py`, `server.py` | The auth error includes `http://127.0.0.1:<HOST_PORT>/auth` |
| `gmail_client.py` | New `reset()` method |
| `logging_setup.py` | Also redacts Google authorization codes |
| `auth_cli.py` | Prompt text only |
| `scripts/auth.sh`, `scripts/lib/common.sh` | `open_url` (wslview → explorer.exe → xdg-open → print), `server_running`, `auth_route_available` (detects a stale image and suggests `rebuild`), `--no-browser` |
| Docs | README (new "Re-authenticating" section, security notes, troubleshooting), `docs/SPEC.md` §4.4, CHANGELOG, CLAUDE.md |
| Tests | `tests/test_signin.py` (new) plus additions to the config, client, logging and server tests |

## Quality gate (run in a python:3.12-slim container on a copy of the repo)

- ruff check: clean · ruff format --check: clean · mypy --strict: no issues
- pytest: **286 passed, 99.19 % coverage** (gate is 85 %)
- `bash -n` OK; shellcheck reports only the existing SC2034 warning for unused shared constants
- `docker build` succeeded (implementation round)

## Review loop

| Round | Verdict | Findings |
|---|---|---|
| 1 | APPROVE | 5 MINOR: `/auth` flood eviction, `HEAD` side effect, stale image waits 5 min, `-T` on `compose run`, long doc lines. All fixed in fix-round-1 |
| 2 | APPROVE | 2 MINOR: a cross-site link click would get 403, curl line continuation. Fixed in fix-round-2 |
| 3 | APPROVE | Curl continuation not actually fixed (the backslash was lost). Fixed in fix-round-3 |
| 4 | APPROVE | None open |

The orchestrator also normalised six edited Python files from CRLF to LF to match
`.gitattributes`.

**Stale-document sweep (after the loop):** a repo-wide search for outdated auth wording found
four stale spots, all fixed by the orchestrator: `docs/SPEC.md` §2 layout (added `signin.py`
and `docs/specs/reauth/`), the README project-layout `docs/` line, the CLAUDE.md docs pointer, and
`scripts/status.sh` (the no-token hint now includes the `/auth` link; `bash -n` OK). Kept as-is
on purpose: `docs/PLAN.md`, `docs/reports/*.md` (the original build's historical records,
still accurate), the port 8400 references (the one-off container fallback still uses it), and
`start.sh`/`setup.sh` (they run before the server is up, so the script is the right advice).

## Not verified (by design: no real credentials were used)

- The real Google round trip (consent screen → `/oauth2callback` → token write). The code
  exchange was tested only against a fake `Flow`. The authorization URL builder was checked
  against the pinned `google-auth-oauthlib` 1.5.0.
- Running `scripts/auth.sh` for real, including whether `explorer.exe` / `wslview` open the
  tab.
- Whether Google accepts the redirect `http://127.0.0.1:8765/oauth2callback` for your
  Desktop OAuth client. Desktop clients normally allow any loopback port and path. If
  Google shows `redirect_uri_mismatch`, report it.

## Residual risk (documented in the README)

Any local process can open `/auth` and start a sign-in, or flood the pending store.
Nothing is stored unless you approve the Google consent screen, and a local process that
can already read `data/token.json` is outside this server's threat model.

## Next steps for the owner

1. `.\scripts\rebuild.ps1`: **required**. The running container uses the old image, which
   has no `/auth` route.
2. Smoke test: open `http://127.0.0.1:8765/auth`, approve, and check that Goose's Gmail
   tools work.
3. Commit the changes when satisfied (nothing has been committed).
