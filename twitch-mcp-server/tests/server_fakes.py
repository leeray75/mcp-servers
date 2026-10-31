"""Shared helpers for ``test_server.py`` and ``test_sizes.py`` (the server-surface agent).

``tests/conftest.py``, ``tests/twitch_fakes.py``, ``tests/core_fakes.py`` and
``tests/service_fakes.py`` are **frozen**; everything the server tests add lives here:

:func:`boot_server`
    A bare :class:`~twitch_mcp.server.TwitchServer` plus a real :class:`~twitch_mcp.service.TwitchService`
    over the frozen :class:`~tests.twitch_fakes.FakeTwitch` transport, with tools registered exactly
    as the lifespan does (``compute_tool_plan`` → ``_register_tools``). The boot probe's requests are
    cleared, so ``fake.request_count`` counts only what a tool call sent.

:func:`running_app` / :func:`http_client`
    The real :func:`~twitch_mcp.server.create_app` inside its lifespan, plus an
    ``httpx.ASGITransport`` client. No socket is ever opened (R55 keeps the network blocked).

:func:`rpc`
    One raw JSON-RPC POST to ``/mcp`` — the way a protocol fault is provoked (an unknown *method*,
    not an unknown tool) so R43's "JSON-RPC error only for protocol faults" is testable.

``WRITE_ARGS`` / ``READ_ARGS``
    One valid argument set per tool, so the R35 pinning tests and the R41 size tests drive every
    registered tool through the real schema rather than a summary.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from mcp import Client
from starlette.applications import Starlette

from tests.twitch_fakes import (
    BROADCASTER,
    FAKE_AUTH_TOKEN,
    FAKE_CLIENT_ID,
    GAME_ID,
    OPERATOR,
    OTHER_BROADCASTER,
    POLL_ID,
    USER_ID,
    FakeClock,
    FakeTwitch,
    make_settings,
)
from twitch_mcp.capabilities import ToolPlan, compute_tool_plan
from twitch_mcp.config import Settings
from twitch_mcp.server import TwitchServer, _register_tools, create_app
from twitch_mcp.service import TwitchService

#: Port the loopback Host header carries (R5 accepts any port on a loopback hostname).
HOST = "127.0.0.1:8770"
#: Everything an MCP HTTP request must carry: bearer + loopback Host (R4, R5).
MCP_HEADERS: dict[str, str] = {
    "Authorization": f"Bearer {FAKE_AUTH_TOKEN}",
    "Host": HOST,
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

#: The operator's connected Twitch id, as the boot probe reports it (R29: the token decides).
VALIDATE_APP = {"client_id": FAKE_CLIENT_ID, "scopes": ["user:read:email"], "expires_in": 15000}
VALIDATE_USER = {
    "client_id": FAKE_CLIENT_ID,
    "login": "operator",
    "user_id": OPERATOR,
    "scopes": ["user:read:email", "moderator:read:chatters"],
    "expires_in": 15000,
}

#: The value of the per-tool disable flag used by the R36 pinning tests.
DISABLED_FLAG_VALUE = "get_videos"

ALL_GROUPS = "channel,polls,predictions,chat,moderation,roles"


def server_settings(**overrides: Any) -> Settings:
    """Settings for the pinning matrix, with ``disabled_tools`` modelled as the operator would set it.

    ``Settings`` has no ``disabled_tools`` field (config.py alone reads the environment, R6), so
    :func:`~twitch_mcp.server.disabled_tool_names` reads it with ``getattr``. ``model_copy`` is the
    only way to attach a non-field attribute to a pydantic instance, which is exactly what makes the
    test prove the safe direction: an unread flag never *adds* a tool.
    """
    data: dict[str, Any] = {
        "twitch_enable_writes": False,
        "twitch_write_groups": "",
        "twitch_user_oauth": False,
        "twitch_oauth_scopes": "user:read:email moderator:read:chatters",
    }
    data.update({key: value for key, value in overrides.items() if key != "disabled_tools"})
    settings = make_settings(**data)
    if "disabled_tools" in overrides:
        value = overrides["disabled_tools"]
        settings = settings.model_copy()
        object.__setattr__(settings, "disabled_tools", value)
    return settings


def plan_for(
    settings: Settings, *, connected: bool | None = None, disabled: Any = None
) -> ToolPlan:
    """The boot plan for one switch combination.

    ``connected`` defaults to "user OAuth on implies a connected account", which is the state a
    correctly configured deployment reaches; pass ``connected=False`` with
    ``twitch_user_oauth=True`` to prove the token-type omission is separate from the switch.
    """
    has_user_token = settings.twitch_user_oauth if connected is None else connected
    names = settings.disabled_tools if disabled is None else disabled  # type: ignore[attr-defined]
    return compute_tool_plan(settings, has_user_token=has_user_token, disabled_tools=names)


async def tool_names(server: TwitchServer) -> list[str]:
    """The registered names in registration order, exactly as ``tools/list`` returns them."""
    async with Client(server) as client:
        return [tool.name for tool in (await client.list_tools()).tools]


async def list_tools(server: TwitchServer) -> list[Any]:
    async with Client(server) as client:
        return list((await client.list_tools()).tools)


async def call(server: TwitchServer, name: str, arguments: dict[str, Any]) -> Any:
    async with Client(server) as client:
        return await client.call_tool(name, arguments)


def wired(
    fake: FakeTwitch,
    fclock: FakeClock,
    settings: Settings,
    *,
    user_connected: bool,
) -> tuple[Any, ToolPlan]:
    """Build the service + plan for one switch combination without registering anything.

    The fake transport answers ``GET /oauth2/validate`` with the app or user body by ``Client-Id``
    and ``Authorization`` shape, so the app and user credentials stay distinguishable end to end.
    """
    from pydantic import SecretStr

    from tests.service_fakes import (  # imported late: it prints, and owns the credential constants
        APP_BEARER,
        USER_BEARER,
    )
    from tests.twitch_fakes import FAKE_CLIENT_SECRET
    from twitch_mcp.cache import TTLCache
    from twitch_mcp.confirmation import ConfirmationStore
    from twitch_mcp.guard import Guard
    from twitch_mcp.identity import IdentityVerifier, OperatorIdentity
    from twitch_mcp.oauth import OAuthFlow, UserToken
    from twitch_mcp.ratelimit import RateLimiter
    from twitch_mcp.service import TwitchService
    from twitch_mcp.twitch_client import TwitchClient

    async def serve_validate(request: httpx.Request) -> httpx.Response:
        """One fake that answers both halves of the R50 probe.

        ``POST /oauth2/token`` is the app-token mint ``AppTokenSource`` performs before
        ``GET /oauth2/validate``; without it every boot would degrade to ``unauthenticated``.
        """
        presented = request.headers.get("authorization", "")
        body = VALIDATE_USER if presented == f"Bearer {USER_BEARER}" else VALIDATE_APP
        return httpx.Response(200, json=body)

    async def mint_app_token(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": APP_BEARER, "expires_in": 15_000})

    fake.add("GET", r"/oauth2/validate", serve_validate)
    fake.add("POST", r"/oauth2/token", mint_app_token)
    limiter = RateLimiter(max_wait_seconds=30.0, clock=fclock.clock, sleep=fclock.sleep)
    client = TwitchClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        limiter=limiter,
    )
    cache = TTLCache(
        max_entries=64, max_bytes=1_000_000, clock=fclock.clock, wall_clock=fclock.wall
    )

    async def fetch_owned(object_id: str) -> Any:
        async def fetch() -> Any:
            return {"broadcaster_id": BROADCASTER}

        return await cache.get(f"owner:{object_id}", fetch, float(settings.ttl_metadata_seconds))

    guard = Guard(settings.broadcaster_ids, fetch_owned)
    operator = OperatorIdentity(OPERATOR if user_connected else None)

    async def fetch_roster(roster: str, broadcaster_id: str) -> list[str]:
        return [OPERATOR]

    identity = IdentityVerifier(
        operator, cache, fetch_roster, ttl_seconds=float(settings.twitch_identity_ttl_seconds)
    )
    confirmations = ConfirmationStore(
        ttl_seconds=float(settings.twitch_confirm_token_ttl_seconds),
        clock=fclock.clock,
        wall_clock=fclock.unix,
    )

    async def app_token() -> str:
        return APP_BEARER

    user_source = None
    flow = None
    if user_connected:

        async def user_token() -> str:
            return USER_BEARER

        user_source = user_token
        flow = OAuthFlow(
            client_id=FAKE_CLIENT_ID,
            client_secret=FAKE_CLIENT_SECRET,
            auth_base=settings.twitch_auth_base,
            redirect_uri=settings.redirect_uri,
            sign_in_url=settings.sign_in_url,
            scopes=settings.oauth_scope_list,
            token_file="/tmp/twitch-mcp-server-tests/twitch_user_token.json",
            enabled=True,
            clock=fclock.unix,
        )
        flow.auth.token = UserToken(
            access_token=SecretStr(USER_BEARER),
            refresh_token=SecretStr("fake-refresh-token-value"),
            token_type="Bearer",
            scope=" ".join(settings.oauth_scope_list),
            expires_at=fclock.unix() + 3600.0,
            user_id=OPERATOR,
            user_login="operator",
            connected_at=fclock.unix(),
        )

    service = TwitchService(
        settings,
        client,
        limiter,
        guard,
        cache,
        identity,
        confirmations,
        app_token=app_token,
        user_token=user_source,
        oauth=flow,
        now=fclock.wall,
    )
    from twitch_mcp.server import disabled_tool_names

    plan = compute_tool_plan(
        settings,
        has_user_token=user_connected,
        disabled_tools=disabled_tool_names(settings),
    )
    return service, plan


async def boot_server(
    fake: FakeTwitch,
    fclock: FakeClock,
    settings: Settings | None = None,
    *,
    connected: bool | None = None,
) -> tuple[TwitchServer, TwitchService, ToolPlan]:
    """Register exactly the planned tools on a real :class:`TwitchServer`, as the lifespan does."""
    settings = settings or server_settings()
    # ``connected`` means "a user access token is present". It deliberately does NOT flip
    # TWITCH_USER_OAUTH: R46 requires the switch to own the user-token tools, so a token with the
    # switch off must register nothing. Conflating the two made "switch on, token absent" -- the
    # token_absent omission cause of R36 -- impossible to express.
    user_connected = settings.twitch_user_oauth if connected is None else connected
    service, plan = wired(fake, fclock, settings, user_connected=user_connected)
    server = TwitchServer("twitch-mcp", version="0.1.0")
    _register_tools(server, service, plan)
    fake.clear()  # the boot probe's validate call is not part of a tool call's request count
    return server, service, plan


@asynccontextmanager
async def running_app(
    fake: FakeTwitch,
    fclock: FakeClock,
    settings: Settings | None = None,
    *,
    connected: bool = False,
) -> AsyncIterator[Starlette]:
    """The real ASGI app with its lifespan run, so boot decided the tool plan for real.

    ``connected=True`` boots with a user access token present, so the lifespan's own probe computes
    the connected plan instead of the 16 app-token reads. ``create_app`` gives no seam for the user
    credential — it builds the ``OAuthFlow`` itself from ``settings.twitch_oauth_token_file`` and
    injects only ``transport``/``sleep``/``now``/``clock``/``rand`` — so the app is handed the token
    through the same two channels ``wired()`` uses for ``boot_server``, just one step later:

    * the fake transport learns ``GET /oauth2/validate`` (by ``Authorization`` shape, so app and
      user credentials stay distinguishable) and ``POST /oauth2/token``, which the real
      ``AppTokenSource`` mints before it probes; and
    * the app's *own* flow, reached through ``app.state.twitch_wiring.oauth``, is given a
      :class:`~twitch_mcp.oauth.UserToken` (no file write — ``Wiring.boot`` calls
      ``oauth.auth.load()``, which leaves an installed token alone). The app token and the minter
      stay the real ones; the injected bearer is the one the fake transport answers.

    This deliberately runs **before** the lifespan starts: ``Wiring.boot`` reads ``oauth.connected``
    to choose the plan. ``connected=False`` — the default — adds nothing and boots exactly as before,
    which is what the majority of these tests depend on.

    Boot still degrades while the app credential cannot mint, so a test that asserts ``ok`` also has
    to answer ``POST /oauth2/token``; that is the app token's business (``AppTokenSource`` is built
    inside ``create_app``), not this function's.
    """
    settings = settings if settings is not None else server_settings()
    token_dir: Path | None = None

    if connected:
        import json
        import tempfile

        from tests.service_fakes import APP_BEARER, USER_BEARER
        from twitch_mcp.oauth import _FILE_NUMBERS, _FILE_STRINGS, _FILE_VERSION

        async def serve_validate(request: httpx.Request) -> httpx.Response:
            """Answer both halves of the R50 probe, as ``wired()`` does for ``boot_server``."""
            presented = request.headers.get("authorization", "")
            body = VALIDATE_USER if presented == f"Bearer {USER_BEARER}" else VALIDATE_APP
            return httpx.Response(200, json=body)

        async def mint_app_token(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"access_token": APP_BEARER, "expires_in": 15_000})

        fake.add("GET", r"/oauth2/validate", serve_validate)
        fake.add("POST", r"/oauth2/token", mint_app_token)

        # A connected boot has to be a *stored* credential, not an assigned attribute:
        # ``Wiring.boot`` calls ``oauth.auth.load()``, which replaces ``auth.token`` with whatever
        # ``settings.twitch_oauth_token_file`` holds (``None`` when the file is absent). So the token
        # is written to a throwaway file and pointed at through the ordinary settings surface, which
        # is exactly what a real operator's ``scripts/auth.sh`` leaves behind. The credential never
        # leaves this temp directory, and the directory is removed on the way out.
        token_dir = Path(tempfile.mkdtemp(prefix="twitch-mcp-connected-"))
        token_path = token_dir / "twitch_user_token.json"
        token_path.write_bytes(
            json.dumps(
                {
                    "version": _FILE_VERSION,
                    "access_token": USER_BEARER,
                    "refresh_token": "fake-refresh-token-value",
                    "token_type": "Bearer",
                    "scope": " ".join(settings.oauth_scope_list),
                    "user_id": OPERATOR,
                    "user_login": "operator",
                    "expires_at": fclock.unix() + 3600.0,
                    "connected_at": fclock.unix(),
                    "issued_at": fclock.unix(),
                }
            ).encode("utf-8")
        )
        token_path.chmod(0o600)
        # Guard the two private name tuples the file format is defined by, so a format change in
        # src/ fails loudly here instead of booting a silently-disconnected app.
        assert "access_token" in _FILE_STRINGS and "user_id" in _FILE_STRINGS
        assert "expires_at" in _FILE_NUMBERS and "issued_at" in _FILE_NUMBERS

        settings = settings.model_copy()
        object.__setattr__(settings, "twitch_oauth_token_file", str(token_path))

    app = create_app(
        settings,
        transport=fake.transport(),
        sleep=fclock.sleep,
        now=fclock.wall,
        clock=fclock.clock,
        rand=lambda: 0.5,
    )
    try:
        async with app.router.lifespan_context(app):
            yield app
    finally:
        if token_dir is not None:
            import shutil

            shutil.rmtree(token_dir, ignore_errors=True)


def http_client(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{HOST}")


async def rpc(
    client: httpx.AsyncClient, method: str, params: dict[str, Any] | None = None, *, id: int = 1
) -> dict[str, Any]:
    """One raw JSON-RPC POST to ``/mcp`` as a bearer-authenticated loopback request."""
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}},
        headers=MCP_HEADERS,
    )
    assert response.status_code == 200, response.text
    return _jsonrpc_envelope(response.text)


def _jsonrpc_envelope(body: str) -> dict[str, Any]:
    """Accept either a bare JSON response or an SSE body, and return the message."""
    text = body.strip()
    if text.startswith("{"):
        parsed = json.loads(text)
        assert isinstance(parsed, dict)
        return parsed
    for line in text.splitlines():
        if line.startswith("data:"):
            parsed = json.loads(line[len("data:") :].strip())
            assert isinstance(parsed, dict)
            return parsed
    raise AssertionError(f"no JSON-RPC message in response body: {body[:200]}")


# --- one valid argument set per tool --------------------------------------------------------------

READ_ARGS: dict[str, dict[str, Any]] = {
    "twitch_account_status": {},
    "get_users": {"ids": [BROADCASTER]},
    "get_users_follows": {"from_id": BROADCASTER, "first": 200},
    "get_channels": {"broadcaster_ids": [BROADCASTER]},
    "search_channels": {"query": "rust", "first": 100},
    "get_streams": {"user_ids": [BROADCASTER], "first": 100},
    "get_top_games": {"first": 100},
    "get_games": {"ids": [GAME_ID], "first": 100},
    "get_videos": {"user_id": BROADCASTER, "first": 100},
    "get_clips": {"broadcaster_id": BROADCASTER, "first": 100},
    "get_polls": {"broadcaster_id": BROADCASTER, "first": 20},
    "get_predictions": {"broadcaster_id": BROADCASTER, "first": 20},
    "get_emotes": {"broadcaster_id": BROADCASTER},
    "get_channel_chat_settings": {"broadcaster_ids": [BROADCASTER]},
    "get_channel_chat_badges": {"broadcaster_id": BROADCASTER},
    "get_stream_markers": {"broadcaster_id": BROADCASTER, "first": 100},
    "get_broadcaster_subscribers": {"broadcaster_id": BROADCASTER, "first": 20},
    "get_channel_editors": {"broadcaster_id": BROADCASTER},
    "get_moderators": {"broadcaster_id": BROADCASTER, "first": 100},
    "get_banned_users": {"broadcaster_id": BROADCASTER, "first": 100},
    "get_automod_settings": {"broadcaster_id": BROADCASTER},
    "get_chat_chatters": {"broadcaster_id": BROADCASTER, "first": 1000},
    "get_schedule": {"broadcaster_id": BROADCASTER, "first": 100},
    "connect_twitch_account": {},
    "disconnect_twitch_account": {},
}

WRITE_ARGS: dict[str, dict[str, Any]] = {
    "update_channel": {"broadcaster_id": BROADCASTER, "title": "Building in Rust"},
    "create_poll": {
        "broadcaster_id": BROADCASTER,
        "title": "Which language next?",
        "choices": ["Rust", "Go"],
    },
    "end_poll": {"broadcaster_id": BROADCASTER, "poll_id": POLL_ID},
    "create_prediction": {
        "broadcaster_id": BROADCASTER,
        "title": "Will we ship today?",
        "outcomes": ["yes", "no"],
    },
    "end_prediction": {
        "broadcaster_id": BROADCASTER,
        "prediction_id": POLL_ID,
        "status": "CANCELLED",
    },
    "send_chat_message": {
        "broadcaster_id": BROADCASTER,
        "message": "The build is green, shipping now.",
    },
    "timeout_user": {"broadcaster_id": BROADCASTER, "user_id": USER_ID, "duration": 600},
    "remove_timeout": {"broadcaster_id": BROADCASTER, "user_id": USER_ID},
    "ban_user": {"broadcaster_id": BROADCASTER, "user_id": USER_ID},
    "unban_user": {"broadcaster_id": BROADCASTER, "user_id": USER_ID},
    "set_automod_term": {
        "broadcaster_id": BROADCASTER,
        "text": "a-very-rude-word",
        "category": "profanity",
    },
    "remove_automod_term": {
        "broadcaster_id": BROADCASTER,
        "text": "a-very-rude-word",
        "category_none": None,
    },
    "add_vip": {"broadcaster_id": BROADCASTER, "user_id": USER_ID},
    "remove_vip": {"broadcaster_id": BROADCASTER, "user_id": USER_ID},
    "add_moderator": {"broadcaster_id": BROADCASTER, "user_id": USER_ID},
    "remove_moderator": {"broadcaster_id": BROADCASTER, "user_id": USER_ID},
}

#: Every tool's valid arguments, reads and writes together.
TOOL_ARGS: dict[str, dict[str, Any]] = {**READ_ARGS, **WRITE_ARGS}

#: An id-shaped value that is well-formed but not allowlisted (R25 refusals).
NOT_ALLOWED = OTHER_BROADCASTER

__all__ = [
    "ALL_GROUPS",
    "HOST",
    "MCP_HEADERS",
    "NOT_ALLOWED",
    "READ_ARGS",
    "TOOL_ARGS",
    "VALIDATE_APP",
    "VALIDATE_USER",
    "WRITE_ARGS",
    "boot_server",
    "call",
    "http_client",
    "list_tools",
    "plan_for",
    "rpc",
    "running_app",
    "server_settings",
    "tool_names",
    "wired",
]
