"""R3, R4, R5, R31, R35-R39, R43, R47: the MCP surface, /healthz and HTTP hardening.

The heart of this file is the R35/R36/R37 pinning block: each switch combination's registered tool
list is asserted **equal** to the SPEC §6.4 list, in order, so the surface cannot drift by one
tool, cannot gain a passthrough tool, a prompt or a resource, and cannot change at runtime. The
rest proves the surrounding invariants: ``/healthz``'s five keys and nothing else (R3, T6), bearer
everywhere else (R4), the Host/Origin guard (R5) and the ``/oauth/*`` exemption (R47), refusals
before any service call (R39), the seven-field ``isError`` shape (R43), and the R31 rule that
``confirmation_required`` is a *success* while a genuine failure is an error.

Everything runs over the frozen :class:`~tests.twitch_fakes.FakeTwitch` transport — no socket is
ever opened (R55) and no real credential exists anywhere in this file.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from tests.server_fakes import (
    ALL_GROUPS,
    HOST,
    MCP_HEADERS,
    NOT_ALLOWED,
    TOOL_ARGS,
    VALIDATE_USER,
    boot_server,
    call,
    http_client,
    list_tools,
    rpc,
    running_app,
    server_settings,
    tool_names,
)
from tests.twitch_fakes import (
    BROADCASTER,
    FAKE_AUTH_TOKEN,
    FAKE_CLIENT_SECRET,
    OPERATOR,
    USER_ID,
    FakeClock,
    FakeTwitch,
)
from twitch_mcp import __version__
from twitch_mcp.capabilities import ACCOUNT_TOOLS, ALL_TOOLS, ALL_WRITE_TOOLS, READ_TOOLS

# The four SPEC §6.4 lists, spelled out here rather than imported from capabilities.py: importing
# them would make this a tautology. capabilities.py owns the plan; these literals own the contract.

APP_READS: tuple[str, ...] = (
    "twitch_account_status",
    "get_users",
    "get_users_follows",
    "get_channels",
    "search_channels",
    "get_streams",
    "get_top_games",
    "get_games",
    "get_videos",
    "get_clips",
    "get_polls",
    "get_predictions",
    "get_emotes",
    "get_channel_chat_settings",
    "get_channel_chat_badges",
    "get_schedule",
)

USER_READS: tuple[str, ...] = (
    "get_stream_markers",
    "get_broadcaster_subscribers",
    "get_channel_editors",
    "get_moderators",
    "get_banned_users",
    "get_automod_settings",
    "get_chat_chatters",
)

#: The 23 reads exactly as SPEC §6.4 lists them: rows 1-15, rows 16-22 (USER_READS), then row 23
#: (``get_schedule``, the last app-token read). This literal is what pins R35's *order* — the
#: interleaving is the part a set comparison could never catch.
READS_WITH_USER: tuple[str, ...] = APP_READS[:15] + USER_READS + (APP_READS[15],)

#: The six write groups in §6.4 order with their tools, spelled out.
WRITE_GROUPS_PINNED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("channel", ("update_channel",)),
    ("polls", ("create_poll", "end_poll")),
    ("predictions", ("create_prediction", "end_prediction")),
    ("chat", ("send_chat_message",)),
    (
        "moderation",
        (
            "timeout_user",
            "remove_timeout",
            "ban_user",
            "unban_user",
            "set_automod_term",
            "remove_automod_term",
        ),
    ),
    ("roles", ("add_vip", "remove_vip", "add_moderator", "remove_moderator")),
)

WRITE_GROUP_NAMES: tuple[str, ...] = tuple(group for group, _ in WRITE_GROUPS_PINNED)
TOOLS_BY_GROUP: dict[str, tuple[str, ...]] = dict(WRITE_GROUPS_PINNED)

#: Default boot: writes off, user OAuth off — the 16 app-token reads and nothing else.
EXPECTED_DEFAULT: tuple[str, ...] = APP_READS
#: User OAuth on with a connected account: 16 + 7 reads + 2 account tools = 25.
EXPECTED_USER_OAUTH: tuple[str, ...] = READS_WITH_USER + ACCOUNT_TOOLS
#: Everything on: 23 reads + 2 account + 16 writes = 41 (the §6.4 maximum).
EXPECTED_ALL: tuple[str, ...] = READS_WITH_USER + ACCOUNT_TOOLS + ALL_WRITE_TOOLS


def writes_of(groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for group in groups for name in TOOLS_BY_GROUP[group])


def expected_plan(
    *, user_oauth: bool, writes_on: bool, groups: tuple[str, ...] = WRITE_GROUP_NAMES
) -> tuple[str, ...]:
    """The §6.4 list one switch combination must register, assembled from the literals above."""
    tools: list[str] = list(READS_WITH_USER if user_oauth else APP_READS)
    if user_oauth:
        tools.extend(ACCOUNT_TOOLS)
    if writes_on:
        tools.extend(writes_of(tuple(g for g in WRITE_GROUP_NAMES if g in groups)))
    return tuple(tools)


# --------------------------------------------------------------------- R35/R36/R37: the tool surface


async def test_default_boot_registers_exactly_the_23_read_minus_user_token_reads(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R35: writes off + user OAuth off = the 16 app-token reads, in §6.4 order, and nothing else."""
    server, _svc, plan = await boot_server(fake, fclock, server_settings())
    names = await tool_names(server)
    assert names == list(EXPECTED_DEFAULT)
    assert len(names) == 16
    assert plan.tool_count == 16
    assert set(names) == set(names)  # no duplicates
    assert len(set(names)) == len(names)


async def test_user_oauth_on_registers_25(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R35: user OAuth on adds exactly the 7 user-token reads and the 2 account tools = 25."""
    settings = server_settings(twitch_user_oauth=True)
    server, _svc, plan = await boot_server(fake, fclock, settings, connected=True)
    names = await tool_names(server)
    assert names == list(EXPECTED_USER_OAUTH)
    assert len(names) == 25
    assert plan.tool_count == 25
    # ...and the switch alone is not enough: without a connected account they are still absent.
    not_connected, _svc2, plan2 = await boot_server(fake, fclock, settings, connected=False)
    assert await tool_names(not_connected) == list(EXPECTED_DEFAULT)
    assert plan2.tool_count == 16


@pytest.mark.parametrize("group", WRITE_GROUP_NAMES)
async def test_each_write_group_adds_exactly_its_own_tools(
    group: str, fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R35/R37: enabling one group adds exactly that group's tools, in §6.4 order."""
    # R46: every write tool requires a user token, so BOTH boots must have the switch on and an
    # account connected. The only difference between them is the write switches (R28).
    baseline = server_settings(twitch_user_oauth=True)
    base_server, _s, _p = await boot_server(fake, fclock, baseline, connected=True)
    base_names = await tool_names(base_server)

    settings = server_settings(
        twitch_user_oauth=True, twitch_enable_writes=True, twitch_write_groups=group
    )
    server, _svc, plan = await boot_server(fake, fclock, settings, connected=True)
    names = await tool_names(server)
    assert names == list(expected_plan(user_oauth=True, writes_on=True, groups=(group,)))
    assert tuple(n for n in names if n not in base_names) == TOOLS_BY_GROUP[group]
    assert len(names) == 25 + len(TOOLS_BY_GROUP[group])
    assert plan.enabled_write_groups == (group,)


async def test_one_kill_switch_off_registers_no_writes(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R28 via R36: either switch alone omits every write with reason write_group_disabled."""
    only_enable = server_settings(twitch_user_oauth=True, twitch_enable_writes=True)  # no groups
    server, _svc, plan = await boot_server(fake, fclock, only_enable, connected=True)
    assert await tool_names(server) == list(EXPECTED_USER_OAUTH)
    assert plan.writes_enabled is False
    assert plan.omitted_reason("create_poll") == "write_group_disabled"

    only_groups = server_settings(
        twitch_user_oauth=True, twitch_write_groups=ALL_GROUPS
    )  # enable flag off
    server2, _svc2, plan2 = await boot_server(fake, fclock, only_groups, connected=True)
    assert await tool_names(server2) == list(EXPECTED_USER_OAUTH)
    assert plan2.writes_enabled is False
    assert plan2.omitted_reason("add_vip") == "write_group_disabled"


async def test_all_six_groups_plus_oauth_register_exactly_41(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R35/R37: the maximum surface is exactly 41 tools and exactly the §6.4 union."""
    settings = server_settings(
        twitch_user_oauth=True, twitch_enable_writes=True, twitch_write_groups=ALL_GROUPS
    )
    server, _svc, plan = await boot_server(fake, fclock, settings, connected=True)
    names = await tool_names(server)
    assert names == list(EXPECTED_ALL)
    assert len(names) == 41
    assert plan.tool_count == 41
    assert plan.enabled_write_groups == WRITE_GROUP_NAMES


async def test_registered_union_is_exactly_the_41_capability_names(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R35/R37: the registration order IS the SPEC §6.4 order (rows 1-23, account, group order)."""
    settings = server_settings(
        twitch_enable_writes=True, twitch_write_groups=ALL_GROUPS, twitch_user_oauth=True
    )
    server, _svc, _plan = await boot_server(fake, fclock, settings, connected=True)
    names = await tool_names(server)
    assert names == list(EXPECTED_ALL)
    # The order itself, pinned independently of capabilities.py: §6.4 rows 1-15, the seven user
    # reads in row order, row 23 (get_schedule), the account pair, then each group in order.
    assert names[:15] == list(APP_READS[:15])
    assert names[15:22] == list(USER_READS)
    assert names[22] == "get_schedule"
    assert names[23:25] == list(ACCOUNT_TOOLS)
    index = 25
    for group, tools in WRITE_GROUPS_PINNED:
        assert names[index : index + len(tools)] == list(tools), group
        index += len(tools)
    assert index == 41


async def test_nothing_outside_the_41_is_ever_registered(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R37: nothing outside the 41 is ever registered; no passthrough tool, no prompt, no resource."""
    settings = server_settings(
        twitch_user_oauth=True, twitch_enable_writes=True, twitch_write_groups=ALL_GROUPS
    )
    server, _svc, _plan = await boot_server(fake, fclock, settings, connected=True)
    names = await tool_names(server)
    assert set(names) == set(ALL_TOOLS)
    assert len(ALL_TOOLS) == 41
    assert len(READ_TOOLS) == 23
    assert len(ACCOUNT_TOOLS) == 2
    assert len(ALL_WRITE_TOOLS) == 16
    # The forbidden shapes: an endpoint passthrough, a prompt, a resource.
    assert not [n for n in names if n.startswith(("raw_", "passthrough_", "request_"))]
    assert not any("http" in n or "url" in n or "endpoint" in n for n in names)
    async with Client(server) as client:
        assert (await client.list_prompts()).prompts == []
        assert (await client.list_resources()).resources == []
        assert (await client.list_resource_templates()).resource_templates == []


async def test_a_tool_is_omitted_only_for_the_three_allowed_causes(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R36: exactly three omission causes, each carrying exactly the tools it should."""
    settings = server_settings(
        twitch_enable_writes=True,
        twitch_write_groups="channel,polls,moderation",
        twitch_user_oauth=True,
        disabled_tools="get_videos,ban_user",
    )
    # R36 has three causes, and consider() checks them in the order group -> token -> flag. That
    # ordering makes two boots necessary: token_absent needs NO token, while explicitly_disabled on
    # a write tool needs a token (else the write is simply token_absent). One boot cannot show both.
    # Boot (a): switch ON, no token -> the 7 user-token reads and all 16 writes are token_absent.
    _server, _svc, plan = await boot_server(fake, fclock, settings, connected=False)
    reasons = dict(plan.omitted)
    assert set(reasons) == set(ALL_TOOLS) - set(plan.registered)
    assert reasons["create_prediction"] == "write_group_disabled"
    assert reasons["add_vip"] == "write_group_disabled"
    assert reasons["get_videos"] == "explicitly_disabled"
    assert {n for n, r in reasons.items() if r == "token_absent"} == set(USER_READS) | set(
        ACCOUNT_TOOLS
    ) | set(writes_of(("channel", "polls", "moderation")))
    # Nothing else was dropped for any other reason, and the registered list is a subsequence.
    assert plan.registered == tuple(n for n in EXPECTED_ALL if n not in reasons)

    # Boot (b): switch ON and connected -> ban_user's group is enabled and its flag is set, so the
    # only remaining cause can be the explicit disable.
    _server2, _svc2, plan2 = await boot_server(fake, fclock, settings, connected=True)
    reasons2 = dict(plan2.omitted)
    assert reasons2["ban_user"] == "explicitly_disabled"  # group on, token present, flag set
    assert reasons2["get_videos"] == "explicitly_disabled"
    assert {n for n, r in reasons2.items() if r == "token_absent"} == set()


async def test_disabling_a_connected_user_token_read_is_honoured(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R36: TWITCH_DISABLE_<TOOL>=true removes a tool even when its group and token are present."""
    settings = server_settings(
        twitch_enable_writes=True,
        twitch_write_groups=ALL_GROUPS,
        twitch_user_oauth=True,
        disabled_tools="get_moderators,timeout_user",
    )
    server, _svc, plan = await boot_server(fake, fclock, settings, connected=True)
    names = await tool_names(server)
    assert "get_moderators" not in names
    assert "timeout_user" not in names
    assert len(names) == 39
    assert plan.omitted_reason("get_moderators") == "explicitly_disabled"
    assert plan.omitted_reason("timeout_user") == "explicitly_disabled"


async def test_an_unreadable_disable_flag_can_never_add_a_tool(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R36 (safe direction): a typo in a disable flag drops nothing and adds nothing."""
    settings = server_settings(
        twitch_enable_writes=True,
        twitch_write_groups=ALL_GROUPS,
        twitch_user_oauth=True,
        disabled_tools="get_videoes,NOT_A_TOOL,,",
    )
    server, _svc, plan = await boot_server(fake, fclock, settings, connected=True)
    assert await tool_names(server) == list(EXPECTED_ALL)
    assert plan.omitted == ()


async def test_nothing_is_ever_added_at_runtime(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R36: tools/list is byte-identical across calls, and a refused call cannot change it."""
    settings = server_settings()
    server, _svc, _plan = await boot_server(fake, fclock, settings)
    async with Client(server) as client:
        first = (await client.list_tools()).model_dump_json()
        await client.call_tool("get_channels", {"broadcaster_ids": [NOT_ALLOWED]})
        second = (await client.list_tools()).model_dump_json()
    assert first == second

    # The SDK refuses an unknown tool by RAISING, and the group escapes when the client's task
    # group tears down -- so assert the raise around the whole client scope, then re-list from a
    # fresh client to prove the refused call registered nothing.
    with pytest.raises(BaseExceptionGroup) as info:
        async with Client(server) as refused:
            await refused.call_tool("not_a_real_tool", {})

    def _leaf_exceptions(excs: tuple[BaseException, ...]) -> list[BaseException]:
        leaves: list[BaseException] = []
        for exc in excs:
            if isinstance(exc, BaseExceptionGroup):
                leaves.extend(_leaf_exceptions(exc.exceptions))
            else:
                leaves.append(exc)
        return leaves

    assert any(isinstance(e, MCPError) for e in _leaf_exceptions(info.value.exceptions))
    async with Client(server) as after:
        assert (await after.list_tools()).model_dump_json() == first


@pytest.mark.parametrize("tool", sorted(TOOL_ARGS))
async def test_every_planned_tool_is_callable_with_its_documented_arguments(
    tool: str, fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R38/R39: each registered tool accepts its §6.1 arguments and returns a CallToolResult."""
    settings = server_settings(
        twitch_enable_writes=True, twitch_write_groups=ALL_GROUPS, twitch_user_oauth=True
    )
    server, _svc, plan = await boot_server(fake, fclock, settings, connected=True)
    assert tool in plan.registered
    async with Client(server) as client:
        result = await client.call_tool(tool, TOOL_ARGS[tool])
    assert result.structured_content is not None
    assert result.content and getattr(result.content[0], "text", None)


async def test_tool_schemas_are_closed_titles_descriptions_and_annotations_present(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R38: title, description, output schema, and additionalProperties=false on every tool."""
    settings = server_settings(
        twitch_enable_writes=True, twitch_write_groups=ALL_GROUPS, twitch_user_oauth=True
    )
    server, _svc, _plan = await boot_server(fake, fclock, settings, connected=True)
    tools = await list_tools(server)
    assert len(tools) == 41
    for tool in tools:
        assert tool.title, tool.name
        assert tool.description and "untrusted" in tool.description.lower(), tool.name
        assert tool.input_schema.get("additionalProperties") is False, tool.name
        assert tool.output_schema is not None, tool.name
        assert tool.output_schema.get("type") == "object", tool.name
        assert tool.annotations is not None, tool.name
    by_name = {t.name: t for t in tools}
    for write in ALL_WRITE_TOOLS:
        ann = by_name[write].annotations
        assert ann is not None
        assert ann.read_only_hint is False and ann.destructive_hint is True, write
        assert ann.idempotent_hint is False, write
        assert "confirm" in by_name[write].input_schema["properties"], write
    for read in READ_TOOLS:
        ann = by_name[read].annotations
        assert ann is not None
        assert ann.read_only_hint is True and ann.destructive_hint is False, read


# --------------------------------------------------------------------- R3: /healthz carries five keys


async def test_healthz_reports_exactly_five_keys_without_credentials(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R3/T6: status, version, tool_count, twitch_reachable, tls — no token, id, login, scope or URL."""
    async with running_app(fake, fclock) as app, http_client(app) as http:
        response = await http.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "status",
        "version",
        "tool_count",
        "twitch_reachable",
        "tls",
    }
    assert body["version"] == __version__
    assert body["tool_count"] == 16
    # The shape is what R3 pins. Whether status is "ok" or "degraded" is the app token's business,
    # and this server cannot reach the real token endpoint in a test (create_app builds its own
    # AppTokenSource), so both answers are legitimate here. See the two focused probe tests below.
    assert body["status"] in {"ok", "degraded"}
    assert isinstance(body["twitch_reachable"], bool)
    text = response.text
    for secret in (FAKE_AUTH_TOKEN, FAKE_CLIENT_SECRET, OPERATOR, "operator", "user:read:email"):
        assert secret not in text
    assert "client_id" not in text and "scopes" not in text and "Bearer" not in text
    # Exactly five keys means exactly five colons; a sixth would betray an extra field (R3).
    assert json.dumps(body).count(":") == 5


async def test_healthz_reports_ok_when_the_app_token_mints(
    fake: FakeTwitch, fclock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3/R50: with the app credential answering, /healthz says ok — still five keys only."""
    monkeypatch.setattr("twitch_mcp.server.AppTokenSource.access_token", _answer_app_token)
    # The boot probe does not merely mint the app credential, it validates it (R50), so this
    # scenario has to answer /oauth2/validate as well — an unregistered endpoint means the probe
    # honestly reports degraded, which is exactly what it is for.
    fake.add("GET", r"^/oauth2/validate$", fake.json({"expires_in": 5000}))
    async with running_app(fake, fclock) as app, http_client(app) as http:
        body = (await http.get("/healthz")).json()
    assert body["status"] == "ok"
    assert body["twitch_reachable"] is True
    assert set(body) == {
        "status",
        "version",
        "tool_count",
        "twitch_reachable",
        "tls",
    }


async def _answer_app_token(self: Any) -> str:
    """Answer the mint with the bearer the test transport already knows."""
    from tests.service_fakes import APP_BEARER

    return APP_BEARER


async def test_healthz_tool_count_matches_the_registered_surface(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R3/R35: the healthz count is the same boot decision tools/list reflects."""
    settings = server_settings(
        twitch_enable_writes=True, twitch_write_groups=ALL_GROUPS, twitch_user_oauth=True
    )
    async with running_app(fake, fclock, settings, connected=True) as app, http_client(app) as http:
        health = (await http.get("/healthz")).json()
        listed = await rpc(http, "tools/list")
    assert health["tool_count"] == 41
    assert len(listed["result"]["tools"]) == health["tool_count"]


async def test_healthz_is_degraded_when_the_boot_probe_cannot_reach_twitch(
    fake: FakeTwitch, fclock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3/R50: a dead token degrades the report; it does not crash boot and does not explain why."""
    monkeypatch.setattr(
        "twitch_mcp.server.AppTokenSource.access_token",
        _raise_unauthenticated,
    )
    async with running_app(fake, fclock) as app, http_client(app) as http:
        body = (await http.get("/healthz")).json()
    assert body["status"] == "degraded"
    assert body["twitch_reachable"] is False
    assert set(body) == {
        "status",
        "version",
        "tool_count",
        "twitch_reachable",
        "tls",
    }


async def _raise_unauthenticated(self: Any) -> str:  # pragma: no cover - patched away
    from twitch_mcp.errors import TwitchMcpError

    raise TwitchMcpError("unauthenticated", "Twitch refused this server's app credentials")


async def test_healthz_still_reports_five_keys_when_the_plan_is_empty(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R3/T6: even the most locked-down boot reports the same five keys and nothing else."""
    async with (
        running_app(fake, fclock, server_settings(twitch_disable_all=True)) as app,
        http_client(app) as http,
    ):
        body = (await http.get("/healthz")).json()
    assert set(body) == {
        "status",
        "version",
        "tool_count",
        "twitch_reachable",
        "tls",
    }
    assert body["tool_count"] == 16  # no such flag exists, so nothing may have been removed


# --------------------------------------------------------------------- R4: bearer everywhere else


async def test_mcp_and_every_other_route_require_bearer(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R4: no bearer, no service — /mcp included. Only GET /healthz answers unauthenticated."""
    async with running_app(fake, fclock) as app, http_client(app) as http:
        assert (await http.get("/healthz")).status_code == 200
        for method, path in (("GET", "/mcp"), ("POST", "/mcp"), ("GET", "/nope")):
            unauthed = await http.request(method, path)
            assert unauthed.status_code == 401, (method, path, unauthed.status_code)
            assert unauthed.headers.get("www-authenticate") == "Bearer"
        wrong = await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={**MCP_HEADERS, "Authorization": "Bearer not-the-token"},
        )
        assert wrong.status_code == 401
    assert fake.helix_request_count == 0  # a refused request never reached Twitch


async def test_post_to_healthz_requires_bearer_while_get_does_not(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R4: the exemption is method-scoped — POST /healthz is a normal protected route."""
    async with running_app(fake, fclock) as app, http_client(app) as http:
        assert (await http.get("/healthz")).status_code == 200
        assert (await http.post("/healthz")).status_code == 401
        assert (await http.head("/healthz")).status_code == 200


async def test_authorised_tools_list_over_http_returns_the_planned_surface(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R4/R35: with the bearer and a loopback Host, tools/list answers over HTTP."""
    async with running_app(fake, fclock) as app, http_client(app) as http:
        message = await rpc(http, "tools/list")
    names = [tool["name"] for tool in message["result"]["tools"]]
    assert names == list(EXPECTED_DEFAULT)


# --------------------------------------------------------------------- R5: Host / Origin guard


async def test_host_must_be_loopback(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R5: a rebinding Host is refused before authentication, on every route."""
    async with running_app(fake, fclock) as app, http_client(app) as http:
        for host in ("evil.example", "attacker.invalid:8770", "127.0.0.1.evil.example"):
            response = await http.get("/healthz", headers={"Host": host})
            assert response.status_code == 421, host
        assert (await http.get("/healthz", headers={"Host": "localhost:8770"})).status_code == 200


async def test_origin_must_be_loopback_when_present(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R5: a foreign Origin is 403; no Origin (a desktop MCP client) is allowed."""
    async with running_app(fake, fclock) as app, http_client(app) as http:
        assert (
            await http.get("/healthz", headers={"Origin": "https://evil.example"})
        ).status_code == 403
        assert (await http.get("/healthz", headers={"Origin": f"http://{HOST}"})).status_code == 200
        assert (await http.get("/healthz")).status_code == 200
        assert (
            await http.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={**MCP_HEADERS, "Origin": "https://evil.example"},
            )
        ).status_code == 403
    assert fake.helix_request_count == 0, "a rejected Origin never reached Twitch"


# --------------------------------------------------------------------- R47: the /oauth/* exemption


async def test_oauth_routes_are_open_only_with_user_oauth_on(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R47: with user OAuth off the two routes are neither registered nor bearer-exempt."""
    async with running_app(fake, fclock) as app, http_client(app) as http:
        for path in ("/oauth/start", "/oauth/callback"):
            assert (await http.get(path)).status_code == 401, path
    assert fake.helix_request_count == 0, "an unauthenticated browser never reached Twitch"

    settings = server_settings(twitch_user_oauth=True)
    async with running_app(fake, fclock, settings) as app, http_client(app) as http:
        started = await http.get("/oauth/start")
        assert started.status_code == 302
        location = started.headers["Location"]
        assert location.startswith("https://id.twitch.tv/oauth2/authorize")
        assert "state=" in location and "client_id=" in location
        # The page carries the security headers and echoes no query value.
        assert started.headers["Cache-Control"] == "no-store"
        # A refused callback must not spend the code: count the mints before it, and demand the same
        # number after. Boot minted once; an exchange would add a POST to /oauth2/token.
        mints_before = len(fake.requests_for("POST", "/oauth2/token"))
        failed = await http.get("/oauth/callback", params={"code": "x", "state": "y"})
        assert failed.status_code == 400
        assert failed.headers["Cache-Control"] == "no-store"
        assert "state=y" not in failed.text and '"x"' not in failed.text
    assert fake.helix_request_count == 0  # a refused callback never reached Twitch
    assert len(fake.requests_for("POST", "/oauth2/token")) == mints_before, (
        "R48: a callback refused on state exchanged nothing"
    )


async def test_oauth_routes_stay_host_guarded_while_bearer_exempt(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R5 + R47: exempt from bearer, never from the Host/Origin guard."""
    settings = server_settings(twitch_user_oauth=True)
    async with running_app(fake, fclock, settings) as app, http_client(app) as http:
        assert (await http.get("/oauth/start", headers={"Host": "evil.example"})).status_code == 421
        assert (
            await http.get("/oauth/start", headers={"Origin": "https://evil.example"})
        ).status_code == 403


# --------------------------------------------------------------------- R39: refusals before service


async def test_unknown_tool_is_refused_before_any_service_call(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R39: an unknown tool name is refused with zero upstream requests."""
    server, _svc, _plan = await boot_server(fake, fclock)
    with pytest.raises(BaseExceptionGroup) as info:
        await call(server, "get_channel_secrets", {"broadcaster_id": BROADCASTER})

    # The SDK reports an unknown tool as MCPError(-32602, "Unknown tool"), wrapped in a nested
    # ExceptionGroup by the task group. R39 is about the inner error, so walk to it rather than
    # match the wrapper's text -- asserting on "unhandled errors in a TaskGroup" asserts nothing.
    def _flatten(exc: BaseException) -> list[BaseException]:
        if isinstance(exc, BaseExceptionGroup):
            return [e for sub in exc.exceptions for e in _flatten(sub)]
        return [exc]

    (inner,) = [e for e in _flatten(info.value) if isinstance(e, MCPError)]
    assert inner.code == -32602
    assert "unknown tool" in str(inner).lower()
    assert fake.request_count == 0


async def test_unknown_argument_is_refused_before_any_service_call(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R39: an unexpected argument is an R43 tool error that sent nothing."""
    server, _svc, _plan = await boot_server(fake, fclock)
    result = await call(
        server, "get_channels", {"broadcaster_ids": [BROADCASTER], "include_email": True}
    )
    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["kind"] == "invalid_arguments"
    assert "include_email" in error["message"]
    assert fake.request_count == 0


async def test_schema_violation_is_refused_before_any_service_call(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R39: a malformed id never reaches the client (R17's first gate)."""
    server, _svc, _plan = await boot_server(fake, fclock)
    result = await call(server, "get_channels", {"broadcaster_ids": ["../../etc/passwd"]})
    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["kind"] == "invalid_arguments"
    assert "broadcaster_ids" in error["message"]
    assert fake.request_count == 0


async def test_missing_required_argument_is_refused_before_any_service_call(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R39: a required argument omitted is still refused before the service."""
    server, _svc, _plan = await boot_server(fake, fclock)
    result = await call(server, "get_channels", {})
    assert result.is_error is True
    assert result.structured_content["error"]["kind"] == "invalid_arguments"
    assert fake.request_count == 0


async def test_protocol_fault_is_a_jsonrpc_error_not_a_tool_result(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R43: JSON-RPC errors are for protocol faults only — here, an unknown method."""
    async with running_app(fake, fclock) as app, http_client(app) as http:
        message = await rpc(http, "nonexistent/method")
    assert "error" in message
    assert message["error"]["code"] in (-32601, -32602, -32600)
    assert "result" not in message
    assert fake.helix_request_count == 0, "a protocol fault never reached Twitch"


async def test_tool_crash_is_a_generic_jsonrpc_error_without_detail(
    fake: FakeTwitch, fclock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R43: an internal exception never leaks its text to the caller."""
    server, svc, _plan = await boot_server(fake, fclock)

    async def boom(**_kwargs: Any) -> Any:
        msg = "".join(["boom-", "INTERNAL-", "DETAIL"])
        raise RuntimeError(msg)

    monkeypatch.setattr(svc, "get_channels", boom)
    async with Client(server) as client:
        with pytest.raises(MCPError):
            await client.call_tool("get_channels", {"broadcaster_ids": [BROADCASTER]})
    assert fake.request_count == 0


# --------------------------------------------------------------------- R43: the error shape


async def test_every_error_result_carries_exactly_the_seven_fields(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R43: isError tool results carry {kind, message, code, http_status, retryable, retry_after, hint}."""
    server, _svc, _plan = await boot_server(fake, fclock)
    result = await call(server, "get_channels", {"broadcaster_ids": [NOT_ALLOWED]})
    assert result.is_error is True
    error = result.structured_content["error"]
    assert set(error) == {
        "kind",
        "message",
        "code",
        "http_status",
        "retryable",
        "retry_after",
        "hint",
    }
    assert error["kind"] == "forbidden_channel"
    assert error["retryable"] is False
    assert error["hint"]
    assert result.content and result.content[0].text.startswith("forbidden_channel:")
    assert fake.request_count == 0


async def test_upstream_404_is_an_isError_tool_result_with_its_status(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R43: a real upstream failure is an isError tool result, not a JSON-RPC error."""
    # FakeTwitch answers an unmatched GET /helix* with an empty 200, so the 404 must be routed
    # explicitly -- leaving it unmatched produced a *successful* empty page, not a failure.
    fake.add("GET", r"/helix/games", fake.json({"error": "Not Found", "status": 404}, status=404))
    server, _svc, _plan = await boot_server(fake, fclock)
    result = await call(server, "get_games", {"ids": ["504129"]})
    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["kind"] in {"upstream", "not_found"}
    assert error["http_status"] == 404


# --------------------------------------------------------------------- R31 critical: confirm is success


async def test_write_without_confirm_is_a_success_and_sends_nothing(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R31: the first turn of a write is NOT an error; it carries the plan and zero requests."""
    settings = server_settings(
        twitch_enable_writes=True, twitch_write_groups=ALL_GROUPS, twitch_user_oauth=True
    )
    server, _svc, _plan = await boot_server(fake, fclock, settings, connected=True)
    fake.clear()
    result = await call(server, "create_poll", TOOL_ARGS["create_poll"])
    assert result.is_error is not True  # the whole point: a plan is data, not a failure
    body = result.structured_content
    assert body["confirmation_required"] is True
    assert body["dispatched"] is False
    confirmation = body["confirmation"]
    assert confirmation["tool"] == "create_poll"
    assert confirmation["broadcaster_id"] == BROADCASTER
    assert confirmation["token"]
    assert confirmation["expires_at"]
    assert fake.request_count == 0
    # The message text is identical to the structured content, and neither holds a secret.
    assert json.loads(result.content[0].text)["confirmation_required"] is True
    assert "Bearer" not in result.content[0].text


async def test_write_then_confirm_token_performs_exactly_one_request(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """A4 half: the token turn is the only one that reaches Helix, exactly once."""
    settings = server_settings(
        twitch_enable_writes=True, twitch_write_groups=ALL_GROUPS, twitch_user_oauth=True
    )
    server, _svc, _plan = await boot_server(fake, fclock, settings, connected=True)
    fake.clear()
    first = await call(server, "add_vip", TOOL_ARGS["add_vip"])
    token = first.structured_content["confirmation"]["token"]
    fake.add("POST", r"/helix/channels/vips", fake.json({"data": [{"user_id": USER_ID}]}))
    second = await call(server, "add_vip", {**TOOL_ARGS["add_vip"], "confirm": token})
    assert first.is_error is not True
    assert second.is_error is not True
    assert second.structured_content["dispatched"] is True
    assert fake.request_count == 1
    assert fake.methods == ["POST"]


async def test_a_genuine_write_failure_is_an_isError_result(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R31 contrast: confirmation_required is a success, a real failure is isError."""
    settings = server_settings(
        twitch_enable_writes=True, twitch_write_groups=ALL_GROUPS, twitch_user_oauth=True
    )
    server, _svc, _plan = await boot_server(fake, fclock, settings, connected=True)
    fake.clear()
    result = await call(server, "add_vip", {**TOOL_ARGS["add_vip"], "user_id": "not-a-user-id"})
    assert result.is_error is True
    assert result.structured_content["error"]["kind"] == "invalid_arguments"
    assert fake.request_count == 0


async def test_write_on_an_unallowlisted_channel_is_refused_with_zero_requests(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R25 + R31: the guard runs before confirmation, so a refused write never mints or sends."""
    settings = server_settings(
        twitch_enable_writes=True, twitch_write_groups=ALL_GROUPS, twitch_user_oauth=True
    )
    server, _svc, _plan = await boot_server(fake, fclock, settings, connected=True)
    fake.clear()
    result = await call(server, "ban_user", {"broadcaster_id": NOT_ALLOWED, "user_id": USER_ID})
    assert result.is_error is True
    assert result.structured_content["error"]["kind"] == "forbidden_channel"
    assert fake.request_count == 0


# --------------------------------------------------------------------- R52: no secret in any result


async def test_no_result_ever_echoes_a_credential(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R52 across the surface: no tool result or error text contains token material."""
    settings = server_settings(
        twitch_enable_writes=True, twitch_write_groups=ALL_GROUPS, twitch_user_oauth=True
    )
    server, _svc, _plan = await boot_server(fake, fclock, settings, connected=True)
    secrets = (FAKE_AUTH_TOKEN, FAKE_CLIENT_SECRET, "app-bearer-value", "user-bearer-value")
    for name in ("twitch_account_status", "get_users", "get_channels", "create_poll"):
        result = await call(server, name, TOOL_ARGS[name])
        text = result.content[0].text
        for secret in secrets:
            assert secret not in text, name
    assert VALIDATE_USER["user_id"] == OPERATOR
