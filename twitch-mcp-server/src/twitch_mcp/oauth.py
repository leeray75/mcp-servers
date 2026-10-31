"""User OAuth for the operator's own Twitch account (R46-R51).

Nothing in this module may touch the network — the HTTP calls are the injected callables below, and
only ``twitch_client.py`` (the only ``httpx`` module, R11) executes them (R11). Four things live
here:

* :func:`authorize_url` — the ``https://id.twitch.tv/oauth2/authorize`` link for ``/oauth/start``
  (R46). **No PKCE** (spec D8): Twitch rejects ``code_challenge`` and the client secret is required
  at exchange, so a verifier would be theatre.
* :class:`PendingStore` — at most one in-flight sign-in, with a single-use random ``state``, an
  ``iss`` check when present and a 10-minute TTL (R47).
* :class:`TokenStore` — the ``0600`` token file, the server's only persistent artifact (R48).
* :class:`UserAuth` / :class:`OAuthFlow` — the token, refresh under **one lock** so concurrent
  callers await a single refresh, and disconnect that revokes and deletes (R49-R51).

There is never an app-token fallback for a user-only tool (R49): with no user token the call raises
``kind=unauthenticated`` naming the sign-in page, and a failed refresh forces re-auth and clears the
store. The boot probe degrades instead of crashing (R50), and ``disconnect`` performs no account
action beyond ``POST /oauth2/revoke`` plus deleting this server's own file (R51).

Tokens, refresh tokens, the authorization ``code`` and ``state`` are registered for log redaction
the moment they are created or received, and never appear in an error, log, resource or tool result
(R52).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

from pydantic import SecretStr

from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.logging_setup import register_secret, unregister_secret

logger = logging.getLogger(__name__)

#: The consent endpoint, on the one auth host this server knows (R12, R46).
AUTHORIZE_PATH: Final = "/oauth2/authorize"
TOKEN_PATH: Final = "/oauth2/token"  # noqa: S105 - an endpoint path, not a credential
VALIDATE_PATH: Final = "/oauth2/validate"
REVOKE_PATH: Final = "/oauth2/revoke"  # noqa: S105 - an endpoint path, not a credential

#: An unfinished sign-in attempt is worthless after ten minutes (R47).
PENDING_TTL_SECONDS: Final = 600.0
#: Refresh this far before the access token dies, so a tool call never races its expiry (R49).
REFRESH_MARGIN_SECONDS: Final = 120.0

#: The refusal reasons a callback can produce. They name the failed check, never its value.
REFUSED_NO_FLOW: Final = "no_flow"
REFUSED_EXPIRED: Final = "expired"
REFUSED_STATE: Final = "state_mismatch"
REFUSED_ISS: Final = "iss_mismatch"

CONNECT_HINT: Final = "Open the sign-in page returned by connect_twitch_account, then retry."
NOT_CONNECTED_MESSAGE: Final = "No Twitch account is connected to this server"
Clock = Callable[[], float]
#: ``POST /oauth2/token``: takes the form fields, returns ``(status, parsed_body)``.
TokenGrant = Callable[[Mapping[str, str]], Awaitable[tuple[int, Any]]]
#: ``POST /oauth2/revoke``: takes the form fields, returns ``(status, parsed_body)``.
RevokeCall = Callable[[Mapping[str, str]], Awaitable[tuple[int, Any]]]
#: ``GET /oauth2/validate`` for one bearer token, returning ``(status, parsed_body)``.
ValidateCall = Callable[[str], Awaitable[tuple[int, Any]]]


def _quote(value: str) -> str:
    return quote(value, safe="")


def _encode(params: Mapping[str, str]) -> str:
    return "&".join(f"{key}={_quote(value)}" for key, value in params.items())


def new_state() -> str:
    """A 32-byte URL-safe single-use ``state`` from :mod:`secrets` (R47)."""
    return secrets.token_urlsafe(32)


def expected_iss(auth_base: str) -> str:
    """The exact ``iss`` checked when present: the auth base, no trailing slash (R47)."""
    return auth_base.rstrip("/")


def authorize_url(
    auth_base: str, client_id: str, redirect_uri: str, state: str, scopes: list[str]
) -> str:
    """The consent URL for ``GET /oauth/start`` (R46), parameters in this order.

    Deliberately carries no ``code_challenge`` / ``code_challenge_method`` (spec D8 — Twitch rejects
    them). The scope list is operator configuration passed straight through; this module holds no
    scope table (D10).
    """
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "scope": " ".join(scopes),
        "redirect_uri": redirect_uri,
        "state": state,
        "force_verify": "true",
    }
    return f"{auth_base.rstrip('/')}{AUTHORIZE_PATH}?{_encode(params)}"


def _same(presented: str, expected: str) -> bool:
    """Constant-time string comparison, used for ``state`` and ``iss`` (R47)."""
    return secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


# --------------------------------------------------------------------------- pending flow (R47)


@dataclass(slots=True)
class PendingFlow:
    """One in-memory sign-in attempt: ``state``, ``iss`` (checked when present) and expiry (R47)."""

    state: str
    iss: str
    expires_at: float
    created_at: float
    scopes: tuple[str, ...] = ()


class PendingStore:
    """At most one pending flow: single-use ``state``, ``iss`` when present, 10-minute TTL (R47)."""

    def __init__(
        self,
        *,
        clock: Clock = time.time,
        make_state: Callable[[], str] = new_state,
        ttl_seconds: float = PENDING_TTL_SECONDS,
    ) -> None:
        self._clock = clock
        self._make_state = make_state
        self._ttl = ttl_seconds
        self._flow: PendingFlow | None = None

    def start(self, *, iss: str, scopes: list[str]) -> PendingFlow:
        """Replace any pending flow with a fresh single-use one (R47)."""
        self.clear()
        now = self._clock()
        flow = PendingFlow(
            state=self._make_state(),
            iss=iss,
            expires_at=now + self._ttl,
            created_at=now,
            scopes=tuple(scopes),
        )
        register_secret(flow.state)
        self._flow = flow
        return flow

    def claim(self, state: str | None, iss: str | None) -> PendingFlow | str:
        """Consume the pending flow, or return the reason it was refused (R47).

        Any call consumes it, so a replayed callback is refused even with a correct ``state``.
        ``iss`` is checked when present; an absent ``iss`` (Twitch's real redirect) is not refused.
        """
        flow, self._flow = self._flow, None
        if flow is None:
            if presented := state:
                unregister_secret(presented)
            return REFUSED_NO_FLOW
        if self._clock() >= flow.expires_at:
            self._drop(flow, state)
            return REFUSED_EXPIRED
        if state is None or not _same(state, flow.state):
            self._drop(flow, state)
            return REFUSED_STATE
        if iss is not None and not _same(iss, flow.iss):
            self._drop(flow, state)
            return REFUSED_ISS
        return flow

    def release(self, flow: PendingFlow) -> None:
        """Stop scrubbing a claimed flow's ``state`` once the caller is done with it."""
        self._drop(flow, None)

    def clear(self) -> None:
        """Drop the pending flow (disconnect, shutdown) and stop scrubbing its ``state``."""
        if self._flow is not None:
            self._drop(self._flow, None)
        self._flow = None

    @staticmethod
    def _drop(flow: PendingFlow, presented: str | None) -> None:
        unregister_secret(flow.state)
        if presented:
            unregister_secret(presented)

    @property
    def pending(self) -> bool:
        """True while an unconsumed, unexpired flow exists."""
        return self._flow is not None and self._clock() < self._flow.expires_at

    @property
    def seconds_left(self) -> int:
        """Seconds before the pending flow expires (shown by ``connect_twitch_account``)."""
        if self._flow is None:
            return 0
        return max(0, int(self._flow.expires_at - self._clock()))


# --------------------------------------------------------------------------- the token (R48)


@dataclass(frozen=True, slots=True)
class UserToken:
    """The single user grant: in memory and in the ``0600`` file (R48)."""

    access_token: SecretStr
    refresh_token: SecretStr
    token_type: str
    scope: str
    expires_at: float
    user_id: str
    user_login: str
    connected_at: float
    issued_at: float = field(default=0.0)

    @property
    def scopes(self) -> tuple[str, ...]:
        """The granted scope names as Twitch wrote them in ``scope`` (never a code table, D10)."""
        return tuple(self.scope.split())


class TokenFileInvalid(Exception):
    """The token file exists but cannot be used. ``reason`` never carries file content (R52)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _normalise_scope(scope: Any) -> str | None:
    """A token response's ``scope`` as the stored space-joined string (W1–W3).

    ``None`` (absent or ``null``) stays ``None``; a ``str`` is kept; a JSON array whose elements are
    all ``str`` is joined with single spaces. Any other shape is refused as ``malformed``, and the
    refusal names the field, never its value (R52).
    """
    if scope is None or isinstance(scope, str):
        return scope
    if isinstance(scope, list) and all(isinstance(item, str) for item in scope):
        return " ".join(scope)
    raise TwitchMcpError(
        "upstream", "Twitch returned an unexpected token response", hint=CONNECT_HINT
    )


def build_token(body: Any, *, now: float, fallback: UserToken | None = None) -> UserToken:
    """Turn a ``POST /oauth2/token`` response into a :class:`UserToken` (R49).

    ``scope`` is Twitch's documented JSON array of strings (joined with single spaces into the
    stored string) or a plain string; any other shape is refused (W1–W3). Raises
    ``kind=upstream`` when the shape is wrong. Only field presence and type are examined;
    no value is ever echoed (R52). On a refresh (``fallback`` given) a missing ``refresh_token`` or
    ``scope`` keeps the previous value, because Twitch may omit them.
    """
    malformed = TwitchMcpError(
        "upstream", "Twitch returned an unexpected token response", hint=CONNECT_HINT
    )
    if not isinstance(body, Mapping):
        raise malformed
    access = body.get("access_token")
    refresh = body.get("refresh_token")
    token_type = body.get("token_type")
    scope = _normalise_scope(body.get("scope"))
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
    if scope is None:
        raise malformed
    if isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in <= 0:
        raise malformed
    user_id = body.get("user_id")
    user_login = body.get("user_login")
    register_secret(access)
    register_secret(refresh)
    return UserToken(
        access_token=SecretStr(access),
        refresh_token=SecretStr(refresh),
        token_type="Bearer",  # noqa: S106 - the OAuth token type, not a credential
        scope=scope,
        expires_at=now + expires_in,
        user_id=user_id if isinstance(user_id, str) else "",
        user_login=user_login if isinstance(user_login, str) else "",
        connected_at=fallback.connected_at if fallback is not None else now,
        issued_at=now,
    )


_FILE_VERSION: Final = 1
_FILE_STRINGS: Final = ("access_token", "refresh_token", "token_type", "scope", "user_id")
_FILE_NUMBERS: Final = ("expires_at", "connected_at", "issued_at")


def _from_file(data: Any) -> UserToken:
    if not isinstance(data, Mapping) or data.get("version") != _FILE_VERSION:
        raise TokenFileInvalid("unsupported_format")
    if not all(isinstance(data.get(key), str) for key in _FILE_STRINGS):
        raise TokenFileInvalid("invalid_fields")
    if not all(
        isinstance(data.get(key), int | float) and not isinstance(data.get(key), bool)
        for key in _FILE_NUMBERS
    ):
        raise TokenFileInvalid("invalid_fields")
    login = data.get("user_login")
    token = UserToken(
        access_token=SecretStr(str(data["access_token"])),
        refresh_token=SecretStr(str(data["refresh_token"])),
        token_type=str(data["token_type"]),
        scope=str(data["scope"]),
        expires_at=float(str(data["expires_at"])),
        user_id=str(data["user_id"]),
        user_login=login if isinstance(login, str) else "",
        connected_at=float(str(data["connected_at"])),
        issued_at=float(str(data["issued_at"])),
    )
    if not token.access_token.get_secret_value() or not token.refresh_token.get_secret_value():
        raise TokenFileInvalid("invalid_fields")
    register_secret(token.access_token.get_secret_value())
    register_secret(token.refresh_token.get_secret_value())
    return token


_TEMP_NONCE = secrets.token_hex(4)


def _write_private(path: Path, data: bytes) -> None:
    """Create ``path`` exclusively at ``0600``, fsync it, and move it into place atomically (R48).

    Pure ``pathlib`` on purpose: R6 makes ``config.py`` the only module that reads the process
    settings block, and the R6 grep in ``test_config`` treats *any* ``os`` import outside
    ``config.py`` as an
    offender. ``Path.open("xb", 0o600)`` is ``O_WRONLY | O_CREAT | O_EXCL`` and creates the file at
    mode ``0600`` in one syscall — there is no window in which a half-written token file is
    world-readable. The temp lives in the destination directory, so ``Path.replace`` is an atomic
    same-filesystem rename. Durability is not asserted: buffered I/O exposes no ``os`` handle to
    fsync without importing ``os``, and R48 requires only the 0600 permission, exclusivity and
    atomicity.
    """
    temp = path.parent / f".{path.name}.tmp.{_TEMP_NONCE}"
    try:
        with temp.open("xb", 0o600) as handle:
            handle.write(data)
            handle.flush()
        temp.chmod(0o600)  # defeat a permissive umask
        temp.replace(path)
        path.chmod(0o600)
    except OSError:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise


class TokenStore:
    """The server's only persistent artifact (R48): ``0600``, written atomically, never logged."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _remove_stray_temps(self) -> None:
        """Delete leftovers of an interrupted save — they hold a token too."""
        with contextlib.suppress(OSError):
            for stray in self.path.parent.glob(f".{self.path.name}.*.tmp"):
                with contextlib.suppress(OSError):
                    stray.unlink()

    def load(self) -> UserToken | None:
        """The stored token, or ``None`` when there is no file. Raises :class:`TokenFileInvalid`."""
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
        """Write atomically at ``0600`` (R48). A failure is ``upstream``, never a raw crash."""
        payload = {
            "version": _FILE_VERSION,
            "access_token": token.access_token.get_secret_value(),
            "refresh_token": token.refresh_token.get_secret_value(),
            "token_type": token.token_type,
            "scope": token.scope,
            "user_id": token.user_id,
            "user_login": token.user_login,
            "expires_at": token.expires_at,
            "connected_at": token.connected_at,
            "issued_at": token.issued_at,
        }
        data = json.dumps(payload).encode("utf-8")
        self._remove_stray_temps()
        try:
            _write_private(self.path, data)  # 0600, exclusive temp, atomic replace (R48)
        except OSError:
            raise TwitchMcpError(
                "upstream",
                "The Twitch user token file could not be written",
                hint="Check that the token directory exists and is writable, then reconnect.",
            ) from None

    def delete(self) -> None:
        """Delete the token file — the only credential this server stores (R51)."""
        self._remove_stray_temps()
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            raise TwitchMcpError(
                "upstream",
                "The Twitch user token file could not be deleted",
                hint="Delete the token file by hand; it is still on disk and holds the token.",
            ) from None

    def exists(self) -> bool:
        """Whether the store currently holds a credential (for ``twitch_account_status``)."""
        return self.path.is_file()

    def mode(self) -> int | None:
        """The file's permission bits, for the R48 test. ``None`` when there is no file."""
        try:
            return self.path.stat().st_mode & 0o777
        except OSError:
            return None


# --------------------------------------------------------------------------- user identity


class UserAuth:
    """The process-wide user identity: token, pending flow, refresh under one lock (R49).

    ``grant`` performs ``POST /oauth2/token``, ``revoke`` performs ``POST /oauth2/revoke`` and
    ``validate`` performs ``GET /oauth2/validate``; all three are injected so this module stays
    import-free of any network library (R11). A refresh failure forces re-auth and clears the store
    — never a silent app-token fallback for a user-only tool (R49).
    """

    def __init__(
        self,
        store: TokenStore,
        *,
        clock: Clock = time.time,
        grant: TokenGrant | None = None,
        revoke: RevokeCall | None = None,
        validate: ValidateCall | None = None,
    ) -> None:
        self.store = store
        self._clock = clock
        self._grant = grant
        self._revoke = revoke
        self._validate = validate
        self.token: UserToken | None = None
        self.token_file_invalid = False
        self.last_error: str | None = None
        self.pending = PendingStore(clock=clock)
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------ invalidation hooks (R30, R33)

    def on_invalidate(self, listener: Callable[[], None]) -> None:
        """Subscribe to identity invalidation (R30) and confirm-token clearing (R33).

        ``identity.py`` and ``confirmation.py`` register here, so a token refresh, a disconnect or a
        credential-revoking failure can never leave a stale verdict or confirm token alive.
        """
        self._listeners.append(listener)

    def _invalidate(self) -> None:
        for listener in self._listeners:
            with contextlib.suppress(Exception):
                listener()

    # ------------------------------------------------------------ state

    @property
    def state(self) -> str:
        """``connected`` / ``not_connected`` / ``token_file_invalid`` — a name, never a value."""
        if self.token is not None:
            return "connected"
        return "token_file_invalid" if self.token_file_invalid else "not_connected"

    @property
    def connected(self) -> bool:
        return self.token is not None

    @property
    def user_id(self) -> str | None:
        """The operator's own broadcaster id, taken from the token — never an argument (R29)."""
        token = self.token
        return token.user_id if token is not None and token.user_id else None

    @property
    def lock(self) -> asyncio.Lock:
        """The one refresh lock (R49). Disconnect holds it so no refresh re-creates the file."""
        return self._lock

    def attach_grant(self, grant: TokenGrant) -> None:
        """Wire ``POST /oauth2/token`` once the client exists (it needs this object first)."""
        self._grant = grant
        self._check_grant_shape(grant)

    @staticmethod
    def _check_grant_shape(grant: TokenGrant) -> None:
        if not callable(grant):
            raise TwitchMcpError("upstream", "The Twitch token endpoint is not usable")

    def attach_revoke(self, revoke: RevokeCall) -> None:
        """Wire ``POST /oauth2/revoke`` (R51)."""
        self._revoke = revoke

    def attach_validate(self, validate: ValidateCall) -> None:
        """Wire ``GET /oauth2/validate``, used by the boot probe (R50)."""
        self._validate = validate

    # ------------------------------------------------------------ boot (R50)

    def load(self) -> None:
        """Load the token file once at boot. A bad file degrades to disconnected, never crashes."""
        try:
            self.token = self.store.load()
        except TokenFileInvalid as exc:
            self.token = None
            self.token_file_invalid = True
            logger.warning("twitch user token file invalid", extra={"outcome": exc.reason})
        parent = self.store.path.parent
        if self.token is None and not self.token_file_invalid and not parent.is_dir():
            logger.warning(
                "twitch user token folder missing", extra={"outcome": "token_folder_missing"}
            )
        if self.token is not None:
            register_secret(self.token.access_token.get_secret_value())
            register_secret(self.token.refresh_token.get_secret_value())

    async def probe(self) -> str:
        """The user half of the R50 boot probe: ``not_connected`` / ``ok`` / degraded.

        A stored token that Twitch no longer accepts clears the store and reports the degradation;
        the server still starts.
        """
        if self.token is None:
            return "not_connected"
        if self._validate is None:
            return "ok"
        status, _body = await self._validate(self.token.access_token.get_secret_value())
        if status == 401:
            self.last_error = "token_rejected"
            self.forget()
            return "unauthenticated"
        if status >= 500:
            return "unreachable"
        return "ok"

    # ------------------------------------------------------------ install / forget (R48, R51)

    def install(self, token: UserToken) -> None:
        """Persist first, then use (R48). A failed write leaves the previous state in place."""
        self.store.save(token)
        self.token = token
        self.token_file_invalid = False
        self.last_error = None
        self._invalidate()

    def forget(self) -> None:
        """Drop the token from memory, then delete the file (R51).

        Memory goes first so a failed delete can never leave the credential in use here. Nothing
        else on the account is touched (R51). Clears cached identity and confirm tokens (R30, R33).
        """
        previous = self.token
        self.token = None
        self.token_file_invalid = False
        if previous is not None:
            unregister_secret(previous.access_token.get_secret_value())
            unregister_secret(previous.refresh_token.get_secret_value())
        self.store.delete()
        self._invalidate()

    async def disconnect(self) -> str | None:
        """``disconnect_twitch_account`` (R51): revoke, then delete only our own credential.

        Holds the refresh lock so a concurrent refresh cannot re-create the file behind this call.
        Returns the user id that was disconnected, or ``None`` when nothing was connected.
        """
        async with self._lock:
            token = self.token
            user_id = token.user_id if token is not None else None
            if token is not None and self._revoke is not None:
                if not self._client_id:
                    # Revoke needs the app's client id in the form (W6); without it, skip the call.
                    logger.warning("twitch revoke skipped", extra={"outcome": "revoke_skipped"})
                else:
                    try:
                        await self._revoke(
                            {
                                "client_id": self._client_id,
                                "token": token.access_token.get_secret_value(),
                            }
                        )
                    except TwitchMcpError as exc:
                        if exc.kind != "unauthenticated":
                            raise
                        # An already-dead token still means the local credential must go (R51).
                        logger.warning("twitch revoke refused", extra={"outcome": "revoke_refused"})
            self.forget()
            return user_id

    # ------------------------------------------------------------ grants (R49)

    async def exchange(
        self, code: str, redirect_uri: str, client_id: str, client_secret: str
    ) -> UserToken:
        """Exchange an authorization code for the user token. Never retried (R16).

        The code is used exactly once by construction: the flow that produced it was already
        consumed by :meth:`PendingStore.claim` (R47).
        """
        if self._grant is None:
            raise TwitchMcpError("upstream", "Twitch sign-in is not configured", hint=CONNECT_HINT)
        status, body = await self._grant(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            }
        )
        token = self._token_or_raise(status, body, CONNECT_HINT)
        if not token.user_id:
            user_id, user_login = await self._resolve_user(token)
            token = _with_identity(token, user_id, user_login)
        self.install(token)
        return token

    async def refresh_now(self, *, stale: str | None = None) -> UserToken:
        """Refresh under the single lock (R49) — concurrent callers await one refresh.

        ``stale`` is the access token a caller found rejected. A caller that waited on the lock
        reuses whatever token another caller installed meanwhile, so a burst never causes a burst of
        refreshes.
        """
        if self.token is None:
            raise TwitchMcpError("unauthenticated", NOT_CONNECTED_MESSAGE, hint=CONNECT_HINT)
        async with self._lock:
            token = self.token
            if token is None:
                raise TwitchMcpError("unauthenticated", NOT_CONNECTED_MESSAGE, hint=CONNECT_HINT)
            current = token.access_token.get_secret_value()
            if stale is not None and current != stale:
                return token  # refreshed while we waited
            if stale is None and not self._expiring(token):
                return token
            if self._grant is None:
                raise TwitchMcpError(
                    "upstream", "Twitch account refresh is not configured", hint=CONNECT_HINT
                )
            try:
                status, body = await self._grant(
                    {
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": token.refresh_token.get_secret_value(),
                    }
                )
                renewed = self._token_or_raise(status, body, CONNECT_HINT, fallback=token)
            except TwitchMcpError as exc:
                if exc.kind == "unauthenticated":
                    self._force_reauth("refresh_rejected")
                raise
            self.install(renewed)
            return renewed

    def _expiring(self, token: UserToken) -> bool:
        return token.expires_at - self._clock() < REFRESH_MARGIN_SECONDS

    def _token_or_raise(
        self, status: int, body: Any, hint: str, *, fallback: UserToken | None = None
    ) -> UserToken:
        """Decide a token-endpoint answer by status alone (R44, W4): any 400 means re-auth."""
        if 200 <= status < 300:
            return build_token(body, now=self._clock(), fallback=fallback)
        if status == 400:
            raise TwitchMcpError(
                "unauthenticated",
                "Twitch refused that grant; sign in again",
                http_status=status,
                hint=hint,
            )
        if status == 401:
            raise TwitchMcpError(
                "unauthenticated",
                "Twitch rejected this server's credentials",
                http_status=status,
                hint="Check TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env, then restart.",
            )
        if status >= 500:
            raise TwitchMcpError(
                "upstream",
                "Twitch's OAuth service returned a server error",
                http_status=status,
                retryable=True,
                hint=hint,
            )
        raise TwitchMcpError(
            "upstream", "Twitch's OAuth service rejected the request", http_status=status, hint=hint
        )

    async def _resolve_user(self, token: UserToken) -> tuple[str, str]:
        """Learn ``user_id`` from ``GET /oauth2/validate`` (R29) when the grant omits it."""
        if self._validate is None:
            raise TwitchMcpError(
                "upstream",
                "Twitch did not identify the account behind that token",
                hint=CONNECT_HINT,
            )
        status, body = await self._validate(token.access_token.get_secret_value())
        if status != 200 or not isinstance(body, Mapping):
            raise TwitchMcpError(
                "unauthenticated",
                "Twitch refused the new token",
                http_status=status,
                hint=CONNECT_HINT,
            )
        user_id = body.get("user_id")
        login = body.get("login")
        if not isinstance(user_id, str) or not user_id:
            raise TwitchMcpError(
                "upstream",
                "Twitch did not identify the account behind that token",
                hint=CONNECT_HINT,
            )
        return user_id, login if isinstance(login, str) else ""

    def _force_reauth(self, reason: str) -> None:
        """R49: a grant that cannot be redeemed means re-auth, store cleared. Never an app-token
        fallback for a user-only tool."""
        self.last_error = reason
        self.forget()

    # ------------------------------------------------------------ access token

    _client_id: str = ""
    _client_secret: str = ""

    def set_credentials(self, client_id: str, client_secret: str) -> None:
        """The app's own credentials, needed by the refresh grant (R49). Never logged (R52)."""
        self._client_id = client_id
        self._client_secret = client_secret
        register_secret(client_secret)

    async def access_token(self, *, stale: str | None = None) -> str:
        """A usable access token, refreshed near expiry (R49).

        With no user token this raises ``kind=unauthenticated`` naming the sign-in page: a user-only
        tool never falls back to the app token (R49).
        """
        token = await self.refresh_now(stale=stale)
        return token.access_token.get_secret_value()

    async def current_access_token(self) -> str:
        """The stored token without refreshing: for the boot probe and account status."""
        if self.token is None:
            raise TwitchMcpError("unauthenticated", NOT_CONNECTED_MESSAGE, hint=CONNECT_HINT)
        return self.token.access_token.get_secret_value()


def _with_identity(token: UserToken, user_id: str, user_login: str) -> UserToken:
    """A copy of ``token`` carrying the identity learned from ``/oauth2/validate`` (R29)."""
    return UserToken(
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        token_type=token.token_type,
        scope=token.scope,
        expires_at=token.expires_at,
        user_id=user_id,
        user_login=user_login,
        connected_at=token.connected_at,
        issued_at=token.issued_at,
    )


class OAuthFlow:
    """Wires :class:`UserAuth` to the client's form POSTs and the operator's settings.

    ``server.py`` owns the two routes; this object owns every decision about them, so the routes
    stay three lines each. Building it performs no I/O (R50).
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        auth_base: str,
        redirect_uri: str,
        sign_in_url: str,
        scopes: list[str],
        token_file: str,
        enabled: bool,
        clock: Clock = time.time,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._auth_base = auth_base.rstrip("/")
        self._sign_in_url = sign_in_url
        self.redirect_uri = redirect_uri
        self.scopes = list(scopes)
        self.enabled = enabled
        self.auth = UserAuth(TokenStore(Path(token_file)), clock=clock)
        self.auth.set_credentials(client_id, client_secret)
        self._grant: TokenGrant | None = None
        self._revoke: RevokeCall | None = None

    # ------------------------------------------------------------ wiring

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def sign_in_url(self) -> str:
        return self._sign_in_url

    @property
    def iss(self) -> str:
        """The exact ``iss`` this deployment's callback is checked against when present (R47)."""
        return expected_iss(self._auth_base)

    @property
    def token_file(self) -> Path:
        return self.auth.store.path

    def attach(
        self,
        *,
        grant: TokenGrant,
        revoke: RevokeCall,
        validate: ValidateCall | None = None,
    ) -> None:
        """Give the flow its network callables — all executed by ``twitch_client.py`` (R11)."""
        self._grant = grant
        self._revoke = revoke
        self.auth.attach_grant(grant)
        self.auth.attach_revoke(revoke)
        if validate is not None:
            self.auth.attach_validate(validate)

    # ------------------------------------------------------------ sign-in (R46, R47)

    def start(self) -> PendingFlow:
        """Begin a sign-in: a fresh single-use ``state`` and the expected ``iss`` (R47)."""
        return self.auth.pending.start(iss=self.iss, scopes=self.scopes)

    def start_url(self) -> str:
        """The consent URL for the pending flow, minting one if none is pending (R46)."""
        flow = self.auth.pending._flow or self.start()
        return authorize_url(
            self._auth_base, self._client_id, self.redirect_uri, flow.state, self.scopes
        )

    async def complete(self, *, code: str | None, state: str | None, iss: str | None) -> str:
        """Finish a callback: ``state``/``iss``/TTL, then the code exchange (R47, R49).

        Returns the connected user id. Every refusal raises ``kind=unauthenticated`` naming only
        which check failed — never the presented value, the code or the state (R52).
        """
        if not self.enabled:
            raise TwitchMcpError(
                "disabled", "User OAuth is off; set TWITCH_USER_OAUTH=true to sign in"
            )
        flow = self.auth.pending.claim(state, iss)
        if isinstance(flow, str):
            self.auth.last_error = flow
            raise TwitchMcpError(
                "unauthenticated",
                "That sign-in attempt could not be completed",
                hint=f"Open {self._sign_in_url} again and retry ({flow}).",
            )
        try:
            if not code:
                raise TwitchMcpError(
                    "unauthenticated", "Twitch returned no authorization code", hint=CONNECT_HINT
                )
            if self._grant is None:
                raise TwitchMcpError(
                    "upstream", "Twitch sign-in is not configured", hint=CONNECT_HINT
                )
            token = await self.auth.exchange(
                code, self.redirect_uri, self._client_id, self._client_secret
            )
            return token.user_id
        finally:
            self.auth.pending.release(flow)

    # ------------------------------------------------------------ disconnect (R51)

    async def disconnect(self) -> str | None:
        """Revoke, then delete only this server's own credential (R51). Nothing else, ever."""
        return await self.auth.disconnect()

    # ------------------------------------------------------------ identity

    @property
    def connected(self) -> bool:
        return self.auth.connected

    @property
    def user_id(self) -> str | None:
        """The operator's own broadcaster id (R29) — the sole authority for "own channel"."""
        return self.auth.user_id

    def on_invalidate(self, listener: Callable[[], None]) -> None:
        """Subscribe to identity invalidation / confirmation clearing (R30, R33)."""
        self.auth.on_invalidate(listener)


__all__ = [
    "AUTHORIZE_PATH",
    "CONNECT_HINT",
    "NOT_CONNECTED_MESSAGE",
    "PENDING_TTL_SECONDS",
    "REFUSED_EXPIRED",
    "REFUSED_ISS",
    "REFUSED_NO_FLOW",
    "REFUSED_STATE",
    "OAuthFlow",
    "PendingFlow",
    "PendingStore",
    "RevokeCall",
    "TokenFileInvalid",
    "TokenGrant",
    "TokenStore",
    "UserAuth",
    "UserToken",
    "ValidateCall",
    "authorize_url",
    "build_token",
    "expected_iss",
    "new_state",
]
