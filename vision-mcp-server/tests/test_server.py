from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import Client
from starlette.applications import Starlette

from tests.conftest import FAKE_API_KEY, FAKE_AUTH_TOKEN, FakeBackend, make_image
from vision_mcp.config import Settings
from vision_mcp.server import create_app, create_server

EXPECTED_TOOLS = {"describe_image"}
SRC = Path(__file__).resolve().parents[1] / "src" / "vision_mcp"


def _client(settings: Settings, backend: FakeBackend) -> Any:
    return create_server(settings, backend.client(settings))


# ---------------------------------------------------------------- tool surface


async def test_registered_tool_set_is_exactly_the_spec(
    settings: Settings, backend: FakeBackend
) -> None:
    async with Client(_client(settings, backend)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == EXPECTED_TOOLS


async def test_tool_annotations(settings: Settings, backend: FakeBackend) -> None:
    async with Client(_client(settings, backend)) as client:
        (tool,) = (await client.list_tools()).tools
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is True


async def test_description_tells_the_agent_when_to_use_it_and_warns(
    settings: Settings, backend: FakeBackend
) -> None:
    async with Client(_client(settings, backend)) as client:
        (tool,) = (await client.list_tools()).tools
    text = (tool.description or "").lower()
    assert "image" in text and "question" in text
    assert "untrusted" in text


async def test_image_cap_is_in_the_schema(settings: Settings, backend: FakeBackend) -> None:
    s = settings.model_copy(update={"max_images_per_call": 2})
    async with Client(create_server(s, backend.client(s))) as client:
        (tool,) = (await client.list_tools()).tools
    paths = tool.input_schema["properties"]["paths"]
    assert paths["maxItems"] == 2
    assert paths["minItems"] == 1
    assert tool.input_schema["required"] == ["paths"]


def test_only_the_backend_client_makes_network_calls() -> None:
    network_import = re.compile(
        r"^\s*(?:import|from)\s+(?:httpx|requests|aiohttp|socket|http\.client|urllib\.request)\b",
        re.MULTILINE,
    )
    offenders = [
        p.name
        for p in SRC.glob("*.py")
        if p.name != "vision_client.py" and network_import.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


# ----------------------------------------------------------------- end to end


async def test_describe_image_end_to_end(
    settings: Settings, backend: FakeBackend, images_dir: Path
) -> None:
    make_image(images_dir / "a.png")
    async with Client(_client(settings, backend)) as client:
        result = await client.call_tool(
            "describe_image", {"paths": ["a.png"], "question": "What colour?"}
        )
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["description"] == "A red square."
    assert result.structured_content["images"][0]["width"] == 64
    assert backend.last_json["messages"][0]["content"][0]["text"].endswith("What colour?")


async def test_question_is_optional(
    settings: Settings, backend: FakeBackend, images_dir: Path
) -> None:
    make_image(images_dir / "a.png")
    async with Client(_client(settings, backend)) as client:
        result = await client.call_tool("describe_image", {"paths": ["a.png"]})
    assert result.is_error is False
    assert "transcribe" in backend.last_json["messages"][0]["content"][0]["text"]


@pytest.mark.parametrize(
    ("path", "needle"),
    [
        ("missing.png", "not found"),
        ("../../etc/passwd", "not found"),
        ("http://evil.example/x.png", "not found"),
    ],
)
async def test_domain_errors_are_returned_verbatim_without_backend_calls(
    settings: Settings, backend: FakeBackend, path: str, needle: str
) -> None:
    async with Client(_client(settings, backend)) as client:
        result = await client.call_tool("describe_image", {"paths": [path]})
    assert result.is_error is True
    assert needle in result.content[0].text  # type: ignore[union-attr]
    assert backend.requests == []


async def test_backend_failure_is_an_actionable_tool_error(
    settings: Settings, backend: FakeBackend, images_dir: Path
) -> None:
    make_image(images_dir / "a.png")
    backend.status = 401
    async with Client(_client(settings, backend)) as client:
        result = await client.call_tool("describe_image", {"paths": ["a.png"]})
    assert result.is_error is True
    assert "rejected the API key" in result.content[0].text  # type: ignore[union-attr]
    assert FAKE_API_KEY not in result.content[0].text  # type: ignore[union-attr]


async def test_unexpected_exceptions_do_not_leak_details(
    settings: Settings,
    backend: FakeBackend,
    images_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_image(images_dir / "a.png")

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("secret internals sk-INTERNALLEAK123456")

    monkeypatch.setattr("vision_mcp.tools.describe_image", boom)
    async with Client(_client(settings, backend)) as client:
        result = await client.call_tool("describe_image", {"paths": ["a.png"]})
    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert "Internal server error" in text
    assert "INTERNALLEAK" not in text


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"paths": []},
        {"paths": "a.png"},
        {"paths": ["a.png"] * 5},
        {"paths": [""]},
        {"paths": ["a.png"], "question": ""},
        {"paths": ["a.png"], "question": "x" * 2001},
    ],
)
async def test_invalid_input_is_rejected_before_any_backend_call(
    settings: Settings, backend: FakeBackend, images_dir: Path, args: dict[str, Any]
) -> None:
    make_image(images_dir / "a.png")
    async with Client(_client(settings, backend)) as client:
        result = await client.call_tool("describe_image", args)
    assert result.is_error is True
    assert backend.requests == []


async def test_tool_logs_never_contain_paths_questions_or_answers(
    settings: Settings,
    backend: FakeBackend,
    images_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    make_image(images_dir / "private-name.png")
    backend.content = "SECRET-ANSWER-TEXT"
    with caplog.at_level(logging.DEBUG):
        async with Client(_client(settings, backend)) as client:
            await client.call_tool(
                "describe_image", {"paths": ["private-name.png"], "question": "SECRET-QUESTION"}
            )
    dumped = json.dumps([r.__dict__ for r in caplog.records], default=str)
    assert "tool call" in dumped
    for secret in ("private-name", "SECRET-QUESTION", "SECRET-ANSWER-TEXT"):
        assert secret not in dumped


# ------------------------------------------------------------------ HTTP layer

BASE = "http://127.0.0.1:8765"
JSONRPC_ACCEPT = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
INITIALIZE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}


@asynccontextmanager
async def serve(application: Starlette) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app lifespan in one dedicated task (anyio scopes must exit where they entered)."""
    ready, stop = asyncio.Event(), asyncio.Event()

    async def run_lifespan() -> None:
        async with application.router.lifespan_context(application):
            ready.set()
            await stop.wait()

    task = asyncio.create_task(run_lifespan())
    await ready.wait()
    try:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            yield client
    finally:
        stop.set()
        await task


@pytest.fixture
async def http(settings: Settings, backend: FakeBackend) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(create_app(settings, backend.client(settings))) as client:
        yield client


def bearer(token: str = FAKE_AUTH_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_app_requires_a_token(settings: Settings) -> None:
    with pytest.raises(ValueError, match="MCP_AUTH_TOKEN"):
        create_app(settings.model_copy(update={"mcp_auth_token": None}))


async def test_healthz_is_unauthenticated_and_minimal(http: httpx.AsyncClient) -> None:
    response = await http.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_healthz_only_exempts_get(http: httpx.AsyncClient) -> None:
    assert (await http.post("/healthz")).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Basic {FAKE_AUTH_TOKEN}"},
        {"Authorization": FAKE_AUTH_TOKEN},
        {"Authorization": f"Bearer {FAKE_AUTH_TOKEN}x"},
        {"Authorization": f"Bearer {FAKE_AUTH_TOKEN[:-1]}"},
        {"Authorization": f"Bearer  {FAKE_AUTH_TOKEN} extra"},
    ],
)
async def test_mcp_rejects_missing_or_invalid_bearer(
    http: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await http.post("/mcp", json=INITIALIZE, headers={**JSONRPC_ACCEPT, **headers})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}  # no hint about what failed
    assert response.headers["www-authenticate"] == "Bearer"


async def test_other_paths_also_require_auth(http: httpx.AsyncClient) -> None:
    assert (await http.get("/anything-else")).status_code == 401
    assert (await http.get("/")).status_code == 401


async def test_valid_bearer_reaches_mcp(http: httpx.AsyncClient) -> None:
    response = await http.post("/mcp", json=INITIALIZE, headers={**JSONRPC_ACCEPT, **bearer()})
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "vision-mcp"


async def test_scheme_is_case_insensitive(http: httpx.AsyncClient) -> None:
    headers = {**JSONRPC_ACCEPT, "Authorization": f"bearer {FAKE_AUTH_TOKEN}"}
    assert (await http.post("/mcp", json=INITIALIZE, headers=headers)).status_code == 200


async def test_tools_list_over_http(http: httpx.AsyncClient) -> None:
    headers = {**JSONRPC_ACCEPT, **bearer(), "MCP-Protocol-Version": "2025-06-18"}
    await http.post("/mcp", json=INITIALIZE, headers=headers)
    response = await http.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers
    )
    assert response.status_code == 200
    assert {t["name"] for t in response.json()["result"]["tools"]} == EXPECTED_TOOLS


async def test_describe_image_over_http(
    http: httpx.AsyncClient, images_dir: Path, backend: FakeBackend
) -> None:
    make_image(images_dir / "a.png")
    headers = {**JSONRPC_ACCEPT, **bearer(), "MCP-Protocol-Version": "2025-06-18"}
    call = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "describe_image", "arguments": {"paths": ["a.png"], "question": "?"}},
    }
    response = await http.post("/mcp", json=call, headers=headers)
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["description"] == "A red square."


async def test_every_auth_decision_goes_through_hmac_compare_digest(
    http: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr("vision_mcp.security.hmac.compare_digest", spy)
    await http.post("/mcp", json=INITIALIZE, headers={**JSONRPC_ACCEPT, **bearer("nope")})
    await http.post("/mcp", json=INITIALIZE, headers=JSONRPC_ACCEPT)  # header missing entirely
    assert len(calls) == 2  # compared on both paths, never short-circuited
    assert all(expected == FAKE_AUTH_TOKEN.encode() for _presented, expected in calls)


@pytest.mark.parametrize(
    "host",
    ["evil.example.com", "evil.example.com:8765", "127.0.0.1.evil.com:8765", "localhost", ""],
)
async def test_bad_host_header_is_rejected_even_with_valid_token(
    http: httpx.AsyncClient, host: str
) -> None:
    headers = {**JSONRPC_ACCEPT, **bearer(), "Host": host}
    assert (await http.post("/mcp", json=INITIALIZE, headers=headers)).status_code == 421


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "LOCALHOST:1"])
async def test_loopback_hosts_are_accepted(http: httpx.AsyncClient, host: str) -> None:
    assert (await http.get("/healthz", headers={"Host": host})).status_code == 200


async def test_foreign_origin_is_rejected(http: httpx.AsyncClient) -> None:
    headers = {**JSONRPC_ACCEPT, **bearer(), "Origin": "https://evil.example.com"}
    assert (await http.post("/mcp", json=INITIALIZE, headers=headers)).status_code == 403


async def test_oversized_request_body_is_rejected(http: httpx.AsyncClient) -> None:
    huge = {**INITIALIZE, "params": {**INITIALIZE["params"], "pad": "x" * (100 * 1024)}}
    response = await http.post("/mcp", json=huge, headers={**JSONRPC_ACCEPT, **bearer()})
    assert response.status_code == 413


async def test_rejections_never_log_the_presented_token(
    http: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    presented = "presented-secret-token-0123456789abcdef-xyz"
    with caplog.at_level(logging.DEBUG):
        await http.post("/mcp", json=INITIALIZE, headers={**JSONRPC_ACCEPT, **bearer(presented)})
    assert any("rejected request" in r.getMessage() for r in caplog.records)
    dumped = json.dumps([r.__dict__ for r in caplog.records], default=str)
    assert presented not in dumped
    assert FAKE_AUTH_TOKEN not in dumped


async def test_server_header_not_advertised(http: httpx.AsyncClient) -> None:
    assert "server" not in (await http.get("/healthz")).headers
