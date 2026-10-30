"""R46–R51: OAuth units — state/iss/TTL, 0600 store, refresh under one lock, revoke-only disconnect."""

from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from tests.twitch_fakes import OPERATOR
from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.oauth import (
    PENDING_TTL_SECONDS,
    REFUSED_EXPIRED,
    REFUSED_ISS,
    REFUSED_NO_FLOW,
    REFUSED_STATE,
    OAuthFlow,
    PendingStore,
    TokenFileInvalid,
    TokenStore,
    UserAuth,
    UserToken,
    authorize_url,
    build_token,
    expected_iss,
)

CLIENT_ID = "fake-client-id-0123456789abcdef"
CLIENT_SECRET = "fake-client-secret"
AUTH_BASE = "https://id.twitch.tv"
REDIRECT = "http://127.0.0.1:8770/oauth/callback"


def _token(**over: Any) -> UserToken:
    """A UserToken with SecretStr fields (build_token/save expect SecretStr)."""
    data: dict[str, Any] = {
        "access_token": SecretStr("user-access"),
        "refresh_token": SecretStr("user-refresh"),
        "token_type": "bearer",
        "scope": "user:read:email chat:read",
        "expires_at": 1_000_000_000.0,
        "user_id": OPERATOR,
        "user_login": "operator_login",
        "connected_at": 999_000_000.0,
    }
    data.update(over)
    return UserToken(**data)


# --------------------------------------------------------------------------- R46 / D8: URL, no PKCE


def test_authorize_url_has_state_and_no_pkce_parameters() -> None:
    """D8: no code_challenge/code_verifier ever — Twitch rejects them. D10: no scope table."""
    url = authorize_url(AUTH_BASE, CLIENT_ID, REDIRECT, "st123", ["user:read:email", "chat:read"])
    assert url.startswith("https://id.twitch.tv/oauth2/authorize?")
    assert "response_type=code" in url
    assert "client_id=fake-client-id-0123456789abcdef" in url
    assert "state=st123" in url
    assert "force_verify=true" in url
    assert (
        "scope=user%3Aread%3Aemail%20chat%3Aread" in url
        or "scope=user%3Aread%3Aemail+chat%3Aread" in url
    )
    for forbidden in ("code_challenge", "code_verifier", "nonce"):
        assert forbidden not in url


# --------------------------------------------------------------------------- R47: pending flow


def test_state_is_single_use(fclock: Any) -> None:
    store = PendingStore(clock=fclock.clock)
    flow = store.start(iss=expected_iss(AUTH_BASE), scopes=[])
    assert store.claim(flow.state, flow.iss) is flow
    assert store.claim(flow.state, flow.iss) == REFUSED_NO_FLOW  # single-use


def test_state_mismatch_and_iss_mismatch(fclock: Any) -> None:
    store = PendingStore(clock=fclock.clock)
    flow = store.start(iss=expected_iss(AUTH_BASE), scopes=[])
    assert store.claim("wrong-state", flow.iss) == REFUSED_STATE
    fresh = store.start(iss=expected_iss(AUTH_BASE), scopes=[])
    assert store.claim(fresh.state, "https://evil.example") == REFUSED_ISS


def test_state_expires_after_ten_minutes(fclock: Any) -> None:
    assert PENDING_TTL_SECONDS == 600.0
    store = PendingStore(clock=fclock.clock)
    flow = store.start(iss=expected_iss(AUTH_BASE), scopes=[])
    fclock.advance(PENDING_TTL_SECONDS + 1)
    assert store.claim(flow.state, flow.iss) == REFUSED_EXPIRED


def test_start_replaces_any_previous_flow(fclock: Any) -> None:
    store = PendingStore(clock=fclock.clock)
    first = store.start(iss=expected_iss(AUTH_BASE), scopes=[])
    second = store.start(iss=expected_iss(AUTH_BASE), scopes=[])
    assert first.state != second.state
    # The live flow is the replacement; its state claims succeed once.
    assert store.pending is True
    assert store.claim(second.state, second.iss) is second
    # The replaced first flow is dead: claiming its state now reports no (live) flow.
    assert store.claim(first.state, first.iss) == REFUSED_NO_FLOW


def test_states_are_long_and_random() -> None:
    states = set()
    for _ in range(20):
        store = PendingStore()
        states.add(store.start(iss="https://id.twitch.tv/oauth", scopes=[]).state)
    assert len(states) == 20
    assert all(len(s) >= 16 for s in states)


# --------------------------------------------------------------------------- R48: token store


def test_token_file_is_0600_round_trips_and_degrades(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "token.json")
    store.save(_token())
    assert store.mode() == 0o600
    assert stat.S_IMODE((tmp_path / "token.json").stat().st_mode) == 0o600
    loaded = store.load()
    assert loaded is not None and loaded.access_token.get_secret_value() == "user-access"
    # A corrupt file degrades (TokenFileInvalid; UserAuth.load turns it into a degraded boot).
    (tmp_path / "token.json").write_text("not json {{{", encoding="utf-8")
    with pytest.raises(TokenFileInvalid) as exc:
        store.load()
    assert "not json" not in str(exc.value).lower()  # R52: reason never carries file content


def test_build_token_keeps_previous_refresh_when_omitted() -> None:
    previous = _token()
    renewed = build_token(
        {"access_token": "new", "token_type": "bearer", "expires_in": 14400},
        now=500.0,
        fallback=previous,
    )
    assert renewed.access_token.get_secret_value() == "new"
    assert renewed.refresh_token.get_secret_value() == "user-refresh"  # kept from fallback
    assert renewed.scope == "user:read:email chat:read"


def test_build_token_refuses_malformed_body_without_echoing() -> None:
    with pytest.raises(TwitchMcpError) as exc:
        build_token({"nope": "secret-leak-value"}, now=0.0)
    assert exc.value.kind == "upstream"
    assert "secret-leak-value" not in str(exc.value)


# --------------------------------------------------------------------------- R49: refresh under ONE lock


async def test_concurrent_refreshes_produce_one_token_request(tmp_path: Path, fclock: Any) -> None:
    calls: dict[str, int] = {}

    async def counting(form: Any) -> tuple[int, Any]:
        calls["n"] = calls.get("n", 0) + 1
        await asyncio.sleep(0.02)
        return (200, {"access_token": f"batch-{calls['n']}", "expires_in": 14400})

    auth = UserAuth(TokenStore(tmp_path / "t2.json"), clock=fclock.clock)
    auth.set_credentials(CLIENT_ID, CLIENT_SECRET)
    auth.attach_grant(counting)
    # Expire the token inside the refresh margin on the shared fake timeline.
    auth.install(_token(expires_at=fclock.clock() + 30.0))
    tokens = await asyncio.gather(*(auth.refresh_now(stale="user-access") for _ in range(6)))
    assert len({t.access_token.get_secret_value() for t in tokens}) == 1
    assert calls["n"] == 1  # ONE POST /oauth2/token despite six concurrent callers (R49)


async def test_refresh_rejection_forces_reauth_and_clears_store(
    tmp_path: Path, fclock: Any
) -> None:
    async def reject(form: Any) -> tuple[int, Any]:
        return (400, {"error": "invalid_grant", "status": 400})

    auth = UserAuth(TokenStore(tmp_path / "token.json"), clock=fclock.clock)
    auth.set_credentials(CLIENT_ID, CLIENT_SECRET)
    auth.attach_grant(reject)
    auth.install(_token(expires_at=fclock.clock() + 30.0))
    with pytest.raises(TwitchMcpError) as exc:
        await auth.refresh_now(stale="user-access")
    assert exc.value.kind == "unauthenticated"
    assert auth.token is None
    assert not (tmp_path / "token.json").exists()  # poisoned store removed, never reused


async def test_no_user_token_never_falls_back_to_app_token(tmp_path: Path, fclock: Any) -> None:
    auth = UserAuth(TokenStore(tmp_path / "token.json"), clock=fclock.clock)
    auth.set_credentials(CLIENT_ID, CLIENT_SECRET)
    granted: list[Any] = []

    async def grant(form: Any) -> tuple[int, Any]:
        granted.append(form)
        return (200, {"access_token": "x", "expires_in": 1})

    auth.attach_grant(grant)
    with pytest.raises(TwitchMcpError) as exc:
        await auth.access_token()  # nothing connected
    assert exc.value.kind == "unauthenticated"
    assert granted == []  # not one token request; no app token is ever handed to a user-only tool


# --------------------------------------------------------------------------- R50: boot probe


async def test_probe_degrades_rather_than_crashes(tmp_path: Path, fclock: Any) -> None:
    path = tmp_path / "token.json"
    store = TokenStore(path)
    store.save(_token())
    auth = UserAuth(store, clock=fclock.clock)

    async def rejected(_bearer: str) -> tuple[int, Any]:
        return (401, {"status": 401})

    auth.attach_validate(rejected)
    auth.load()
    assert await auth.probe() == "unauthenticated"  # degraded, not a crash
    assert auth.token is None and not path.exists()

    async def unreachable(_bearer: str) -> tuple[int, Any]:
        return (503, {"status": 503})

    auth2 = UserAuth(TokenStore(tmp_path / "t2.json"), clock=fclock.clock)
    auth2.attach_validate(unreachable)
    auth2.install(_token())
    assert await auth2.probe() == "unreachable"
    assert auth2.token is not None  # a Twitch outage does not log the operator out

    auth3 = UserAuth(TokenStore(tmp_path / "t3.json"), clock=fclock.clock)
    assert await auth3.probe() == "not_connected"  # clean boot without a token file

    (tmp_path / "t4.json").write_text("corrupt{", encoding="utf-8")
    auth4 = UserAuth(TokenStore(tmp_path / "t4.json"), clock=fclock.clock)
    auth4.load()  # must not raise (R50)
    assert auth4.token is None and auth4.token_file_invalid is True


# --------------------------------------------------------------------------- R51: disconnect


async def test_disconnect_revokes_and_touches_nothing_else(tmp_path: Path, fclock: Any) -> None:
    calls: list[dict[str, Any]] = []

    async def revoke(form: Any) -> tuple[int, Any]:
        calls.append(dict(form))
        return (200, {})

    async def validate(_bearer: str) -> tuple[int, Any]:
        raise AssertionError("disconnect must not validate anything")

    auth = UserAuth(TokenStore(tmp_path / "token.json"), clock=fclock.clock)
    auth.attach_revoke(revoke)
    auth.attach_validate(validate)
    auth.install(_token())
    invalidated: list[bool] = []
    auth.on_invalidate(lambda: invalidated.append(True))
    user_id = await auth.disconnect()
    assert user_id == OPERATOR
    assert len(calls) == 1  # exactly one POST /oauth2/revoke (R51)
    assert auth.token is None and not (tmp_path / "token.json").exists()
    assert invalidated == [True]  # identity cache + confirm tokens cleared (R30/R33)


async def test_disconnect_without_connection_is_a_noop(tmp_path: Path, fclock: Any) -> None:
    async def revoke(form: Any) -> tuple[int, Any]:
        raise AssertionError("no revoke without a token")

    auth = UserAuth(TokenStore(tmp_path / "token.json"), clock=fclock.clock)
    auth.attach_revoke(revoke)
    assert await auth.disconnect() is None


# --------------------------------------------------------------------------- OAuthFlow wiring


def _flow(tmp_path: Path, fclock: Any) -> OAuthFlow:
    return OAuthFlow(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        auth_base=AUTH_BASE,
        redirect_uri=REDIRECT,
        sign_in_url="http://127.0.0.1:8770/oauth/start",
        scopes=["user:read:email"],
        token_file=str(tmp_path / "token.json"),
        enabled=True,
        clock=fclock.clock,
    )


async def _noop_revoke(form: Any) -> tuple[int, Any]:
    return (200, {})


_GRANT_OK = {
    "access_token": "a",
    "refresh_token": "r",
    "token_type": "bearer",
    "scope": "user:read:email",
    "expires_in": 14400,
}


async def test_flow_complete_forces_reauth_on_a_refused_grant(tmp_path: Path, fclock: Any) -> None:
    """R48/R49: Twitch refusing the exchange means sign-in again — and no token is kept."""
    flow = _flow(tmp_path, fclock)

    async def grant(form: Any) -> tuple[int, Any]:
        return (400, {"error": "invalid_grant"})

    flow.attach(grant=grant, revoke=_noop_revoke)
    pending = flow.start()

    with pytest.raises(TwitchMcpError) as exc:
        await flow.complete(code="code-1", state=pending.state, iss=pending.iss)

    assert exc.value.kind == "unauthenticated"
    assert exc.value.http_status == 400
    assert flow.connected is False and flow.auth.token is None, "nothing half-installed survives"
    flow.auth.load()
    assert flow.auth.token is None, "nothing half-installed survives a refused grant"


async def test_flow_complete_reports_bad_client_credentials_on_a_401(
    tmp_path: Path, fclock: Any
) -> None:
    """R48: a 401 from the token endpoint is this server's own credentials, not the user's fault."""
    flow = _flow(tmp_path, fclock)

    async def grant(form: Any) -> tuple[int, Any]:
        return (401, {"error": "unauthorized"})

    flow.attach(grant=grant, revoke=_noop_revoke)
    pending = flow.start()

    with pytest.raises(TwitchMcpError) as exc:
        await flow.complete(code="code-1", state=pending.state, iss=pending.iss)

    assert exc.value.kind == "unauthenticated"
    assert exc.value.http_status == 401


async def test_flow_complete_marks_an_oauth_server_error_retryable(
    tmp_path: Path, fclock: Any
) -> None:
    """R44: a 5xx from id.twitch.tv is upstream and retryable — the user should not re-sign-in."""
    flow = _flow(tmp_path, fclock)

    async def grant(form: Any) -> tuple[int, Any]:
        return (503, {"error": "service_unavailable"})

    flow.attach(grant=grant, revoke=_noop_revoke)
    pending = flow.start()

    with pytest.raises(TwitchMcpError) as exc:
        await flow.complete(code="code-1", state=pending.state, iss=pending.iss)

    assert exc.value.kind == "upstream"
    assert exc.value.retryable is True
    assert flow.connected is False


async def test_flow_complete_reports_an_unexpected_oauth_rejection(
    tmp_path: Path, fclock: Any
) -> None:
    """R44: anything else the OAuth service answers is upstream, still carrying its status."""
    flow = _flow(tmp_path, fclock)

    async def grant(form: Any) -> tuple[int, Any]:
        return (403, {"error": "forbidden"})

    flow.attach(grant=grant, revoke=_noop_revoke)
    pending = flow.start()

    with pytest.raises(TwitchMcpError) as exc:
        await flow.complete(code="code-1", state=pending.state, iss=pending.iss)

    assert exc.value.kind == "upstream"
    assert exc.value.http_status == 403


async def test_flow_complete_refuses_a_token_whose_account_it_cannot_identify(
    tmp_path: Path, fclock: Any
) -> None:
    """R29: the token decides whose channel is "own". An unidentified account is never installed."""
    flow = _flow(tmp_path, fclock)

    async def grant(form: Any) -> tuple[int, Any]:
        return (200, dict(_GRANT_OK))  # no user_id: validate has to supply it

    asked: list[str] = []

    async def validate(bearer: str) -> tuple[int, Any]:
        asked.append(bearer)
        return (200, {"scopes": []})  # and it does not

    flow.attach(grant=grant, revoke=_noop_revoke, validate=validate)
    pending = flow.start()

    with pytest.raises(TwitchMcpError) as exc:
        await flow.complete(code="code-1", state=pending.state, iss=pending.iss)

    assert asked, "R29: the grant omitted user_id, so validate is the only authority"
    assert exc.value.kind == "upstream"
    assert flow.connected is False and flow.user_id is None
    assert flow.auth.token is None, "R29: no identity, no connection"


async def test_flow_complete_refuses_a_token_validate_rejects(tmp_path: Path, fclock: Any) -> None:
    """R29/R48: validate refusing the brand-new bearer is an authentication failure, not upstream."""
    flow = _flow(tmp_path, fclock)

    async def grant(form: Any) -> tuple[int, Any]:
        return (200, dict(_GRANT_OK))

    asked: list[str] = []

    async def validate(bearer: str) -> tuple[int, Any]:
        asked.append(bearer)
        return (401, {"status": 401})

    flow.attach(grant=grant, revoke=_noop_revoke, validate=validate)
    pending = flow.start()

    with pytest.raises(TwitchMcpError) as exc:
        await flow.complete(code="code-1", state=pending.state, iss=pending.iss)

    assert asked, "the refusal has to come from validate, not from a malformed grant"
    assert exc.value.kind == "unauthenticated"
    assert flow.connected is False


async def test_flow_complete_refuses_when_no_validate_source_exists(
    tmp_path: Path, fclock: Any
) -> None:
    """R29: with no way to ask Twitch whose token it is, the connection is refused outright."""
    flow = _flow(tmp_path, fclock)

    granted: list[Any] = []

    async def grant(form: Any) -> tuple[int, Any]:
        granted.append(dict(form))
        return (200, dict(_GRANT_OK))

    flow.attach(grant=grant, revoke=_noop_revoke)  # no validate wired
    pending = flow.start()

    with pytest.raises(TwitchMcpError) as exc:
        await flow.complete(code="code-1", state=pending.state, iss=pending.iss)

    assert len(granted) == 1, "the exchange ran; the refusal is about identity, not the grant"
    assert exc.value.kind == "upstream"
    assert flow.connected is False


async def test_flow_complete_refuses_state_mismatch_and_consumes_flow(
    tmp_path: Path, fclock: Any
) -> None:
    flow = _flow(tmp_path, fclock)
    exchanges: list[Any] = []

    async def grant(form: Any) -> tuple[int, Any]:
        exchanges.append(dict(form))
        return (200, {"access_token": "a", "refresh_token": "r", "expires_in": 14400})

    flow.attach(grant=grant, revoke=_noop_revoke)
    pending = flow.start()
    with pytest.raises(TwitchMcpError):
        await flow.complete(code="code-1", state="wrong", iss=pending.iss)
    assert exchanges == []  # no token request on a refused callback
    # Even with the right state now, the flow was consumed by the attempt (single-use):
    with pytest.raises(TwitchMcpError):
        await flow.complete(code="code-1", state=pending.state, iss=pending.iss)
    assert exchanges == []


async def test_flow_complete_happy_path_installs_token(tmp_path: Path, fclock: Any) -> None:
    flow = _flow(tmp_path, fclock)

    async def grant(form: Any) -> tuple[int, Any]:
        assert form["grant_type"] == "authorization_code"
        return (
            200,
            {
                "access_token": "a",
                "refresh_token": "r",
                "token_type": "bearer",
                "scope": "user:read:email",
                "expires_in": 14400,
                "user_id": OPERATOR,
                "login": "operator_login",
            },
        )

    async def validate(bearer: str) -> tuple[int, Any]:
        return (200, {"user_id": OPERATOR, "login": "operator_login", "scopes": []})

    flow.attach(grant=grant, revoke=_noop_revoke, validate=validate)
    pending = flow.start()
    outcome = await flow.complete(code="code-1", state=pending.state, iss=pending.iss)
    assert outcome == OPERATOR  # complete() returns the connected user id (R49)
    assert flow.connected is True
    assert flow.user_id == OPERATOR
    assert flow.auth.token is not None
    assert stat.S_IMODE(flow.token_file.stat().st_mode) == 0o600
