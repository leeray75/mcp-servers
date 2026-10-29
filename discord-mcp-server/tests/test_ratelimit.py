"""R20-R24 and section 9: buckets, pre-emptive waits, global pause and pacing, invalid window."""

from __future__ import annotations

import asyncio
import logging

import pytest

from discord_mcp.errors import DiscordMcpError
from discord_mcp.ratelimit import INVALID_WINDOW_SECONDS, RateLimiter, route_key
from tests.conftest import FakeClock


def limiter(fclock: FakeClock, *, max_wait: float = 30.0, budget: int = 1000) -> RateLimiter:
    return RateLimiter(
        max_wait_seconds=max_wait,
        invalid_budget=budget,
        global_rps=1000.0,
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )


async def test_bucket_is_learned_from_headers_and_pre_emptive_wait_applies(
    fclock: FakeClock,
) -> None:
    rl = limiter(fclock)
    lease = await rl.acquire("GET", "/channels/{channel_id}", "1")
    lease.learn(
        {
            "x-ratelimit-bucket": "abc",
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset-after": "2.5",
        }
    )
    lease.release()
    # The next request on the same route is in the learned bucket and must wait for the reset.
    lease2 = await rl.acquire("GET", "/channels/{channel_id}", "1")
    assert fclock.sleeps == [2.5]
    lease2.release()


async def test_pre_emptive_wait_refuses_when_longer_than_the_in_call_limit(
    fclock: FakeClock,
) -> None:
    rl = limiter(fclock, max_wait=5.0)
    lease = await rl.acquire("GET", "/x", "")
    lease.learn({"x-ratelimit-remaining": "0", "x-ratelimit-reset-after": "60"})
    lease.release()
    with pytest.raises(DiscordMcpError) as info:
        await rl.acquire("GET", "/x", "")
    assert info.value.kind == "rate_limited"
    assert info.value.retry_after == 60.0
    assert fclock.sleeps == []


async def test_different_major_parameters_do_not_share_a_bucket(fclock: FakeClock) -> None:
    rl = limiter(fclock)
    lease = await rl.acquire("GET", "/channels/{channel_id}/messages", "1")
    lease.learn(
        {
            "x-ratelimit-bucket": "shared",
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset-after": "9",
        }
    )
    lease.release()
    other = await rl.acquire("GET", "/channels/{channel_id}/messages", "2")
    assert fclock.sleeps == []
    other.release()


async def test_same_bucket_requests_are_serialised(fclock: FakeClock) -> None:
    rl = limiter(fclock)
    first = await rl.acquire("GET", "/r", "1")
    second_started = asyncio.Event()

    async def second() -> None:
        lease = await rl.acquire("GET", "/r", "1")
        second_started.set()
        lease.release()

    task = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert not second_started.is_set()  # still waiting for the first lease
    first.release()
    await task
    assert second_started.is_set()


async def test_block_is_full_retry_after_and_is_not_capped(fclock: FakeClock) -> None:
    rl = limiter(fclock, max_wait=30.0)
    lease = await rl.acquire("GET", "/long", "")
    lease.block(1337.0)
    lease.release()
    with pytest.raises(DiscordMcpError) as info:
        await rl.acquire("GET", "/long", "")
    assert info.value.retry_after == 1337.0
    assert info.value.retryable is True


async def test_global_pause_blocks_unrelated_routes_then_clears(fclock: FakeClock) -> None:
    rl = limiter(fclock, max_wait=30.0)
    rl.pause_global(4.0)
    lease = await rl.acquire("GET", "/unrelated", "")
    assert fclock.sleeps == [4.0]
    lease.release()


async def test_global_pause_longer_than_limit_refuses_locally(fclock: FakeClock) -> None:
    rl = limiter(fclock, max_wait=2.0)
    rl.pause_global(90.0)
    with pytest.raises(DiscordMcpError) as info:
        await rl.acquire("GET", "/unrelated", "")
    assert info.value.kind == "rate_limited"
    assert info.value.retry_after == 90.0


async def test_global_pacing_spaces_requests(fclock: FakeClock) -> None:
    rl = RateLimiter(
        max_wait_seconds=30.0,
        invalid_budget=1000,
        global_rps=10.0,
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    for index in range(12):
        lease = await rl.acquire("GET", f"/p{index}", "")
        lease.release()
    assert fclock.sleeps, "pacing must sleep once the burst is spent"
    assert all(s > 0 for s in fclock.sleeps)


async def test_auth_hard_stop_refuses_everything_and_logs_once(
    fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    rl = limiter(fclock)
    with caplog.at_level(logging.ERROR):
        rl.trip_auth()
        rl.trip_auth()
    assert rl.auth_failed
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1
    with pytest.raises(DiscordMcpError) as info:
        await rl.acquire("GET", "/x", "")
    assert info.value.kind == "auth"


async def test_invalid_budget_warns_at_half_and_opens_circuit(
    fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    rl = limiter(fclock, budget=10)
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            rl.record_invalid()
        rl.record_invalid()  # second call inside the same window: no second warning
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    for _ in range(4):
        rl.record_invalid()
    with pytest.raises(DiscordMcpError) as info:
        await rl.acquire("GET", "/x", "")
    assert info.value.kind == "rate_limited"
    assert info.value.retry_after is not None and info.value.retry_after > 0


async def test_circuit_closes_once_the_window_drains(fclock: FakeClock) -> None:
    rl = limiter(fclock, budget=10)
    for _ in range(10):
        rl.record_invalid()
    assert rl.circuit_retry_after() is not None
    fclock.t += INVALID_WINDOW_SECONDS + 1
    assert rl.circuit_retry_after() is None
    lease = await rl.acquire("GET", "/x", "")
    lease.release()


def test_route_key_includes_method_template_and_major() -> None:
    assert route_key("GET", "/channels/{channel_id}", "5") == "GET /channels/{channel_id} 5"


def test_jitter_uses_injected_random() -> None:
    rl = RateLimiter(max_wait_seconds=1, invalid_budget=10, global_rps=5, rand=lambda: 1.0)
    assert rl.jitter() == 0.25
