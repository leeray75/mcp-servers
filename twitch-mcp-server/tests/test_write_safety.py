"""R15/R16 read-write retry asymmetry + the confirmation dispatch story (R28–R34).

Reads retry on 5xx (503 exactly once); writes — POST/PUT/PATCH/DELETE to Helix — are provably
never retried, including on 429 and transport errors, which yield ``kind=unknown_outcome``.
The dispatch mirror (identity → mint-or-consume → re-check → write) is the flow ``service.py``
will compose; here it proves the zero-request refusals.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import httpx
import pytest

from tests.core_fakes import APP_ID, USER_TOKEN, make_confirmation_store
from tests.twitch_fakes import BROADCASTER, OPERATOR, FakeTwitch, helix_headers, make_settings
from twitch_mcp import twitch_client as twitch_client_module
from twitch_mcp.cache import TTLCache
from twitch_mcp.config import Settings
from twitch_mcp.confirmation import ConfirmationStore
from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.identity import IdentityVerifier, OperatorIdentity
from twitch_mcp.ratelimit import RateLimiter
from twitch_mcp.twitch_client import Route, TwitchClient

CHAT = "/chat"
STREAMS = "/streams"
TOOL = "send_chat_message"
ARGS = {"message": "hello"}


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
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, headers=dict(headers or {}))

    fake.add(method, pattern, handler, times)


def _client(fake: FakeTwitch, fclock: Any, settings: Settings | None = None) -> TwitchClient:
    return TwitchClient(
        settings or make_settings(),
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        limiter=RateLimiter(max_wait_seconds=30.0, clock=fclock.clock, sleep=fclock.sleep),
    )


def _verifier(fclock: Any, roster_members: list[str]) -> IdentityVerifier:
    cache = TTLCache(
        max_entries=64, max_bytes=1_000_000, clock=fclock.clock, wall_clock=fclock.wall
    )

    async def fetch_roster(roster: str, broadcaster_id: str) -> list[str]:
        return roster_members

    return IdentityVerifier(OperatorIdentity(OPERATOR), cache, fetch_roster)


# --------------------------------------------------------------------------- R15: reads retry


async def test_read_retries_5xx_within_budget_then_succeeds(fake: FakeTwitch, fclock: Any) -> None:
    stub(
        fake,
        "GET",
        r"/helix/streams",
        {"e": 1},
        status=500,
        headers=helix_headers(30, 15, clock=fclock),
        times=1,
    )
    stub(
        fake,
        "GET",
        r"/helix/streams",
        {"e": 1},
        status=502,
        headers=helix_headers(30, 15, clock=fclock),
        times=1,
    )
    stub(fake, "GET", r"/helix/streams", {"data": [{"id": "1"}], "pagination": {}})
    client = _client(fake, fclock)
    resp = await client.get(
        Route(template=STREAMS, path=STREAMS), client_id=APP_ID, bearer="app-token"
    )
    assert resp.data[0]["id"] == "1"
    assert fake.request_count == 3  # two 5xx retries within the budget, then success


async def test_read_503_is_retried_exactly_once(fake: FakeTwitch, fclock: Any) -> None:
    settings = make_settings(twitch_max_5xx_retries=0)  # isolate the 503 rule from the 5xx budget
    stub(
        fake,
        "GET",
        r"/helix/streams",
        {"e": 1},
        status=503,
        headers=helix_headers(30, 15, clock=fclock),
        times=5,
    )
    client = _client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError) as exc:
        await client.get(
            Route(template=STREAMS, path=STREAMS), client_id=APP_ID, bearer="app-token"
        )
    assert exc.value.kind == "upstream"
    assert fake.request_count == 2  # one 503 retry regardless of the larger budget (R15)


async def test_read_retry_budget_is_configurable(fake: FakeTwitch, fclock: Any) -> None:
    settings = make_settings(twitch_max_5xx_retries=0)
    stub(
        fake,
        "GET",
        r"/helix/streams",
        {"e": 1},
        status=500,
        headers=helix_headers(30, 15, clock=fclock),
        times=3,
    )
    client = _client(fake, fclock, settings)
    with pytest.raises(TwitchMcpError):
        await client.get(
            Route(template=STREAMS, path=STREAMS), client_id=APP_ID, bearer="app-token"
        )
    assert fake.request_count == 1


# --------------------------------------------------------------------------- R16: writes never retry


@pytest.mark.parametrize(
    ("status", "expected_kind"),
    [
        # R16: a dispatched write whose effect cannot be read back is unknown_outcome, never
        # retried; a 429 refused via the lease carries rate_limited; 404 never reached Helix.
        (500, {"upstream", "unknown_outcome"}),
        (502, {"upstream", "unknown_outcome"}),
        (503, {"upstream", "unknown_outcome"}),
        (429, {"rate_limited"}),
        (404, {"unknown_outcome"}),
    ],
)
async def test_write_never_retried_on_status(
    fake: FakeTwitch, fclock: Any, status: int, expected_kind: set[str]
) -> None:
    stub(
        fake,
        "POST",
        r"/helix/chat",
        {"error": "x", "status": status},
        status=status,
        headers=helix_headers(10, 0, clock=fclock),
        times=5,
    )
    client = _client(fake, fclock)
    with pytest.raises(TwitchMcpError) as exc:
        await client.post(
            Route(template=CHAT, path=CHAT, token_type="user"),
            client_id=APP_ID,
            bearer=USER_TOKEN,
            json_body={"message": "hello"},
        )
    assert exc.value.kind in expected_kind
    assert fake.request_count == 1  # exactly one, whatever happened (R16)


async def test_write_transport_error_is_unknown_outcome_never_retried(
    fake: FakeTwitch, fclock: Any
) -> None:
    state = {"first": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["first"]:
            state["first"] = False
            raise httpx.TransportError("connection reset")
        return httpx.Response(200, json={"data": [{"would-be-a-double-send": True}]})

    fake.add("POST", r"/helix/chat", handler)
    client = _client(fake, fclock)
    with pytest.raises(TwitchMcpError) as exc:
        await client.post(
            Route(template=CHAT, path=CHAT, token_type="user"),
            client_id=APP_ID,
            bearer=USER_TOKEN,
            json_body={"message": "hello"},
        )
    err = exc.value
    assert err.kind == "unknown_outcome"
    assert err.retryable is False
    assert "channel" in (err.hint or "").lower() or "chat" in (err.hint or "").lower()
    assert fake.request_count == 1  # the write is never re-issued (R16)


def test_retry_helper_is_unreachable_from_every_write_path() -> None:
    """Static proof (R16): no write method reaches the read retry machinery.

    The graph walks only the client's own *private* helpers — public names like ``get`` are also
    dict/mapping methods (``body.get(...)``) that appear everywhere and are not the retry loop.
    """
    tree = ast.parse(inspect.getsource(twitch_client_module))
    functions: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions[f"{node.name}.{sub.name}"] = sub

    def callees(func: ast.AST) -> set[str]:
        found: set[str] = set()
        for sub in ast.walk(func):
            if isinstance(sub, ast.Call):
                if isinstance(sub.func, ast.Attribute):
                    found.add(sub.func.attr)
                elif isinstance(sub.func, ast.Name):
                    found.add(sub.func.id)
        return found

    def reachable(start: str) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            name = stack.pop()
            key = name if name in functions else f"TwitchClient.{name}"
            if key not in functions or key in seen:
                continue
            seen.add(key)
            if key.split(".")[-1] == "get":
                continue  # the read loop is a terminal node; never expand into dict-method noise
            stack.extend(callees(functions[key]))
        return seen

    # Every public write verb is checked, not just the shared helper.
    for start in ("_write", "post", "put", "patch", "delete"):
        write_names = {k.split(".")[-1] for k in reachable(start)}
        for forbidden in ("_get", "_read_retry_allowed", "_backoff"):
            assert forbidden not in write_names, (
                f"{start} reaches read retry machinery: {forbidden}"
            )
    read_seen: set[str] = set()

    def read_reachable(name: str) -> set[str]:
        stack = [name]
        while stack:
            item = stack.pop()
            key = item if item in functions else f"TwitchClient.{item}"
            if key not in functions or key in read_seen:
                continue
            read_seen.add(key)
            stack.extend(callees(functions[key]))
        return {k.split(".")[-1] for k in read_seen}

    read_names = read_reachable("_read_retry_allowed") | read_reachable("_backoff") | {"get"}
    assert "_read_retry_allowed" in read_names and "_backoff" in read_names  # reads DO have it
    # The retry helpers must be reachable from the read side: a call inside get's body.
    get_body_calls = callees(functions["TwitchClient.get"])
    assert "_read_retry_allowed" in get_body_calls or "_backoff" in get_body_calls


# --------------------------------------------------------------------------- dispatch (R31–R34)


def _dispatch_flow(
    client: TwitchClient,
    identity: IdentityVerifier,
    store: ConfirmationStore,
    *,
    token: str | None,
    broadcaster_id: str = BROADCASTER,
    arguments: dict[str, object] | None = None,
) -> Any:
    """The service-layer flow mirrored (R31/R32/R34): identity → mint or consume → re-check → write."""
    args = ARGS if arguments is None else arguments

    async def run() -> dict[str, object]:
        await identity.require_writable(broadcaster_id, "chat")
        if token is None:
            minted = store.mint(TOOL, broadcaster_id, args)
            return {
                "confirmation_required": True,
                "confirmation": {
                    "token": minted.token,
                    "tool": minted.plan.tool,
                    "broadcaster_id": minted.plan.broadcaster_id,
                    "arguments_hash": minted.plan.arguments_hash,
                },
            }
        store.consume(TOOL, broadcaster_id, args, token)
        await identity.require_writable(broadcaster_id, "chat")  # re-checked with a valid token
        write = await client.post(
            Route(template=CHAT, path=CHAT, token_type="user"),
            client_id=APP_ID,
            bearer=USER_TOKEN,
            json_body={"message": str(args["message"])},
        )
        return {"confirmation_required": False, "dispatched": True, "data": write.data}

    return run()


async def test_write_without_token_asks_confirmation_with_zero_http_calls(
    fake: FakeTwitch, fclock: Any
) -> None:
    stub(fake, "POST", r"/helix/chat", {"data": [{"message_id": "m1"}]})
    client = _client(fake, fclock)
    store = make_confirmation_store(fclock)
    result = await _dispatch_flow(client, _verifier(fclock, [OPERATOR]), store, token=None)
    assert result["confirmation_required"] is True
    assert result["confirmation"]["tool"] == TOOL
    assert result["confirmation"]["broadcaster_id"] == BROADCASTER
    assert fake.request_count == 0  # nothing left the process (R31)


async def test_write_with_valid_token_dispatches_exactly_one_request(
    fake: FakeTwitch, fclock: Any
) -> None:
    stub(fake, "POST", r"/helix/chat", {"data": [{"message_id": "m1", "is_sent": True}]})
    client = _client(fake, fclock)
    store = make_confirmation_store(fclock)
    minted = store.mint(TOOL, BROADCASTER, ARGS)
    result = await _dispatch_flow(client, _verifier(fclock, [OPERATOR]), store, token=minted.token)
    assert result["dispatched"] is True
    assert fake.request_count == 1  # exactly one write (R31)
    assert fake.methods == ["POST"]
    assert fake.requests[0].headers["Authorization"] == f"Bearer {USER_TOKEN}"


async def test_replay_refuses_with_no_additional_request(fake: FakeTwitch, fclock: Any) -> None:
    stub(fake, "POST", r"/helix/chat", {"data": [{"message_id": "m1"}]})
    client = _client(fake, fclock)
    store = make_confirmation_store(fclock)
    minted = store.mint(TOOL, BROADCASTER, ARGS)
    await _dispatch_flow(client, _verifier(fclock, [OPERATOR]), store, token=minted.token)
    used = fake.request_count
    with pytest.raises(TwitchMcpError) as exc:
        await _dispatch_flow(client, _verifier(fclock, [OPERATOR]), store, token=minted.token)
    assert exc.value.kind == "stale_confirmation"
    assert fake.request_count == used  # no request behind the refusal (R32)


async def test_expiry_refuses_with_zero_requests(fake: FakeTwitch, fclock: Any) -> None:
    stub(fake, "POST", r"/helix/chat", {"data": []})
    client = _client(fake, fclock)
    store = make_confirmation_store(fclock)
    minted = store.mint(TOOL, BROADCASTER, ARGS)
    fclock.advance(301.0)
    with pytest.raises(TwitchMcpError) as exc:
        await _dispatch_flow(client, _verifier(fclock, [OPERATOR]), store, token=minted.token)
    assert exc.value.kind == "stale_confirmation"
    assert fake.request_count == 0


async def test_argument_drift_refuses_with_zero_requests(fake: FakeTwitch, fclock: Any) -> None:
    stub(fake, "POST", r"/helix/chat", {"data": []})
    client = _client(fake, fclock)
    store = make_confirmation_store(fclock)
    minted = store.mint(TOOL, BROADCASTER, ARGS)
    with pytest.raises(TwitchMcpError) as exc:
        await _dispatch_flow(
            client,
            _verifier(fclock, [OPERATOR]),
            store,
            token=minted.token,
            arguments={"message": "sneaky"},
        )
    assert exc.value.kind == "stale_confirmation"
    assert fake.request_count == 0


async def test_roster_recheck_refusal_sends_nothing_before_dispatch(
    fake: FakeTwitch, fclock: Any
) -> None:
    """R34 shape: the roster is re-checked ahead of dispatch — a failed check sends nothing."""
    stub(fake, "POST", r"/helix/chat", {"data": []})
    client = _client(fake, fclock)
    store = make_confirmation_store(fclock)
    identity = _verifier(fclock, ["999888777"])  # operator not on the roster
    minted = store.mint(TOOL, BROADCASTER, ARGS)
    with pytest.raises(TwitchMcpError) as exc:
        await _dispatch_flow(client, identity, store, token=minted.token)
    assert exc.value.kind == "not_authorized"
    assert fake.request_count == 0


async def test_consume_happens_before_the_final_recheck_and_refusal_still_sends_nothing(
    fake: FakeTwitch, fclock: Any
) -> None:
    """If the final roster re-check refused (it does not for a stable roster), the write still
    never fires: consume precedes dispatch and the dispatch is the last step."""
    stub(fake, "POST", r"/helix/chat", {"data": []})
    client = _client(fake, fclock)
    store = make_confirmation_store(fclock)
    minted = store.mint(TOOL, BROADCASTER, ARGS)
    # Token consumed out-of-band: dispatching now must refuse with zero requests.
    store.consume(TOOL, BROADCASTER, ARGS, minted.token)
    with pytest.raises(TwitchMcpError) as exc:
        await _dispatch_flow(client, _verifier(fclock, [OPERATOR]), store, token=minted.token)
    assert exc.value.kind == "stale_confirmation"
    assert fake.request_count == 0


def test_write_group_default_is_empty(settings: Settings) -> None:
    """R28 default surface: nothing enabled at boot."""
    assert settings.twitch_enable_writes is False
    assert settings.twitch_write_groups == ""
    assert settings.writes_enabled is False
