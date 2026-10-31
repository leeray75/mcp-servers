"""The ONLY module that imports ``httpx`` (R11) — every byte that leaves the process uses it.

Both upstream hosts are pinned from the boot configuration (R12): Helix for the API,
``id.twitch.tv`` for the OAuth endpoints. Redirects are never followed (a 3xx is an error, not a
hop). Bodies are streamed and refused past ``TWITCH_MAX_RESPONSE_BYTES`` rather than truncated
(R14). Every request carries ``Client-Id`` and ``Authorization: Bearer``; a request that would
leave without either is raised **before** the socket (R13).

Argument-derived path segments are validated and percent-encoded here (R17): user ids are 1–20
digits, logins ``[A-Za-z0-9_]{1,25}``, cursors the base64 charset, and any id appearing in a path
goes through :func:`path_segment`. Query lists repeat the parameter per value (``&id=1&id=2``)
rather than comma-joining it (R18).

**Reads and writes are deliberately asymmetric (R15/R16).** :meth:`TwitchClient.get` retries 5xx and
transport errors within ``TWITCH_MAX_5XX_RETRIES`` and retries a 503 exactly once. The four write
verbs (:meth:`TwitchClient.post` / :meth:`put` / :meth:`patch` / :meth:`delete`, plus every OAuth
form POST) share no code path with that retry helper: a write is dispatched **exactly once** and an
outcome that cannot be determined (transport error, 5xx, a 2xx whose body could not be read)
becomes ``kind=unknown_outcome`` with a check-the-channel hint, never a replay.

Rate limits are learned from ``Ratelimit-*`` via the :class:`~twitch_mcp.ratelimit.Lease` (R19); a
429 is never replayed (R22). The first ``401`` from Helix opens the process-wide circuit through
:meth:`RateLimiter.trip_auth` (R24). ``kind`` comes from the HTTP status plus Twitch's own
``status`` / ``error`` / ``error_description`` fields — never from the free-form ``message`` (R44).
A 401/403 whose body names a missing scope becomes ``kind=permission`` whose hint carries that scope
string plus the token's granted scopes (R45); the scope string is whatever Twitch said and is not
hard-coded anywhere (D10).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal
from urllib.parse import quote, urlsplit

import httpx

from twitch_mcp.config import Settings
from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.logging_setup import log_request, log_write
from twitch_mcp.ratelimit import APP_CLASS, Clock, Lease, RateLimiter, Sleep, TokenType

logger = logging.getLogger(__name__)

#: Backoff before a read's second attempt. Kept tiny so a tool call stays interactive.
READ_BACKOFF_SECONDS: Final = (0.2, 0.6, 1.2)

#: A Helix 429 with no usable learned wait parks for this long (R21). It is a parking duration,
#: not a capacity — no limit value is ever baked in (R19).
DEFAULT_429_PARK_SECONDS: Final = 1.0

#: A stable per-process fraction added to a learned 429 park so several clients parked on the same
#: header do not all wake on the same instant. It is jitter, not a limit value (R19/R21).
JITTER_CEILING_SECONDS: Final = 0.5

#: The only OAuth paths ever contacted, all under ``TWITCH_AUTH_BASE`` (R17, R51).
OAUTH_TOKEN_PATH: Final = "/oauth2/token"  # noqa: S105 - an endpoint path, not a secret
OAUTH_AUTHORIZE_PATH: Final = "/oauth2/authorize"
OAUTH_VALIDATE_PATH: Final = "/oauth2/validate"
OAUTH_REVOKE_PATH: Final = "/oauth2/revoke"  # noqa: S105 - an endpoint path, not a secret

#: The two shapes a write result takes, decided by :meth:`TwitchClient._finish`.
WriteOutcome = Literal["ok", "unknown_outcome"]

_USER_ID_RE: Final = re.compile(r"[0-9]{1,20}")
_LOGIN_RE: Final = re.compile(r"[A-Za-z0-9_]{1,25}")
_CURSOR_RE: Final = re.compile(r"[A-Za-z0-9_=-]{1,256}")
_SEGMENT_RE: Final = re.compile(r"[A-Za-z0-9_.%~-]{1,128}")
_INT_RE: Final = re.compile(r"[0-9]{1,10}")
#: Where a Helix 401/403 body says the token lacks the scope the endpoint needs (R45). These are
#: Twitch's own field names — a location in the body, not a scope table (D10).
_SCOPE_BEARING_KEYS: Final[tuple[str, ...]] = ("error_description", "error")
#: Substrings that mark one of those fields as being about a missing scope. ``message`` is never
#: consulted (R44).
_SCOPE_PHRASES: Final[tuple[str, ...]] = ("missing scope", "requires", "does not have", "missing")

Method = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]

#: Methods that change state on Twitch. R16's no-retry rule applies to every one of them.
WRITE_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: The only methods this server ever issues (R17).
ALLOWED_METHODS: Final[frozenset[str]] = WRITE_METHODS | {"GET"}


# --------------------------------------------------------------------------- validation (R17)


class InvalidArgument(ValueError):
    """An argument-derived URL component is malformed. Refused before the socket (R17)."""


def _validated(value: object, pattern: re.Pattern[str], what: str) -> str:
    text = value if isinstance(value, str) else str(value)
    if pattern.fullmatch(text) is None:
        raise InvalidArgument(f"{what} is not a valid value")
    return text


def user_id(value: object, *, what: str = "user id") -> str:
    """A Twitch user/broadcaster id: 1–20 digits (R17). Raises :class:`InvalidArgument`."""
    return _validated(value, _USER_ID_RE, what)


def login(value: object, *, what: str = "login") -> str:
    """A Twitch login: ``[A-Za-z0-9_]{1,25}`` (R17). Raises :class:`InvalidArgument`."""
    return _validated(value, _LOGIN_RE, what)


def cursor(value: object, *, what: str = "cursor") -> str:
    """A Helix pagination cursor: the base64 charset, nothing else (R17)."""
    return _validated(value, _CURSOR_RE, what)


def integer(value: object, *, what: str = "integer") -> str:
    """A non-negative integer argument rendered for a query string (R17)."""
    if isinstance(value, bool):
        raise InvalidArgument(f"{what} must be an integer")
    if isinstance(value, int):
        return _validated(str(value), _INT_RE, what)
    return _validated(value, _INT_RE, what)


def opaque(value: object, *, what: str = "identifier") -> str:
    """An opaque Helix id (video/clip/poll/prediction/tag/set id).

    These are opaque strings of unknown alphabet, so the accepted set is conservative: URL-safe
    characters only, no ``/``, ``?``, ``#`` or ``..`` (R17).
    """
    text = _validated(value, _SEGMENT_RE, what)
    if ".." in text:
        raise InvalidArgument(f"{what} is not a valid identifier")
    return text


def path_segment(value: object, *, what: str = "path segment") -> str:
    """Percent-encode one argument-derived path segment (R17).

    Rejected outright rather than escaped when the value could change the path's shape or carries a
    control character — an agent-supplied string never becomes a raw path segment.
    """
    text = value if isinstance(value, str) else str(value)
    if not text or len(text) > 128:
        raise InvalidArgument(f"{what} is not a valid path segment")
    if any(char in text for char in "/?#\\") or ".." in text:
        raise InvalidArgument(f"{what} cannot be used as a path segment")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
        raise InvalidArgument(f"{what} contains control characters")
    return quote(text, safe="")


def repeat(name: str, values: Sequence[str]) -> list[tuple[str, str]]:
    """Helix list convention: the parameter repeats per value, never comma-delimited (R18)."""
    return [(name, value) for value in values]


# --------------------------------------------------------------------------- routes


@dataclass(frozen=True, slots=True)
class Route:
    """One Helix route: the template used for limits and logs, and the concrete path."""

    template: str
    path: str
    major: str = ""
    token_type: TokenType = APP_CLASS

    @staticmethod
    def build(
        template: str,
        major: str = "",
        *,
        token_type: TokenType = APP_CLASS,
        **values: str,
    ) -> Route:
        """Fill ``{name}`` placeholders with already-validated values (R17).

        Callers validate with :func:`user_id` / :func:`login` / :func:`cursor` / :func:`opaque`
        first; this only percent-encodes and refuses a segment that would change the path's shape.
        """
        path = template
        for name, value in values.items():
            path = path.replace("{" + name + "}", path_segment(value, what=name))
        if "{" in path or "}" in path:
            raise InvalidArgument("route template has an unfilled segment")
        return Route(template=template, path=path, major=major, token_type=token_type)


@dataclass(frozen=True, slots=True)
class Response:
    """A successful (2xx) Helix response with its decoded JSON body."""

    status: int
    body: Any
    headers: Mapping[str, str]

    @property
    def data(self) -> list[Any]:
        """The ``data`` array of a Helix envelope, empty when Helix returned none."""
        if isinstance(self.body, Mapping):
            data = self.body.get("data")
            if isinstance(data, list):
                return data
        return []

    @property
    def pagination(self) -> dict[str, str]:
        """The ``pagination`` object. ``{}`` means there is no further page (R42)."""
        if isinstance(self.body, Mapping):
            page = self.body.get("pagination")
            if isinstance(page, Mapping):
                return {str(key): str(value) for key, value in page.items()}
        return {}

    @property
    def total(self) -> int | None:
        """Helix's ``total`` field when the endpoint publishes one."""
        if isinstance(self.body, Mapping):
            value = self.body.get("total")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    @property
    def template(self) -> dict[str, str]:
        """The ``template`` of ``GET /chat/emotes`` — a URL template, never logged (R52)."""
        if isinstance(self.body, Mapping):
            template = self.body.get("template")
            if isinstance(template, str):
                return {"template": template}
        return {}


@dataclass(frozen=True, slots=True)
class WriteResponse:
    """The single dispatch of one write (R16): Helix's records, and how it ended.

    ``outcome`` is ``"ok"`` only when a 2xx body was actually read back. Every other dispatched
    result — 4xx, 5xx, transport error, unreadable body — is produced by raising instead, with
    ``kind=unknown_outcome`` wherever the outcome is genuinely undeterminable.
    """

    status: int
    data: list[Any]
    outcome: WriteOutcome


@dataclass(frozen=True, slots=True)
class BootProbe:
    """Outcome of the R50 boot probe. ``outcome`` is a state name, never a credential or body."""

    outcome: Literal["ok", "unauthenticated", "unreachable"]
    scopes: tuple[str, ...] = ()
    user_id: str | None = None
    user_login: str | None = None
    expires_in: int | None = None


# --------------------------------------------------------------------------- error mapping


def _parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


def _field(body: Any, name: str) -> str | None:
    if isinstance(body, Mapping):
        value = body.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _status_field(body: Any) -> int | None:
    if isinstance(body, Mapping):
        value = body.get("status")
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return value
    return None


def _error_code(body: Any, status: int) -> int | None:
    """Twitch's own numeric status from the body (R44), falling back to the HTTP status.

    Helix returns its error as ``{"error": "...", "status": 404, "message": "..."}``; the numeric
    ``status`` is the code this server reports. Nothing is ever read from ``message``.
    """
    documented = _status_field(body)
    return documented if documented is not None else status


def _scope_phrase(body: Any) -> str | None:
    """Twitch's own words about a missing scope, from its structured fields only (R44/R45).

    Only ``error`` and ``error_description`` are consulted — never ``message`` — and only when the
    phrase is unmistakably about a scope. The returned string is Twitch's, so nothing about scopes
    is hard-coded here (D10).
    """
    for key in _SCOPE_BEARING_KEYS:
        text = _field(body, key)
        if text is None:
            continue
        lowered = text.lower()
        if "scope" not in lowered:
            continue
        if any(phrase in lowered for phrase in _SCOPE_PHRASES):
            return text
    return None


class _TooLarge(Exception):
    """Internal: the streamed body passed ``TWITCH_MAX_RESPONSE_BYTES`` (R14)."""


# --------------------------------------------------------------------------- the client


class TwitchClient:
    """Async Helix + OAuth client. One instance per process, two bucket classes (R20).

    The app token is supplied by the caller of every call (it is owned by ``oauth.py``) and the user
    token by the caller of the user-credential helpers. The client itself stores no credential, so
    it cannot leak one.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        limiter: RateLimiter | None = None,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._settings = settings
        self._api_base = settings.twitch_api_base
        self._auth_base = settings.twitch_auth_base
        self._api_host = settings.api_host
        self._auth_host = settings.auth_host
        self._max_bytes = settings.twitch_max_response_bytes
        self._max_5xx_retries = settings.twitch_max_5xx_retries
        self._client_id = settings.twitch_client_id.get_secret_value()
        self._clock = clock
        self._sleep_fn = sleep
        self._rand = rand
        self._jitter = max(0.0, min(1.0, rand())) * JITTER_CEILING_SECONDS
        self.limiter = limiter or RateLimiter(
            max_wait_seconds=settings.twitch_max_rate_limit_wait_seconds,
            clock=clock,
            sleep=sleep,
        )
        timeout = settings.twitch_timeout_seconds
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            # R12: a redirect is an error, never a hop. There is no way to opt back in.
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "twitch-mcp-server"},
        )

    async def aclose(self) -> None:
        """Release the connection pool. Called from the server's lifespan shutdown."""
        await self._http.aclose()

    # ------------------------------------------------------------ credential material

    @property
    def client_id(self) -> str:
        """The app's Client-Id. It is an identifier, not a secret, and is never logged (R52)."""
        return self._client_id

    @property
    def auth_failed(self) -> bool:
        """True once the process-wide 401 circuit is open (R24)."""
        return self.limiter.auth_failed

    @property
    def settings(self) -> Settings:
        """The boot settings this client was built from (read-only for the service layer)."""
        return self._settings

    # ------------------------------------------------------------ URL construction

    def _url(self, base: str, host: str, path: str, params: Sequence[tuple[str, str]]) -> str:
        """Build one URL and re-check its host. Only the two pinned bases ever pass (R12)."""
        if not path.startswith("/") or "//" in path:
            raise TwitchMcpError(
                "upstream", "Refused a request path that is not a single set of segments"
            )
        query = "&".join(
            f"{quote(name, safe='')}={quote(value, safe='')}" for name, value in params
        )
        url = f"{base}{path}" + (f"?{query}" if query else "")
        if urlsplit(url).netloc.lower() != host:
            raise TwitchMcpError("upstream", "Refused a request to a host this server does not use")
        return url

    def _headers(self, client_id: str | None, bearer: str | None) -> dict[str, str]:
        """Both credential headers, or the request is refused before the socket (R13)."""
        if not client_id:
            raise TwitchMcpError(
                "unauthenticated",
                "No Client-Id is available for this request",
                hint="Check TWITCH_CLIENT_ID in .env and restart the container.",
            )
        if not bearer:
            raise TwitchMcpError(
                "unauthenticated",
                "No bearer token is available for this request",
                hint="Let the app token be minted, or connect the Twitch account, then retry.",
            )
        return {"Client-Id": client_id, "Authorization": f"Bearer {bearer}"}

    # ------------------------------------------------------------ transport

    async def _send(
        self,
        method: Method,
        url: str,
        *,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None = None,
        json_body: Any = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Stream one request's body under the byte cap (R14). Never follows redirects (R12)."""
        request_headers = dict(headers)
        payload: Mapping[str, str] | None = None
        content: bytes | None = None
        if json_body is not None:
            request_headers["Content-Type"] = "application/json"
            content = json.dumps(json_body).encode("utf-8")
        elif form is not None:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
            payload = {key: value for key, value in form.items()}
        async with self._http.stream(
            method, url, headers=request_headers, data=payload, content=content
        ) as resp:
            lowered = {key.lower(): value for key, value in resp.headers.items()}
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body.extend(chunk)
                if len(body) > self._max_bytes:
                    raise _TooLarge()
            return resp.status_code, lowered, bytes(body)

    def _log(
        self,
        route: Route,
        started: float,
        outcome: str,
        *,
        token_type: TokenType,
        status: int | None = None,
        write: bool = False,
        method: Method = "GET",
    ) -> None:
        """One R53 line: template, status, token type, duration, outcome. Never a URL (R52)."""
        duration_ms = round((self._clock() - started) * 1000, 1)
        if write:
            log_write(
                tool=f"{method} {route.template}",
                broadcaster_id=route.major,
                outcome=outcome,
                duration_ms=duration_ms,
                logger=logger,
            )
            return
        log_request(
            route=f"{method} {route.template}",
            status=status,
            token_type=token_type,
            duration_ms=duration_ms,
            outcome=outcome,
            logger=logger,
        )

    # ------------------------------------------------------------ reads (R15)

    async def get(
        self,
        route: Route,
        *,
        client_id: str,
        bearer: str,
        params: Sequence[tuple[str, str]] = (),
        token_type: TokenType | None = None,
    ) -> Response:
        """GET one Helix route, retrying 5xx and transport errors (R15).

        A 503 is retried **once** whatever ``TWITCH_MAX_5XX_RETRIES`` says — that is Twitch's own
        guidance. A 429 learns from ``Ratelimit-*`` through the lease and is never replayed (R22).
        """
        url = self._url(self._api_base, self._api_host, route.path, params)
        headers = self._headers(client_id, bearer)
        kind: TokenType = token_type or route.token_type
        retries = 0
        saw_503 = False
        while True:
            lease = await self.limiter.acquire(kind, "GET", route.template, route.major)
            started = self._clock()
            try:
                try:
                    status, resp_headers, raw = await self._send("GET", url, headers=headers)
                except _TooLarge:
                    self._log(route, started, "too_large", token_type=kind)
                    raise self._too_large_error() from None
                except httpx.TransportError:
                    if await self._read_retry_allowed(lease, retries):
                        retries += 1
                        continue
                    self._log(route, started, "transport_error", token_type=kind)
                    return self._map("GET", route, 0, {}, None, kind, write=False)
                lease.learn(resp_headers)
                if status == 429:
                    self._apply_429(route, lease, started, resp_headers, kind)
                if status == 503 and not saw_503:
                    # Twitch's own guidance: one retry for a 503, independent of the 5xx budget.
                    saw_503 = True
                    self._log(route, started, "upstream_503_retry", status=status, token_type=kind)
                    lease.release()
                    await self._sleep_fn(READ_BACKOFF_SECONDS[0])
                    continue
                if status >= 500 and retries < self._max_5xx_retries:
                    self._log(route, started, "upstream_5xx_retry", status=status, token_type=kind)
                    lease.release()
                    await self._backoff(retries)
                    retries += 1
                    continue
                return self._finish_read(route, started, status, resp_headers, raw, kind)
            finally:
                lease.release()  # idempotent: every normal path already released it

    async def _backoff(self, retries: int) -> None:
        await self._sleep_fn(READ_BACKOFF_SECONDS[min(retries, len(READ_BACKOFF_SECONDS) - 1)])

    async def _read_retry_allowed(self, lease: Lease, retries: int) -> bool:
        """Release the lease and decide whether a transport failure may be retried (R15)."""
        lease.release()
        if retries >= self._max_5xx_retries:
            return False
        await self._backoff(retries)
        return True

    def _finish_read(
        self,
        route: Route,
        started: float,
        status: int,
        headers: Mapping[str, str],
        raw: bytes,
        kind: TokenType,
    ) -> Response:
        body = _parse_json(raw)
        if 200 <= status < 300:
            if body is None:
                self._log(route, started, "upstream_unexpected", status=status, token_type=kind)
                raise TwitchMcpError(
                    "upstream", "Twitch returned a response this server could not read"
                )
            self._log(route, started, "ok", status=status, token_type=kind)
            return Response(status, body, headers)
        return self._map("GET", route, status, headers, body, kind, write=False)

    # ------------------------------------------------------------ writes (R16)

    async def post(
        self,
        route: Route,
        *,
        client_id: str,
        bearer: str,
        json_body: Any,
        params: Sequence[tuple[str, str]] = (),
    ) -> WriteResponse:
        """``POST`` one Helix route. Dispatched exactly once, never retried (R16)."""
        return await self._write("POST", route, client_id, bearer, params, json_body=json_body)

    async def put(
        self,
        route: Route,
        *,
        client_id: str,
        bearer: str,
        json_body: Any,
        params: Sequence[tuple[str, str]] = (),
    ) -> WriteResponse:
        """``PUT`` one Helix route. Dispatched exactly once, never retried (R16)."""
        return await self._write("PUT", route, client_id, bearer, params, json_body=json_body)

    async def patch(
        self,
        route: Route,
        *,
        client_id: str,
        bearer: str,
        json_body: Any,
        params: Sequence[tuple[str, str]] = (),
    ) -> WriteResponse:
        """``PATCH`` one Helix route. Dispatched exactly once, never retried (R16)."""
        return await self._write("PATCH", route, client_id, bearer, params, json_body=json_body)

    async def delete(
        self, route: Route, *, client_id: str, bearer: str, params: Sequence[tuple[str, str]] = ()
    ) -> WriteResponse:
        """``DELETE`` one Helix route. Dispatched exactly once, never retried (R16)."""
        return await self._write("DELETE", route, client_id, bearer, params, json_body=None)

    async def _write(
        self,
        method: Method,
        route: Route,
        client_id: str,
        bearer: str,
        params: Sequence[tuple[str, str]],
        *,
        json_body: Any,
    ) -> WriteResponse:
        """The single write path: no loop, and no retry helper reachable from here (R16)."""
        url = self._url(self._api_base, self._api_host, route.path, params)
        headers = self._headers(client_id, bearer)
        kind: TokenType = route.token_type
        lease = await self.limiter.acquire(kind, method, route.template, route.major)
        started = self._clock()
        try:
            try:
                status, resp_headers, raw = await self._send(
                    method, url, headers=headers, json_body=json_body
                )
            except _TooLarge:
                # The request left and Helix may have acted: undeterminable, never re-issued (R16).
                self._log(route, started, "too_large", token_type=kind, write=True, method=method)
                raise self._unknown_outcome(
                    "Twitch's answer to this write was larger than this server may read"
                ) from None
            except httpx.TransportError:
                self._log(
                    route, started, "transport_error", token_type=kind, write=True, method=method
                )
                raise self._unknown_outcome(
                    "The connection to Twitch broke before this write was answered"
                ) from None
            lease.learn(resp_headers)
            if status == 429:
                # A write is not replayed even after the bucket clears (R16, R22).
                self._apply_429(
                    route, lease, started, resp_headers, kind, write=True, method=method
                )
                # _apply_429 always raises: this path has no loop to return to.
            body = _parse_json(raw)
            if 200 <= status < 300:
                if body is None:
                    self._log(
                        route,
                        started,
                        "upstream_unexpected",
                        status=status,
                        token_type=kind,
                        write=True,
                        method=method,
                    )
                    raise self._unknown_outcome(
                        "Twitch accepted this write but its answer could not be read"
                    )
                self._log(
                    route,
                    started,
                    "ok",
                    status=status,
                    token_type=kind,
                    write=True,
                    method=method,
                )
                data = body.get("data") if isinstance(body, Mapping) else None
                return WriteResponse(status, data if isinstance(data, list) else [], "ok")
            self._map(method, route, status, resp_headers, body, kind, write=True)
            raise AssertionError("unreachable: the status ladder always raises")
        finally:
            lease.release()

    # ------------------------------------------------------------ shared mapping (R44)

    def _too_large_error(self) -> TwitchMcpError:
        return TwitchMcpError(
            "too_large",
            "Twitch's answer was larger than this server may read",
            hint="Lower the 'first' argument so the page fits TWITCH_MAX_RESPONSE_BYTES.",
        )

    def _unknown_outcome(self, message: str, *, http_status: int | None = None) -> TwitchMcpError:
        """R16: a write whose result cannot be determined. Check the channel, never re-issue.

        ``http_status`` is carried when Twitch did answer (R43 puts it in the error shape); it
        stays ``None`` when no response arrived at all, which is the genuinely unknown case.
        """
        return TwitchMcpError(
            "unknown_outcome",
            message,
            http_status=http_status,
            hint=(
                "Do not re-issue this call: it may already have taken effect. Check the channel "
                "in the Twitch console or chat, and re-issue only if the effect is absent."
            ),
        )

    def _map(
        self,
        method: Method,
        route: Route,
        status: int,
        headers: Mapping[str, str],
        body: Any,
        kind: TokenType,
        *,
        write: bool,
    ) -> Response:
        """Turn a non-success response into the one error its status and body justify (R44).

        Always raises — the declared return type exists so the read path stays typed. ``write``
        selects R16's ``unknown_outcome`` wherever the effect of a dispatched request is
        undeterminable, so it is never re-issued.
        """
        code = _error_code(body, status)

        def note(outcome: str, status_code: int | None = status) -> None:
            self._log(
                route, 0.0, outcome, status=status_code, token_type=kind, write=write, method=method
            )

        if status == 0:  # transport failure: no response at all
            note("transport_error", None)
            if write:
                raise self._unknown_outcome(
                    "The connection to Twitch broke before this write was answered"
                )
            raise TwitchMcpError("upstream", "Twitch is unreachable", retryable=True)

        if status in {301, 302, 303, 307, 308}:
            note("redirect")
            raise TwitchMcpError(
                "upstream",
                "Twitch answered with a redirect, which this server never follows",
                code=code,
                http_status=status,
            )

        if status == 401:
            # R24: the first 401 from Helix stops all further upstream traffic for the process.
            # Both credential classes share the one circuit, which only a restart clears.
            self.limiter.trip_auth(message="Twitch rejected this server's token")
            note("auth")
            scope_hint = _scope_phrase(body)
            if scope_hint is not None:
                raise self._permission_error(body, status, code, scope_hint)
            if write:
                raise self._unknown_outcome(
                    "Twitch rejected the credential while answering this write",
                    http_status=status,
                )
            raise TwitchMcpError(
                "unauthenticated",
                "Twitch rejected this server's token",
                code=code,
                http_status=status,
                hint=(
                    "Check the credentials in .env, re-authorize the account if needed, and "
                    "restart the container: this circuit is process-wide and never resets itself."
                ),
            )

        if status == 403:
            scope_hint = _scope_phrase(body)
            note("permission" if scope_hint else "forbidden")
            if scope_hint is not None:
                raise self._permission_error(body, status, code, scope_hint)
            if write:
                raise self._unknown_outcome("Twitch rejected this write without a readable answer")
            raise TwitchMcpError(
                "upstream", "Twitch denied this request", code=code, http_status=status
            )

        if status == 404:
            note("not_found")
            if write:
                raise self._unknown_outcome(
                    "Twitch reported no such target while answering this write"
                )
            # No allowlist clause in the hint: this branch is only reachable after the guard let the
            # id through, so the allowlist is provably not the cause and naming it would send the
            # operator to widen it for an object that simply does not exist (A2-D2/F1).
            raise TwitchMcpError(
                "not_found",
                "Twitch has no such object",
                code=code,
                http_status=status,
            )

        if status in {400, 422}:
            note("invalid_arguments")
            if write:
                raise self._unknown_outcome(
                    "Twitch rejected this write's arguments after the request was sent"
                )
            raise TwitchMcpError(
                "invalid_arguments",
                "Twitch rejected one of the arguments for this call",
                code=code,
                http_status=status,
                hint="Check the argument names, types and bounds in the tool's input schema.",
            )

        if status == 429:
            note("rate_limited")
            raise TwitchMcpError(
                "rate_limited",
                "Twitch rate-limited this route, so this request was not replayed",
                code=code,
                http_status=429,
                retryable=not write,
                retry_after=self._park_seconds(headers),
            )

        if status >= 500:
            note("upstream_5xx")
            if write:
                raise self._unknown_outcome(
                    "Twitch returned a server error while answering this write"
                )
            raise TwitchMcpError(
                "upstream",
                "Twitch returned a server error",
                code=code,
                http_status=status,
                retryable=True,
            )

        note("upstream_error")
        raise TwitchMcpError(
            "upstream", "Twitch returned an unexpected error", code=code, http_status=status
        )

    def _permission_error(
        self, body: Any, status: int, code: int | None, scope_hint: str
    ) -> TwitchMcpError:
        """R45: the hint carries Twitch's own scope words plus what the token actually holds.

        ``scope_hint`` is Twitch's own sentence, lifted from its structured ``error`` /
        ``error_description`` fields; ``scopes`` is the structured list of granted scopes when the
        body carries one. This method adds no scope name of its own (D10).
        """
        granted = body.get("scopes") if isinstance(body, Mapping) else None
        names = (
            sorted(str(scope) for scope in granted if isinstance(scope, str))
            if isinstance(granted, list)
            else []
        )
        holds = f"This token holds: {', '.join(names)}." if names else "This token holds no scopes."
        return TwitchMcpError(
            "permission",
            "This Twitch token is not authorized for that action",
            code=code,
            http_status=status,
            retryable=False,
            hint=(
                f"{scope_hint} {holds} Add that scope to TWITCH_OAUTH_SCOPES in .env, then sign "
                "in again with the connect_twitch_account tool to re-authorize."
            ),
        )

    # ------------------------------------------------------------ 429 handling (R21, R22)

    def _park_seconds(self, headers: Mapping[str, str]) -> float:
        """How long a 429 parks its bucket, taken from ``Ratelimit-Reset`` (R19), never invented.

        ``RateLimiter`` compares ``Ratelimit-Reset`` (absolute unix) against the monotonic timeline
        of its injected clock through its ``_epoch`` bridge, so the remaining wait is measured on
        exactly the same projection :meth:`~twitch_mcp.ratelimit.Lease.learn` applies.
        """
        reset = headers.get("ratelimit-reset")
        if reset is not None:
            try:
                value = float(reset)
            except ValueError:
                value = float("nan")
            if value == value and value > 0:  # NaN-safe
                projected = value - self.limiter._epoch - self._clock()
                if projected > 0:
                    return round(projected, 3)
        return DEFAULT_429_PARK_SECONDS

    def _apply_429(
        self,
        route: Route,
        lease: Lease,
        started: float,
        headers: Mapping[str, str],
        kind: TokenType,
        *,
        write: bool = False,
        method: Method = "GET",
    ) -> None:
        """Park the bucket and refuse. A 429 is never replayed (R22); a write never is (R16)."""
        lease.block(self._park_seconds(headers) + self._jitter)
        self._log(
            route, started, "rate_limited", status=429, token_type=kind, write=write, method=method
        )
        raise TwitchMcpError(
            "rate_limited",
            "Twitch rate-limited this route, so this request was not replayed",
            http_status=429,
            retryable=not write,
            retry_after=self._park_seconds(headers),
        )

    # ------------------------------------------------------------ OAuth endpoints (R17, R48-R51)

    async def post_form(
        self,
        path: str,
        form: Mapping[str, str],
        *,
        client_id: str | None = None,
        bearer: str | None = None,
    ) -> tuple[int, Any]:
        """POST a form to exactly one fixed ``id.twitch.tv/oauth2/*`` path. Never retried (R16).

        ``/oauth2/token`` and ``/oauth2/revoke`` carry their identifiers in the form body, so they
        may go without ``Client-Id``/``Authorization``: the token request carries the client id and
        secret, and revoke carries the client id and the token (W6). On ``/oauth2/token`` the
        exception applies only when the caller passes neither keyword. On ``/oauth2/revoke`` any
        keyword is refused before the socket, and a form without a non-empty ``client_id`` is
        refused too. Anything else still needs both headers or is refused (R13). No token, code or
        ``state`` value reaches a log (R52).
        """
        if path not in {OAUTH_TOKEN_PATH, OAUTH_REVOKE_PATH}:
            raise TwitchMcpError("upstream", "That OAuth path is not one this server may call")
        url = self._url(self._auth_base, self._auth_host, path, ())
        # The header exception is keyed on the exact path and, for the token request, on the
        # caller passing no keyword, so no other endpoint can borrow it and every other request
        # still needs both headers or is refused before the socket (R13). Revoke never carries
        # the headers: a keyword on that path is a caller bug, and a missing client id cannot be
        # sent (W6, R2-1).
        if path == OAUTH_REVOKE_PATH:
            if client_id is not None or bearer is not None:
                raise TwitchMcpError(
                    "unauthenticated", "Revoke is sent without Client-Id or Authorization"
                )
            if not form.get("client_id"):
                raise TwitchMcpError("unauthenticated", "Revoke needs the client id in the form")
            headers: dict[str, str] = {}
        elif client_id is None and bearer is None and path == OAUTH_TOKEN_PATH:
            headers = {}
        else:
            headers = self._headers(client_id, bearer)
        route = Route(template=path, path=path, major="")
        started = self._clock()
        lease = await self.limiter.acquire(APP_CLASS, "POST", path, "")
        try:
            try:
                status, resp_headers, raw = await self._send(
                    "POST", url, headers=headers, form=form
                )
            except _TooLarge:
                self._log(route, started, "too_large", token_type=APP_CLASS)
                raise TwitchMcpError(
                    "too_large", "Twitch's OAuth answer was larger than this server may read"
                ) from None
            except httpx.TransportError:
                self._log(route, started, "transport_error", token_type=APP_CLASS)
                raise TwitchMcpError(
                    "upstream", "Twitch's OAuth service is unreachable", retryable=True
                ) from None
            lease.learn(resp_headers)
            body = _parse_json(raw)
            if status == 429:
                self._apply_429(route, lease, started, resp_headers, APP_CLASS)
            if 200 <= status < 300:
                self._log(route, started, "ok", status=status, token_type=APP_CLASS)
                return status, body
            self._log(route, started, "oauth_error", status=status, token_type=APP_CLASS)
            raise self._oauth_error(status, body, path, form.get("grant_type"))
        finally:
            lease.release()

    def _oauth_error(self, status: int, body: Any, path: str, grant: str | None) -> TwitchMcpError:
        """An OAuth failure decided by status and path/grant (R44, W4, W12), never by its text.

        Every ``400`` is ``unauthenticated`` whatever its body: Twitch's documented 400 bodies carry
        no ``error`` code this server could key on. The hint is chosen by path first, then by the
        form's ``grant_type``; ``body`` is not read here, so no value reaches the message (R52).
        """
        del body  # decided by status, path and grant only (W4)
        if status == 400:
            if path == OAUTH_REVOKE_PATH:
                hint: str | None = None
            elif grant == "client_credentials":
                hint = "Check TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env and restart."
            elif grant in {"authorization_code", "refresh_token"}:
                hint = "Use the connect_twitch_account tool to re-authorize the Twitch account."
            else:
                hint = "Check the OAuth request and retry."
            return TwitchMcpError(
                "unauthenticated",
                "Twitch refused those OAuth credentials for this grant",
                http_status=status,
                hint=hint,
            )
        if status == 401:
            return TwitchMcpError(
                "unauthenticated",
                "Twitch rejected this server's credentials",
                http_status=status,
                hint="Check TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env and restart.",
            )
        if status >= 500:
            return TwitchMcpError(
                "upstream",
                "Twitch's OAuth service returned a server error",
                http_status=status,
                retryable=True,
            )
        return TwitchMcpError(
            "upstream", "Twitch's OAuth service rejected the request", http_status=status
        )

    async def validate_token(self, bearer: str, *, client_id: str | None = None) -> BootProbe:
        """R50 boot probe: ``GET /oauth2/validate``. Degrades — it never raises on a bad token.

        Returns the state plus the granted scope names and, for a user token, its ``user_id``
        (R29). No token material is logged or returned.
        """
        url = self._url(self._auth_base, self._auth_host, OAUTH_VALIDATE_PATH, ())
        headers = self._headers(client_id or self._client_id, bearer)
        route = Route(template=OAUTH_VALIDATE_PATH, path=OAUTH_VALIDATE_PATH, major="")
        started = self._clock()
        lease = await self.limiter.acquire(APP_CLASS, "GET", OAUTH_VALIDATE_PATH, "")
        try:
            try:
                status, resp_headers, raw = await self._send("GET", url, headers=headers)
            except (_TooLarge, httpx.TransportError):
                self._log(route, started, "unreachable", token_type=APP_CLASS)
                return BootProbe("unreachable")
            lease.learn(resp_headers)
            body = _parse_json(raw)
            if status == 401:
                # R50: an invalid token degrades. The API circuit opens only on a Helix 401 (R24).
                self._log(route, started, "auth", status=status, token_type=APP_CLASS)
                return BootProbe("unauthenticated")
            if status >= 500:
                self._log(route, started, "upstream_5xx", status=status, token_type=APP_CLASS)
                return BootProbe("unreachable")
            if not 200 <= status < 300 or not isinstance(body, Mapping):
                self._log(route, started, "oauth_error", status=status, token_type=APP_CLASS)
                return BootProbe("unreachable")
            scopes = body.get("scopes")
            granted = (
                tuple(str(scope) for scope in scopes if isinstance(scope, str))
                if isinstance(scopes, list)
                else ()
            )
            user = body.get("user_id")
            expires_in = body.get("expires_in")
            usable = isinstance(expires_in, int) and not isinstance(expires_in, bool)
            self._log(route, started, "ok", status=status, token_type=APP_CLASS)
            return BootProbe(
                "ok",
                scopes=granted,
                user_id=user if isinstance(user, str) and user else None,
                user_login=_field(body, "login"),
                expires_in=expires_in if usable else None,
            )
        finally:
            lease.release()

    async def fetch_validate(self, bearer: str, *, client_id: str | None = None) -> Response:
        """``GET /oauth2/validate`` for ``twitch_account_status`` — errors raise, no circuit."""
        url = self._url(self._auth_base, self._auth_host, OAUTH_VALIDATE_PATH, ())
        headers = self._headers(client_id or self._client_id, bearer)
        route = Route(template=OAUTH_VALIDATE_PATH, path=OAUTH_VALIDATE_PATH, major="")
        started = self._clock()
        lease = await self.limiter.acquire(APP_CLASS, "GET", OAUTH_VALIDATE_PATH, "")
        try:
            try:
                status, resp_headers, raw = await self._send("GET", url, headers=headers)
            except _TooLarge:
                self._log(route, started, "too_large", token_type=APP_CLASS)
                raise self._too_large_error() from None
            except httpx.TransportError:
                self._log(route, started, "transport_error", token_type=APP_CLASS)
                raise TwitchMcpError("upstream", "Twitch is unreachable", retryable=True) from None
            lease.learn(resp_headers)
            if 200 <= status < 300:
                body = _parse_json(raw)
                if body is not None:
                    self._log(route, started, "ok", status=status, token_type=APP_CLASS)
                    return Response(status, body, resp_headers)
            return self._map(
                "GET", route, status, resp_headers, _parse_json(raw), APP_CLASS, write=False
            )
        finally:
            lease.release()


__all__ = [
    "ALLOWED_METHODS",
    "WRITE_METHODS",
    "BootProbe",
    "InvalidArgument",
    "Response",
    "Route",
    "TwitchClient",
    "WriteResponse",
    "cursor",
    "integer",
    "login",
    "opaque",
    "path_segment",
    "repeat",
    "user_id",
]
