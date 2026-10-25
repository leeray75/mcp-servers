"""R1: the auth state model. Temp token files only; no network, no real credentials."""

from __future__ import annotations

import datetime as dt
import os
import socket
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from gmail_mcp.auth import write_token
from gmail_mcp.auth_state import AuthStatus, compute_auth_status
from gmail_mcp.config import SCOPE_COMPOSE, SCOPE_READONLY, Settings
from gmail_mcp.errors import auth_url
from tests.conftest import FAKE_AUTH_TOKEN, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, FAKE_REFRESH_TOKEN

SECRETS = (FAKE_REFRESH_TOKEN, FAKE_CLIENT_SECRET, FAKE_AUTH_TOKEN, FAKE_CLIENT_ID)


def _rejection() -> dt.datetime:
    """A rejection newer than any token written earlier in the test (M1: older ones are stale)."""
    return dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5)


def _age_token(path: Path, days: float) -> None:
    mtime = time.time() - days * 86_400
    os.utime(path, (mtime, mtime))


def _signed_in_token(settings: Settings) -> Path:
    path = Path(settings.token_path)
    write_token(path, FAKE_REFRESH_TOKEN, [SCOPE_READONLY, SCOPE_COMPOSE])
    return path


def test_missing_token_is_not_signed_in(settings: Settings) -> None:
    status = compute_auth_status(settings, None)
    assert status.state == "not_signed_in"
    assert status.signed_in is False
    assert status.token_age_days is None
    assert status.scopes_granted == []
    assert status.rejected_at is None


def test_signed_in_with_all_scopes(settings: Settings) -> None:
    _signed_in_token(settings)
    status = compute_auth_status(settings, None)
    assert status.state == "signed_in"
    assert status.signed_in is True
    assert status.scopes_missing == []
    assert status.scopes_granted == sorted([SCOPE_READONLY, SCOPE_COMPOSE])
    assert status.next_step == "No action needed."
    assert status.warnings == []


def test_unreadable_token_is_token_unreadable(settings: Settings) -> None:
    path = Path(settings.token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not json", encoding="utf-8")
    status = compute_auth_status(settings, None)
    assert status.state == "token_unreadable"
    assert status.signed_in is False


def test_scope_shortfall_lists_missing_scopes(settings: Settings, tmp_path: Path) -> None:
    write_token(Path(settings.token_path), FAKE_REFRESH_TOKEN, [SCOPE_READONLY])
    status = compute_auth_status(settings, None)
    assert status.state == "scopes_insufficient"
    assert status.scopes_missing == [SCOPE_COMPOSE]
    assert status.scopes_granted == [SCOPE_READONLY]


def test_rejection_is_reported_with_its_time(settings: Settings) -> None:
    _signed_in_token(settings)
    rejection = _rejection()
    status = compute_auth_status(settings, rejection)
    assert status.state == "rejected"
    assert status.signed_in is False
    assert status.rejected_at == rejection.isoformat()
    assert any("myaccount.google.com/permissions" in w for w in status.warnings)
    assert any("password change" in w for w in status.warnings)


@pytest.mark.parametrize("scenario", ["missing", "unreadable", "scope_short"])
def test_rejected_takes_precedence(settings: Settings, scenario: str) -> None:
    path = Path(settings.token_path)
    if scenario == "unreadable":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{", encoding="utf-8")
    elif scenario == "scope_short":
        write_token(path, FAKE_REFRESH_TOKEN, [SCOPE_READONLY])
    status = compute_auth_status(settings, _rejection())
    assert status.state == "rejected"


def test_unreadable_takes_precedence_over_missing_scopes_only_when_unreadable(
    settings: Settings,
) -> None:
    path = Path(settings.token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")  # valid JSON, wrong shape
    assert compute_auth_status(settings, None).state == "token_unreadable"


def test_expiry_warning_below_six_days_is_absent(settings: Settings) -> None:
    _age_token(_signed_in_token(settings), 5.9)
    status = compute_auth_status(settings, None)
    assert status.state == "signed_in"
    assert status.token_age_days is not None and 5.8 < status.token_age_days < 6.0
    assert status.warnings == []


def test_expiry_warning_at_six_days(settings: Settings) -> None:
    _age_token(_signed_in_token(settings), 6.0)
    status = compute_auth_status(settings, None)
    assert status.token_age_days is not None and status.token_age_days >= 6.0
    assert len(status.warnings) == 1
    assert "7 days" in status.warnings[0]
    assert "In production" in status.warnings[0]


def test_next_step_for_each_problem_state_is_one_sentence_with_the_link(
    settings: Settings,
) -> None:
    status = compute_auth_status(settings, None)
    assert status.state == "not_signed_in"
    assert status.next_step.startswith("Ask the user to open ")
    assert status.next_step.endswith("then call auth_status and retry.")
    assert f"{auth_url(settings.host_port)} " in status.next_step


def test_open_commands_are_built_from_the_sign_in_url(settings: Settings) -> None:
    url = auth_url(settings.host_port)
    status = compute_auth_status(settings, None)
    assert status.sign_in_url == url
    assert status.open_commands == {
        "windows": f'Start-Process "{url}"',
        "macos": f'open "{url}"',
        "linux": f'xdg-open "{url}"',
    }


def test_sign_in_url_follows_host_port(settings: Settings) -> None:
    custom = settings.model_copy(update={"host_port": 9001})
    assert compute_auth_status(custom, None).sign_in_url == "http://127.0.0.1:9001/auth"


def test_output_never_contains_credentials(settings: Settings) -> None:
    _age_token(_signed_in_token(settings), 7.0)
    for rejection in (None, _rejection()):
        dumped = compute_auth_status(settings, rejection).model_dump_json()
        for secret in SECRETS:
            assert secret not in dumped


def test_status_makes_no_network_calls(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("auth_state must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    _signed_in_token(settings)
    assert compute_auth_status(settings, _rejection()).state == "rejected"


def test_status_is_frozen(settings: Settings) -> None:
    status = compute_auth_status(settings, None)
    assert isinstance(status, AuthStatus)
    with pytest.raises(ValidationError):  # assignment to a frozen model
        status.state = "signed_in"
