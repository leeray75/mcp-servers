"""Environment-driven configuration.

This is the only module allowed to read the process environment. Validation
failures raise :class:`ConfigError` carrying field names and constraint
messages only, never the offending values (they may be secrets).
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gmail_mcp.errors import DEFAULT_HOST_PORT

SCOPE_READONLY: Final = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_COMPOSE: Final = "https://www.googleapis.com/auth/gmail.compose"
ALLOWED_SCOPES: Final[frozenset[str]] = frozenset({SCOPE_READONLY, SCOPE_COMPOSE})
_SCOPE_ALIASES: Final[dict[str, str]] = {
    "gmail.readonly": SCOPE_READONLY,
    "gmail.compose": SCOPE_COMPOSE,
}
PLACEHOLDER_PREFIX: Final = "replace-me"
MIN_AUTH_TOKEN_LENGTH: Final = 32
_LOG_LEVELS: Final = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigError(Exception):
    """Raised when configuration is missing or invalid. Never contains secret values."""


def _reject_placeholder(value: SecretStr, name: str) -> SecretStr:
    raw = value.get_secret_value().strip()
    if not raw:
        raise ValueError(f"{name} must not be empty")
    if raw.startswith(PLACEHOLDER_PREFIX):
        raise ValueError(f"{name} still has its placeholder value; edit .env")
    return SecretStr(raw)


class Settings(BaseSettings):
    """All runtime configuration, loaded from environment variables."""

    model_config = SettingsConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
        case_sensitive=False,
    )

    google_client_id: SecretStr
    google_client_secret: SecretStr
    mcp_auth_token: SecretStr
    gmail_scopes: str = f"{SCOPE_READONLY} {SCOPE_COMPOSE}"
    token_path: str = "/data/token.json"  # noqa: S105 - a file path, not a secret
    host: str = "0.0.0.0"  # noqa: S104 - container bind; compose restricts host exposure
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    # Port the user's browser reaches the server on (the published host port).
    host_port: Annotated[int, Field(ge=1, le=65535)] = DEFAULT_HOST_PORT
    log_level: str = "INFO"
    max_results_cap: Annotated[int, Field(ge=1, le=500)] = 50
    max_body_chars: Annotated[int, Field(ge=100, le=1_000_000)] = 20_000
    # Kill switch for URL-mode elicitation: when false, auth errors are plain tool errors only.
    auth_url_elicitation: bool = True

    @field_validator("google_client_id", mode="after")
    @classmethod
    def _check_client_id(cls, value: SecretStr) -> SecretStr:
        return _reject_placeholder(value, "GOOGLE_CLIENT_ID")

    @field_validator("google_client_secret", mode="after")
    @classmethod
    def _check_client_secret(cls, value: SecretStr) -> SecretStr:
        return _reject_placeholder(value, "GOOGLE_CLIENT_SECRET")

    @field_validator("mcp_auth_token", mode="after")
    @classmethod
    def _check_auth_token(cls, value: SecretStr) -> SecretStr:
        checked = _reject_placeholder(value, "MCP_AUTH_TOKEN")
        if len(checked.get_secret_value()) < MIN_AUTH_TOKEN_LENGTH:
            raise ValueError(f"MCP_AUTH_TOKEN must be at least {MIN_AUTH_TOKEN_LENGTH} characters")
        return checked

    @field_validator("gmail_scopes", mode="after")
    @classmethod
    def _check_scopes(cls, value: str) -> str:
        scopes = [_SCOPE_ALIASES.get(s, s) for s in value.split()]
        if not scopes:
            raise ValueError("GMAIL_SCOPES must list at least one scope")
        unknown = sorted(set(scopes) - ALLOWED_SCOPES)
        if unknown:
            raise ValueError(
                "GMAIL_SCOPES may only contain gmail.readonly and gmail.compose "
                f"({len(unknown)} unsupported scope(s) given)"
            )
        return " ".join(dict.fromkeys(scopes))

    @field_validator("log_level", mode="after")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in _LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {', '.join(sorted(_LOG_LEVELS))}")
        return level

    @property
    def scopes(self) -> list[str]:
        """The validated, normalised (full URL) OAuth scopes."""
        return self.gmail_scopes.split()


def load_settings() -> Settings:
    """Load settings from the environment, failing fast with a secret-free message."""
    try:
        return Settings()  # type: ignore[call-arg]  # fields are populated from the environment
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p).upper() for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"Invalid configuration: {problems}") from None
