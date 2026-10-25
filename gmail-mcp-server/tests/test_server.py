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

from gmail_mcp.config import Settings
from gmail_mcp.errors import AuthRequiredError, GmailApiError
from gmail_mcp.gmail_client import GmailClient
from gmail_mcp.server import create_app, create_server
from tests.conftest import FAKE_AUTH_TOKEN, FakeGmail, make_message

EXPECTED_TOOLS = {
    "search_threads",
    "get_thread",
    "get_message",
    "list_labels",
    "list_drafts",
    "create_draft",
    "auth_status",
}
READ_ONLY_TOOLS = EXPECTED_TOOLS - {"create_draft"}
SRC = Path(__file__).resolve().parents[1] / "src" / "gmail_mcp"


def _as_client(fake: FakeGmail) -> GmailClient:
    return fake  # type: ignore[return-value]  # structural stand-in


# ---------------------------------------------------------------- tool surface


async def test_registered_tool_set_is_exactly_the_spec(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == EXPECTED_TOOLS


async def test_tool_annotations(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in READ_ONLY_TOOLS:
        ann = tools[name].annotations
        assert ann is not None
        assert ann.read_only_hint is True
        assert ann.destructive_hint is False
    draft = tools["create_draft"].annotations
    assert draft is not None
    assert draft.read_only_hint is False
    assert draft.destructive_hint is False
    assert draft.idempotent_hint is False


async def test_descriptions_warn_about_untrusted_content(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in ("search_threads", "get_thread", "get_message"):
        assert "untrusted" in (tools[name].description or "").lower()


async def test_result_cap_is_in_the_schema(settings: Settings) -> None:
    capped = settings.model_copy(update={"max_results_cap": 7})
    async with Client(create_server(capped, _as_client(FakeGmail()))) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in ("search_threads", "list_drafts"):
        assert tools[name].input_schema["properties"]["max_results"]["maximum"] == 7


def test_no_send_delete_or_modify_code_path_exists_in_source() -> None:
    forbidden = re.compile(
        r"\.(send|delete|trash|untrash|modify|batchModify|batchDelete|import_|insert|"
        r"attachments|settings|watch|stop)\s*\(|users\(\)\s*\.\s*settings|\.update\s*\(userId"
    )
    offenders = [
        f"{path.name}:{n}: {line.strip()}"
        for path in SRC.glob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if forbidden.search(line)
    ]
    assert offenders == []


def test_gmail_client_exposes_no_mutating_call_besides_create_draft() -> None:
    public = {n for n in dir(GmailClient) if not n.startswith("_")}
    assert public == {
        "list_threads",
        "get_thread",
        "get_message",
        "list_labels",
        "list_drafts",
        "get_draft",
        "create_draft",
        "reset",
        "rejected_at",  # read-only view of the rejection record (R1.3)
    }


# ------------------------------------------------------------ calling the tools


async def test_search_threads_tool_end_to_end(settings: Settings) -> None:
    fake = FakeGmail()
    fake.thread_listing = {"threads": [{"id": "t1", "snippet": "s"}]}
    fake.threads["t1"] = {"id": "t1", "messages": [make_message("m1", "t1")]}
    async with Client(create_server(settings, _as_client(fake))) as client:
        result = await client.call_tool("search_threads", {"query": "is:unread"})
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["threads"][0]["subject"] == "Hello"


async def test_create_draft_tool_end_to_end(settings: Settings) -> None:
    fake = FakeGmail()
    async with Client(create_server(settings, _as_client(fake))) as client:
        result = await client.call_tool(
            "create_draft",
            {"to": ["bob@example.com"], "subject": "Hi", "body": "Hello Bob"},
        )
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["draft_id"] == "d1"
    assert len(fake.created) == 1


async def test_missing_token_is_a_clear_tool_error_not_a_crash(settings: Settings) -> None:
    async with Client(create_server(settings)) as client:
        result = await client.call_tool("list_labels", {})
        again = await client.call_tool("list_labels", {})  # server survived the first failure
    assert result.is_error is True
    assert "AUTH_REQUIRED:" in result.content[0].text  # type: ignore[union-attr]
    assert again.is_error is True


class _Boom(FakeGmail):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self.exc = exc

    async def list_labels(self) -> Any:
        raise self.exc


async def test_domain_errors_are_returned_verbatim(settings: Settings) -> None:
    fake = _Boom(GmailApiError("Gmail API error: HTTP 404 Not Found."))
    async with Client(create_server(settings, _as_client(fake))) as client:
        result = await client.call_tool("list_labels", {})
    assert result.is_error is True
    assert "HTTP 404" in result.content[0].text  # type: ignore[union-attr]


async def test_auth_error_is_actionable(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(_Boom(AuthRequiredError())))) as client:
        result = await client.call_tool("list_labels", {})
    assert "AUTH_REQUIRED:" in result.content[0].text  # type: ignore[union-attr]
    assert "scripts/auth" not in result.content[0].text  # type: ignore[union-attr]


async def test_unexpected_exceptions_do_not_leak_details(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "dear-bob-this-is-private-ya29.LEAK"
    fake = _Boom(RuntimeError(secret))
    with caplog.at_level(logging.INFO):
        async with Client(create_server(settings, _as_client(fake))) as client:
            result = await client.call_tool("list_labels", {})
    assert result.is_error is True
    assert secret not in result.content[0].text  # type: ignore[union-attr]
    assert all(secret not in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_message", {"message_id": "../etc/passwd"}),
        ("get_thread", {"thread_id": "x" * 65}),
        ("get_thread", {"thread_id": "abc", "format": "raw"}),
        ("search_threads", {"query": "q" * 501}),
        ("search_threads", {"query": "x", "max_results": 0}),
        ("search_threads", {"query": "x", "max_results": 10_000}),
        ("list_drafts", {"max_results": 51}),
        (
            "create_draft",
            {"to": ["a@example.com"], "subject": "x\r\nBcc: e@example.com", "body": "b"},
        ),
        (
            "create_draft",
            {"to": ["a@example.com\nBcc: e@example.com"], "subject": "s", "body": "b"},
        ),
        ("create_draft", {"to": [], "subject": "s", "body": "b"}),
        ("create_draft", {"to": ["a@example.com"], "subject": "s", "body": "b" * 102_401}),
        (
            "create_draft",
            {"to": ["a@example.com"], "subject": "s", "body": "b", "reply_to_message_id": "bad id"},
        ),
        ("create_draft", {"to": ["a@example.com"] * 51, "subject": "s", "body": "b"}),
    ],
)
async def test_invalid_input_is_rejected_before_any_gmail_call(
    settings: Settings, tool: str, args: dict[str, Any]
) -> None:
    fake = FakeGmail()
    async with Client(create_server(settings, _as_client(fake))) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error is True
    assert fake.calls == []
    assert fake.created == []


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
async def http(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(create_app(settings, _as_client(FakeGmail()))) as client:
        yield client


def bearer(token: str = FAKE_AUTH_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_healthz_is_unauthenticated_and_minimal(http: httpx.AsyncClient) -> None:
    response = await http.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_healthz_does_not_reveal_google_credential_state(
    settings: Settings, tmp_path: Path
) -> None:
    bodies = []
    for token_present in (False, True):
        if token_present:
            from gmail_mcp.auth import write_token

            write_token(Path(settings.token_path), "fake-refresh", settings.scopes)
        async with serve(create_app(settings, _as_client(FakeGmail()))) as client:
            bodies.append((await client.get("/healthz")).content)
    assert bodies[0] == bodies[1]


async def test_healthz_only_exempts_get(http: httpx.AsyncClient) -> None:
    assert (await http.post("/healthz")).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
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
    assert response.json()["result"]["serverInfo"]["name"] == "gmail-mcp"


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


async def test_token_comparison_is_constant_time(
    http: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr("gmail_mcp.security.hmac.compare_digest", spy)
    await http.post("/mcp", json=INITIALIZE, headers={**JSONRPC_ACCEPT, **bearer("nope")})
    await http.post("/mcp", json=INITIALIZE, headers=JSONRPC_ACCEPT)  # header missing entirely
    assert len(calls) == 2  # compared on both paths, never short-circuited
    assert all(expected == FAKE_AUTH_TOKEN.encode() for _presented, expected in calls)


@pytest.mark.parametrize(
    "host",
    [
        "evil.example.com",
        "evil.example.com:8765",
        "127.0.0.1.evil.com:8765",
        "localhost",
        "0.0.0.0:8765",
        "",
    ],
)
async def test_bad_host_header_is_rejected_even_with_valid_token(
    http: httpx.AsyncClient, host: str
) -> None:
    headers = {**JSONRPC_ACCEPT, **bearer(), "Host": host}
    response = await http.post("/mcp", json=INITIALIZE, headers=headers)
    assert response.status_code == 421


async def test_bad_host_rejected_on_healthz_too(http: httpx.AsyncClient) -> None:
    assert (await http.get("/healthz", headers={"Host": "evil.example.com:80"})).status_code == 421


@pytest.mark.parametrize(
    "host", ["127.0.0.1:8765", "localhost:8765", "LOCALHOST:1", "127.0.0.1:8000"]
)
async def test_loopback_hosts_are_accepted(http: httpx.AsyncClient, host: str) -> None:
    assert (await http.get("/healthz", headers={"Host": host})).status_code == 200


async def test_foreign_origin_is_rejected(http: httpx.AsyncClient) -> None:
    headers = {**JSONRPC_ACCEPT, **bearer(), "Origin": "https://evil.example.com"}
    assert (await http.post("/mcp", json=INITIALIZE, headers=headers)).status_code == 403


async def test_loopback_origin_is_accepted(http: httpx.AsyncClient) -> None:
    headers = {**JSONRPC_ACCEPT, **bearer(), "Origin": "http://localhost:3000"}
    assert (await http.post("/mcp", json=INITIALIZE, headers=headers)).status_code == 200


async def test_oversized_request_body_is_rejected(http: httpx.AsyncClient) -> None:
    huge = {**INITIALIZE, "params": {**INITIALIZE["params"], "pad": "x" * (600 * 1024)}}
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
    response = await http.get("/healthz")
    assert "server" not in response.headers


@pytest.mark.parametrize(
    ("header", "should_pass"),
    [
        (None, False),
        ("", False),
        ("Bearer", False),
        ("Bearer wrong", False),
        ("Basic abc", False),
        (f"Bearer {FAKE_AUTH_TOKEN}", True),
    ],
)
async def test_every_auth_decision_goes_through_hmac_compare_digest(
    http: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    header: str | None,
    should_pass: bool,
) -> None:
    results: list[bool] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        results.append(real(a, b))
        return results[-1]

    monkeypatch.setattr("gmail_mcp.security.hmac.compare_digest", spy)
    headers = dict(JSONRPC_ACCEPT)
    if header is not None:
        headers["Authorization"] = header
    response = await http.post("/mcp", json=INITIALIZE, headers=headers)
    assert len(results) == 1  # exactly one constant-time comparison per request
    assert (response.status_code == 200) is should_pass
    assert results[0] is should_pass
