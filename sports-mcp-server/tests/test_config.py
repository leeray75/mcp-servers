from __future__ import annotations

import pytest

from sports_mcp.config import ConfigError, Settings, load_settings
from tests.conftest import FAKE_AUTH_TOKEN


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", FAKE_AUTH_TOKEN)
    s = load_settings()
    assert s.espn_base_url == "https://site.api.espn.com"
    assert s.espn_host == "site.api.espn.com"
    assert s.default_timezone == "America/New_York"
    assert s.tz.key == "America/New_York"
    assert s.max_response_chars == 20_000
    assert s.ttl_live_seconds == 15
    assert s.stale_if_error_seconds == 300
    assert s.port == 8000


def test_stdio_mode_needs_no_token() -> None:
    assert load_settings(require_auth_token=False).mcp_auth_token is None


def test_http_mode_requires_token() -> None:
    with pytest.raises(ConfigError, match="MCP_AUTH_TOKEN"):
        load_settings()


@pytest.mark.parametrize(
    ("value", "needle"),
    [
        ("replace-me-with-at-least-32-random-characters", "placeholder"),
        ("short", "at least 32"),
        ("   ", "empty"),
    ],
)
def test_bad_tokens_are_rejected_without_echoing_the_value(
    monkeypatch: pytest.MonkeyPatch, value: str, needle: str
) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", value)
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert needle in str(info.value)
    assert value.strip() == "" or value not in str(info.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ESPN_BASE_URL", "ftp://x"),
        ("ESPN_BASE_URL", "https://user:pw@site.api.espn.com"),
        ("ESPN_BASE_URL", "https://site.api.espn.com?x=1"),
        ("ESPN_BASE_URL", "not a url"),
        ("DEFAULT_TIMEZONE", "Mars/Olympus"),
        ("LOG_LEVEL", "LOUD"),
        ("PORT", "0"),
        ("ESPN_TIMEOUT_SECONDS", "0"),
        ("ESPN_MAX_CONCURRENCY", "100"),
        ("TTL_LIVE_SECONDS", "-1"),
    ],
)
def test_invalid_values_name_the_field(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", FAKE_AUTH_TOKEN)
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert name in str(info.value)
    assert FAKE_AUTH_TOKEN not in str(info.value)


def test_overrides_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", FAKE_AUTH_TOKEN)
    monkeypatch.setenv("ESPN_BASE_URL", "http://espn.test:8080/")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "Europe/London")
    monkeypatch.setenv("TTL_LIVE_SECONDS", "5")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    s = load_settings()
    assert s.espn_base_url == "http://espn.test:8080"
    assert s.espn_host == "espn.test:8080"
    assert s.default_timezone == "Europe/London"
    assert s.ttl_live_seconds == 5
    assert s.log_level == "DEBUG"


def test_token_is_secret_in_repr() -> None:
    assert FAKE_AUTH_TOKEN not in repr(Settings(mcp_auth_token=FAKE_AUTH_TOKEN))  # type: ignore[arg-type]
