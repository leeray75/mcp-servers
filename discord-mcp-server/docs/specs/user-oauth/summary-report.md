# Summary report: user OAuth guild discovery (amendment A3)

- **Date:** 2026-10-29
- **Spec:** `SPEC.md` in this folder (approved 2026-10-29; amendment A3.1 §11 and A3.2 §12 approved 2026-10-29; all approved by:
  human). Phase-1 input: `PLAN.md` (another agent's draft, re-verified against docs.discord.com).
- **Branch:** `feature/discord-user-oauth`, from `main` fast-forwarded to `origin/main` at `744bf04`. **Nothing committed or pushed.**
- **Result:** review round 3 verdict **APPROVED** with no findings; 3 of 5 review rounds used.

## Process

| # | Step | Actor | Output | Result |
|---|---|---|---|---|
| 1 | Read PLAN.md; verify its claims against the live Discord docs and the Gmail sibling; ask 12 decisions | orchestrator + human | D17-D28 | answered; two PLAN.md claims corrected (Gmail is not code-paste; secret-less PKCE undocumented) |
| 2 | Write spec, tasks, workflow | orchestrator | `SPEC.md`, `tasks.md`, `workflow.md` | Draft |
| 3 | Approval gate | human | "Approve" | Approved |
| 4 | Sync `main`, create branch | orchestrator | `feature/discord-user-oauth` | done |
| 5 | SDK gate (T0) then T1-T12 | implementer (Haiku 5.5) | code, `implementation-report.md` | gate green, 594 tests |
| 6 | Review round 1 | reviewer (Sonnet 5.5) | `review-round-1.md` | **APPROVED**: 11 minors, 4 spec issues |
| 7 | Amendment A3.1 (S1-S4: no revoke on reconnect, revoke on post-exchange failure, ignore OAuth settings when off, wording) | orchestrator + human | `SPEC.md` §11 | Approved |
| 8 | Fix round 1 (A3.1, m1-m11) | implementer (same agent) | `fix-round-1.md` | gate green, 617 tests |
| 9 | Review round 2 | reviewer (same agent) | `review-round-2.md` | **APPROVED**: 3 minors |
| 10 | Amendment A3.2 (n1: failed token-file delete returns unavailable with the path) | orchestrator + human | `SPEC.md` §12 | Approved |
| 11 | Fix round 2 (n1-n3, comment) | implementer (same agent) | `fix-round-2.md` | gate green, 622 tests |
| 12 | Review round 3 | reviewer (same agent) | `review-round-3.md` | **APPROVED**: no findings |
| 13 | Final gate re-run | orchestrator | this report | green |

## What was built (mapped to the spec)

With `DISCORD_USER_OAUTH=true` (off by default; off mode is unchanged, U3):

- **Five tools** after the 14 bot tools (U5-U7, U16-U21): `discord_account_status`, `connect_discord_account` (URL-mode elicitation
  for capable 2026-07-28 clients, plain URL + open commands otherwise), `disconnect_discord_account` (best-effort revoke, always
  clears the token), `list_my_guilds` (paged, with `allowlisted`, `bot_present`, `can_add_bot`; not allowlist-gated, not cached),
  `get_guild_invite_link` (pure URL builder, permissions 66688).
- **Sign-in routes** `GET /oauth/start` and `GET /oauth/callback` on port 8769 (U8-U15): single-use `state`, `iss` check, 600 s
  expiry, S256 PKCE behind `DISCORD_OAUTH_PKCE`, confidential exchange with Basic auth, `/users/@me` profile, fixed no-store /
  no-referrer / CSP pages. Bearer-exempt only when on; Host guard still applies.
- **Client** (U22-U25): POST only to the two OAuth URLs, never replayed except 429; user-token traffic has its own rate limiter and
  hard-stop; serialized refresh within 5 minutes of expiry or after a 401; `invalid_grant` clears the token.
- **Token store** (U19): atomic 0600 JSON at `/data/discord_user_token.json`, stray temp files cleaned up.
- **Secrets** (U26, U29): client secret, tokens, code, state and verifier are redacted; per-flow literals are unregistered when the
  flow ends, so the redaction set stays bounded.
- **Ops and docs** (U30-U35): `docker-compose.oauth.yml` (mounts `./data` only when on, selected by the scripts from `.env`),
  Dockerfile `/data`, `.gitignore`, `.env.example`, optional `setup.sh` prompt, `smoke.sh` check, README section, CHANGELOGs,
  server and root `CLAUDE.md`, `docs/SPEC.md` §18.

## Verification

| Check | Result (orchestrator re-run, 2026-10-29) |
|---|---|
| `make check` (ruff, format, `mypy --strict`, pytest, 90% branch gate) | **622 passed, coverage 96.61%** |
| `make audit` | No known vulnerabilities |
| `bash -n` on all scripts | pass |
| Only `discord_client.py` imports httpx | confirmed |
| Pre-existing test files | unmodified (`git diff --stat -- tests` empty); off mode keeps the original 454 tests green |
| `docker compose config` base and base + override (implementer and reviewer, temporary empty `.env`) | valid |
| SDK gate (§6.3): per-request capabilities and `InputRequiredResult` URL elicitation in stateless JSON mode | passed |
| Live Discord (AC-L1) | **not run** (needs your account; optional) |

## Deviations and accepted items

- No deviations from the approved spec (with A3.1 and A3.2). Behaviors first built beyond the spec (revoke on post-exchange failure,
  failed-delete error) were brought to the human and written into the spec as A3.1 and A3.2 rather than left as deviations.
- Accepted by the reviewer without spec change: empty-body revoke counts as success; `list_guilds` shares a paging helper with
  identical output.
- `setup.sh` prints a `sudo chown 10001:10001 ./data` instruction instead of changing modes itself (m10).

## Open items for the human

1. **AC-L1 live check:** in the Developer Portal copy the client secret and register the redirect URI
   `http://127.0.0.1:8769/oauth/callback`; enable with `scripts/setup.sh`, `restart.sh --recreate`; sign in; call `list_my_guilds`,
   `get_guild_invite_link`; reconnect once and call `list_my_guilds` again; `disconnect_discord_account`. This confirms that Discord
   accepts the 127.0.0.1 redirect and the PKCE parameters (if PKCE is rejected, set `DISCORD_OAUTH_PKCE=false` and tell me).
2. On a native Linux filesystem, `./data` must be writable by UID 10001 (`sudo chown 10001:10001 ./data`); 0600 is not enforced on
   `/mnt/c`.
3. Carried over from the initial build: credential rotation, portal permissions 66688, shellcheck, optional n2-n4 / O1.
4. Commit / PR when you're ready (nothing has been committed).
