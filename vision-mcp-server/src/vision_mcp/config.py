"""Environment-driven configuration.

This is the only module allowed to read the process environment. Validation
failures raise :class:`ConfigError` carrying field names and constraint
messages only, never the offending values (they may be secrets).
"""

from __future__ import annotations

from typing import Annotated, Final
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # --- vision backend (any OpenAI-compatible /chat/completions endpoint, e.g. LiteLLM) ---
    vision_base_url: str = "http://spark-8013.taileaa4a8.ts.net:4000/v1"
    vision_api_key: SecretStr
    vision_model: str = "qwen3.8-27b"
    vision_timeout_seconds: Annotated[float, Field(ge=5, le=900)] = 180.0
    vision_max_tokens: Annotated[int, Field(ge=64, le=32_000)] = 3000
    # Low temperature keeps transcription and descriptions repeatable.
    vision_temperature: Annotated[float, Field(ge=0, le=2)] = 0.2

    # --- images ---
    # Folder (inside the container) that images may be read from. Mounted read-only.
    images_dir: str = "/images"
    # Optional host-side spellings of that folder, so agents can pass the paths they see:
    # a Windows path (C:\Users\me\Pictures) and/or a WSL path (/mnt/c/Users/me/Pictures).
    windows_images_dir: str | None = None
    host_images_dir: str | None = None
    max_image_bytes: Annotated[int, Field(ge=1_024, le=50 * 1024 * 1024)] = 20 * 1024 * 1024
    max_image_side: Annotated[int, Field(ge=256, le=8192)] = 2048
    max_images_per_call: Annotated[int, Field(ge=1, le=8)] = 4
    max_answer_chars: Annotated[int, Field(ge=500, le=200_000)] = 20_000

    # --- HTTP server (not used in stdio mode) ---
    mcp_auth_token: SecretStr | None = None
    host: str = "0.0.0.0"  # noqa: S104 - container bind; compose restricts host exposure
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    log_level: str = "INFO"

    @field_validator("vision_api_key", mode="after")
    @classmethod
    def _check_api_key(cls, value: SecretStr) -> SecretStr:
        return _reject_placeholder(value, "VISION_API_KEY")

    @field_validator("mcp_auth_token", mode="after")
    @classmethod
    def _check_auth_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        checked = _reject_placeholder(value, "MCP_AUTH_TOKEN")
        if len(checked.get_secret_value()) < MIN_AUTH_TOKEN_LENGTH:
            raise ValueError(f"MCP_AUTH_TOKEN must be at least {MIN_AUTH_TOKEN_LENGTH} characters")
        return checked

    @field_validator("vision_base_url", mode="after")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("VISION_BASE_URL must be an http(s) URL")
        if parts.username or parts.password:
            raise ValueError("VISION_BASE_URL must not embed credentials")
        return value.strip().rstrip("/")

    @field_validator("log_level", mode="after")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in _LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {', '.join(sorted(_LOG_LEVELS))}")
        return level

    @field_validator("windows_images_dir", "host_images_dir", mode="after")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @property
    def images_folder_hint(self) -> str:
        """The folder as an agent should spell it: Windows if configured, else WSL, else as-is."""
        return self.windows_images_dir or self.host_images_dir or self.images_dir


def load_settings(*, require_auth_token: bool = True) -> Settings:
    """Load settings from the environment, failing fast with a secret-free message.

    Args:
        require_auth_token: HTTP mode needs MCP_AUTH_TOKEN; stdio mode does not.
    """
    try:
        settings = Settings()  # type: ignore[call-arg]  # fields are populated from the environment
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p).upper() for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"Invalid configuration: {problems}") from None
    if require_auth_token and settings.mcp_auth_token is None:
        raise ConfigError("Invalid configuration: MCP_AUTH_TOKEN: field required")
    return settings
