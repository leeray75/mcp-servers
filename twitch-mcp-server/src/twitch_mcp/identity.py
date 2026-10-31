"""Verified write authorization (R29, R30) — the check no sibling of this server has.

``TWITCH_ALLOWED_BROADCASTER_IDS`` is a *necessary* condition for a write, never a sufficient one
(spec §7.3). Two facts decide a write target:

1. **Own channel.** The operator's own ``broadcaster_id`` is the ``user_id`` of the token as
   reported by ``GET /oauth2/validate`` — read from :attr:`OperatorIdentity.broadcaster_id` and
   **never** from a tool argument (R29). An argument claiming to be the operator is only ever
   *compared* to that value, never adopted.
2. **Verified role.** Any other allowlisted channel needs proof from Twitch itself: the operator
   appear in ``GET /moderation/moderators`` (the ``moderation``, ``chat``, ``polls`` and
   ``predictions`` groups) or in ``GET /channels/editors`` (the ``channel`` group). The proof is
   fetched through the TTL cache with ``TWITCH_IDENTITY_TTL_SECONDS`` (R30).

Verification results are invalidated on token refresh, on disconnect, and on the first ``401``
(R30): :meth:`IdentityVerifier.invalidate` is wired to the user-auth object and the client's
circuit, so a role that was revoked on Twitch cannot keep authorizing writes from this
process for longer than one TTL, and never at all after a state change.

A target that is neither of the above returns ``kind=not_authorized`` and no write request (R29).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from twitch_mcp.cache import TTLCache
from twitch_mcp.errors import PASS_THROUGH_KINDS, TwitchMcpError

logger = logging.getLogger(__name__)

#: The write groups of SPEC §6.4, as :func:`require_writable` knows them.
WriteGroup = str

GROUP_CHANNEL: Final = "channel"
GROUP_MODERATION: Final = "moderation"
GROUP_CHAT: Final = "chat"
GROUP_POLLS: Final = "polls"
GROUP_PREDICTIONS: Final = "predictions"
GROUP_ROLES: Final = "roles"

WRITE_GROUPS: Final[frozenset[str]] = frozenset(
    {GROUP_CHANNEL, GROUP_MODERATION, GROUP_CHAT, GROUP_POLLS, GROUP_PREDICTIONS, GROUP_ROLES}
)

#: Which Twitch roster proves a role in each group (R29). ``moderators`` is
#: ``GET /moderation/moderators``; ``editors`` is ``GET /channels/editors``.
GROUP_ROSTER: Final[Mapping[str, str]] = MappingProxyType(
    {
        GROUP_CHANNEL: "editors",
        GROUP_MODERATION: "moderators",
        GROUP_CHAT: "moderators",
        GROUP_POLLS: "moderators",
        GROUP_PREDICTIONS: "moderators",
        GROUP_ROLES: "moderators",
    }
)

#: What the operator holds on one channel, as established by a lookup.
VERDICT_NONE: Final = "none"
VERDICT_OWN: Final = "own"
VERDICT_MODERATOR: Final = "moderator"
VERDICT_EDITOR: Final = "editor"

#: Cache key prefix, so identity can be dropped on its own (R30) without touching metadata.
IDENTITY_CACHE_PREFIX: Final = "identity:"

NOT_AUTHORIZED_MESSAGE: Final = (
    "This server may only write on the operator's own channel, or on a channel where Twitch's own "
    "moderator/editor roster shows the operator"
)


class OperatorIdentity:
    """The operator's own identity as the token says it is (R29).

    Holds only the ``user_id`` learned from ``GET /oauth2/validate``. There is no setter taking a
    value from a tool argument, because there is no path from an argument to this value.
    """

    def __init__(self, broadcaster_id: str | None = None) -> None:
        self._broadcaster_id = broadcaster_id

    @property
    def broadcaster_id(self) -> str | None:
        """The operator's own channel id, from the token (R29). ``None`` when not signed in."""
        return self._broadcaster_id

    @broadcaster_id.setter
    def broadcaster_id(self, value: str | None) -> None:
        """Set from ``/oauth2/validate`` only — called by boot, connect and refresh (R29)."""
        self._broadcaster_id = value or None

    def is_own_channel(self, broadcaster_id: str) -> bool:
        """True when ``broadcaster_id`` *equals* the token's own id. Never adopts the argument."""
        own = self._broadcaster_id
        return own is not None and broadcaster_id == own

    def require_present(self) -> str:
        """The operator id, or ``kind=unauthenticated`` naming the sign-in page (R49)."""
        own = self._broadcaster_id
        if own is None:
            raise TwitchMcpError(
                "unauthenticated",
                "No Twitch account is connected, so this server cannot establish whose channel "
                "a write would be on",
                hint="Use the connect_twitch_account tool to sign in, then retry.",
            )
        return own


@dataclass(frozen=True, slots=True)
class Verification:
    """One verified role on one channel, and how it was established (diagnostics only)."""

    broadcaster_id: str
    verdict: str
    cached: bool


#: ``fetch_roster(roster, broadcaster_id)`` -> the raw Helix object list of that roster.
RosterFetcher = Callable[[str, str], Awaitable[Sequence[str]]]


class IdentityVerifier:
    """Verifies "the operator may write here" against Twitch's own rosters (R29, R30)."""

    def __init__(
        self,
        operator: OperatorIdentity,
        cache: TTLCache,
        fetch_roster: RosterFetcher,
        *,
        ttl_seconds: float = 300.0,
    ) -> None:
        self._operator = operator
        self._cache = cache
        self._fetch_roster = fetch_roster
        self._ttl = ttl_seconds

    @property
    def operator(self) -> OperatorIdentity:
        return self._operator

    def is_own_channel(self, broadcaster_id: str) -> bool:
        """Convenience passthrough of the token comparison (R29)."""
        return self._operator.is_own_channel(broadcaster_id)

    def cache_key(self, broadcaster_id: str, roster: str) -> str:
        """The identity cache key. Ids only — never a token, login or URL (R52)."""
        return f"{IDENTITY_CACHE_PREFIX}{roster}:{broadcaster_id}"

    async def _roster_contains(self, roster: str, broadcaster_id: str, user_id: str) -> bool:
        """True when the operator appears in one cached roster for one channel (R30)."""

        async def fetch() -> Any:
            members = await self._fetch_roster(roster, broadcaster_id)
            return sorted({str(member) for member in members})

        fetched = await self._cache.get(self.cache_key(broadcaster_id, roster), fetch, self._ttl)
        value = fetched.value
        return isinstance(value, (list, tuple, set)) and user_id in value

    async def require_writable(self, broadcaster_id: str, group: str) -> Verification:
        """Prove the operator may write to ``broadcaster_id`` in ``group`` — or refuse (R29).

        The own-channel path costs zero requests. Every other target costs exactly one cached roster
        lookup. ``kind=not_authorized`` is raised with no write request when the roster does not
        contain the operator. An auth or rate-limit failure surfaces unchanged; any other lookup
        failure is treated as "not verified" rather than "verified", so a flaky lookup can never
        become an authorization.
        """
        if group not in WRITE_GROUPS:
            raise TwitchMcpError(
                "disabled",
                "That write group is not one this server knows",
                hint="Enable a documented group in TWITCH_WRITE_GROUPS.",
            )
        own = self._operator.require_present()
        if self._operator.is_own_channel(broadcaster_id):
            return Verification(broadcaster_id, VERDICT_OWN, cached=False)
        roster = GROUP_ROSTER[group]
        try:
            member = await self._roster_contains(roster, broadcaster_id, own)
        except TwitchMcpError as exc:
            if exc.kind in PASS_THROUGH_KINDS and (
                exc.kind != "upstream" or exc.http_status is None or exc.http_status >= 500
            ):
                raise
            self._log_refusal(broadcaster_id, group)
            raise TwitchMcpError("not_authorized", NOT_AUTHORIZED_MESSAGE) from None
        except Exception:
            self._log_refusal(broadcaster_id, group)
            raise TwitchMcpError("not_authorized", NOT_AUTHORIZED_MESSAGE) from None
        if not member:
            self._log_refusal(broadcaster_id, group)
            raise TwitchMcpError("not_authorized", NOT_AUTHORIZED_MESSAGE)
        verdict = VERDICT_MODERATOR if roster == "moderators" else VERDICT_EDITOR
        return Verification(broadcaster_id, verdict, True)

    # ------------------------------------------------------------ invalidation (R30)

    def invalidate(self) -> None:
        """Drop every verified role (R30).

        Called on token refresh, on disconnect and on the first ``401`` — the three moments at which
        a role Twitch granted a moment ago may no longer be true.
        """
        self._cache.invalidate(IDENTITY_CACHE_PREFIX)
        logger.info(
            "twitch identity cache invalidated",
            extra={"outcome": "identity_invalidated", "status": None, "route": "identity"},
        )
        return None

    def _log_refusal(self, broadcaster_id: str, group: str) -> None:
        """Refusals are auditable by tool/group and channel, and carry no roster content."""
        logger.warning(
            "twitch write refused: operator not verified for this channel",
            extra={
                "outcome": "not_authorized",
                "tool": f"write:{group}",
                "broadcaster_id": broadcaster_id,
            },
        )


def members_of(body: Any, *, user_key: str = "user_id") -> list[str]:
    """Pull the member ids out of a Helix roster body (``{"data": [{"user_id": ...}]}``).

    Accepts the raw envelope or a bare list, so the fetcher may hand over either. Anything that is
    not a digit string is skipped: an unusable entry is simply not a member (fail closed).
    """
    records: Any = body
    if isinstance(body, Mapping):
        records = body.get("data", [])
    if not isinstance(records, list):
        return []
    out: list[str] = []
    for record in records:
        if isinstance(record, Mapping):
            value = record.get(user_key)
            if isinstance(value, str) and value.isdigit():
                out.append(value)
            elif isinstance(value, int) and not isinstance(value, bool):
                out.append(str(value))
    return out


__all__ = [
    "GROUP_CHAT",
    "GROUP_CHANNEL",
    "GROUP_MODERATION",
    "GROUP_POLLS",
    "GROUP_PREDICTIONS",
    "GROUP_ROLES",
    "IDENTITY_CACHE_PREFIX",
    "NOT_AUTHORIZED_MESSAGE",
    "VERDICT_EDITOR",
    "VERDICT_MODERATOR",
    "VERDICT_NONE",
    "VERDICT_OWN",
    "WRITE_GROUPS",
    "IdentityVerifier",
    "OperatorIdentity",
    "RosterFetcher",
    "Verification",
    "members_of",
]
