"""Synthetic OAuth and user-account responses for the user-OAuth tests (A3).

Everything here is offline and synthetic: fake client secret, fake tokens, a fake human. The
``OAuthDiscord`` object is attached to the shared ``FakeDiscord`` with ``add`` overrides, and
delegates bot requests to the normal fixtures, so the bot and user sides can be told apart by the
``Authorization`` scheme (``Bot`` versus ``Bearer``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx

from discord_mcp.config import Settings
from tests.conftest import API, FakeDiscord, make_settings

APP_ID = "222222222222222222"
FAKE_SECRET = "FAKE-oauth-client-secret-0000000000"
HUMAN_ID = "444444444444444444"
HUMAN_NAME = "synthetic-human"
TOKEN_PATH = "/api/oauth2/token"
REVOKE_PATH = "/api/oauth2/token/revoke"
USER_GUILDS = f"{API}/users/@me/guilds"
USER_ME = f"{API}/users/@me"


def user_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings with the user OAuth switch on, a fake secret and a token file in ``tmp_path``."""
    data: dict[str, Any] = {
        "discord_user_oauth": True,
        "discord_application_id": APP_ID,
        "discord_oauth_client_secret": FAKE_SECRET,
        "discord_oauth_token_file": str(tmp_path / "discord_user_token.json"),
    }
    data.update(overrides)
    return make_settings(**data)


def token_body(
    access: str = "ACC-1",
    refresh: str = "REF-1",
    scope: str = "identify guilds",
    expires_in: int = 604800,
) -> dict[str, Any]:
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": refresh,
        "scope": scope,
    }


def form_of(request: httpx.Request) -> dict[str, str]:
    return dict(parse_qsl(request.content.decode("utf-8")))


def bearer_of(request: httpx.Request) -> str | None:
    value = request.headers.get("authorization", "")
    return value[len("Bearer ") :] if value.startswith("Bearer ") else None


def is_bot(request: httpx.Request) -> bool:
    return bool(request.headers.get("authorization", "").startswith("Bot "))


class OAuthDiscord:
    """Scripted Discord OAuth and user-account endpoints, attached to a ``FakeDiscord``."""

    def __init__(self, fake: FakeDiscord) -> None:
        self.fake = fake
        self.token_responses: list[httpx.Response] = []  # used in order before the default
        self.refresh_responses: list[httpx.Response] = []  # used for refresh grants first
        self.revoke_status = 200
        self.me_status = 200
        self.identity_body: dict[str, Any] | None = None  # replaces the human identity when set
        self.user_me_status: int | None = None  # status for rejected Bearer tokens when set
        self.rejected_tokens: set[str] = set()  # Bearer tokens answered with user_me_status
        self.guild_status: int | None = None  # status for the human's guild list when set
        self.bot_guilds_status: int | None = None  # status for the bot's guild list when set
        self.human_guilds: list[dict[str, Any]] = []
        self.human_guild_pages: list[list[dict[str, Any]]] | None = None
        self.posts: list[str] = []
        self.revoked: list[str] = []
        self.grants: list[str] = []
        self.bearers: list[str] = []
        self.guild_queries: list[dict[str, str]] = []
        self._next_page: dict[str, int] = {}
        fake.add("POST", TOKEN_PATH, self._token)
        fake.add("POST", REVOKE_PATH, self._revoke)
        fake.add("GET", USER_ME, self._me)
        fake.add("GET", USER_GUILDS, self._guilds)

    def _token(self, request: httpx.Request) -> httpx.Response:
        self.posts.append(TOKEN_PATH)
        form = form_of(request)
        self.grants.append(form.get("grant_type", ""))
        if form.get("grant_type") == "refresh_token" and self.refresh_responses:
            return self.refresh_responses.pop(0)
        if form.get("grant_type") != "refresh_token" and self.token_responses:
            return self.token_responses.pop(0)
        if form.get("grant_type") == "refresh_token":
            return httpx.Response(200, json=token_body(access="ACC-2", refresh="REF-2"))
        return httpx.Response(200, json=token_body())

    def _revoke(self, request: httpx.Request) -> httpx.Response:
        self.posts.append(REVOKE_PATH)
        self.revoked.append(form_of(request).get("token", ""))
        if self.revoke_status != 200:
            return httpx.Response(self.revoke_status, json={"code": 0, "message": "no"})
        return httpx.Response(200)

    def _me(self, request: httpx.Request) -> httpx.Response:
        if is_bot(request):
            return self.fake._default(request)  # noqa: SLF001 - the bot probe and bot calls
        token = bearer_of(request) or ""
        self.bearers.append(token)
        if self.user_me_status is not None and token in self.rejected_tokens:
            return httpx.Response(self.user_me_status, json={"code": 0, "message": "no"})
        if self.identity_body is not None:
            return httpx.Response(200, json=self.identity_body)
        if self.me_status != 200:
            return httpx.Response(self.me_status, json={"code": 0, "message": "no"})
        return httpx.Response(200, json={"id": HUMAN_ID, "username": HUMAN_NAME})

    def _guilds(self, request: httpx.Request) -> httpx.Response:
        if is_bot(request):
            if self.bot_guilds_status is not None:
                return httpx.Response(self.bot_guilds_status, json={"code": 0})
            return self.fake._default(request)  # noqa: SLF001 - the bot's guild list
        token = bearer_of(request) or ""
        self.bearers.append(token)
        self.guild_queries.append(dict(request.url.params))
        if self.guild_status is not None:
            return httpx.Response(self.guild_status, json={"code": 0, "message": "no"})
        pages = self.human_guild_pages or [self.human_guilds]
        after = request.url.params.get("after")
        index = 0 if after is None else self._next_page.get(after, len(pages))
        if index >= len(pages):
            return httpx.Response(200, json=[])
        page = pages[index]
        if page:
            self._next_page[str(page[-1]["id"])] = index + 1
        return httpx.Response(200, json=page)


def human_guild(
    guild_id: str,
    *,
    owner: bool = False,
    permissions: str = "0",
    name: str = "Synthetic Guild",
) -> dict[str, Any]:
    return {
        "id": guild_id,
        "name": name,
        "icon": None,
        "owner": owner,
        "permissions": permissions,
        "features": ["COMMUNITY"],
        "approximate_member_count": 12,
        "approximate_presence_count": 3,
    }


def on_disk(path: str | Path) -> bool:
    """Whether a token file exists (sync helper so async tests do not block on pathlib)."""
    return Path(path).exists()


def write_text_file(path: str | Path, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")
