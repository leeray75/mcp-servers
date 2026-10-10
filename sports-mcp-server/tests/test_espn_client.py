from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

from sports_mcp.config import Settings
from sports_mcp.errors import NotFoundError, UpstreamError
from sports_mcp.espn_client import EspnClient
from sports_mcp.leagues import resolve_league
from tests.conftest import FAKE_AUTH_TOKEN, FakeEspn, _no_sleep

NFL = resolve_league("nfl")
SRC = Path(__file__).resolve().parents[1] / "src" / "sports_mcp"


def make(espn: FakeEspn, **env: object) -> EspnClient:
    settings = Settings(mcp_auth_token=FAKE_AUTH_TOKEN, **env)  # type: ignore[arg-type]
    return EspnClient(settings, transport=espn.transport(), sleep=_no_sleep)


def test_url_builders(client: EspnClient) -> None:
    base = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
    assert client.scoreboard_url(NFL, date="20261004") == f"{base}/scoreboard?dates=20261004"
    assert client.scoreboard_url(NFL, week=3, season_type=2, limit=5) == (
        f"{base}/scoreboard?limit=5&seasontype=2&week=3"
    )
    assert client.scoreboard_url(NFL, week=3, date="20261004").endswith("?week=3")  # week wins
    ncaam = resolve_league("ncaam")
    assert client.scoreboard_url(ncaam, date="20260221").endswith(
        "scoreboard?dates=20260221&groups=50"
    )
    assert client.summary_url(NFL, "401872965") == f"{base}/summary?event=401872965"
    assert client.teams_url(NFL) == f"{base}/teams?limit=1000"
    assert client.team_url(NFL, "2") == f"{base}/teams/2"
    assert client.schedule_url(NFL, "2") == f"{base}/teams/2/schedule"
    assert client.schedule_url(NFL, "2", 2025).endswith("/schedule?season=2025")
    assert client.standings_url(NFL, 2025) == (
        "https://site.api.espn.com/apis/v2/sports/football/nfl/standings?level=3&season=2025"
    )
    assert client.standings_url(resolve_league("nba")).endswith("/basketball/nba/standings")
    assert client.news_url(NFL, 7) == f"{base}/news?limit=7"


@pytest.mark.parametrize("bad", ["", "abc", "12 3", "../1", "1?x=2", "1\n", "9" * 21, "-1"])
def test_ids_must_be_digits(client: EspnClient, bad: str) -> None:
    # ids come from tool-level validation or ESPN itself; a bad one is "unexpected response"
    with pytest.raises(UpstreamError, match="unexpected response"):
        client.summary_url(NFL, bad)
    with pytest.raises(UpstreamError, match="unexpected response"):
        client.schedule_url(NFL, bad)


async def test_only_the_allow_listed_host_is_ever_contacted(
    client: EspnClient, espn: FakeEspn
) -> None:
    with pytest.raises(UpstreamError):
        await client.get_json("https://evil.example.com/apis/site/v2/sports/football/nfl/news")
    with pytest.raises(UpstreamError):
        await client.get_json("https://site.api.espn.com.evil.example/x")
    with pytest.raises(UpstreamError):
        client.build_url("@evil.example.com/x")
    assert espn.requests == []
    await client.get_json(client.news_url(NFL, 5))
    assert {r.url.host for r in espn.requests} == {"site.api.espn.com"}


async def test_successful_fetch_sends_user_agent_and_no_credentials(
    client: EspnClient, espn: FakeEspn
) -> None:
    data = await client.get_json(client.news_url(NFL, 5))
    assert "articles" in data
    request = espn.requests[0]
    assert request.headers["user-agent"].startswith("sports-mcp/")
    assert "authorization" not in request.headers and "cookie" not in request.headers


@pytest.mark.parametrize("status", [400, 404])
async def test_id_endpoints_map_400_404_to_not_found(
    espn: FakeEspn, client: EspnClient, status: int
) -> None:
    espn.default_status = status
    with pytest.raises(NotFoundError) as info:
        await client.get_json(client.summary_url(NFL, "1"), id_endpoint=True)
    assert "SECRET-BODY" not in str(info.value)


async def test_404_on_other_endpoints_is_a_plain_http_error(
    espn: FakeEspn, client: EspnClient
) -> None:
    espn.default_status = 404
    with pytest.raises(UpstreamError, match="HTTP 404"):
        await client.get_json(client.news_url(NFL, 5))


async def test_429_message_and_no_retry(espn: FakeEspn, client: EspnClient) -> None:
    espn.default_status = 429
    with pytest.raises(UpstreamError, match="rate-limited"):
        await client.get_json(client.news_url(NFL, 5))
    assert len(espn.requests) == 1


async def test_5xx_is_retried_once_then_reported_without_body(
    espn: FakeEspn, client: EspnClient
) -> None:
    espn.default_status = 503
    with pytest.raises(UpstreamError) as info:
        await client.get_json(client.news_url(NFL, 5))
    assert str(info.value) == "ESPN is unavailable"
    assert len(espn.requests) == 2


async def test_retry_recovers_from_a_transient_failure(espn: FakeEspn, client: EspnClient) -> None:
    url = client.news_url(NFL, 5)
    key = FakeEspn.key(httpx.Request("GET", url))
    espn.overrides[key] = httpx.ConnectError("boom")
    original = espn.handler
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 2:
            espn.overrides.pop(key)
        return original(request)

    fixed = EspnClient(
        Settings(mcp_auth_token=FAKE_AUTH_TOKEN),  # type: ignore[arg-type]
        transport=httpx.MockTransport(flaky),
        sleep=_no_sleep,
    )
    assert "articles" in await fixed.get_json(url)
    assert calls["n"] == 2


@pytest.mark.parametrize("exc", [httpx.ConnectError("x"), httpx.ReadTimeout("x")])
async def test_connect_errors_and_timeouts_retry_then_fail(
    espn: FakeEspn, client: EspnClient, exc: Exception
) -> None:
    url = client.news_url(NFL, 5)
    espn.overrides[FakeEspn.key(httpx.Request("GET", url))] = exc
    with pytest.raises(UpstreamError, match="unavailable"):
        await client.get_json(url)
    assert len(espn.requests) == 2


async def test_other_transport_errors_are_not_retried(espn: FakeEspn, client: EspnClient) -> None:
    url = client.news_url(NFL, 5)
    espn.overrides[FakeEspn.key(httpx.Request("GET", url))] = httpx.DecodingError("bad gzip")
    with pytest.raises(UpstreamError, match="unavailable"):
        await client.get_json(url)
    assert len(espn.requests) == 1


async def test_other_status_codes(espn: FakeEspn, client: EspnClient) -> None:
    espn.default_status = 418
    with pytest.raises(UpstreamError, match="HTTP 418"):
        await client.get_json(client.news_url(NFL, 5))


async def test_redirects_are_not_followed(espn: FakeEspn) -> None:
    def redirect(request: httpx.Request) -> httpx.Response:
        espn.requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example.com/x"})

    c = EspnClient(
        Settings(mcp_auth_token=FAKE_AUTH_TOKEN),  # type: ignore[arg-type]
        transport=httpx.MockTransport(redirect),
        sleep=_no_sleep,
    )
    with pytest.raises(UpstreamError, match="HTTP 302"):
        await c.get_json(c.news_url(NFL, 5))
    assert [r.url.host for r in espn.requests] == ["site.api.espn.com"]


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b"", b'"str"'])
async def test_invalid_json_or_wrong_shape(espn: FakeEspn, client: EspnClient, body: bytes) -> None:
    espn.raw_body = body
    with pytest.raises(UpstreamError, match="unexpected response"):
        await client.get_json(client.news_url(NFL, 5))


async def test_oversize_body_is_refused_while_streaming(espn: FakeEspn) -> None:
    big = json.dumps({"x": "a" * 2_000_000}).encode()
    espn.raw_body = big
    c = make(espn, espn_max_response_bytes=1_048_576)
    with pytest.raises(UpstreamError, match="unexpected response"):
        await c.get_json(c.news_url(NFL, 5))


async def test_oversize_content_length_is_refused_up_front() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{}", headers={"Content-Length": "99999999"})

    c = EspnClient(
        Settings(mcp_auth_token=FAKE_AUTH_TOKEN),  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )
    # httpx itself rejects a lying Content-Length; either way the client reports one error.
    with pytest.raises((UpstreamError, httpx.HTTPError)):
        await c.get_json(c.news_url(NFL, 5))


async def test_concurrency_is_limited(espn: FakeEspn) -> None:
    running = {"now": 0, "max": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        return httpx.Response(200, json={"ok": True})

    c = EspnClient(
        Settings(mcp_auth_token=FAKE_AUTH_TOKEN, espn_max_concurrency=2),  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )
    await asyncio.gather(*(c.get_json(c.news_url(NFL, i + 1)) for i in range(8)))
    assert running["max"] == 2


async def test_aclose(client: EspnClient) -> None:
    await client.aclose()


def test_only_the_espn_client_imports_a_network_library() -> None:
    network_import = re.compile(
        r"^\s*(?:import|from)\s+(?:httpx|requests|aiohttp|socket|http\.client|urllib\.request)\b",
        re.MULTILINE,
    )
    offenders = [
        p.name
        for p in SRC.rglob("*.py")
        if p.name != "espn_client.py" and network_import.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
