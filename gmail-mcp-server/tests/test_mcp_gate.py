"""R5.6 implementation gate: facts about the pinned ``mcp==2.2.0`` SDK, recorded as tests.

The server cannot switch to stateful mode or fork the SDK (R5.6), so these tests pin what URL
elicitation depends on, using a minimal stateless JSON-mode server:

(a) per-request client capabilities are readable inside a tool handler (2026-07-28 envelope);
(b) (A1) the 2026-07-28 response shape, ``InputRequiredResult`` with an ``elicitation/create``
    url request, can be produced from a tool handler.

Two further tests pin the consequences of D7 (amendment A1). Stateless mode does not keep
handshake capabilities, so a 2025-11-25 client cannot be recognised as url-capable. Such a client
therefore gets the R4 tool error and never an elicitation.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ElicitRequest, ElicitRequestURLParams, InputRequiredResult

from gmail_mcp.errors import REJECTED_REASON, AuthRequiredError
from tests.test_server import INITIALIZE, JSONRPC_ACCEPT, serve

URL = "http://127.0.0.1:8765/auth"
MODERN = "2026-07-28"
LEGACY = "2025-11-25"


def _gate_app() -> Any:
    server = MCPServer("gate", version="0")

    @server.tool(name="probe")
    def probe(ctx: Context[Any, Any]) -> str:
        caps = ctx.client_capabilities
        return json.dumps(caps.model_dump(mode="json", exclude_none=True) if caps else None)

    @server.tool(name="input_required")
    def input_required(ctx: Context[Any, Any]) -> InputRequiredResult | str:
        request = ElicitRequest(params=ElicitRequestURLParams(message="sign in", url=URL))
        return InputRequiredResult(input_requests={"signin": request})

    @server.tool(name="auth_fails")
    def auth_fails(ctx: Context[Any, Any]) -> str:
        raise AuthRequiredError(REJECTED_REASON)

    return server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=["http://127.0.0.1:*"],
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
    """A 2025-11-25 ``tools/call`` after an ``initialize`` on the same stateless app."""
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


async def test_gate_a_per_request_capabilities_are_readable_when_stateless() -> None:
    url_caps: dict[str, Any] = {"elicitation": {"url": {}}}
    async with serve(_gate_app()) as c:
        body, headers = _modern("probe", url_caps)
        response = await c.post("/mcp", json=body, headers=headers)
    text = response.json()["result"]["content"][0]["text"]
    assert json.loads(text) == url_caps


async def test_gate_b_input_required_result_is_produced_from_a_tool_handler() -> None:
    async with serve(_gate_app()) as c:
        body, headers = _modern("input_required", {"elicitation": {"url": {}}})
        response = await c.post("/mcp", json=body, headers=headers)
    result = response.json()["result"]
    assert result["resultType"] == "input_required"
    request = result["inputRequests"]["signin"]
    assert request["method"] == "elicitation/create"
    assert request["params"]["mode"] == "url"
    assert request["params"]["url"] == URL


async def test_stateless_legacy_handshake_capabilities_are_not_retained() -> None:
    """Pins the finding behind D7: the 2025-11-25 handshake's capabilities are not visible."""
    async with serve(_gate_app()) as c:
        await c.post("/mcp", json=INITIALIZE, headers=JSONRPC_ACCEPT)
        call, headers = _legacy("probe")
        response = await c.post("/mcp", json=call, headers=headers)
    assert json.loads(response.json()["result"]["content"][0]["text"]) is None


async def test_2025_client_gets_the_r4_fallback_and_never_an_elicitation() -> None:
    """Pins D7 (A1): with capabilities unknown, a 2025-11-25 client gets a tool error, with no
    JSON-RPC error and no elicitation of any mode (R5.4). The R4 text itself is asserted against
    the production server in ``test_elicitation.py``, because this bare gate app does not use
    the production ``_instrumented`` wrapper."""
    async with serve(_gate_app()) as c:
        await c.post("/mcp", json=INITIALIZE, headers=JSONRPC_ACCEPT)
        call, headers = _legacy("auth_fails")
        response = await c.post("/mcp", json=call, headers=headers)
    payload = response.json()
    assert "error" not in payload
    assert "inputRequests" not in payload["result"]
    assert payload["result"]["isError"] is True
