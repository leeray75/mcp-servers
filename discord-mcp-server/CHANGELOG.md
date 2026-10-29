# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed (fix round 1, review M1 and m1-m11)

- A bucket lease is released on every path, including cancellation and non-transport request errors; a cancelled
  in-flight request can no longer wedge its bucket. `httpx.DecodingError` maps to `kind=upstream`.
- A shared bucket keeps its first learned state; a non-finite or negative `retry_after` is treated as absent.
- Malformed upstream bodies map to `kind=upstream` (not an internal error). `type_id` is null when Discord omits the type.
- Children sort under their own parent when categories share a position.
- Search channel lookups run concurrently; the first failure is reported in `channel_ids` order.
- Channel refusals use one identical message (A1-R13). The search channel mismatch uses the same message.

### Changed (SPEC section 16, amendment A1)

- `obfuscated` removed from channels. `get_channel` returns `bot_can_view` and `bot_can_read_history`.
  Inapplicable keys are omitted rather than null.
- `search_messages`: `channel_ids` max 25; `has` values may be prefixed with `-`; results carry `reactions: null`.
- `search_members`: a permission error carries a hint to enable the GUILD_MEMBERS intent.
- `get_audit_log`: `before`/`after` accept snowflakes or ISO datetimes, `after` also `"0"`; `action_type` 1 or more
  (no upper bound).
- Audit action names transcribed from the Discord Audit Log Events table (69 events, values 1-193); unknown values are null.
- `DISCORD_MAX_RESPONSE_BYTES` default 4 MiB, with a hint to retry with a smaller limit.
- New settings `DISCORD_FORCE_MESSAGE_CONTENT` and `DISCORD_FORCE_GUILD_MEMBERS` (default false).
- Intents read from `flags` and `flags_new` independently (A2): malformed values are ignored, the effective flags
  are the bitwise OR of every usable field, and the boot line records the flags and `flags_source`.
- Worst-case size test per tool (A1-R43), with measured figures in the README.

### Added

- Initial build of `discord-mcp-server` (spec: `docs/SPEC.md`, approved 2026-10-28).
- Fourteen read-only tools over Discord REST API v10: `list_guilds`, `get_guild`, `list_channels`, `get_channel`,
  `list_messages`, `get_message`, `search_messages`, `list_reactions`, `list_pins`, `list_threads`, `list_members`,
  `get_member`, `search_members`, `get_audit_log`. Intent-dependent tools are registered only when the privileged
  intent is on (detected at boot from the application flags).
- Default-deny guild allowlist (`DISCORD_ALLOWED_GUILD_IDS`) enforced by one guard for every tool.
- Effective channel permission computation with explicit `permission` errors before any request that would otherwise
  return an empty list (empty versus forbidden).
- Rate-limit handling learned from Discord headers: per-bucket serialisation, pre-emptive waits, user, global and shared
  429 scopes, a global pacing bucket and an invalid-request circuit.
- Search index handling (202 with code 110000): bounded retries, then `index_pending`.
- Auth hard-stop on the first 401; boot probe with `/healthz` states `ok`, `discord_auth` and
  `discord_unreachable_at_boot`.
- Tool-call budget (overall and per tool), short-TTL metadata cache, structured JSON logs with redaction.
- Container: Streamable HTTP on `127.0.0.1:8769`, bearer authentication, Host/Origin guard, read-only hardened Docker
  image, operations scripts (`setup`, `start`, `stop`, `restart`, `rebuild`, `logs`, `status`, `smoke`) with PowerShell
  twins.
