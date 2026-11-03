"""Async TTL/LRU cache for slow-changing Twitch metadata and verified identity.

Adapted from ``../discord-mcp-server`` (whose lineage runs back to ``sports-mcp-server``): bounded
by entry count *and* approximate bytes, with in-flight de-duplication. The TTL is supplied by the
caller (``TTL_METADATA_SECONDS`` for metadata, ``TWITCH_IDENTITY_TTL_SECONDS`` for identity) and a
TTL of 0 disables storage. A failed fetch raises and is never stored.

This cache holds **metadata and identity only**: games, emote/badge sets, and verified
moderator/editor membership. It is never used for list results — Helix lists are dynamic views and
a stale page would be indistinguishable from an empty one (R42's README note). Keys are built by the
caller from metadata kinds and ids only, never from tokens, chat content or MCP session identifiers.
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

# Parsed JSON costs several times its serialised size in Python objects.
RESIDENT_FACTOR = 4


def estimate_bytes(value: Any) -> int:
    """Approximate resident memory of a parsed JSON payload."""
    return len(json.dumps(value, separators=(",", ":"), default=str)) * RESIDENT_FACTOR


@dataclass(frozen=True, slots=True)
class Fetched:
    """A value plus where it came from."""

    value: Any
    fetched_at: datetime  # UTC wall-clock time of the upstream fetch
    age_seconds: int
    cached: bool


@dataclass(slots=True)
class _Entry:
    size: int
    value: Any
    fetched_mono: float
    fetched_at: datetime
    expires_mono: float


class TTLCache:
    """Bounded in-memory cache; one instance per process."""

    def __init__(
        self,
        *,
        max_entries: int,
        max_bytes: int,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._max = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0
        self._clock = clock
        self._wall = wall_clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[_Entry]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def total_bytes(self) -> int:
        """Approximate bytes held by cached values."""
        return self._bytes

    def _result(self, entry: _Entry, *, cached: bool) -> Fetched:
        age = max(0, int(self._clock() - entry.fetched_mono))
        return Fetched(entry.value, entry.fetched_at, age, cached)

    async def get(
        self,
        key: str,
        fetch: Callable[[], Awaitable[Any]],
        ttl_seconds: float,
    ) -> Fetched:
        """Return the cached value for ``key`` or fetch and (if ``ttl_seconds`` > 0) store it."""
        if ttl_seconds <= 0:
            value = await fetch()
            return Fetched(value, self._wall(), 0, False)
        entry = self._entries.get(key)
        if entry is not None and self._clock() < entry.expires_mono:
            self._entries.move_to_end(key)
            return self._result(entry, cached=True)

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._load(key, fetch, ttl_seconds))
            self._inflight[key] = task
            task.add_done_callback(lambda _t: self._inflight.pop(key, None))
        fresh = await asyncio.shield(task)
        return self._result(fresh, cached=False)

    async def _load(
        self, key: str, fetch: Callable[[], Awaitable[Any]], ttl_seconds: float
    ) -> _Entry:
        value = await fetch()
        now_mono = self._clock()
        entry = _Entry(
            estimate_bytes(value),
            value,
            now_mono,
            self._wall(),
            now_mono + max(0.0, ttl_seconds),
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

    def invalidate(self, prefix: str) -> int:
        """Drop every entry whose key starts with ``prefix``; return how many were dropped.

        Used by identity invalidation (R30): a token refresh, disconnect or the first 401 must not
        leave a stale moderator/editor verdict behind.
        """
        dropped = 0
        for key in [key for key in self._entries if key.startswith(prefix)]:
            self._drop(key)
            dropped += 1
        return dropped

    def clear(self) -> None:
        """Drop everything. Called on the 401 circuit opening and on disconnect (R30, R33)."""
        self._entries.clear()
        self._bytes = 0
