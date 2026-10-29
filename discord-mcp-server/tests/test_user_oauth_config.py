"""U1-U5, U28: user OAuth configuration, cross-field validation and the tool plan."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from discord_mcp.capabilities import USER_OAUTH_TOOLS, plan_tools
from discord_mcp.config import ConfigError, load_settings
from discord_mcp.discord_client import BootProbe
from tests.conftest import FAKE_AUTH_TOKEN, FAKE_BOT_TOKEN, GUILD, make_settings
from tests.user_oauth_fakes import APP_ID, FAKE_SECRET, user_settings

BOT_TOOLS = (
    "list_guilds",
    "get_guild",
    "list_channels",
    "get_channel",
    "list_messages",
    "get_message",
    "search_messages",
    "list_reactions",
    "list_pins",
    "list_threads",
    "list_members",
    "get_member",
    "search_members",
    "get_audit_log",
)
FLAGS_ALL = 278528  # message content and guild members intents, as in the fixture
PROBE_OK = BootProbe("ok", bot_user_id="111", flags=FLAGS_ALL, flags_source="flags")


def test_switch_off_ignores_every_oauth_setting() -> None:
    """U3: with the switch off, nothing new is validated or read for behaviour."""
    settings = make_settings(
        discord_oauth_client_secret="replace-me-please",
        discord_oauth_token_file="relative/path.json",
    )
    assert settings.discord_user_oauth is False
    assert plan_tools(settings, PROBE_OK).user_oauth == "off"


def test_switch_on_requires_the_client_secret_and_names_the_field() -> None:
    """U1-U2: the error names the field and never echoes a value."""
    with pytest.raises(ValidationError) as caught:
        make_settings(
            discord_user_oauth=True,
            discord_application_id=APP_ID,
            discord_oauth_client_secret=None,
        )
    message = str(caught.value)
    assert "DISCORD_OAUTH_CLIENT_SECRET" in message
    assert FAKE_SECRET not in message


def test_switch_on_rejects_the_placeholder_secret_without_echoing_it() -> None:
    with pytest.raises(ValidationError) as caught:
        make_settings(
            discord_user_oauth=True,
            discord_application_id=APP_ID,
            discord_oauth_client_secret="replace-me-with-your-secret",
        )
    message = str(caught.value)
    assert "DISCORD_OAUTH_CLIENT_SECRET" in message
    assert "replace-me-with-your-secret" not in message


def test_switch_on_requires_the_application_id() -> None:
    with pytest.raises(ValidationError) as caught:
        make_settings(
            discord_user_oauth=True,
            discord_application_id=None,
            discord_oauth_client_secret=FAKE_SECRET,
        )
    assert "DISCORD_APPLICATION_ID" in str(caught.value)


def test_switch_on_requires_an_absolute_token_path(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as caught:
        user_settings(tmp_path, discord_oauth_token_file="relative.json")
    assert "DISCORD_OAUTH_TOKEN_FILE must be an absolute path" in str(caught.value)


@pytest.mark.parametrize("port", [0, 65536])
def test_host_port_is_range_checked(port: int) -> None:
    with pytest.raises(ValidationError) as caught:
        make_settings(
            discord_user_oauth=True,
            discord_application_id=APP_ID,
            discord_oauth_client_secret=FAKE_SECRET,
            host_port=port,
        )
    assert "host_port" in str(caught.value)


def test_redirect_uri_and_sign_in_url_derive_from_host_port() -> None:
    settings = make_settings(host_port=9001)
    assert settings.redirect_uri == "http://127.0.0.1:9001/oauth/callback"
    assert settings.sign_in_url == "http://127.0.0.1:9001/oauth/start"
    assert make_settings().host_port == 8769


def test_load_settings_reports_the_model_error_with_a_field_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model-level error (no field location) still names the field, never a value."""
    monkeypatch.setenv("MCP_AUTH_TOKEN", FAKE_AUTH_TOKEN)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("DISCORD_ALLOWED_GUILD_IDS", GUILD)
    monkeypatch.setenv("DISCORD_USER_OAUTH", "true")
    monkeypatch.setenv("DISCORD_APPLICATION_ID", APP_ID)
    with pytest.raises(ConfigError) as caught:
        load_settings()
    assert "DISCORD_OAUTH_CLIENT_SECRET" in str(caught.value)
    assert FAKE_BOT_TOKEN not in str(caught.value)


def test_plan_on_appends_the_five_tools_after_the_bot_tools(tmp_path: Path) -> None:
    """U5: with the switch on the plan adds the five tools, in U5 order, after the bot tools."""
    plan = plan_tools(user_settings(tmp_path), PROBE_OK)
    assert plan.user_oauth == "on(not_connected)"
    assert plan.user_tools == USER_OAUTH_TOOLS
    assert USER_OAUTH_TOOLS == (
        "discord_account_status",
        "connect_discord_account",
        "disconnect_discord_account",
        "list_my_guilds",
        "get_guild_invite_link",
    )
    assert plan.registered == BOT_TOOLS


def test_plan_reports_the_account_state_in_the_boot_line(tmp_path: Path) -> None:
    """U28: connected, not_connected and token_file_invalid are the three on() states."""
    settings = user_settings(tmp_path)
    for state in ("connected", "not_connected", "token_file_invalid"):
        assert plan_tools(settings, PROBE_OK, state).user_oauth == f"on({state})"


def test_plan_mismatched_application_id_disables_the_user_tools(tmp_path: Path) -> None:
    """U4: a probed application id that differs omits the five tools (and the routes)."""
    probe = BootProbe("ok", bot_user_id="111", flags=FLAGS_ALL, application_id="999999999999999999")
    plan = plan_tools(user_settings(tmp_path), probe)
    assert plan.user_oauth == "disabled(application_id_mismatch)"
    assert plan.user_tools == ()


def test_plan_unprobed_application_id_is_trusted(tmp_path: Path) -> None:
    """U4: when the probe could not read the application id, the setting is trusted."""
    plan = plan_tools(user_settings(tmp_path), BootProbe("auth"), "connected")
    assert plan.user_oauth == "on(connected)"
    assert plan.user_tools == USER_OAUTH_TOOLS


def test_malformed_a3_settings_do_not_stop_an_off_mode_boot() -> None:
    """A3.1 (U3): with the switch off, malformed HOST_PORT and kill switches are ignored, not validated."""
    settings = make_settings(
        host_port=0,
        discord_oauth_pkce="maybe",
        discord_oauth_url_elicitation="maybe",
        discord_oauth_client_secret="replace-me-later",
        discord_oauth_token_file="relative.json",
    )
    assert settings.host_port == 8769
    assert settings.discord_oauth_pkce is True
    assert settings.discord_oauth_url_elicitation is True
    assert plan_tools(settings, PROBE_OK).user_oauth == "off"


def test_the_same_malformed_values_are_refused_when_the_switch_is_on(tmp_path: Path) -> None:
    """A3.1 (U1): once the switch is on the ranges and formats apply again."""
    with pytest.raises(ValidationError) as caught:
        user_settings(tmp_path, discord_oauth_pkce="maybe")
    assert "discord_oauth_pkce" in str(caught.value)
