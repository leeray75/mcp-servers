"""Tool-call budget (R36): overall and per-tool token buckets, process-wide.

A call that exceeds either bucket is refused with a retry hint and consumes no Discord request.
Refusals never queue. Both buckets are checked before either is charged, so a refused call costs
nothing.
"""

from __future__ import annotations

import time
from collections.abc import Callable

Clock = Callable[[], float]


class _TokenBucket:
    def __init__(self, per_minute: int, clock: Clock) -> None:
        self._capacity = float(per_minute)
        self._rate = per_minute / 60.0
        self._tokens = float(per_minute)
        self._clock = clock
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
        self._last = now

    def wait_seconds(self) -> float:
        """0 when a token is available now, otherwise the time until one is."""
        self._refill()
        if self._tokens >= 1:
            return 0.0
        return (1 - self._tokens) / self._rate

    def take(self) -> None:
        self._refill()
        self._tokens -= 1


class ToolBudget:
    """Global and per-tool token buckets (R36)."""

    def __init__(
        self, *, per_minute: int, per_tool_per_minute: int, clock: Clock = time.monotonic
    ) -> None:
        self._clock = clock
        self._overall = _TokenBucket(per_minute, clock)
        self._per_tool_limit = per_tool_per_minute
        self._per_tool: dict[str, _TokenBucket] = {}

    def try_consume(self, tool: str) -> float | None:
        """Charge one call to ``tool``. Returns None when allowed, else seconds to wait."""
        bucket = self._per_tool.get(tool)
        if bucket is None:
            bucket = _TokenBucket(self._per_tool_limit, self._clock)
            self._per_tool[tool] = bucket
        wait = max(self._overall.wait_seconds(), bucket.wait_seconds())
        if wait > 0:
            return round(wait, 3)
        self._overall.take()
        bucket.take()
        return None
