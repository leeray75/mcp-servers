"""Shared fixtures. Nothing here touches the network or real credentials.

The fake Discord serves synthetic JSON fixtures through ``httpx.MockTransport`` and records every
request, so tests can assert that a refused call sent nothing. Clock and sleep are injected; the
fake clock advances when sleeping, so rate-limit waits run instantly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from discord_mcp.config import Settings
from discord_mcp.discord_client import DiscordClient
from discord_mcp.service import DiscordService

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_AUTH_TOKEN = "t" * 16 + "FAKE-MCP-TOKEN" + "u" * 16  # 46 chars, obviously fake
FAKE_BOT_TOKEN = "MTAAAAAAAAAAAAAAAAAAAAAA.FAKEbot.ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
GUILD = "333333333333333333"
OTHER_GUILD = "777777777777777777"
OTHER_CHANNEL = "404040404040404040"  # a channel in OTHER_GUILD (not allowlisted)
BOT = "111111111111111111"
TEXT = "999999999999999999"
REPORTS = "888888888888888888"
CATEGORY = "150150150150150150"
VOICE = "160160160160160160"
THREAD = "171717171717171717"
THREAD_B = "181818181818181818"
ALICE = "121212121212121212"
MESSAGE = "1234567890123456789"
OLD_MESSAGE = "1212121212121212121"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
API = "/api/v10"

_ENV_NAMES = (
    "MCP_AUTH_TOKEN",
    "DISCORD_BOT_TOKEN",
    "DISCORD_ALLOWED_GUILD_IDS",
    "DISCORD_APPLICATION_ID",
    "DISCORD_API_BASE",
    "DISCORD_USER_AGENT",
    "DISCORD_ENABLE_SEARCH",
    "DISCORD_ENABLE_MEMBER_LIST",
    "DISCORD_FORCE_MESSAGE_CONTENT",
    "DISCORD_FORCE_GUILD_MEMBERS",
    "DISCORD_TIMEOUT_SECONDS",
    "DISCORD_MAX_RESPONSE_BYTES",
    "DISCORD_MAX_RATE_LIMIT_WAIT_SECONDS",
    "DISCORD_MAX_429_RETRIES",
    "DISCORD_SEARCH_INDEX_RETRIES",
    "DISCORD_GLOBAL_REQUESTS_PER_SECOND",
    "DISCORD_INVALID_REQUEST_BUDGET",
    "TTL_METADATA_SECONDS",
    "CACHE_MAX_ENTRIES",
    "CACHE_MAX_MB",
    "TOOL_CALLS_PER_MINUTE",
    "TOOL_CALLS_PER_MINUTE_PER_TOOL",
    "LOG_LEVEL",
    "HOST",
    "PORT",
)


def load(name: str) -> Any:
    """Load a synthetic fixture from tests/fixtures."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def make_settings(**overrides: Any) -> Settings:
    data: dict[str, Any] = {
        "mcp_auth_token": FAKE_AUTH_TOKEN,
        "discord_bot_token": FAKE_BOT_TOKEN,
        "discord_allowed_guild_ids": GUILD,
    }
    data.update(overrides)
    return Settings.model_validate(data)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer's real environment never leaks into tests."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Fail any request that would leave the process through httpx's real transport."""

    async def refuse(self: object, req: httpx.Request) -> httpx.Response:
        raise AssertionError(f"test attempted a real network request: {req.url.host}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


class FakeClock:
    """Monotonic clock whose ``sleep`` advances time instead of waiting."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def wall(self) -> datetime:
        return NOW + timedelta(seconds=self.t - 1000.0)


Handler = Callable[[httpx.Request], httpx.Response]


class FakeDiscord:
    """Serves synthetic Discord responses and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.overrides: list[tuple[str, str, Handler, int | None]] = []
        self.messages_by_channel: dict[str, Any] = {}
        self.guilds: dict[str, Any] = {}
        self.search_responses: list[httpx.Response] = []
        self.channels: dict[str, dict[str, Any]] = {}
        self.probe_status: dict[str, int] = {}
        self.me = load("user_me")
        self.application = load("application")
        self.guild = load("guild")
        self.user_guilds = load("user_guilds")
        self.guild_channels = load("guild_channels")
        self.member_bot = load("bot_member")
        self.members = load("members")
        self.messages = load("messages")
        self.search = load("search_response")
        self.threads = load("threads_active")
        self.audit = load("audit_log")
        self.pins = load("pins")
        self.reaction_users = load("reaction_users")
        self.channels = {str(c["id"]): c for c in self.guild_channels}
        self.channels[THREAD] = self.threads["threads"][0]
        self.channels[THREAD_B] = self.threads["threads"][1]
        self.channels[OLD_MESSAGE] = {}
        del self.channels[OLD_MESSAGE]
        self.dm_channel = {"id": "303030303030303030", "type": 1, "recipients": []}
        self.channels["303030303030303030"] = self.dm_channel
        self.other_guild_channel = {
            "id": "404040404040404040",
            "type": 0,
            "guild_id": OTHER_GUILD,
            "name": "elsewhere",
            "position": 0,
            "permission_overwrites": [],
        }
        self.channels["404040404040404040"] = self.other_guild_channel

    def add(self, method: str, pattern: str, handler: Handler, times: int | None = None) -> None:
        """Serve ``handler`` for requests matching ``pattern`` (checked before the defaults)."""
        self.overrides.append((method, pattern, handler, times))

    def json(self, body: Any, status: int = 200, headers: dict[str, str] | None = None) -> Handler:
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=body, headers=headers)

        return handler

    @property
    def paths(self) -> list[str]:
        return [req.url.path for req in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        for index, (method, pattern, handler, times) in enumerate(self.overrides):
            if request.method == method and re.fullmatch(pattern, path):
                if times is not None:
                    left = times - 1
                    if left <= 0:
                        self.overrides.pop(index)
                    else:
                        self.overrides[index] = (method, pattern, handler, left)
                return handler(request)
        return self._default(request)

    def _default(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(API)
        query = request.url.params
        if path == "/users/@me":
            return httpx.Response(200, json=self.me)
        if path == "/applications/@me":
            return httpx.Response(200, json=self.application)
        if path == "/users/@me/guilds":
            if query.get("after"):
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=self.user_guilds)
        m = re.fullmatch(r"/guilds/(\d+)", path)
        if m:
            return httpx.Response(200, json=self.guild if m.group(1) == GUILD else None)
        if re.fullmatch(r"/guilds/\d+/channels", path):
            return httpx.Response(200, json=self.guild_channels)
        m = re.fullmatch(r"/guilds/(\d+)/members/(\d+)", path)
        if m:
            if m.group(2) == BOT:
                return httpx.Response(200, json=self.member_bot)
            return httpx.Response(200, json=self.members[0])
        if re.fullmatch(r"/guilds/\d+/members", path):
            return httpx.Response(200, json=self.members)
        if re.fullmatch(r"/guilds/\d+/members/search", path):
            return httpx.Response(200, json=self.members)
        if re.fullmatch(r"/guilds/\d+/threads/active", path):
            return httpx.Response(200, json=self.threads)
        if re.fullmatch(r"/guilds/\d+/audit-logs", path):
            return httpx.Response(200, json=self.audit)
        if re.fullmatch(r"/guilds/\d+/messages/search", path):
            if self.search_responses:
                return self.search_responses.pop(0)
            return httpx.Response(200, json=self.search)
        m = re.fullmatch(r"/channels/(\d+)", path)
        if m:
            channel = self.channels.get(m.group(1))
            if channel is None:
                return httpx.Response(404, json={"code": 10003, "message": "Unknown Channel"})
            return httpx.Response(200, json=channel)
        m = re.fullmatch(r"/channels/(\d+)/messages", path)
        if m:
            return httpx.Response(200, json=self.messages_by_channel.get(m.group(1), self.messages))
        m = re.fullmatch(r"/channels/(\d+)/messages/pins", path)
        if m:
            return httpx.Response(200, json=self.pins)
        m = re.fullmatch(r"/channels/(\d+)/messages/(\d+)", path)
        if m:
            if m.group(2) == MESSAGE:
                return httpx.Response(200, json=self.messages[0])
            return httpx.Response(404, json={"code": 10008, "message": "Unknown Message"})
        if re.fullmatch(r"/channels/\d+/messages/\d+/reactions/.+", path):
            return httpx.Response(200, json=self.reaction_users)
        return httpx.Response(404, json={"code": 0, "message": "no fixture for this route"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


@pytest.fixture
def fake() -> FakeDiscord:
    return FakeDiscord()


@pytest.fixture
def fclock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def client(settings: Settings, fake: FakeDiscord, fclock: FakeClock) -> Iterator[DiscordClient]:
    instance = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    yield instance


@pytest.fixture
def service(settings: Settings, client: DiscordClient, fclock: FakeClock) -> DiscordService:
    return DiscordService(settings, client, now=fclock.wall, sleep=fclock.sleep)
