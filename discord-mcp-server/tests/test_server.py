"""R5-R11, R15, R30, R36-R38: the MCP surface, boot outcomes, /healthz and HTTP hardening."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from mcp import Client
from starlette.applications import Starlette

from discord_mcp.budget import ToolBudget
from discord_mcp.capabilities import ALL_TOOL_NAMES, TOOL_SPECS, ToolPlan, plan_tools
from discord_mcp.config import Settings
from discord_mcp.discord_client import BootProbe, DiscordClient
from discord_mcp.server import DiscordServer, _register_tools, create_app
from discord_mcp.service import DiscordService
from tests.conftest import (
    ALICE,
    BOT,
    FAKE_AUTH_TOKEN,
    FAKE_BOT_TOKEN,
    GUILD,
    MESSAGE,
    OTHER_CHANNEL,
    OTHER_GUILD,
    REPORTS,
    TEXT,
    THREAD_B,
    VOICE,
    FakeClock,
    FakeDiscord,
    make_settings,
)

HOST = "127.0.0.1:8769"
MCP_HEADERS = {
    "Authorization": f"Bearer {FAKE_AUTH_TOKEN}",
    "Host": HOST,
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def text_of(result: Any) -> str:
    return str(result.content[0].text)


async def boot_server(
    settings: Settings,
    fake: FakeDiscord,
    fclock: FakeClock,
    *,
    budget: ToolBudget | None = None,
) -> tuple[DiscordServer, DiscordService, ToolPlan]:
    """Boot the service and register tools exactly as the lifespan does."""
    server = DiscordServer("discord-mcp", version="0.1.0")
    client = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    svc = DiscordService(settings, client, now=fclock.wall, sleep=fclock.sleep)
    await svc.boot()
    plan = plan_tools(settings, svc.probe)
    _register_tools(
        server,
        svc,
        budget or ToolBudget(per_minute=600, per_tool_per_minute=600, clock=fclock.clock),
        plan,
    )
    fake.requests.clear()  # the boot probe is not part of the test's requests
    return server, svc, plan


# ------------------------------------------------------------------ registry (R8-R11)


async def test_registered_tools_are_exact_names_in_fixed_order(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    async with Client(server) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]
    assert names == list(ALL_TOOL_NAMES)
    assert names == [spec.name for spec in TOOL_SPECS]
    assert len(names) == 14


async def test_tools_list_is_identical_across_requests(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    async with Client(server) as client:
        first = (await client.list_tools()).model_dump_json()
        second = (await client.list_tools()).model_dump_json()
    assert first == second


async def test_every_tool_has_title_annotations_schema_and_output_schema(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    for tool in tools:
        assert tool.title, tool.name
        assert tool.input_schema.get("additionalProperties") is False, tool.name
        assert tool.output_schema is not None and tool.output_schema.get("type") == "object"
        assert "meta" in tool.output_schema["properties"], tool.name
        ann = tool.annotations
        assert ann is not None
        assert ann.read_only_hint is True and ann.destructive_hint is False
        assert ann.idempotent_hint is True and ann.open_world_hint is True
        assert "untrusted third-party text" in (tool.description or "")


async def test_instructions_carry_the_untrusted_content_note(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    async with running_app(fake, fclock) as app:
        instructions = app.state.discord_server.instructions or ""
    assert "untrusted third-party text" in instructions


async def test_success_has_structured_content_and_identical_text_block(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    async with Client(server) as client:
        result = await client.call_tool("get_guild", {"guild_id": GUILD})
    assert result.is_error is not True
    assert json.loads(text_of(result)) == result.structured_content
    assert result.structured_content["name"] == "Example Guild"


async def test_error_result_has_the_r30_shape(fake: FakeDiscord, fclock: FakeClock) -> None:
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    async with Client(server) as client:
        result = await client.call_tool("get_guild", {"guild_id": OTHER_GUILD})
    assert result.is_error is True
    payload = result.structured_content
    assert set(payload["error"]) == {
        "kind",
        "message",
        "code",
        "http_status",
        "retryable",
        "retry_after",
        "hint",
    }
    assert payload["error"]["kind"] == "forbidden_guild"
    assert text_of(result).startswith("forbidden_guild: Guild not in this server's allowlist Hint:")


# ------------------------------------------------------------------ argument and protocol faults


async def test_unexpected_argument_is_a_validation_error_before_any_request(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    async with Client(server) as client:
        result = await client.call_tool("get_guild", {"guild_id": GUILD, "bogus": 1})
    assert result.is_error is True
    assert result.structured_content["error"]["kind"] == "validation"
    assert "bogus" in result.structured_content["error"]["message"]
    assert fake.requests == []


async def test_schema_violation_names_the_field_and_sends_nothing(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    async with Client(server) as client:
        result = await client.call_tool("get_guild", {"guild_id": "not-a-snowflake"})
    error = result.structured_content["error"]
    assert error["kind"] == "validation"
    assert "guild_id" in error["message"]
    assert "not-a-snowflake" not in error["message"]
    assert fake.requests == []


async def test_unknown_tool_is_a_jsonrpc_protocol_error(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    async with Client(server) as client:
        with pytest.raises(Exception, match="Unknown tool"):
            await client.call_tool("send_message", {})


async def test_unexpected_exception_is_generic_jsonrpc_error_without_detail(
    fake: FakeDiscord, fclock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, svc, _plan = await boot_server(make_settings(), fake, fclock)

    async def boom(_guild_id: str) -> Any:
        raise RuntimeError("SECRET-INTERNAL-DETAIL")

    monkeypatch.setattr(svc, "get_guild", boom)
    async with Client(server) as client:
        with pytest.raises(Exception) as info:
            await client.call_tool("get_guild", {"guild_id": GUILD})
    assert "SECRET-INTERNAL-DETAIL" not in str(info.value)
    assert "Internal server error" in str(info.value)


async def test_budget_refusal_is_rate_limited_and_sends_nothing(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    budget = ToolBudget(per_minute=600, per_tool_per_minute=1, clock=fclock.clock)
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock, budget=budget)
    async with Client(server) as client:
        first = await client.call_tool("get_guild", {"guild_id": GUILD})
        second = await client.call_tool("get_guild", {"guild_id": GUILD})
    assert first.is_error is not True
    assert second.is_error is True
    error = second.structured_content["error"]
    assert error["kind"] == "rate_limited" and error["retryable"] is True
    assert error["retry_after"] and error["retry_after"] > 0
    assert len(fake.requests) == 1  # the refused call sent no Discord request


# ------------------------------------------------------------------ guard across every tool (R15)

GUARD_CASES: dict[str, dict[str, Any]] = {
    "list_guilds": {},
    "get_guild": {"guild_id": OTHER_GUILD},
    "list_channels": {"guild_id": OTHER_GUILD},
    "get_channel": {"channel_id": OTHER_CHANNEL},
    "list_messages": {"channel_id": OTHER_CHANNEL},
    "get_message": {"channel_id": OTHER_CHANNEL, "message_id": MESSAGE},
    "search_messages": {"guild_id": OTHER_GUILD},
    "list_reactions": {"channel_id": OTHER_CHANNEL, "message_id": MESSAGE, "emoji": "✅"},
    "list_pins": {"channel_id": OTHER_CHANNEL},
    "list_threads": {"guild_id": OTHER_GUILD},
    "list_members": {"guild_id": OTHER_GUILD},
    "get_member": {"guild_id": OTHER_GUILD, "user_id": ALICE},
    "search_members": {"guild_id": OTHER_GUILD, "query": "al"},
    "get_audit_log": {"guild_id": OTHER_GUILD},
}


CHANNEL_TOOLS: frozenset[str] = frozenset(
    {"get_channel", "list_messages", "get_message", "list_reactions", "list_pins"}
)


def test_guard_cases_cover_every_registered_tool() -> None:
    assert set(GUARD_CASES) == set(ALL_TOOL_NAMES)


@pytest.mark.parametrize("tool", [name for name in GUARD_CASES if name != "list_guilds"])
async def test_non_allowlisted_scope_is_refused_with_no_guild_request(
    tool: str, fake: FakeDiscord, fclock: FakeClock
) -> None:
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    async with Client(server) as client:
        result = await client.call_tool(tool, GUARD_CASES[tool])
    assert result.is_error is True
    assert result.structured_content["error"]["kind"] == "forbidden_guild"
    # A1-R15: guild tools send nothing; channel tools send exactly one channel lookup.
    expected = [f"/api/v10/channels/{OTHER_CHANNEL}"] if tool in CHANNEL_TOOLS else []
    assert [r.url.path for r in fake.requests] == expected


async def test_search_channel_scope_outside_the_allowlist_is_refused(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    async with Client(server) as client:
        result = await client.call_tool(
            "search_messages", {"guild_id": GUILD, "channel_ids": [OTHER_CHANNEL]}
        )
    assert result.structured_content["error"]["kind"] == "forbidden_guild"
    assert all(r.url.path == f"/api/v10/channels/{OTHER_CHANNEL}" for r in fake.requests)


# ------------------------------------------------------------------ boot outcomes (R4-R7)


async def test_boot_auth_keeps_every_tool_and_each_returns_kind_auth(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    fake.add("GET", r".*/users/@me", fake.json({"code": 0}, 401), times=1)
    server, _svc, plan = await boot_server(make_settings(), fake, fclock)
    assert plan.omitted == ()
    async with Client(server) as client:
        result = await client.call_tool("list_channels", {"guild_id": GUILD})
    assert result.structured_content["error"]["kind"] == "auth"
    assert result.structured_content["error"]["retryable"] is False


async def test_boot_unreachable_omits_only_intent_tools(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    fake.add("GET", r".*/users/@me", refuse, times=None)
    _server, _svc, plan = await boot_server(make_settings(), fake, fclock)
    assert dict(plan.omitted) == {"search_messages": "probe_failed", "list_members": "probe_failed"}


@pytest.mark.parametrize(
    ("flags", "omitted"),
    [
        (278528, {}),
        (0, {"search_messages": "intent_disabled", "list_members": "intent_disabled"}),
        (1 << 18, {"list_members": "intent_disabled"}),
        (1 << 14, {"search_messages": "intent_disabled"}),
    ],
)
async def test_flag_matrix_decides_registration(
    fake: FakeDiscord, fclock: FakeClock, flags: int, omitted: dict[str, str]
) -> None:
    fake.application["flags"] = flags
    server, _svc, plan = await boot_server(make_settings(), fake, fclock)
    assert dict(plan.omitted) == omitted
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == set(ALL_TOOL_NAMES) - set(omitted)


async def test_config_force_disable_removes_intent_tools(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings = make_settings(discord_enable_search=False, discord_enable_member_list=False)
    server, _svc, _plan = await boot_server(settings, fake, fclock)
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert "search_messages" not in names and "list_members" not in names


async def test_list_guilds_has_no_id_and_never_returns_other_guilds(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    """list_guilds takes no id (R14): the guard filters its output to the allowlist."""
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    async with Client(server) as client:
        result = await client.call_tool("list_guilds", {})
    assert result.is_error is not True
    returned = [g["id"] for g in result.structured_content["guilds"]]
    assert returned == [GUILD]
    assert OTHER_GUILD not in returned


async def test_boot_log_lists_registered_and_omitted_without_ids(
    fake: FakeDiscord, fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    fake.application["flags"] = 0
    with caplog.at_level(logging.INFO):
        async with running_app(fake, fclock):
            pass
    lines = [r.getMessage() for r in caplog.records if "registered" in r.getMessage()]
    assert lines and "search_messages=intent_disabled" in lines[0]
    assert GUILD not in lines[0] and BOT not in lines[0]


# ------------------------------------------------------------------ channels and messages end to end


async def test_list_messages_end_to_end_with_permission_error(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    async with Client(server) as client:
        ok = await client.call_tool("list_messages", {"channel_id": TEXT, "limit": 2})
        denied = await client.call_tool("list_messages", {"channel_id": REPORTS})
        voice = await client.call_tool("list_messages", {"channel_id": VOICE})
        thread = await client.call_tool("list_messages", {"channel_id": THREAD_B})
    assert ok.structured_content["count"] == 2
    assert denied.structured_content["error"]["kind"] == "permission"
    assert "CONNECT" in voice.structured_content["error"]["message"]
    assert "READ_MESSAGE_HISTORY" in thread.structured_content["error"]["message"]


async def test_budget_is_global_across_tools(fake: FakeDiscord, fclock: FakeClock) -> None:
    budget = ToolBudget(per_minute=1, per_tool_per_minute=600, clock=fclock.clock)
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock, budget=budget)
    async with Client(server) as client:
        await client.call_tool("get_guild", {"guild_id": GUILD})
        refused = await client.call_tool("list_channels", {"guild_id": GUILD})
    assert refused.structured_content["error"]["kind"] == "rate_limited"


# ------------------------------------------------------------------ HTTP app (R5, R38)


@asynccontextmanager
async def running_app(
    fake: FakeDiscord, fclock: FakeClock, settings: Settings | None = None
) -> AsyncIterator[Starlette]:
    app = create_app(
        settings or make_settings(),
        transport=fake.transport(),
        sleep=fclock.sleep,
        now=fclock.wall,
    )
    async with app.router.lifespan_context(app):
        yield app


def http_client(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8769"
    )


async def test_healthz_is_open_and_reports_ok(fake: FakeDiscord, fclock: FakeClock) -> None:
    async with running_app(fake, fclock) as app, http_client(app) as http:
        response = await http.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_healthz_degraded_on_auth(fake: FakeDiscord, fclock: FakeClock) -> None:
    fake.add("GET", r".*/users/@me", fake.json({"code": 0}, 401), times=1)
    async with running_app(fake, fclock) as app, http_client(app) as http:
        response = await http.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "reason": "discord_auth"}


async def test_healthz_degraded_when_unreachable_at_boot_and_leaks_nothing(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    fake.add("GET", r".*/users/@me", refuse, times=None)
    async with running_app(fake, fclock) as app, http_client(app) as http:
        response = await http.get("/healthz")
    assert response.json() == {"status": "degraded", "reason": "discord_unreachable_at_boot"}
    assert BOT not in response.text and GUILD not in response.text


async def test_mcp_requires_bearer_token(fake: FakeDiscord, fclock: FakeClock) -> None:
    async with running_app(fake, fclock) as app, http_client(app) as http:
        response = await http.post("/mcp", json={}, headers={"Host": HOST})
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


async def test_host_header_must_be_loopback(fake: FakeDiscord, fclock: FakeClock) -> None:
    async with running_app(fake, fclock) as app, http_client(app) as http:
        response = await http.post(
            "/mcp", json={}, headers={**MCP_HEADERS, "Host": "evil.example.com:8769"}
        )
    assert response.status_code == 421


async def test_origin_must_be_loopback(fake: FakeDiscord, fclock: FakeClock) -> None:
    async with running_app(fake, fclock) as app, http_client(app) as http:
        response = await http.post(
            "/mcp", json={}, headers={**MCP_HEADERS, "Origin": "http://evil.example.com"}
        )
    assert response.status_code == 403


async def test_request_body_is_limited_to_64_kib(fake: FakeDiscord, fclock: FakeClock) -> None:
    async with running_app(fake, fclock) as app, http_client(app) as http:
        response = await http.post("/mcp", content=b"x" * (70 * 1024), headers=MCP_HEADERS)
    assert response.status_code == 413


async def test_authorised_tools_list_over_http(fake: FakeDiscord, fclock: FakeClock) -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    async with running_app(fake, fclock) as app, http_client(app) as http:
        response = await http.post("/mcp", json=payload, headers=MCP_HEADERS)
    assert response.status_code == 200
    body = response.json()
    names = [t["name"] for t in body["result"]["tools"]]
    assert names == list(ALL_TOOL_NAMES)


async def test_app_state_exposes_the_registered_server(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    async with running_app(fake, fclock) as app:
        assert isinstance(app.state.discord_server, DiscordServer)
        assert isinstance(app.state.discord_service, DiscordService)


def test_create_app_requires_no_probe_before_serving() -> None:
    app = create_app(make_settings(), transport=None)
    assert isinstance(app, Starlette)


def test_bot_token_must_never_appear_in_settings_repr() -> None:
    settings = make_settings()
    assert FAKE_BOT_TOKEN not in repr(settings)
    assert FAKE_AUTH_TOKEN not in repr(settings)


def test_boot_probe_dataclass_defaults() -> None:
    assert BootProbe("unreachable").bot_user_id is None


async def test_unreachable_boot_logs_the_reason_once(
    fake: FakeDiscord, fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    fake.add("GET", r".*/users/@me", refuse, times=None)
    with caplog.at_level(logging.WARNING):
        async with running_app(fake, fclock) as app:
            assert app.state.discord_service.probe_outcome == "unreachable"
    reasons = [r for r in caplog.records if r.getMessage() == "discord_unreachable_at_boot"]
    assert len(reasons) == 1


DM_CHANNEL: str = "303030303030303030"
UNKNOWN_CHANNEL: str = "123123123123123123"


@pytest.mark.parametrize("tool", sorted(CHANNEL_TOOLS))
@pytest.mark.parametrize("channel", [DM_CHANNEL, UNKNOWN_CHANNEL])
async def test_dm_and_unknown_channels_are_refused_identically_for_every_channel_tool(
    tool: str, channel: str, fake: FakeDiscord, fclock: FakeClock
) -> None:
    """A1-R13 and A1-R15 for every channel-scoped tool: one identical refusal, one lookup."""
    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    args = {**GUARD_CASES[tool], "channel_id": channel}
    async with Client(server) as client:
        result = await client.call_tool(tool, args)
    error = result.structured_content["error"]
    assert error["kind"] == "forbidden_guild"
    assert error["message"] == "Channel is not in an allowlisted guild"
    assert [r.url.path for r in fake.requests] == [f"/api/v10/channels/{channel}"]
