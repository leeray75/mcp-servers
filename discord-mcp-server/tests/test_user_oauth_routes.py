"""U8-U15, U27-U28: the loopback sign-in routes, the callback, boot states and log redaction."""

from __future__ import annotations

import asyncio
import logging
import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from starlette.applications import Starlette

from discord_mcp.config import Settings
from discord_mcp.oauth import code_challenge
from discord_mcp.server import create_app
from tests.conftest import FAKE_AUTH_TOKEN, FakeClock, FakeDiscord, make_settings
from tests.user_oauth_fakes import (
    FAKE_SECRET,
    TOKEN_PATH,
    OAuthDiscord,
    form_of,
    on_disk,
    token_body,
    user_settings,
)

ISSUER = "https://discord.com"
REDIRECT = "http://127.0.0.1:8769/oauth/callback"
BEARER = {"Authorization": f"Bearer {FAKE_AUTH_TOKEN}"}
SIGNED_IN = "Discord account connected. You can close this tab and return to your assistant."
FAILED = "Discord sign-in failed. Return to your assistant and call connect_discord_account again."


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
        yield app


def http(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8769"
    )


async def start_flow(h: httpx.AsyncClient) -> tuple[str, str]:
    """Hit /oauth/start and return (state, code_challenge) from the redirect."""
    response = await h.get("/oauth/start")
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    return query["state"][0], query.get("code_challenge", [""])[0]


def token_post(fake: FakeDiscord) -> httpx.Request:
    return next(r for r in fake.requests if r.method == "POST" and r.url.path == TOKEN_PATH)


# ------------------------------------------------------------------ start (U8-U10)


async def test_start_redirects_with_the_exact_authorize_url_and_headers(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        response = await h.get("/oauth/start")  # no bearer: the route is exempt
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://discord.com/oauth2/authorize?response_type=code&client_id=")
    query = parse_qs(urlparse(location).query)
    assert query["client_id"] == ["222222222222222222"]
    assert query["scope"] == ["identify guilds"]
    assert query["redirect_uri"] == [REDIRECT]
    assert query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["state"][0]) == 43
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_start_without_pkce_has_no_challenge(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    settings = user_settings(tmp_path, discord_oauth_pkce=False)
    async with user_app(fake, fclock, settings) as app, http(app) as h:
        response = await h.get("/oauth/start")
    assert "code_challenge" not in response.headers["location"]


async def test_a_new_start_replaces_the_pending_flow(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        old_state, _ = await start_flow(h)
        new_state, _ = await start_flow(h)
        refused = await h.get(
            "/oauth/callback", params={"code": "C", "state": old_state, "iss": ISSUER}
        )
    assert old_state != new_state
    assert refused.status_code == 400
    assert oauth.posts == []


async def test_a_pending_flow_expires_after_ten_minutes(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        state, _ = await start_flow(h)
        fclock.t += 601
        response = await h.get(
            "/oauth/callback", params={"code": "C", "state": state, "iss": ISSUER}
        )
    assert response.status_code == 400
    assert oauth.posts == []


# ------------------------------------------------------------------ callback refusals (U11)


@pytest.mark.parametrize(
    "params",
    [
        {"code": "CODE-1", "state": "WRONG", "iss": ISSUER},
        {"code": "CODE-1", "iss": ISSUER},  # state absent
        {"code": "CODE-1", "state": "{state}", "iss": "https://evil.example"},
        {"code": "CODE-1", "state": "{state}"},  # iss absent
        {"state": "{state}", "iss": ISSUER},  # code absent
        {"code": "CODE-1", "state": "{state}", "iss": ISSUER, "error": "access_denied"},
    ],
)
async def test_a_refused_callback_sends_nothing_and_shows_the_fixed_page(
    tmp_path: Path,
    fake: FakeDiscord,
    fclock: FakeClock,
    oauth: OAuthDiscord,
    params: dict[str, str],
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        state, _ = await start_flow(h)
        query = {key: value.replace("{state}", state) for key, value in params.items()}
        response = await h.get("/oauth/callback", params=query)
        status = (await h.get("/oauth/start")).status_code
    assert response.status_code == 400
    assert FAILED in response.text
    assert "CODE-1" not in response.text and state not in response.text
    assert oauth.posts == []
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["content-security-policy"] == "default-src 'none'"
    assert status == 302


async def test_a_callback_without_any_pending_flow_is_refused(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        response = await h.get("/oauth/callback", params={"code": "C", "state": "S", "iss": ISSUER})
    assert response.status_code == 400
    assert oauth.posts == []


async def test_a_second_callback_with_the_same_state_is_refused(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        state, _ = await start_flow(h)
        first = await h.get("/oauth/callback", params={"code": "C", "state": state, "iss": ISSUER})
        second = await h.get("/oauth/callback", params={"code": "C", "state": state, "iss": ISSUER})
    assert first.status_code == 200
    assert second.status_code == 400
    assert oauth.posts == [TOKEN_PATH]


# ------------------------------------------------------------------ successful sign-in (U12-U14)


async def test_a_full_sign_in_stores_the_token_0600_and_reports_connected(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    settings = user_settings(tmp_path)
    async with user_app(fake, fclock, settings) as app, http(app) as h:
        state, challenge = await start_flow(h)
        done = await h.get(
            "/oauth/callback", params={"code": "CODE-1", "state": state, "iss": ISSUER}
        )
        status = app.state.user_oauth.status()
    assert done.status_code == 200
    assert SIGNED_IN in done.text
    assert "CODE-1" not in done.text and state not in done.text
    exchange = form_of(token_post(fake))
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == "CODE-1"
    assert exchange["redirect_uri"] == REDIRECT
    assert code_challenge(exchange["code_verifier"]) == challenge  # PKCE checked on the wire
    assert status.state == "connected"
    assert status.username == "synthetic-human"
    assert status.scopes == ["identify", "guilds"]
    assert status.last_error is None
    token_file = Path(settings.discord_oauth_token_file)
    assert on_disk(token_file)
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(token_file).st_mode) == 0o600


async def test_without_pkce_the_exchange_has_no_verifier(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    settings = user_settings(tmp_path, discord_oauth_pkce=False)
    async with user_app(fake, fclock, settings) as app, http(app) as h:
        state, _ = await start_flow(h)
        await h.get("/oauth/callback", params={"code": "CODE-1", "state": state, "iss": ISSUER})
    assert "code_verifier" not in form_of(token_post(fake))


async def test_reconnecting_keeps_the_previous_grant_and_replaces_the_token(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        state, _ = await start_flow(h)
        await h.get("/oauth/callback", params={"code": "C1", "state": state, "iss": ISSUER})
        oauth.token_responses = [
            httpx.Response(200, json=token_body(access="ACC-3", refresh="REF-3"))
        ]
        state, _ = await start_flow(h)
        done = await h.get("/oauth/callback", params={"code": "C2", "state": state, "iss": ISSUER})
        status = app.state.user_oauth.status()
    assert done.status_code == 200
    assert "REF-1" not in oauth.revoked  # A3.1 (D29): the previous grant is never revoked
    assert status.state == "connected"


@pytest.mark.parametrize(
    "failure",
    ["missing_scope", "bad_identity", "invalid_grant", "identity_unavailable"],
)
async def test_a_failed_exchange_stores_nothing_and_records_exchange_failed(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord, failure: str
) -> None:
    if failure == "missing_scope":
        oauth.token_responses = [httpx.Response(200, json=token_body(scope="identify"))]
    elif failure == "bad_identity":
        oauth.token_responses = [httpx.Response(200, json=token_body())]
        oauth.identity_body = {"id": 5, "username": "synthetic-human"}  # id is not a string
    elif failure == "invalid_grant":
        oauth.token_responses = [httpx.Response(400, json={"error": "invalid_grant"})]
    else:
        oauth.token_responses = [httpx.Response(200, json=token_body())]
        oauth.me_status = 500
    settings = user_settings(tmp_path)
    async with user_app(fake, fclock, settings) as app, http(app) as h:
        state, _ = await start_flow(h)
        done = await h.get("/oauth/callback", params={"code": "C", "state": state, "iss": ISSUER})
        status = app.state.user_oauth.status()
    assert done.status_code == 400
    assert FAILED in done.text
    assert status.state == "not_connected"
    assert status.last_error == "exchange_failed"
    assert not on_disk(settings.discord_oauth_token_file)


async def test_a_grant_that_cannot_be_stored_is_revoked_and_fails(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    settings = user_settings(
        tmp_path, discord_oauth_token_file=str(tmp_path / "missing" / "t.json")
    )
    async with user_app(fake, fclock, settings) as app, http(app) as h:
        state, _ = await start_flow(h)
        done = await h.get("/oauth/callback", params={"code": "C", "state": state, "iss": ISSUER})
        status = app.state.user_oauth.status()
    assert done.status_code == 400
    assert status.last_error == "exchange_failed"
    assert "REF-1" in oauth.revoked


# ------------------------------------------------------------------ routes, bearer and host (U15)


async def test_the_sign_in_routes_accept_only_get(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        start = await h.post("/oauth/start", headers=BEARER)
        callback = await h.post("/oauth/callback", headers=BEARER)
        no_bearer = await h.post("/oauth/callback")
    assert start.status_code == 405
    assert callback.status_code == 405
    assert no_bearer.status_code == 401  # only GET is exempt from the bearer check


async def test_a_foreign_host_or_origin_is_still_refused_on_the_routes(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        bad_host = await h.get("/oauth/start", headers={"Host": "evil.example"})
        bad_origin = await h.get("/oauth/start", headers={"Origin": "https://evil.example"})
    assert bad_host.status_code == 421
    assert bad_origin.status_code == 403


async def test_with_the_switch_off_the_routes_do_not_exist(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock
) -> None:
    async with user_app(fake, fclock, make_settings()) as app, http(app) as h:
        unauthenticated = await h.get("/oauth/start")
        authenticated = await h.get("/oauth/start", headers=BEARER)
    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 404


async def test_a_mismatched_application_id_turns_the_routes_into_404(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock
) -> None:
    """U4: the routes return 404 when the probed application id differs (no secret is shown)."""
    fake.application["id"] = "999999999999999999"
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        start = await h.get("/oauth/start")
        callback = await h.get("/oauth/callback", params={"code": "C", "state": "S", "iss": ISSUER})
    assert start.status_code == 404
    assert callback.status_code == 404


async def test_healthz_is_identical_with_the_switch_on_and_off(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock
) -> None:
    """U27: /healthz says nothing about the account and is unchanged by the switch."""
    async with user_app(fake, fclock, make_settings()) as app, http(app) as h:
        off = await h.get("/healthz")
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        on = await h.get("/healthz")
    assert off.json() == on.json() == {"status": "ok"}


# ------------------------------------------------------------------ boot line (U28)


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("off", "user_oauth=off"),
        ("not_connected", "user_oauth=on(not_connected)"),
        ("connected", "user_oauth=on(connected)"),
        ("invalid", "user_oauth=on(token_file_invalid)"),
        ("mismatch", "user_oauth=disabled(application_id_mismatch)"),
    ],
)
async def test_the_boot_line_reports_the_user_oauth_state(
    tmp_path: Path,
    fake: FakeDiscord,
    fclock: FakeClock,
    caplog: pytest.LogCaptureFixture,
    setup: str,
    expected: str,
) -> None:
    settings: Settings = make_settings() if setup == "off" else user_settings(tmp_path)
    token_file = Path(settings.discord_oauth_token_file) if setup != "off" else None
    if setup == "connected" and token_file is not None:
        from discord_mcp.oauth import TokenStore, UserAuth
        from tests.test_user_oauth_client import user_token

        UserAuth(TokenStore(token_file), clock=fclock.clock).install(user_token())
    if setup == "invalid" and token_file is not None:
        token_file.write_text("garbage", encoding="utf-8")
    if setup == "mismatch":
        fake.application["id"] = "999999999999999999"
    with caplog.at_level(logging.INFO):
        async with user_app(fake, fclock, settings):
            pass
    lines = [r.getMessage() for r in caplog.records if "boot tools registered" in r.getMessage()]
    assert lines and expected in lines[0]
    assert "222222222222222222" not in lines[0]  # no ids in the boot line


# ------------------------------------------------------------------ redaction (U26, U29)


async def test_no_oauth_secret_appears_in_any_log_record_during_a_flow(
    tmp_path: Path,
    fake: FakeDiscord,
    fclock: FakeClock,
    oauth: OAuthDiscord,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        state, _ = await start_flow(h)
        await h.get("/oauth/callback", params={"code": "CODE-1", "state": state, "iss": ISSUER})
        verifier = form_of(token_post(fake))["code_verifier"]
        await app.state.user_oauth.disconnect()
    server_records = [r for r in caplog.records if not r.name.startswith(("httpx", "httpcore"))]
    blob = "\n".join(r.getMessage() + " " + str(r.__dict__) for r in server_records)
    for secret in (FAKE_SECRET, "ACC-1", "REF-1", "CODE-1", state, verifier):
        assert secret not in blob
    assert any(r.getMessage() == "discord request" for r in server_records)


@pytest.mark.parametrize(
    "failure", ["missing_scope", "identity_unavailable", "bad_identity", "store"]
)
async def test_every_failure_after_the_exchange_revokes_the_new_grant(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord, failure: str
) -> None:
    """A3.1 (U13): one best-effort revoke of the new grant, last_error=exchange_failed."""
    settings = user_settings(tmp_path)
    if failure == "missing_scope":
        oauth.token_responses = [httpx.Response(200, json=token_body(scope="identify"))]
    elif failure == "identity_unavailable":
        oauth.token_responses = [httpx.Response(200, json=token_body())]
        oauth.me_status = 500
    elif failure == "bad_identity":
        oauth.token_responses = [httpx.Response(200, json=token_body())]
        oauth.identity_body = {"id": 5, "username": "synthetic-human"}
    else:
        oauth.token_responses = [httpx.Response(200, json=token_body())]
        settings = user_settings(
            tmp_path, discord_oauth_token_file=str(tmp_path / "missing" / "t.json")
        )
    async with user_app(fake, fclock, settings) as app, http(app) as h:
        state, _ = await start_flow(h)
        done = await h.get("/oauth/callback", params={"code": "C", "state": state, "iss": ISSUER})
        status = app.state.user_oauth.status()
    assert done.status_code == 400
    assert status.last_error == "exchange_failed"
    assert oauth.revoked == ["REF-1"]


async def test_a_malformed_token_response_revokes_the_access_token_it_carries(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """A3.1 (U13): a response without a refresh token is revoked by its access token."""
    body = token_body()
    del body["refresh_token"]
    oauth.token_responses = [httpx.Response(200, json=body)]
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        state, _ = await start_flow(h)
        done = await h.get("/oauth/callback", params={"code": "C", "state": state, "iss": ISSUER})
        status = app.state.user_oauth.status()
    assert done.status_code == 400
    assert status.last_error == "exchange_failed"
    assert oauth.revoked == ["ACC-1"]


async def test_a_refused_exchange_grants_nothing_so_nothing_is_revoked(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    oauth.token_responses = [httpx.Response(400, json={"error": "invalid_grant"})]
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        state, _ = await start_flow(h)
        await h.get("/oauth/callback", params={"code": "C", "state": state, "iss": ISSUER})
    assert oauth.revoked == []


async def test_post_to_a_sign_in_route_without_a_bearer_is_401_and_with_one_405(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """A3.1 (U15): the bearer middleware runs first; only GET is exempt."""
    async with user_app(fake, fclock, user_settings(tmp_path)) as app, http(app) as h:
        without = await h.post("/oauth/start")
        with_bearer = await h.post("/oauth/start", headers=BEARER)
    assert without.status_code == 401
    assert with_bearer.status_code == 405


async def test_the_sign_in_install_waits_for_the_refresh_lock(
    tmp_path: Path, fake: FakeDiscord, fclock: FakeClock, oauth: OAuthDiscord
) -> None:
    """A3.2 round 2 (n3): the callback stores its token only under the refresh lock."""
    settings = user_settings(tmp_path)
    async with user_app(fake, fclock, settings) as app, http(app) as h:
        state, _ = await start_flow(h)
        auth = app.state.user_oauth._auth  # noqa: SLF001 - the lock under test
        await auth.lock.acquire()
        try:
            callback = asyncio.create_task(
                h.get("/oauth/callback", params={"code": "C", "state": state, "iss": ISSUER})
            )
            for _ in range(400):  # the exchange and /users/@me run unlocked; install then waits
                await asyncio.sleep(0.005)
                if "ACC-1" in oauth.bearers:
                    break
            await asyncio.sleep(0.05)
            assert not callback.done()
            assert not on_disk(settings.discord_oauth_token_file)
        finally:
            auth.lock.release()
        done = await callback
        assert done.status_code == 200
        assert on_disk(settings.discord_oauth_token_file)
