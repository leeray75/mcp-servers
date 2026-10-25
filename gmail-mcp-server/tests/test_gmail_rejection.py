"""R1.3: GmailClient records a rejection on RefreshError/401 and clears it on sign-in (reset)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import httplib2
import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from gmail_mcp.errors import AuthRequiredError, GmailApiError
from gmail_mcp.gmail_client import GmailClient


def _service(execute: Any) -> MagicMock:
    service = MagicMock()
    service.users.return_value.labels.return_value.list.return_value.execute = execute
    return service


def _raising(exc: BaseException) -> Any:
    def execute(*_args: Any, **_kwargs: Any) -> None:
        raise exc

    return execute


def _http(status: int) -> HttpError:
    return HttpError(resp=httplib2.Response({"status": str(status)}), content=b"{}")


def test_no_rejection_before_any_call() -> None:
    client = GmailClient(lambda: _service(lambda *a, **k: {}))
    assert client.rejected_at is None


async def test_refresh_error_records_a_timestamped_rejection() -> None:
    client = GmailClient(lambda: _service(_raising(RefreshError("invalid_grant"))))
    with pytest.raises(AuthRequiredError):
        await client.list_labels()
    assert isinstance(client.rejected_at, datetime)
    assert client.rejected_at.tzinfo is not None


async def test_http_401_records_a_rejection() -> None:
    client = GmailClient(lambda: _service(_raising(_http(401))))
    with pytest.raises(AuthRequiredError):
        await client.list_labels()
    assert client.rejected_at is not None


async def test_other_http_errors_do_not_record_a_rejection() -> None:
    client = GmailClient(lambda: _service(_raising(_http(500))))
    with pytest.raises(GmailApiError):
        await client.list_labels()
    assert client.rejected_at is None


async def test_missing_token_is_not_recorded_as_a_rejection() -> None:
    def factory() -> MagicMock:
        raise AuthRequiredError("No Google token found.")

    client = GmailClient(factory)
    with pytest.raises(AuthRequiredError):
        await client.list_labels()
    assert client.rejected_at is None


async def test_reset_clears_the_rejection_and_the_service() -> None:
    calls = {"n": 0}

    def factory() -> MagicMock:
        calls["n"] += 1
        return _service(
            _raising(RefreshError("invalid_grant")) if calls["n"] == 1 else lambda *a, **k: {}
        )

    client = GmailClient(factory)
    with pytest.raises(AuthRequiredError):
        await client.list_labels()
    assert client.rejected_at is not None
    client.reset()
    assert client.rejected_at is None
    await client.list_labels()  # rebuilt service succeeds
    assert calls["n"] == 2
