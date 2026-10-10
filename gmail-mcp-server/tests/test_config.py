from __future__ import annotations

import pytest

from gmail_mcp.config import (
    SCOPE_COMPOSE,
    SCOPE_READONLY,
    ConfigError,
    Settings,
    load_settings,
)
from tests.conftest import FAKE_AUTH_TOKEN, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET


def _set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", FAKE_CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", FAKE_CLIENT_SECRET)
    monkeypatch.setenv("MCP_AUTH_TOKEN", FAKE_AUTH_TOKEN)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    s = load_settings()
    assert s.scopes == [SCOPE_READONLY, SCOPE_COMPOSE]
    assert (s.token_path, s.host, s.port) == ("/data/token.json", "0.0.0.0", 8000)
    assert (s.log_level, s.max_results_cap, s.max_body_chars) == ("INFO", 50, 20_000)


def test_missing_required_names_fields_only() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings()
    message = str(info.value)
    assert "GOOGLE_CLIENT_ID" in message
    assert "MCP_AUTH_TOKEN" in message


def test_short_token_rejected_without_echoing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    secret = "short-but-secret-value"
    monkeypatch.setenv("MCP_AUTH_TOKEN", secret)
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert secret not in str(info.value)
    assert "at least 32" in str(info.value)


@pytest.mark.parametrize("name", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "MCP_AUTH_TOKEN"])
def test_placeholders_rejected(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv(name, "replace-me" + "x" * 40)
    with pytest.raises(ConfigError, match="placeholder"):
        load_settings()


def test_scope_aliases_are_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("GMAIL_SCOPES", "gmail.readonly")
    assert load_settings().scopes == [SCOPE_READONLY]


@pytest.mark.parametrize(
    "value",
    [
        "https://mail.google.com/",
        "gmail.readonly https://www.googleapis.com/auth/gmail.modify",
        "gmail.send",
        "   ",
    ],
)
def test_scope_must_be_subset_of_allowed(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("GMAIL_SCOPES", value)
    with pytest.raises(ConfigError, match="GMAIL_SCOPES"):
        load_settings()


def test_scope_error_does_not_echo_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("GMAIL_SCOPES", "https://mail.google.com/")
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert "mail.google.com" not in str(info.value)


def test_log_level_normalised_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert load_settings().log_level == "DEBUG"
    monkeypatch.setenv("LOG_LEVEL", "chatty")
    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        load_settings()


@pytest.mark.parametrize(
    ("name", "value"), [("PORT", "0"), ("PORT", "70000"), ("MAX_RESULTS_CAP", "0")]
)
def test_numeric_bounds(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        load_settings()


def test_unrelated_env_vars_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("HOST_PORT", "8765")
    assert isinstance(load_settings(), Settings)


def test_secrets_not_in_repr(settings: Settings) -> None:
    text = repr(settings)
    assert FAKE_CLIENT_SECRET not in text
    assert FAKE_AUTH_TOKEN not in text


def test_host_port_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    assert load_settings().host_port == 8765
    monkeypatch.setenv("HOST_PORT", "9001")
    assert load_settings().host_port == 9001


@pytest.mark.parametrize("value", ["0", "65536", "abc"])
def test_host_port_out_of_range_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("HOST_PORT", value)
    with pytest.raises(ConfigError, match="HOST_PORT"):
        load_settings()
