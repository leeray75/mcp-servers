"""Shared fixtures. Nothing here touches the network or real credentials.

**This file is frozen** — it deliberately holds only what must be autouse: the network block (R55),
``httpx``'s redirect guard (R12) and the environment hygiene fixture. Everything reusable lives in
:mod:`tests.twitch_fakes`, which later test modules import directly. Add shared helpers there, not
here, so the modules owned by other agents never need to edit this file.

See the :mod:`tests.twitch_fakes` docstring for :class:`FakeTwitch`, :class:`FakeClock`,
:func:`make_settings`, :func:`set_env` and :func:`make_guard`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from tests.twitch_fakes import (
    ENV_NAMES,
    FakeClock,
    FakeTwitch,
    make_settings,
)
from twitch_mcp.cache import TTLCache
from twitch_mcp.config import Settings
from twitch_mcp.ratelimit import RateLimiter


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer's real environment never leaks into tests (R6: config.py is the only reader)."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """R55: any request that would leave the process fails the run.

    Patching httpx's real transport catches every path to the socket, including a client built
    directly by a module under test, so no test can reach Twitch by accident or by omission.
    """

    async def refuse(self: object, req: httpx.Request) -> httpx.Response:
        raise AssertionError(f"test attempted a real network request: {req.url.host}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


@pytest.fixture(autouse=True)
def _no_silent_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """R12: redirects are an error, never a hop — a module under test cannot opt into following them.

    httpx's own redirect plumbing is disabled for the whole run, so a client that was built with
    ``follow_redirects=True`` (a bug) fails on the 3xx instead of quietly hopping. The client-level
    assertion for the real client lives in ``test_twitch_client`` (owned by the client agent); this
    makes the bug impossible to hide in the meantime.
    """

    def refuse(
        self: httpx._client.AsyncClient,
        request: httpx.Request,
        *,
        follow_redirects: bool,
        history: list[httpx.Response],
    ) -> Any:
        if follow_redirects:
            raise AssertionError("redirects must never be followed (R12)")
        return real(self, request, follow_redirects=False, history=history)

    real = httpx._client.AsyncClient._send_handling_redirects
    monkeypatch.setattr(httpx._client.AsyncClient, "_send_handling_redirects", refuse, True)


@pytest.fixture
def fake() -> FakeTwitch:
    return FakeTwitch()


@pytest.fixture
def fclock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def limiter(fclock: FakeClock) -> Iterator[RateLimiter]:
    """A limiter on the fake clock with a generous in-call wait (override per test)."""
    yield RateLimiter(max_wait_seconds=30.0, clock=fclock.clock, sleep=fclock.sleep)


@pytest.fixture
def cache(fclock: FakeClock) -> TTLCache:
    return TTLCache(max_entries=64, max_bytes=1_000_000, clock=fclock.clock, wall_clock=fclock.wall)
