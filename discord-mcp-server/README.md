# discord-mcp-server

A self-hosted, **read-only** [Model Context Protocol](https://modelcontextprotocol.io) server that lets an agent
(Claude Code, Goose, or any Streamable HTTP MCP client) read the Discord servers you allow it to read: list channels,
read and search message history, look up members and roles, read pins, threads and the audit log.

It talks to Discord's public REST API (v10) with a **bot token**, through a thin HTTP client it owns (no discord.py).
It is deliberately narrow: 14 read-only tools, a default-deny guild allowlist, and explicit errors for the cases where
Discord would otherwise answer with an empty list that looks like "nothing here".

**What this server can and can never read.** It reads guilds where (a) the bot has been invited — which requires
someone with **Manage Server** in that guild (the owner or an admin) — and (b) the guild id is in your allowlist. It can
**never** read your personal DMs, group DMs, friends list or DM content: bots cannot be present in user-to-user
conversations, and Discord gates the DM-capable OAuth scopes to approved partners. If you need your own DMs archived,
use Discord's Settings → Privacy & Safety → *Request All Data* export and hand the archive to your agent offline.

> Specification: [`docs/SPEC.md`](docs/SPEC.md) (authoritative). Build record: [`docs/specs/initial-build/`](docs/specs/initial-build/).

## Before you install: checklist

1. If the bot token was ever pasted into a chat, committed to a repository, or included in a printed or shared
   document, **rotate it now** (Developer Portal > your application > Bot > Reset Token) and delete the copies.
   Anything you rotate invalidates the old token immediately.
2. Create a **dedicated** Discord application for this server. Do not reuse a bot that other systems depend on.
3. Enable only what you need (section 1 below). The server detects privileged intents at boot and disables the tools
   that need an intent you did not enable.

## 1. Discord Developer Portal setup

1. Open the [Developer Portal](https://discord.com/developers/applications), create an application, then open **Bot**.
2. Under **Privileged Gateway Intents**, enable only what you need:
   - **Message Content Intent**: needed for `search_messages`. Without it the tool is not registered at boot.
   - **Server Members Intent**: needed for `list_members` (`get_member` and `search_members` work without it).
   - Leave **Presence** off. The server never uses presences.
3. Under **Bot**, keep *Public Bot* off unless you need it, and reset/copy the **token** once. Store it only in `.env`.
4. Invite the bot to each server you want to allow, with the minimum permissions below. Use this URL (replace
   `<APP_ID>` with the Application ID from **General Information**):

   ```
   https://discord.com/oauth2/authorize?client_id=<APP_ID>&scope=bot&permissions=66688
   ```

   `66688` = `VIEW_CHANNEL` (1024) | `READ_MESSAGE_HISTORY` (65536) | `VIEW_AUDIT_LOG` (128). Add `CONNECT`
   (1048576) to read text chat in voice channels: the combined value is **`1115264`**.

   Completing this step requires **Manage Server** in that server — the owner or an admin. If you are only a member,
   you need an admin to open the link. With user OAuth enabled (section 4), `get_guild_invite_link` generates the same
   link prefilled and locked to one guild, so an admin's install is a single click.

5. Get the **guild ids** you allow: enable Developer Mode in Discord (User Settings > Advanced), then right-click a
   server > **Copy Server ID**.

Discord permissions are per channel: a channel can deny the bot a permission with an overwrite even when the server
role allows it. The server computes the bot's effective permissions itself (section 10 of the spec) and tells you which
one is missing and in which channel.

## 2. Install and run

Prerequisites: Windows 10/11 with WSL2 (Ubuntu) and Docker Engine inside WSL2 (with the Compose plugin).

```bash
cd discord-mcp-server
scripts/setup.sh      # asks for the bot token (hidden), the guild allowlist and the port; generates the MCP token
scripts/start.sh      # builds the image, starts the container, waits for health, prints client snippets
scripts/smoke.sh      # optional: calls list_guilds through the container and prints sizes only
```

On Windows run the `.ps1` twin of any script (for example `.\scripts\start.ps1`); it forwards into WSL2.

`setup.sh` writes `.env` (git-ignored, excluded from the Docker build context). Secrets are never echoed. Run
`scripts/restart.sh --recreate` after changing `.env`.

## 3. Configuration

All settings are read in `config.py`. Unknown variables are ignored. Config errors name the field, never its value.

| Setting | Default | Notes |
|---|---|---|
| `MCP_AUTH_TOKEN` | required | Bearer token MCP clients send. At least 32 characters. |
| `DISCORD_BOT_TOKEN` | required | Secret. Three dot-separated segments. |
| `DISCORD_ALLOWED_GUILD_IDS` | required | Comma-separated snowflakes, 1 to 100, no duplicates. Default-deny. |
| `DISCORD_APPLICATION_ID` | unset | Optional. A WARNING is logged if it differs from the token's application. |
| `DISCORD_API_BASE` | `https://discord.com/api/v10` | Must be `https://discord.com/api/v<N>`, N >= 10. |
| `DISCORD_USER_AGENT` | `DiscordBot (https://github.com/leeray75/mcp-servers, 0.1.0)` | Must look like `DiscordBot (<url>, <version>)`. |
| `DISCORD_ENABLE_SEARCH` | `true` | Can only disable `search_messages`. |
| `DISCORD_ENABLE_MEMBER_LIST` | `true` | Can only disable `list_members`. |
| `DISCORD_FORCE_MESSAGE_CONTENT` | `false` | Registers `search_messages` even when the flags do not show the intent (A1-R6). Never overrides `DISCORD_ENABLE_SEARCH=false`. |
| `DISCORD_FORCE_GUILD_MEMBERS` | `false` | Registers `list_members` regardless of the detected intent (A1-R6). Never overrides `DISCORD_ENABLE_MEMBER_LIST=false`. |
| `DISCORD_TIMEOUT_SECONDS` | `10` | 1 to 60. |
| `DISCORD_MAX_RESPONSE_BYTES` | `4194304` | Upstream body cap (64 KiB to 16 MiB; default 4 MiB, A1-R16). A page over the cap is an error with a hint to retry with a smaller `limit`, never a shortened result. |
| `DISCORD_MAX_RATE_LIMIT_WAIT_SECONDS` | `30` | Longest a call waits for a rate-limit bucket (0 to 120). Longer waits fail fast with `retry_after`. |
| `DISCORD_MAX_429_RETRIES` | `3` | Replays of a user- or global-scoped 429 within one call (0 to 10). |
| `DISCORD_SEARCH_INDEX_RETRIES` | `2` | Retries while a search index builds (0 to 5). |
| `DISCORD_GLOBAL_REQUESTS_PER_SECOND` | `40` | Process-wide pacing (1 to 50). |
| `DISCORD_INVALID_REQUEST_BUDGET` | `1000` | 401/403/429 responses per 10 minutes before the circuit opens (10 to 9000). |
| `TTL_METADATA_SECONDS` | `300` | Cache for guild, roles, channels and the bot's member record only (0 disables). |
| `CACHE_MAX_ENTRIES` / `CACHE_MAX_MB` | `2000` / `64` | Cache bounds. |
| `TOOL_CALLS_PER_MINUTE` | `60` | Overall tool-call budget. Excess calls are refused, not queued. |
| `TOOL_CALLS_PER_MINUTE_PER_TOOL` | `30` | Per-tool budget. |
| `HOST_PORT` | `8769` | Compose only: published on `127.0.0.1`. |
| `LOG_LEVEL` | `INFO` | |

## 4. Tools

Every tool is read-only and is described to the agent with its required permission or intent, its caps and when not to
use it. Results carry `meta` (`as_of`, `cached`, `cache_age_seconds`, `endpoint`). Errors are tool errors
(`isError: true`) with `kind`, `message`, `code`, `http_status`, `retryable`, `retry_after` and `hint`.

| Tool | Purpose | Needs |
|---|---|---|
| `list_guilds` | Allowlisted guilds the bot is in, plus `missing_allowlisted_guild_ids` | nothing |
| `get_guild` | One guild: owner, counts, features, roles | allowlist |
| `list_channels` | Channels in categorical order, with `bot_can_view` and `bot_can_read_history` | allowlist |
| `get_channel` | One channel (thread fields for threads) | `VIEW_CHANNEL` |
| `list_messages` | Messages, newest first (`limit` 1-100, one of `before`/`after`/`around`) | `VIEW_CHANNEL`, `READ_MESSAGE_HISTORY` (+`CONNECT` in voice) |
| `get_message` | One message | as `list_messages` |
| `search_messages` | Guild search: `content`, `author_ids`, `channel_ids` (max 25, looked up in parallel), `mentions`, `has` (prefix `-` to exclude), paging. Results carry `reactions: null`; use `list_reactions` for reactions | message-content intent |
| `list_reactions` | Users who reacted with one emoji | as `list_messages` |
| `list_pins` | Pinned messages (`limit` 1-50, `before`) | as `list_messages` |
| `list_threads` | Active threads, optionally under one parent channel, with `bot_joined` | allowlist |
| `list_members` | Guild members (`limit` 1-1000, always sent explicitly) | server-members intent |
| `get_member` | One member by user id | nothing privileged |
| `search_members` | Members whose username or nickname starts with a query | nothing privileged |
| `get_audit_log` | Audit entries (`user_id`, `action_type` 1 or more, `before`/`after` as a snowflake or ISO datetime, `after` also `"0"` for oldest first, `limit` 1-100) | `VIEW_AUDIT_LOG` at guild level |

Tools whose intent is off are **not registered** at all. A tool that is not in `tools/list` is not a bug.

**Result size (D11, A1-R43).** Results are complete pages, not truncated. This is a deliberate departure from the
sibling servers' per-tool size budgets: the agent controls size with `limit` and the cursors. Measured worst-case
projections at each tool's maximum `limit` (`tests/test_sizes.py`, all under 4 MiB structured):

| Tool (worst case) | Structured JSON | Structured + text block (R11) |
|---|---|---|
| `list_guilds` (100 guilds) | 25 KB | 51 KB |
| `get_guild` (250 roles) | 63 KB | 126 KB |
| `list_channels` (500 channels) | 713 KB | 1.4 MB |
| `get_channel` | 2 KB | 3 KB |
| `list_messages` (100 messages) | 2.83 MB | 5.66 MB |
| `get_message` | 28 KB | 57 KB |
| `search_messages` (25 hits) | 695 KB | 1.39 MB |
| `list_reactions` (100 users) | 14 KB | 27 KB |
| `list_pins` (50 pins) | 1.42 MB | 2.83 MB |
| `list_threads` (500 threads) | 799 KB | 1.6 MB |
| `list_members` (1000 members) | 1.36 MB | 2.72 MB |
| `get_member` | 1.5 KB | 3 KB |
| `search_members` (1000 members) | 1.36 MB | 2.72 MB |
| `get_audit_log` (100 entries) | 2.27 MB | 4.54 MB |

The structured figure is what A1-R43 asserts. The text block (R11) repeats the same data, so the on-wire total is
roughly double; for the largest pages that total can exceed 4 MiB. Lower `limit` if a client chokes on very large results.

Returned Discord content (messages, names, topics, embeds) is untrusted third-party text, not instructions. The
server returns it in full (no per-item truncation) and never fetches the links in it.

With `DISCORD_USER_OAUTH=true` five account tools are added after these (see the next section). They use the human's own
Discord account, not the bot, and none of them reads guild content.

| Tool | Purpose | Needs |
|---|---|---|
| `discord_account_status` | Connected or not, username, scopes, expiry, pending sign-in, last OAuth error. No network request | nothing |
| `connect_discord_account` | Returns `sign_in_url` (the browser opens it). Sends nothing to Discord. `reconnect=true` replaces a connection | nothing |
| `disconnect_discord_account` | Revokes the grant (best effort) and always deletes the local token file | a connected account (or none) |
| `list_my_guilds` | Your servers with `allowlisted`, `bot_present`, `can_add_bot`; `with_counts` adds approximate counts | a connected account |
| `get_guild_invite_link` | Bot invite URL for one guild (`permissions=66688`). No request | nothing |

### User OAuth guild discovery (optional)

**What it can do.** List the servers you belong to, see which ones the bot is already in, which ones you can add it to
(owner, Administrator or Manage Server), and get a one-click invite link. That is all.

**What it cannot do.** Discord's user OAuth scopes used here (`identify guilds`) do not read channels, messages, members or
audit logs. Reading guild content as a member who is not the bot's owner is not possible through any sanctioned API, so this
feature never does it.

**Portal setup (once).** In the Developer Portal, open your application, go to OAuth2, copy the **Client Secret** and add
this exact **Redirect URI**: `http://127.0.0.1:<HOST_PORT>/oauth/callback` (default `http://127.0.0.1:8769/oauth/callback`).
`HOST_PORT` must match the value in `.env`. The redirect is derived from it; there is no separate setting.

**Enable.** Run `scripts/setup.sh` and answer yes to "Enable user OAuth guild discovery?", then enter the application id
and the client secret (hidden). Or set in `.env`: `DISCORD_USER_OAUTH=true`, `DISCORD_APPLICATION_ID`,
`DISCORD_OAUTH_CLIENT_SECRET`. Then `scripts/restart.sh --recreate` (compose then also mounts `./data`).

**Sign-in.** Call `connect_discord_account`, open the `sign_in_url` it returns (or accept the client's prompt), approve on
Discord, and wait for "Discord account connected". Then `discord_account_status` shows `connected`. The sign-in page is served
by the MCP server on `127.0.0.1` only; `state` (single use, 10 minutes), `iss` and the code never reach the model.

**Token file.** `./data/discord_user_token.json` on the host (`/data/discord_user_token.json` in the container), written
atomically with mode 0600 (not enforced on `/mnt/c`, as with Gmail). The folder is git-ignored and must be writable by the container user (UID 10001); on a native Linux filesystem run
`sudo chown 10001:10001 data`. Temporary `.discord_user_token.json.*.tmp` files left by an interrupted save hold
tokens; the server deletes them at the next start or save. To remove the account, call
`disconnect_discord_account`, or stop the container and delete the file by hand. If the server cannot delete the file
(for example a permission error), `disconnect_discord_account` returns `kind=unavailable`; the token is already out of
the server's memory and the revoke result still applies, but the file remains on disk and must be deleted by hand. The
error hint names the configured path, never the contents (A3.2, U18). Reconnecting does **not** revoke the previous grant (D29): revoking one token revokes every token of that
authorization, which can include the new one. Old grants can be removed under Discord User Settings → Authorized
Apps. The live check AC-L1 records what Discord does on reconnect.

**Lifetime.** Discord access tokens last about 7 days. The server refreshes them shortly before expiry (within 5 minutes)
and after a rejected request, one refresh at a time. A rejected refresh clears the token and asks you to connect again.
Refresh-token rotation is not documented by Discord; the server stores whatever Discord returns.

**Kill switches.** `DISCORD_OAUTH_URL_ELICITATION=false` disables the URL elicitation prompt (the plain result is returned).
`DISCORD_OAUTH_PKCE=false` drops the PKCE parameters if Discord rejects them. Set it only if sign-in fails with PKCE on.

**Routes.** `GET /oauth/start` and `GET /oauth/callback` are unauthenticated, but only on `127.0.0.1` behind the Host and
Origin guard, only while this feature is on, and only with `GET` (a non-GET request without a valid bearer gets `401`; with a valid bearer it gets `405`). They are protected by single-use
`state`, the `iss` check and the 10-minute expiry, and they show fixed pages that echo nothing.

## 5. Client configuration

The endpoint is `http://127.0.0.1:8769/mcp`. Send `Authorization: Bearer <MCP_AUTH_TOKEN>` (the value in your `.env`).
`start.sh` prints both snippets with the placeholder token; it never prints the real one.

Claude Code:

```bash
claude mcp add --transport http discord http://127.0.0.1:8769/mcp \
  --header "Authorization: Bearer <MCP_AUTH_TOKEN>"
```

Goose (`config.yaml`, extensions section):

```yaml
discord:
  name: discord
  description: Read-only Discord servers the bot may read (allowlisted guilds)
  type: streamable_http
  enabled: true
  uri: http://127.0.0.1:8769/mcp
  headers:
    Authorization: "Bearer <MCP_AUTH_TOKEN>"
  timeout: 60
```

## 6. Behaviour you should know about

- **Empty is not forbidden.** If the bot cannot read a channel's history, `list_messages` returns `kind=permission`
  naming the missing permission and the channel. Discord would otherwise return `[]`, and the server never makes that
  request. A channel that is genuinely empty returns `messages: []` with success.
- **Search can be not ready.** A cold search index returns HTTP 202 with code 110000. The server waits up to
  `retry_after` (within the in-call limit) and retries; if it is still building it returns `kind=index_pending`, which
  is not "no results".
- **Rate limits are learned, not hard-coded.** Buckets come from Discord's headers. A `retry_after` longer than the
  in-call limit returns `kind=rate_limited` with the full remaining wait, and calls to that bucket are refused locally
  until it clears.
- **Auth hard-stop.** The first `401` from Discord stops every later Discord request. Every tool returns `kind=auth`
  until you rotate the token and restart. `/healthz` reports `degraded`.
- **Boot.** The server probes `/users/@me` and `/applications/@me` before it accepts MCP traffic. If Discord is
  unreachable, intent tools are omitted and `/healthz` reports `discord_unreachable_at_boot`; restart to re-probe.
- **Channels from 2026-11-16.** Discord omits channels the bot cannot view from the channel list.
- **Audit log retention.** Discord keeps audit entries for about 45 days.
- **Channel refusals are identical.** A DM, an unknown channel, and a channel in a guild that is not allowlisted all
  return `forbidden_guild` with the same message, "Channel is not in an allowlisted guild", so the result does not
  reveal whether a channel exists. Auth, rate-limit and unavailable errors still surface as themselves.
- **Intent detection.** `flags` and `flags_new` are read independently; malformed values are ignored (A2). The
  effective flags are the bitwise OR of every usable field, so a field that carries only some intent bits does not
  hide the others. If neither is usable, the intent tools are omitted with reason `flags_missing`. The boot line
  records the effective flags integer and `flags_source` (`flags`, `flags_new`, `flags+flags_new`, or `null`). `DISCORD_FORCE_*` registers a tool regardless, marked `forced` in the boot line.
- **`search_members`.** Discord documents no privileged intent for this endpoint. A permission error returns a hint to
  enable the GUILD_MEMBERS intent.

## 7. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `kind=forbidden_guild` | The guild or channel is not in `DISCORD_ALLOWED_GUILD_IDS`, or it is a DM. Add the guild id to `.env` and restart with `--recreate`. |
| `kind=permission`, "Missing READ_MESSAGE_HISTORY" | Grant the permission to the bot's role in that channel (or remove the channel overwrite that denies it). |
| `kind=index_pending` | Search index still building. Retry after `retry_after`. |
| `kind=auth` / `/healthz` reason `discord_auth` | The token was rejected. Reset it in the Developer Portal, update `.env`, run `scripts/restart.sh --recreate`. |
| `/healthz` reason `discord_unreachable_at_boot` | Discord could not be reached when the server started. Check WSL networking, then `scripts/restart.sh`. |
| `kind=rate_limited` | Wait `retry_after` seconds. Calls are refused, not queued. |
| A search tool is missing from the client | The message-content intent is off in the Developer Portal, or `DISCORD_ENABLE_SEARCH=false`. |
| `kind=validation` | An argument is out of range or malformed. The message names the field. |
| Boot line `omitted: …=flags_missing` | Discord did not return usable application flags. If you verified the intent in the portal, set `DISCORD_FORCE_MESSAGE_CONTENT` or `DISCORD_FORCE_GUILD_MEMBERS` to `true`, then `scripts/restart.sh --recreate`. |
| `kind=upstream`, "exceeded the configured size cap" | A page is larger than `DISCORD_MAX_RESPONSE_BYTES`. Retry with a smaller `limit`. |
| `kind=permission` from `search_members` with a GUILD_MEMBERS hint | Enable the Server Members intent in the Developer Portal (Discord documents none for this endpoint). |

## 8. Operations

| Script | What it does |
|---|---|
| `scripts/setup.sh` | Interactive `.env` creation or update (values never echoed) |
| `scripts/start.sh` / `stop.sh` | Build if needed, start, wait for health / stop and remove the container |
| `scripts/restart.sh [--recreate]` | Restart; `--recreate` is required after editing `.env` |
| `scripts/rebuild.sh [--keep-cache]` | Rebuild the image and recreate the container |
| `scripts/logs.sh [-n N] [--no-follow]` | Structured JSON logs (never tokens, URLs, ids or message content) |
| `scripts/status.sh` | Container state, health, endpoint and allowlist size (ids are not printed) |
| `scripts/smoke.sh` | Calls `list_guilds` through the running container and prints sizes |

Opt-in live check with your own token (not run by the tests): `python tools/try_live.py list_guilds`, run from a
virtualenv that has the package installed. It sends real requests; run it only against servers you administer.

## 9. Security posture

- Loopback only (`127.0.0.1:8769`), bearer token on every MCP request, Host and Origin checks, 64 KiB request cap.
- Read-only container: non-root UID 10001, read-only root filesystem, all capabilities dropped,
  `no-new-privileges`, PID, memory and CPU limits. No host mounts.
- Only the configured Discord API host is contacted; redirects are never followed; response size is capped.
- Every guild and channel goes through one allowlist guard (default-deny), with no request for a refused guild id.
- Logs and error messages never contain the tokens, request URLs, Discord response bodies or message content.

## 10. Development

```bash
uv venv ~/.venvs/discord-mcp --python 3.12
make install VENV=~/.venvs/discord-mcp PYTHON=python3
make check VENV=~/.venvs/discord-mcp      # ruff + mypy --strict + pytest (coverage gate 90%, branch)
make audit VENV=~/.venvs/discord-mcp      # pip-audit on the hash-pinned runtime locks
make lock VENV=~/.venvs/discord-mcp       # regenerate requirements*.txt (never edit them by hand)
```

Tests are offline: an autouse fixture fails any real socket use, and Discord is served from synthetic fixtures in
`tests/fixtures/`. See [`CLAUDE.md`](CLAUDE.md) for the architecture and invariants.

## License

MIT, as the rest of this repository (see the root [`LICENSE`](../LICENSE)).
