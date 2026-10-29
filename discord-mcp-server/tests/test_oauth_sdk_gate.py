"""SDK gate (user-oauth SPEC section 6.3, T0): facts about the pinned ``mcp`` SDK, recorded as tests.

The server cannot switch to stateful mode or patch the SDK, so these tests pin what the URL
elicitation of U17 depends on, using a minimal app built with the same stateless JSON settings as
``server.create_app``:

(a) per-request client capabilities and the protocol version are readable inside a tool handler
    (2026-07-28 envelope);
(b) an ``InputRequiredResult`` with one ``elicitation/create`` ``mode: "url"`` request can be
    returned from a tool handler;
(c) stateless 2025-06-18 handshake capabilities are not retained, so a legacy client never
    receives an elicitation (it takes the plain authorization_required result, U17).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ElicitRequest, ElicitRequestURLParams, InputRequiredResult
from starlette.applications import Starlette

URL = "http://127.0.0.1:8769/oauth/start"
MODERN = "2026-07-28"
LEGACY = "2025-06-18"
BASE = "http://127.0.0.1:8769"
JSONRPC_ACCEPT = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
INITIALIZE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": LEGACY,
        "capabilities": {},
        "clientInfo": {"name": "gate", "version": "0"},
    },
}


def _gate_app() -> Starlette:
    server = MCPServer("gate", version="0")

    @server.tool(name="probe")
    def probe(ctx: Context[Any, Any]) -> str:
        caps = ctx.client_capabilities
        payload = {
            "capabilities": caps.model_dump(mode="json", exclude_none=True) if caps else None,
            "protocol_version": ctx.protocol_version,
        }
        return json.dumps(payload)

    @server.tool(name="input_required")
    def input_required(ctx: Context[Any, Any]) -> InputRequiredResult | str:
        request = ElicitRequest(params=ElicitRequestURLParams(message="sign in", url=URL))
        return InputRequiredResult(input_requests={"discord_sign_in": request})

    return server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )


def _modern(name: str, caps: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    meta = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "gate", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": caps,
    }
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}, "_meta": meta},
    }
    headers = {
        **JSONRPC_ACCEPT,
        "mcp-protocol-version": MODERN,
        "mcp-method": "tools/call",
        "mcp-name": name,
    }
    return body, headers


def _legacy(name: str) -> tuple[dict[str, Any], dict[str, str]]:
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }
    headers = {
        **JSONRPC_ACCEPT,
        "mcp-protocol-version": LEGACY,
        "mcp-method": "tools/call",
        "mcp-name": name,
    }
    return call, headers


@asynccontextmanager
async def _serve(application: Starlette) -> AsyncIterator[httpx.AsyncClient]:
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


async def test_gate_a_per_request_capabilities_and_version_are_readable_when_stateless() -> None:
    url_caps: dict[str, Any] = {"elicitation": {"url": {}}}
    async with _serve(_gate_app()) as c:
        body, headers = _modern("probe", url_caps)
        response = await c.post("/mcp", json=body, headers=headers)
    text = response.json()["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["capabilities"] == url_caps
    assert payload["protocol_version"] == MODERN


async def test_gate_b_input_required_url_elicitation_is_produced_from_a_tool_handler() -> None:
    async with _serve(_gate_app()) as c:
        body, headers = _modern("input_required", {"elicitation": {"url": {}}})
        response = await c.post("/mcp", json=body, headers=headers)
    result = response.json()["result"]
    assert result["resultType"] == "input_required"
    request = result["inputRequests"]["discord_sign_in"]
    assert request["method"] == "elicitation/create"
    assert request["params"]["mode"] == "url"
    assert request["params"]["url"] == URL


async def test_gate_c_legacy_handshake_capabilities_are_not_retained() -> None:
    """Stateless mode drops the handshake's capabilities: a 2025-06-18 client is not recognised."""
    async with _serve(_gate_app()) as c:
        await c.post("/mcp", json=INITIALIZE, headers=JSONRPC_ACCEPT)
        call, headers = _legacy("probe")
        response = await c.post("/mcp", json=call, headers=headers)
    payload = json.loads(response.json()["result"]["content"][0]["text"])
    assert payload["capabilities"] is None
