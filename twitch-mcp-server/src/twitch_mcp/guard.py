"""Broadcaster allowlist guard (R25-R27). Every broadcaster-scoped tool uses it.

Default-deny: an id that is not in ``TWITCH_ALLOWED_BROADCASTER_IDS`` is refused with
``kind=forbidden_channel`` and **zero network activity** — the check is a set membership test
against the boot configuration, so no request can be issued for a refused id (R25).

Uniform denial (R27): a ``channel_id``/``video_id``/``clip_id`` whose owner is unknown costs exactly
one cached lookup (``GET /videos`` or ``GET /channels``) to learn its broadcaster (R26) and nothing
else. Every failure of that lookup — unknown id, 403, 404, 5xx, malformed body, missing owner — and
every object whose owner is not allowlisted gets the *identical* message, so the response cannot be
used to probe which channels are on the allowlist. Auth (the process-wide 401 circuit), rate-limit
and genuinely-unavailable errors still surface as themselves; collapsing those would hide an outage
behind a denial.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from twitch_mcp.cache import Fetched
from twitch_mcp.errors import PASS_THROUGH_KINDS, TwitchMcpError

logger = logging.getLogger(__name__)

#: The one denial text (R27). Any refusal of a broadcaster id uses it verbatim.
BROADCASTER_REFUSAL: Final = "Broadcaster is not in this server's allowlist"
#: The one denial text for an owned object whose owner could not be established or is refused.
OWNER_REFUSAL: Final = "This content is not from an allowlisted broadcaster"

#: Lookup kinds that surface unchanged rather than as a denial (R27).
PASS_THROUGH: Final = PASS_THROUGH_KINDS

#: How an owned object was resolved (diagnostic field, never part of the denial).
OwnedResolver = Callable[[str], Awaitable[Fetched]]


@dataclass(frozen=True, slots=True)
class OwnerResolution:
    """The allowlisted broadcaster id that owns one object, and the cached lookup proving it."""

    broadcaster_id: str
    fetched: Fetched


def _owner_of(value: Any) -> str | None:
    """First broadcaster-owner field of a Helix object, as a digit string, else None."""
    if not isinstance(value, Mapping):
        return None
    for key in ("broadcaster_id", "user_id", "author_name"):
        candidate = value.get(key)
        if key == "author_name":
            continue  # a login, not an id: never usable as an allowlist key
        if isinstance(candidate, str) and candidate.isdigit() and candidate:
            return candidate
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return str(candidate)
    return None


class Guard:
    """Allowlist checks shared by all tools (R25-R27)."""

    def __init__(
        self,
        allowed_broadcaster_ids: frozenset[str],
        fetch_owned: OwnedResolver,
    ) -> None:
        """``fetch_owned`` performs the single cached owner lookup (``GET /videos`` or
        ``GET /channels``) for an object id and returns the raw Helix object (R26)."""
        self._allowed = allowed_broadcaster_ids
        self._fetch_owned = fetch_owned

    @property
    def allowed_broadcaster_ids(self) -> frozenset[str]:
        return self._allowed

    def require_broadcaster(self, broadcaster_id: str, *, field: str = "broadcaster_id") -> None:
        """Refuse any broadcaster id outside the allowlist, with no upstream request (R25).

        Also refuses a malformed id, so an unvalidated string can never reach a URL segment (R17).
        The refusal is the same for both cases and names no value (R27).
        """
        if not _is_user_id(broadcaster_id) or broadcaster_id not in self._allowed:
            self._log_denial(None)
            raise TwitchMcpError(
                "forbidden_channel",
                BROADCASTER_REFUSAL,
                hint=_id_hint(field),
            )

    async def resolve_owner(self, object_id: str) -> OwnerResolution:
        """Learn an object's broadcaster with exactly one cached lookup, then allowlist-check it.

        A missing object (404) or a refused lookup (403) proves nothing about ownership and gets the
        uniform denial (R27). A genuinely unreachable Helix (5xx / unavailable) surfaces as itself:
        collapsing an outage into a denial would hide it (R27 pass-through).
        """
        if not _is_user_id(object_id):
            self._log_denial(None)
            raise TwitchMcpError("forbidden_channel", OWNER_REFUSAL)
        try:
            fetched = await self._fetch_owned(object_id)
        except TwitchMcpError as exc:
            if _is_outage(exc):
                raise
            self._log_denial(None)
            raise TwitchMcpError("forbidden_channel", OWNER_REFUSAL) from None
        except Exception:
            # A malformed body, a missing key or any other lookup crash proves nothing about
            # ownership, so it gets the identical denial (R27). Auth/rate-limit/unavailable surfaced
            # above as themselves, so an outage can never be laundered into a refusal here.
            self._log_denial(None)
            raise TwitchMcpError("forbidden_channel", OWNER_REFUSAL) from None
        owner = _owner_of(fetched.value)
        if owner is None or owner not in self._allowed:
            self._log_denial(None)
            raise TwitchMcpError("forbidden_channel", OWNER_REFUSAL)
        return OwnerResolution(broadcaster_id=owner, fetched=fetched)

    def _log_denial(self, _: None) -> None:
        """Refusals are logged without the refused id, so logs cannot enumerate the allowlist."""
        logger.warning("refused request", extra={"outcome": "forbidden_channel"})


def _is_user_id(value: str) -> bool:
    return isinstance(value, str) and value.isdigit() and 1 <= len(value) <= 20


def _is_outage(exc: TwitchMcpError) -> bool:
    """True when the error reports a real outage rather than a lookup that proved nothing.

    Auth (the process-wide 401 circuit) and rate limiting always surface unchanged (R27): they
    describe the *credential* or the *traffic*, never the object, so laundering them into a
    denial would hide a broken token or an overload behind an allowlist message. A
    transport-level ``upstream`` error is an outage only when it is a 5xx (or carries no status
    at all); a 403/404 ``upstream`` answer is the
    ordinary "this object is not yours" reply and must collapse into the uniform denial so allowlist
    membership cannot be probed.
    """
    if exc.kind not in PASS_THROUGH:
        return False
    if exc.kind in {"rate_limited", "unauthenticated"}:
        return True
    return exc.http_status is None or exc.http_status >= 500


def _id_hint(field: str) -> str:
    return (
        f"Add the owning broadcaster id to TWITCH_ALLOWED_BROADCASTER_IDS in .env and run "
        f"scripts/restart.sh --recreate, or call this tool with an allowlisted {field}."
    )
