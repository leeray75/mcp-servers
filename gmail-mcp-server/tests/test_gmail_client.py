from __future__ import annotations

import errno
import logging
import socket
from typing import Any
from unittest.mock import MagicMock

import httplib2
import pytest
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError

from gmail_mcp.auth import write_token
from gmail_mcp.config import SCOPE_COMPOSE, SCOPE_READONLY, Settings
from gmail_mcp.errors import AuthRequiredError, GmailApiError, NetworkUnreachableError
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
    assert "AUTH_REQUIRED:" in mapped.user_message()


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
    assert "AUTH_REQUIRED:" in info.value.user_message()


async def test_missing_token_is_actionable_not_a_crash(settings: Settings) -> None:
    client = GmailClient(lambda: build_service(settings))
    with pytest.raises(AuthRequiredError, match="No Google token found"):
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
        OSError(errno.ECONNRESET, "reset"),
        httplib2.HttpLib2Error("generic"),
        TransportError("x"),
    ],
)
async def test_transport_failures_are_concise(exc: Exception) -> None:
    client = GmailClient(lambda: service_with_execute(_raise(exc)))
    with pytest.raises(GmailApiError, match="Could not reach") as info:
        await client.list_labels()
    assert not isinstance(info.value, NetworkUnreachableError)


def _transport_error_like_google_auth_httplib2() -> TransportError:
    """Mirror google_auth_httplib2: ``raise TransportError(exc)`` inside the ``except`` block."""
    try:
        try:
            raise httplib2.ServerNotFoundError("Unable to find the server at a.example")
        except httplib2.HttpLib2Error as exc:
            raise TransportError(exc)  # noqa: B904 - the library's exact shape (no ``from``)
    except TransportError as wrapped:
        return wrapped


def _transport_error_with_gaierror_cause() -> TransportError:
    wrapped = TransportError("wrapped")
    wrapped.__cause__ = socket.gaierror(-2, "Name or service not known")
    return wrapped


@pytest.mark.parametrize(
    "exc",
    [
        httplib2.ServerNotFoundError("Unable to find the server at a.example"),
        _transport_error_like_google_auth_httplib2(),
        _transport_error_with_gaierror_cause(),
        socket.gaierror(-2, "Name or service not known"),
    ],
    ids=["server-not-found", "transport-wraps-httplib2-via-context", "transport-cause", "gaierror"],
)
async def test_dns_failures_raise_network_unreachable(
    exc: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    client = GmailClient(lambda: service_with_execute(_raise(exc)))
    with (
        caplog.at_level(logging.WARNING, logger="gmail_mcp.gmail_client"),
        pytest.raises(NetworkUnreachableError) as info,
    ):
        await client.list_labels()
    assert info.value.__suppress_context__ is True  # raised ``from None``
    dns_records = [r for r in caplog.records if r.getMessage() == "gmail dns failure"]
    assert len(dns_records) == 1
    assert dns_records[0].outcome == "dns_failure"  # type: ignore[attr-defined]
    assert dns_records[0].cause == type(exc).__name__  # type: ignore[attr-defined]


async def test_dns_failure_leaves_the_client_usable_and_no_rejection_recorded() -> None:
    client = GmailClient(lambda: service_with_execute(_raise(socket.gaierror(-2, "x"))))
    with pytest.raises(NetworkUnreachableError):
        await client.list_labels()
    assert client.rejected_at is None


async def test_dns_failure_logs_no_host_or_query(caplog: pytest.LogCaptureFixture) -> None:
    exc = httplib2.ServerNotFoundError("Unable to find the server at gmail.googleapis.com")
    client = GmailClient(lambda: service_with_execute(_raise(exc)))
    with caplog.at_level(logging.DEBUG), pytest.raises(NetworkUnreachableError):
        await client.list_labels()
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "gmail.googleapis.com" not in logged
    assert "gmail dns failure" in logged


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


async def test_reset_forces_the_factory_to_run_again() -> None:
    factory = MagicMock(return_value=service_with_execute(lambda **_: {"labels": []}))
    client = GmailClient(factory)
    await client.list_labels()
    client.reset()
    await client.list_labels()
    assert factory.call_count == 2
