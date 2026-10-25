"""Server-hosted sign-in: /auth and /oauth2callback. Google is fully faked; no network."""

from __future__ import annotations

import base64
import hashlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from google_auth_oauthlib.flow import Flow as RealFlow
from mcp import Client
from starlette.applications import Starlette

from gmail_mcp import signin
from gmail_mcp.auth import read_token
from gmail_mcp.config import SCOPE_COMPOSE, SCOPE_READONLY, Settings
from gmail_mcp.errors import AuthRequiredError, auth_url
from gmail_mcp.gmail_client import GmailClient
from gmail_mcp.server import create_app, create_server
from gmail_mcp.signin import MAX_PENDING, PendingStore, SignInService
from tests.conftest import FAKE_AUTH_TOKEN, FAKE_REFRESH_TOKEN, FakeGmail
from tests.test_server import INITIALIZE, JSONRPC_ACCEPT, serve

SENTINEL_CODE = "4/0SENTINEL-code-0123456789abcdef"
SECURITY_HEADERS = {
    "cache-control": "no-store",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
}


def _as_client(fake: FakeGmail) -> GmailClient:
    return fake  # type: ignore[return-value]  # structural stand-in


@pytest.fixture
def fake() -> FakeGmail:
    return FakeGmail()


@pytest.fixture
async def http(settings: Settings, fake: FakeGmail) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(create_app(settings, _as_client(fake))) as client:
        yield client


async def begin(client: httpx.AsyncClient) -> tuple[str, dict[str, list[str]]]:
    """Call /auth; return (state, query of the Google redirect)."""
    response = await client.get("/auth")
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return query["state"][0], query


class FakeFlow:
    """Stand-in for google_auth_oauthlib's Flow on the callback side."""

    refresh_token: str | None = FAKE_REFRESH_TOKEN
    granted: list[str] | None = [SCOPE_READONLY, SCOPE_COMPOSE]
    raises: BaseException | None = None
    seen_verifiers: list[str | None] = []
    seen_codes: list[str] = []

    def __init__(self, verifier: str | None) -> None:
        self.code_verifier = verifier

    @classmethod
    def from_client_config(cls, config: Any, **kwargs: Any) -> Any:
        if kwargs.get("code_verifier") is None:  # /auth side: the real, offline-capable Flow
            return RealFlow.from_client_config(config, **kwargs)
        cls.seen_verifiers.append(kwargs.get("code_verifier"))
        return cls(kwargs.get("code_verifier"))

    def fetch_token(self, code: str) -> None:
        self.seen_codes.append(code)
        if self.raises is not None:
            raise self.raises

    @property
    def credentials(self) -> Any:
        class Creds:
            refresh_token = FakeFlow.refresh_token
            granted_scopes = FakeFlow.granted
            scopes = None

        return Creds()


@pytest.fixture
def fake_flow(monkeypatch: pytest.MonkeyPatch) -> type[FakeFlow]:
    FakeFlow.refresh_token = FAKE_REFRESH_TOKEN
    FakeFlow.granted = [SCOPE_READONLY, SCOPE_COMPOSE]
    FakeFlow.raises = None
    FakeFlow.seen_verifiers = []
    FakeFlow.seen_codes = []
    monkeypatch.setattr(signin, "Flow", FakeFlow)
    return FakeFlow


def _token_exists(settings: Settings) -> bool:
    return Path(settings.token_path).exists()


def _remove_token(settings: Settings) -> None:
    Path(settings.token_path).unlink()


def _assert_page(response: httpx.Response) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value
    assert response.headers["content-type"].startswith("text/html")
    assert "<script" not in response.text.lower()
    assert "http://" not in response.text


# ------------------------------------------------------------------- /auth


@pytest.mark.parametrize("port", [8765, 9001])
async def test_auth_redirects_to_google_with_state_and_pkce(
    settings: Settings, fake: FakeGmail, port: int
) -> None:
    custom = settings.model_copy(update={"host_port": port})
    async with serve(create_app(custom, _as_client(fake))) as client:
        response = await client.get("/auth")
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert (location.scheme, location.netloc) == ("https", "accounts.google.com")
    query = {k: v[0] for k, v in parse_qs(location.query).items()}
    assert query["redirect_uri"] == f"http://127.0.0.1:{port}/oauth2callback"
    assert query["code_challenge_method"] == "S256"
    assert query["code_challenge"]
    assert query["access_type"] == "offline"
    assert query["prompt"] == "consent"
    assert len(query["state"]) >= 32
    assert set(query["scope"].split()) == {SCOPE_READONLY, SCOPE_COMPOSE}
    assert query["response_type"] == "code"
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


async def test_auth_states_are_unique(http: httpx.AsyncClient) -> None:
    first, _ = await begin(http)
    second, _ = await begin(http)
    assert first != second


async def test_auth_works_with_localhost_host_header(http: httpx.AsyncClient) -> None:
    response = await http.get("/auth", headers={"Host": "localhost:8765"})
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/oauth2callback"]


async def test_head_auth_has_no_side_effect(settings: Settings, fake: FakeGmail) -> None:
    service = SignInService(settings, lambda: None)
    transport = httpx.ASGITransport(app=Starlette(routes=service.routes()))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.head("/auth")
    assert response.status_code == 200
    assert len(service.store) == 0
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


async def test_head_auth_needs_no_bearer(http: httpx.AsyncClient) -> None:
    assert (await http.head("/auth")).status_code == 200


@pytest.mark.parametrize("site", ["cross-site", "same-site", "bogus"])
async def test_cross_site_auth_is_refused(settings: Settings, fake: FakeGmail, site: str) -> None:
    service = SignInService(settings, lambda: None)
    transport = httpx.ASGITransport(app=Starlette(routes=service.routes()))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.get("/auth", headers={"Sec-Fetch-Site": site})
    assert response.status_code == 403
    assert len(service.store) == 0
    _assert_page(response)


_NAV = {"Sec-Fetch-User": "?1", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}


@pytest.mark.parametrize(
    "extra",
    [
        _NAV,
        {"Sec-Fetch-User": "?1", "Sec-Fetch-Mode": "navigate"},  # Dest absent
    ],
)
async def test_user_activated_cross_site_navigation_is_allowed(
    http: httpx.AsyncClient, extra: dict[str, str]
) -> None:
    response = await http.get("/auth", headers={"Sec-Fetch-Site": "cross-site", **extra})
    assert response.status_code == 302


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"},  # no user activation
        {"Sec-Fetch-User": "?1", "Sec-Fetch-Mode": "no-cors"},
        {"Sec-Fetch-User": "?1", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"},
        {"Sec-Fetch-User": "?0", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"},
    ],
)
async def test_scripted_cross_site_requests_stay_refused(
    http: httpx.AsyncClient, extra: dict[str, str]
) -> None:
    response = await http.get("/auth", headers={"Sec-Fetch-Site": "cross-site", **extra})
    assert response.status_code == 403


@pytest.mark.parametrize("site", ["none", "same-origin", None])
async def test_user_initiated_auth_is_allowed(http: httpx.AsyncClient, site: str | None) -> None:
    headers = {} if site is None else {"Sec-Fetch-Site": site}
    response = await http.get("/auth", headers=headers)
    assert response.status_code == 302


# ---------------------------------------------------- middleware interaction


async def test_signin_routes_need_no_bearer_but_mcp_still_does(http: httpx.AsyncClient) -> None:
    assert (await http.get("/auth")).status_code == 302
    assert (await http.get("/oauth2callback")).status_code == 400
    mcp = await http.post("/mcp", json=INITIALIZE, headers=JSONRPC_ACCEPT)
    assert mcp.status_code == 401


@pytest.mark.parametrize(
    "path", ["/authx", "/auth/", "/auth/../mcp", "/oauth2callbackx", "/oauth2callback/", "/AUTH"]
)
async def test_similar_paths_do_not_bypass_bearer(http: httpx.AsyncClient, path: str) -> None:
    assert (await http.get(path)).status_code == 401


@pytest.mark.parametrize("path", ["/auth", "/oauth2callback"])
async def test_post_requires_bearer_and_then_is_405(http: httpx.AsyncClient, path: str) -> None:
    assert (await http.post(path)).status_code == 401
    authed = await http.post(path, headers={"Authorization": f"Bearer {FAKE_AUTH_TOKEN}"})
    assert authed.status_code == 405
    assert authed.headers["allow"] == "GET, HEAD"
    _assert_page(authed)


@pytest.mark.parametrize("path", ["/auth", "/oauth2callback"])
async def test_non_loopback_host_is_421(http: httpx.AsyncClient, path: str) -> None:
    response = await http.get(path, headers={"Host": "evil.example.com:8765"})
    assert response.status_code == 421


async def test_non_loopback_origin_is_403(http: httpx.AsyncClient) -> None:
    response = await http.get("/auth", headers={"Origin": "http://evil.example.com"})
    assert response.status_code == 403


# ---------------------------------------------------------- /oauth2callback


async def test_success_writes_token_and_resets_client(
    http: httpx.AsyncClient, settings: Settings, fake: FakeGmail, fake_flow: type[FakeFlow]
) -> None:
    state, query = await begin(http)
    response = await http.get("/oauth2callback", params={"state": state, "code": SENTINEL_CODE})
    assert response.status_code == 200
    assert "Gmail is connected" in response.text
    _assert_page(response)
    refresh, granted = read_token(Path(settings.token_path))
    assert refresh == FAKE_REFRESH_TOKEN
    assert granted == {SCOPE_READONLY, SCOPE_COMPOSE}
    assert fake.resets == 1
    assert fake_flow.seen_codes == [SENTINEL_CODE]
    # the verifier given to the exchange is the one whose S256 challenge Google received
    verifier = fake_flow.seen_verifiers[0]
    assert verifier
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    assert query["code_challenge"] == [challenge]


async def _assert_rejected(
    http: httpx.AsyncClient,
    settings: Settings,
    fake: FakeGmail,
    params: dict[str, str],
    status: int,
) -> httpx.Response:
    response = await http.get("/oauth2callback", params=params)
    assert response.status_code == status
    assert not _token_exists(settings)
    assert fake.resets == 0
    _assert_page(response)
    return response


async def test_unknown_state(
    http: httpx.AsyncClient, settings: Settings, fake: FakeGmail, fake_flow: type[FakeFlow]
) -> None:
    await _assert_rejected(http, settings, fake, {"state": "nope", "code": "c"}, 400)
    await _assert_rejected(http, settings, fake, {"code": "c"}, 400)
    assert fake_flow.seen_codes == []


async def test_state_is_single_use(
    http: httpx.AsyncClient, settings: Settings, fake: FakeGmail, fake_flow: type[FakeFlow]
) -> None:
    state, _ = await begin(http)
    first = await http.get("/oauth2callback", params={"state": state, "code": "c"})
    assert first.status_code == 200
    fake.resets = 0
    _remove_token(settings)
    await _assert_rejected(http, settings, fake, {"state": state, "code": "c"}, 400)


async def test_state_requires_exact_match(
    http: httpx.AsyncClient, settings: Settings, fake: FakeGmail, fake_flow: type[FakeFlow]
) -> None:
    state, _ = await begin(http)
    await _assert_rejected(http, settings, fake, {"state": state[:-1], "code": "c"}, 400)
    await _assert_rejected(http, settings, fake, {"state": state + "x", "code": "c"}, 400)


async def test_denied_consumes_state_and_writes_nothing(
    http: httpx.AsyncClient, settings: Settings, fake: FakeGmail, fake_flow: type[FakeFlow]
) -> None:
    state, _ = await begin(http)
    response = await _assert_rejected(
        http, settings, fake, {"state": state, "error": "access_denied"}, 400
    )
    assert "access_denied" not in response.text
    await _assert_rejected(http, settings, fake, {"state": state, "code": "c"}, 400)


async def test_error_without_state(
    http: httpx.AsyncClient, settings: Settings, fake: FakeGmail
) -> None:
    await _assert_rejected(http, settings, fake, {"error": "access_denied"}, 400)


async def test_missing_code(
    http: httpx.AsyncClient, settings: Settings, fake: FakeGmail, fake_flow: type[FakeFlow]
) -> None:
    state, _ = await begin(http)
    await _assert_rejected(http, settings, fake, {"state": state}, 400)
    assert fake_flow.seen_codes == []


async def test_no_refresh_token(
    http: httpx.AsyncClient, settings: Settings, fake: FakeGmail, fake_flow: type[FakeFlow]
) -> None:
    fake_flow.refresh_token = None
    state, _ = await begin(http)
    response = await _assert_rejected(http, settings, fake, {"state": state, "code": "c"}, 400)
    assert "myaccount.google.com/permissions" in response.text


@pytest.mark.parametrize("granted", [[SCOPE_READONLY], [], None])
async def test_missing_scope(
    http: httpx.AsyncClient,
    settings: Settings,
    fake: FakeGmail,
    fake_flow: type[FakeFlow],
    granted: list[str] | None,
) -> None:
    fake_flow.granted = granted
    state, _ = await begin(http)
    response = await _assert_rejected(http, settings, fake, {"state": state, "code": "c"}, 400)
    assert "permission" in response.text.lower()


async def test_scope_change_warning_from_oauthlib_means_missing_scope(
    http: httpx.AsyncClient, settings: Settings, fake: FakeGmail, fake_flow: type[FakeFlow]
) -> None:
    fake_flow.raises = Warning("Scope has changed")
    state, _ = await begin(http)
    await _assert_rejected(http, settings, fake, {"state": state, "code": "c"}, 400)


def _record_text(record: logging.LogRecord) -> str:
    return record.getMessage() + " " + " ".join(str(v) for v in record.__dict__.values())


async def test_exchange_failure_is_500_and_leaks_nothing(
    http: httpx.AsyncClient,
    settings: Settings,
    fake: FakeGmail,
    fake_flow: type[FakeFlow],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_flow.raises = RuntimeError(f"boom {SENTINEL_CODE}")
    state, _ = await begin(http)
    with caplog.at_level(logging.INFO):
        response = await _assert_rejected(
            http, settings, fake, {"state": state, "code": SENTINEL_CODE}, 500
        )
    assert SENTINEL_CODE not in response.text
    assert all(SENTINEL_CODE not in _record_text(r) for r in caplog.records)
    assert any(getattr(r, "outcome", "") == "failed:RuntimeError" for r in caplog.records)


async def test_pages_reflect_no_query_values(http: httpx.AsyncClient) -> None:
    marker = "REFLECT-ME-12345"
    for params in ({"error": marker}, {"state": marker}, {"state": marker, "code": marker}):
        response = await http.get("/oauth2callback", params=params)
        assert marker not in response.text
        assert marker not in str(response.headers)


# ----------------------------------------------------------------- logging


async def test_flow_logs_contain_no_code_state_or_token(
    http: httpx.AsyncClient,
    fake_flow: type[FakeFlow],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        state, query = await begin(http)
        await http.get("/oauth2callback", params={"state": state, "code": SENTINEL_CODE})
        await http.get("/oauth2callback", params={"state": state, "code": SENTINEL_CODE})
    records = [r for r in caplog.records if r.name == "gmail_mcp.signin"]
    text = "\n".join(_record_text(r) for r in records)
    for secret in (SENTINEL_CODE, state, FAKE_REFRESH_TOKEN, query["code_challenge"][0]):
        assert secret not in text
    assert [getattr(r, "outcome", None) for r in records] == ["started", "succeeded", "bad_state"]


# --------------------------------------------------- PendingStore and expiry


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_store_is_bounded_and_evicts_the_oldest() -> None:
    store = PendingStore(clock=Clock())
    for i in range(MAX_PENDING + 1):
        store.add(f"s{i}", "v", "r")
    assert len(store) == MAX_PENDING
    assert store.pop("s0") is None
    assert store.pop("s1") is not None
    assert store.pop("s8") is not None


def test_store_entries_expire_after_ten_minutes() -> None:
    clock = Clock()
    store = PendingStore(clock=clock)
    store.add("a", "v", "r")
    clock.now += 599
    store.add("b", "v", "r")
    clock.now += 2  # a is now 601 s old, b is 2 s old
    assert store.pop("a") is None
    assert store.pop("b") is not None
    assert len(store) == 0


async def test_expired_state_is_rejected_over_http(
    settings: Settings, fake_flow: type[FakeFlow]
) -> None:
    clock = Clock()
    service = SignInService(settings, lambda: None, PendingStore(clock=clock))
    transport = httpx.ASGITransport(app=Starlette(routes=service.routes()))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        state, _ = await begin(client)
        clock.now += 601
        response = await client.get("/oauth2callback", params={"state": state, "code": "c"})
    assert response.status_code == 400
    assert not _token_exists(settings)


# ------------------------------------------------------------- error hint


@pytest.mark.parametrize("port", [8765, 9001])
async def test_auth_error_message_contains_the_clickable_link(
    settings: Settings, port: int
) -> None:
    class Boom(FakeGmail):
        async def list_labels(self) -> Any:
            raise AuthRequiredError("Google rejected the stored token.")

    custom = settings.model_copy(update={"host_port": port})
    async with Client(create_server(custom, _as_client(Boom()))) as client:
        result = await client.call_tool("list_labels", {})
    text = result.content[0].text  # type: ignore[union-attr]
    assert auth_url(port) in text
    assert f"http://127.0.0.1:{port}/auth" in text
    assert "rejected the stored token" in text
    # The SDK prefixes tool errors with "Error executing tool <name>: "; R4 text follows it.
    assert "AUTH_REQUIRED: " in text
    assert "scripts/auth" not in text
    assert text.count(f"{auth_url(port)} ") == 1  # the bare, copy-friendly token
