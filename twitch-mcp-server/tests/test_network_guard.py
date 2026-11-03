"""R55: the autouse network block actually fires, and the shared fakes are usable offline."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from tests.twitch_fakes import BROADCASTER, FakeClock, FakeTwitch, make_settings


async def test_direct_httpx_call_fails_the_run() -> None:
    """R55: a request built by hand and sent through the real transport must raise, not reach the net."""
    async with httpx.AsyncClient() as client:
        with pytest.raises(AssertionError, match="real network request"):
            await client.get("https://api.twitch.tv/helix/users")


async def test_direct_call_to_the_auth_host_also_fails() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(AssertionError, match="real network request"):
            await client.post("https://id.twitch.tv/oauth2/token", data={"grant_type": "x"})


async def test_mock_transport_is_still_usable() -> None:
    """The block targets the real transport only; the shared fake still serves responses."""
    fake = FakeTwitch()
    fake.add("GET", r"/helix/users", fake.json({"data": [{"id": BROADCASTER}]}))
    async with httpx.AsyncClient(
        transport=fake.transport(), base_url="https://api.twitch.tv"
    ) as client:
        response = await client.get("/helix/users")
    assert response.status_code == 200
    assert fake.request_count == 1


def test_shared_settings_fixture_is_valid() -> None:
    settings = make_settings()
    assert settings.api_host == "api.twitch.tv"
    assert settings.auth_host == "id.twitch.tv"
    assert settings.broadcaster_ids == frozenset({BROADCASTER})


def test_twitch_client_is_the_only_module_that_imports_httpx() -> None:
    """R11 / acceptance A5: exactly one source module may import httpx, and a second one fails here.

    All network I/O is meant to sit behind :mod:`twitch_mcp.twitch_client`, so a stray import is a
    hole in the loopback-only posture rather than a style nit. The scan reads the *import statements*
    out of the AST: the word ``httpx`` also appears in module docstrings and in the third-party
    logger table in ``logging_setup.py``, and neither of those is a network path.
    """
    src = Path(__file__).resolve().parent.parent / "src" / "twitch_mcp"
    assert src.is_dir(), f"expected the package at {src}"
    offenders: list[str] = []
    for path in sorted(src.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                hit = any(alias.name.split(".")[0] == "httpx" for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                hit = (node.module or "").split(".")[0] == "httpx"
            else:
                continue
            if hit:
                offenders.append(path.name)
    assert set(offenders) == {"twitch_client.py"}, f"httpx escaped twitch_client.py: {offenders}"


async def test_fake_clock_sleep_advances_one_timeline() -> None:
    clock = FakeClock()
    before = clock.clock()
    await clock.sleep(12.5)
    assert clock.clock() == before + 12.5
    assert clock.sleeps == [12.5]


def test_unregistered_request_is_counted_and_visible() -> None:
    """An unexpected call surfaces as a 404 plus a recorded request, never as a silent pass."""
    fake = FakeTwitch()
    assert fake.strict is False
    fake.handle(httpx.Request("GET", "https://api.twitch.tv/helix/nobody-uses-this"))
    assert fake.request_count == 1
    assert fake.paths == ["/helix/nobody-uses-this"]
