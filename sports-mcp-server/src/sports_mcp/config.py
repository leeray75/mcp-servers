"""Environment-driven configuration.

This is the only module allowed to read the process environment. Validation
failures raise :class:`ConfigError` carrying field names and constraint
messages only, never the offending values (they may be secrets).
"""

from __future__ import annotations

from typing import Annotated, Final
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_PREFIX: Final = "replace-me"
MIN_AUTH_TOKEN_LENGTH: Final = 32
_LOG_LEVELS: Final = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigError(Exception):
    """Raised when configuration is missing or invalid. Never contains secret values."""


class Settings(BaseSettings):
    """All runtime configuration, loaded from environment variables."""

    model_config = SettingsConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
        case_sensitive=False,
    )

    # --- upstream ---
    espn_base_url: str = "https://site.api.espn.com"
    espn_timeout_seconds: Annotated[float, Field(ge=1, le=120)] = 10.0
    espn_max_concurrency: Annotated[int, Field(ge=1, le=32)] = 4
    # Hard cap on one upstream body (college standings are ~6 MB, the largest seen).
    espn_max_response_bytes: Annotated[int, Field(ge=1_048_576, le=64 * 1024 * 1024)] = (
        12 * 1024 * 1024
    )

    # --- behaviour ---
    default_timezone: str = "America/New_York"
    max_response_chars: Annotated[int, Field(ge=1_000, le=200_000)] = 20_000
    stale_if_error_seconds: Annotated[int, Field(ge=0, le=86_400)] = 300

    # --- cache TTLs (seconds) ---
    ttl_live_seconds: Annotated[int, Field(ge=0, le=3600)] = 15
    ttl_scoreboard_idle_seconds: Annotated[int, Field(ge=0, le=86_400)] = 120
    ttl_summary_pre_seconds: Annotated[int, Field(ge=0, le=86_400)] = 120
    ttl_summary_post_seconds: Annotated[int, Field(ge=0, le=86_400)] = 3600
    ttl_standings_seconds: Annotated[int, Field(ge=0, le=86_400)] = 600
    ttl_teams_seconds: Annotated[int, Field(ge=0, le=7 * 86_400)] = 86_400
    ttl_schedule_seconds: Annotated[int, Field(ge=0, le=86_400)] = 300
    ttl_schedule_live_seconds: Annotated[int, Field(ge=0, le=3600)] = 60
    ttl_news_seconds: Annotated[int, Field(ge=0, le=86_400)] = 300
    ttl_player_search_seconds: Annotated[int, Field(ge=0, le=86_400)] = 3600
    ttl_player_seconds: Annotated[int, Field(ge=0, le=86_400)] = 300
    # Most recent seasons fetched for get_player_gamelog(season="all") (older ones are omitted).
    player_max_seasons: Annotated[int, Field(ge=1, le=25)] = 10
    cache_max_entries: Annotated[int, Field(ge=8, le=10_000)] = 256
    # Approximate resident size of cached payloads (container limit is 512 MiB).
    cache_max_mb: Annotated[int, Field(ge=8, le=400)] = 96

    # --- HTTP server (not used in stdio mode) ---
    mcp_auth_token: SecretStr | None = None
    host: str = "0.0.0.0"  # noqa: S104 - container bind; compose restricts host exposure
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    log_level: str = "INFO"

    @field_validator("mcp_auth_token", mode="after")
    @classmethod
    def _check_auth_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        if not raw:
            raise ValueError("MCP_AUTH_TOKEN must not be empty")
        if raw.startswith(PLACEHOLDER_PREFIX):
            raise ValueError("MCP_AUTH_TOKEN still has its placeholder value; edit .env")
        if len(raw) < MIN_AUTH_TOKEN_LENGTH:
            raise ValueError(f"MCP_AUTH_TOKEN must be at least {MIN_AUTH_TOKEN_LENGTH} characters")
        return SecretStr(raw)

    @field_validator("espn_base_url", mode="after")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("ESPN_BASE_URL must be an http(s) URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("ESPN_BASE_URL must be a plain origin without credentials or query")
        return value.strip().rstrip("/")

    @field_validator("default_timezone", mode="after")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        name = value.strip()
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError("DEFAULT_TIMEZONE must be a valid IANA time zone name") from None
        return name

    @field_validator("log_level", mode="after")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in _LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {', '.join(sorted(_LOG_LEVELS))}")
        return level

    @property
    def espn_host(self) -> str:
        """Lower-cased host (and non-default port) of the configured upstream."""
        return urlsplit(self.espn_base_url).netloc.lower()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.default_timezone)


def load_settings(*, require_auth_token: bool = True) -> Settings:
    """Load settings from the environment, failing fast with a secret-free message.

    Args:
        require_auth_token: HTTP mode needs MCP_AUTH_TOKEN; stdio mode does not.
    """
    try:
        settings = Settings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p).upper() for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"Invalid configuration: {problems}") from None
    if require_auth_token and settings.mcp_auth_token is None:
        raise ConfigError("Invalid configuration: MCP_AUTH_TOKEN: field required")
    return settings
