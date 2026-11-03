"""R11–R14, R17, R18, R22, R24, R44, R45: the HTTP boundary (the only httpx-using module).

``Settings.twitch_api_base`` already ends in ``/helix``, so a :class:`Route`'s concrete path
starts *after* that prefix (``Route(path="/streams")`` → ``https://api.twitch.tv/helix/streams``).
The frozen :class:`FakeTwitch` matches full request paths, so handler patterns keep ``/helix``.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from tests.core_fakes import APP_ID, APP_TOKEN, USER_TOKEN, make_client
from tests.twitch_fakes import (
    BROADCASTER,
    OTHER_BROADCASTER,
    FakeTwitch,
    helix_headers,
    make_settings,
)
from twitch_mcp.config import Settings
from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.ratelimit import APP_CLASS, USER_CLASS
from twitch_mcp.twitch_client import (
    InvalidArgument,
    Response,
    Route,
    cursor,
    integer,
    login,
    path_segment,
    repeat,
    user_id,
)

USERS = "/users"
STREAMS = "/streams"


def stub(
    fake: FakeTwitch,
    method: str,
    pattern: str,
    body: Any,
    *,
    status: int = 200,
    headers: Any = None,
    times: int | None = None,
) -> None:
    """Register a fixed JSON handler on the frozen FakeTwitch (its add() takes a handler)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, headers=dict(headers or {}))

    fake.add(method, pattern, handler, times)


def _route(path: str = USERS) -> Route:
    return Route(template=path, path=path)


async def test_client_refuses_redirects_and_pins_the_host(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    def redirect(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://evil.example"})

    fake.add("GET", r"/helix/users", redirect)
    client = make_client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError) as exc:
        await client.get(_route(), client_id=APP_ID, bearer=APP_TOKEN)
    assert exc.value.http_status == 302  # the redirect is an error, never a hop (R12)
    req = fake.requests[0]
    assert req.url.host == "api.twitch.tv" and req.url.scheme == "https"  # R12
    assert req.headers["Client-Id"] == APP_ID  # R13
    assert req.headers["Authorization"] == f"Bearer {APP_TOKEN}"


async def test_429_learns_ratelimit_headers_and_is_never_replayed(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    stub(
        fake,
        "GET",
        r"/helix/users",
        {"error": "Too Many Requests", "status": 429},
        status=429,
        headers=helix_headers(30, 0, clock=fclock),
    )
    client = make_client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError) as exc:
        await client.get(_route(), client_id=APP_ID, bearer=APP_TOKEN)
    assert exc.value.kind == "rate_limited"
    assert exc.value.retryable is True and exc.value.retry_after is not None
    assert fake.request_count == 1  # a real 429 is never replayed (R22)


async def test_first_401_trips_its_token_class_circuit(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    stub(fake, "GET", r"/helix/users", {"error": "Unauthorized", "status": 401}, status=401)
    client = make_client(fake, fclock, settings)
    assert client.limiter.auth_failed(APP_CLASS) is False
    with pytest.raises(TwitchMcpError) as exc:
        await client.get(_route(), client_id=APP_ID, bearer=APP_TOKEN)
    assert exc.value.kind == "unauthenticated"
    assert client.limiter.auth_failed(APP_CLASS) is True  # R24 / C3
    assert client.limiter.auth_failed(USER_CLASS) is False
    assert "Restart the container" in (exc.value.hint or "")  # the app-class hint (C3)
    before = fake.request_count
    with pytest.raises(TwitchMcpError) as exc2:
        await client.get(_route(), client_id=APP_ID, bearer=APP_TOKEN)
    assert exc2.value.kind == "unauthenticated"
    assert fake.request_count == before  # the open circuit refuses without a request


async def test_missing_scope_becomes_permission_carrying_twitchs_own_string(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """R45: the scope phrase comes from Twitch's body — nothing here holds a scope table (D10)."""
    stub(
        fake,
        "GET",
        r"/helix/users",
        {
            "error": "Missing scope: user:read:email required for this endpoint",
            "status": 403,
            "scopes": ["chat:read"],
        },
        status=403,
    )
    client = make_client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError) as exc:
        await client.get(_route(), client_id=APP_ID, bearer=APP_TOKEN)
    err = exc.value
    assert err.kind == "permission"
    hint = err.hint or ""
    assert "user:read:email" in hint  # Twitch's own words
    assert "chat:read" in hint  # what the token actually holds


@pytest.mark.parametrize("status", [400, 404, 422])
async def test_kinds_map_from_status_not_message_text(
    fake: FakeTwitch, fclock: Any, settings: Settings, status: int
) -> None:
    stub(
        fake,
        "GET",
        r"/helix/streams",
        {"error": "x", "status": status, "message": "confusing wording about tokens"},
        status=status,
    )
    client = make_client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError) as exc:
        await client.get(_route(STREAMS), client_id=APP_ID, bearer=APP_TOKEN)
    assert exc.value.http_status == status


async def test_oversized_response_is_refused_without_leaking_body(
    fake: FakeTwitch, fclock: Any
) -> None:
    """R14: the body is streamed with a hard byte cap, and a refusal never echoes what it read."""
    settings = make_settings(twitch_max_response_bytes=65_536)  # the minimum the config allows
    big = {"data": [{"x": "y" * 200_000}]}

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=big)

    fake.add("GET", r"/helix/users", handler)
    client = make_client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError) as exc:
        await client.get(_route(), client_id=APP_ID, bearer=APP_TOKEN)
    assert "yyyy" not in str(exc.value)  # R52: the body never rides along
    assert exc.value.kind == "too_large"  # the dedicated kind for R14's byte cap


# --------------------------------------------------------------------------- R17: validation


@pytest.mark.parametrize("bad", ["", "abc", "1" * 21, "12;--", "1.5", "-7", "\u0661\u0662\u0663"])
def test_user_id_validation(bad: str) -> None:
    with pytest.raises(InvalidArgument):
        user_id(bad)


@pytest.mark.parametrize("good", ["123456789", "1"])
def test_user_id_accepts_digits(good: str) -> None:
    assert user_id(good) == good


@pytest.mark.parametrize("bad", ["", "a" * 26, "has space", "has-dash", "has.dot", "\U0001f525"])
def test_login_validation(bad: str) -> None:
    with pytest.raises(InvalidArgument):
        login(bad)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "a/b", "a?b", "a#b", "", "a\\b"])
def test_path_segment_validation(bad: str) -> None:
    with pytest.raises(InvalidArgument):
        path_segment(bad)


def test_route_build_fills_segments_and_keeps_the_template() -> None:
    route = Route.build("/clips/{clip_id}", clip_id="Abc_123")
    assert route.path == "/clips/Abc_123"
    assert route.template == "/clips/{clip_id}"  # limits/logs use the template
    with pytest.raises(InvalidArgument):
        Route.build("/users/{id}", id="1/2")
    with pytest.raises(InvalidArgument):
        Route.build("/users/{id}", other="123")  # unfilled placeholder


def test_cursor_and_integer_validation() -> None:
    assert cursor("eyJhIjoxfQ=") == "eyJhIjoxfQ="
    with pytest.raises(InvalidArgument):
        cursor("bad cursor!")
    assert integer("50") == "50"
    with pytest.raises(InvalidArgument):
        integer("5.5")


# --------------------------------------------------------------------------- R18: repeat list params


async def test_list_parameters_repeat_per_value(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    stub(fake, "GET", r"/helix/users", {"data": [], "pagination": {}})
    client = make_client(fake, fclock, settings)
    await client.get(
        _route(),
        client_id=APP_ID,
        bearer=APP_TOKEN,
        params=repeat("id", [BROADCASTER, OTHER_BROADCASTER]),
    )
    assert fake.requests[0].url.params.get_list("id") == [BROADCASTER, OTHER_BROADCASTER]
    assert "id=%5B" not in str(fake.requests[0].url)  # never JSON-encoded


# --------------------------------------------------------------------------- credential placement


async def test_user_route_sends_user_bearer(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    stub(fake, "POST", r"/helix/chat", {"data": [{"message_id": "m1"}]})
    client = make_client(fake, fclock, settings)
    await client.post(
        Route(template="/chat", path="/chat", token_type="user"),
        client_id=APP_ID,
        bearer=USER_TOKEN,
        json_body={"message": "hi"},
    )
    req = fake.requests[0]
    assert req.headers["Authorization"] == f"Bearer {USER_TOKEN}"
    assert req.headers["Client-Id"] == APP_ID
    assert json.loads(req.content) == {"message": "hi"}


async def test_error_payloads_never_carry_body_text(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    secret_marker = "s3cr3t-channel-text"
    stub(
        fake,
        "GET",
        r"/helix/users",
        {"error": "Bad Request", "status": 400, "message": secret_marker},
        status=400,
    )
    client = make_client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError) as exc:
        await client.get(_route(), client_id=APP_ID, bearer=APP_TOKEN)
    assert secret_marker not in json.dumps(exc.value.to_payload())  # R52


async def test_client_stores_no_credential(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    client = make_client(fake, fclock, settings)
    assert APP_TOKEN not in repr(client.__dict__)
    assert client.client_id == settings.twitch_client_id.get_secret_value()


# ------------------------------------------- R17/R13: the app-token mint is the one anonymous call


async def test_the_app_token_mint_goes_out_without_credential_headers(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """R17/R13: ``/oauth2/token`` produces the credential, so it carries neither credential header.

    The credential itself travels in the form body. Before this was pinned the client refused its
    own mint for having no bearer, which left a real server permanently degraded — a defect no test
    could see because this call had never been exercised.
    """
    fake.add("POST", r"^/oauth2/token$", fake.json({"access_token": APP_TOKEN, "expires_in": 5000}))
    client = make_client(fake, fclock, settings)

    status, body = await client.post_form(
        "/oauth2/token",
        {"grant_type": "client_credentials", "client_id": APP_ID, "client_secret": "s3cret"},
    )

    assert status == 200
    assert body["access_token"] == APP_TOKEN
    assert len(fake.requests) == 1, "R16: the mint is never retried"
    request = fake.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/oauth2/token"
    assert request.headers.get("authorization") is None, "the mint has no bearer to send"
    assert request.headers.get("client-id") is None, "the mint is anonymous at the header level"
    sent = request.content.decode()
    assert "grant_type=client_credentials" in sent
    assert "client_id=" in sent and "client_secret=" in sent, "credentials travel in the form body"


REVOKE = "/oauth2/revoke"
TOKEN = "/oauth2/token"
CLIENT = "fake-client-id-0123456789abcdef"
SECRET = "fake-client-secret"


async def test_revoke_sends_client_id_and_no_headers(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """W6 (R13 exception): revoke carries client id and token in the form, and no header."""
    fake.add("POST", r"^/oauth2/revoke$", fake.json({}, status=200))
    client = make_client(fake, fclock, settings)

    await client.post_form(REVOKE, {"client_id": CLIENT, "token": APP_TOKEN})

    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.headers.get("client-id") is None
    assert request.headers.get("authorization") is None
    sent = parse_qs(request.content.decode())
    assert sent == {"client_id": [CLIENT], "token": [APP_TOKEN]}


async def test_revoke_without_client_id_is_refused_before_the_socket(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """W6: a revoke form with no client id is refused with zero requests recorded."""
    fake.add("POST", r"^/oauth2/revoke$", fake.json({}))
    client = make_client(fake, fclock, settings)

    with pytest.raises(TwitchMcpError) as exc:
        await client.post_form(REVOKE, {"token": APP_TOKEN})

    assert exc.value.kind == "unauthenticated"
    assert fake.requests == [], "refused before the socket"


@pytest.mark.parametrize(
    "keywords",
    [
        {"client_id": CLIENT},
        {"bearer": APP_TOKEN},
        {"client_id": CLIENT, "bearer": APP_TOKEN},
    ],
    ids=["client_id_only", "bearer_only", "both"],
)
async def test_revoke_with_any_keyword_is_refused_before_the_socket(
    fake: FakeTwitch, fclock: Any, settings: Settings, keywords: dict[str, str]
) -> None:
    """R2-1 (W6): revoke never carries Client-Id or Authorization, so any keyword is refused."""
    fake.add("POST", r"^/oauth2/revoke$", fake.json({}))
    client = make_client(fake, fclock, settings)

    with pytest.raises(TwitchMcpError) as exc:
        await client.post_form(REVOKE, {"client_id": CLIENT, "token": APP_TOKEN}, **keywords)

    assert exc.value.kind == "unauthenticated"
    assert fake.requests == [], "refused before the socket"


async def test_token_endpoint_without_keywords_sends_no_headers(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """R13: only the token path may go without credential headers, and only with no keyword."""
    fake.add("POST", r"^/oauth2/token$", fake.json({"access_token": APP_TOKEN}))
    client = make_client(fake, fclock, settings)

    await client.post_form(TOKEN, {"client_id": CLIENT, "client_secret": SECRET})

    assert fake.requests[0].headers.get("authorization") is None


async def test_token_endpoint_with_a_keyword_needs_both_headers(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """R13: passing a keyword on the token path ends the header exception, so both are required."""
    client = make_client(fake, fclock, settings)

    with pytest.raises(TwitchMcpError) as exc:
        await client.post_form(TOKEN, {"client_id": CLIENT}, client_id=CLIENT)

    assert exc.value.kind == "unauthenticated"
    assert fake.requests == [], "refused before the socket (no bearer)"


@pytest.mark.parametrize(
    ("grant", "answer", "status", "kind", "hint_has"),
    [
        # W4: a revoke 400 (message only, a dead token) is unauthenticated, with no sign-in hint.
        ("revoke", {"status": 400, "message": "Invalid token"}, 400, "unauthenticated", None),
        # W4/W12: a token 400 with the documented "Bad Request" body is unauthenticated, sign-in hint.
        (
            "authorization_code",
            {"error": "Bad Request", "status": 400, "message": "Invalid code"},
            400,
            "unauthenticated",
            "connect_twitch_account",
        ),
        # W12: a client_credentials mint 400 points at the env values.
        (
            "client_credentials",
            {"status": 400, "message": "Invalid client secret"},
            400,
            "unauthenticated",
            "TWITCH_CLIENT_SECRET",
        ),
        # W4: a 404 on revoke is a configuration fault, not a dead token.
        ("revoke_404", {"status": 404, "message": "client does not exist"}, 404, "upstream", None),
        # 401 keeps its existing mapping.
        (
            "client_credentials_401",
            {"status": 401, "message": "Unauthorized"},
            401,
            "unauthenticated",
            "TWITCH_CLIENT_SECRET",
        ),
        # W4: a 503 is upstream and retryable.
        ("revoke_503", {"status": 503, "message": "Unavailable"}, 503, "upstream", None),
    ],
    ids=[
        "revoke_400",
        "token_400_bad_request",
        "mint_400",
        "revoke_404",
        "mint_401",
        "revoke_503",
    ],
)
async def test_oauth_status_mapping_and_hints(
    fake: FakeTwitch,
    fclock: Any,
    settings: Settings,
    grant: str,
    answer: dict[str, Any],
    status: int,
    kind: str,
    hint_has: str | None,
) -> None:
    """W4 and W12: the 400 mapping is by status, the hint is chosen by path and grant."""
    path, form = _case_request(grant)
    fake.add("POST", rf"^{path}$", fake.json(answer, status=status))
    client = make_client(fake, fclock, settings)

    with pytest.raises(TwitchMcpError) as exc:
        await client.post_form(path, form)

    assert exc.value.kind == kind
    assert exc.value.http_status == status
    assert len(fake.requests) == 1, "OAuth calls are never retried (R16)"
    if hint_has is None:
        assert exc.value.hint is None or "connect_twitch_account" not in exc.value.hint
    else:
        assert exc.value.hint is not None and hint_has in exc.value.hint
    if status >= 500:
        assert exc.value.retryable is True


def _case_request(grant: str) -> tuple[str, dict[str, str]]:
    """The path and form each status-mapping case sends, by its grant label."""
    if grant.startswith("revoke"):
        return REVOKE, {"client_id": CLIENT, "token": APP_TOKEN}
    if grant == "client_credentials" or grant == "client_credentials_401":
        return TOKEN, {
            "client_id": CLIENT,
            "client_secret": SECRET,
            "grant_type": "client_credentials",
        }
    return TOKEN, {"client_id": CLIENT, "client_secret": SECRET, "grant_type": grant, "code": "c"}


async def test_a_helix_call_missing_only_the_client_id_is_refused_before_the_socket(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """R13's other half: the check is two independent refusals, not one "both missing" case."""
    fake.add("GET", r"^/helix/users$", fake.json({"data": []}))
    client = make_client(fake, fclock, settings)

    with pytest.raises(TwitchMcpError) as exc:
        await client.get(Route(template="/users", path="/users"), client_id="", bearer=APP_TOKEN)

    assert exc.value.kind == "unauthenticated"
    assert fake.requests == [], "refused before the socket, exactly like the bearer-less case"


async def test_the_real_app_token_source_can_mint(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """R17 end to end: the production credential source, no monkeypatching, actually mints.

    This is the path ``/healthz`` and every Helix call depend on. Asserting the bearer comes back
    from the unmodified ``AppTokenSource`` is what keeps the mint from being silently un-wireable
    again; the second call must be served from the cache rather than a second mint (R17).
    """
    from twitch_mcp.server import AppTokenSource

    fake.add(
        "POST",
        r"^/oauth2/token$",
        # A real Twitch app token lives ~60 days; the refresh margin is a day, so a bearer this
        # long-lived must be reused. A short expires_in would (correctly) force a re-mint.
        fake.json({"access_token": APP_TOKEN, "expires_in": 5_184_000}),
        times=2,
    )
    client = make_client(fake, fclock, settings)
    source = AppTokenSource(client, settings)

    assert await source.access_token() == APP_TOKEN
    assert await source.access_token() == APP_TOKEN
    assert len(fake.requests) == 1, "R17: the cached bearer is reused, not re-minted early"


# ------------------------------------------------- R16: a write whose answer is lost stays undetermined


def _refuse(request: httpx.Request) -> httpx.Response:
    """A handler that fails the way a dead socket does, before any answer exists."""
    raise httpx.ConnectError("connection reset", request=request)


async def test_a_write_whose_response_never_arrives_is_undetermined_and_sent_once(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """R16: a write the connection swallowed is *not* retried — a retry could create it twice.

    This is the single most dangerous path in the client. The honest answer is
    ``unknown_outcome``; anything else (a retry, or a confident failure) risks a duplicate
    poll, webhook or moderation action on Twitch.
    """
    fake.add("POST", r"^/helix/polls$", _refuse)
    client = make_client(fake, fclock, settings)

    with pytest.raises(TwitchMcpError) as exc:
        await client.post(
            Route(template="/polls", path="/polls"),
            client_id=APP_ID,
            bearer=APP_TOKEN,
            json_body={"title": "A poll"},
        )

    assert exc.value.kind == "unknown_outcome"
    assert exc.value.retryable is not True, "an undetermined write is never offered as retryable"
    assert len(fake.requests) == 1, "R16: dispatched exactly once, no replay on transport failure"


async def test_a_write_answered_with_an_unreadable_body_is_undetermined(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """R16: 200 plus a body this server cannot parse is still an answered write — report, don't resend."""
    fake.add(
        "POST",
        r"^/helix/polls$",
        lambda request: httpx.Response(
            200, text="not json", headers={"content-type": "text/plain"}
        ),
    )
    client = make_client(fake, fclock, settings)

    with pytest.raises(TwitchMcpError) as exc:
        await client.post(
            Route(template="/polls", path="/polls"),
            client_id=APP_ID,
            bearer=APP_TOKEN,
            json_body={"title": "A poll"},
        )

    assert exc.value.kind == "unknown_outcome"
    assert len(fake.requests) == 1, "the write is not re-sent because its answer was unreadable"


async def test_a_read_that_cannot_reach_twitch_is_a_retryable_upstream(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """R16/R18: a read has no side effect, so a transport failure is reported as retryable upstream."""
    fake.add("GET", r"^/helix/games$", _refuse)
    client = make_client(fake, fclock, settings)

    with pytest.raises(TwitchMcpError) as exc:
        await client.get(
            Route(template="/games", path="/games"),
            client_id=APP_ID,
            bearer=APP_TOKEN,
            params=(("id", "504129"),),
        )

    assert exc.value.kind == "upstream"
    assert exc.value.retryable is True, "reads may be retried by the caller; writes may not"
    assert fake.requests, "the read was actually attempted"


# ------------------------------------------------------- R42/R53: envelope accessors on malformed bodies


def test_envelope_accessors_tolerate_a_body_that_is_not_an_envelope() -> None:
    """R42/R53: a body of the wrong shape yields "no page", "no total", "no template" — never a crash.

    ``pagination == {}`` is the documented signal that there is no further page, so a malformed
    envelope must not be mistaken for one, and no value from it is ever surfaced.
    """
    broken = Response(status=200, body="not a mapping", headers={})
    assert broken.data == []
    assert broken.pagination == {}
    assert broken.total is None
    assert broken.template == {}


def test_envelope_accessors_ignore_fields_of_the_wrong_type() -> None:
    """R42: only well-typed Helix fields are honoured; anything else reads as absent."""
    odd = Response(
        status=200,
        body={"data": "nope", "pagination": "oops", "total": "12", "template": 7},
        headers={},
    )
    assert odd.data == []
    assert odd.pagination == {}
    assert odd.total is None, "a string total is not a total"
    assert odd.template == {}


def test_envelope_accessors_read_the_fields_twitch_publishes() -> None:
    """The positive case, so the two above cannot pass by every branch returning empty."""
    good = Response(
        status=200,
        body={
            "data": [{"id": "1"}],
            "pagination": {"cursor": "next"},
            "total": 7,
            "template": "https://static-cdn.jtvnw.net/emoticons/v2/{id}/default/dark/1.0",
        },
        headers={},
    )
    assert good.data == [{"id": "1"}]
    assert good.pagination == {"cursor": "next"}
    assert good.total == 7
    assert good.template["template"].startswith("https://")


# --------------------------------------------------- R15: reads retry on 5xx, writes never do (R16)


def _server_error(status: int) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return fake_json(request, status)

    return handler


def fake_json(_request: httpx.Request, status: int) -> httpx.Response:
    """A Helix-shaped error answer with Twitch's own numeric ``status`` field (R44)."""
    return httpx.Response(
        status,
        json={"error": "Internal Server Error", "status": status, "message": "boom"},
        headers={"content-type": "application/json"},
    )


async def test_a_5xx_is_retried_up_to_the_documented_bound(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """R15: a read retries 5xx up to TWITCH_MAX_5XX_RETRIES and then reports a retryable upstream.

    Reads have no side effect, so bounded retry is safe and is what the operators were promised;
    the bound is what keeps a Twitch outage from turning into a request storm.
    """
    fake.add("GET", r"^/helix/games$", _server_error(500))
    client = make_client(fake, fclock, make_settings(twitch_max_5xx_retries=2))

    with pytest.raises(TwitchMcpError) as exc:
        await client.get(
            Route(template="/games", path="/games"),
            client_id=APP_ID,
            bearer=APP_TOKEN,
            params=(("id", "504129"),),
        )

    assert exc.value.kind == "upstream"
    assert exc.value.retryable is True
    assert len(fake.requests) == 3, "the first attempt plus two retries, never more"


async def test_a_read_recovers_when_a_retry_succeeds(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """R15 the positive case: a transient 5xx is invisible to the caller once the retry answers."""
    responses: list[httpx.Response] = [
        fake_json(httpx.Request("GET", "https://api.twitch.tv/helix/games"), 500),
        httpx.Response(
            200,
            json={"data": [{"id": "504129", "name": "Games"}]},
            headers={"content-type": "application/json"},
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    fake.add("GET", r"^/helix/games$", handler)
    client = make_client(fake, fclock, settings)

    result = await client.get(
        Route(template="/games", path="/games"),
        client_id=APP_ID,
        bearer=APP_TOKEN,
        params=(("id", "504129"),),
    )

    assert result.status == 200
    assert [record["id"] for record in result.data] == ["504129"]
    assert responses == [], "the second attempt was the one that answered"


# --------------------------------------------------------------------------- C1/C2 (N1, N2)


@pytest.mark.parametrize("status", [401, 403])
async def test_missing_scope_in_message_is_permission_and_opens_no_circuit(
    fake: FakeTwitch, fclock: Any, settings: Settings, status: int
) -> None:
    """C1/C2: Twitch's missing-scope text lives in ``message``; it is a permission, not a 401."""
    stub(
        fake,
        "GET",
        r"/helix/users",
        {"error": "Unauthorized", "status": status, "message": "Missing scope: user:read:email"},
        status=status,
    )
    client = make_client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError) as exc:
        await client.get(_route(), client_id=APP_ID, bearer=APP_TOKEN)
    err = exc.value
    assert err.kind == "permission"
    assert err.retryable is False
    assert "user:read:email" in (err.hint or "")
    assert "holds no scopes" not in (err.hint or "")
    assert client.limiter.auth_failed(APP_CLASS) is False
    assert client.limiter.auth_failed(USER_CLASS) is False
    before = fake.request_count
    with pytest.raises(TwitchMcpError):
        await client.get(_route(), client_id=APP_ID, bearer=APP_TOKEN)
    assert fake.request_count == before + 1, "no circuit: the next call is sent"


# --------------------------------------------------------------------------- C3/C9 (N4, N15)


async def test_user_and_app_circuits_are_isolated_at_the_client(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """N4 / C4 / C5: a non-scope 401 on one class opens only that class's circuit."""
    stub(
        fake,
        "GET",
        r"/helix/streams",
        {"error": "Unauthorized", "status": 401, "message": "Invalid OAuth token"},
        status=401,
    )
    client = make_client(fake, fclock, settings)
    user_route = Route(template=STREAMS, path=STREAMS, token_type=USER_CLASS)
    with pytest.raises(TwitchMcpError) as exc:
        await client.get(user_route, client_id=APP_ID, bearer=USER_TOKEN, token_type=USER_CLASS)
    assert exc.value.kind == "unauthenticated"
    assert client.limiter.auth_failed(USER_CLASS) is True
    assert client.limiter.auth_failed(APP_CLASS) is False
    before = fake.request_count
    await client.get(_route(), client_id=APP_ID, bearer=APP_TOKEN)  # the app read still goes out
    assert fake.request_count == before + 1

    # Mirror: an app-class 401 leaves the user class working.
    stub(
        fake,
        "GET",
        r"/helix/clips",
        {"error": "Unauthorized", "status": 401, "message": "Invalid OAuth token"},
        status=401,
    )
    mirror = make_client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError):
        await mirror.get(
            Route(template="/clips", path="/clips"), client_id=APP_ID, bearer=APP_TOKEN
        )
    assert mirror.limiter.auth_failed(APP_CLASS) is True
    assert mirror.limiter.auth_failed(USER_CLASS) is False
    before = fake.request_count
    with pytest.raises(TwitchMcpError):
        await mirror.get(user_route, client_id=APP_ID, bearer=USER_TOKEN, token_type=USER_CLASS)
    assert fake.request_count == before + 1, "the user read still makes its request"


async def test_ungated_paths_still_send_with_both_circuits_open(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """N15 / C9: with both circuits open, validate and the grant still send a request."""
    stub(
        fake,
        "GET",
        r"/helix/streams",
        {"error": "Unauthorized", "status": 401, "message": "Invalid OAuth token"},
        status=401,
    )
    stub(
        fake,
        "GET",
        r"/helix/clips",
        {"error": "Unauthorized", "status": 401, "message": "Invalid OAuth token"},
        status=401,
    )
    stub(fake, "POST", r"/oauth2/token", {"access_token": "x", "expires_in": 14400})
    fake.set_validate(user_id="1", login="op", scopes=["user:read:email"])
    client = make_client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError):
        await client.get(
            Route(template=STREAMS, path=STREAMS, token_type=USER_CLASS),
            client_id=APP_ID,
            bearer=USER_TOKEN,
            token_type=USER_CLASS,
        )
    with pytest.raises(TwitchMcpError):
        await client.get(
            Route(template="/clips", path="/clips"), client_id=APP_ID, bearer=APP_TOKEN
        )
    assert client.limiter.auth_failed(USER_CLASS) is True
    assert client.limiter.auth_failed(APP_CLASS) is True
    before = fake.request_count
    await client.fetch_validate(USER_TOKEN, token_type=USER_CLASS)
    await client.post_form("/oauth2/token", {"grant_type": "refresh_token"})
    assert fake.request_count == before + 2, "validate and the grant are never refused"

    # Half two: a fresh client with closed circuits; a validate 401 trips nothing and gives no
    # circuit hint.
    fresh_fake = FakeTwitch()
    fresh_fake.add(
        "GET", r"/oauth2/validate", fresh_fake.json({"status": 401, "message": "x"}, 401)
    )
    fresh = make_client(fresh_fake, fclock, settings)
    with pytest.raises(TwitchMcpError) as exc:
        await fresh.fetch_validate(USER_TOKEN, token_type=USER_CLASS)
    assert exc.value.kind == "unauthenticated"
    assert "Restart" not in (exc.value.hint or "") and "connect_twitch_account" not in (
        exc.value.hint or ""
    )
    assert fresh.limiter.auth_failed(USER_CLASS) is False
    assert fresh.limiter.auth_failed(APP_CLASS) is False


# --------------------------------------------------------------------------- C2/C6 (N11, N13, N14)


async def test_scope_hint_drops_control_characters_and_newlines(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """N11 / C2: the hint carries Twitch's wording with control characters and newlines removed."""
    stub(
        fake,
        "GET",
        r"/helix/users",
        {
            "error": "Unauthorized",
            "status": 401,
            "message": "Missing scope:\nuser:read:email\x07 required",
        },
        status=401,
    )
    client = make_client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError) as exc:
        await client.get(_route(), client_id=APP_ID, bearer=APP_TOKEN)
    hint = exc.value.hint or ""
    assert exc.value.kind == "permission"
    assert "user:read:email" in hint
    assert "\n" not in hint and "\x07" not in hint


async def test_non_scope_write_401_is_unknown_outcome_and_trips_only_the_user_class(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """N13 / C6: a write is never re-issued; its refusal carries the R16 hint and no class hint."""
    stub(
        fake,
        "POST",
        r"/helix/chat/messages",
        {"error": "Unauthorized", "status": 401, "message": "Invalid OAuth token"},
        status=401,
    )
    client = make_client(fake, fclock, settings)
    before = fake.request_count
    write_route = Route(template="/chat/messages", path="/chat/messages", token_type=USER_CLASS)
    with pytest.raises(TwitchMcpError) as exc:
        await client.post(write_route, client_id=APP_ID, bearer=USER_TOKEN, json_body={})
    err = exc.value
    assert err.kind == "unknown_outcome"
    assert "Do not re-issue" in (err.hint or "")
    assert "connect_twitch_account" not in (err.hint or "") and "Restart" not in (err.hint or "")
    assert fake.request_count == before + 1
    assert client.limiter.auth_failed(USER_CLASS) is True
    assert client.limiter.auth_failed(APP_CLASS) is False


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (
            500,
            {"error": "x", "status": 500, "message": "Missing scope: user:read:email"},
            "upstream",
        ),
        (
            401,
            {"error": "Unauthorized", "status": 401, "message": "Missing token"},
            "unauthenticated",
        ),
        (403, {"error": "Forbidden", "status": 403, "message": "missing"}, "upstream"),
    ],
)
async def test_message_is_read_only_for_a_scope_phrase_on_401_or_403(
    fake: FakeTwitch, fclock: Any, settings: Settings, status: int, body: Any, kind: str
) -> None:
    """N14 / C1 guard: ``message`` recognises a scope only on 401/403 and only with the word scope."""
    stub(fake, "GET", r"/helix/users", body, status=status)
    client = make_client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError) as exc:
        await client.get(_route(), client_id=APP_ID, bearer=APP_TOKEN)
    assert exc.value.kind == kind


async def test_scope_401_on_a_write_is_permission_and_opens_no_circuit(
    fake: FakeTwitch, fclock: Any, settings: Settings
) -> None:
    """N13 / C6: a missing-scope 401 on a write is a permission, sent once, and trips nothing."""
    stub(
        fake,
        "POST",
        r"/helix/chat/messages",
        {"error": "Unauthorized", "status": 401, "message": "Missing scope: user:write:chat"},
        status=401,
    )
    client = make_client(fake, fclock, settings)
    before = fake.request_count
    write_route = Route(template="/chat/messages", path="/chat/messages", token_type=USER_CLASS)
    with pytest.raises(TwitchMcpError) as exc:
        await client.post(write_route, client_id=APP_ID, bearer=USER_TOKEN, json_body={})
    assert exc.value.kind == "permission"
    assert "user:write:chat" in (exc.value.hint or "")
    assert fake.request_count == before + 1, "a write is dispatched once, never re-issued"
    assert client.limiter.auth_failed(USER_CLASS) is False
    assert client.limiter.auth_failed(APP_CLASS) is False
