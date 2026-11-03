"""R4/R5/R47 (security): bearer coverage, Host/Origin pinning, and the OAuth exemption.

The bearer token must cover every route except ``GET /healthz``; the two ``/oauth/*`` paths are exempt
**only** when user OAuth is enabled (i.e. only when they are passed in ``open_paths``) and only for
``GET`` (R47). The Host/Origin guard applies to every request including the exempt paths (R5), which is
what makes the exemption safe next to the loopback bind. Both are pure ASGI middleware driven directly
from synthetic scopes here — no server, no network (R55).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import pytest

from tests.twitch_fakes import FAKE_AUTH_TOKEN
from twitch_mcp.security import (
    HEALTH_PATH,
    OAUTH_CALLBACK_PATH,
    OAUTH_PATHS,
    OAUTH_START_PATH,
    BearerAuthMiddleware,
    HostGuardMiddleware,
)

Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
App = Callable[[MutableMapping[str, Any], Receive, Send], Awaitable[None]]


def _app(body: str = "ok") -> tuple[App, list[dict[str, Any]]]:
    """A passthrough app that records the scopes it was handed."""
    seen: list[dict[str, Any]] = []

    async def app(
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        seen.append(dict(scope))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body.encode()})

    return app, seen


def _scope(
    method: str = "POST",
    path: str = "/mcp",
    *,
    host: str = "127.0.0.1:8770",
    origin: str | None = None,
    authorization: str | None = None,
    typ: str = "http",
) -> dict[str, Any]:
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return {"type": typ, "method": method, "path": path, "headers": headers, "query_string": b""}


async def _call(app: App, scope: dict[str, Any]) -> tuple[int | None, list[dict[str, Any]]]:
    """Send one scope through ``app``, returning the response status (if any) and sent messages."""
    sent: list[dict[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(dict(message))

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    await app(scope, receive, send)
    status = next(
        (m["status"] for m in sent if m.get("type") == "http.response.start"),
        None,
    )
    return status, sent


def _auth(token: str = FAKE_AUTH_TOKEN) -> str:
    return f"Bearer {token}"


# --------------------------------------------------------------------------- R4: bearer coverage


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/mcp"),
        ("GET", "/mcp"),
        ("POST", "/mcp"),
        ("DELETE", "/mcp"),
        ("GET", "/sse"),
        ("POST", "/messages"),
        ("POST", "/"),
        ("GET", "/"),
        ("POST", "/oauth/start"),
        ("POST", "/oauth/callback"),
        ("GET", "/some/other/route"),
        ("OPTIONS", "/mcp"),
    ],
)
async def test_every_route_but_health_requires_the_bearer_token(method: str, path: str) -> None:
    """R4: no route other than ``GET /healthz`` is reachable unauthenticated."""
    app, seen = _app()
    middleware = BearerAuthMiddleware(app, FAKE_AUTH_TOKEN)
    status, _ = await _call(middleware, _scope(method, path))
    assert status == 401, f"{method} {path} must require a bearer token"
    assert seen == [], "an unauthorised request must never reach the application"


async def test_health_is_reachable_without_a_token_for_get_and_head() -> None:
    """R4: the liveness probe is the single public path."""
    app, seen = _app("healthy")
    middleware = BearerAuthMiddleware(app, FAKE_AUTH_TOKEN)
    for method in ("GET", "HEAD"):
        status, _ = await _call(middleware, _scope(method, HEALTH_PATH))
        assert status == 200
    assert len(seen) == 2


async def test_health_post_still_requires_a_token() -> None:
    """R4: only the safe methods are exempt; a POST to /healthz is an attack surface."""
    app, seen = _app()
    middleware = BearerAuthMiddleware(app, FAKE_AUTH_TOKEN)
    status, _ = await _call(middleware, _scope("POST", HEALTH_PATH))
    assert status == 401
    assert seen == []


async def test_a_valid_token_reaches_the_application() -> None:
    """R4: the happy path forwards the request untouched."""
    app, seen = _app()
    middleware = BearerAuthMiddleware(app, FAKE_AUTH_TOKEN)
    status, _ = await _call(middleware, _scope("POST", "/mcp", authorization=_auth()))
    assert status == 200
    assert len(seen) == 1


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Basic " + FAKE_AUTH_TOKEN,
        "bearer wrong-token",
        "Bearer " + FAKE_AUTH_TOKEN[:-1] + ("A" if FAKE_AUTH_TOKEN[-1] != "A" else "B"),
        "Bearer " + FAKE_AUTH_TOKEN + "extra",
        FAKE_AUTH_TOKEN,  # missing the scheme
    ],
    ids=[
        "missing",
        "empty",
        "scheme-only",
        "scheme-space-only",
        "wrong-scheme",
        "wrong-token",
        "one-char-off",
        "extra-suffix",
        "no-scheme",
    ],
)
async def test_rejections_are_uniform_401(authorization: str | None) -> None:
    """R4: every bad credential gets the same 401, and never reaches the app."""
    app, seen = _app()
    middleware = BearerAuthMiddleware(app, FAKE_AUTH_TOKEN)
    status, sent = await _call(middleware, _scope("POST", "/mcp", authorization=authorization))
    assert status == 401
    assert seen == []
    body = b"".join(m.get("body", b"") for m in sent if m.get("type") == "http.response.body")
    assert b"unauthorized" in body
    headers = dict(next(m["headers"] for m in sent if m.get("type") == "http.response.start"))
    assert (b"www-authenticate", b"Bearer") in headers.items()


async def test_lowercase_scheme_is_accepted() -> None:
    """The Authorization scheme is case-insensitive (RFC 9110), so a lowercase bearer works."""
    app, _seen = _app()
    middleware = BearerAuthMiddleware(app, FAKE_AUTH_TOKEN)
    status, _ = await _call(
        middleware, _scope("POST", "/mcp", authorization="bearer " + FAKE_AUTH_TOKEN)
    )
    assert status == 200


async def test_surrounding_whitespace_on_the_credential_is_tolerated() -> None:
    """A stray pad around the token is not a credential mismatch (whitespace is stripped before the
    constant-time compare), but the credential itself must still match exactly."""
    app, _seen = _app()
    middleware = BearerAuthMiddleware(app, FAKE_AUTH_TOKEN)
    status, _ = await _call(
        middleware, _scope("POST", "/mcp", authorization="Bearer  " + FAKE_AUTH_TOKEN + " ")
    )
    assert status == 200


# --------------------------------------------------------------------------- R47: OAuth exemption


@pytest.mark.parametrize("path", sorted(OAUTH_PATHS))
async def test_oauth_paths_are_exempt_only_when_opened_and_only_for_get(path: str) -> None:
    """R47: the exemption exists solely when user OAuth is enabled, and only for GET."""
    app, seen = _app()
    # User OAuth OFF: no open paths, so these routes demand a token.
    closed = BearerAuthMiddleware(app, FAKE_AUTH_TOKEN)
    status, _ = await _call(closed, _scope("GET", path))
    assert status == 401, "with user OAuth off, /oauth/* is not exempt"

    # User OAuth ON: the two paths are exempt for GET only.
    opened = BearerAuthMiddleware(app, FAKE_AUTH_TOKEN, open_paths=OAUTH_PATHS)
    status, _ = await _call(opened, _scope("GET", path))
    assert status == 200
    # A POST to the same path still needs the token: the exemption never widens to writes.
    status, _ = await _call(opened, _scope("POST", path))
    assert status == 401
    # And a token still works on the exempt path.
    status, _ = await _call(opened, _scope("GET", path, authorization=_auth()))
    assert status == 200
    assert len(seen) == 2, "only the two exempt GETs reached the app"


async def test_a_stranger_path_is_never_exempted_by_open_paths() -> None:
    """R47: opening the two OAuth paths must not accidentally open anything else."""
    app, _seen = _app()
    middleware = BearerAuthMiddleware(app, FAKE_AUTH_TOKEN, open_paths=OAUTH_PATHS)
    for path in ("/oauth", "/oauth/", "/oauth/start/extra", "/oauthx/start"):
        status, _ = await _call(middleware, _scope("GET", path))
        assert status == 401, f"{path} must not be exempt"


async def test_health_stays_public_even_when_oauth_paths_are_opened() -> None:
    """R4/R47: opening the OAuth set never removes the /healthz exemption — the two are independent."""
    app, _seen = _app("healthy")
    middleware = BearerAuthMiddleware(app, FAKE_AUTH_TOKEN, open_paths=OAUTH_PATHS)
    status, _ = await _call(middleware, _scope("GET", HEALTH_PATH))
    assert status == 200, "the liveness probe is public regardless of the OAuth setting"


async def test_oauth_start_and_callback_are_the_only_openable_paths() -> None:
    """R47 pins the exempt set to exactly the two documented paths."""
    assert frozenset({OAUTH_START_PATH, OAUTH_CALLBACK_PATH}) == OAUTH_PATHS
    assert HEALTH_PATH not in OAUTH_PATHS


async def test_oauth_exempt_paths_are_still_host_guarded() -> None:
    """R5/R47: the exemption is safe because the Host guard still rejects a foreign Host."""
    _bearer_app, bearer_seen = _app()
    chain = HostGuardMiddleware(
        BearerAuthMiddleware(_bearer_app, FAKE_AUTH_TOKEN, open_paths=OAUTH_PATHS)
    )
    status, _ = await _call(chain, _scope("GET", OAUTH_START_PATH, host="evil.example:8770"))
    assert status == 421
    assert bearer_seen == [], "the Host guard rejected it before the bearer layer saw it"

    # With a loopback Host, the exempt GET passes both layers and reaches the application.
    status, _ = await _call(chain, _scope("GET", OAUTH_START_PATH, host="127.0.0.1:8770"))
    assert status == 200
    assert len(bearer_seen) == 1


# --------------------------------------------------------------------------- R5: Host / Origin


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", 200),
        ("127.0.0.1:8770", 200),
        ("localhost", 200),
        ("localhost:8770", 200),
        ("[::1]", 200),
        ("[::1]:8770", 200),
        ("[::1]:1", 200),
        ("127.0.0.1:1", 200),
        ("127.0.0.1:65535", 200),
        ("evil.example", 421),
        ("evil.example:8770", 421),
        ("attacker.127.0.0.1.evil.com", 421),
        ("127.0.0.1.evil.com", 421),
        ("", 421),
        ("127.0.0.1:notaport", 421),
        ("::1", 421),  # bare, unbracketed IPv6 is ambiguous against the allowlist -> rejected
        ("[::1", 421),  # malformed bracket
        ("[::1]x", 421),  # trailing junk after the bracket
    ],
)
async def test_host_guard_is_loopback_only(host: str, expected: int) -> None:
    """R5: DNS-rebinding hosts are rejected with 421; any port on a loopback host is fine."""
    app, _seen = _app()
    middleware = HostGuardMiddleware(app)
    status, _ = await _call(middleware, _scope("POST", "/mcp", host=host))
    assert status == expected


async def test_a_foreign_origin_is_rejected() -> None:
    """R5: a browser Origin that is not loopback is rejected with 403 (DNS-rebinding second check)."""
    app, _seen = _app()
    middleware = HostGuardMiddleware(app)
    status, _ = await _call(
        middleware, _scope("POST", "/mcp", origin="http://evil.example", host="127.0.0.1:8770")
    )
    assert status == 403


@pytest.mark.parametrize(
    "origin",
    [None, "http://127.0.0.1:8770", "http://localhost:8770", "https://localhost"],
)
async def test_loopback_or_absent_origin_passes(origin: str | None) -> None:
    """R5: non-browser MCP clients send no Origin; loopback browsers are allowed."""
    app, _seen = _app()
    middleware = HostGuardMiddleware(app)
    status, _ = await _call(middleware, _scope("POST", "/mcp", origin=origin))
    assert status == 200


async def test_a_non_http_origin_scheme_is_rejected() -> None:
    """R5: only http/https Origins can be allowed."""
    app, _seen = _app()
    middleware = HostGuardMiddleware(app)
    status, _ = await _call(
        middleware, _scope("POST", "/mcp", origin="javascript:alert(1)", host="127.0.0.1:8770")
    )
    assert status == 403


async def test_custom_allowed_hostnames_are_honoured() -> None:
    """The guard takes an explicit allowlist, so a non-loopback bind can still be guarded."""
    app, _seen = _app()
    middleware = HostGuardMiddleware(app, allowed_hostnames={"mcp.internal"})
    status, _ = await _call(middleware, _scope("POST", "/mcp", host="mcp.internal:8770"))
    assert status == 200
    status, _ = await _call(middleware, _scope("POST", "/mcp", host="127.0.0.1:8770"))
    assert status == 421, "the custom list replaces the loopback default"


# --------------------------------------------------------------------------- transport edges


async def test_a_websocket_is_closed_with_policy_violation() -> None:
    """A websocket upgrade is refused outright (code 1008), not left hanging."""
    app, seen = _app()
    middleware = BearerAuthMiddleware(app, FAKE_AUTH_TOKEN)
    status, sent = await _call(middleware, _scope("GET", "/ws", typ="websocket"))
    assert status is None
    assert any(m.get("type") == "websocket.close" and m.get("code") == 1008 for m in sent)
    assert seen == []


async def test_lifespan_passes_through_both_middlewares() -> None:
    """Startup/shutdown events are not HTTP and must reach the app untouched."""
    inner, seen = _app()
    chain = HostGuardMiddleware(BearerAuthMiddleware(inner, FAKE_AUTH_TOKEN))
    scope: dict[str, Any] = {"type": "lifespan", "headers": []}
    await _call(chain, scope)
    assert len(seen) == 1
    assert seen[0]["type"] == "lifespan"


async def test_middleware_order_is_host_then_bearer() -> None:
    """A foreign Host is rejected even with a valid token: the outer guard runs first."""
    app, seen = _app()
    chain = HostGuardMiddleware(BearerAuthMiddleware(app, FAKE_AUTH_TOKEN))
    status, _ = await _call(
        chain, _scope("POST", "/mcp", host="evil.example:8770", authorization=_auth())
    )
    assert status == 421
    assert seen == []


async def test_rejection_carries_no_detail_about_which_check_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R4/R5: rejections are logged by outcome only, with no token or host echo."""
    app, _seen = _app()
    middleware = HostGuardMiddleware(BearerAuthMiddleware(app, FAKE_AUTH_TOKEN))
    with caplog.at_level("WARNING"):
        await _call(middleware, _scope("POST", "/mcp", host="evil.example:8770"))
    assert FAKE_AUTH_TOKEN not in caplog.text
    assert "evil.example" not in caplog.text
