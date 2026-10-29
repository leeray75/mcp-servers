# Phase-1 planning: user OAuth mode (discord-mcp-server)

- **Status:** Phase 1 draft — **not a spec, not approved**. No code may be written from this document.
  It exists to collect the human's decisions (D-numbers) that become `docs/specs/user-oauth/SPEC.md`.
- **Date:** 2026-10-29
- **Author:** orchestrator (goose), for review by the human and implementation via Claude Code.
- **Parent spec:** `../../SPEC.md` (approved 2026-10-28; amendments A1 §16, A2 §17). This feature would be
  a new amendment/spec, not an edit of §§1–15.
- **Trigger:** the human wants to read Discord content from servers where they are only a member
  (e.g. a public game-community server) and is not willing/able to have a bot installed there.

## 1. Verified ground truth (fetched 2026-10-29)

Source: `https://docs.discord.com/developers/topics/oauth2` (OAuth2 Scopes table), fetched 2026-10-29.
The complete user-OAuth scope list is: `activities.read`, `activities.write`, `applications.builds.read`,
`applications.builds.upload`, `applications.commands`, `applications.commands.update`,
`applications.commands.permissions.update`, `applications.entitlements`, `applications.store.update`, `bot`,
`connections`, `dm_channels.read`, `email`, `gdm.join`, `guilds`, `guilds.join`, `guilds.members.read`,
`identify`, `identify.premium`, `messages.read`, `relationships.read`, `role_connections.write`, `rpc`,
`rpc.activities.write`, `rpc.notifications.read`, `rpc.voice.read`, `rpc.voice.write`, `voice`,
`webhook.incoming`.

Facts that bound this feature:

1. **No user-OAuth scope reads guild content.** There is no scope for guild channel lists, guild messages,
   member lists, or audit logs. `guilds` only enables `GET /users/@me/guilds` ("basic information about all
   of a user's guilds"); `guilds.members.read` only returns *the user's own* member object in one guild.
2. **`messages.read` is not a REST scope.** It is documented as "for local rpc server api access" — the
   desktop client's localhost RPC family; sibling `rpc*`/`voice`/`dm_channels.read` scopes are marked
   "only available to approved partners". Not a viable basis for a headless server feature.
3. **The ToS bars the obvious shortcut.** The same page warns that "automating standard user accounts
   (generally called 'self-bots') outside of the OAuth2/bot API" violates the developer ToS. Not an option.
4. **Token mechanics (documented):** authorization code grant; token URL and revocation URL accept only
   `application/x-www-form-urlencoded`; access tokens expire (`expires_in` 604800 = 7 days) with refresh
   tokens; `state` and issuer identification (`iss=https://discord.com`) are the documented CSRF/mix-up
   mitigations; the docs recommend authorization code + PKCE over implicit.
5. **Consequence for the original goal:** reading guild message content in a server the human does not
   manage is **not achievable through any Discord-sanctioned API surface**. The bot model (invite with
   Manage Server) remains the only way to read guild content. This was verified so no one builds toward it.

## 2. What user OAuth can legitimately add (the honest feature)

A personal, single-user "account connector" whose value is **discovery and onboarding**, not content reads:

- **`list_my_guilds()`** (user-OAuth `identify guilds`): lists every guild the *authorized user* is in —
  id, name, owner flag, the user's effective permissions — so the human can (a) find guild snowflakes
  without Developer Mode hunting, (b) see exactly which servers they can manage (Admin/Manage Server bit)
  and therefore where the bot can be added, and (c) sanity-check the allowlist.
- **`get_guild_invite_link(guild_id)`**: pure URL builder (no network, no auth): produces the
  `https://discord.com/oauth2/authorize?...client_id=...&scope=bot&permissions=66688&guild_id=...` install
  link for one click. Available regardless of OAuth status.
- **`connect_discord_account()` / `discord_account_status()` / `disconnect_discord_account()`**: lifecycle
  for the one user token. Connect returns the authorize URL; the human completes it in a browser and pastes
  back the code (or the callback URL); disconnect revokes at Discord and deletes the stored token.

Everything already built (the 14 bot tools) is unchanged. `list_my_guilds` is **not** subject to the guild
allowlist (it reads the user's own account data, not bot-scoped guild data) — this invariant boundary must
be stated explicitly in the spec: **the allowlist continues to gate every bot-token tool with no exception.**

## 3. Goals

- G1. Let the human enumerate their own guilds with owner/permission info to fill
  `DISCORD_ALLOWED_GUILD_IDS` and invite the bot, without Discord UI guesswork.
- G2. Keep the read-only posture: no write tools, no message reads beyond what scopes allow (none).
- G3. Keep every repo invariant: loopback-only, bearer auth, fixed tool set (extended only by this
  amendment), single network module, secret redaction, hash-pinned deps, gate green.

## 4. Non-goals

- Reading guild messages/channels/members/audit logs with the user token (impossible — §1).
- Multi-user support (one human, one account token; the server is single-tenant).
- Any write capability (`guilds.join`, `gdm.join`, `webhook.incoming` etc. — not requested).
- Partner-gated scopes (rpc family), self-bots (ToS), gateway connections.

## 5. Design sketch (to be finalized in the spec after decisions)

- New module `oauth.py` (flows, state, PKCE, token storage) but **only `discord_client.py` keeps httpx** —
  the token/refresh/revoke POSTs to `https://discord.com/api/oauth2/token` go through it like every other
  request (form-urlencoded per §1.4, no secrets in logs).
- New settings (all optional; feature off by default): `DISCORD_USER_OAUTH` (bool, default false),
  `DISCORD_OAUTH_CLIENT_SECRET`, `DISCORD_OAUTH_REDIRECT_URI` (must be registered in the portal),
  `DISCORD_OAUTH_SCOPES` (default `identify guilds`), `DISCORD_OAUTH_TOKEN_FILE` (path).
- Tool registry grows from 14 to 14+3|4 only when `DISCORD_USER_OAUTH=true` (mirrors the existing
  config-omission mechanism; exact-set tests updated accordingly). Boot line records the mode.
- Token at rest: file mode 0600, same trust domain as `.env`; `SecretStr` in memory; never logged;
  redaction tests extended for access/refresh tokens and the client secret.
- Errors: expired/invalid token → `kind=auth` with "call connect_discord_account" hint (same pattern as
  the search-index and intent hints).

## 6. Open decisions (Phase 1 — the human must answer before SPEC.md is written)

Recorded as `D17+` when answered. Options with a recommendation marked.

- **D17 — Do it at all / scope of the feature:**
  1. **(Recommended) Discovery helper only**: `list_my_guilds`, `get_guild_invite_link`, plus the
     connect/status/disconnect trio. Honest, small, solves the real pain (empty dropdown, unknown IDs).
  2. Discovery helper **+** `get_my_membership(guild_id)` (`guilds.members.read`: the user's own roles in
     a guild). One extra tool, extra scope.
  3. **Abandon** — decide the bot-only server is enough; keep this plan as a documented dead end.
  4. Other.
- **D18 — Authorize UX:**
  1. **(Recommended) Code-paste**: `connect_discord_account()` prints the authorize URL (browser), human
     pastes back the `code` (or whole redirect URL) into a `connect_discord_account(code=...)` second call.
     No listener needed; Docker-friendly; mirrors the gmail server's sign-in pattern.
  2. **Loopback listener**: server temporarily listens on 127.0.0.1 for the redirect. Nicer UX, but adds a
     second surface and WSL/Docker port plumbing.
  3. Other.
- **D19 — Client type / secret handling:**
  1. **(Recommended) Confidential client**: client secret in `.env` (`DISCORD_OAUTH_CLIENT_SECRET`),
     required only when `DISCORD_USER_OAUTH=true`.
  2. **PKCE public client** (docs recommend PKCE; needs verification that Discord permits code exchange
     without the secret for this app — verify against docs at spec time if chosen).
  3. Other.
- **D20 — Token storage:**
  1. **(Recommended)** Plain file at `DISCORD_OAUTH_TOKEN_FILE`, mode 0600, `SecretStr` in memory,
     revoked at Discord + deleted by `disconnect_discord_account()`. Same trust domain as `.env`.
  2. **Encrypted at rest** (key from a separate env var). Stronger, more moving parts.
  3. Other.
- **D21 — `state`/CSRF handling for the code-paste flow:**
  1. **(Recommended)** `connect_discord_account()` generates + stores `state`, and the code-paste call
     must reference the pending connection; mismatch/absent → refuse. Aligns with the docs' recommendation.
  2. Skip `state` (single-user loopback; lower ceremony, weaker). Not recommended — the docs explicitly
     recommend it.
  3. Other.

## 7. Risks and follow-ups for the spec stage

- PKCE availability for public clients (D19.2) and the exact `GET /users/@me/guilds` response fields
  (owner/permissions presence) must be verified against the docs before requirements are frozen.
- User-token rate limits are not documented here — spec must verify how they interact with the existing
  bucket layer (user-OAuth calls should get their own bucket major parameter or an isolated client path).
- Token refresh races and persistence across container restarts (file must live in the compose volume).
- The fixed-tool-set tests, README/CLAUDE.md tool tables, `.env.example`, CHANGELOG all need the same
  treatment they got in A1 (docs changes extend R42).

## 8. What happens next (SDD)

1. Human answers D17–D21 (chat, numbered).
2. Orchestrator writes `docs/specs/user-oauth/SPEC.md` (Draft) with Decisions, numbered requirements,
   test plan; presents the approval gate (Approve / Revise / Reject / Hold).
3. Only after explicit Approve: implementer → reviewer rounds per `../initial-build/workflow.md` pattern.
4. The bot-token server stays untouched meanwhile; `DISCORD_USER_OAUTH=false` default keeps current
   behavior byte-identical.
