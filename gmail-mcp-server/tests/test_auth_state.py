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


def test_next_step_for_each_problem_state_carries_the_guardrails(settings: Settings) -> None:
    status = compute_auth_status(settings, None)
    assert status.state == "not_signed_in"
    assert status.next_step.startswith("Open the sign-in page for the user:")
    assert "run the open command for their operating system from open_commands in a shell" in (
        status.next_step
    )
    assert f"{auth_url(settings.host_port)} " in status.next_step  # copy-and-paste link
    assert "Do not use browser-automation tools" in status.next_step
    assert "never type or capture credentials" in status.next_step
    assert status.next_step.endswith("retry only when it reports ready_to_retry: true.")


def _set_problem(settings: Settings, scenario: str) -> dt.datetime | None:
    """Put the store into one problem state; returns the rejection time when one applies."""
    path = Path(settings.token_path)
    if scenario == "missing":
        return None
    if scenario == "unreadable":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{", encoding="utf-8")
        return None
    if scenario == "scope_short":
        write_token(path, FAKE_REFRESH_TOKEN, [SCOPE_READONLY])
        return None
    _signed_in_token(settings)  # rejected
    return _rejection()


PROBLEM_SCENARIOS = ["missing", "unreadable", "scope_short", "rejected"]


@pytest.mark.parametrize("scenario", PROBLEM_SCENARIOS)
def test_ready_to_retry_is_true_only_when_signed_in(settings: Settings, scenario: str) -> None:
    rejection = _set_problem(settings, scenario)
    status = compute_auth_status(settings, rejection)
    assert status.ready_to_retry is False
    assert status.signed_in is False


def test_ready_to_retry_is_true_when_signed_in(settings: Settings) -> None:
    _signed_in_token(settings)
    status = compute_auth_status(settings, None)
    assert status.state == "signed_in"
    assert status.ready_to_retry is True


def test_checked_at_is_a_recent_utc_timestamp_on_every_call(settings: Settings) -> None:
    before = dt.datetime.now(dt.UTC)
    status = compute_auth_status(settings, None)
    parsed = dt.datetime.fromisoformat(status.checked_at)
    assert parsed.utcoffset() == dt.timedelta(0)
    assert before - dt.timedelta(seconds=1) <= parsed <= dt.datetime.now(dt.UTC)


@pytest.mark.parametrize("scenario", PROBLEM_SCENARIOS)
def test_pending_sign_in_replaces_every_problem_state(settings: Settings, scenario: str) -> None:
    rejection = _set_problem(settings, scenario)
    status = compute_auth_status(settings, rejection, sign_in_pending=True)
    assert status.state == "sign_in_in_progress"
    assert status.signed_in is False
    assert status.ready_to_retry is False
    assert status.next_step.startswith("A sign-in is already open.")
    assert "ready_to_retry stays false until state is signed_in" in status.next_step


def test_pending_sign_in_keeps_the_rejection_time_and_its_warning(settings: Settings) -> None:
    rejection = _set_problem(settings, "rejected")
    status = compute_auth_status(settings, rejection, sign_in_pending=True)
    assert status.state == "sign_in_in_progress"
    assert rejection is not None
    assert status.rejected_at == rejection.isoformat()
    assert any("password change" in w for w in status.warnings)


def test_pending_sign_in_never_changes_a_signed_in_status(settings: Settings) -> None:
    _signed_in_token(settings)
    status = compute_auth_status(settings, None, sign_in_pending=True)
    assert status.state == "signed_in"
    assert status.signed_in is True
    assert status.ready_to_retry is True
    assert status.next_step == "No action needed."


def test_status_fields_are_exact_and_pending_adds_no_detail(settings: Settings) -> None:
    expected = {
        "state",
        "signed_in",
        "ready_to_retry",
        "checked_at",
        "sign_in_url",
        "scopes_granted",
        "scopes_missing",
        "token_age_days",
        "rejected_at",
        "warnings",
        "next_step",
        "open_commands",
    }
    status = compute_auth_status(settings, None, sign_in_pending=True)
    assert set(status.model_dump()) == expected
    dumped = status.model_dump_json()
    for secret in SECRETS:
        assert secret not in dumped


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
