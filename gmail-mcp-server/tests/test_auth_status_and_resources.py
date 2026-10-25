"""R2 (auth_status tool) and R3 (resources), through the in-process MCP client and HTTP."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from gmail_mcp.auth import write_token
from gmail_mcp.auth_state import compute_auth_status
from gmail_mcp.config import SCOPE_COMPOSE, SCOPE_READONLY, Settings
from gmail_mcp.errors import auth_url
from gmail_mcp.gmail_client import GmailClient
from gmail_mcp.server import create_app, create_server
from tests.conftest import FAKE_REFRESH_TOKEN, FakeGmail
from tests.test_server import bearer, serve

TOOL_ORDER = [
    "search_threads",
    "get_thread",
    "get_message",
    "list_labels",
    "list_drafts",
    "create_draft",
    "auth_status",
]
RESOURCE_URIS = [
    "gmail://auth/status",
    "gmail://guide/auth",
    "gmail://guide/search",
    "gmail://guide/limits",
]


def _as_client(fake: FakeGmail) -> GmailClient:
    return fake  # type: ignore[return-value]  # structural stand-in


def _token(settings: Settings, scopes: list[str]) -> None:
    write_token(Path(settings.token_path), FAKE_REFRESH_TOKEN, scopes)


# ------------------------------------------------------------------- R2 tool


async def test_tool_order_is_deterministic_and_has_seven_tools(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        names = [t.name for t in (await client.list_tools()).tools]
    assert names == TOOL_ORDER


async def test_auth_status_annotations_title_and_output_schema(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    tool = tools["auth_status"]
    assert tool.title == "Gmail sign-in status"
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is True
    assert tool.annotations.open_world_hint is False
    assert tool.output_schema is not None
    assert {"state", "signed_in", "warnings", "open_commands"} <= set(
        tool.output_schema["properties"]
    )
    assert "read-only" in (tool.description or "").lower()
    assert "never" in (tool.description or "").lower()


async def test_auth_status_has_no_inputs(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        tool = next(t for t in (await client.list_tools()).tools if t.name == "auth_status")
    assert not tool.input_schema.get("properties")


async def test_auth_status_signed_out_is_a_normal_result(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        result = await client.call_tool("auth_status", {})
    assert result.is_error is False
    assert result.structured_content["state"] == "not_signed_in"
    assert result.structured_content["signed_in"] is False
    # R2.3 (A1, D8): the text block is the full JSON of the same payload as structuredContent.
    text = result.content[0].text  # type: ignore[union-attr]
    assert json.loads(text) == result.structured_content
    assert json.loads(text)["state"] == "not_signed_in"
    assert "Gmail sign-in:" not in text  # the old two-line summary is gone


async def test_auth_status_signed_in(settings: Settings) -> None:
    _token(settings, [SCOPE_READONLY, SCOPE_COMPOSE])
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        result = await client.call_tool("auth_status", {})
    assert result.structured_content["state"] == "signed_in"
    assert result.structured_content["next_step"] == "No action needed."


async def test_auth_status_reports_rejection_recorded_by_the_client(settings: Settings) -> None:
    _token(settings, [SCOPE_READONLY, SCOPE_COMPOSE])
    fake = FakeGmail()
    # Newer than the token written above: a rejection older than the token is stale (M1).
    fake.rejected_at = datetime.now(UTC) + timedelta(seconds=5)
    async with Client(create_server(settings, _as_client(fake))) as client:
        result = await client.call_tool("auth_status", {})
    assert result.is_error is False
    assert result.structured_content["state"] == "rejected"
    assert result.structured_content["rejected_at"] == fake.rejected_at.isoformat()


# ------------------------------------------------------------------- R3 resources


async def test_resources_are_listed_with_their_metadata(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        resources = {str(r.uri): r for r in (await client.list_resources()).resources}
    assert set(resources) == set(RESOURCE_URIS)
    expected = {
        "gmail://auth/status": ("auth-status", "application/json", ["user", "assistant"], 0.9),
        "gmail://guide/auth": ("auth-guide", "text/markdown", ["user", "assistant"], 0.8),
        "gmail://guide/search": ("search-guide", "text/markdown", ["assistant"], 0.5),
        "gmail://guide/limits": ("limits-guide", "text/markdown", ["assistant"], 0.6),
    }
    for uri, (name, mime, audience, priority) in expected.items():
        resource = resources[uri]
        assert resource.name == name
        assert resource.mime_type == mime
        assert resource.annotations is not None
        assert list(resource.annotations.audience or []) == audience
        assert resource.annotations.priority == priority


async def test_status_resource_equals_the_auth_status_payload(settings: Settings) -> None:
    _token(settings, [SCOPE_READONLY])
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        read = await client.read_resource("gmail://auth/status")
    body = read.contents[0].text  # type: ignore[union-attr]
    assert json.loads(body) == json.loads(compute_auth_status(settings, None).model_dump_json())


async def test_auth_guide_contains_the_ordered_procedure(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        guide = (await client.read_resource("gmail://guide/auth")).contents[0].text  # type: ignore[union-attr]
    assert auth_url(settings.host_port) in guide
    for phrase in (
        "Call `auth_status`",
        "Start-Process",
        'open "',
        'xdg-open "',
        "Do NOT try to complete the Google consent yourself",
        "7 days",
        "Testing",
    ):
        assert phrase in guide
    assert guide.index("Call `auth_status`") < guide.index("Call `auth_status` again")


async def test_search_guide_documents_the_operators(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        guide = (await client.read_resource("gmail://guide/search")).contents[0].text  # type: ignore[union-attr]
    for operator in (
        "from:",
        "to:",
        "subject:",
        "has:attachment",
        "is:unread",
        "label:",
        "newer_than:",
        "before:",
        "after:",
        " OR ",
        "-newsletter",
        '"exact phrase"',
    ):
        assert operator in guide


async def test_limits_guide_states_the_configured_limits(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        guide = (await client.read_resource("gmail://guide/limits")).contents[0].text  # type: ignore[union-attr]
    assert f"{settings.max_results_cap} per page" in guide
    assert f"{settings.max_body_chars} characters" in guide
    assert "50 messages" in guide
    assert "untrusted" in guide
    assert "NO tool that sends" in guide


async def test_unknown_resource_uri_is_an_invalid_params_error(settings: Settings) -> None:
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        with pytest.raises(MCPError):
            await client.read_resource("gmail://does/not/exist")


async def test_resource_content_holds_no_credentials(settings: Settings) -> None:
    _token(settings, [SCOPE_READONLY, SCOPE_COMPOSE])
    async with Client(create_server(settings, _as_client(FakeGmail()))) as client:
        for uri in RESOURCE_URIS:
            text = (await client.read_resource(uri)).contents[0].text  # type: ignore[union-attr]
            assert FAKE_REFRESH_TOKEN not in text
            assert settings.mcp_auth_token.get_secret_value() not in text


async def test_resources_require_the_bearer_token(settings: Settings) -> None:
    async with serve(create_app(settings, _as_client(FakeGmail()))) as c:
        response = await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 401


async def test_resources_are_served_through_the_guarded_mcp_route(settings: Settings) -> None:
    async with serve(create_app(settings, _as_client(FakeGmail()))) as c:
        response = await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}},
            headers={**bearer(), "Accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 200
    assert "gmail://auth/status" in response.text
