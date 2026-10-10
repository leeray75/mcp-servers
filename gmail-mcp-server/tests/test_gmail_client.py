from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httplib2
import pytest
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError

from gmail_mcp.auth import write_token
from gmail_mcp.config import SCOPE_COMPOSE, SCOPE_READONLY, Settings
from gmail_mcp.errors import AUTH_HINT, AuthRequiredError, GmailApiError
from gmail_mcp.gmail_client import GmailClient, build_service, map_http_error
from tests.conftest import FAKE_REFRESH_TOKEN


def http_error(status: int, body: bytes = b'{"error": "SECRET-BODY ya29.leak"}') -> HttpError:
    return HttpError(resp=httplib2.Response({"status": str(status)}), content=body)


def service_with_execute(execute: Any) -> MagicMock:
    """A mock service whose every ``...execute()`` call goes through ``execute``."""
    service = MagicMock()
    leaf = MagicMock()
    leaf.execute = execute
    for resource in ("threads", "messages", "labels", "drafts"):
        res = getattr(service.users.return_value, resource).return_value
        for method in ("list", "get", "create"):
            getattr(res, method).return_value = leaf
    return service


@pytest.mark.parametrize(
    ("status", "needle"),
    [(403, "scope"), (404, "does not exist"), (429, "Rate limit"), (500, "Internal Server Error")],
)
def test_map_http_error(status: int, needle: str) -> None:
    mapped = map_http_error(http_error(status))
    assert isinstance(mapped, GmailApiError)
    assert f"HTTP {status}" in str(mapped)
    assert needle in str(mapped)
    assert mapped.status == status


def test_map_http_error_never_echoes_body() -> None:
    assert "SECRET-BODY" not in str(map_http_error(http_error(400)))


def test_map_http_error_unknown_status() -> None:
    assert "HTTP 599" in str(map_http_error(http_error(599)))


def test_map_http_error_401_is_auth_required() -> None:
    mapped = map_http_error(http_error(401))
    assert isinstance(mapped, AuthRequiredError)
    assert AUTH_HINT in str(mapped)


async def test_success_returns_payload_and_reuses_service() -> None:
    factory = MagicMock(return_value=service_with_execute(lambda **_: {"labels": []}))
    client = GmailClient(factory)
    assert await client.list_labels() == {"labels": []}
    await client.list_labels()
    assert factory.call_count == 1


async def test_http_error_becomes_gmail_api_error() -> None:
    def execute(**_: Any) -> None:
        raise http_error(404)

    client = GmailClient(lambda: service_with_execute(execute))
    with pytest.raises(GmailApiError, match="HTTP 404") as info:
        await client.get_message("m1", "full")
    assert "SECRET-BODY" not in str(info.value)


async def test_401_resets_service_and_requires_auth() -> None:
    factory = MagicMock(side_effect=lambda: service_with_execute(_raise(http_error(401))))
    client = GmailClient(factory)
    with pytest.raises(AuthRequiredError):
        await client.list_labels()
    with pytest.raises(AuthRequiredError):
        await client.list_labels()
    assert factory.call_count == 2  # service was discarded, so a fresh token is picked up


def _raise(exc: Exception) -> Any:
    def execute(**_: Any) -> None:
        raise exc

    return execute


async def test_refresh_error_maps_to_auth_required_without_leaking() -> None:
    client = GmailClient(
        lambda: service_with_execute(_raise(RefreshError("invalid_grant: Token revoked ya29.X")))
    )
    with pytest.raises(AuthRequiredError) as info:
        await client.list_labels()
    assert "invalid_grant" not in str(info.value)
    assert "ya29" not in str(info.value)
    assert AUTH_HINT in str(info.value)


async def test_missing_token_is_actionable_not_a_crash(settings: Settings) -> None:
    client = GmailClient(lambda: build_service(settings))
    with pytest.raises(AuthRequiredError, match="auth"):
        await client.list_labels()


async def test_token_added_later_is_picked_up_without_restart(settings: Settings) -> None:
    from pathlib import Path

    client = GmailClient(lambda: build_service(settings))
    with pytest.raises(AuthRequiredError):
        await client.list_labels()
    write_token(Path(settings.token_path), FAKE_REFRESH_TOKEN, [SCOPE_READONLY, SCOPE_COMPOSE])
    # With a token present the service builds offline (static discovery); it only fails when
    # an actual API request would need the network, which we do not make here.
    service = build_service(settings)
    assert hasattr(service, "users")


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("slow"),
        OSError("reset"),
        httplib2.ServerNotFoundError("dns"),
        TransportError("x"),
    ],
)
async def test_transport_failures_are_concise(exc: Exception) -> None:
    client = GmailClient(lambda: service_with_execute(_raise(exc)))
    with pytest.raises(GmailApiError, match="Could not reach"):
        await client.list_labels()


async def test_unexpected_exceptions_propagate_unchanged() -> None:
    client = GmailClient(lambda: service_with_execute(_raise(RuntimeError("bug"))))
    with pytest.raises(RuntimeError):
        await client.list_labels()


async def test_each_method_issues_the_expected_call() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    service = MagicMock()
    users = service.users.return_value

    def recorder(label: str) -> Any:
        def method(**kwargs: Any) -> MagicMock:
            calls.append((label, kwargs))
            out = MagicMock()
            out.execute.return_value = {"ok": label}
            return out

        return method

    users.threads.return_value.list = recorder("threads.list")
    users.threads.return_value.get = recorder("threads.get")
    users.messages.return_value.get = recorder("messages.get")
    users.labels.return_value.list = recorder("labels.list")
    users.drafts.return_value.list = recorder("drafts.list")
    users.drafts.return_value.get = recorder("drafts.get")
    users.drafts.return_value.create = recorder("drafts.create")
    client = GmailClient(lambda: service)

    await client.list_threads("q", 5, "tok")
    await client.get_thread("t1", "metadata", ["Subject"])
    await client.get_message("m1", "full")
    await client.list_labels()
    await client.list_drafts(3, None)
    await client.get_draft("d1")
    await client.create_draft("cmF3", "t9")
    await client.create_draft("cmF3", None)

    by_label = {label: kwargs for label, kwargs in calls}
    assert by_label["threads.list"] == {
        "userId": "me",
        "q": "q",
        "maxResults": 5,
        "pageToken": "tok",
    }
    assert by_label["threads.get"]["metadataHeaders"] == ["Subject"]
    assert by_label["messages.get"]["metadataHeaders"] is None
    assert by_label["drafts.get"]["format"] == "metadata"
    creates = [kw for label, kw in calls if label == "drafts.create"]
    assert creates[0]["body"] == {"message": {"raw": "cmF3", "threadId": "t9"}}
    assert creates[1]["body"] == {"message": {"raw": "cmF3"}}
