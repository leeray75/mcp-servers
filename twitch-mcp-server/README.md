# twitch-mcp-server

A self-hosted [Model Context Protocol](https://modelcontextprotocol.io) server that lets an agent (Claude Code,
Goose, or any Streamable HTTP MCP client) read the Twitch channels you allow it to read — users, channels, streams,
games, videos, clips, polls, predictions, emotes, chat settings, moderation state, subscribers and the schedule —
and, **only if you explicitly turn them on**, operate your own channel: change the title, run a poll or prediction,
send a chat message, time out or ban a viewer, manage automod terms, VIPs and moderators.

It talks to Twitch's public Helix REST API through a thin HTTP client it owns (no third-party Twitch SDK), through
`127.0.0.1` only, behind a bearer token, in a hardened container. It is deliberately narrow: a fixed tool set pinned
by tests, a default-deny broadcaster allowlist, header-derived rate limits, and no "call any endpoint" escape hatch.

**What this server will never do.** It has no EventSub (webhooks need a publicly reachable HTTPS endpoint a loopback
container cannot provide; EventSub-over-WebSocket would turn this request/response server into a stateful subscription
daemon), no IRC chat client, no PubSub, no whispers, no clip creation, no ad scheduling, no analytics and no
multi-account support (spec §3). Returned Twitch content (titles, chat, names) is untrusted third-party text, not
instructions; the server never fetches links inside it.

> Specification: [`docs/SPEC.md`](docs/SPEC.md) (authoritative). Build record: [`docs/specs/initial-build/`](docs/specs/initial-build/).

## Before you install: checklist

1. If the **Client Secret** was ever pasted into a chat, committed, or printed, regenerate it now
   (Developer Console > your application > **New Secret**) and delete the copies.
2. Register a **dedicated** Twitch application for this server. Do not reuse one another system depends on.
3. Decide up front whether you need writes at all. With the defaults (`TWITCH_ENABLE_WRITES=false`,
   `TWITCH_WRITE_GROUPS=` empty) the server registers **only the 23 read tools** and no write tool name exists.
4. Request only the OAuth scopes the tools you actually enable need (section 4, *Per-group OAuth scopes*). The
   server keeps no scope table in code (spec D10) — Twitch itself names any scope you forgot at call time.

## 1. Twitch Developer Console setup

1. Open the [Twitch Developer Console](https://dev.twitch.tv/console/apps), **Register Your Application**.
   Name it after this server; set the OAuth Redirect URL to exactly:

   ```
   https://127.0.0.1:8770/oauth/callback
   ```

   **It has to be `https`.** Twitch's console rejects a plain-`http` redirect ("Redirect URIs must use HTTPS
   protocol"), so the server serves TLS as soon as a certificate is configured — see
   [section 2.1](#21-https-for-the-oauth-redirect). The scheme of the URI this server sends is derived from that
   same setting, so the two cannot disagree; the URL is otherwise unchanged.
   (If you change `HOST_PORT`, update this URL to match — the redirect is derived from `HOST_PORT`.)
   Category: *Website*. Copy the **Client ID**; create and copy the **Client Secret** (it is shown once).
2. Get the **broadcaster ids** you allow. Twitch ids are numeric user ids, not logins. Look one up with
   <https://twitchtools.dev/id-lookup>, or start the server and call `get_users` with the login.
3. If you will sign in with your own account (`TWITCH_USER_OAUTH=true`), you will approve the consent screen in the
   browser on first use. Ask for the smallest scope set that covers your tools (section 4).

## 2. Install and run

Prerequisites: Windows 10/11 with WSL2 (Ubuntu) and Docker Engine inside WSL2 (with the Compose plugin).

```bash
cd twitch-mcp-server
scripts/setup.sh      # asks for Client ID/Secret (hidden), the broadcaster allowlist and the port; generates the MCP token
scripts/start.sh      # builds the image, starts the container, waits for health, prints client snippets
scripts/smoke.sh      # optional: calls three read tools through the container and prints sizes only
```

On Windows run the `.ps1` twin of any script (for example `.\scripts\start.ps1`); it forwards into WSL2.

`setup.sh` writes `.env` (git-ignored, excluded from the Docker build context). Secrets are never echoed. Run
`scripts/restart.sh --recreate` after changing `.env`. With `TWITCH_USER_OAUTH=true`, compose additionally mounts the
Docker named volume `twitch-mcp-data` at `/data` for the token file. The token is kept inside Docker, not in the
repository tree, and `setup.sh` creates no folder. The volume survives `stop`, `restart`, `rebuild` and `compose down`.
To delete the stored credential, run `disconnect_twitch_account` (it deletes the file and revokes the token), or after
`scripts/stop.sh` run `docker volume rm twitch-mcp-data`.

Operator notes: an existing `./data/twitch_user_token.json` is left in place by the upgrade. It is a live refresh
token, so delete it after you sign in again. Turning `TWITCH_USER_OAUTH` off leaves the volume, and so the token, in
place until `docker volume rm twitch-mcp-data`. Docker copies the image's `/data` ownership into a new empty volume
only; an existing volume is not re-initialised. Legacy upgrade: older versions stored the token in `./data`; delete any
`twitch_user_token.json` there after upgrading.

### 2.1 HTTPS for the OAuth redirect

Twitch's console accepts only an `https` Redirect URL, so signing in with your own account (`TWITCH_USER_OAUTH=true`)
requires the container to serve TLS. It is opt-in: with no certificate configured the server answers plain HTTP on
loopback exactly as before, and the 16 app-token read tools never needed OAuth or TLS in the first place.

One command:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\make-cert.ps1   # Windows (trusts the CA for you)
scripts/make-cert.sh                                             # WSL2 / Linux (generate only)
```

That creates a small local certificate authority and a leaf for `CN=localhost` with
`SAN DNS:localhost,IP:127.0.0.1` — 398 days, under a 10-year CA — in the git-ignored `certs/` folder, then adds the
CA to **your** user's trusted-root store with `certutil -addstore -user Root certs\ca.crt`. That is a per-user trust
change: no administrator rights, nothing machine-wide, and no certificate warning in the browser when Twitch
redirects back. Re-run with `--force` to reissue. The script prints only the CA path and its SHA-256 fingerprint;
key material is never printed.

On macOS (or any non-Windows host) run `scripts/make-cert.sh` and import the CA into your **login keychain**, which
is likewise per-user and needs no administrator rights:

```bash
security add-trusted-cert -k ~/Library/Keychains/login.keychain-db certs/ca.crt
```

`make-cert.sh` prints whichever of the two commands applies to the machine it runs on.

Then set the two container-side paths in `.env` and recreate the container:

```dotenv
TWITCH_TLS_CERT_FILE=/certs/server.crt
TWITCH_TLS_KEY_FILE=/certs/server.key
```

```bash
scripts/restart.sh --recreate
```

Once both are set, every `scripts/*.sh` command picks `docker-compose.tls.yml` up by itself (the `compose` helper in
`scripts/lib/common.sh` adds `-f docker-compose.tls.yml` to each `docker compose` call). Bare `docker compose up` does
not read `.env` for that decision, so run it with both files if you use it directly:
`docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d`. The override mounts `./certs` read-only at
`/certs` and nothing else; the root filesystem stays read-only and `/certs` is never writable, so the process cannot
rewrite the key it is serving. `scripts/status.sh` then reports `tls : on` and prints the exact URI to register.

What turning TLS on changes, and what it does not:

- Plain `http` requests to the same port are **not answered**: the TLS handshake fails inside the server and the
  client is never redirected to `https`. A client resends `Authorization: Bearer …` to a redirect target, so the
  server never sends one (S7).
- `GET /healthz` stays unauthenticated and gains one key, `"tls": true`/`false`. It never reports the certificate or
  key path, and neither does any log line.
- Bearer auth (R4), the Host/Origin guard (R5), the broadcaster allowlist (R25–R27) and the write kill switches
  (R28) are untouched. TLS adds transport security and removes none; the calls this server makes to Twitch already
  used it. Certificate verification is disabled for the container's own loopback health probe only.

## 3. Configuration

All settings are read in `config.py`. Unknown variables are ignored. Config errors name the field, never its value.
A `replace-me*` placeholder or a missing required value aborts startup (R7).

| Setting | Default | Notes |
|---|---|---|
| `MCP_AUTH_TOKEN` | required | Bearer token MCP clients send. At least 32 characters. |
| `TWITCH_CLIENT_ID` | required | Application Client ID (Developer Console). |
| `TWITCH_CLIENT_SECRET` | required | Secret. Used for the app token and the user code exchange. |
| `TWITCH_ALLOWED_BROADCASTER_IDS` | required | Comma-separated Twitch user ids, 1–20 digits each, 1–100 entries. Default-deny. An empty value aborts startup. |
| `TWITCH_API_BASE` | `https://api.twitch.tv/helix` | Must match exactly (R9); anything else is a startup failure. |
| `TWITCH_AUTH_BASE` | `https://id.twitch.tv` | Must match exactly (R9). |
| `TWITCH_TIMEOUT_SECONDS` | `10` | 1–60. |
| `TWITCH_MAX_RESPONSE_BYTES` | `4194304` | Upstream body cap (64 KiB–16 MiB). Over the cap is `kind=too_large` with a hint to lower `first`, never a truncated result (R14). |
| `TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS` | `30` | Longest a call waits for a rate-limit bucket (0–120). Longer waits fail fast with `retry_after` and make no request (R21). |
| `TWITCH_MAX_429_RETRIES` | `3` | Replays of a 429 within one call, **reads only** (0–10). Writes are never retried regardless of this value (R16). |
| `TWITCH_MAX_5XX_RETRIES` | `2` | Extra read attempts on 5xx/transport (0–10). A 503 is retried once (R15). |
| `TWITCH_USER_OAUTH` | `false` | User identity: `/oauth/*` routes, the two account tools, the token store, and user-token reads (R46). |
| `TWITCH_OAUTH_SCOPES` | unset | Space-delimited scope list sent to `/oauth2/authorize`. Operator configuration, not a code table (D10). |
| `TWITCH_OAUTH_TOKEN_FILE` | `/data/twitch_user_token.json` | Absolute path in the container. Written 0600 (R48). |
| `TWITCH_TLS_CERT_FILE` | *(empty)* | PEM certificate for the listener, path inside the container (`/certs/server.crt`). Set both TLS variables or neither (T1). Empty means the server answers plain HTTP, as before (T2). |
| `TWITCH_TLS_KEY_FILE` | *(empty)* | Private key for that certificate. The server does not check its mode or location (S5, S6): keep it owner-only (`chmod 600`) and never commit it. Never logged, never reported (T9). |
| `TWITCH_ENABLE_WRITES` | `false` | **Security-relevant kill switch 1 of 2.** No write tool exists while false (R28). |
| `TWITCH_WRITE_GROUPS` | *(empty)* | **Security-relevant kill switch 2 of 2.** Comma-separated subset of `channel,polls,predictions,chat,moderation,roles`. Empty means no group, even when `TWITCH_ENABLE_WRITES=true`. |
| `TWITCH_CONFIRM_TOKEN_TTL_SECONDS` | `300` | Confirmation-token lifetime (30–3600). Single-use, in-memory (R32). |
| `TWITCH_IDENTITY_TTL_SECONDS` | `300` | How long a verified moderator/editor status may be cached; invalidated on refresh, disconnect and the first `401` (R30). |
| `TWITCH_DISABLE_<TOOL>` | `false` | Per-tool kill switch, e.g. `TWITCH_DISABLE_SEND_CHAT_MESSAGE=true` (R36). |
| `TTL_METADATA_SECONDS` | `300` | Cache for slow-changing metadata and identity verification only (0 disables). |
| `CACHE_MAX_ENTRIES` / `CACHE_MAX_MB` | `2000` / `64` | Cache bounds. |
| `HOST_PORT` | `8770` | Compose only: published on `127.0.0.1` only. |
| `LOG_LEVEL` | `INFO` | |

## 4. Tools

Results carry `meta` (`as_of`, `cached`, `cache_age_seconds`, `endpoint`). Errors are tool errors (`isError: true`)
with `kind`, `message`, `code`, `http_status`, `retryable`, `retry_after` and `hint` (R43). A page is returned
complete — never truncated to fit a budget (R41).

### Reads (23) — always available

This is `capabilities.READ_TOOLS`, verbatim from spec §6.4, in registration order:

| # | Tool | Helix | Token |
|---|---|---|---|
| 1 | `twitch_account_status` | `GET /oauth2/validate` | app / user |
| 2 | `get_users` | `GET /users` | app |
| 3 | `get_users_follows` | `GET /users/follows` | app |
| 4 | `get_channels` | `GET /channels` | app |
| 5 | `search_channels` | `GET /search/channels` | app |
| 6 | `get_streams` | `GET /streams` | app |
| 7 | `get_top_games` | `GET /games/top` | app |
| 8 | `get_games` | `GET /games` | app |
| 9 | `get_videos` | `GET /videos` | app |
| 10 | `get_clips` | `GET /clips` | app |
| 11 | `get_polls` | `GET /polls` | app |
| 12 | `get_predictions` | `GET /predictions` | app |
| 13 | `get_emotes` | `GET /chat/emotes` (+`/set`, `/global`) | app |
| 14 | `get_channel_chat_settings` | `GET /chat/settings` | app |
| 15 | `get_channel_chat_badges` | `GET /chat/badges` | app |
| 16 | `get_stream_markers` | `GET /streams/markers` | user |
| 17 | `get_broadcaster_subscribers` | `GET /subscriptions` | user |
| 18 | `get_channel_editors` | `GET /channels/editors` | user |
| 19 | `get_moderators` | `GET /moderation/moderators` | user |
| 20 | `get_banned_users` | `GET /moderation/banned` | user |
| 21 | `get_automod_settings` | `GET /moderation/automod/settings` | user |
| 22 | `get_chat_chatters` | `GET /chat/chatters` | user |
| 23 | `get_schedule` | `GET /schedule` | app |

A *user-token* tool is omitted from `tools/list` while `TWITCH_USER_OAUTH=false` or before you have signed in (R36)
— that omission is by design, not a bug.

### Account (2) — when `TWITCH_USER_OAUTH=true`

`connect_twitch_account` returns a `sign_in_url` you open in the browser; `disconnect_twitch_account` revokes the
grant (`POST /oauth2/revoke`) and deletes only the server's own stored credential — it performs no other account
action (R51).

### Writes (16) — six groups, both kill switches required

A write tool exists only when `TWITCH_ENABLE_WRITES=true` **and** its group is listed in `TWITCH_WRITE_GROUPS`
(R28). Both defaults are off.

| Group | Tools | Helix |
|---|---|---|
| `channel` | `update_channel` | `PATCH /channels` |
| `polls` | `create_poll`, `end_poll` | `POST /polls`, `PATCH /polls` |
| `predictions` | `create_prediction`, `end_prediction` | `POST /predictions`, `PATCH /predictions` |
| `chat` | `send_chat_message` | `POST /chat/messages` |
| `moderation` | `timeout_user`, `remove_timeout`, `ban_user`, `unban_user`, `set_automod_term`, `remove_automod_term` | `POST/DELETE /moderation/timeouts`, `POST/DELETE /moderation/banned`, `PUT/DELETE /moderation/automod/terms` |
| `roles` | `add_vip`, `remove_vip`, `add_moderator`, `remove_moderator` | `POST/DELETE /channels/vips`, `POST/DELETE /moderation/moderators` |

Maximum **41** tools (all write groups on + a signed-in account). The **default is 16** — the app-token reads only.
The 7 user-token reads and the 2 account tools appear **only once an account is actually signed in**, taking the
surface to 25 (23 reads + 2 account tools); `TWITCH_USER_OAUTH=true` alone is not enough, because a tool that could
only answer `unauthenticated` is not registered (R36, A2-D1).

### Per-group OAuth scopes

`TWITCH_OAUTH_SCOPES` is the space-delimited list sent to `https://id.twitch.tv/oauth2/authorize`. It is **operator
configuration, not a code table** (spec §6.6, D10): request only what the groups you enabled need. If a scope is
missing at call time the tool returns `kind=permission` with the scope Twitch itself named in the hint, plus the
scopes your token actually holds (R45) — never an empty list.

A channel operator who runs polls never needs `moderator:manage:timeouts`; a moderator never needs
`channel:manage:polls`. Request per group (scope names are Twitch's; verify in the
[scopes list](https://dev.twitch.tv/docs/authentication/scopes/) when you set them):

| You want | Request (in addition to reads you need) |
|---|---|
| Sign in / identity (`twitch_account_status`, `connect_*`) | `user:read:email` |
| Read your channel's subscriber list (`get_broadcaster_subscribers`) | `channel:read:subscriptions` |
| `channel` group (`update_channel`) | `channel:manage:broadcast` |
| `polls` group | `channel:manage:polls` (read: `polls:read`) |
| `predictions` group | `channel:manage:predictions` |
| `chat` group (`send_chat_message`) | `user:chat` (send as yourself) |
| `moderation` group | `moderator:manage:timeouts`, `moderator:manage:bans`, `moderator:manage:automod`, `moderator:read:automod_settings` |
| `roles` group | `channel:manage:vips`, `moderator:manage:moderators` |
| `get_chat_chatters` | `moderator:read:chatters` |
| `get_stream_markers` | `user:read:broadcast` |

Example (own channel + polls, no moderation power):
`TWITCH_OAUTH_SCOPES=user:read:email channel:manage:broadcast channel:manage:polls polls:read`

### Result size (R41)

Results are complete pages; the agent controls size with `first` and the cursors. `tests/test_sizes.py` asserts every
tool's worst-case structured projection at its maximum `first` stays under 4 MiB.

Measured, not estimated, with the same worst-case bodies `tests/test_sizes.py` asserts against: every optional
field of every projected entity populated, at each tool's schema maximum `first`. `structuredContent` is the
serialized result; `text block` is the human-readable block the agent also receives. Both are complete pages —
nothing is trimmed to fit (R41).

| Tool (worst case) | `first` | `structuredContent` | text block |
|---|---|---|---|
| `get_users` | 100 | 151,024 B | 73,294 B |
| `get_users_follows` | 200 | 105,380 B | 50,567 B |
| `get_channels` | 100 | 271,236 B | 132,100 B |
| `search_channels` | 100 | 263,568 B | 128,861 B |
| `get_streams` | 100 | 258,950 B | 126,052 B |
| `get_top_games` | 100 | 44,790 B | 21,672 B |
| `get_games` | 100 | 44,782 B | 21,668 B |
| `get_videos` | 100 | 160,366 B | 77,360 B |
| `get_clips` | 100 | 124,914 B | 60,135 B |
| `get_polls` | 20 | 49,702 B | 23,788 B |
| `get_predictions` | 20 | 31,006 B | 14,780 B |
| `get_stream_markers` | 100 | **1,294,798 B** | 628,977 B |
| `get_broadcaster_subscribers` | 20 | 32,448 B | 15,600 B |
| `get_moderators` | 100 | 35,604 B | 16,879 B |
| `get_banned_users` | 100 | 104,000 B | 50,477 B |
| `get_channel_editors` | 100 | 35,470 B | 16,817 B |
| `get_emotes` | 100 | 62,370 B | 29,365 B |
| `get_channel_chat_badges` | 100 | **862,306 B** | 422,735 B |
| `get_channel_chat_settings` | 100 | 132,906 B | 64,435 B |
| `get_chat_chatters` | 1000 | 294,200 B | 140,977 B |
| `get_schedule` | 25 | 40,148 B | 19,445 B |
| `get_automod_settings` | n/a (one record) | 1,004 B | 371 B |

The largest is `get_stream_markers` at 1.29 MB — a third of the ceiling. `get_channel_chat_badges` is next at 842 KB,
because a fully populated badge set carries one entry per badge tier with its image URLs. Re-measure with
`PYTHONPATH=src pytest tests/test_sizes.py -q` (the assertion is the contract; these numbers are its by-product)
and keep this table in step with it.

Helix lists are **dynamic views**: entries can repeat or be dropped between pages. `pagination.has_more=false` when
Twitch returns an empty cursor `{}` (R42).

## 5. The two write kill switches and the confirm-token flow, in plain terms

Writes are the reason this server is different from its siblings, so the safety model is deliberately layered.
Four independent things must all be true before a single byte is written to Twitch:

1. **Kill switch 1**: `TWITCH_ENABLE_WRITES=true` in `.env`. Default `false`.
2. **Kill switch 2**: the group must be named in `TWITCH_WRITE_GROUPS` (empty by default). One flag is never enough:
   enabling writes is not an accident of a single setting (R28). If you flip switch 1 and forget switch 2, you get
   no write tools at all — which is the safe direction.
3. **Verified identity**: the target is either your own channel (the broadcaster id comes from *your token*, never
   from an argument) or a channel where the server has **verified** you appear in Twitch's moderator list (for
   moderation writes) or editor list (for channel-management writes). Your allowlist entry alone is never enough for
   a third-party write (R29). Verification is cached for `TWITCH_IDENTITY_TTL_SECONDS` and thrown away on token
   refresh, disconnect and the first `401` (R30).
4. **A single-use confirmation token**: the first call to any write tool does **not** write. It runs the guard and
   identity checks, then returns `kind=confirmation_required` with the resolved plan (tool, broadcaster, validated
   argument summary) and a token. Read the plan; call the same tool again with `confirm=<token>` to execute exactly
   one write. The token is single-use, expires after `TWITCH_CONFIRM_TOKEN_TTL_SECONDS` (default 300 s), is bound to
   `(tool, canonical-args-hash, broadcaster_id)` — replay it, let it expire, or change an argument and it fails with
   `kind=stale_confirmation` and no request (R31–R33). It lives only in memory, is never logged, never written to
   disk, and is cleared on disconnect, refresh, the auth circuit opening and process exit. A valid token still
   re-runs the allowlist guard before dispatch (R34).

And one rule that exists because Twitch is a network: **writes are never retried** — not on 5xx, not on a dropped
connection, not on 429 (R16). If a write's outcome is unknown (the connection died after the request left), the
result is `kind=unknown_outcome` with a hint to check the channel, *not* a second attempt: a retry could post that
chat message twice. Reads do retry (R15).

Practically: leave both switches off unless you need a group; enable one group at a time; treat
`kind=unknown_outcome` as "go look at the channel", never as "call it again".

## 6. Client configuration

The endpoint is `http://127.0.0.1:8770/mcp`. Send `Authorization: Bearer <MCP_AUTH_TOKEN>` (the value in your
`.env`). `start.sh` prints both snippets with the placeholder token; it never prints the real one.

Claude Code:

```bash
claude mcp add --transport http twitch http://127.0.0.1:8770/mcp \
  --header "Authorization: Bearer <MCP_AUTH_TOKEN>"
```

Goose (`config.yaml`, extensions section):

```yaml
twitch:
  name: twitch
  description: Allowlisted Twitch channels (reads; writes only when both write switches are on)
  type: streamable_http
  enabled: true
  uri: http://127.0.0.1:8770/mcp
  headers:
    Authorization: "Bearer <MCP_AUTH_TOKEN>"
  timeout: 60
```

## 7. Behaviour you should know about

- **Empty is not forbidden.** A scope you did not grant returns `kind=permission` naming the scope Twitch asked
  for and the scopes you hold (R45). An empty list always means genuinely empty.
- **Uniform denial.** An unknown id, a non-allowlisted broadcaster, and a 403/404/5xx while resolving ownership all
  return the identical `kind=forbidden_channel` message (R27) — the answer cannot be used to probe the allowlist.
- **Rate limits are learned, never hard-coded.** App and user buckets come from `Ratelimit-*` headers (R19/R20). A
  429 parks that bucket; a wait longer than `TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS` fails fast with `retry_after` and
  makes no request. There is no local per-minute budget: Twitch's own limits apply through these headers (R19–R22).
- **Auth circuit.** The first `401` from the API stops every later upstream request for the process lifetime; tools
  return `kind=unauthenticated` until you fix the credentials and restart (R24).
- **Boot is a probe, not a crash.** The server validates the bases, mints the app token, validates it (and your
  user token when on), then serves. A dead token leaves it **degraded** and visible in `/healthz`
  (`{status, version, tool_count, twitch_reachable}` only — never tokens, ids, logins, scopes or URLs, R3/R50).
- **Refresh never silently falls back.** If your user token cannot be refreshed, the store is cleared and you are
  asked to sign in again; a user-only tool will not quietly answer with the app token (R49).
- **Write audit.** A write is logged with its broadcaster id, tool name and outcome — and nothing else. Chat
  message *content* is never logged (R52/R53).
- **Process-wide state.** Caches and confirmation tokens live in the process; a restart clears them.

## 8. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Startup aborts naming a field | A required value is missing, empty, or still `replace-me*` (R7). Field names are reported, never values. |
| `kind=forbidden_channel` | The broadcaster id is not in `TWITCH_ALLOWED_BROADCASTER_IDS` (or its owner could not be resolved — the message is identical by design). Add the id and `scripts/restart.sh --recreate`. |
| `kind=not_authorized` on a write | You are neither the broadcaster (per your token) nor a verified moderator/editor of that channel. Add yourself on Twitch, wait for the identity cache to expire, or call `disconnect_twitch_account` and reconnect to invalidate it (R29/R30). |
| `kind=not_found` | Twitch has no such user, channel, video, tag or segment (a `404` on a read, R44/A2-D2). Check the id or login. This is *not* an allowlist answer: an id this server may not ask about at all reports `forbidden_channel`, never `not_found`. |
| `kind=confirmation_required` | Normal first turn of every write. Read the plan, then call the same tool with `confirm=<token>`. |
| `kind=stale_confirmation` | The token was used, expired, or the arguments changed. Make the first call again to mint a fresh one (R32). |
| `kind=unknown_outcome` on a write | The outcome is unknown and the server will **not** retry (R16). Check the channel on Twitch before doing anything else. |
| `kind=permission` naming a scope | Your token lacks that scope. Add it to `TWITCH_OAUTH_SCOPES` (section 4), then `disconnect_twitch_account` → `connect_twitch_account` to re-consent. |
| `kind=rate_limited` with `retry_after` | Wait it out; calls are refused, not queued. If it persists past `retry_after`, another consumer of your client id is spending the bucket. |
| `kind=unauthenticated` for every tool | The `401` circuit opened (R24). Fix the credentials in `.env`, `scripts/restart.sh --recreate`. |
| `kind=too_large` | The page exceeds `TWITCH_MAX_RESPONSE_BYTES`. Retry with a smaller `first` (R14). |
| A user-token tool is missing from `tools/list` | `TWITCH_USER_OAUTH=false` or you have not signed in yet (R36). |
| A write tool is missing from `tools/list` | `TWITCH_ENABLE_WRITES` is false or its group is not in `TWITCH_WRITE_GROUPS` (R28). This is the default. |
| `connect_twitch_account` says invalid `redirect_uri` | The Developer Console redirect URL does not exactly match `https://127.0.0.1:<HOST_PORT>/oauth/callback`. `scripts/status.sh` prints the exact URI for the current configuration. |
| The Twitch console rejects the redirect URL with *"Redirect URIs must use HTTPS protocol"* | TLS is not configured, so the server is offering `http`. Run `scripts/make-cert.ps1`, set `TWITCH_TLS_CERT_FILE` and `TWITCH_TLS_KEY_FILE` in `.env`, restart, and register the `https` URI (section 2.1). |
| Startup refuses with `... must both be set, or neither` | Only one of the two TLS variables is set in `.env`. Half a TLS configuration is a mistake, so it stops the process rather than silently serving `http` (T1). |
| `curl: (60) SSL certificate problem` on `https://127.0.0.1:8770/healthz` | The local CA is not trusted by that client. `certutil -addstore -user Root certs\ca.crt` for browsers on Windows, or pass `-k` for a one-off `curl` (the leaf is your own, self-issued). |
| Connection fails or closes for a client that still uses `http://127.0.0.1:8770/mcp` | Expected once TLS is on: cleartext fails at the TLS handshake and is never redirected, so there is no readable error text. Point the client at `https://127.0.0.1:8770/mcp`. |
| `/healthz` reports degraded | A token was invalid at boot (R50). Reconnect or restart after fixing `.env`. |
| `404` on `/oauth/callback` after approving | User OAuth is off in `.env`; the routes do not exist while off (R46). |

## 9. Operations

| Script | What it does |
|---|---|
| `scripts/setup.sh` | Interactive `.env` creation or update (secrets never echoed) |
| `scripts/start.sh` / `stop.sh` | Build if needed, start, wait for health / stop and remove the container |
| `scripts/restart.sh [--recreate]` | Restart; `--recreate` is required after editing `.env` |
| `scripts/rebuild.sh [--keep-cache]` | Rebuild the image and recreate the container |
| `scripts/logs.sh [-n N] [--no-follow]` | Structured JSON logs (never tokens, URLs, response bodies or chat content) |
| `scripts/status.sh` | Container state, health, endpoint, allowlist size, and whether writes are on (no ids, no values) |
| `scripts/smoke.sh` | Calls `twitch_account_status`, `get_channels`, `get_streams` through the running container and prints sizes |

Opt-in live checks (never part of `make check`): `pytest -m live --no-cov` and `python tools/try_live.py <tool>`,
run from a virtualenv that has the package installed and the operator's `.env`. They send real requests; run them
only against channels you own or moderate (spec A2/A3).

## 10. Security posture

- Loopback only (`127.0.0.1:8770`), bearer token on every route but `GET /healthz`, Host and Origin checks, request
  and response size caps, no TLS.
- Read-only container: non-root UID 10001, read-only root filesystem, all capabilities dropped,
  `no-new-privileges`, PID/memory/CPU limits. No host folder is mounted writable. User OAuth adds one named volume,
  `twitch-mcp-data`, at `/data` (user token file, mode 0600), added only when user OAuth is on; TLS adds `./certs`
  read-only at `/certs`.
- Only `api.twitch.tv` and `id.twitch.tv` are contacted; the two bases are pinned at startup; redirects are never
  followed; ids, cursors and logins are validated and percent-encoded — no agent-supplied string becomes a raw path
  segment.
- Default-deny broadcaster allowlist enforced before any request, with uniform denial (no probe oracle).
- Writes: two kill switches, verified identity, single-use confirmation tokens, and never retried.
- Logs, errors and `/healthz` never contain tokens, the client secret, the OAuth code, `state`, URLs with query
  strings, chat message bodies or Twitch response bodies.
- The attack surface the spec rejects: SSRF via base-URL pinning (R9), path traversal via ids/cursors (R17), CSRF on the
  callback (R47), token leak via logs or `/healthz` (R52), allowlist enumeration (R27), scope-table rot (D10), and
  double-submit on flaky networks (R16).

## 11. Development

```bash
uv venv ~/.venvs/twitch-mcp --python 3.12
make install VENV=~/.venvs/twitch-mcp PYTHON=python3
make check VENV=~/.venvs/twitch-mcp      # ruff + mypy --strict + pytest (coverage gate 90%, branch)
make audit VENV=~/.venvs/twitch-mcp      # pip-audit on the hash-pinned runtime locks
make lock VENV=~/.venvs/twitch-mcp       # regenerate requirements*.txt (never edit them by hand)
```

Tests are offline: an autouse fixture fails any real socket use (R55), fixtures are synthetic, and `pytest -m live`
is excluded by default. `make check` runs ruff, `mypy --strict` and the 90% branch-coverage gate (R56). See
[`CLAUDE.md`](CLAUDE.md) for the architecture and the invariants every change must preserve.

## License

MIT, as the rest of this repository (see the root [`LICENSE`](../LICENSE)).
