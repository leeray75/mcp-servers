"""R4, R16-R19, R22, R24, R31: HTTP client behaviour against the fake Discord."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from discord_mcp.config import Settings
from discord_mcp.discord_client import DiscordClient, Route
from discord_mcp.errors import DiscordMcpError
from tests.conftest import API, BOT, FAKE_BOT_TOKEN, FakeClock, FakeDiscord, make_settings

SRC = Path(__file__).resolve().parent.parent / "src" / "discord_mcp"
CHANNEL = Route.build(
    "/channels/{channel_id}", "999999999999999999", channel_id="999999999999999999"
)


def _json(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


def _rate(
    retry_after: float, *, scope: str | None = "user", global_flag: bool = False
) -> httpx.Response:
    headers = {"retry-after": str(retry_after)}
    if scope is not None:
        headers["x-ratelimit-scope"] = scope
    body: dict[str, Any] = {"message": "You are being rate limited.", "retry_after": retry_after}
    if global_flag:
        body["global"] = True
    return httpx.Response(429, json=body, headers=headers)


async def test_request_carries_auth_user_agent_and_accept(
    client: DiscordClient, fake: FakeDiscord
) -> None:
    await client.get(CHANNEL)
    request = fake.requests[-1]
    assert request.headers["authorization"] == f"Bot {FAKE_BOT_TOKEN}"
    assert request.headers["user-agent"].startswith("DiscordBot (")
    assert request.headers["accept"] == "application/json"
    assert request.url.host == "discord.com"
    assert request.url.path == f"{API}/channels/999999999999999999"


async def test_success_returns_decoded_body(client: DiscordClient) -> None:
    response = await client.get(CHANNEL)
    assert response.status == 200
    assert response.body["name"] == "general"


async def test_redirects_are_never_followed(client: DiscordClient, fake: FakeDiscord) -> None:
    fake.add(
        "GET",
        r".*/channels/\d+",
        lambda _r: httpx.Response(302, headers={"location": "https://evil.example.com/x"}),
        times=1,
    )
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "upstream"
    assert len(fake.requests) == 1


async def test_oversized_body_is_refused_and_not_retried(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings = make_settings(discord_max_response_bytes=65_536)
    big = {"blob": "x" * 70_000}
    fake.add("GET", r".*/channels/\d+", fake.json(big), times=None)
    client = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "upstream"
    assert "size cap" in info.value.message
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    ("status", "kind", "code"),
    [
        (400, "validation", None),
        (403, "permission", 50013),
        (404, "not_found", 10003),
        (400, "permission", 50001),  # numeric code wins over the status
        (403, "permission", None),
        (410, "upstream", None),
        (404, "not_found", 10008),
    ],
)
async def test_error_mapping_uses_status_and_numeric_code_only(
    client: DiscordClient, fake: FakeDiscord, status: int, kind: str, code: int | None
) -> None:
    body = {"code": code, "message": "Missing Access"} if code else {"message": "whatever"}
    fake.add("GET", r".*/channels/\d+", fake.json(body, status), times=None)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == kind
    assert info.value.code == code
    assert info.value.http_status == status
    assert len(fake.requests) == 1  # 4xx other than 429 is never retried


async def test_message_text_is_ignored_when_it_contradicts_the_code(
    client: DiscordClient, fake: FakeDiscord
) -> None:
    fake.add(
        "GET",
        r".*/channels/\d+",
        fake.json({"code": 10003, "message": "Missing Permissions"}, 404),
        times=None,
    )
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "not_found"


async def test_5xx_is_retried_twice_with_backoff_then_upstream(
    client: DiscordClient, fake: FakeDiscord, fclock: FakeClock
) -> None:
    fake.add("GET", r".*/channels/\d+", fake.json({"message": "down"}, 503), times=None)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "upstream" and info.value.retryable is True
    assert len(fake.requests) == 3
    assert fclock.sleeps == [0.5, 1.0]


async def test_5xx_then_success_returns_the_success(
    client: DiscordClient, fake: FakeDiscord
) -> None:
    fake.add("GET", r".*/channels/\d+", fake.json({}, 500), times=1)
    response = await client.get(CHANNEL)
    assert response.status == 200
    assert len(fake.requests) == 2


async def test_connect_failure_is_retried_then_unavailable(
    client: DiscordClient, fake: FakeDiscord, fclock: FakeClock
) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    fake.add("GET", r".*/channels/\d+", refuse, times=None)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "unavailable"
    assert info.value.retryable is True
    assert len(fake.requests) == 3
    assert fclock.sleeps == [0.5, 1.0]


async def test_read_timeout_is_an_upstream_error(client: DiscordClient, fake: FakeDiscord) -> None:
    def slow(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    fake.add("GET", r".*/channels/\d+", slow, times=None)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "upstream"


async def test_non_json_success_is_an_unexpected_response(
    client: DiscordClient, fake: FakeDiscord
) -> None:
    fake.add(
        "GET", r".*/channels/\d+", lambda _r: httpx.Response(200, content=b"<html>"), times=None
    )
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "upstream"


async def test_unauthorised_trips_the_hard_stop_and_later_calls_send_nothing(
    client: DiscordClient, fake: FakeDiscord
) -> None:
    fake.add("GET", r".*/channels/\d+", fake.json({"code": 0, "message": "401"}, 401), times=1)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "auth" and info.value.retryable is False
    assert client.auth_failed
    sent = len(fake.requests)
    with pytest.raises(DiscordMcpError) as again:
        await client.get(CHANNEL)
    assert again.value.kind == "auth"
    assert len(fake.requests) == sent  # no request after the hard-stop


async def test_user_429_is_replayed_once_after_the_full_wait(
    client: DiscordClient, fake: FakeDiscord, fclock: FakeClock
) -> None:
    fake.add("GET", r".*/channels/\d+", lambda _r: _rate(2.0), times=1)
    response = await client.get(CHANNEL)
    assert response.status == 200
    assert len(fake.requests) == 2
    assert fclock.sleeps and fclock.sleeps[0] >= 2.0


async def test_user_429_beyond_the_in_call_limit_returns_rate_limited(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings = make_settings(discord_max_rate_limit_wait_seconds=5)
    client = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    fake.add("GET", r".*/channels/\d+", lambda _r: _rate(1337.0), times=None)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "rate_limited"
    assert info.value.retry_after == 1337.0
    assert info.value.retryable is True
    assert len(fake.requests) == 1


async def test_user_429_replays_are_capped(fake: FakeDiscord, fclock: FakeClock) -> None:
    settings = make_settings(discord_max_429_retries=2)
    client = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    fake.add("GET", r".*/channels/\d+", lambda _r: _rate(0.5), times=None)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "rate_limited"
    assert len(fake.requests) == 3  # the first try plus two replays


async def test_global_429_pauses_unrelated_routes(
    client: DiscordClient, fake: FakeDiscord, fclock: FakeClock
) -> None:
    fake.add(
        "GET", r".*/channels/\d+", lambda _r: _rate(3.0, scope=None, global_flag=True), times=1
    )
    await client.get(CHANNEL)
    members = Route.build(
        "/guilds/{guild_id}/members/{user_id}",
        "333333333333333333",
        guild_id="333333333333333333",
        user_id=BOT,
    )
    await client.get(members)
    assert 3.0 in fclock.sleeps  # the unrelated route waited for the global pause


async def test_shared_429_blocks_the_bucket_is_not_replayed_or_counted(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings = make_settings(discord_invalid_request_budget=10)
    client = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    fake.add("GET", r".*/channels/\d+", lambda _r: _rate(4.0, scope="shared"), times=1)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "rate_limited"
    assert len(fake.requests) == 1
    assert client.limiter.circuit_retry_after() is None  # not counted as invalid


async def test_invalid_responses_open_the_circuit(fake: FakeDiscord, fclock: FakeClock) -> None:
    settings = make_settings(discord_invalid_request_budget=10)
    client = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    fake.add("GET", r".*/channels/\d+", fake.json({"code": 50013}, 403), times=None)
    for _ in range(10):
        with pytest.raises(DiscordMcpError):
            await client.get(CHANNEL)
    sent = len(fake.requests)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "rate_limited"
    assert len(fake.requests) == sent


async def test_429_without_body_uses_retry_after_header(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings = make_settings(discord_max_rate_limit_wait_seconds=5)
    client = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    fake.add(
        "GET",
        r".*/channels/\d+",
        lambda _r: httpx.Response(429, content=b"", headers={"retry-after": "9"}),
        times=None,
    )
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.retry_after == 9.0


async def test_429_with_no_retry_hint_defaults(fake: FakeDiscord, fclock: FakeClock) -> None:
    settings = make_settings(discord_max_429_retries=0)
    client = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    fake.add("GET", r".*/channels/\d+", lambda _r: httpx.Response(429, content=b""), times=None)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.retry_after == 1.0


async def test_index_pending_carries_retry_after_and_documents(
    client: DiscordClient, fake: FakeDiscord
) -> None:
    fake.add(
        "GET",
        r".*/channels/\d+",
        fake.json(
            {
                "code": 110000,
                "message": "Index not yet available",
                "documents_indexed": 5,
                "retry_after": 2.5,
            },
            202,
        ),
        times=None,
    )
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "index_pending"
    assert info.value.retry_after == 2.5
    assert "documents_indexed=5" in info.value.message
    assert len(fake.requests) == 1  # the client never retries 202/110000 itself


async def test_redirect_status_range_is_detected(client: DiscordClient, fake: FakeDiscord) -> None:
    fake.add("GET", r".*/channels/\d+", lambda _r: httpx.Response(301), times=1)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "upstream"


async def test_other_2xx_codes_are_success(client: DiscordClient, fake: FakeDiscord) -> None:
    fake.add("GET", r".*/channels/\d+", fake.json({"ok": True}, 202), times=1)
    assert (await client.get(CHANNEL)).body == {"ok": True}


async def test_unknown_error_family_is_upstream(client: DiscordClient, fake: FakeDiscord) -> None:
    fake.add("GET", r".*/channels/\d+", fake.json({}, 418), times=None)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(CHANNEL)
    assert info.value.kind == "upstream"


async def test_probe_ok_reads_bot_id_and_flags(client: DiscordClient) -> None:
    probe = await client.probe()
    assert probe.outcome == "ok"
    assert probe.bot_user_id == BOT
    assert probe.flags == 278528
    assert probe.application_id == "222222222222222222"


async def test_probe_auth_is_reported(fake: FakeDiscord, client: DiscordClient) -> None:
    fake.add("GET", r".*/users/@me", fake.json({"code": 0}, 401), times=1)
    probe = await client.probe()
    assert probe.outcome == "auth"
    assert client.auth_failed


async def test_probe_unreachable_after_retries(
    fake: FakeDiscord, fclock: FakeClock, settings: Settings
) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    fake.add("GET", r".*/users/@me", refuse, times=None)
    client = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    probe = await client.probe()
    assert probe.outcome == "unreachable"
    assert fclock.sleeps.count(1.0) >= 1 and fclock.sleeps.count(4.0) == 1


async def test_probe_unusable_flags_are_ok_with_unknown_flags(
    fake: FakeDiscord, client: DiscordClient
) -> None:
    fake.add("GET", r".*/applications/@me", fake.json({"id": "1", "flags": "nope"}), times=None)
    probe = await client.probe()
    assert probe.outcome == "ok" and probe.flags is None and probe.flags_source is None


async def test_probe_application_auth_and_missing_body(
    fake: FakeDiscord, client: DiscordClient
) -> None:
    fake.add("GET", r".*/applications/@me", fake.json({"code": 0}, 401), times=1)
    assert (await client.probe()).outcome == "auth"


async def test_probe_user_without_id_is_unreachable(
    fake: FakeDiscord, client: DiscordClient
) -> None:
    fake.add("GET", r".*/users/@me", fake.json({"username": "x"}), times=None)
    assert (await client.probe()).outcome == "unreachable"


async def test_probe_application_without_id_is_still_ok(
    fake: FakeDiscord, client: DiscordClient
) -> None:
    fake.add("GET", r".*/applications/@me", fake.json({"flags": 0}), times=None)
    probe = await client.probe()
    assert probe.outcome == "ok" and probe.application_id is None and probe.flags == 0


def test_no_network_library_is_imported_outside_discord_client() -> None:
    """R16: only discord_client.py imports httpx (or any other network client)."""
    network = {"httpx", "requests", "urllib3", "aiohttp", "socket", "http.client"}
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path.name == "discord_client.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in network or name in network:
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []


def test_the_only_module_importing_httpx_is_discord_client() -> None:
    importers = [
        p.name
        for p in SRC.rglob("*.py")
        if re.search(r"^\s*import httpx|^\s*from httpx", p.read_text(encoding="utf-8"), re.M)
    ]
    assert importers == ["discord_client.py"]
