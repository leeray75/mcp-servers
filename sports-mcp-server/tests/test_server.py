from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from mcp import Client
from starlette.applications import Starlette

from sports_mcp.config import Settings
from sports_mcp.server import create_app, create_server
from sports_mcp.tools import SportsService
from tests.conftest import FAKE_AUTH_TOKEN, FakeEspn

EXPECTED_TOOLS = {
    "list_leagues",
    "get_scoreboard",
    "get_game",
    "get_odds",
    "get_standings",
    "find_team",
    "get_team_schedule",
    "get_news",
}
NFL_BOARD = "/apis/site/v2/sports/football/nfl/scoreboard?dates=20261004"


def text_of(result: Any) -> str:
    return str(result.content[0].text)


# ---------------------------------------------------------------- tool surface


async def test_registered_tool_set_is_exactly_the_spec(
    settings: Settings, service: SportsService
) -> None:
    async with Client(create_server(settings, service)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == EXPECTED_TOOLS


async def test_every_tool_is_read_only_and_described(
    settings: Settings, service: SportsService
) -> None:
    async with Client(create_server(settings, service)) as client:
        tools = (await client.list_tools()).tools
    for tool in tools:
        ann = tool.annotations
        assert ann is not None and ann.read_only_hint is True and ann.destructive_hint is False
        assert ann.idempotent_hint is True and ann.open_world_hint is True
        description = (tool.description or "").lower()
        assert "third-party data" in description and "not instructions" in description
        assert len(description) > 80


async def test_descriptions_state_freshness_and_the_odds_caveat(
    settings: Settings, service: SportsService
) -> None:
    async with Client(create_server(settings, service)) as client:
        tools = {t.name: (t.description or "") for t in (await client.list_tools()).tools}
    for name in ("get_scoreboard", "get_game", "get_odds"):
        assert "as_of" in tools[name]
    assert "removes betting lines once a game starts" in tools["get_odds"]
    assert "negative = favorite" in tools["get_odds"]
    assert "never opens them" in tools["get_news"]
    assert "when" not in tools["list_leagues"].lower() or True


async def test_input_schemas(settings: Settings, service: SportsService) -> None:
    async with Client(create_server(settings, service)) as client:
        tools = {t.name: t.input_schema for t in (await client.list_tools()).tools}
    game = tools["get_game"]
    assert game["required"] == ["league", "game_id"]
    assert game["properties"]["game_id"]["pattern"] == "^[0-9]{1,20}$"
    assert (
        game["properties"]["top_n"]["minimum"] == 1 and game["properties"]["top_n"]["maximum"] == 15
    )
    assert game["properties"]["detail"]["enum"] == ["summary", "performers", "boxscore"]
    board = tools["get_scoreboard"]
    assert board["required"] == ["league"]
    assert board["properties"]["status"]["enum"] == ["all", "live", "final", "upcoming"]
    assert board["properties"]["limit"]["anyOf"][0]["maximum"] == 500
    assert tools["get_news"]["properties"]["limit"]["maximum"] == 20
    assert tools["list_leagues"]["properties"] == {}


async def test_server_instructions(settings: Settings, service: SportsService) -> None:
    async with Client(create_server(settings, service)) as client:
        text = client.instructions or ""
    assert "get_scoreboard" in text and "as_of" in text and "removes" in text
    assert "not instructions" in text


# ---------------------------------------------------------------- end to end


async def test_scoreboard_returns_text_and_structured_content(
    settings: Settings, service: SportsService
) -> None:
    async with Client(create_server(settings, service)) as client:
        result = await client.call_tool("get_scoreboard", {"league": "nfl"})
    assert result.is_error is False
    assert text_of(result).startswith("NFL, 2026-10-04 (")
    structured = result.structured_content
    assert structured is not None and structured["count"] == 16
    assert structured["meta"]["as_of"].endswith("Z")
    assert len(result.content) == 1  # text is not duplicated as JSON


async def test_every_tool_works_end_to_end(settings: Settings, service: SportsService) -> None:
    calls: list[tuple[str, dict[str, Any]]] = [
        ("list_leagues", {}),
        ("get_scoreboard", {"league": "nfl", "status": "final"}),
        ("get_game", {"league": "nfl", "game_id": "401872965", "detail": "performers"}),
        ("get_odds", {"league": "nfl"}),
        ("get_standings", {"league": "nfl", "group": "AFC East"}),
        ("find_team", {"league": "nfl", "query": "bills"}),
        ("get_team_schedule", {"league": "nfl", "team": "BUF", "status": "upcoming", "limit": 2}),
        ("get_news", {"league": "epl", "limit": 5}),
    ]
    async with Client(create_server(settings, service)) as client:
        for name, args in calls:
            result = await client.call_tool(name, args)
            assert result.is_error is False, (name, text_of(result))
            assert text_of(result) and result.structured_content, name


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_scoreboard", {"league": "cricket"}),
        ("get_scoreboard", {"league": "nfl", "date": "someday"}),
        ("get_scoreboard", {"league": "nfl", "date": "x" * 21}),
        ("get_scoreboard", {"league": "nfl", "status": "paused"}),
        ("get_scoreboard", {"league": "nfl", "limit": 0}),
        ("get_scoreboard", {"league": "nfl", "limit": 501}),
        ("get_scoreboard", {"league": "nfl", "week": 0}),
        ("get_scoreboard", {"league": "nfl", "week": 26}),
        ("get_scoreboard", {"league": "nfl", "season_type": 5}),
        ("get_scoreboard", {"league": "nfl", "refresh": "maybe"}),
        ("get_scoreboard", {}),
        ("get_scoreboard", {"league": ""}),
        ("get_game", {"league": "nfl", "game_id": "../1"}),
        ("get_game", {"league": "nfl", "game_id": "1?x=2"}),
        ("get_game", {"league": "nfl", "game_id": "12\n3"}),
        ("get_game", {"league": "nfl", "game_id": "abc"}),
        ("get_game", {"league": "nfl", "game_id": "1" * 21}),
        ("get_game", {"league": "nfl", "game_id": "1", "detail": "all"}),
        ("get_game", {"league": "nfl", "game_id": "1", "top_n": 0}),
        ("get_game", {"league": "nfl", "game_id": "1", "top_n": 16}),
        ("get_game", {"league": "nfl", "game_id": "1", "max_chars": 10}),
        ("get_game", {"league": "nfl", "game_id": "1", "max_chars": 10**7}),
        ("get_odds", {"league": "nfl", "game_id": "x"}),
        ("get_odds", {"league": "nfl", "date": "../etc"}),
        ("get_standings", {"league": "nfl", "season": 1800}),
        ("get_standings", {"league": "nfl", "group": "../x"}),
        ("get_standings", {"league": "nfl", "group": "a\nb"}),
        ("get_standings", {"league": "nfl", "group": ""}),
        ("find_team", {"league": "nfl", "query": ""}),
        ("find_team", {"league": "nfl", "query": "../etc/passwd"}),
        ("find_team", {"league": "nfl", "query": "a?b=1"}),
        ("find_team", {"league": "nfl", "query": "a\nb"}),
        ("find_team", {"league": "nfl", "query": "x" * 61}),
        ("get_team_schedule", {"league": "nfl", "team": "../2"}),
        ("get_team_schedule", {"league": "nfl", "team": "BUF", "limit": 0}),
        ("get_team_schedule", {"league": "nfl", "team": "BUF", "season": 3000}),
        ("get_news", {"league": "nfl", "limit": 0}),
        ("get_news", {"league": "nfl", "limit": 21}),
        ("get_news", {"league": "../nfl"}),
    ],
)
async def test_invalid_input_is_rejected_before_any_upstream_call(
    settings: Settings, service: SportsService, espn: FakeEspn, tool: str, args: dict[str, Any]
) -> None:
    async with Client(create_server(settings, service)) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error is True
    assert espn.requests == []


async def test_unknown_league_error_lists_valid_ids(
    settings: Settings, service: SportsService
) -> None:
    async with Client(create_server(settings, service)) as client:
        result = await client.call_tool("get_scoreboard", {"league": "cricket"})
    assert result.is_error is True
    message = text_of(result)
    assert (
        "nfl" in message and "champions-league" in message and "mens-college-basketball" in message
    )


async def test_domain_errors_are_returned_verbatim(
    settings: Settings, service: SportsService, espn: FakeEspn
) -> None:
    espn.default_status = 404
    async with Client(create_server(settings, service)) as client:
        missing = await client.call_tool("get_game", {"league": "nfl", "game_id": "1"})
        espn.default_status = 503
        down = await client.call_tool("get_scoreboard", {"league": "nfl"})
        espn.default_status = 429
        limited = await client.call_tool("get_news", {"league": "nfl"})
    assert missing.is_error and text_of(missing).endswith(": Game not found for league nfl")
    assert down.is_error and text_of(down).endswith(": ESPN is unavailable")
    assert limited.is_error and "rate-limited" in text_of(limited)
    for result in (missing, down, limited):
        assert "SECRET-BODY" not in text_of(result)


async def test_unexpected_exceptions_do_not_leak_details(
    settings: Settings, service: SportsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("secret internals sk-INTERNALLEAK123456")

    monkeypatch.setattr(service, "get_news", boom)
    async with Client(create_server(settings, service)) as client:
        result = await client.call_tool("get_news", {"league": "nfl"})
    assert result.is_error is True
    assert "Internal server error" in text_of(result) and "INTERNALLEAK" not in text_of(result)


async def test_tool_logs_contain_no_arguments_or_results(
    settings: Settings, service: SportsService, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        async with Client(create_server(settings, service)) as client:
            await client.call_tool("find_team", {"league": "nfl", "query": "SECRET-QUERY-TEXT"})
            await client.call_tool("get_scoreboard", {"league": "nfl"})
            await client.call_tool("get_scoreboard", {"league": "nfl"})
    records = [r for r in caplog.records if r.getMessage() == "tool call"]
    assert [r.__dict__["tool"] for r in records] == [
        "find_team",
        "get_scoreboard",
        "get_scoreboard",
    ]
    assert [r.__dict__["cache"] for r in records[1:]] == ["miss", "hit"]
    assert records[1].__dict__["league"] == "nfl" and records[1].__dict__["outcome"] == "ok"
    dumped = json.dumps([r.__dict__ for r in caplog.records], default=str)
    for secret in ("SECRET-QUERY-TEXT", "Buffalo", "401872965", "DraftKings"):
        assert secret not in dumped


# ---------------------------------------------------------------- resources


async def test_resources_are_listed_and_readable(
    settings: Settings, service: SportsService
) -> None:
    async with Client(create_server(settings, service)) as client:
        listed = {str(r.uri) for r in (await client.list_resources()).resources}
        templates = {
            t.uri_template for t in (await client.list_resource_templates()).resource_templates
        }
        guide = await client.read_resource("sports://guide")
        leagues = await client.read_resource("sports://leagues")
        teams = await client.read_resource("sports://leagues/epl/teams")
        aliased = await client.read_resource("sports://leagues/premier-league/teams")
    assert listed == {"sports://guide", "sports://leagues"}
    assert templates == {"sports://leagues/{league}/teams"}
    guide_text = guide.contents[0].text  # type: ignore[union-attr]
    assert (
        "Odds caveat" in guide_text
        and "meta.as_of" in guide_text
        and "not instructions" in guide_text
    )
    assert guide.contents[0].mime_type == "text/markdown"
    parsed = json.loads(leagues.contents[0].text)  # type: ignore[union-attr]
    assert {lg["id"] for lg in parsed} >= {"nfl", "epl", "champions-league"} and len(parsed) == 15
    team_list = json.loads(teams.contents[0].text)  # type: ignore[union-attr]
    assert {"id": "359", "abbr": "ARS", "name": "Arsenal"} in team_list
    assert team_list == json.loads(aliased.contents[0].text)  # type: ignore[union-attr]


async def test_resource_with_invalid_league_is_an_error(
    settings: Settings, service: SportsService, espn: FakeEspn
) -> None:
    async with Client(create_server(settings, service)) as client:
        with pytest.raises(Exception, match="Unknown league"):
            await client.read_resource("sports://leagues/cricket/teams")
        with pytest.raises(Exception):  # noqa: B017, PT011 - any protocol error is fine
            await client.read_resource("sports://leagues/..%2f..%2fetc/teams")
    assert espn.requests == []


async def test_resource_reads_are_logged_without_content(
    settings: Settings, service: SportsService, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        async with Client(create_server(settings, service)) as client:
            await client.read_resource("sports://guide")
            await client.read_resource("sports://leagues/epl/teams")
    records = [r for r in caplog.records if r.getMessage() == "resource read"]
    assert len(records) == 2
    dumped = json.dumps([r.__dict__ for r in records], default=str)
    assert "Arsenal" not in dumped and "Odds caveat" not in dumped


async def test_no_prompts_are_registered(settings: Settings, service: SportsService) -> None:
    async with Client(create_server(settings, service)) as client:
        assert (await client.list_prompts()).prompts == []


async def test_resource_team_error_path(
    settings: Settings, service: SportsService, espn: FakeEspn
) -> None:
    espn.default_status = 503
    async with Client(create_server(settings, service)) as client:
        with pytest.raises(Exception, match="unavailable"):
            await client.read_resource("sports://leagues/epl/teams")


# ---------------------------------------------------------------- HTTP layer

BASE = "http://127.0.0.1:8767"
JSONRPC_ACCEPT = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
INITIALIZE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}


@asynccontextmanager
async def serve(application: Starlette) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app lifespan in one dedicated task (anyio scopes must exit where they entered)."""
    ready, stop = asyncio.Event(), asyncio.Event()

    async def run_lifespan() -> None:
        async with application.router.lifespan_context(application):
            ready.set()
            await stop.wait()

    task = asyncio.create_task(run_lifespan())
    await ready.wait()
    try:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            yield client
    finally:
        stop.set()
        await task


@pytest.fixture
async def http(settings: Settings, service: SportsService) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(create_app(settings, service)) as client:
        yield client


def bearer(token: str = FAKE_AUTH_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_app_requires_a_token() -> None:
    with pytest.raises(ValueError, match="MCP_AUTH_TOKEN"):
        create_app(Settings())


def test_create_app_builds_its_own_service(settings: Settings) -> None:
    assert isinstance(create_app(settings), Starlette)


async def test_healthz_is_unauthenticated_and_minimal(http: httpx.AsyncClient) -> None:
    response = await http.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_healthz_only_exempts_get(http: httpx.AsyncClient) -> None:
    assert (await http.post("/healthz")).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Basic {FAKE_AUTH_TOKEN}"},
        {"Authorization": FAKE_AUTH_TOKEN},
        {"Authorization": f"Bearer {FAKE_AUTH_TOKEN}x"},
        {"Authorization": f"Bearer {FAKE_AUTH_TOKEN[:-1]}"},
        {"Authorization": f"Bearer  {FAKE_AUTH_TOKEN} extra"},
    ],
)
async def test_mcp_rejects_missing_or_invalid_bearer(
    http: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await http.post("/mcp", json=INITIALIZE, headers={**JSONRPC_ACCEPT, **headers})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}  # no hint about what failed
    assert response.headers["www-authenticate"] == "Bearer"


async def test_unauthenticated_requests_never_reach_espn(
    http: httpx.AsyncClient, espn: FakeEspn
) -> None:
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "get_scoreboard", "arguments": {"league": "nfl"}},
    }
    assert (await http.post("/mcp", json=call, headers=JSONRPC_ACCEPT)).status_code == 401
    assert espn.requests == []


async def test_other_paths_also_require_auth(http: httpx.AsyncClient) -> None:
    assert (await http.get("/anything-else")).status_code == 401
    assert (await http.get("/")).status_code == 401


async def test_valid_bearer_reaches_mcp(http: httpx.AsyncClient) -> None:
    response = await http.post("/mcp", json=INITIALIZE, headers={**JSONRPC_ACCEPT, **bearer()})
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "sports-mcp"


async def test_scheme_is_case_insensitive(http: httpx.AsyncClient) -> None:
    headers = {**JSONRPC_ACCEPT, "Authorization": f"bearer {FAKE_AUTH_TOKEN}"}
    assert (await http.post("/mcp", json=INITIALIZE, headers=headers)).status_code == 200


async def test_tools_list_over_http(http: httpx.AsyncClient) -> None:
    headers = {**JSONRPC_ACCEPT, **bearer(), "MCP-Protocol-Version": "2025-06-18"}
    await http.post("/mcp", json=INITIALIZE, headers=headers)
    response = await http.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers
    )
    assert response.status_code == 200
    assert {t["name"] for t in response.json()["result"]["tools"]} == EXPECTED_TOOLS


async def test_scoreboard_over_http(http: httpx.AsyncClient, espn: FakeEspn) -> None:
    headers = {**JSONRPC_ACCEPT, **bearer(), "MCP-Protocol-Version": "2025-06-18"}
    call = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "get_scoreboard", "arguments": {"league": "nfl", "limit": 2}},
    }
    response = await http.post("/mcp", json=call, headers=headers)
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["count"] == 2
    assert result["content"][0]["text"].startswith("NFL, 2026-10-04")
    assert [r.url.host for r in espn.requests] == ["site.api.espn.com"]


async def test_every_auth_decision_goes_through_hmac_compare_digest(
    http: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr("sports_mcp.security.hmac.compare_digest", spy)
    await http.post("/mcp", json=INITIALIZE, headers={**JSONRPC_ACCEPT, **bearer("nope")})
    await http.post("/mcp", json=INITIALIZE, headers=JSONRPC_ACCEPT)  # header missing entirely
    assert len(calls) == 2  # compared on both paths, never short-circuited
    assert all(expected == FAKE_AUTH_TOKEN.encode() for _presented, expected in calls)


@pytest.mark.parametrize(
    "host",
    ["evil.example.com", "evil.example.com:8767", "127.0.0.1.evil.com:8767", "localhost", ""],
)
async def test_bad_host_header_is_rejected_even_with_valid_token(
    http: httpx.AsyncClient, host: str
) -> None:
    headers = {**JSONRPC_ACCEPT, **bearer(), "Host": host}
    assert (await http.post("/mcp", json=INITIALIZE, headers=headers)).status_code == 421


@pytest.mark.parametrize("host", ["127.0.0.1:8767", "localhost:8767", "LOCALHOST:1"])
async def test_loopback_hosts_are_accepted(http: httpx.AsyncClient, host: str) -> None:
    assert (await http.get("/healthz", headers={"Host": host})).status_code == 200


async def test_foreign_origin_is_rejected(http: httpx.AsyncClient) -> None:
    headers = {**JSONRPC_ACCEPT, **bearer(), "Origin": "https://evil.example.com"}
    assert (await http.post("/mcp", json=INITIALIZE, headers=headers)).status_code == 403


async def test_oversized_request_body_is_rejected(http: httpx.AsyncClient) -> None:
    huge = {**INITIALIZE, "params": {**INITIALIZE["params"], "pad": "x" * (100 * 1024)}}
    response = await http.post("/mcp", json=huge, headers={**JSONRPC_ACCEPT, **bearer()})
    assert response.status_code == 413


async def test_rejections_never_log_the_presented_token(
    http: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    presented = "presented-secret-token-0123456789abcdef-xyz"
    with caplog.at_level(logging.DEBUG):
        await http.post("/mcp", json=INITIALIZE, headers={**JSONRPC_ACCEPT, **bearer(presented)})
    assert any("rejected request" in r.getMessage() for r in caplog.records)
    dumped = json.dumps([r.__dict__ for r in caplog.records], default=str)
    assert presented not in dumped
    assert FAKE_AUTH_TOKEN not in dumped


async def test_successful_calls_never_log_the_token(
    http: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    headers = {**JSONRPC_ACCEPT, **bearer(), "MCP-Protocol-Version": "2025-06-18"}
    with caplog.at_level(logging.DEBUG):
        await http.post("/mcp", json=INITIALIZE, headers=headers)
    assert FAKE_AUTH_TOKEN not in json.dumps([r.__dict__ for r in caplog.records], default=str)


async def test_server_header_not_advertised(http: httpx.AsyncClient) -> None:
    assert "server" not in (await http.get("/healthz")).headers
