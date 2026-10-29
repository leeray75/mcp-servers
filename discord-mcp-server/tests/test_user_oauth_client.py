"""U22-U25, U23-U24: the user-side client, its isolation from the bot, and the OAuth POST rules."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from discord_mcp.config import Settings
from discord_mcp.discord_client import DiscordClient, Route, oauth_urls
from discord_mcp.errors import DiscordMcpError
from discord_mcp.oauth import CONNECT_HINT, TokenStore, UserAuth, UserToken
from tests.conftest import FakeClock, FakeDiscord
from tests.user_oauth_fakes import (
    APP_ID,
    FAKE_SECRET,
    HUMAN_ID,
    REVOKE_PATH,
    TOKEN_PATH,
    OAuthDiscord,
    user_settings,
)

ME = Route.build("/users/@me")
FAR = 1_000_000_000.0


def user_token(access: str = "ACC-1", refresh: str = "REF-1", expires_at: float = FAR) -> UserToken:
    return UserToken(
        access_token=SecretStr(access),
        refresh_token=SecretStr(refresh),
        token_type="Bearer",
        scope="identify guilds",
        expires_at=expires_at,
        user_id=HUMAN_ID,
        username="synthetic-human",
        connected_at=1000.0,
    )


def build(
    settings: Settings, fake: FakeDiscord, fclock: FakeClock
) -> tuple[DiscordClient, UserAuth]:
    """A user-side client wired the way ``create_app`` wires it (refresh goes through post_form)."""
    auth = UserAuth(TokenStore(Path(settings.discord_oauth_token_file)), clock=fclock.clock)
    client = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
        user_auth=auth,
    )

    async def refresh(refresh_token: str) -> dict[str, Any]:
        body: dict[str, Any] = await client.post_form(
            "token", {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        return body

    auth.attach_refresh(refresh)
    return client, auth


def bot_client(settings: Settings, fake: FakeDiscord, fclock: FakeClock) -> DiscordClient:
    return DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )


@pytest.fixture
def oauth(fake: FakeDiscord) -> OAuthDiscord:
    return OAuthDiscord(fake)


# ------------------------------------------------------------------ user GET (U23-U24)


async def test_user_get_sends_only_the_bearer_token(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    client, auth = build(user_settings(tmp_path), fake, fclock)
    auth.install(user_token("ACC-1"))
    response = await client.get(ME)
    assert response.body == {"id": HUMAN_ID, "username": "synthetic-human"}
    sent = fake.requests[-1].headers
    assert sent["authorization"] == "Bearer ACC-1"
    assert oauth.bearers == ["ACC-1"]


async def test_a_user_401_refreshes_once_and_replays(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """U24-U25: one serialized refresh, one replay of the GET, and no bot hard-stop."""
    oauth.user_me_status = 401
    oauth.rejected_tokens = {"ACC-1"}
    client, auth = build(user_settings(tmp_path), fake, fclock)
    auth.install(user_token("ACC-1"))
    response = await client.get(ME)
    assert response.body["id"] == HUMAN_ID
    assert oauth.grants == ["refresh_token"]
    assert oauth.bearers == ["ACC-1", "ACC-2"]
    assert auth.token is not None and auth.token.access_token.get_secret_value() == "ACC-2"
    assert client.auth_failed is False


async def test_a_user_401_after_the_refresh_is_auth_with_the_connect_hint(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.user_me_status = 401
    oauth.rejected_tokens = {"ACC-1", "ACC-2"}
    client, auth = build(user_settings(tmp_path), fake, fclock)
    auth.install(user_token("ACC-1"))
    with pytest.raises(DiscordMcpError) as caught:
        await client.get(ME)
    assert caught.value.kind == "auth"
    assert caught.value.hint == CONNECT_HINT
    assert oauth.grants == ["refresh_token"]  # never a second refresh
    assert client.auth_failed is False


async def test_a_user_403_is_permission_not_auth(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.user_me_status = 403
    oauth.rejected_tokens = {"ACC-1"}
    client, auth = build(user_settings(tmp_path), fake, fclock)
    auth.install(user_token("ACC-1"))
    with pytest.raises(DiscordMcpError) as caught:
        await client.get(ME)
    assert caught.value.kind == "permission"


async def test_without_a_connected_account_a_user_get_is_auth_and_sends_nothing(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock
) -> None:
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    before = len(fake.requests)
    with pytest.raises(DiscordMcpError) as caught:
        await client.get(ME)
    assert caught.value.kind == "auth"
    assert len(fake.requests) == before


async def test_an_explicit_token_is_used_as_is_and_never_refreshed(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """The connect flow sends the new token before it is stored (U13); no refresh applies."""
    oauth.user_me_status = 401
    oauth.rejected_tokens = {"NEW"}
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    with pytest.raises(DiscordMcpError) as caught:
        await client.get(ME, access_token="NEW")
    assert caught.value.kind == "auth"
    assert oauth.grants == []
    assert oauth.bearers == ["NEW"]


async def test_a_user_response_over_the_size_cap_is_upstream(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock
) -> None:
    fake.overrides.clear()
    fake.add("GET", r"/api/v10/users/@me", lambda _r: httpx.Response(200, json={"x": "a" * 70_000}))
    client, auth = build(user_settings(tmp_path, discord_max_response_bytes=65_536), fake, fclock)
    auth.install(user_token())
    with pytest.raises(DiscordMcpError) as caught:
        await client.get(ME)
    assert caught.value.kind == "upstream"


async def test_a_bot_hard_stop_never_blocks_the_user_side(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """U23: the bot's hard-stop and the user limiter are isolated in both directions."""
    settings = user_settings(tmp_path)
    bot = bot_client(settings, fake, fclock)
    client, auth = build(settings, fake, fclock)
    assert client.limiter is not bot.limiter
    bot.limiter.trip_auth()
    auth.install(user_token())
    assert (await client.get(ME)).body["id"] == HUMAN_ID
    with pytest.raises(DiscordMcpError) as caught:
        await bot.get(Route.build("/users/@me"))
    assert caught.value.kind == "auth"


# ------------------------------------------------------------------ OAuth POSTs (U22, U24)


def test_only_the_two_oauth_urls_can_receive_a_post() -> None:
    """U22: derived from the scheme and host of DISCORD_API_BASE, and nothing else."""
    assert oauth_urls("https://discord.com/api/v10") == (
        "https://discord.com/api/oauth2/token",
        "https://discord.com/api/oauth2/token/revoke",
    )


async def test_post_form_sends_form_encoded_basic_auth_and_no_bearer(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    body = await client.post_form("token", {"grant_type": "authorization_code", "code": "C"})
    assert body["access_token"] == "ACC-1"
    sent = fake.requests[-1]
    assert sent.method == "POST" and sent.url.path == TOKEN_PATH
    assert sent.headers["content-type"] == "application/x-www-form-urlencoded"
    expected = base64.b64encode(f"{APP_ID}:{FAKE_SECRET}".encode()).decode()
    assert sent.headers["authorization"] == f"Basic {expected}"
    assert "bearer" not in sent.headers["authorization"].lower()


async def test_every_post_targets_only_the_oauth_urls(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    await client.post_form("token", {"grant_type": "authorization_code"})
    await client.post_form("revoke", {"token": "REF-1", "token_type_hint": "refresh_token"})
    posts = {r.url.path for r in fake.requests if r.method == "POST"}
    assert posts == {TOKEN_PATH, REVOKE_PATH}
    assert oauth.posts == [TOKEN_PATH, REVOKE_PATH]


async def test_a_successful_revoke_with_an_empty_body_is_success(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock
) -> None:
    fake.overrides.clear()
    fake.add("POST", REVOKE_PATH, lambda _r: httpx.Response(200))
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    assert await client.post_form("revoke", {"token": "REF-1"}) == {}


async def test_a_post_5xx_is_unavailable_and_never_replayed(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.token_responses = [httpx.Response(500, json={"code": 0})]
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    with pytest.raises(DiscordMcpError) as caught:
        await client.post_form("token", {"grant_type": "authorization_code"})
    assert caught.value.kind == "unavailable"
    assert oauth.posts == [TOKEN_PATH]


async def test_invalid_grant_is_auth_and_the_description_is_never_read(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.token_responses = [
        httpx.Response(400, json={"error": "invalid_grant", "error_description": "secret detail"})
    ]
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    with pytest.raises(DiscordMcpError) as caught:
        await client.post_form("token", {"grant_type": "authorization_code"})
    assert caught.value.kind == "auth"
    assert caught.value.hint == CONNECT_HINT
    assert "secret detail" not in caught.value.message


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"error": "invalid_client"}),
        httpx.Response(401, json={"error": "invalid_client"}),
    ],
)
async def test_other_4xx_on_the_oauth_endpoint_is_upstream(
    tmp_path: Path,
    fake: FakeDiscord,
    fclock: FakeClock,
    oauth: OAuthDiscord,
    response: httpx.Response,
) -> None:
    oauth.token_responses = [response]
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    with pytest.raises(DiscordMcpError) as caught:
        await client.post_form("token", {"grant_type": "authorization_code"})
    assert caught.value.kind == "upstream"


async def test_a_transport_failure_on_post_is_unavailable_without_retry(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock
) -> None:
    calls: list[int] = []

    def boom(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("no route")

    fake.overrides.clear()
    fake.add("POST", TOKEN_PATH, boom)
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    with pytest.raises(DiscordMcpError) as caught:
        await client.post_form("token", {"grant_type": "authorization_code"})
    assert caught.value.kind == "unavailable"
    assert calls == [1]


async def test_a_429_on_post_is_waited_out_and_replayed_once(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.token_responses = [
        httpx.Response(
            429, json={"retry_after": 0.5, "global": False}, headers={"X-RateLimit-Scope": "user"}
        )
    ]
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    body = await client.post_form("token", {"grant_type": "authorization_code"})
    assert body["access_token"] == "ACC-1"
    assert oauth.posts == [TOKEN_PATH, TOKEN_PATH]


async def test_a_429_that_cannot_be_replayed_is_rate_limited(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.token_responses = [
        httpx.Response(429, json={"retry_after": 999.0}, headers={"X-RateLimit-Scope": "user"})
    ]
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    with pytest.raises(DiscordMcpError) as caught:
        await client.post_form("token", {"grant_type": "authorization_code"})
    assert caught.value.kind == "rate_limited"
    assert caught.value.retry_after == 999.0


async def test_a_non_object_success_body_is_upstream(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.token_responses = [httpx.Response(200, json=[1, 2])]
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    with pytest.raises(DiscordMcpError) as caught:
        await client.post_form("token", {"grant_type": "authorization_code"})
    assert caught.value.kind == "upstream"


async def test_a_bot_client_cannot_post(fake: FakeDiscord, fclock: FakeClock) -> None:
    """Only the user-side instance may POST at all (U22)."""
    settings = user_settings(Path("/unused-but-valid"))
    with pytest.raises(DiscordMcpError) as caught:
        await bot_client(settings, fake, fclock).post_form("token", {"grant_type": "x"})
    assert caught.value.kind == "upstream"
    assert fake.requests == []


async def test_a_post_with_a_server_error_body_keeps_the_error_code_only(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.token_responses = [
        httpx.Response(418, json={"error": "teapot", "error_description": "x"})
    ]
    client, _auth = build(user_settings(tmp_path), fake, fclock)
    with pytest.raises(DiscordMcpError) as caught:
        await client.post_form("token", {"grant_type": "authorization_code"})
    assert caught.value.kind == "upstream"
