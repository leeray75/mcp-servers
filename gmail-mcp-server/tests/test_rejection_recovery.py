"""Review M1 / m1: a recorded rejection must not outlive a later sign-in or a successful call,
and reset() must not race with an in-flight call that records a rejection."""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httplib2
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from gmail_mcp.auth import write_token
from gmail_mcp.auth_state import compute_auth_status
from gmail_mcp.config import SCOPE_COMPOSE, SCOPE_READONLY, Settings
from gmail_mcp.errors import AuthRequiredError
from gmail_mcp.gmail_client import GmailClient
from tests.conftest import FAKE_REFRESH_TOKEN


def _set_mtime(path: Path, when: dt.datetime) -> None:
    os.utime(path, (when.timestamp(), when.timestamp()))


def _write_token(settings: Settings, mtime: dt.datetime) -> Path:
    path = Path(settings.token_path)
    write_token(path, FAKE_REFRESH_TOKEN, [SCOPE_READONLY, SCOPE_COMPOSE])
    _set_mtime(path, mtime)
    return path


# ------------------------------------------------ out-of-process re-auth (M1, state side)


def test_sign_in_after_the_rejection_clears_it(settings: Settings) -> None:
    """scripts/auth (auth_cli) writes the token in another process: the newer token wins."""
    now = dt.datetime.now(dt.UTC)
    rejected = now - dt.timedelta(hours=2)
    _write_token(settings, mtime=now - dt.timedelta(hours=1))
    status = compute_auth_status(settings, rejected)
    assert status.state == "signed_in"
    assert status.signed_in is True
    assert status.rejected_at is None
    assert status.next_step == "No action needed."


def test_rejection_newer_than_the_token_still_applies(settings: Settings) -> None:
    now = dt.datetime.now(dt.UTC)
    _write_token(settings, mtime=now - dt.timedelta(hours=2))
    status = compute_auth_status(settings, now - dt.timedelta(hours=1))
    assert status.state == "rejected"
    assert status.rejected_at is not None


def test_rejection_without_a_token_file_still_applies(settings: Settings) -> None:
    rejected = dt.datetime.now(dt.UTC)
    assert compute_auth_status(settings, rejected).state == "rejected"


# ------------------------------------------------------------ successful call (M1, client)


async def test_a_successful_call_clears_the_rejection() -> None:
    calls = {"n": 0}

    def factory() -> MagicMock:
        calls["n"] += 1
        service = MagicMock()
        leaf = service.users.return_value.labels.return_value.list.return_value
        if calls["n"] == 1:
            leaf.execute.side_effect = RefreshError("invalid_grant")
        else:
            leaf.execute.return_value = {"labels": []}
        return service

    client = GmailClient(factory)
    with contextlib.suppress(AuthRequiredError):
        await client.list_labels()
    assert client.rejected_at is not None
    await client.list_labels()  # the rebuilt credentials work
    assert client.rejected_at is None


async def test_http_401_then_success_clears_too() -> None:
    http_401 = HttpError(resp=httplib2.Response({"status": "401"}), content=b"{}")
    state: dict[str, Any] = {"fail": True}

    def execute(*_a: Any, **_k: Any) -> Any:
        if state["fail"]:
            raise http_401
        return {"labels": []}

    def factory() -> MagicMock:
        service = MagicMock()
        service.users.return_value.labels.return_value.list.return_value.execute = execute
        return service

    client = GmailClient(factory)
    with contextlib.suppress(AuthRequiredError):
        await client.list_labels()
    assert client.rejected_at is not None
    state["fail"] = False
    await client.list_labels()
    assert client.rejected_at is None


# ----------------------------------------------------------------- reset race (m1)


def test_reset_waits_for_an_in_flight_call_and_clears_its_rejection() -> None:
    client = GmailClient(lambda: MagicMock())
    entered = threading.Event()
    release = threading.Event()

    def in_flight_call_fails() -> None:
        # Mirrors _execute: the rejection is recorded while the call holds the client lock.
        with client._lock:
            client._record_rejection()
            entered.set()
            release.wait(5)

    worker = threading.Thread(target=in_flight_call_fails)
    worker.start()
    assert entered.wait(5)
    resetter = threading.Thread(target=client.reset)
    resetter.start()
    time.sleep(0.05)
    assert resetter.is_alive(), "reset() must wait behind the in-flight call"
    release.set()
    worker.join(5)
    resetter.join(5)
    assert client.rejected_at is None
