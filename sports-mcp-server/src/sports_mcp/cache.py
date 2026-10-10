"""Async TTL cache with LRU eviction, in-flight de-duplication and stale-if-error.

Entries are keyed by the normalised upstream URL. The TTL of an entry is decided *after*
the fetch from the fetched value (a scoreboard with a live game expires sooner).
Failed fetches are never stored.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sports_mcp.errors import SportsMcpError


@dataclass(frozen=True, slots=True)
class Fetched:
    """A value plus where it came from."""

    value: Any
    fetched_at: datetime  # UTC wall-clock time of the upstream fetch
    age_seconds: int
    cached: bool
    stale: bool = False


# Parsed JSON costs several times its serialised size in Python objects; this factor turns the
# compact-JSON length into an approximate resident size (measured ~4.5x on ESPN summaries).
RESIDENT_FACTOR = 4


def estimate_bytes(value: Any) -> int:
    """Approximate resident memory of a parsed JSON payload."""
    return len(json.dumps(value, separators=(",", ":"), default=str)) * RESIDENT_FACTOR


@dataclass(slots=True)
class _Entry:
    size: int
    value: Any
    fetched_mono: float
    fetched_at: datetime
    expires_mono: float


class TTLCache:
    """Bounded in-memory cache; one instance per server."""

    def __init__(
        self,
        max_entries: int = 256,
        stale_if_error_seconds: int = 300,
        max_bytes: int = 96 * 1024 * 1024,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._max = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0
        self._stale_if_error = stale_if_error_seconds
        self._clock = clock
        self._wall = wall_clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[Any]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def total_bytes(self) -> int:
        """Approximate bytes held by cached values."""
        return self._bytes

    def _result(self, entry: _Entry, *, cached: bool, stale: bool = False) -> Fetched:
        age = max(0, int(self._clock() - entry.fetched_mono))
        return Fetched(entry.value, entry.fetched_at, age, cached, stale)

    async def get(
        self,
        key: str,
        fetch: Callable[[], Awaitable[Any]],
        ttl: Callable[[Any], float],
        *,
        refresh: bool = False,
    ) -> Fetched:
        """Return the cached value for ``key`` or fetch it.

        Args:
            key: normalised upstream URL.
            fetch: coroutine function performing the upstream call.
            ttl: maps the fetched value to its time-to-live in seconds.
            refresh: bypass a fresh cache entry (the result still updates the cache).
        """
        entry = self._entries.get(key)
        if entry is not None and not refresh and self._clock() < entry.expires_mono:
            self._entries.move_to_end(key)
            return self._result(entry, cached=True)

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._load(key, fetch, ttl))
            self._inflight[key] = task
            task.add_done_callback(lambda _t: self._inflight.pop(key, None))
        try:
            fresh: _Entry = await asyncio.shield(task)
        except SportsMcpError:
            stale = self._entries.get(key)
            if stale is not None and self._clock() - stale.fetched_mono <= self._stale_if_error:
                return self._result(stale, cached=True, stale=True)
            raise
        return self._result(fresh, cached=False)

    async def _load(
        self, key: str, fetch: Callable[[], Awaitable[Any]], ttl: Callable[[Any], float]
    ) -> _Entry:
        value = await fetch()
        now_mono = self._clock()
        entry = _Entry(
            estimate_bytes(value), value, now_mono, self._wall(), now_mono + max(0.0, ttl(value))
        )
        self._drop(key)
        if entry.size <= self._max_bytes:  # a payload bigger than the whole budget is not kept
            self._entries[key] = entry
            self._bytes += entry.size
        while self._entries and (len(self._entries) > self._max or self._bytes > self._max_bytes):
            self._drop(next(iter(self._entries)))  # least recently used first
        return entry

    def _drop(self, key: str) -> None:
        removed = self._entries.pop(key, None)
        if removed is not None:
            self._bytes -= removed.size

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0
