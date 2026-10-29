"""Process-wide Discord rate-limit state (R20-R24, section 9).

Nothing here imports a network library. The client (``discord_client.py``) asks for a
:class:`Lease` before each request, feeds response headers back through it, and reports 429s and
invalid responses. All state is process-wide and is never keyed to an MCP connection or session.
Clock and sleep are injected so tests run instantly.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Final

from discord_mcp.errors import DiscordMcpError

logger = logging.getLogger(__name__)

INVALID_WINDOW_SECONDS: Final = 600.0
RESET_JITTER_SECONDS: Final = 0.25

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(eq=False)
class BucketState:
    """Learned state of one bucket (or one route before its bucket is learned)."""

    remaining: int | None = None
    reset_at: float | None = None  # monotonic time at which the bucket refills
    blocked_until: float = 0.0  # monotonic; set by 429 handling
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def route_key(method: str, template: str, major: str) -> str:
    """Route key = method + route template + major parameter value (section 9)."""
    return f"{method} {template} {major}"


class Lease:
    """Exclusive use of one bucket for one request. Release it in a ``finally``."""

    def __init__(self, limiter: RateLimiter, base: str, major: str, state: BucketState) -> None:
        self._limiter = limiter
        self._base = base
        self._major = major
        self.state = state
        self._held = True

    def learn(self, headers: Mapping[str, str]) -> None:
        """Update bucket identity and counters from a response's ``X-RateLimit-*`` headers."""
        limiter = self._limiter
        bucket = headers.get("x-ratelimit-bucket")
        if bucket:
            limiter._route_buckets[self._base] = bucket
            # Never replace a live state object: another route may already hold this bucket's
            # lock and blocked_until (review m3). The first state learned for a key wins.
            key = limiter._bucket_key(bucket, self._major)
            existing = limiter._buckets.setdefault(key, self.state)
            if existing is not self.state:
                existing.blocked_until = max(existing.blocked_until, self.state.blocked_until)
        remaining = _parse_int(headers.get("x-ratelimit-remaining"))
        if remaining is not None:
            self.state.remaining = remaining
        reset_after = _parse_float(headers.get("x-ratelimit-reset-after"))
        if reset_after is not None:
            self.state.reset_at = limiter._clock() + reset_after

    def block(self, seconds: float) -> None:
        """Block this bucket for ``seconds`` (user and shared 429s)."""
        self.state.blocked_until = max(self.state.blocked_until, self._limiter._clock() + seconds)

    def release(self) -> None:
        if self._held:
            self._held = False
            self.state.lock.release()


class RateLimiter:
    """Bucket store, global pause, global pacing and the invalid-request window."""

    def __init__(
        self,
        *,
        max_wait_seconds: float,
        invalid_budget: int,
        global_rps: float,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._max_wait = max_wait_seconds
        self._invalid_budget = invalid_budget
        self._rps = global_rps
        self._clock = clock
        self._sleep = sleep
        self._rand = rand
        self._route_buckets: dict[str, str] = {}
        self._buckets: dict[str, BucketState] = {}
        self._global_pause_until = 0.0
        self._tokens = global_rps
        self._last_refill = clock()
        self._invalid: deque[float] = deque()
        self._warned = False
        self._auth_failed = False

    # ------------------------------------------------------------ hard stops

    @property
    def auth_failed(self) -> bool:
        return self._auth_failed

    def trip_auth(self) -> None:
        """Set the process-wide auth hard-stop (R19). Logged once."""
        if not self._auth_failed:
            self._auth_failed = True
            logger.error("discord auth hard-stop: the bot token was rejected")

    def _prune(self) -> None:
        cutoff = self._clock() - INVALID_WINDOW_SECONDS
        while self._invalid and self._invalid[0] <= cutoff:
            self._invalid.popleft()
        if not self._invalid:
            self._warned = False

    def record_invalid(self) -> None:
        """Count a 401, 403 or non-shared 429 in the sliding 10-minute window (R24)."""
        self._prune()
        self._invalid.append(self._clock())
        if not self._warned and len(self._invalid) * 2 >= self._invalid_budget:
            self._warned = True
            logger.warning(
                "invalid-request budget at 50% in the last 10 minutes",
                extra={"outcome": "invalid_budget_warning"},
            )

    def circuit_retry_after(self) -> float | None:
        """Seconds until the circuit closes, or None when it is closed (R24)."""
        self._prune()
        if len(self._invalid) < self._invalid_budget:
            return None
        return max(0.0, self._invalid[0] + INVALID_WINDOW_SECONDS - self._clock())

    def check_open(self) -> None:
        """Refuse locally when the auth hard-stop or the invalid-request circuit is active."""
        if self._auth_failed:
            raise DiscordMcpError("auth", "The bot token was rejected by Discord")
        wait = self.circuit_retry_after()
        if wait is not None:
            raise DiscordMcpError(
                "rate_limited",
                "Too many invalid requests to Discord recently; calls are paused",
                retryable=True,
                retry_after=round(wait, 3),
                hint="Wait retry_after seconds; the invalid-request window is draining.",
            )

    # ------------------------------------------------------------ global controls

    def pause_global(self, seconds: float) -> None:
        """Pause every request for the full ``seconds`` (global 429, R22)."""
        self._global_pause_until = max(self._global_pause_until, self._clock() + seconds)

    async def _wait_global(self) -> None:
        while True:
            wait = self._global_pause_until - self._clock()
            if wait <= 0:
                return
            if wait > self._max_wait:
                raise DiscordMcpError(
                    "rate_limited",
                    "Discord asked for a global pause longer than this call may wait",
                    retryable=True,
                    retry_after=round(wait, 3),
                )
            await self._sleep(wait)

    async def _take_token(self) -> None:
        """Global pacing token bucket (R23)."""
        while True:
            now = self._clock()
            self._tokens = min(self._rps, self._tokens + (now - self._last_refill) * self._rps)
            self._last_refill = now
            if self._tokens >= 1:
                self._tokens -= 1
                return
            await self._sleep((1 - self._tokens) / self._rps)

    # ------------------------------------------------------------ buckets

    def _bucket_key(self, bucket: str, major: str) -> str:
        return f"bucket:{bucket}:{major}"

    def _state_for(self, method: str, template: str, major: str) -> tuple[str, BucketState]:
        base = f"{method} {template}"
        bucket = self._route_buckets.get(base)
        key = self._bucket_key(bucket, major) if bucket else route_key(method, template, major)
        state = self._buckets.get(key)
        if state is None:
            state = BucketState()
            self._buckets[key] = state
        return base, state

    async def _wait_bucket(self, state: BucketState) -> None:
        while True:
            now = self._clock()
            until = state.blocked_until
            if state.remaining == 0 and state.reset_at is not None:
                until = max(until, state.reset_at)
            wait = until - now
            if wait <= 0:
                return
            if wait > self._max_wait:
                raise DiscordMcpError(
                    "rate_limited",
                    "This Discord bucket is rate-limited for longer than this call may wait",
                    retryable=True,
                    retry_after=round(wait, 3),
                )
            await self._sleep(wait)

    async def acquire(self, method: str, template: str, major: str = "") -> Lease:
        """Wait for permission to send one request on this route. Caller must ``release()``."""
        self.check_open()
        await self._wait_global()
        await self._take_token()
        base, state = self._state_for(method, template, major)
        await state.lock.acquire()
        lease = Lease(self, base, major, state)
        try:
            await self._wait_bucket(state)
        except BaseException:
            lease.release()
            raise
        return lease

    def jitter(self) -> float:
        return self._rand() * RESET_JITTER_SECONDS


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None
