"""Environment-driven configuration (R1-R3, section 12).

This is the only module allowed to read the process environment. Validation failures raise
:class:`ConfigError` carrying field names and constraint messages only, never the offending
values (they may be secrets).
"""

from __future__ import annotations

import re
from typing import Annotated, Final
from urllib.parse import urlsplit

from pydantic import (
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from discord_mcp.snowflake import is_snowflake

PLACEHOLDER_PREFIX: Final = "replace-me"
MIN_AUTH_TOKEN_LENGTH: Final = 32
MAX_ALLOWED_GUILDS: Final = 100
DEFAULT_API_BASE: Final = "https://discord.com/api/v10"
DEFAULT_USER_AGENT: Final = "DiscordBot (https://github.com/leeray75/mcp-servers, 0.1.0)"
_OAUTH_DEFAULTS: Final[dict[str, object]] = {
    "host_port": 8769,
    "discord_oauth_url_elicitation": True,
    "discord_oauth_pkce": True,
}
DEFAULT_TOKEN_FILE: Final = "/data/discord_user_token.json"  # noqa: S105 - a path, not a secret
_BOT_TOKEN_RE: Final = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){2}$")
_USER_AGENT_RE: Final = re.compile(r"^DiscordBot \(\S+, \S+\)$")
_API_PATH_RE: Final = re.compile(r"^/api/v([0-9]+)$")
_LOG_LEVELS: Final = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigError(Exception):
    """Raised when configuration is missing or invalid. Never contains secret values."""


def _check_secret(value: SecretStr, name: str, min_length: int = 1) -> SecretStr:
    raw = value.get_secret_value().strip()
    if not raw:
        raise ValueError(f"{name} must not be empty")
    if raw.startswith(PLACEHOLDER_PREFIX):
        raise ValueError(f"{name} still has its placeholder value; edit .env")
    if len(raw) < min_length:
        raise ValueError(f"{name} must be at least {min_length} characters")
    return SecretStr(raw)


class Settings(BaseSettings):
    """All runtime configuration, loaded from environment variables (names are upper-case)."""

    model_config = SettingsConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
        case_sensitive=False,
    )

    # --- required secrets and scope (R2) ---
    mcp_auth_token: SecretStr
    discord_bot_token: SecretStr
    discord_allowed_guild_ids: str

    # --- optional identity check (section 12) ---
    discord_application_id: str | None = None

    # --- user OAuth guild discovery (A3, U1). Secret, path and switches are read and checked only
    # when DISCORD_USER_OAUTH is true (model validator below). HOST_PORT is bounded only when the
    # switch is on (A3.1 U3). ---
    discord_user_oauth: bool = False
    discord_oauth_client_secret: SecretStr | None = None
    host_port: Annotated[int, Field(ge=1, le=65535)] = 8769
    discord_oauth_token_file: str = DEFAULT_TOKEN_FILE
    discord_oauth_url_elicitation: bool = True
    discord_oauth_pkce: bool = True

    # --- Discord HTTP (R3) ---
    discord_api_base: str = DEFAULT_API_BASE
    discord_user_agent: str = DEFAULT_USER_AGENT

    # --- intent overrides: can only disable (R6) ---
    discord_enable_search: bool = True
    discord_enable_member_list: bool = True
    # Force-enable escape hatches for an intent verified in the portal (A1-R6). Default off.
    discord_force_message_content: bool = False
    discord_force_guild_members: bool = False

    # --- limits ---
    discord_timeout_seconds: Annotated[float, Field(ge=1, le=60)] = 10.0
    discord_max_response_bytes: Annotated[int, Field(ge=65_536, le=16_777_216)] = 4_194_304
    discord_max_rate_limit_wait_seconds: Annotated[float, Field(ge=0, le=120)] = 30.0
    discord_max_429_retries: Annotated[int, Field(ge=0, le=10)] = 3
    discord_search_index_retries: Annotated[int, Field(ge=0, le=5)] = 2
    discord_global_requests_per_second: Annotated[float, Field(ge=1, le=50)] = 40.0
    discord_invalid_request_budget: Annotated[int, Field(ge=10, le=9000)] = 1000

    # --- cache (R37) ---
    ttl_metadata_seconds: Annotated[int, Field(ge=0, le=3600)] = 300
    cache_max_entries: Annotated[int, Field(ge=8, le=10_000)] = 2000
    cache_max_mb: Annotated[int, Field(ge=8, le=400)] = 64

    # --- tool-call budget (R36) ---
    tool_calls_per_minute: Annotated[int, Field(ge=1, le=6000)] = 60
    tool_calls_per_minute_per_tool: Annotated[int, Field(ge=1, le=6000)] = 30

    log_level: str = "INFO"

    @field_validator("mcp_auth_token", mode="after")
    @classmethod
    def _check_auth_token(cls, value: SecretStr) -> SecretStr:
        return _check_secret(value, "MCP_AUTH_TOKEN", MIN_AUTH_TOKEN_LENGTH)

    @field_validator("discord_bot_token", mode="after")
    @classmethod
    def _check_bot_token(cls, value: SecretStr) -> SecretStr:
        checked = _check_secret(value, "DISCORD_BOT_TOKEN")
        if not _BOT_TOKEN_RE.fullmatch(checked.get_secret_value()):
            raise ValueError("DISCORD_BOT_TOKEN is not shaped like a Discord bot token")
        return checked

    @field_validator("discord_allowed_guild_ids", mode="after")
    @classmethod
    def _check_allowlist(cls, value: str) -> str:
        if value.strip().startswith(PLACEHOLDER_PREFIX):
            raise ValueError("DISCORD_ALLOWED_GUILD_IDS still has its placeholder value; edit .env")
        parts = [part.strip() for part in value.split(",")]
        if not all(parts) or not all(is_snowflake(part) for part in parts):
            raise ValueError("DISCORD_ALLOWED_GUILD_IDS must be comma-separated snowflake ids")
        if not 1 <= len(parts) <= MAX_ALLOWED_GUILDS:
            raise ValueError(f"DISCORD_ALLOWED_GUILD_IDS must list 1 to {MAX_ALLOWED_GUILDS} ids")
        if len(set(parts)) != len(parts):
            raise ValueError("DISCORD_ALLOWED_GUILD_IDS must not contain duplicate ids")
        return ",".join(parts)

    @field_validator("discord_application_id", mode="after")
    @classmethod
    def _check_application_id(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not is_snowflake(value.strip()):
            raise ValueError("DISCORD_APPLICATION_ID must be a snowflake id")
        return value.strip()

    @field_validator("discord_api_base", mode="after")
    @classmethod
    def _check_api_base(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        parts = urlsplit(cleaned)
        if parts.scheme != "https" or parts.hostname != "discord.com" or parts.port is not None:
            raise ValueError("DISCORD_API_BASE must be an https://discord.com/api/v<N> URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("DISCORD_API_BASE must not contain credentials, query or fragment")
        match = _API_PATH_RE.fullmatch(parts.path)
        if match is None or int(match.group(1)) < 10:
            raise ValueError(
                "DISCORD_API_BASE must be an https://discord.com/api/v<N> URL, N >= 10"
            )
        return cleaned

    @field_validator("discord_user_agent", mode="after")
    @classmethod
    def _check_user_agent(cls, value: str) -> str:
        if not _USER_AGENT_RE.fullmatch(value.strip()):
            raise ValueError("DISCORD_USER_AGENT must look like 'DiscordBot (<url>, <version>)'")
        return value.strip()

    @field_validator(
        "host_port", "discord_oauth_url_elicitation", "discord_oauth_pkce", mode="wrap"
    )
    @classmethod
    def _oauth_ignored_when_off(
        cls, value: object, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> object:
        """A3.1 (U3): a malformed A3 setting never stops an off-mode server; it takes its default.

        With the switch on the error is raised as usual (U1). ``discord_user_oauth`` is declared
        before these fields, so it is already validated in ``info.data``.
        """
        try:
            return handler(value)
        except ValidationError:
            if info.data.get("discord_user_oauth"):
                raise
            return _OAUTH_DEFAULTS[str(info.field_name)]

    @field_validator("log_level", mode="after")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in _LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {', '.join(sorted(_LOG_LEVELS))}")
        return level

    @model_validator(mode="after")
    def _check_user_oauth(self) -> Settings:
        """U1-U2: with the switch on, the client secret, application id and token path are required.

        Errors name the field and never the value. With the switch off nothing here is checked.
        """
        if not self.discord_user_oauth:
            return self
        if self.discord_oauth_client_secret is None:
            raise ValueError(
                "DISCORD_OAUTH_CLIENT_SECRET is required when DISCORD_USER_OAUTH is true"
            )
        _check_secret(self.discord_oauth_client_secret, "DISCORD_OAUTH_CLIENT_SECRET")
        if not self.discord_application_id:
            raise ValueError("DISCORD_APPLICATION_ID is required when DISCORD_USER_OAUTH is true")
        if not self.discord_oauth_token_file.startswith("/"):
            raise ValueError("DISCORD_OAUTH_TOKEN_FILE must be an absolute path")
        return self

    @property
    def redirect_uri(self) -> str:
        """The exact OAuth redirect URI (U10), derived from HOST_PORT (D26)."""
        return f"http://127.0.0.1:{self.host_port}/oauth/callback"

    @property
    def sign_in_url(self) -> str:
        """The server's own sign-in page, opened in a browser (U8, U17)."""
        return f"http://127.0.0.1:{self.host_port}/oauth/start"

    @property
    def guild_ids(self) -> frozenset[str]:
        """The default-deny guild allowlist (D7)."""
        return frozenset(self.discord_allowed_guild_ids.split(","))

    @property
    def api_host(self) -> str:
        """Lower-cased host of the configured Discord API (the only host ever contacted)."""
        return urlsplit(self.discord_api_base).netloc.lower()


def load_settings() -> Settings:
    """Load settings from the environment, failing fast with a secret-free message."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p).upper() for p in err['loc']) or 'CONFIGURATION'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"Invalid configuration: {problems}") from None
