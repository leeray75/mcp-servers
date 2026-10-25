"""R5: URL-mode elicitation for sign-in, over the real stateless HTTP app.

A client gets an elicitation only when it declares ``elicitation.url`` on the request and
the setting is on; otherwise it gets the R4 tool error. Only the SDK-defined shapes are used.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import (
    ClientCapabilities,
    ElicitationCapability,
    FormElicitationCapability,
    InputRequiredResult,
    UrlElicitationCapability,
)

from gmail_mcp.config import Settings
from gmail_mcp.errors import REJECTED_REASON, AuthRequiredError, auth_url
from gmail_mcp.gmail_client import GmailClient
from gmail_mcp.server import (
    MODERN_REVISION,
    SIGN_IN_KEY,
    SIGN_IN_MESSAGE,
    _instrumented,
    _sign_in_response,
    create_app,
)
from tests.conftest import FakeGmail
from tests.test_server import INITIALIZE, JSONRPC_ACCEPT, bearer, serve

# Not a sign-in elicitation revision any more (amendment A1, D7): used only to prove the fallback.
LEGACY_REVISION = "2025-11-25"
URL_CAPS: dict[str, Any] = {"elicitation": {"url": {}}}
FORM_CAPS: dict[str, Any] = {"elicitation": {"form": {}}}
CLIENT_INFO = {"name": "pytest", "version": "0"}


class _AuthFailing(FakeGmail):
    async def list_labels(self) -> Any:
        raise AuthRequiredError(REJECTED_REASON)


def _as_client(fake: FakeGmail) -> GmailClient:
    return fake  # type: ignore[return-value]  # structural stand-in


def _modern_call(
    name: str, caps: dict[str, Any] | None, *, version: str = MODERN_REVISION
) -> tuple[dict[str, Any], dict[str, str]]:
    """A 2026-07-28 self-contained ``tools/call`` POST with its routing headers."""
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
    }
    if caps is not None:
        meta["io.modelcontextprotocol/clientCapabilities"] = caps
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}, "_meta": meta},
    }
    headers = {
        **bearer(),
        **JSONRPC_ACCEPT,
        "mcp-protocol-version": version,
        "mcp-method": "tools/call",
        "mcp-name": name,
    }
    return body, headers


def _fake_ctx(caps: Any, version: str | None) -> Context[Any, Any]:
    return cast(
        Context[Any, Any],
        SimpleNamespace(client_capabilities=caps, protocol_version=version),
    )


def _url_caps() -> ClientCapabilities:
    return ClientCapabilities(elicitation=ElicitationCapability(url=UrlElicitationCapability()))


def _form_caps() -> ClientCapabilities:
    return ClientCapabilities(elicitation=ElicitationCapability(form=FormElicitationCapability()))


# ------------------------------------------------------------- live HTTP behaviour


async def test_url_capable_2026_client_is_asked_to_open_the_plain_sign_in_url(
    settings: Settings,
) -> None:
    async with serve(create_app(settings, _as_client(_AuthFailing()))) as c:
        body, headers = _modern_call("list_labels", URL_CAPS)
        response = await c.post("/mcp", json=body, headers=headers)
    result = response.json()["result"]
    assert result["resultType"] == "input_required"
    request = result["inputRequests"][SIGN_IN_KEY]
    assert request["method"] == "elicitation/create"
    params = request["params"]
    assert params["mode"] == "url"
    assert params["message"] == SIGN_IN_MESSAGE
    assert params["url"] == auth_url(settings.host_port)  # exact /auth URL, no query (R5.3)
    assert "?" not in params["url"]


async def test_form_only_client_gets_the_plain_tool_error_not_an_elicitation(
    settings: Settings,
) -> None:
    async with serve(create_app(settings, _as_client(_AuthFailing()))) as c:
        body, headers = _modern_call("list_labels", FORM_CAPS)
        response = await c.post("/mcp", json=body, headers=headers)
    result = response.json()["result"]
    assert result.get("resultType") != "input_required"
    assert "inputRequests" not in result
    assert result["isError"] is True
    assert "AUTH_REQUIRED:" in result["content"][0]["text"]


async def test_kill_switch_disables_elicitation(settings: Settings) -> None:
    off = settings.model_copy(update={"auth_url_elicitation": False})
    async with serve(create_app(off, _as_client(_AuthFailing()))) as c:
        body, headers = _modern_call("list_labels", URL_CAPS)
        response = await c.post("/mcp", json=body, headers=headers)
    result = response.json()["result"]
    assert "inputRequests" not in result
    assert result["isError"] is True
    assert "AUTH_REQUIRED:" in result["content"][0]["text"]


async def test_legacy_2025_handshake_client_gets_the_fallback_when_capabilities_are_unknown(
    settings: Settings,
) -> None:
    """Stateless mode keeps no handshake state, so the URL-capable 2025 client's capabilities
    cannot be read inside the tool. R5.4 then requires the R4 text (never an undeclared mode)."""
    async with serve(create_app(settings, _as_client(_AuthFailing()))) as c:
        init = await c.post("/mcp", json=INITIALIZE, headers={**bearer(), **JSONRPC_ACCEPT})
        assert init.status_code == 200
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_labels", "arguments": {}},
        }
        headers = {
            **bearer(),
            **JSONRPC_ACCEPT,
            "mcp-protocol-version": LEGACY_REVISION,
            "mcp-method": "tools/call",
            "mcp-name": "list_labels",
        }
        response = await c.post("/mcp", json=call, headers=headers)
    payload = response.json()
    assert "error" not in payload
    assert payload["result"]["isError"] is True
    assert "AUTH_REQUIRED:" in payload["result"]["content"][0]["text"]


async def test_instrumented_wrapper_maps_the_decision_to_the_right_response(
    settings: Settings,
) -> None:
    """The wrapper returns InputRequiredResult for a 2026-07-28 url-capable request and the R4
    ToolError for every other request, including 2025-11-25 (amendment A1, D7)."""

    async def fails(**_kwargs: Any) -> Any:
        raise AuthRequiredError(REJECTED_REASON)

    wrapped = _instrumented("probe", settings)(fails)
    modern = await wrapped(ctx=_fake_ctx(_url_caps(), MODERN_REVISION))
    assert isinstance(modern, InputRequiredResult)
    with pytest.raises(ToolError, match="AUTH_REQUIRED:"):
        await wrapped(ctx=_fake_ctx(_url_caps(), LEGACY_REVISION))
    with pytest.raises(ToolError, match="AUTH_REQUIRED:"):
        await wrapped(ctx=_fake_ctx(_form_caps(), MODERN_REVISION))


async def test_bearer_is_still_required_for_tool_calls(settings: Settings) -> None:
    async with serve(create_app(settings, _as_client(_AuthFailing()))) as c:
        body, headers = _modern_call("list_labels", URL_CAPS)
        headers.pop("Authorization")
        response = await c.post("/mcp", json=body, headers=headers)
    assert response.status_code == 401


# ------------------------------------------------------------ decision function


def test_no_context_means_no_elicitation(settings: Settings) -> None:
    assert _sign_in_response(None, settings) is None


def test_setting_off_means_no_elicitation(settings: Settings) -> None:
    off = settings.model_copy(update={"auth_url_elicitation": False})
    assert _sign_in_response(_fake_ctx(_url_caps(), MODERN_REVISION), off) is None


@pytest.mark.parametrize("caps", [None, "no-elicitation-object", "form-only"])
def test_undeclared_or_form_only_capabilities_mean_no_elicitation(
    settings: Settings, caps: str | None
) -> None:
    value: Any = None
    if caps == "no-elicitation-object":
        value = ClientCapabilities()
    elif caps == "form-only":
        value = _form_caps()
    assert _sign_in_response(_fake_ctx(value, MODERN_REVISION), settings) is None


def test_modern_revision_with_url_capability_returns_input_required(settings: Settings) -> None:
    response = _sign_in_response(_fake_ctx(_url_caps(), MODERN_REVISION), settings)
    assert isinstance(response, InputRequiredResult)
    request = response.input_requests[SIGN_IN_KEY]  # type: ignore[index]
    assert request.params.mode == "url"  # type: ignore[union-attr]
    assert request.params.url == auth_url(settings.host_port)  # type: ignore[union-attr]


def test_2025_revision_with_url_capability_gets_no_elicitation(settings: Settings) -> None:
    """D7: the -32042 mechanism is removed; a 2025-11-25 client takes the R4 tool error."""
    assert _sign_in_response(_fake_ctx(_url_caps(), LEGACY_REVISION), settings) is None


@pytest.mark.parametrize("version", ["2025-06-18", "2024-11-05", None])
def test_other_or_unknown_revisions_fall_back_to_the_tool_error(
    settings: Settings, version: str | None
) -> None:
    assert _sign_in_response(_fake_ctx(_url_caps(), version), settings) is None
