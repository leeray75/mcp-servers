"""MCP surface of the player tools: schema, validation before any upstream call, messages."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from sports_mcp.config import Settings
from sports_mcp.server import create_server
from sports_mcp.tools import SportsService
from tests.conftest import FakeEspn

PLAYER_TOOLS = {"find_player", "get_player_stats", "get_player_gamelog"}


def text_of(result: Any) -> str:
    return str(result.content[0].text)


async def test_player_tool_schemas_carry_the_constraints(
    settings: Settings, service: SportsService
) -> None:
    async with Client(create_server(settings, service)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) >= PLAYER_TOOLS
    find_props = tools["find_player"].input_schema["properties"]
    assert find_props["limit"]["maximum"] == 10 and find_props["limit"]["minimum"] == 1
    gamelog_props = tools["get_player_gamelog"].input_schema["properties"]
    assert gamelog_props["season"]["pattern"] == "^(current|all|[0-9]{4})$"
    assert set(gamelog_props["season_type"]["enum"]) == {"regular", "post", "all"}
    description = tools["get_player_stats"].description or ""
    assert "not instructions" in description
    for name in PLAYER_TOOLS:
        annotations = tools[name].annotations
        assert annotations is not None and annotations.read_only_hint is True


async def test_player_tools_end_to_end(settings: Settings, service: SportsService) -> None:
    calls: list[tuple[str, dict[str, Any]]] = [
        ("find_player", {"league": "nfl", "query": "skattebo"}),
        ("get_player_stats", {"league": "nfl", "player": "4696981"}),
        ("get_player_gamelog", {"league": "nfl", "player": "skattebo", "season": "2025"}),
    ]
    async with Client(create_server(settings, service)) as client:
        for name, args in calls:
            result = await client.call_tool(name, args)
            assert result.is_error is False, (name, text_of(result))
            assert text_of(result) and result.structured_content, name


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("find_player", {"league": "nfl", "query": ""}),
        ("find_player", {"league": "nfl", "query": "../etc/passwd"}),
        ("find_player", {"league": "nfl", "query": "x" * 61}),
        ("find_player", {"league": "nfl", "query": "a_b"}),
        ("find_player", {"league": "nfl", "query": "a", "limit": 0}),
        ("find_player", {"league": "nfl", "query": "a", "limit": 11}),
        ("get_player_stats", {"league": "nfl", "player": "../x"}),
        ("get_player_stats", {"league": "nfl", "player": "x" * 61}),
        ("get_player_stats", {"league": "nfl", "player": "4696981", "season_type": "pre"}),
        ("get_player_gamelog", {"league": "nfl", "player": "4696981", "season": "20x5"}),
        ("get_player_gamelog", {"league": "nfl", "player": "4696981", "season": "1989"}),
        ("get_player_gamelog", {"league": "nfl", "player": "4696981", "season_type": "x"}),
        ("get_player_gamelog", {"league": "nfl", "player": "a?b=1"}),
        ("get_player_gamelog", {"league": "nope", "player": "4696981"}),
    ],
)
async def test_invalid_player_input_is_rejected_before_any_upstream_call(
    settings: Settings, service: SportsService, espn: FakeEspn, tool: str, args: dict[str, Any]
) -> None:
    async with Client(create_server(settings, service)) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error is True
    assert espn.requests == []


async def test_unknown_athlete_error_does_not_echo_the_id(
    settings: Settings, service: SportsService, espn: FakeEspn
) -> None:
    espn.overrides["/apis/common/v3/sports/football/nfl/athletes/99999999"] = 404
    async with Client(create_server(settings, service)) as client:
        result = await client.call_tool(
            "get_player_gamelog", {"league": "nfl", "player": "99999999", "season": "2025"}
        )
    assert result.is_error is True
    assert text_of(result).endswith("Athlete not found for league nfl")
    assert "99999999" not in text_of(result)
