"""U5-U7, U16-U21, U17 (elicitation): the five user-OAuth tools, end to end over the MCP surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import Client
from starlette.applications import Starlette

from discord_mcp.capabilities import ALL_TOOL_NAMES, USER_OAUTH_TOOLS
from discord_mcp.config import Settings
from discord_mcp.oauth import CONNECT_HINT, TokenStore, UserAuth
from discord_mcp.server import create_app
from tests.conftest import (
    FAKE_AUTH_TOKEN,
    GUILD,
    OTHER_GUILD,
    FakeClock,
    FakeDiscord,
    make_settings,
)
from tests.test_user_oauth_client import user_token
from tests.user_oauth_fakes import (
    HUMAN_ID,
    HUMAN_NAME,
    OAuthDiscord,
    human_guild,
    on_disk,
    user_settings,
    write_text_file,
)

MCP_URL = "/mcp"
BEARER = {"Authorization": f"Bearer {FAKE_AUTH_TOKEN}"}
SIGN_IN_URL = "http://127.0.0.1:8769/oauth/start"
REDIRECT = "http://127.0.0.1:8769/oauth/callback"
ELICIT_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "mcp-protocol-version": "2026-07-28",
    "mcp-method": "tools/call",
}


@pytest.fixture
def oauth(fake: FakeDiscord) -> OAuthDiscord:
    return OAuthDiscord(fake)


@asynccontextmanager
async def user_app(
    fake: FakeDiscord, fclock: FakeClock, settings: Settings
) -> AsyncIterator[Starlette]:
    app = create_app(
        settings,
        transport=fake.transport(),
        sleep=fclock.sleep,
        now=fclock.wall,
        user_now=fclock.clock,
    )
    async with app.router.lifespan_context(app):
        fake.requests.clear()  # the boot probe is not part of the test requests
        yield app


def http(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8769"
    )


def payload(result: Any) -> dict[str, Any]:
    return dict(result.structured_content)


def connect_user(app: Starlette, tmp_path: Path, fclock: FakeClock) -> None:
    """Install a token through the same store the server uses (as after a completed sign-in)."""
    store = TokenStore(Path(app.state.user_oauth._settings.discord_oauth_token_file))  # noqa: SLF001
    UserAuth(store, clock=fclock.clock).install(user_token())
    app.state.user_oauth._auth.load()  # noqa: SLF001 - the server loads the file at boot


# ------------------------------------------------------------------ registry (U5-U7)


async def test_the_switch_on_registers_the_bot_tools_then_the_five_in_order(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with (
        user_app(fake, fclock, user_settings(tmp_path)) as app,
        Client(app.state.discord_server) as client,
    ):
        names = [tool.name for tool in (await client.list_tools()).tools]
    assert names == list(ALL_TOOL_NAMES) + list(USER_OAUTH_TOOLS)
    assert len(names) == 19


async def test_the_switch_off_keeps_exactly_the_fourteen_tools(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    async with (
        user_app(fake, fclock, make_settings()) as app,
        Client(app.state.discord_server) as client,
    ):
        names = [tool.name for tool in (await client.list_tools()).tools]
    assert names == list(ALL_TOOL_NAMES)
    assert len(names) == 14


async def test_the_five_tools_carry_the_annotations_of_the_amendment(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """U6: read-only where it is read-only; disconnect is destructive and not read-only."""
    expected = {
        "discord_account_status": (True, False, True, False),
        "connect_discord_account": (True, False, True, False),
        "disconnect_discord_account": (False, True, True, True),
        "list_my_guilds": (True, False, True, True),
        "get_guild_invite_link": (True, False, True, False),
    }
    async with (
        user_app(fake, fclock, user_settings(tmp_path)) as app,
        Client(app.state.discord_server) as client,
    ):
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    for name, (read_only, destructive, idempotent, open_world) in expected.items():
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is read_only, name
        assert annotations.destructive_hint is destructive, name
        assert annotations.idempotent_hint is idempotent, name
        assert annotations.open_world_hint is open_world, name


async def test_an_unexpected_argument_is_a_validation_error_before_any_request(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """U7: the unknown-argument rejection applies to the user tools too."""
    async with user_app(fake, fclock, user_settings(tmp_path)) as app:
        before = len(fake.requests)
        async with Client(app.state.discord_server) as client:
            result = await client.call_tool("discord_account_status", {"extra": 1})
    assert result.is_error is True
    assert payload(result)["error"]["kind"] == "validation"
    assert len(fake.requests) == before


async def test_the_budget_applies_to_the_user_tools(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """U7: a user tool is refused by the tool-call budget like any other tool."""
    settings = user_settings(tmp_path, tool_calls_per_minute_per_tool=1)
    async with user_app(fake, fclock, settings) as app, Client(app.state.discord_server) as client:
        await client.call_tool("discord_account_status", {})
        refused = await client.call_tool("discord_account_status", {})
    assert payload(refused)["error"]["kind"] == "rate_limited"


async def test_a_mismatched_application_id_registers_none_of_the_five(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock
) -> None:
    fake.application["id"] = "999999999999999999"
    async with (
        user_app(fake, fclock, user_settings(tmp_path)) as app,
        Client(app.state.discord_server) as client,
    ):
        names = [tool.name for tool in (await client.list_tools()).tools]
    assert names == list(ALL_TOOL_NAMES)


# ------------------------------------------------------------------ status and connect (U16-U17)


async def test_status_when_not_connected_has_no_account_fields(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with (
        user_app(fake, fclock, user_settings(tmp_path)) as app,
        Client(app.state.discord_server) as client,
    ):
        result = await client.call_tool("discord_account_status", {})
    data = payload(result)
    assert data["enabled"] is True
    assert data["state"] == "not_connected"
    assert data["pending_sign_in"] is False
    assert data["last_error"] is None
    assert data["sign_in_url"] == SIGN_IN_URL
    assert data["redirect_uri"] == REDIRECT
    assert "user_id" not in data and "scopes" not in data
    assert fake.requests == []  # local state only


async def test_status_when_connected_reports_the_account_without_tokens(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app:
        connect_user(app, tmp_path, fclock)
        async with Client(app.state.discord_server) as client:
            result = await client.call_tool("discord_account_status", {})
    data = payload(result)
    assert data["state"] == "connected"
    assert data["username"] == "synthetic-human"
    assert data["user_id"] == HUMAN_ID
    assert data["scopes"] == ["identify", "guilds"]
    assert data["expires_at"].endswith("Z") and data["connected_at"].endswith("Z")
    assert "ACC-1" not in str(result.content) and "REF-1" not in str(result.content)


async def test_connect_when_not_connected_returns_the_plain_result_and_sends_nothing(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with (
        user_app(fake, fclock, user_settings(tmp_path)) as app,
        Client(app.state.discord_server) as client,
    ):
        result = await client.call_tool("connect_discord_account", {})
    data = payload(result)
    assert data["state"] == "authorization_required"
    assert data["sign_in_url"] == SIGN_IN_URL
    assert data["redirect_uri"] == REDIRECT
    assert set(data["open_commands"]) == {"windows", "macos", "linux"}
    assert SIGN_IN_URL in data["open_commands"]["linux"]
    assert fake.requests == []  # no Discord request, no state created
    assert app.state.user_oauth.status().pending_sign_in is False


async def test_connect_when_connected_says_so_unless_reconnect_is_true(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app:
        connect_user(app, tmp_path, fclock)
        async with Client(app.state.discord_server) as client:
            already = await client.call_tool("connect_discord_account", {})
            again = await client.call_tool("connect_discord_account", {"reconnect": True})
    assert payload(already) == {
        "state": "already_connected",
        "username": HUMAN_NAME,
        "sign_in_url": SIGN_IN_URL,
    }
    assert payload(again)["state"] == "authorization_required"


# ------------------------------------------------------------------ elicitation (U17, §6.3)


def connect_request(caps: dict[str, Any] | None, version: str = "2026-07-28") -> dict[str, Any]:
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "gate", "version": "0"},
    }
    if caps is not None:
        meta["io.modelcontextprotocol/clientCapabilities"] = caps
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "connect_discord_account", "arguments": {}, "_meta": meta},
    }


async def test_a_2026_client_with_url_elicitation_gets_the_sign_in_elicitation(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """U17: InputRequiredResult with one elicitation/create in URL mode, the exact sign-in URL."""
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        response = await h.post(
            MCP_URL,
            json=connect_request({"elicitation": {"url": {}}}),
            headers={**BEARER, **ELICIT_HEADERS, "mcp-name": "connect_discord_account"},
        )
    result = response.json()["result"]
    assert result["resultType"] == "input_required"
    request = result["inputRequests"]["discord_sign_in"]
    assert request["method"] == "elicitation/create"
    assert request["params"]["mode"] == "url"
    assert request["params"]["url"] == SIGN_IN_URL
    assert request["params"]["message"] == "Discord needs you to sign in to connect your account"
    assert fake.requests == []


async def test_the_elicitation_kill_switch_returns_the_plain_result(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    settings = user_settings(tmp_path, discord_oauth_url_elicitation=False)
    async with user_app(fake, fclock, settings) as app, http(app) as h:
        response = await h.post(
            MCP_URL,
            json=connect_request({"elicitation": {"url": {}}}),
            headers={**BEARER, **ELICIT_HEADERS, "mcp-name": "connect_discord_account"},
        )
    result = response.json()["result"]
    assert result.get("resultType") != "input_required"
    assert result["structuredContent"]["state"] == "authorization_required"


async def test_a_2025_client_gets_the_plain_result_even_with_url_capability(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """Only 2026-07-28 requests are elicited; the legacy revision takes the plain result."""
    body = connect_request({"elicitation": {"url": {}}}, version="2025-06-18")
    headers = {
        **BEARER,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "mcp-protocol-version": "2025-06-18",
        "mcp-method": "tools/call",
        "mcp-name": "connect_discord_account",
    }
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        response = await h.post(MCP_URL, json=body, headers=headers)
    result = response.json()["result"]
    assert result.get("resultType") != "input_required"
    assert result["structuredContent"]["state"] == "authorization_required"


async def test_a_2026_client_without_url_capability_gets_the_plain_result(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        response = await h.post(
            MCP_URL,
            json=connect_request({"elicitation": {"form": {}}}),
            headers={**BEARER, **ELICIT_HEADERS, "mcp-name": "connect_discord_account"},
        )
    assert response.json()["result"].get("resultType") != "input_required"


async def test_a_connected_request_is_not_elicited_without_reconnect(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        connect_user(app, tmp_path, fclock)
        response = await h.post(
            MCP_URL,
            json=connect_request({"elicitation": {"url": {}}}),
            headers={**BEARER, **ELICIT_HEADERS, "mcp-name": "connect_discord_account"},
        )
    result = response.json()["result"]
    assert result.get("resultType") != "input_required"
    assert result["structuredContent"]["state"] == "already_connected"


# ------------------------------------------------------------------ disconnect (U18)


async def test_disconnect_when_not_connected_is_a_no_op(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with (
        user_app(fake, fclock, user_settings(tmp_path)) as app,
        Client(app.state.discord_server) as client,
    ):
        result = await client.call_tool("disconnect_discord_account", {})
    assert payload(result) == {"state": "not_connected"}
    assert oauth.posts == []


async def test_disconnect_revokes_then_deletes_the_token_file(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    settings = user_settings(tmp_path)
    async with user_app(fake, fclock, settings) as app:
        connect_user(app, tmp_path, fclock)
        async with Client(app.state.discord_server) as client:
            result = await client.call_tool("disconnect_discord_account", {})
            status = await client.call_tool("discord_account_status", {})
    assert payload(result) == {"state": "disconnected", "revoked": True}
    assert oauth.revoked == ["REF-1"]
    assert not on_disk(settings.discord_oauth_token_file)
    assert payload(status)["state"] == "not_connected"


async def test_a_failed_revoke_still_deletes_and_records_revoke_failed(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.revoke_status = 500
    settings = user_settings(tmp_path)
    async with user_app(fake, fclock, settings) as app:
        connect_user(app, tmp_path, fclock)
        async with Client(app.state.discord_server) as client:
            result = await client.call_tool("disconnect_discord_account", {})
            status = await client.call_tool("discord_account_status", {})
    assert payload(result) == {"state": "disconnected", "revoked": False}
    assert payload(status)["last_error"] == "revoke_failed"
    assert not on_disk(settings.discord_oauth_token_file)


async def test_an_invalid_token_file_is_removed_by_disconnect(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    settings = user_settings(tmp_path)
    write_text_file(settings.discord_oauth_token_file, "garbage")
    async with user_app(fake, fclock, settings) as app, Client(app.state.discord_server) as client:
        status_before = await client.call_tool("discord_account_status", {})
        result = await client.call_tool("disconnect_discord_account", {})
    assert payload(status_before)["state"] == "token_file_invalid"
    assert payload(result) == {"state": "not_connected"}
    assert not on_disk(settings.discord_oauth_token_file)


# ------------------------------------------------------------------ list_my_guilds (U20)


async def test_list_my_guilds_without_a_connected_account_is_auth_with_the_hint(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with (
        user_app(fake, fclock, user_settings(tmp_path)) as app,
        Client(app.state.discord_server) as client,
    ):
        result = await client.call_tool("list_my_guilds", {})
    error = payload(result)["error"]
    assert result.is_error is True
    assert error["kind"] == "auth"
    assert error["hint"] == CONNECT_HINT
    assert oauth.bearers == []


async def test_list_my_guilds_derives_the_flags_for_each_guild(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.human_guilds = [
        human_guild(GUILD, owner=False, permissions="8", name="allowlisted admin"),
        human_guild("555555555555555555", owner=True, permissions="0", name="owned"),
        human_guild("666666666666666666", owner=False, permissions="32", name="manager"),
        human_guild("888888888888888888", owner=False, permissions="1024", name="plain member"),
    ]
    async with user_app(fake, fclock, user_settings(tmp_path)) as app:
        connect_user(app, tmp_path, fclock)
        async with Client(app.state.discord_server) as client:
            result = await client.call_tool("list_my_guilds", {})
    data = payload(result)
    assert data["count"] == 4
    assert data["truncated"] is False
    assert data["bot_guilds_known"] is True
    by_id = {guild["id"]: guild for guild in data["guilds"]}
    assert by_id[GUILD]["allowlisted"] is True
    assert by_id[GUILD]["bot_present"] is True  # in the bot's fixture guild list
    assert by_id[GUILD]["can_add_bot"] is True  # ADMINISTRATOR
    assert by_id["555555555555555555"]["can_add_bot"] is True  # owner
    assert by_id["666666666666666666"]["can_add_bot"] is True  # MANAGE_GUILD
    assert by_id["888888888888888888"]["can_add_bot"] is False
    assert by_id["888888888888888888"]["bot_present"] is False
    assert by_id["888888888888888888"]["allowlisted"] is False
    assert "approximate_member_count" not in by_id[GUILD]  # counts only when asked
    assert oauth.guild_queries[0] == {"limit": "200"}


async def test_list_my_guilds_with_counts_adds_the_approximate_counts(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.human_guilds = [human_guild(GUILD, owner=True, permissions="0")]
    async with user_app(fake, fclock, user_settings(tmp_path)) as app:
        connect_user(app, tmp_path, fclock)
        async with Client(app.state.discord_server) as client:
            result = await client.call_tool("list_my_guilds", {"with_counts": True})
    guild = payload(result)["guilds"][0]
    assert guild["approximate_member_count"] == 12
    assert guild["approximate_presence_count"] == 3
    assert oauth.guild_queries[-1] == {"limit": "200", "with_counts": "true"}


async def test_a_bot_guild_fetch_failure_leaves_bot_present_unknown(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """U20: the tool still succeeds; bot_present is null and bot_guilds_known is false."""
    oauth.human_guilds = [human_guild(GUILD, owner=True)]
    oauth.bot_guilds_status = 500
    async with user_app(fake, fclock, user_settings(tmp_path)) as app:
        connect_user(app, tmp_path, fclock)
        async with Client(app.state.discord_server) as client:
            result = await client.call_tool("list_my_guilds", {})
    data = payload(result)
    assert data["bot_guilds_known"] is False
    assert data["guilds"][0]["bot_present"] is None


async def test_list_my_guilds_pages_with_the_last_id_and_is_not_truncated(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    full = [human_guild(str(100_000_000_000_000_000 + i)) for i in range(200)]
    tail = [human_guild(str(200_000_000_000_000_000 + i)) for i in range(3)]
    oauth.human_guild_pages = [full, tail]
    async with user_app(fake, fclock, user_settings(tmp_path)) as app:
        connect_user(app, tmp_path, fclock)
        async with Client(app.state.discord_server) as client:
            result = await client.call_tool("list_my_guilds", {})
    data = payload(result)
    assert data["count"] == 203
    assert data["truncated"] is False
    assert oauth.guild_queries[1]["after"] == full[-1]["id"]


async def test_five_full_pages_are_truncated_at_one_thousand(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    pages = [
        [human_guild(str(300_000_000_000_000_000 + page * 1000 + i)) for i in range(200)]
        for page in range(5)
    ]
    oauth.human_guild_pages = pages
    async with user_app(fake, fclock, user_settings(tmp_path)) as app:
        connect_user(app, tmp_path, fclock)
        async with Client(app.state.discord_server) as client:
            result = await client.call_tool("list_my_guilds", {})
    data = payload(result)
    assert data["count"] == 1000
    assert data["truncated"] is True
    assert len(oauth.guild_queries) == 5


async def test_list_my_guilds_is_not_limited_by_the_guild_allowlist(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.human_guilds = [human_guild(OTHER_GUILD, owner=True)]
    async with user_app(fake, fclock, user_settings(tmp_path)) as app:
        connect_user(app, tmp_path, fclock)
        async with Client(app.state.discord_server) as client:
            result = await client.call_tool("list_my_guilds", {})
    guild = payload(result)["guilds"][0]
    assert guild["id"] == OTHER_GUILD
    assert guild["allowlisted"] is False


# ------------------------------------------------------------------ invite link (U21)


async def test_the_invite_link_is_exact_and_makes_no_request(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with (
        user_app(fake, fclock, user_settings(tmp_path)) as app,
        Client(app.state.discord_server) as client,
    ):
        result = await client.call_tool("get_guild_invite_link", {"guild_id": GUILD})
    data = payload(result)
    assert data["guild_id"] == GUILD
    assert data["url"] == (
        "https://discord.com/oauth2/authorize?client_id=222222222222222222&scope=bot"
        f"&permissions=66688&guild_id={GUILD}&disable_guild_select=true"
    )
    assert "Manage Server" in data["note"]
    assert fake.requests == []


async def test_the_invite_link_rejects_a_bad_snowflake(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with (
        user_app(fake, fclock, user_settings(tmp_path)) as app,
        Client(app.state.discord_server) as client,
    ):
        result = await client.call_tool("get_guild_invite_link", {"guild_id": "12"})
    assert result.is_error is True
    assert payload(result)["error"]["kind"] == "validation"


async def test_user_guild_results_are_never_cached(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """A3 fix round (m9): every list_my_guilds call goes to Discord with the user token (U20)."""
    oauth.human_guilds = [human_guild(GUILD, owner=True)]
    async with user_app(fake, fclock, user_settings(tmp_path)) as app:
        connect_user(app, tmp_path, fclock)
        async with Client(app.state.discord_server) as client:
            await client.call_tool("list_my_guilds", {})
            await client.call_tool("list_my_guilds", {})
    assert len(oauth.guild_queries) == 2
    assert all(bearer == "ACC-1" for bearer in oauth.bearers if bearer)


async def test_a_failed_token_file_delete_on_disconnect_is_unavailable_and_names_the_path(
    tmp_path: Path,
    fake: FakeDiscord,
    fclock: FakeClock,
    oauth: OAuthDiscord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A3.2 (U18, D33): the revoke result stands, memory is cleared, and the error names the file."""
    settings = user_settings(tmp_path)

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("denied")

    async with user_app(fake, fclock, settings) as app:
        connect_user(app, tmp_path, fclock)
        monkeypatch.setattr(Path, "unlink", refuse)
        async with Client(app.state.discord_server) as client:
            result = await client.call_tool("disconnect_discord_account", {})
            status = await client.call_tool("discord_account_status", {})
    error = payload(result)["error"]
    assert result.is_error is True
    assert error["kind"] == "unavailable"
    assert settings.discord_oauth_token_file in error["hint"]
    assert "ACC-1" not in error["hint"] and "REF-1" not in error["hint"]
    assert payload(status)["state"] == "not_connected"
    assert oauth.revoked == ["REF-1"]
