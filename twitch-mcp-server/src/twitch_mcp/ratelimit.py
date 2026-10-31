"""Process-wide Twitch rate-limit state (R19-R22, R24).

Nothing here imports a network library or performs I/O; the client (``twitch_client.py``, the only
``httpx`` module — R11) asks for a :class:`Lease` before each request, feeds response headers back
through it, and reports 429s. All state is process-wide and is never keyed to an MCP connection or
session. Clock and sleep are injected so tests run instantly.

How the buckets work (R19): Twitch publishes ``Ratelimit-Limit``, ``Ratelimit-Remaining`` and
``Ratelimit-Reset`` per bucket and rotates the window itself. This module therefore *learns*
capacity and refill cadence from those three headers on every response and never bakes in a limit
value or an endpoint's point cost. Until a bucket's headers have been seen the bucket is unbounded,
which is what lets the very first request through without any local guess.

Buckets are keyed by token type (R20): Twitch maintains an app ("client_credentials") bucket and a
per-user ("user") bucket separately, and so does this module.

A 429 parks its own bucket until ``Ratelimit-Reset`` and marks that lease's route *spent* so the
client never replays the same request inside one call (R21, R22). A wait longer than
``TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS`` fails fast with ``kind=rate_limited`` and ``retry_after``
without the request being made. The first 401 from Helix opens a circuit that stops all further
upstream traffic for the process lifetime (R24); only a restart clears it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Final

from twitch_mcp.errors import TwitchMcpError

logger = logging.getLogger(__name__)


def measure_unix_to_monotonic() -> float:
    """``wall clock minus monotonic clock``, sampled back-to-back on the same clock source.

    Both members of the pair must come from the *same* source: ``time.monotonic()`` is
    relative to an arbitrary origin (on Linux, boot), so subtracting it from a unix timestamp
    without pairing them
    produces a meaningless bridge. Callers that inject a test clock pass that clock's own offset
    instead (see ``tests.twitch_fakes.FakeClock.offset``); a clock that reads monotonic time and a
    clock that reads wall time each need their own value.
    """
    wall = time.time()
    mono = time.monotonic()
    return wall - mono


#: How far ahead of ``Ratelimit-Reset`` a request may be released. Twitch rotates the window at the
#: boundary; releasing a hair early is harmless, releasing late costs a wasted round trip.
RESET_LEEWAY_SECONDS: Final = 0.05

#: Plausible length of one Helix window in seconds, used only to reject an implausible *learned*
#: window (a malformed header). It is not a capacity and no request is ever counted against it.
_MAX_LEARNED_WINDOW_SECONDS: Final = 3600

#: A ``Ratelimit-Reset`` that far ahead of the current window end is not a Helix window — Helix
#: rotates within the minute. It means the caller handed us the wrong timeline (or garbage), and
#: parking on it would wedge the route, so the header is ignored instead.
_MAX_PLAUSIBLE_RESET_AHEAD_SECONDS: Final = 86_400

#: The two header names this module knows (R19). Deliberately a tuple, not a per-endpoint table.
RATELIMIT_HEADER_LIMIT: Final = "ratelimit-limit"
RATELIMIT_HEADER_REMAINING: Final = "ratelimit-remaining"
RATELIMIT_HEADER_RESET: Final = "ratelimit-reset"

TokenType = str
#: Token classes used to key buckets (R20: the application-token and user-token budgets are
#: independent). These are bucket *classes*, never credentials and never a token value; the
#: client passes its token's class (``"app"`` or ``"user"``) into :meth:`RateLimiter.acquire`.
APP_CLASS: Final[TokenType] = "app"
USER_CLASS: Final[TokenType] = "user"

#: The class-correct hint a refused Helix read carries while its class's circuit is open (C3).
#: Writes never receive it (C6): their refusal carries no hint.
_CIRCUIT_HINTS: Final[dict[TokenType, str]] = {
    USER_CLASS: (
        "Sign in again with the connect_twitch_account tool. "
        "A successful sign-in clears this block."
    ),
    APP_CLASS: "Restart the container; the app-token circuit never resets itself.",
}
_CIRCUIT_MESSAGE: Final = "Twitch rejected this server's token"
#: The hint for an ``unauthenticated`` answer that is not a circuit refusal: a validate 401 (C9)
#: and a write refused by an open circuit (C6). It names no restart and claims no circuit.
NEUTRAL_AUTH_HINT: Final = "Check the credentials in .env and re-authorize the account if needed."


def circuit_hint(token_type: TokenType) -> str:
    """The class hint for an open circuit of ``token_type`` (C3). Never carries a credential."""
    return _CIRCUIT_HINTS.get(token_type, _CIRCUIT_HINTS[APP_CLASS])


Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(eq=False)
class BucketState:
    """State of one (token type, route, major) bucket, learned from response headers."""

    limit: int | None = None
    remaining: int | None = None
    window_started: float | None = None  # monotonic time the current window began
    window_seconds: float | None = None  # measured from two observed Ratelimit-Reset instants
    last_reset: float | None = None  # projected monotonic instant of the last Ratelimit-Reset
    blocked_until: float = 0.0  # monotonic; set by 429 handling
    blocked_for: float | None = None  # the 429's documented wait, reported as retry_after (R21)
    spent: bool = False  # set by 429: this lease's route must not be replayed (R22)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def route_key(token_type: TokenType, method: str, template: str, major: str) -> str:
    """Bucket key = token type + method + route template + major parameter value (R20)."""
    return f"{token_type}:{method} {template} {major}"


class Lease:
    """Exclusive use of one bucket for one request. Release it in a ``finally``."""

    def __init__(self, limiter: RateLimiter, state: BucketState, key: str) -> None:
        self._limiter = limiter
        self._key = key
        self.state = state
        # Set by block() and deliberately **not** cleared by learn(): the client calls learn() for
        # every response including the 429 itself, so a flag learn() could overwrite would be
        # useless. The client reads it to decide "this request was throttled, do not replay it"
        # (R22). The bucket-level mirror (BucketState.spent) is cleared when a learned window
        # rotates.
        self.received_429 = False
        self._held = True

    @property
    def key(self) -> str:
        """The bucket key, for logging by route template only (never a URL)."""
        return self._key

    def learn(self, headers: Mapping[str, str]) -> None:
        """Update capacity and refill cadence from a response's ``Ratelimit-*`` headers (R19).

        HTTP header names are case-insensitive and Helix sends them capitalised, so the lookup
        is normalised here rather than requiring the caller to pre-lower them. Unknown or absent
        headers
        leave the previous learned state untouched; nothing here invents a limit.
        """
        limiter = self._limiter
        # Sample the clock once: every comparison in this method is against the same instant.
        now = limiter._clock()
        limit = _parse_int(_header(headers, RATELIMIT_HEADER_LIMIT))
        remaining = _parse_int(_header(headers, RATELIMIT_HEADER_REMAINING))
        reset_unix = _parse_float(_header(headers, RATELIMIT_HEADER_RESET))
        if limit is not None:
            self.state.limit = limit
        reset: float | None = None
        if reset_unix is not None and reset_unix > 0:
            projected = limiter._monotonic_reset(reset_unix)
            if -RESET_LEEWAY_SECONDS <= projected - now <= _MAX_PLAUSIBLE_RESET_AHEAD_SECONDS:
                # A reset already in the past by more than the leeway means the header was built on
                # a
                # different timeline than the injected clock (or the response was queued for a long
                # time); trusting it would park the route on a value we cannot trust, so it is
                # dropped.
                reset = projected
        if remaining is not None and self.state.remaining == 0 and self.state.blocked_until > now:
            # Mid-park, a non-zero Remaining can only come from a response that was issued before
            # the
            # 429 was seen (Helix publishes 0 for the rest of the window). Trusting it would un-park
            # a bucket that a real 429 was told to block, so the park is kept and the value ignored.
            # The same guard deliberately does NOT apply to window learning: an older response can
            # still legitimately document the length of a whole window.
            self.state.remaining = 0
            if reset is not None:
                self.state.blocked_until = max(
                    self.state.blocked_until, reset - RESET_LEEWAY_SECONDS
                )
            return
        if remaining is not None:
            if remaining < 0:
                remaining = 0
            if self.state.limit is not None:
                remaining = min(remaining, self.state.limit)
            self.state.remaining = remaining
        if reset is not None and self.state.window_seconds is None and self.state.limit is not None:
            # Cadence is *measured*, never assumed (R19). ``Ratelimit-Reset`` publishes the absolute
            # end of the window Helix was serving when it answered, so a single response documents a
            # single instant and no interval exists to derive a cadence from. Waiting for the next
            # response that reports a full window and differencing its reset against the previous
            # one
            # measures the real window length. The gap keeps sub-second precision — Helix publishes
            # fractional seconds, and rounding it away would make a request throttled a moment
            # before
            # the boundary wait into the next window and be refused again. While nothing has been
            # measured the limiter stays permissive rather than guessing.
            full_window = limit is not None and remaining is not None and remaining >= limit
            if full_window:
                if self.state.last_reset is not None:
                    measured = reset - self.state.last_reset
                    if 0.001 <= measured <= _MAX_LEARNED_WINDOW_SECONDS:
                        self.state.window_seconds = measured
                        self.state.window_started = self.state.last_reset
                self.state.last_reset = reset
        if reset is not None and self.state.remaining == 0:
            # Park until the documented reset, minus a hair of leeway (R21).
            self.state.blocked_until = max(self.state.blocked_until, reset - RESET_LEEWAY_SECONDS)

    def block(self, seconds: float) -> None:
        """Park this bucket for ``seconds`` and mark the route spent so it is never replayed.

        Called by the client for every 429 (R21, R22). ``spent`` gates new acquisitions of the
        bucket
        until a learned window rotates; ``received_429`` tells the client that *this* request was
        throttled, so it must not replay it inside the same call.
        """
        self.state.blocked_until = max(self.state.blocked_until, self._limiter._clock() + seconds)
        self.state.blocked_for = max(self.state.blocked_for or 0.0, seconds)
        self.state.remaining = 0
        self.state.spent = True
        self.received_429 = True

    def release(self) -> None:
        if self._held:
            self._held = False
            self.state.lock.release()


class RateLimiter:
    """Per-token-type bucket store learned from headers, plus the process-wide 401 circuit."""

    def __init__(
        self,
        *,
        max_wait_seconds: float,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        unix_to_monotonic: float | None = None,
    ) -> None:
        self._max_wait = max_wait_seconds
        self._clock = clock
        self._sleep = sleep
        self._epoch = (
            measure_unix_to_monotonic() if unix_to_monotonic is None else unix_to_monotonic
        )
        self._buckets: dict[str, BucketState] = {}
        self._auth_failed: dict[TokenType, bool] = {}
        self._auth_message: dict[TokenType, str] = {}
        self._auth_listener: Callable[[], None] | None = None

    def _monotonic_reset(self, reset_unix: float) -> float:
        """Project an absolute ``Ratelimit-Reset`` unix stamp onto the injected clock's timeline."""
        return reset_unix - self._epoch

    # ------------------------------------------------------------ 401 circuit (R24)

    def auth_failed(self, token_type: TokenType) -> bool:
        """True once this token class's circuit is open (R24, C3).

        The app class clears only on restart. The user class clears through
        :meth:`reset_user_circuit`
        when a new user token is stored (C7).
        """
        return self._auth_failed.get(token_type, False)

    def set_auth_failure_listener(self, listener: Callable[[], None]) -> None:
        """Register the hook the circuit calls the first time it opens (R30's third trigger).

        A rejected credential has to drop the two live caches of R30/R33 — verified role verdicts
        and outstanding confirm tokens — in the same instant the circuit opens. Anything else lets
        a verdict made under the old credential keep authorising writes after Twitch revoked it.
        The listener is attached after construction because the caches belong to the service, which
        is built *from* this limiter. One listener, fired once per token class on that class's first
        open (C3): a second trip of the same class does not announce again.
        """
        self._auth_listener = listener

    def trip_auth(self, token_type: TokenType, *, message: str | None = None) -> None:
        """Open one token class's 401 circuit. Logged once per class, no credential material."""
        if not self.auth_failed(token_type):
            self._auth_failed[token_type] = True
            if message:
                self._auth_message[token_type] = message
            logger.error(
                "twitch 401 circuit open: no further Helix calls of this token class",
                extra={"outcome": "auth_circuit", "token_type": token_type},
            )
            if self._auth_listener is not None:
                try:
                    self._auth_listener()
                except Exception:  # noqa: BLE001 - the circuit opens even if the hook explodes
                    logger.exception("the 401 cache-invalidation listener raised")

    def reset_user_circuit(self) -> None:
        """Clear the user class's circuit only (C7). Runs when a user token is stored or forgotten.

        The app class is never cleared here: it resets only by restart (C-D3). It does not fire the
        auth listener; the caller already invalidated the identity caches.
        """
        self._auth_failed.pop(USER_CLASS, None)
        self._auth_message.pop(USER_CLASS, None)
        logger.info("twitch user circuit reset", extra={"outcome": "auth_circuit_reset"})

    # ------------------------------------------------------------ buckets

    def _state_for(self, key: str) -> BucketState:
        state = self._buckets.get(key)
        if state is None:
            state = BucketState()
            self._buckets[key] = state
        return state

    def _advance_window(self, state: BucketState, now: float) -> None:
        """Rotate a learned window when its reset instant has passed (R19)."""
        if state.window_seconds is None or state.limit is None:
            # Without a measured cadence there is no window boundary to rotate on, so a park would
            # be
            # permanent; the next response's headers are what release it.
            return
        if state.window_started is None:
            state.window_started = state.last_reset or now
        while now >= state.window_started + state.window_seconds:
            state.window_started += state.window_seconds
            state.remaining = state.limit
            state.spent = False
            state.last_reset = state.window_started + state.window_seconds
            if state.blocked_until <= state.window_started:
                state.blocked_until = 0.0

    async def _wait_bucket(self, state: BucketState) -> None:
        while True:
            now = self._clock()
            self._advance_window(state, now)
            if state.spent:
                # A real 429 parked this route (R22). It is never replayed inside this process: the
                # acquisition is refused with the wait Twitch documented, never an invented one
                # (R21).
                # A learned window rotation is what clears this; otherwise the next call decides.
                wait = state.blocked_until - now
                if state.window_started is not None and state.window_seconds is not None:
                    wait = max(wait, state.window_started + state.window_seconds - now)
                raise TwitchMcpError(
                    "rate_limited",
                    "This Twitch bucket was just rate-limited and is not replayed"
                    " until its window has passed",
                    retryable=True,
                    retry_after=round(max(wait, state.blocked_for or 0.0), 3),
                )
            wait = state.blocked_until - now
            if wait > 0:
                if wait > self._max_wait:
                    # Fail fast: the request is not made (R21).
                    raise TwitchMcpError(
                        "rate_limited",
                        "This Twitch bucket is rate-limited for longer than this call may wait",
                        retryable=True,
                        retry_after=round(wait, 3),
                    )
                await self._sleep(wait)
                continue
            if state.remaining is not None and state.remaining <= 0:
                if state.window_started is None or state.window_seconds is None:
                    # Headers said the window is empty but nothing has been measured yet (R19): the
                    # next response is what teaches the limiter, so let one through.
                    return
                # Headers said the window is empty but the reset has not advanced the state yet:
                # wait for the window boundary rather than burning a request on a certain 429.
                wait = state.window_started + state.window_seconds - now
                if wait > self._max_wait:
                    raise TwitchMcpError(
                        "rate_limited",
                        "This Twitch bucket is rate-limited for longer than this call may wait",
                        retryable=True,
                        retry_after=round(max(wait, 0.0), 3),
                    )
                await self._sleep(wait)
                continue
            return

    def _make_lease(self, token_type: TokenType, method: str, template: str, major: str) -> Lease:
        """Create (without waiting on) the lease for one route. Used by tests and by acquire."""
        key = route_key(token_type, method, template, major)
        return Lease(self, self._state_for(key), key)

    async def acquire(
        self,
        token_type: TokenType,
        method: str,
        template: str,
        major: str = "",
        *,
        circuit: bool = True,
        class_hint: bool = True,
    ) -> Lease:
        """Wait for permission to send one request. The caller must ``release()`` it.

        Raises ``kind=unauthenticated`` without a request once this token class's 401 circuit is
        open (R24, C3) and ``circuit`` is true; ``circuit=False`` callers (token exchange, refresh,
        revoke, the mint, validate) are never gated (C9). ``class_hint=False`` (writes, C6) refuses
        without the class hint. ``kind=rate_limited`` without a request when a bucket needs a longer
        wait than allowed (R21).
        """
        if circuit and self.auth_failed(token_type):
            raise TwitchMcpError(
                "unauthenticated",
                self._auth_message.get(token_type, _CIRCUIT_MESSAGE),
                http_status=401,
                hint=circuit_hint(token_type) if class_hint else NEUTRAL_AUTH_HINT,
            )
        key = route_key(token_type, method, template, major)
        state = self._state_for(key)
        await state.lock.acquire()
        lease = Lease(self, state, key)
        try:
            await self._wait_bucket(state)
        except BaseException:
            lease.release()
            raise
        return lease

    # ------------------------------------------------------------ reporting

    def snapshot(self, token_type: TokenType) -> dict[str, dict[str, float | int | None]]:
        """Learned state of every bucket of one token type, keyed by bucket key.

        Values are counts and seconds only — never ids, tokens or URLs (R53). Intended for tests,
        the boot log and `/healthz`-adjacent diagnostics.
        """
        prefix = f"{token_type}:"
        out: dict[str, dict[str, float | int | None]] = {}
        now = self._clock()
        for key, state in self._buckets.items():
            if not key.startswith(prefix):
                continue
            self._advance_window(state, now)
            out[key] = {
                "limit": state.limit,
                "remaining": state.remaining,
                "window_seconds": state.window_seconds,
                "blocked_for": max(0.0, round(state.blocked_until - now, 3)),
            }
        return out


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup (HTTP names are case-insensitive; Helix capitalises them)."""
    value = headers.get(name)
    if value is not None:
        return value
    for key, candidate in headers.items():
        if key.lower() == name:
            return candidate
    return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _parse_float(value: str | None) -> float | None:
    """Parse an absolute unix timestamp; NaN and non-numeric text are None."""
    if value is None:
        return None
    try:
        parsed = float(value.strip())
    except ValueError:
        return None
    if parsed != parsed or parsed < 0:  # NaN-safe
        return None
    return parsed
