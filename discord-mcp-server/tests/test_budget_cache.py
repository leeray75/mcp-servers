"""R36 (tool-call budget) and R37 (metadata cache)."""

from __future__ import annotations

import asyncio

import pytest

from discord_mcp.budget import ToolBudget
from discord_mcp.cache import TTLCache, estimate_bytes
from discord_mcp.errors import DiscordMcpError
from tests.conftest import FakeClock


def test_budget_refuses_overall_then_refills(fclock: FakeClock) -> None:
    budget = ToolBudget(per_minute=2, per_tool_per_minute=10, clock=fclock.clock)
    assert budget.try_consume("a") is None
    assert budget.try_consume("b") is None
    wait = budget.try_consume("c")
    assert wait is not None and wait > 0
    fclock.t += 31  # half a minute refills one token at 2 per minute
    assert budget.try_consume("c") is None


def test_budget_refuses_per_tool_without_charging_the_overall_bucket(fclock: FakeClock) -> None:
    budget = ToolBudget(per_minute=3, per_tool_per_minute=1, clock=fclock.clock)
    assert budget.try_consume("hot") is None
    assert budget.try_consume("hot") is not None  # per-tool exhausted
    # The refused call did not consume the overall bucket, so two other tools still fit.
    assert budget.try_consume("cold1") is None
    assert budget.try_consume("cold2") is None
    assert budget.try_consume("cold3") is not None  # now the overall bucket is empty


async def test_cache_stores_and_serves_fresh_values(fclock: FakeClock) -> None:
    cache = TTLCache(max_entries=8, max_bytes=10_000, clock=fclock.clock, wall_clock=fclock.wall)
    calls: list[int] = []

    async def fetch() -> dict[str, int]:
        calls.append(1)
        return {"v": len(calls)}

    first = await cache.get("guild:1", fetch, 60)
    second = await cache.get("guild:1", fetch, 60)
    assert first.cached is False and second.cached is True
    assert second.value == {"v": 1}
    assert len(calls) == 1
    fclock.t += 61
    third = await cache.get("guild:1", fetch, 60)
    assert third.cached is False and len(calls) == 2


async def test_zero_ttl_disables_storage(fclock: FakeClock) -> None:
    cache = TTLCache(max_entries=8, max_bytes=10_000, clock=fclock.clock, wall_clock=fclock.wall)
    calls: list[int] = []

    async def fetch() -> int:
        calls.append(1)
        return 1

    await cache.get("k", fetch, 0)
    await cache.get("k", fetch, 0)
    assert len(calls) == 2
    assert len(cache) == 0


async def test_failed_fetch_is_never_cached(fclock: FakeClock) -> None:
    cache = TTLCache(max_entries=8, max_bytes=10_000, clock=fclock.clock, wall_clock=fclock.wall)
    attempts: list[int] = []

    async def fetch() -> int:
        attempts.append(1)
        if len(attempts) == 1:
            raise DiscordMcpError("upstream", "boom")
        return 7

    with pytest.raises(DiscordMcpError):
        await cache.get("k", fetch, 60)
    assert len(cache) == 0
    assert (await cache.get("k", fetch, 60)).value == 7


async def test_in_flight_requests_are_deduplicated(fclock: FakeClock) -> None:
    cache = TTLCache(max_entries=8, max_bytes=10_000, clock=fclock.clock, wall_clock=fclock.wall)
    calls: list[int] = []
    gate = asyncio.Event()

    async def fetch() -> int:
        calls.append(1)
        await gate.wait()
        return 3

    first = asyncio.create_task(cache.get("k", fetch, 60))
    second = asyncio.create_task(cache.get("k", fetch, 60))
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(first, second)
    assert [r.value for r in results] == [3, 3]
    assert len(calls) == 1


async def test_byte_bound_evicts_least_recently_used(fclock: FakeClock) -> None:
    payload = {"blob": "x" * 200}
    size = estimate_bytes(payload)
    cache = TTLCache(
        max_entries=100, max_bytes=size * 2, clock=fclock.clock, wall_clock=fclock.wall
    )

    async def fetch() -> dict[str, str]:
        return payload

    await cache.get("a", fetch, 60)
    await cache.get("b", fetch, 60)
    await cache.get("a", fetch, 60)  # touch a so b is least recently used
    await cache.get("c", fetch, 60)
    assert cache.total_bytes <= size * 2
    assert (await cache.get("a", fetch, 60)).cached is True
    assert (await cache.get("b", fetch, 60)).cached is False


async def test_oversized_payload_is_not_kept(fclock: FakeClock) -> None:
    cache = TTLCache(max_entries=8, max_bytes=50, clock=fclock.clock, wall_clock=fclock.wall)

    async def fetch() -> dict[str, str]:
        return {"blob": "y" * 500}

    result = await cache.get("big", fetch, 60)
    assert result.cached is False
    assert len(cache) == 0


async def test_entry_count_bound_evicts(fclock: FakeClock) -> None:
    cache = TTLCache(max_entries=2, max_bytes=10_000, clock=fclock.clock, wall_clock=fclock.wall)

    async def fetch() -> int:
        return 1

    for key in ("a", "b", "c"):
        await cache.get(key, fetch, 60)
    assert len(cache) == 2
    cache.clear()
    assert len(cache) == 0 and cache.total_bytes == 0
