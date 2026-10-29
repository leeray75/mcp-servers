"""User OAuth guild discovery (amendment A3, U8-U9, U11, U19, U24-U25, U26).

This module holds the in-process sign-in state and the single user token. It never imports httpx:
the HTTP calls (token exchange, refresh, revoke, ``/users/@me``) are made by ``discord_client.py``
and ``service.py`` and handed in as callables. Clock and randomness are injectable for tests.

Secrets (access and refresh tokens, the PKCE verifier, ``state``) are registered for log redaction
when they are created or received (U26) and are held as ``SecretStr`` in memory (D20).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import string
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

from pydantic import SecretStr

from discord_mcp.errors import DiscordMcpError
from discord_mcp.logging_setup import register_secret, unregister_secret

logger = logging.getLogger(__name__)

AUTHORIZE_URL: Final = "https://discord.com/oauth2/authorize"
ISSUER: Final = "https://discord.com"
SCOPES: Final = "identify guilds"
INVITE_PERMISSIONS: Final = "66688"
PENDING_TTL_SECONDS: Final = 600.0
REFRESH_MARGIN_SECONDS: Final = 300.0
VERIFIER_LENGTH: Final = 64
VERIFIER_ALPHABET: Final = string.ascii_letters + string.digits + "-._~"  # RFC 7636 unreserved
TOKEN_FILE_VERSION: Final = 1
CONNECT_HINT: Final = "Call connect_discord_account to connect your Discord account."
NOT_CONNECTED_MESSAGE: Final = "No Discord account is connected"
REFUSED_NO_FLOW: Final = "no_flow"
REFUSED_EXPIRED: Final = "expired"
REFUSED_STATE: Final = "state_mismatch"

Clock = Callable[[], float]
TokenExchange = Callable[[str], Awaitable[Mapping[str, Any]]]


def code_challenge(verifier: str) -> str:
    """PKCE S256 challenge: base64url of SHA-256(verifier), no padding (U8)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_verifier() -> str:
    """A 64-character RFC 7636 code verifier from ``secrets`` (U9)."""
    return "".join(secrets.choice(VERIFIER_ALPHABET) for _ in range(VERIFIER_LENGTH))


def new_state() -> str:
    """A 32-byte URL-safe ``state`` from ``secrets`` (U9)."""
    return secrets.token_urlsafe(32)


def _encode(params: list[tuple[str, str]]) -> str:
    return "&".join(f"{key}={_quote(value)}" for key, value in params)


def _quote(value: str) -> str:
    return quote(value, safe="")


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str | None) -> str:
    """The Discord authorize URL for ``/oauth/start`` (U8). Parameters in the specified order."""
    params = [
        ("response_type", "code"),
        ("client_id", client_id),
        ("scope", SCOPES),
        ("redirect_uri", redirect_uri),
        ("state", state),
        ("prompt", "consent"),
    ]
    if challenge is not None:
        params += [("code_challenge", challenge), ("code_challenge_method", "S256")]
    return f"{AUTHORIZE_URL}?{_encode(params)}"


def invite_url(application_id: str, guild_id: str) -> str:
    """The bot invite URL for one guild (U21). No request is made."""
    params = [
        ("client_id", application_id),
        ("scope", "bot"),
        ("permissions", INVITE_PERMISSIONS),
        ("guild_id", guild_id),
        ("disable_guild_select", "true"),
    ]
    return f"{AUTHORIZE_URL}?{_encode(params)}"


@dataclass(slots=True)
class PendingFlow:
    """One in-memory sign-in attempt (U9): state, PKCE verifier and expiry (epoch seconds)."""

    state: str
    verifier: str
    expires_at: float


class PendingStore:
    """At most one pending flow; single-use; expires after ``PENDING_TTL_SECONDS`` (U9, U11)."""

    def __init__(
        self,
        clock: Clock = time.time,
        *,
        make_state: Callable[[], str] = new_state,
        make_verifier: Callable[[], str] = new_verifier,
    ) -> None:
        self._clock = clock
        self._make_state = make_state
        self._make_verifier = make_verifier
        self._flow: PendingFlow | None = None

    def start(self) -> PendingFlow:
        """Replace any pending flow with a fresh one (U9)."""
        if self._flow is not None:
            self._drop(self._flow)
        flow = PendingFlow(
            state=self._make_state(),
            verifier=self._make_verifier(),
            expires_at=self._clock() + PENDING_TTL_SECONDS,
        )
        register_secret(flow.state)
        register_secret(flow.verifier)
        self._flow = flow
        return flow

    def claim(self, state: str | None) -> PendingFlow | str:
        """Consume the pending flow. Returns it, or the refusal reason (U11).

        Any call consumes the flow, so a second callback with the same state is refused. A refused
        flow's secrets are unregistered at once. A claimed flow's secrets stay registered while the
        caller uses the verifier; the caller must then call :meth:`release` (A3 fix round 2, n2).
        """
        flow, self._flow = self._flow, None
        if flow is None:
            return REFUSED_NO_FLOW
        if self._clock() >= flow.expires_at:
            self._drop(flow)
            return REFUSED_EXPIRED
        if state is None or not hmac.compare_digest(state.encode("utf-8"), flow.state.encode()):
            self._drop(flow)
            return REFUSED_STATE
        return flow

    def release(self, flow: PendingFlow) -> None:
        """Unregister a claimed flow's secrets once the caller no longer needs them (n2)."""
        self._drop(flow)

    def clear(self) -> None:
        """Drop the pending flow and its secrets (n2)."""
        if self._flow is not None:
            self._drop(self._flow)
        self._flow = None

    @staticmethod
    def _drop(flow: PendingFlow) -> None:
        unregister_secret(flow.state)
        unregister_secret(flow.verifier)

    @property
    def pending(self) -> bool:
        return self._flow is not None and self._clock() < self._flow.expires_at


@dataclass(frozen=True, slots=True)
class UserToken:
    """The single user grant held in memory and in the token file (U19)."""

    access_token: SecretStr
    refresh_token: SecretStr
    token_type: str
    scope: str
    expires_at: float
    user_id: str
    username: str
    connected_at: float


class TokenFileInvalid(Exception):
    """The token file exists but cannot be used. ``reason`` never contains file content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def build_token(
    body: Any,
    *,
    now: float,
    user_id: str,
    username: str,
    connected_at: float,
    fallback: UserToken | None = None,
) -> UserToken:
    """Validate a token-endpoint response (U12, U25). Raises ``upstream`` when malformed.

    On a refresh (``fallback`` given) a missing refresh token or scope keeps the previous value.
    Only the error code of a failure is ever read; this function never echoes the body.
    """
    malformed = DiscordMcpError("upstream", "Discord returned an unexpected token response")
    if not isinstance(body, dict):
        raise malformed
    access = body.get("access_token")
    refresh = body.get("refresh_token")
    token_type = body.get("token_type")
    scope = body.get("scope")
    expires_in = body.get("expires_in")
    if fallback is not None:
        refresh = refresh or fallback.refresh_token.get_secret_value()
        scope = scope or fallback.scope
        token_type = token_type or fallback.token_type
    if not isinstance(access, str) or not access:
        raise malformed
    if not isinstance(refresh, str) or not refresh:
        raise malformed
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise malformed
    if not isinstance(scope, str):
        raise malformed
    if isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in <= 0:
        raise malformed
    register_secret(access)
    register_secret(refresh)
    return UserToken(
        access_token=SecretStr(access),
        refresh_token=SecretStr(refresh),
        token_type="Bearer",  # noqa: S106 - the OAuth token type, not a secret
        scope=scope,
        expires_at=now + expires_in,
        user_id=user_id,
        username=username,
        connected_at=connected_at,
    )


def _from_file(data: Any) -> UserToken:
    if not isinstance(data, dict) or data.get("version") != TOKEN_FILE_VERSION:
        raise TokenFileInvalid("unsupported_format")
    strings = ("access_token", "refresh_token", "token_type", "scope", "user_id", "username")
    numbers = ("expires_at", "connected_at")
    if not all(isinstance(data.get(key), str) for key in strings):
        raise TokenFileInvalid("invalid_fields")
    if not all(
        isinstance(data.get(key), int | float) and not isinstance(data.get(key), bool)
        for key in numbers
    ):
        raise TokenFileInvalid("invalid_fields")
    token = UserToken(
        access_token=SecretStr(data["access_token"]),
        refresh_token=SecretStr(data["refresh_token"]),
        token_type=data["token_type"],
        scope=data["scope"],
        expires_at=float(data["expires_at"]),
        user_id=data["user_id"],
        username=data["username"],
        connected_at=float(data["connected_at"]),
    )
    if not token.access_token.get_secret_value() or not token.refresh_token.get_secret_value():
        raise TokenFileInvalid("invalid_fields")
    register_secret(token.access_token.get_secret_value())
    register_secret(token.refresh_token.get_secret_value())
    return token


class TokenStore:
    """The token JSON file (U19): atomic 0600 writes, invalid files reported by reason only."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _remove_stray_temps(self) -> None:
        """Delete leftovers of an interrupted save (A3 fix round, m11): they hold tokens."""
        for stray in self.path.parent.glob(f".{self.path.name}.*.tmp"):
            with contextlib.suppress(OSError):
                stray.unlink()

    def load(self) -> UserToken | None:
        """The stored token, ``None`` when no file exists. Raises :class:`TokenFileInvalid`."""
        self._remove_stray_temps()
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise TokenFileInvalid("unreadable") from None
        try:
            data = json.loads(raw)
        except ValueError:
            raise TokenFileInvalid("not_json") from None
        return _from_file(data)

    def save(self, token: UserToken) -> None:
        """Write atomically: temp file created 0600 with O_EXCL, fsynced, then ``os.replace``."""
        payload = {
            "version": TOKEN_FILE_VERSION,
            "access_token": token.access_token.get_secret_value(),
            "refresh_token": token.refresh_token.get_secret_value(),
            "token_type": token.token_type,
            "scope": token.scope,
            "expires_at": token.expires_at,
            "user_id": token.user_id,
            "username": token.username,
            "connected_at": token.connected_at,
        }
        data = json.dumps(payload).encode("utf-8")
        self._remove_stray_temps()
        temp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(temp)
            raise DiscordMcpError(
                "unavailable",
                "The Discord token file could not be written",
                hint="Check that the token directory exists and is writable, then retry.",
            ) from None

    def delete(self) -> None:
        """Delete the token file. A failure is ``unavailable``, never an internal error (m5)."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            raise DiscordMcpError(
                "unavailable",
                "The Discord token file could not be deleted",
                hint=(
                    f"The token file {self.path} is still on disk and holds the token: "
                    "delete it by hand."
                ),
            ) from None


class UserAuth:
    """The process-wide user account: token, sign-in flow, last OAuth error and refresh (U16-U25).

    Refreshes run under one lock (U25). ``refresh`` exchanges a refresh token for a raw token
    response; it raises ``DiscordMcpError`` (``auth`` for ``invalid_grant``, ``unavailable`` for
    5xx and transport errors).
    """

    def __init__(
        self,
        store: TokenStore,
        *,
        clock: Clock = time.time,
        refresh: TokenExchange | None = None,
    ) -> None:
        self.store = store
        self._clock = clock
        self._refresh = refresh
        self.token: UserToken | None = None
        self.token_file_invalid = False
        self.last_error: str | None = None
        self.pending = PendingStore(clock)
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        if self.token is not None:
            return "connected"
        return "token_file_invalid" if self.token_file_invalid else "not_connected"

    def attach_refresh(self, refresh: TokenExchange) -> None:
        """Wire the refresh grant once the user-side client exists (it needs this object first)."""
        self._refresh = refresh

    def load(self) -> None:
        """Load the token file once at boot (U19). A bad file is treated as disconnected."""
        try:
            self.token = self.store.load()
        except TokenFileInvalid as exc:
            self.token = None
            self.token_file_invalid = True
            logger.warning("discord user token file invalid", extra={"outcome": exc.reason})
        if (
            self.token is None
            and not self.token_file_invalid
            and not self.store.path.parent.is_dir()
        ):
            # U19: a missing folder is a boot warning, not a crash; the first write then fails.
            logger.warning(
                "discord user token folder is missing", extra={"outcome": "token_folder_missing"}
            )

    def install(self, token: UserToken) -> None:
        """Persist first, then use (U25). A failed write leaves the previous state in place."""
        self.store.save(token)
        self.token = token
        self.token_file_invalid = False

    @property
    def lock(self) -> asyncio.Lock:
        """The refresh lock. Disconnect takes it so a refresh cannot re-create the file (m6)."""
        return self._lock

    def forget(self) -> None:
        """Drop the token from memory, then delete the file (U18). Memory is cleared first, so a
        failed delete never leaves the credential in use in this process (m5)."""
        self.token = None
        self.token_file_invalid = False
        self.store.delete()

    def _require(self) -> UserToken:
        if self.token is None:
            raise DiscordMcpError("auth", NOT_CONNECTED_MESSAGE, hint=CONNECT_HINT)
        return self.token

    def _expiring(self, token: UserToken) -> bool:
        return token.expires_at - self._clock() < REFRESH_MARGIN_SECONDS

    async def access_token(self, *, stale: str | None = None) -> str:
        """A usable access token: refreshed when near expiry, or replaced after a 401 (U24-U25).

        ``stale`` is the token a 401 rejected. A caller that waited on the lock reuses a token
        another caller has already refreshed, so there is never a double refresh.
        """
        current = self._require().access_token.get_secret_value()
        if stale is None and not self._expiring(self._require()):
            return current
        if stale is not None and current != stale:
            return current
        async with self._lock:
            token = self._require()
            current = token.access_token.get_secret_value()
            if stale is not None and current != stale:
                return current
            if stale is None and not self._expiring(token):
                return current
            return await self._refresh_locked(token)

    async def _refresh_locked(self, token: UserToken) -> str:
        if self._refresh is None:
            raise DiscordMcpError("unavailable", "Discord account refresh is not configured")
        try:
            body = await self._refresh(token.refresh_token.get_secret_value())
        except DiscordMcpError as exc:
            if exc.kind != "auth":
                raise
            self.last_error = "refresh_rejected"
            self.token = None
            self.token_file_invalid = False
            try:
                self.store.delete()
            except DiscordMcpError:
                logger.warning(
                    "discord user token file could not be deleted",
                    extra={"outcome": "delete_failed"},
                )
            raise DiscordMcpError("auth", NOT_CONNECTED_MESSAGE, hint=CONNECT_HINT) from None
        renewed = build_token(
            body,
            now=self._clock(),
            user_id=token.user_id,
            username=token.username,
            connected_at=token.connected_at,
            fallback=token,
        )
        self.install(renewed)
        return renewed.access_token.get_secret_value()
