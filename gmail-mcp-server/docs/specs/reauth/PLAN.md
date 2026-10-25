# Easy Re-authentication — Plan

## Problem

When the Google refresh token expires (every 7 days while the consent screen is in
*Testing*) or is revoked, Goose only sees "Run scripts/auth.sh". The user must then open a
terminal, run `scripts/auth.ps1`, copy a URL out of the terminal (the one-off container
has `open_browser=False`) and paste it into a browser.

## Goal

Re-authentication becomes "click a link (or run one script), approve in Google, done".
No terminal copy/paste, no server restart, no browser-automation MCP tools.

## Decisions (confirmed with the owner, 2026-10-10)

| # | Question | Decision |
|---|---|---|
| D1 | Trigger | **Both**: (a) the running server hosts its own sign-in flow at `http://127.0.0.1:<HOST_PORT>/auth`, and every auth error returned to Goose contains that clickable link; (b) `scripts/auth.sh` / `auth.ps1` open the browser automatically. |
| D2 | Protecting the in-server route | Unauthenticated (browsers cannot send the bearer header), loopback-only via the existing Host/Origin guard, single-use expiring OAuth `state`, PKCE, scope verification before saving. |
| D3 | MCP tool set | **Unchanged** — exactly the six existing tools. The link travels in the tool error message. |
| D4 | Reduce re-auth frequency (production publishing docs, expiry warnings) | **Out of scope.** |

## Approach

1. **Server-hosted OAuth** (`GET /auth` → Google → `GET /oauth2callback`) using
   `google_auth_oauthlib.flow.Flow` with the existing Desktop OAuth client and a loopback
   redirect on the published host port. On success the token is written with the existing
   `write_token` and the cached Gmail service is reset, so the next tool call works.
2. **Clickable hint**: `AUTH_HINT` includes the real link (built from `HOST_PORT`), with the
   script as a fallback.
3. **Script auto-open**: `auth.sh` prefers the running server (opens `/auth`, waits for the
   token file to change); otherwise it runs the one-off container and opens the printed
   Google URL itself (`wslview` → `explorer.exe` → `xdg-open` → print only). `--no-browser`
   restores today's behaviour.
4. Docs: README, `docs/SPEC.md` §4, CHANGELOG, CLAUDE.md.

Details and acceptance criteria: [SPEC.md](SPEC.md).

## Workflow (SDD)

1. Spec: `docs/specs/reauth/SPEC.md` (this plan's normative companion).
2. **Implementer** subagent (latest Sonnet) implements the spec, runs the full quality gate,
   writes `docs/specs/reauth/implementation-report.md`.
3. **Reviewer** subagent (latest Sonnet, separate, does not edit code) reviews against the
   spec and a security checklist and writes `docs/specs/reauth/review-round-N.md`, with
   findings tagged BLOCKER / MAJOR / MINOR.
4. Loop: the implementer fixes all BLOCKER/MAJOR findings (and MINOR where cheap) and
   writes `fix-round-N.md`; the reviewer re-reviews. **At most 5 rounds.**
5. Orchestrator writes `docs/specs/reauth/summary-report.md`.

## Constraints on agents

- Never read, print or ask for `.env` values or `data/token.json` contents. Fakes only.
- Never run `auth`, `start`, or anything that contacts Google with real credentials.
- No `git commit` / `git push` (the owner decides afterwards).
- Do not weaken existing invariants in `CLAUDE.md` / `docs/SPEC.md` to make tests pass.
