"""R1-R3: configuration, validation messages, and the secret-free error text."""

from __future__ import annotations

import pytest

from discord_mcp.config import ConfigError, Settings, load_settings
from tests.conftest import FAKE_AUTH_TOKEN, FAKE_BOT_TOKEN, GUILD, make_settings


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    base = {
        "MCP_AUTH_TOKEN": FAKE_AUTH_TOKEN,
        "DISCORD_BOT_TOKEN": FAKE_BOT_TOKEN,
        "DISCORD_ALLOWED_GUILD_IDS": GUILD,
    }
    base.update(values)
    for key, value in base.items():
        monkeypatch.setenv(key, value)


def test_valid_environment_loads_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    settings = load_settings()
    assert settings.discord_api_base == "https://discord.com/api/v10"
    assert settings.guild_ids == frozenset({GUILD})
    assert settings.discord_enable_search is True
    assert settings.discord_enable_member_list is True
    assert settings.log_level == "INFO"


@pytest.mark.parametrize(
    "field",
    ["MCP_AUTH_TOKEN", "DISCORD_BOT_TOKEN", "DISCORD_ALLOWED_GUILD_IDS"],
)
def test_required_fields_are_named_when_missing(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    _env(monkeypatch)
    monkeypatch.delenv(field)
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert field in str(info.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("MCP_AUTH_TOKEN", "replace-me-with-at-least-32-random-characters"),
        ("MCP_AUTH_TOKEN", "short-token"),
        ("DISCORD_BOT_TOKEN", "replace-me-token"),
        ("DISCORD_BOT_TOKEN", "not-a-bot-token"),
        ("DISCORD_BOT_TOKEN", "   "),
        ("DISCORD_ALLOWED_GUILD_IDS", "replace-me-guild"),
        ("DISCORD_ALLOWED_GUILD_IDS", "123"),
        ("DISCORD_ALLOWED_GUILD_IDS", f"{GUILD},{GUILD}"),
        ("DISCORD_ALLOWED_GUILD_IDS", f"{GUILD},,"),
        ("DISCORD_ALLOWED_GUILD_IDS", "x" * 15),
        ("DISCORD_ALLOWED_GUILD_IDS", ",".join(str(10**15 + i) for i in range(101))),
        ("DISCORD_API_BASE", "http://discord.com/api/v10"),
        ("DISCORD_API_BASE", "https://evil.example.com/api/v10"),
        ("DISCORD_API_BASE", "https://discord.com/api/v9"),
        ("DISCORD_API_BASE", "https://discord.com:8443/api/v10"),
        ("DISCORD_API_BASE", "https://user:pw@discord.com/api/v10"),
        ("DISCORD_API_BASE", "https://discord.com/api/v10?x=1"),
        ("DISCORD_USER_AGENT", "curl/8.0"),
        ("DISCORD_APPLICATION_ID", "abc"),
        ("DISCORD_TIMEOUT_SECONDS", "0"),
        ("DISCORD_MAX_RESPONSE_BYTES", "1000"),
        ("DISCORD_MAX_429_RETRIES", "11"),
        ("DISCORD_INVALID_REQUEST_BUDGET", "5"),
        ("TOOL_CALLS_PER_MINUTE", "0"),
        ("LOG_LEVEL", "LOUD"),
    ],
)
def test_invalid_values_are_rejected_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    _env(monkeypatch, **{field: value})
    with pytest.raises(ConfigError) as info:
        load_settings()
    message = str(info.value)
    assert field in message
    if value.strip():
        assert value not in message


def test_api_base_accepts_newer_versions_and_strips_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, DISCORD_API_BASE="https://discord.com/api/v11/")
    assert load_settings().discord_api_base == "https://discord.com/api/v11"


def test_user_agent_must_match_the_discord_pattern() -> None:
    settings = make_settings(discord_user_agent="DiscordBot (https://example.com, 1.0)")
    assert settings.discord_user_agent == "DiscordBot (https://example.com, 1.0)"


def test_allowlist_is_normalised_and_split() -> None:
    settings = make_settings(discord_allowed_guild_ids=f" {GUILD} , 777777777777777777 ")
    assert settings.discord_allowed_guild_ids == f"{GUILD},777777777777777777"
    assert settings.guild_ids == frozenset({GUILD, "777777777777777777"})


def test_intent_overrides_can_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, DISCORD_ENABLE_SEARCH="false", DISCORD_ENABLE_MEMBER_LIST="false")
    settings = load_settings()
    assert settings.discord_enable_search is False
    assert settings.discord_enable_member_list is False


def test_application_id_is_optional_and_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, DISCORD_APPLICATION_ID="")
    assert load_settings().discord_application_id is None
    settings = make_settings(discord_application_id="222222222222222222")
    assert settings.discord_application_id == "222222222222222222"


def test_settings_type_is_importable() -> None:
    assert (
        Settings.model_validate(
            {
                "mcp_auth_token": FAKE_AUTH_TOKEN,
                "discord_bot_token": FAKE_BOT_TOKEN,
                "discord_allowed_guild_ids": GUILD,
            }
        ).api_host
        == "discord.com"
    )
