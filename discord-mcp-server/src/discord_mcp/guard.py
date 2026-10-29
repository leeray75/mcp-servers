"""Guild allowlist guard (R12-R13, A1-R13, D7). Every guild or channel tool uses it.

Guild ids are checked against the allowlist with no Discord request. Channel ids are resolved
first (one cached channel fetch) so that their guild can be checked. Every failure of that channel
lookup, and every channel whose guild is not allowlisted, DM and group DM included, gets the same
message, so the result does not reveal whether the channel exists (A1-R13). Auth, rate-limit and
unavailable errors still surface as themselves.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from discord_mcp.cache import Fetched
from discord_mcp.errors import DiscordMcpError

ChannelFetcher = Callable[[str], Awaitable[Fetched]]

GUILD_REFUSAL: str = "Guild not in this server's allowlist"
CHANNEL_REFUSAL: str = "Channel is not in an allowlisted guild"
PASS_THROUGH_KINDS: frozenset[str] = frozenset({"auth", "rate_limited", "unavailable"})


class Guard:
    """Allowlist checks shared by all tools."""

    def __init__(self, allowed_guild_ids: frozenset[str], fetch_channel: ChannelFetcher) -> None:
        self._allowed = allowed_guild_ids
        self._fetch_channel = fetch_channel

    @property
    def allowed_guild_ids(self) -> frozenset[str]:
        return self._allowed

    def require_guild(self, guild_id: str) -> None:
        """Refuse any guild id outside the allowlist, with no upstream request (R13)."""
        if guild_id not in self._allowed:
            raise DiscordMcpError("forbidden_guild", GUILD_REFUSAL)

    async def resolve_channel(self, channel_id: str) -> Fetched:
        """Fetch (cached) the channel and check its guild is allowlisted (R12, A1-R13)."""
        try:
            fetched = await self._fetch_channel(channel_id)
        except DiscordMcpError as exc:
            if exc.kind in PASS_THROUGH_KINDS:
                raise
            raise DiscordMcpError("forbidden_guild", CHANNEL_REFUSAL) from None
        value = fetched.value if isinstance(fetched.value, dict) else {}
        guild_id = value.get("guild_id")
        if not isinstance(guild_id, str) or guild_id not in self._allowed:
            raise DiscordMcpError("forbidden_guild", CHANNEL_REFUSAL)
        return fetched
