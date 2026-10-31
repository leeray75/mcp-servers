"""Single-use confirmation tokens — what Gmail achieves by refusing to send (R31-R34).

Every write has two turns. The first performs guard and identity checks and returns
``kind=confirmation_required`` with the resolved plan and a token, dispatching **no** write (R31).
The second presents that token, and only then is the request sent — after the guard runs again,
because a token never bypasses it (R34).

A token is minted by :meth:`ConfirmationStore.mint` and bound to the tuple
``(tool, canonical-args-hash, broadcaster_id)`` with a TTL of
``TWITCH_CONFIRM_TOKEN_TTL_SECONDS`` (R32). It lives only in this process: never logged, never
written to disk, and never included in a tool result other than the one that minted it (R33).

:meth:`ConfirmationStore.consume` is the single point of truth. A token that was already used, has
expired, or is presented against different arguments is refused with
``kind=stale_confirmation`` and no request (R32). Tokens are cleared on disconnect, on token
refresh, when the ``401`` circuit opens, and on process exit (R33) — :meth:`clear` is wired to those
moments by ``service.py``.

Tokens are 32 bytes from :mod:`secrets`, URL-safe, and are registered for log redaction for their
lifetime (R52). Comparison is constant-time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.logging_setup import register_secret, unregister_secret

logger = logging.getLogger(__name__)

#: How long a confirmation token stays good, from ``TWITCH_CONFIRM_TOKEN_TTL_SECONDS`` (R32).
DEFAULT_TTL_SECONDS: Final = 300

#: The refusal text for any token problem. Identical in every case, so a caller cannot learn which
#: of replay, expiry or argument drift occurred (R32).
STALE_MESSAGE: Final = (
    "The confirm token was already used, has expired, or the arguments changed since it was minted"
)
STALE_HINT: Final = (
    "Call the tool again without confirm to receive a fresh token, then repeat the call exactly."
)

Clock = Callable[[], float]
TokenFactory = Callable[[], str]


def new_token() -> str:
    """A 32-byte URL-safe single-use token from :mod:`secrets` (R32)."""
    return secrets.token_urlsafe(32)


def canonical_json(value: Any) -> str:
    """A deterministic JSON rendering of arguments, so a hash is comparable across turns (R32).

    Keys are sorted, separators collapsed, and non-string keys stringified. ``confirm`` itself is
    never part of the hashed arguments (the caller excludes it), so a token never binds to itself.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)


def args_hash(arguments: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical argument rendering (R32).

    The digest is what binds a token to a plan. Argument *values* are never stored on the token or
    logged, only this digest (R52).
    """
    return hashlib.sha256(canonical_json(dict(arguments)).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ConfirmationPlan:
    """What a token is bound to: tool, canonical-args-hash, broadcaster (R32)."""

    tool: str
    broadcaster_id: str
    arguments_hash: str


@dataclass(frozen=True, slots=True)
class MintedConfirmation:
    """A freshly minted token and the plan it binds — returned only by the minting call (R33)."""

    token: str
    expires_at: float
    plan: ConfirmationPlan


class ConfirmationStore:
    """In-memory, single-use, TTL-bound confirmation tokens (R32, R33)."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Clock = time.monotonic,
        wall_clock: Clock = time.time,
        make_token: TokenFactory = new_token,
    ) -> None:
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._wall = wall_clock
        self._make_token = make_token
        self._entries: dict[str, _Entry] = {}

    # ------------------------------------------------------------ mint (R31)

    def mint(
        self, tool: str, broadcaster_id: str, arguments: Mapping[str, Any]
    ) -> MintedConfirmation:
        """Bind a fresh single-use token to ``(tool, args-hash, broadcaster_id)`` (R31, R32).

        The argument values are never retained — only their digest — so the store cannot become a
        place where chat text or a timeout reason accumulates (R52).
        """
        plan = ConfirmationPlan(
            tool=tool, broadcaster_id=broadcaster_id, arguments_hash=args_hash(arguments)
        )
        token = self._make_token()
        expires_at = self._clock() + self._ttl
        self._entries[token] = _Entry(plan=plan, expires_at=expires_at)
        register_secret(token)
        return MintedConfirmation(token=token, expires_at=expires_at, plan=plan)

    def expires_at_wall(self, expires_at: float) -> float:
        """Project a mint-time expiry onto wall time, for the plan's ``expires_at`` field (R31)."""
        return self._wall() + max(0.0, expires_at - self._clock())

    # ------------------------------------------------------------ consume (R32)

    def consume(
        self, tool: str, broadcaster_id: str, arguments: Mapping[str, Any], token: str
    ) -> None:
        """Validate and **consume** one token, or raise ``kind=stale_confirmation`` (R32).

        Single use by construction: the entry is removed before anything is compared, so a second
        presentation of the same token can never pass. Expiry and argument/broadcaster/tool drift
        all give the identical refusal, and none of them leaves a request behind — the caller only
        dispatches when this returns normally.
        """
        entry = self._entries.pop(token, None)
        if entry is None:
            raise self._stale("used")
        unregister_secret(token)
        if self._clock() >= entry.expires_at:
            raise self._stale("expired")
        if (
            entry.plan.tool != tool
            or entry.plan.broadcaster_id != broadcaster_id
            or not secrets.compare_digest(
                entry.plan.arguments_hash.encode("ascii"),
                args_hash(arguments).encode("ascii"),
            )
        ):
            raise self._stale("drift")

    def validate_only(
        self, tool: str, broadcaster_id: str, arguments: Mapping[str, Any], token: str
    ) -> bool:
        """Non-consuming check, used by tests and diagnostics. Prefer :meth:`consume`."""
        entry = self._entries.get(token)
        if entry is None or self._clock() >= entry.expires_at:
            return False
        return (
            entry.plan.tool == tool
            and entry.plan.broadcaster_id == broadcaster_id
            and entry.plan.arguments_hash == args_hash(arguments)
        )

    @staticmethod
    def _stale(why: str) -> TwitchMcpError:
        """One refusal text for every token problem; ``why`` stays in the log (R32)."""
        logger.warning("twitch confirm token refused", extra={"outcome": "stale_confirmation"})
        return TwitchMcpError("stale_confirmation", STALE_MESSAGE, hint=STALE_HINT)

    # ------------------------------------------------------------ clearing (R33)

    def clear(self) -> int:
        """Drop every outstanding token (R33).

        Called on disconnect, on token refresh, when the ``401`` circuit opens, and on process exit.
        Returns how many were dropped (for the boot/shutdown log).
        """
        count = 0
        for token in list(self._entries):
            unregister_secret(token)
            self._entries.pop(token, None)
            count += 1
        if count:
            logger.info("twitch confirm tokens cleared", extra={"outcome": "confirmations_cleared"})
        return count

    def prune(self) -> int:
        """Drop expired tokens without a shutdown. Returns how many were dropped."""
        now = self._clock()
        expired = [token for token, entry in self._entries.items() if now >= entry.expires_at]
        for token in expired:
            unregister_secret(token)
            self._entries.pop(token, None)
        return len(expired)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, token: object) -> bool:
        return isinstance(token, str) and token in self._entries


@dataclass(slots=True)
class _Entry:
    """One outstanding token: its bound plan and when it dies. No argument values (R52)."""

    plan: ConfirmationPlan
    expires_at: float


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "STALE_HINT",
    "STALE_MESSAGE",
    "ConfirmationPlan",
    "ConfirmationStore",
    "MintedConfirmation",
    "args_hash",
    "canonical_json",
    "new_token",
]
