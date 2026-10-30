"""Environment-driven configuration (R6-R10).

This is the only module allowed to read the process environment (R6). Validation failures raise
:class:`ConfigError` naming the field and the constraint, never the offending value (R6) — the
pydantic setting ``hide_input_in_errors`` plus validators that raise pre-written, value-free
messages make that structural rather than a formatting afterthought.

Required at startup (R7): ``MCP_AUTH_TOKEN``, ``TWITCH_CLIENT_ID``, ``TWITCH_CLIENT_SECRET`` and
``TWITCH_ALLOWED_BROADCASTER_IDS``. The allowlist can never be empty (R8) and both upstream bases
are pinned to Twitch's own hosts (R9) so no configuration value can redirect this server (SSRF).
Every numeric knob is range-validated here (R10).
"""

from __future__ import annotations

import re
import ssl
from pathlib import Path
from typing import Annotated, Final
from urllib.parse import urlsplit

from pydantic import (
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_PREFIX: Final = "replace-me"
MIN_AUTH_TOKEN_LENGTH: Final = 32
MIN_CREDENTIAL_LENGTH: Final = 1
MAX_ALLOWED_BROADCASTERS: Final = 100
MAX_BROADCASTER_ID_DIGITS: Final = 20
MAX_WRITE_GROUPS: Final = 20
#: The only two upstream origins this server will ever contact (R9, R12).
DEFAULT_API_BASE: Final = "https://api.twitch.tv/helix"
DEFAULT_AUTH_BASE: Final = "https://id.twitch.tv"
DEFAULT_HOST_PORT: Final = 8770
DEFAULT_TOKEN_FILE: Final = "/data/twitch_user_token.json"  # noqa: S105 - a path, not a secret

_BROADCASTER_ID_RE: Final = re.compile(r"^[0-9]{1,20}$")
_WRITE_GROUP_RE: Final = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_LOG_LEVELS: Final = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigError(Exception):
    """Raised when configuration is missing or invalid. Never contains secret values."""


def _check_secret(
    value: SecretStr, name: str, min_length: int = MIN_CREDENTIAL_LENGTH
) -> SecretStr:
    """Non-empty, non-placeholder secret. Messages name the field only (R6/R7)."""
    raw = value.get_secret_value().strip()
    if not raw:
        raise ValueError(f"{name} must not be empty")
    if raw.lower().startswith(PLACEHOLDER_PREFIX):
        raise ValueError(f"{name} still has its placeholder value; edit .env")
    if len(raw) < min_length:
        raise ValueError(f"{name} must be at least {min_length} characters")
    return SecretStr(raw)


def _check_base_url(value: str, name: str, expected_host: str, expected_path: str) -> str:
    """Pin one base URL to its exact Twitch origin (R9). Rejects any other value."""
    cleaned = value.strip().rstrip("/")
    try:
        parts = urlsplit(cleaned)
        port = parts.port
    except ValueError:
        port = None
        parts = None
    if parts is None:
        raise ValueError(f"{name} is not a valid URL")
    if (
        parts.scheme != "https"
        or parts.hostname != expected_host
        or port is not None
        or parts.path.rstrip("/") != expected_path
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError(f"{name} must be exactly https://{expected_host}{expected_path}")
    return f"https://{expected_host}{expected_path}"


class Settings(BaseSettings):
    """All runtime configuration, loaded from environment variables (names are upper-case)."""

    model_config = SettingsConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
        case_sensitive=False,
    )

    # --- required secrets and scope (R7, R8) ---
    mcp_auth_token: SecretStr
    twitch_client_id: SecretStr
    twitch_client_secret: SecretStr
    twitch_allowed_broadcaster_ids: str

    # --- upstream pinning (R9, R12) ---
    twitch_api_base: str = DEFAULT_API_BASE
    twitch_auth_base: str = DEFAULT_AUTH_BASE

    # --- transport limits (R10) ---
    twitch_timeout_seconds: Annotated[float, Field(ge=1, le=60)] = 10.0
    twitch_max_response_bytes: Annotated[int, Field(ge=65_536, le=16_777_216)] = 4_194_304
    twitch_max_rate_limit_wait_seconds: Annotated[float, Field(ge=0, le=120)] = 30.0
    twitch_max_429_retries: Annotated[int, Field(ge=0, le=10)] = 3
    twitch_max_5xx_retries: Annotated[int, Field(ge=0, le=10)] = 2

    # --- host port (D6) ---
    host_port: Annotated[int, Field(ge=1, le=65535)] = DEFAULT_HOST_PORT

    # --- TLS for the OAuth redirect (docs/specs/tls-oauth-redirect): off by default (T-D2) ---
    twitch_tls_cert_file: str = ""
    twitch_tls_key_file: str = ""

    # --- user OAuth (R46): off by default ---
    twitch_user_oauth: bool = False
    twitch_oauth_scopes: str = "user:read:email"
    twitch_oauth_token_file: str = DEFAULT_TOKEN_FILE

    # --- writes (R28): both kill switches default off ---
    twitch_enable_writes: bool = False
    twitch_write_groups: str = ""

    # --- confirmation and identity (R30-R32) ---
    twitch_confirm_token_ttl_seconds: Annotated[int, Field(ge=30, le=3600)] = 300
    twitch_identity_ttl_seconds: Annotated[int, Field(ge=0, le=3600)] = 300

    # --- cache (metadata + identity only) ---
    ttl_metadata_seconds: Annotated[int, Field(ge=0, le=3600)] = 300
    cache_max_entries: Annotated[int, Field(ge=8, le=10_000)] = 2000
    cache_max_mb: Annotated[int, Field(ge=8, le=400)] = 64

    log_level: str = "INFO"

    # ------------------------------------------------------------ secrets (R6, R7)

    @field_validator("mcp_auth_token", mode="after")
    @classmethod
    def _check_auth_token(cls, value: SecretStr) -> SecretStr:
        return _check_secret(value, "MCP_AUTH_TOKEN", MIN_AUTH_TOKEN_LENGTH)

    @field_validator("twitch_client_id", mode="after")
    @classmethod
    def _check_client_id(cls, value: SecretStr) -> SecretStr:
        return _check_secret(value, "TWITCH_CLIENT_ID")

    @field_validator("twitch_client_secret", mode="after")
    @classmethod
    def _check_client_secret(cls, value: SecretStr) -> SecretStr:
        return _check_secret(value, "TWITCH_CLIENT_SECRET")

    # ------------------------------------------------------------ allowlist (R8)

    @field_validator("twitch_allowed_broadcaster_ids", mode="after")
    @classmethod
    def _check_allowlist(cls, value: str) -> str:
        if value.strip().lower().startswith(PLACEHOLDER_PREFIX):
            raise ValueError(
                "TWITCH_ALLOWED_BROADCASTER_IDS still has its placeholder value; edit .env"
            )
        parts = [part.strip() for part in value.split(",")]
        if not all(parts):
            raise ValueError(
                "TWITCH_ALLOWED_BROADCASTER_IDS must list at least one broadcaster id "
                "(the server cannot run with an empty allowlist)"
            )
        if not all(_BROADCASTER_ID_RE.fullmatch(part) for part in parts):
            raise ValueError(
                "TWITCH_ALLOWED_BROADCASTER_IDS must be comma-separated Twitch user ids "
                f"of 1 to {MAX_BROADCASTER_ID_DIGITS} digits"
            )
        if not 1 <= len(parts) <= MAX_ALLOWED_BROADCASTERS:
            raise ValueError(
                f"TWITCH_ALLOWED_BROADCASTER_IDS must list 1 to {MAX_ALLOWED_BROADCASTERS} ids"
            )
        if len(set(parts)) != len(parts):
            raise ValueError("TWITCH_ALLOWED_BROADCASTER_IDS must not contain duplicate ids")
        return ",".join(parts)

    # ------------------------------------------------------------ base URLs (R9)

    @field_validator("twitch_api_base", mode="after")
    @classmethod
    def _check_api_base(cls, value: str) -> str:
        return _check_base_url(value, "TWITCH_API_BASE", "api.twitch.tv", "/helix")

    @field_validator("twitch_auth_base", mode="after")
    @classmethod
    def _check_auth_base(cls, value: str) -> str:
        return _check_base_url(value, "TWITCH_AUTH_BASE", "id.twitch.tv", "")

    # ------------------------------------------------------------ optional strings

    @field_validator("twitch_oauth_scopes", mode="after")
    @classmethod
    def _check_scopes(cls, value: str) -> str:
        scopes = value.split()
        if any(not re.fullmatch(r"[A-Za-z0-9:._-]{1,100}", scope) for scope in scopes):
            raise ValueError("TWITCH_OAUTH_SCOPES must be a space-delimited list of scope names")
        return " ".join(scopes)

    @field_validator("twitch_oauth_token_file", mode="after")
    @classmethod
    def _check_token_file(cls, value: str) -> str:
        if not value.strip().startswith("/"):
            raise ValueError("TWITCH_OAUTH_TOKEN_FILE must be an absolute path")
        return value.strip()

    @field_validator("twitch_write_groups", mode="after")
    @classmethod
    def _check_write_groups(cls, value: str) -> str:
        if not value.strip():
            return ""
        parts = [part.strip().lower() for part in value.split(",")]
        if not all(parts):
            raise ValueError("TWITCH_WRITE_GROUPS must be a comma-separated list of group names")
        if not all(_WRITE_GROUP_RE.fullmatch(part) for part in parts):
            raise ValueError(
                "TWITCH_WRITE_GROUPS entries must be lower-case group names "
                "(channel, polls, predictions, chat, moderation, roles)"
            )
        if len(parts) > MAX_WRITE_GROUPS:
            raise ValueError(f"TWITCH_WRITE_GROUPS must list at most {MAX_WRITE_GROUPS} groups")
        if len(set(parts)) != len(parts):
            raise ValueError("TWITCH_WRITE_GROUPS must not contain duplicates")
        return ",".join(parts)

    @field_validator("twitch_tls_cert_file", "twitch_tls_key_file", mode="after")
    @classmethod
    def _check_tls_paths_are_trimmed(cls, value: str) -> str:
        """Normalise the two TLS paths so "unset" and "   " are the same thing (T1)."""
        return value.strip()

    @field_validator("log_level", mode="after")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in _LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {', '.join(sorted(_LOG_LEVELS))}")
        return level

    # ------------------------------------------------------------ derived views

    @property
    def broadcaster_ids(self) -> frozenset[str]:
        """The default-deny broadcaster allowlist (R8)."""
        return frozenset(self.twitch_allowed_broadcaster_ids.split(","))

    @property
    def write_groups(self) -> frozenset[str]:
        """Enabled write groups; empty unless ``TWITCH_WRITE_GROUPS`` names them (R28)."""
        if not self.twitch_write_groups:
            return frozenset()
        return frozenset(self.twitch_write_groups.split(","))

    @property
    def writes_enabled(self) -> bool:
        """Both kill switches of R28 must be thrown before any write tool exists."""
        return self.twitch_enable_writes and bool(self.twitch_write_groups)

    @property
    def oauth_scope_list(self) -> list[str]:
        """``TWITCH_OAUTH_SCOPES`` as the list to send to ``/oauth2/authorize`` (D10)."""
        return self.twitch_oauth_scopes.split()

    @property
    def cache_max_bytes(self) -> int:
        """``CACHE_MAX_MB`` expressed in bytes for :mod:`twitch_mcp.cache`."""
        return self.cache_max_mb * 1_048_576

    @property
    def api_host(self) -> str:
        """Lower-cased Helix host — one of the only two hosts ever contacted (R12)."""
        return urlsplit(self.twitch_api_base).netloc.lower()

    @property
    def auth_host(self) -> str:
        """Lower-cased OAuth host — the other host ever contacted (R12)."""
        return urlsplit(self.twitch_auth_base).netloc.lower()

    # ------------------------------------------------------------ TLS (T1-T4, T9; S5-S6)

    @model_validator(mode="after")
    def _check_tls(self) -> Settings:
        """TLS is all-or-nothing, and the material is proven parseable before uvicorn ever sees it.

        Messages name the variable and the constraint only (T9, same rule as R6): the key's path is
        as sensitive as its contents, since a path tells an attacker where to aim. The key's mode
        and location are not checked (S5, S6): key permissions are the operator's responsibility.
        """
        cert = self.twitch_tls_cert_file
        key = self.twitch_tls_key_file
        if not cert and not key:
            return self
        if not cert or not key:
            raise ValueError(
                "TWITCH_TLS_CERT_FILE and TWITCH_TLS_KEY_FILE must both be set, or neither"
            )
        for name, raw in (("TWITCH_TLS_CERT_FILE", cert), ("TWITCH_TLS_KEY_FILE", key)):
            path = Path(raw)
            if not path.is_absolute():
                raise ValueError(f"{name} must be an absolute path")
            if not path.is_file():
                raise ValueError(f"{name} does not name a readable file")
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, keyfile=key)
        except (OSError, ssl.SSLError) as exc:
            raise ValueError(
                "TWITCH_TLS_CERT_FILE and TWITCH_TLS_KEY_FILE do not parse as a certificate "
                f"and its matching key ({type(exc).__name__})"
            ) from exc
        return self

    @property
    def tls_enabled(self) -> bool:
        """Whether the server serves TLS — decided solely by the certificate being configured."""
        return bool(self.twitch_tls_cert_file.strip())

    @property
    def scheme(self) -> str:
        """``https`` with TLS configured, ``http`` without; the single source of truth (T-D3)."""
        return "https" if self.tls_enabled else "http"

    @property
    def redirect_uri(self) -> str:
        """The exact OAuth redirect URI, derived from ``scheme`` and HOST_PORT (T3).

        The scheme cannot disagree with the listener: Twitch compares the registered Redirect URL
        against the one sent to ``/oauth2/authorize`` exactly, and both come from this property.
        """
        return f"{self.scheme}://127.0.0.1:{self.host_port}/oauth/callback"

    @property
    def sign_in_url(self) -> str:
        """The server's own sign-in page (``/oauth/start``), same scheme as ``redirect_uri``."""
        return f"{self.scheme}://127.0.0.1:{self.host_port}/oauth/start"


def load_settings() -> Settings:
    """Load settings from the environment, failing fast with a secret-free message (R6)."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p).upper() for p in err['loc']) or 'CONFIGURATION'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"Invalid configuration: {problems}") from None
