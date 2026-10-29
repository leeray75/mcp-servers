"""The ONLY module that imports httpx / touches the network (R4, R16-R19, R31).

Every request is a GET to ``DISCORD_API_BASE`` (host pinned, redirects never followed, body size
capped and streamed). Each request first acquires a rate-limit lease
(``ratelimit.py``). The lease is
released in a ``finally`` on every path, including cancellation (review M1). Status and Discord's
numeric error code decide the outcome; message text is never parsed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal
from urllib.parse import quote, urlencode, urlsplit

import httpx

from discord_mcp.config import Settings
from discord_mcp.errors import DiscordMcpError
from discord_mcp.oauth import CONNECT_HINT, UserAuth
from discord_mcp.ratelimit import Clock, Lease, RateLimiter, Sleep

logger = logging.getLogger(__name__)

PERMISSION_CODES: Final = frozenset({50001, 50013})
NOT_FOUND_CODES: Final = frozenset({10003, 10004, 10007, 10008, 10013, 10014})
SEARCH_INDEX_CODE: Final = 110000
GET_RETRIES: Final = 2
TRANSPORT_BACKOFF: Final = (0.5, 1.0)
PROBE_BACKOFF: Final = (1.0, 2.0, 4.0)
DEFAULT_RETRY_AFTER: Final = 1.0
SIZE_CAP_HINT: Final = (
    "Retry with a smaller limit (the tool's limit argument) so the page fits the response cap."
)
_FLAGS_NEW_RE: Final = re.compile(r"[0-9]{1,30}")
ME_PATH: Final = "/users/@me"
APPLICATION_PATH: Final = "/applications/@me"


@dataclass(frozen=True, slots=True)
class Route:
    """One Discord route: the template (for limits and logs) and the concrete path."""

    template: str
    path: str
    major: str = ""

    @staticmethod
    def build(template: str, major: str = "", **values: str) -> Route:
        """Fill ``{name}`` placeholders. Values must already be validated by the caller (R17)."""
        path = template
        for name, value in values.items():
            path = path.replace("{" + name + "}", quote(value, safe=""))
        return Route(template=template, path=path, major=major)


@dataclass(frozen=True, slots=True)
class Response:
    """A successful (2xx) Discord response with its decoded JSON body."""

    status: int
    body: Any


@dataclass(frozen=True, slots=True)
class BootProbe:
    """Outcome of the boot probe (R4-R6, A1-R6)."""

    outcome: Literal["ok", "auth", "unreachable"]
    bot_user_id: str | None = None
    flags: int | None = None
    flags_source: str | None = None
    application_id: str | None = None


class _TooLarge(Exception):
    """Internal: the streamed body exceeded DISCORD_MAX_RESPONSE_BYTES."""


def _parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


def _code_of(body: Any) -> int | None:
    if isinstance(body, dict) and isinstance(body.get("code"), int):
        return int(body["code"])
    return None


def _float_or_none(value: Any) -> float | None:
    """A finite, non-negative number from a JSON number or string; anything else is None (m4)."""
    if isinstance(value, bool):
        return None
    number: float | None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _retry_after_seconds(body: Any, headers: Mapping[str, str]) -> float:
    """``retry_after`` from the JSON body, else the ``Retry-After`` header, else a default."""
    value = _float_or_none(body.get("retry_after")) if isinstance(body, dict) else None
    if value is None:
        value = _float_or_none(headers.get("retry-after"))
    return value if value is not None else DEFAULT_RETRY_AFTER


def _scope_of(body: Any, scope: str | None) -> str:
    """``global`` from the body flag or scope header, ``shared``, else ``user`` (R22)."""
    if (isinstance(body, dict) and body.get("global") is True) or scope == "global":
        return "global"
    if scope == "shared":
        return "shared"
    return "user"


def _usable_flags(value: Any, *, accept_string: bool) -> int | None:
    """One application flags field as a non-negative integer, or None when unusable (A2-R6a).

    ``flags`` is usable only as a JSON integer. ``flags_new`` is also usable as a string of
    1-30 ASCII digits (bigint-safe). Anything else, including booleans, floats, lists, negatives
    and ``"1e3"``, ``"0x10"``, ``" 12"``, ``"\u00b2"``, is ignored. This function never raises.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if accept_string and isinstance(value, str) and _FLAGS_NEW_RE.fullmatch(value):
        return int(value)
    return None


def _application_flags(app: Mapping[str, Any]) -> tuple[int | None, str | None]:
    """Effective intent flags and their source (A2-R6a, A2-R7a).

    Each field is parsed independently. The effective flags are the bitwise OR of every usable
    field. The source is ``flags``, ``flags_new``, ``flags+flags_new`` (both usable) or ``None``.
    """
    own = _usable_flags(app.get("flags"), accept_string=False)
    new = _usable_flags(app.get("flags_new"), accept_string=True)
    if own is not None and new is not None:
        return own | new, "flags+flags_new"
    if own is not None:
        return own, "flags"
    if new is not None:
        return new, "flags_new"
    return None, None


def oauth_urls(api_base: str) -> tuple[str, str]:
    """The only two URLs that may ever receive a POST (U22): token and revoke, same origin."""
    parts = urlsplit(api_base)
    origin = f"{parts.scheme}://{parts.netloc}"
    return f"{origin}/api/oauth2/token", f"{origin}/api/oauth2/token/revoke"


class DiscordClient:
    """Thin async client for Discord REST API v10 with rate limiting and error mapping.

    GET is the only method for bot calls. The user-side instance (``user_auth`` given) also POSTs,
    but only through :meth:`post_form`, to the two fixed OAuth URLs.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        rand: Callable[[], float] = random.random,
        user_auth: UserAuth | None = None,
    ) -> None:
        """Bot client by default. With ``user_auth`` this is the user-side client (A3, U23): it
        sends ``Authorization: Bearer`` per request, has its own rate limiter and hard-stop state,
        and is the only client allowed to POST to the two fixed OAuth URLs (U22)."""
        self._base = settings.discord_api_base
        self._host = settings.api_host
        self._max_bytes = settings.discord_max_response_bytes
        self._max_wait = settings.discord_max_rate_limit_wait_seconds
        self._max_429 = settings.discord_max_429_retries
        self._clock = clock
        self._sleep_fn = sleep
        self._user = user_auth
        self._token_url, self._revoke_url = oauth_urls(settings.discord_api_base)
        self._oauth_id = settings.discord_application_id or ""
        secret = settings.discord_oauth_client_secret
        self._oauth_secret = secret.get_secret_value() if secret is not None else ""
        self.limiter = RateLimiter(
            max_wait_seconds=settings.discord_max_rate_limit_wait_seconds,
            invalid_budget=settings.discord_invalid_request_budget,
            global_rps=settings.discord_global_requests_per_second,
            clock=clock,
            sleep=sleep,
            rand=rand,
        )
        timeout = settings.discord_timeout_seconds
        headers = {"User-Agent": settings.discord_user_agent, "Accept": "application/json"}
        if user_auth is None:
            headers["Authorization"] = f"Bot {settings.discord_bot_token.get_secret_value()}"
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            follow_redirects=False,
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def auth_failed(self) -> bool:
        return self.limiter.auth_failed

    # ------------------------------------------------------------ transport

    def _url(self, path: str, params: Sequence[tuple[str, str]]) -> str:
        query = urlencode(list(params))
        url = f"{self._base}{path}" + (f"?{query}" if query else "")
        if urlsplit(url).netloc.lower() != self._host:
            raise DiscordMcpError("upstream", "Discord request host is not the configured host")
        return url

    async def _send(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
        auth: httpx.Auth | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        async with self._http.stream(
            method, url, headers=headers, data=form, auth=auth
        ) as response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > self._max_bytes:
                    raise _TooLarge()
            return response.status_code, headers, bytes(body)

    def _log(
        self,
        route: Route,
        started: float,
        outcome: str,
        status: int | None = None,
        scope: str | None = None,
    ) -> None:
        logger.info(
            "discord request",
            extra={
                "route": route.template,
                "status": status,
                "scope": scope,
                "duration_ms": round((self._clock() - started) * 1000, 1),
                "outcome": outcome,
            },
        )

    # ------------------------------------------------------------ requests

    async def get(
        self,
        route: Route,
        params: Sequence[tuple[str, str]] = (),
        *,
        access_token: str | None = None,
    ) -> Response:
        """GET one route. Raises :class:`DiscordMcpError` for every failure (R30-R31).

        The bucket lease is released in ``finally`` on every path, including cancellation, so a
        cancelled or crashed request can never wedge its bucket (review M1). On the user-side
        client the Bearer token is resolved before the lease is taken; a user-token 401 gets one
        serialized refresh and one replay (U24-U25). ``access_token`` is only used by the connect
        flow, before any token is stored, and is never refreshed.
        """
        url = self._url(route.path, params)
        retries = 0  # transport errors and 5xx share the GET retry budget (R18)
        replays = 0  # 429 replays (R22)
        user_replays = 0  # user-token 401 replays (U24): at most one
        bearer = await self._bearer(access_token)
        while True:
            lease = await self.limiter.acquire("GET", route.template, route.major)
            started = self._clock()
            try:
                try:
                    status, headers, raw = await self._send(
                        url, headers=self._request_headers(bearer)
                    )
                except _TooLarge:
                    self._log(route, started, "upstream_response_too_large")
                    raise DiscordMcpError(
                        "upstream",
                        "Discord response exceeded the configured size cap",
                        hint=SIZE_CAP_HINT,
                    ) from None
                except httpx.TransportError as exc:
                    if retries < GET_RETRIES:
                        await self._sleep_and_retry(lease, retries)
                        retries += 1
                        continue
                    self._log(route, started, "transport_error")
                    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
                        raise DiscordMcpError(
                            "unavailable", "Discord is unreachable", retryable=True
                        ) from None
                    raise DiscordMcpError(
                        "upstream", "Discord did not answer in time", retryable=True
                    ) from None
                except httpx.RequestError:
                    # DecodingError and other non-transport request errors (review M1).
                    self._log(route, started, "upstream_request_error")
                    raise DiscordMcpError(
                        "upstream", "Discord response could not be read"
                    ) from None

                lease.learn(headers)
                body = _parse_json(raw)
                code = _code_of(body)
                if status == 429:
                    # The 429 is decided (and its bucket blocked) while the lease is still held.
                    replay = self._rate_limited(route, lease, started, body, headers, replays)
                    lease.release()
                    if replay:
                        replays += 1
                        continue
                    raise DiscordMcpError(
                        "rate_limited",
                        "Discord rate limit reached for this route",
                        code=code,
                        http_status=429,
                        retryable=True,
                        retry_after=round(_retry_after_seconds(body, headers), 3),
                    )
                lease.release()
                if (
                    status == 401
                    and self._user is not None
                    and access_token is None
                    and user_replays == 0
                ):
                    # U24: one serialized refresh (U25), then exactly one replay of this GET.
                    user_replays += 1
                    bearer = await self._user.access_token(stale=bearer)
                    self._log(route, started, "user_token_refreshed", status, "user")
                    continue
                if status >= 500 and retries < GET_RETRIES:
                    self._log(route, started, "upstream_5xx_retry", status)
                    await self._sleep_fn(TRANSPORT_BACKOFF[retries])
                    retries += 1
                    continue
                return self._finish(route, started, status, body, code, headers)
            finally:
                lease.release()  # idempotent: the normal paths release earlier

    async def _bearer(self, explicit: str | None) -> str | None:
        """The Bearer token for one request: the explicit connect token, the user token, or None
        for the bot client, which sends its own header (U23)."""
        if self._user is None:
            return None
        if explicit is not None:
            return explicit
        return await self._user.access_token()

    @staticmethod
    def _request_headers(bearer: str | None) -> dict[str, str] | None:
        return None if bearer is None else {"Authorization": f"Bearer {bearer}"}

    async def post_form(self, kind: Literal["token", "revoke"], form: Mapping[str, str]) -> Any:
        """POST a form to one of the two OAuth URLs (U22, U24). The only POST in the server.

        Basic auth with the application id and client secret. Redirects are never followed and
        the size cap applies. POSTs are never retried, so no code or refresh token is replayed;
        only a 429 is waited out and retried under the normal rules. Returns the JSON object of a
        2xx response. Errors: ``invalid_grant`` (400) is ``auth``; 5xx and transport errors are
        ``unavailable``; other 4xx are ``upstream``. Only the OAuth error *code* is read.
        """
        if self._user is None:
            raise DiscordMcpError("upstream", "The OAuth exchange is not configured")
        url = self._token_url if kind == "token" else self._revoke_url
        if urlsplit(url).netloc.lower() != self._host:
            raise DiscordMcpError("upstream", "Discord request host is not the configured host")
        route = Route.build(f"/oauth2/{kind}" if kind == "token" else "/oauth2/token/revoke")
        basic = httpx.BasicAuth(self._oauth_id, self._oauth_secret)
        replays = 0
        while True:
            lease = await self.limiter.acquire("POST", route.template, "")
            started = self._clock()
            try:
                try:
                    status, headers, raw = await self._send(
                        url, method="POST", form=dict(form), auth=basic
                    )
                except _TooLarge:
                    self._log(route, started, "upstream_response_too_large")
                    raise DiscordMcpError(
                        "upstream",
                        "Discord response exceeded the configured size cap",
                        hint=SIZE_CAP_HINT,
                    ) from None
                except httpx.TransportError:
                    self._log(route, started, "transport_error")
                    raise DiscordMcpError(
                        "unavailable", "Discord is unreachable", retryable=True
                    ) from None
                except httpx.RequestError:
                    self._log(route, started, "upstream_request_error")
                    raise DiscordMcpError(
                        "upstream", "Discord response could not be read"
                    ) from None

                lease.learn(headers)
                body = _parse_json(raw)
                if status == 429:
                    replay = self._rate_limited(route, lease, started, body, headers, replays)
                    lease.release()
                    if replay:
                        replays += 1
                        continue
                    raise DiscordMcpError(
                        "rate_limited",
                        "Discord rate limit reached for this route",
                        http_status=429,
                        retryable=True,
                        retry_after=round(_retry_after_seconds(body, headers), 3),
                    )
                lease.release()
                if 200 <= status < 300:
                    if kind == "revoke":
                        # Discord answers a successful revoke with an empty body (U18).
                        self._log(route, started, "ok", status, "user")
                        return {}
                    if not isinstance(body, dict):
                        self._log(route, started, "upstream_unexpected", status)
                        raise DiscordMcpError("upstream", "Discord returned an unexpected response")
                    self._log(route, started, "ok", status, "user")
                    return body
                error = body.get("error") if isinstance(body, dict) else None
                if status == 400 and error == "invalid_grant":
                    self._log(route, started, "auth", status, "user")
                    raise DiscordMcpError(
                        "auth",
                        "Discord refused the stored grant",
                        http_status=status,
                        hint=CONNECT_HINT,
                    )
                if status >= 500:
                    self._log(route, started, "upstream_5xx", status, "user")
                    raise DiscordMcpError(
                        "unavailable",
                        "Discord returned a server error",
                        http_status=status,
                        retryable=True,
                    )
                self._log(route, started, "upstream_error", status, "user")
                raise DiscordMcpError(
                    "upstream", "Discord rejected the OAuth request", http_status=status
                )
            finally:
                lease.release()

    async def _sleep_and_retry(self, lease: Lease, retries: int) -> None:
        """Release the lease before a transport backoff so other requests are not blocked."""
        lease.release()
        await self._sleep_fn(TRANSPORT_BACKOFF[retries])

    def _finish(
        self,
        route: Route,
        started: float,
        status: int,
        body: Any,
        code: int | None,
        headers: Mapping[str, str],
    ) -> Response:
        """Turn a non-429 response into a success or the R31 error for its status and code."""
        if status == 202 and code == SEARCH_INDEX_CODE:  # checked before the 2xx success path
            docs = body.get("documents_indexed") if isinstance(body, dict) else None
            wait = _retry_after_seconds(body, headers)
            self._log(route, started, "index_pending", status)
            raise DiscordMcpError(
                "index_pending",
                f"The search index is not ready yet (documents_indexed={docs})",
                code=code,
                http_status=status,
                retryable=True,
                retry_after=round(wait, 3),
            )

        if 200 <= status < 300:
            if body is None:
                self._log(route, started, "upstream_unexpected", status)
                raise DiscordMcpError("upstream", "Discord returned an unexpected response")
            self._log(route, started, "ok", status)
            return Response(status, body)

        if 300 <= status < 400:
            self._log(route, started, "upstream_redirect", status)
            raise DiscordMcpError(
                "upstream",
                "Discord answered with a redirect, which is not followed",
                http_status=status,
            )

        if status == 401 and self._user is not None:
            # U23: a user-token 401 never sets the bot hard-stop; it is the user's account state.
            self.limiter.record_invalid()
            self._log(route, started, "auth", status, "user")
            raise DiscordMcpError(
                "auth",
                "The Discord account token was rejected",
                code=code,
                http_status=status,
                hint=CONNECT_HINT,
            )

        if status == 401:
            self.limiter.trip_auth()
            self.limiter.record_invalid()
            self._log(route, started, "auth", status)
            raise DiscordMcpError(
                "auth", "The bot token was rejected by Discord", code=code, http_status=status
            )

        if status == 403 or code in PERMISSION_CODES:
            self.limiter.record_invalid()
            self._log(route, started, "permission", status)
            raise DiscordMcpError(
                "permission",
                "Discord denied access to this resource",
                code=code,
                http_status=status,
            )

        if status == 404 or code in NOT_FOUND_CODES:
            self._log(route, started, "not_found", status)
            raise DiscordMcpError(
                "not_found",
                "Discord reports no such object for the bot",
                code=code,
                http_status=status,
            )

        if status == 400:
            self._log(route, started, "validation", status)
            raise DiscordMcpError(
                "validation",
                "Discord rejected the request as malformed",
                code=code,
                http_status=status,
            )

        if status >= 500:
            self._log(route, started, "upstream_5xx", status)
            raise DiscordMcpError(
                "upstream",
                "Discord returned a server error",
                code=code,
                http_status=status,
                retryable=True,
            )

        self._log(route, started, "upstream_error", status)
        raise DiscordMcpError(
            "upstream", "Discord returned an unexpected error", code=code, http_status=status
        )

    def _rate_limited(
        self,
        route: Route,
        lease: Lease,
        started: float,
        body: Any,
        headers: Mapping[str, str],
        replays: int,
    ) -> bool:
        """Apply a 429 (R22). Returns True when the request should be replayed in this call."""
        retry_after = _retry_after_seconds(body, headers)
        scope = _scope_of(body, headers.get("x-ratelimit-scope"))
        if scope == "global":
            self.limiter.pause_global(retry_after)
            self.limiter.record_invalid()
        elif scope == "shared":
            # Shared: block the bucket, never replay in this call, never count as invalid.
            lease.block(retry_after)
        else:
            lease.block(retry_after + self.limiter.jitter())
            self.limiter.record_invalid()
        replay = scope != "shared" and retry_after <= self._max_wait and replays < self._max_429
        self._log(route, started, "rate_limited_replay" if replay else "rate_limited", 429, scope)
        return replay

    # ------------------------------------------------------------ boot probe

    async def _probe_call(self, path: str) -> tuple[str, Any]:
        route = Route.build(path)
        for attempt in range(len(PROBE_BACKOFF) + 1):
            try:
                response = await self.get(route)
            except DiscordMcpError as exc:
                if exc.kind == "auth":
                    return "auth", None
                if exc.kind not in {"unavailable", "upstream", "rate_limited"}:
                    return "unreachable", None
            else:
                return "ok", response.body
            if attempt < len(PROBE_BACKOFF):
                await self._sleep_fn(PROBE_BACKOFF[attempt])
        return "unreachable", None

    async def probe(self) -> BootProbe:
        """Boot probe: bot user id and intent flags (R4-R5, A1-R6).

        A probe whose application body has no usable flags is still ``ok`` with ``flags=None``:
        the intent state is then unknown and the server applies ``flags_missing`` (A1-R6).
        """
        user_state, user = await self._probe_call(ME_PATH)
        if user_state == "auth":
            return BootProbe("auth")
        if user_state != "ok" or not isinstance(user, dict) or not isinstance(user.get("id"), str):
            return BootProbe("unreachable")
        app_state, app = await self._probe_call(APPLICATION_PATH)
        if app_state == "auth":
            return BootProbe("auth")
        if app_state != "ok" or not isinstance(app, dict):
            return BootProbe("unreachable")
        flags, source = _application_flags(app)
        app_id = app.get("id")
        return BootProbe(
            "ok",
            bot_user_id=str(user["id"]),
            flags=flags,
            flags_source=source,
            application_id=str(app_id) if isinstance(app_id, str) else None,
        )
