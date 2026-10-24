"""Settings for the player tools (spec section 6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sports_mcp.config import Settings


def test_player_settings_defaults() -> None:
    settings = Settings()
    assert settings.ttl_player_search_seconds == 3600
    assert settings.ttl_player_seconds == 300
    assert settings.player_max_seasons == 10


def test_player_settings_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTL_PLAYER_SEARCH_SECONDS", "120")
    monkeypatch.setenv("TTL_PLAYER_SECONDS", "60")
    monkeypatch.setenv("PLAYER_MAX_SEASONS", "25")
    settings = Settings()
    assert (settings.ttl_player_search_seconds, settings.ttl_player_seconds) == (120, 60)
    assert settings.player_max_seasons == 25


@pytest.mark.parametrize(
    ("name", "value"),
    [("PLAYER_MAX_SEASONS", "0"), ("PLAYER_MAX_SEASONS", "26"), ("TTL_PLAYER_SECONDS", "-1")],
)
def test_player_settings_bounds_are_enforced(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings()
