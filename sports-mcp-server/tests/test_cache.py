from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sports_mcp.cache import TTLCache
from sports_mcp.errors import UpstreamError


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.start = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

    def mono(self) -> float:
        return self.t

    def wall(self) -> datetime:
        return self.start + timedelta(seconds=self.t - 1000.0)


class Source:
    def __init__(self) -> None:
        self.calls = 0
        self.fail: Exception | None = None
        self.gate: asyncio.Event | None = None

    async def fetch(self) -> dict[str, Any]:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail is not None:
            raise self.fail
        return {"n": self.calls}


def make(clock: Clock, **kwargs: Any) -> TTLCache:
    return TTLCache(clock=clock.mono, wall_clock=clock.wall, **kwargs)


def ttl(seconds: float) -> Any:
    return lambda _value: seconds


async def test_hit_within_ttl_and_expiry() -> None:
    clock, src = Clock(), Source()
    cache = make(clock)
    first = await cache.get("k", src.fetch, ttl(30))
    assert (first.cached, first.age_seconds, src.calls) == (False, 0, 1)
    clock.t += 10
    second = await cache.get("k", src.fetch, ttl(30))
    assert second.cached is True and second.age_seconds == 10 and src.calls == 1
    assert second.value == first.value
    assert second.fetched_at == first.fetched_at  # as_of is the upstream fetch time
    clock.t += 21
    third = await cache.get("k", src.fetch, ttl(30))
    assert third.cached is False and src.calls == 2


async def test_ttl_is_decided_from_the_fetched_value() -> None:
    clock, src = Clock(), Source()
    cache = make(clock)
    live = lambda value: 15 if value["n"] == 1 else 120  # noqa: E731
    await cache.get("k", src.fetch, live)
    clock.t += 16
    await cache.get("k", src.fetch, live)  # first value expired after 15 s
    assert src.calls == 2
    clock.t += 100
    await cache.get("k", src.fetch, live)  # second value lives 120 s
    assert src.calls == 2


async def test_refresh_bypasses_and_updates_cache() -> None:
    clock, src = Clock(), Source()
    cache = make(clock)
    await cache.get("k", src.fetch, ttl(60))
    refreshed = await cache.get("k", src.fetch, ttl(60), refresh=True)
    assert refreshed.cached is False and src.calls == 2
    after = await cache.get("k", src.fetch, ttl(60))
    assert after.cached is True and after.value == refreshed.value and src.calls == 2


async def test_in_flight_requests_are_deduplicated() -> None:
    clock, src = Clock(), Source()
    src.gate = asyncio.Event()
    cache = make(clock)
    tasks = [asyncio.create_task(cache.get("k", src.fetch, ttl(30))) for _ in range(5)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    src.gate.set()
    results = await asyncio.gather(*tasks)
    assert src.calls == 1
    assert all(r.value == {"n": 1} for r in results)


async def test_failures_are_not_cached() -> None:
    clock, src = Clock(), Source()
    cache = make(clock, stale_if_error_seconds=0)
    src.fail = UpstreamError("ESPN is unavailable")
    with pytest.raises(UpstreamError):
        await cache.get("k", src.fetch, ttl(30))
    assert len(cache) == 0
    src.fail = None
    ok = await cache.get("k", src.fetch, ttl(30))
    assert ok.cached is False and src.calls == 2
    # a failure while a fresh copy exists is never even attempted
    src.fail = UpstreamError("boom")
    again = await cache.get("k", src.fetch, ttl(30))
    assert again.cached is True and src.calls == 2


async def test_stale_if_error_serves_recent_copy_then_gives_up() -> None:
    clock, src = Clock(), Source()
    cache = make(clock, stale_if_error_seconds=300)
    await cache.get("k", src.fetch, ttl(15))
    clock.t += 100  # expired but younger than the stale window
    src.fail = UpstreamError("ESPN is unavailable")
    stale = await cache.get("k", src.fetch, ttl(15))
    assert stale.stale is True and stale.cached is True and stale.age_seconds == 100
    clock.t += 250  # now 350 s old: older than STALE_IF_ERROR_SECONDS
    with pytest.raises(UpstreamError):
        await cache.get("k", src.fetch, ttl(15))


async def test_refresh_failure_also_falls_back_to_stale() -> None:
    clock, src = Clock(), Source()
    cache = make(clock)
    await cache.get("k", src.fetch, ttl(60))
    src.fail = UpstreamError("ESPN is unavailable")
    result = await cache.get("k", src.fetch, ttl(60), refresh=True)
    assert result.stale is True


async def test_lru_eviction_is_bounded() -> None:
    clock, src = Clock(), Source()
    cache = make(clock, max_entries=2)
    await cache.get("a", src.fetch, ttl(60))
    await cache.get("b", src.fetch, ttl(60))
    await cache.get("a", src.fetch, ttl(60))  # touch a: b is now least recently used
    await cache.get("c", src.fetch, ttl(60))
    assert len(cache) == 2
    calls = src.calls
    await cache.get("a", src.fetch, ttl(60))
    assert src.calls == calls  # a survived
    await cache.get("b", src.fetch, ttl(60))
    assert src.calls == calls + 1  # b was evicted


async def test_clear() -> None:
    clock, src = Clock(), Source()
    cache = make(clock)
    await cache.get("k", src.fetch, ttl(60))
    cache.clear()
    assert len(cache) == 0


async def test_default_clocks_work() -> None:
    cache = TTLCache()
    src = Source()
    result = await cache.get("k", src.fetch, ttl(60))
    assert result.fetched_at.tzinfo is not None
