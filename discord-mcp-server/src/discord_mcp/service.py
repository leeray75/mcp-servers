"""DiscordService: one async method per tool (section 7, A1 amendments). Transport-independent.

Each method performs, in order: argument checks beyond the schema, the guild/channel guard
(R12, A1-R13), the permission pre-check where section 7 requires it (R29), the Discord request(s)
through ``DiscordClient`` and projection (section 8). Metadata responses are cached (D8); messages,
pins, reactions, search, members and audit log are never cached.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel

from discord_mcp import project
from discord_mcp.cache import Fetched, TTLCache
from discord_mcp.config import Settings
from discord_mcp.discord_client import BootProbe, DiscordClient, Route
from discord_mcp.errors import DiscordMcpError
from discord_mcp.guard import Guard
from discord_mcp.models import (
    AuditLogResult,
    ChannelListResult,
    ChannelResult,
    GuildResult,
    GuildSummary,
    ListGuildsResult,
    MemberListResult,
    MemberResult,
    MemberSearchResult,
    MessageListResult,
    MessageResult,
    Meta,
    PinListResult,
    ReactionListResult,
    SearchResult,
    ThreadListResult,
)
from discord_mcp.permissions import (
    CONNECT,
    READ_MESSAGE_HISTORY,
    VIEW_AUDIT_LOG,
    VIEW_CHANNEL,
    can_read_history,
    can_view,
    effective_permissions,
    missing_permissions,
)
from discord_mcp.snowflake import (
    time_argument_to_datetime,
    time_argument_to_snowflake,
    to_iso,
    validate_snowflake,
)

logger = logging.getLogger(__name__)

GUILD_PAGE_LIMIT: Final = 200
GUILD_MAX_PAGES: Final = 5
SEARCH_MAX_CONTENT: Final = 1024
SEARCH_MAX_AUTHORS: Final = 100
SEARCH_MAX_MENTIONS: Final = 100
SEARCH_MAX_CHANNELS: Final = 25
SEARCH_HAS_VALUES: Final = frozenset(
    {"image", "sound", "video", "file", "sticker", "embed", "link", "poll", "snapshot"}
)
EMOJI_CUSTOM_RE: Final = re.compile(r"^[A-Za-z0-9_]{2,32}:[0-9]{15,21}$")
THREAD_TYPES: Final = frozenset({10, 11, 12})
VOICE_TYPES: Final = frozenset({2, 13})
UNEXPECTED_RESPONSE: Final = "Discord returned an unexpected response"

HINT_CHANNEL: Final = "Call list_channels for guild {guild_id} to find valid channel ids."
HINT_MESSAGE: Final = (
    "The message may have been deleted or the id is wrong; list_messages returns valid ids."
)
HINT_MEMBER: Final = "Use search_members or list_members to find valid user ids."
HINT_GUILD: Final = "Check the guild id; list_guilds shows the allowlisted guilds the bot is in."
HINT_GUILD_MEMBERS: Final = (
    "Discord documents no privileged intent for search_members; if it returns a permission error, "
    "enable the GUILD_MEMBERS intent in the Developer Portal."
)

ENDPOINTS: Final = {
    "guilds": "/users/@me/guilds",
    "guild": "/guilds/{guild_id}",
    "guild_channels": "/guilds/{guild_id}/channels",
    "channel": "/channels/{channel_id}",
    "bot_member": "/guilds/{guild_id}/members/{user_id}",
    "messages": "/channels/{channel_id}/messages",
    "message": "/channels/{channel_id}/messages/{message_id}",
    "search": "/guilds/{guild_id}/messages/search",
    "reactions": "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}",
    "pins": "/channels/{channel_id}/messages/pins",
    "threads": "/guilds/{guild_id}/threads/active",
    "members": "/guilds/{guild_id}/members",
    "member": "/guilds/{guild_id}/members/{user_id}",
    "member_search": "/guilds/{guild_id}/members/search",
    "audit": "/guilds/{guild_id}/audit-logs",
    "me": "/users/@me",
}


def _endpoint(key: str) -> str:
    return f"GET {ENDPOINTS[key]}"


def validate_emoji(value: str) -> str:
    """A custom ``name:id`` or a Unicode emoji (section 7.8)."""
    if EMOJI_CUSTOM_RE.fullmatch(value):
        return value
    if (
        not 1 <= len(value) <= 32
        or any(ord(ch) < 128 for ch in value)
        or any(ch.isspace() for ch in value)
    ):
        raise DiscordMcpError(
            "validation",
            "emoji must be a custom name:id or a Unicode emoji of 1-32 code points "
            "containing no ASCII characters or whitespace",
        )
    return value


def _with_meta[T: BaseModel](model: type[T], base: BaseModel, meta: Meta) -> T:
    # Only the fields that were set by the projection (A1-5: inapplicable keys are omitted).
    return model(meta=meta, **base.model_dump(exclude_unset=True))


def _meta_of(fetched: Fetched, key: str) -> Meta:
    return Meta(
        as_of=to_iso(fetched.fetched_at),
        cached=fetched.cached,
        cache_age_seconds=fetched.age_seconds,
        endpoint=_endpoint(key),
    )


def _as_list(value: Any) -> list[Any]:
    """A list body (review m5): anything else is an unexpected response, not an internal error."""
    if not isinstance(value, list):
        raise DiscordMcpError("upstream", UNEXPECTED_RESPONSE)
    return value


def _as_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DiscordMcpError("upstream", UNEXPECTED_RESPONSE)
    return value


def _snowflake_order(value: Any) -> int:
    text = str(value)
    return int(text) if text.isdigit() else 0


def _sort_channels(raws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Categories before their children; uncategorised first; children grouped by parent (m9)."""
    by_id = {str(c["id"]): c for c in raws}

    def position(c: Mapping[str, Any]) -> int:
        value = c.get("position")
        return value if isinstance(value, int) else 0

    def key(c: dict[str, Any]) -> tuple[int, int, int, int, int]:
        cid = _snowflake_order(c["id"])
        if c.get("type") == 4:
            return (position(c), _snowflake_order(c["id"]), 0, position(c), cid)
        parent = by_id.get(str(c.get("parent_id"))) if c.get("parent_id") else None
        if parent is None:
            return (-1, 0, 1, position(c), cid)
        return (position(parent), _snowflake_order(parent["id"]), 1, position(c), cid)

    return sorted(raws, key=key)


class DiscordService:
    """Transport-independent tool logic."""

    def __init__(
        self,
        settings: Settings,
        client: DiscordClient,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        cache: TTLCache | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._now = now
        self._sleep = sleep
        self._ttl = float(settings.ttl_metadata_seconds)
        self._cache = cache or TTLCache(
            max_entries=settings.cache_max_entries,
            max_bytes=settings.cache_max_mb * 1024 * 1024,
            wall_clock=now,
        )
        self.guard = Guard(settings.guild_ids, self._channel)
        self.bot_user_id: str | None = None
        self.probe: BootProbe = BootProbe("unreachable")

    # ------------------------------------------------------------ lifecycle

    @property
    def probe_outcome(self) -> str:
        """``ok``, ``auth`` or ``unreachable`` once booted (R5)."""
        return self.probe.outcome

    async def boot(self) -> str:
        """Run the boot probe (R4-R6). Returns the outcome: ``ok``, ``auth`` or ``unreachable``."""
        probe = await self._client.probe()
        self.bot_user_id = probe.bot_user_id
        if (
            probe.application_id
            and self._settings.discord_application_id
            and probe.application_id != self._settings.discord_application_id
        ):
            logger.warning(
                "DISCORD_APPLICATION_ID differs from the application the bot token belongs to",
                extra={"outcome": "application_id_mismatch"},
            )
        self.probe = probe
        return probe.outcome

    @property
    def auth_failed(self) -> bool:
        return self._client.auth_failed

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------ cached and direct fetches

    async def _cached(
        self,
        key: str,
        route: Route,
        params: Sequence[tuple[str, str]] = (),
    ) -> Fetched:
        async def fetch() -> Any:
            return (await self._client.get(route, params)).body

        return await self._cache.get(key, fetch, self._ttl)

    async def _direct(
        self,
        route: Route,
        params: Sequence[tuple[str, str]] = (),
        *,
        not_found_hint: str | None = None,
    ) -> Any:
        try:
            return (await self._client.get(route, params)).body
        except DiscordMcpError as exc:
            if exc.kind == "not_found" and not_found_hint is not None:
                raise DiscordMcpError(
                    "not_found",
                    exc.message,
                    code=exc.code,
                    http_status=exc.http_status,
                    hint=not_found_hint,
                ) from None
            raise

    async def _channel(self, channel_id: str) -> Fetched:
        route = Route.build("/channels/{channel_id}", channel_id, channel_id=channel_id)
        return await self._cached(f"channel:{channel_id}", route)

    async def _guild(self, guild_id: str) -> Fetched:
        route = Route.build("/guilds/{guild_id}", guild_id, guild_id=guild_id)
        return await self._cached(f"guild:{guild_id}", route, [("with_counts", "true")])

    async def _guild_channels(self, guild_id: str) -> Fetched:
        route = Route.build("/guilds/{guild_id}/channels", guild_id, guild_id=guild_id)
        return await self._cached(f"guild_channels:{guild_id}", route)

    async def _bot_id(self) -> str:
        if self.bot_user_id is None:
            body = _as_dict(await self._direct(Route.build("/users/@me")))
            if not isinstance(body.get("id"), str):
                raise DiscordMcpError("upstream", UNEXPECTED_RESPONSE)
            self.bot_user_id = body["id"]
        return self.bot_user_id

    async def _bot_member(self, guild_id: str) -> Fetched:
        bot_id = await self._bot_id()
        route = Route.build(
            "/guilds/{guild_id}/members/{user_id}", guild_id, guild_id=guild_id, user_id=bot_id
        )
        return await self._cached(f"bot_member:{guild_id}", route)

    def _meta_now(self, key: str) -> Meta:
        return Meta(
            as_of=to_iso(self._now()), cached=False, cache_age_seconds=0, endpoint=_endpoint(key)
        )

    # ------------------------------------------------------------ permissions (R28-R29)

    async def _guild_context(self, guild_id: str) -> tuple[dict[str, Any], dict[str, Any], str]:
        guild = await self._guild(guild_id)
        member = await self._bot_member(guild_id)
        bot_id = await self._bot_id()
        guild_raw = guild.value if isinstance(guild.value, dict) else {}
        member_raw = member.value if isinstance(member.value, dict) else {}
        return guild_raw, member_raw, bot_id

    async def _channel_permissions(self, channel: Mapping[str, Any]) -> int:
        guild_id = str(channel.get("guild_id", ""))
        guild_raw, member_raw, bot_id = await self._guild_context(guild_id)
        overwrite_source: Mapping[str, Any] = channel
        if channel.get("type") in THREAD_TYPES and channel.get("parent_id"):
            parent = await self._channel(str(channel["parent_id"]))
            if isinstance(parent.value, dict):
                overwrite_source = parent.value
        return effective_permissions(
            guild=guild_raw,
            member_role_ids=[str(r) for r in member_raw.get("roles") or []],
            bot_user_id=bot_id,
            channel=overwrite_source,
        )

    @staticmethod
    def _check(perms: int, required: Sequence[int], where: str, hint_target: str) -> None:
        missing = missing_permissions(perms, required)
        if missing:
            names = ", ".join(missing)
            raise DiscordMcpError(
                "permission",
                f"Missing {names} in {where}",
                hint=f"Grant {names} to the bot's role in {hint_target}",
            )

    async def _require_channel(self, channel: Mapping[str, Any], required: Sequence[int]) -> int:
        perms = await self._channel_permissions(channel)
        self._check(perms, required, f"channel {channel.get('id')}", "this channel")
        return perms

    @staticmethod
    def _history_permissions(channel: Mapping[str, Any]) -> list[int]:
        required = [VIEW_CHANNEL, READ_MESSAGE_HISTORY]
        if channel.get("type") in VOICE_TYPES:
            required.append(CONNECT)
        return required

    async def _require_guild_permission(self, guild_id: str, bit: int) -> None:
        guild_raw, member_raw, bot_id = await self._guild_context(guild_id)
        perms = effective_permissions(
            guild=guild_raw,
            member_role_ids=[str(r) for r in member_raw.get("roles") or []],
            bot_user_id=bot_id,
            channel=None,
        )
        self._check(perms, [bit], f"guild {guild_id}", "this server")

    # ------------------------------------------------------------ guilds

    async def list_guilds(self) -> ListGuildsResult:
        allowed = self.guard.allowed_guild_ids
        found: list[GuildSummary] = []
        seen: set[str] = set()
        first: Fetched | None = None
        after: str | None = None
        exhausted = False
        for _ in range(GUILD_MAX_PAGES):
            params = [("with_counts", "true"), ("limit", str(GUILD_PAGE_LIMIT))]
            if after is not None:
                params.append(("after", after))
            route = Route.build("/users/@me/guilds")
            fetched = await self._cached(f"user_guilds:{after or ''}", route, params)
            first = first or fetched
            page = _as_list(fetched.value)
            for raw in page:
                if (
                    isinstance(raw, dict)
                    and isinstance(raw.get("id"), str)
                    and raw["id"] in allowed
                    and raw["id"] not in seen
                ):
                    seen.add(raw["id"])
                    found.append(project.guild_summary(raw))
            if len(page) < GUILD_PAGE_LIMIT:
                exhausted = True
                break
            last = page[-1]
            after = str(last["id"]) if isinstance(last, dict) and last.get("id") else None
            if after is None:
                exhausted = True
                break
        if not exhausted:
            logger.warning("guild list truncated at the page cap", extra={"outcome": "truncated"})
        missing = sorted(allowed - seen)
        if first is None:  # the loop always runs at least once
            raise DiscordMcpError("upstream", UNEXPECTED_RESPONSE)
        return ListGuildsResult(
            meta=_meta_of(first, "guilds"),
            guilds=found,
            missing_allowlisted_guild_ids=missing,
        )

    async def get_guild(self, guild_id: str) -> GuildResult:
        self.guard.require_guild(guild_id)
        fetched = await self._guild(guild_id)
        return _with_meta(GuildResult, project.guild(fetched.value), _meta_of(fetched, "guild"))

    async def list_channels(self, guild_id: str) -> ChannelListResult:
        self.guard.require_guild(guild_id)
        channels_f = await self._guild_channels(guild_id)
        guild_raw, member_raw, bot_id = await self._guild_context(guild_id)
        roles = [str(r) for r in member_raw.get("roles") or []]
        raws = [
            c for c in _as_list(channels_f.value) if isinstance(c, dict) and c.get("id") is not None
        ]
        items = []
        for raw in _sort_channels(raws):
            perms = effective_permissions(
                guild=guild_raw, member_role_ids=roles, bot_user_id=bot_id, channel=raw
            )
            items.append(
                project.channel(
                    raw,
                    bot_can_view=can_view(perms),
                    bot_can_read_history=can_read_history(perms),
                )
            )
        return ChannelListResult(meta=_meta_of(channels_f, "guild_channels"), channels=items)

    async def get_channel(self, channel_id: str) -> ChannelResult:
        validate_snowflake(channel_id, "channel_id")
        fetched = await self.guard.resolve_channel(channel_id)
        perms = await self._channel_permissions(fetched.value)
        self._check(perms, [VIEW_CHANNEL], f"channel {channel_id}", "this channel")
        channel = project.channel(
            fetched.value,
            bot_can_view=can_view(perms),
            bot_can_read_history=can_read_history(perms),
        )
        return _with_meta(ChannelResult, channel, _meta_of(fetched, "channel"))

    # ------------------------------------------------------------ messages

    async def list_messages(
        self,
        channel_id: str,
        limit: int = 50,
        before: str | None = None,
        after: str | None = None,
        around: str | None = None,
    ) -> MessageListResult:
        validate_snowflake(channel_id, "channel_id")
        if sum(value is not None for value in (before, after, around)) > 1:
            raise DiscordMcpError(
                "validation", "At most one of before, after or around may be given"
            )
        now = self._now()
        params = [("limit", str(limit))]
        for name, value in (("before", before), ("after", after), ("around", around)):
            if value is not None:
                params.append((name, time_argument_to_snowflake(value, name, now)))
        fetched = await self.guard.resolve_channel(channel_id)
        channel = fetched.value
        await self._require_channel(channel, self._history_permissions(channel))
        route = Route.build("/channels/{channel_id}/messages", channel_id, channel_id=channel_id)
        body = await self._direct(
            route, params, not_found_hint=HINT_CHANNEL.format(guild_id=channel.get("guild_id"))
        )
        messages = [project.message(m) for m in _as_list(body)]
        has_more = len(messages) == limit
        cursor: dict[str, str] | None = None
        if has_more and messages:
            ids = [_snowflake_order(m.id) for m in messages]
            if around is not None:
                cursor = None
            elif after is not None:
                cursor = {"after": str(max(ids))}
            else:
                cursor = {"before": str(min(ids))}
        return MessageListResult(
            meta=self._meta_now("messages"),
            messages=messages,
            count=len(messages),
            has_more=has_more,
            next_cursor=cursor,
        )

    async def get_message(self, channel_id: str, message_id: str) -> MessageResult:
        validate_snowflake(channel_id, "channel_id")
        validate_snowflake(message_id, "message_id")
        fetched = await self.guard.resolve_channel(channel_id)
        channel = fetched.value
        await self._require_channel(channel, self._history_permissions(channel))
        route = Route.build(
            "/channels/{channel_id}/messages/{message_id}",
            channel_id,
            channel_id=channel_id,
            message_id=message_id,
        )
        body = await self._direct(route, not_found_hint=HINT_MESSAGE)
        return _with_meta(MessageResult, project.message(body), self._meta_now("message"))

    async def _check_search_channel(self, guild_id: str, channel_id: str) -> None:
        fetched = await self.guard.resolve_channel(channel_id)
        if str(fetched.value.get("guild_id")) != guild_id:
            raise DiscordMcpError("forbidden_guild", "Channel is not in an allowlisted guild")
        await self._require_channel(fetched.value, [READ_MESSAGE_HISTORY])

    async def _check_search_channels(self, guild_id: str, channel_ids: list[str]) -> None:
        """Guard and permission lookups for every listed channel, run concurrently (A1-7.7).

        Results are read in ``channel_ids`` order so the first failure reported is deterministic.
        """
        results = await asyncio.gather(
            *(self._check_search_channel(guild_id, cid) for cid in channel_ids),
            return_exceptions=True,
        )
        for outcome in results:
            if isinstance(outcome, BaseException):
                raise outcome

    async def search_messages(
        self,
        guild_id: str,
        content: str | None = None,
        author_ids: list[str] | None = None,
        channel_ids: list[str] | None = None,
        mentions: list[str] | None = None,
        has: Sequence[str] | None = None,
        before: str | None = None,
        after: str | None = None,
        sort_by: str = "timestamp",
        sort_order: str = "desc",
        limit: int = 25,
        offset: int = 0,
    ) -> SearchResult:
        self.guard.require_guild(guild_id)
        if content is not None and len(content) > SEARCH_MAX_CONTENT:
            raise DiscordMcpError("validation", "content must be at most 1024 characters")
        authors = list(author_ids or [])
        mention_ids = list(mentions or [])
        channels = list(channel_ids or [])
        for name, values, cap in (
            ("author_ids", authors, SEARCH_MAX_AUTHORS),
            ("mentions", mention_ids, SEARCH_MAX_MENTIONS),
            ("channel_ids", channels, SEARCH_MAX_CHANNELS),
        ):
            if len(values) > cap:
                raise DiscordMcpError("validation", f"{name} accepts at most {cap} ids")
            for value in values:
                validate_snowflake(value, name)
        has_values = list(has or [])
        for value in has_values:
            # A leading "-" negates a filter (A1-7.7, documented by Discord).
            base = value[1:] if value.startswith("-") else value
            if base not in SEARCH_HAS_VALUES:
                raise DiscordMcpError(
                    "validation",
                    "has values must be from: "
                    + ", ".join(sorted(SEARCH_HAS_VALUES))
                    + " (optionally prefixed with -)",
                )
        now = self._now()
        params: list[tuple[str, str]] = []
        if content is not None:
            params.append(("content", content))
        params.extend(("author_id", a) for a in authors)
        params.extend(("mentions", m) for m in mention_ids)
        params.extend(("has", h) for h in has_values)
        await self._check_search_channels(guild_id, channels)
        params.extend(("channel_id", c) for c in channels)
        if before is not None:
            params.append(("max_id", time_argument_to_snowflake(before, "before", now)))
        if after is not None:
            params.append(("min_id", time_argument_to_snowflake(after, "after", now)))
        params.extend(
            [
                ("sort_by", sort_by),
                ("sort_order", sort_order),
                ("limit", str(limit)),
                ("offset", str(offset)),
            ]
        )
        route = Route.build("/guilds/{guild_id}/messages/search", guild_id, guild_id=guild_id)
        body = await self._search_with_index_retry(route, params)
        payload = _as_dict(body)
        groups = _as_list(payload.get("messages", []))
        hits = []
        for group in groups:
            if isinstance(group, list):
                for item in group:
                    if isinstance(item, dict) and item.get("hit") is True:
                        hits.append(project.message(item, search=True))
                        break
        total = payload.get("total_results")
        total_results = total if isinstance(total, int) else 0
        count = len(groups)
        has_more = offset + count < total_results
        return SearchResult(
            meta=self._meta_now("search"),
            messages=hits,
            total_results=total_results,
            offset=offset,
            has_more=has_more,
            next_offset=offset + count if has_more else None,
        )

    async def _search_with_index_retry(self, route: Route, params: list[tuple[str, str]]) -> Any:
        attempts = 0
        max_wait = self._settings.discord_max_rate_limit_wait_seconds
        while True:
            try:
                return await self._direct(route, params)
            except DiscordMcpError as exc:
                if exc.kind != "index_pending":
                    raise
                wait = exc.retry_after or 0.0
                if attempts >= self._settings.discord_search_index_retries or wait > max_wait:
                    raise
                attempts += 1
                await self._sleep(wait)

    async def list_reactions(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
        type: str = "normal",
        limit: int = 25,
        after: str | None = None,
    ) -> ReactionListResult:
        validate_snowflake(channel_id, "channel_id")
        validate_snowflake(message_id, "message_id")
        validate_emoji(emoji)
        if after is not None:
            validate_snowflake(after, "after")
        fetched = await self.guard.resolve_channel(channel_id)
        channel = fetched.value
        await self._require_channel(channel, self._history_permissions(channel))
        params = [("type", "1" if type == "burst" else "0"), ("limit", str(limit))]
        if after is not None:
            params.append(("after", after))
        route = Route.build(
            "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}",
            channel_id,
            channel_id=channel_id,
            message_id=message_id,
            emoji=emoji,
        )
        body = await self._direct(route, params, not_found_hint=HINT_MESSAGE)
        users = [u for u in (project.user_summary(x) for x in _as_list(body)) if u is not None]
        has_more = len(users) == limit
        cursor = {"after": users[-1].id} if has_more and users else None
        return ReactionListResult(
            meta=self._meta_now("reactions"), users=users, has_more=has_more, next_cursor=cursor
        )

    async def list_pins(
        self,
        channel_id: str,
        limit: int = 50,
        before: str | None = None,
    ) -> PinListResult:
        validate_snowflake(channel_id, "channel_id")
        params = [("limit", str(limit))]
        if before is not None:
            moment = time_argument_to_datetime(before, "before", self._now())
            params.append(("before", to_iso(moment)))
        fetched = await self.guard.resolve_channel(channel_id)
        channel = fetched.value
        await self._require_channel(channel, self._history_permissions(channel))
        route = Route.build(
            "/channels/{channel_id}/messages/pins", channel_id, channel_id=channel_id
        )
        body = _as_dict(
            await self._direct(
                route,
                params,
                not_found_hint=HINT_CHANNEL.format(guild_id=channel.get("guild_id")),
            )
        )
        items = _as_list(body.get("items", []))
        pins = [project.pin(i) for i in items]
        has_more = bool(body.get("has_more"))
        cursor = None
        if has_more and pins and pins[-1].pinned_at is not None:
            cursor = {"before": str(pins[-1].pinned_at)}
        return PinListResult(
            meta=self._meta_now("pins"), pins=pins, has_more=has_more, next_cursor=cursor
        )

    # ------------------------------------------------------------ threads

    async def list_threads(
        self, guild_id: str, parent_channel_id: str | None = None
    ) -> ThreadListResult:
        self.guard.require_guild(guild_id)
        if parent_channel_id is not None:
            validate_snowflake(parent_channel_id, "parent_channel_id")
            parent = await self.guard.resolve_channel(parent_channel_id)
            if str(parent.value.get("guild_id")) != guild_id:
                raise DiscordMcpError("forbidden_guild", "Channel is not in an allowlisted guild")
        route = Route.build("/guilds/{guild_id}/threads/active", guild_id, guild_id=guild_id)
        body = _as_dict(await self._direct(route, not_found_hint=HINT_GUILD))
        threads_raw = _as_list(body.get("threads", []))
        members_raw = _as_list(body.get("members", []))
        joined = {
            str(m["id"]) for m in members_raw if isinstance(m, dict) and m.get("id") is not None
        }
        threads = [t for t in threads_raw if isinstance(t, dict) and t.get("id") is not None]
        if parent_channel_id is not None:
            threads = [t for t in threads if str(t.get("parent_id")) == parent_channel_id]
        threads.sort(key=lambda t: _snowflake_order(t["id"]), reverse=True)
        return ThreadListResult(
            meta=self._meta_now("threads"),
            threads=[project.channel(t, bot_joined=str(t["id"]) in joined) for t in threads],
        )

    # ------------------------------------------------------------ members

    async def list_members(
        self, guild_id: str, limit: int = 100, after: str | None = None
    ) -> MemberListResult:
        self.guard.require_guild(guild_id)
        if after is not None:
            validate_snowflake(after, "after")
        params = [("limit", str(limit))]
        if after is not None:
            params.append(("after", after))
        route = Route.build("/guilds/{guild_id}/members", guild_id, guild_id=guild_id)
        body = await self._direct(route, params, not_found_hint=HINT_GUILD)
        members = [project.member(m) for m in _as_list(body)]
        has_more = len(members) == limit
        cursor = None
        last_user = members[-1].user if members else None
        if has_more and last_user is not None:
            cursor = {"after": last_user.id}
        return MemberListResult(
            meta=self._meta_now("members"), members=members, has_more=has_more, next_cursor=cursor
        )

    async def get_member(self, guild_id: str, user_id: str) -> MemberResult:
        self.guard.require_guild(guild_id)
        validate_snowflake(user_id, "user_id")
        route = Route.build(
            "/guilds/{guild_id}/members/{user_id}", guild_id, guild_id=guild_id, user_id=user_id
        )
        body = await self._direct(route, not_found_hint=HINT_MEMBER)
        return _with_meta(MemberResult, project.member(body), self._meta_now("member"))

    async def search_members(
        self, guild_id: str, query: str, limit: int = 25
    ) -> MemberSearchResult:
        self.guard.require_guild(guild_id)
        if not 1 <= len(query) <= 100:
            raise DiscordMcpError("validation", "query must be 1-100 characters")
        route = Route.build("/guilds/{guild_id}/members/search", guild_id, guild_id=guild_id)
        try:
            body = await self._direct(
                route, [("query", query), ("limit", str(limit))], not_found_hint=HINT_GUILD
            )
        except DiscordMcpError as exc:
            if exc.kind == "permission":
                raise DiscordMcpError(
                    "permission",
                    exc.message,
                    code=exc.code,
                    http_status=exc.http_status,
                    hint=HINT_GUILD_MEMBERS,
                ) from None
            raise
        members = [project.member(m) for m in _as_list(body)]
        return MemberSearchResult(meta=self._meta_now("member_search"), members=members)

    # ------------------------------------------------------------ audit log

    async def get_audit_log(
        self,
        guild_id: str,
        user_id: str | None = None,
        action_type: int | None = None,
        before: str | None = None,
        after: str | None = None,
        limit: int = 50,
    ) -> AuditLogResult:
        self.guard.require_guild(guild_id)
        if before is not None and after is not None:
            raise DiscordMcpError("validation", "Give at most one of before or after")
        if user_id is not None:
            validate_snowflake(user_id, "user_id")
        if action_type is not None and action_type < 1:
            raise DiscordMcpError("validation", "action_type must be 1 or more")
        now = self._now()
        params: list[tuple[str, str]] = [("limit", str(limit))]
        if user_id is not None:
            params.append(("user_id", user_id))
        if action_type is not None:
            params.append(("action_type", str(action_type)))
        if before is not None:
            params.append(("before", time_argument_to_snowflake(before, "before", now)))
        if after is not None:
            # The literal "0" starts oldest-first paging from the beginning (A1-7.14).
            params.append(
                ("after", "0")
                if after == "0"
                else ("after", time_argument_to_snowflake(after, "after", now))
            )
        await self._require_guild_permission(guild_id, VIEW_AUDIT_LOG)
        route = Route.build("/guilds/{guild_id}/audit-logs", guild_id, guild_id=guild_id)
        body = _as_dict(await self._direct(route, params, not_found_hint=HINT_GUILD))
        users = [u for u in _as_list(body.get("users", [])) if isinstance(u, dict)]
        entries = [
            project.audit_entry(e, users) for e in _as_list(body.get("audit_log_entries", []))
        ]
        has_more = len(entries) == limit
        cursor = None
        if has_more and entries:
            key = "after" if after is not None else "before"
            cursor = {key: entries[-1].id}
        return AuditLogResult(
            meta=self._meta_now("audit"),
            entries=entries,
            order="oldest_first" if after is not None else "newest_first",
            has_more=has_more,
            next_cursor=cursor,
        )
