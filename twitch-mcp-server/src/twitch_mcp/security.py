"""ASGI middleware: DNS-rebinding (Host/Origin) guard and bearer-token authentication.

Adapted from ``../discord-mcp-server``. Both are pure ASGI middleware so they wrap the MCP transport
without touching its streaming responses. Rejections carry no detail about which check failed.

The bearer token covers every route except ``GET /healthz`` (R4). When user OAuth is enabled, the
two ``/oauth/*`` paths are additionally answered without a bearer token (R47) and only for ``GET``;
the Host/Origin guard still applies to them (R5), which is what makes the exemption safe alongside
the loopback bind, the single-use ``state``, the ``iss`` check and the 10-minute flow TTL.

The Origin check accepts both ``http`` and ``https`` unconditionally, so turning TLS on needs no
change there: a loopback browser arriving over either scheme is equally loopback (T7). There is no
plaintext-refusal middleware (S7); uvicorn refuses plaintext at the TLS handshake.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Collection
from typing import Final
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

HEALTH_PATH: Final = "/healthz"
OAUTH_START_PATH: Final = "/oauth/start"
OAUTH_CALLBACK_PATH: Final = "/oauth/callback"
#: The two paths that may be exempted from bearer auth, and only when user OAuth is enabled (R47).
OAUTH_PATHS: Final[frozenset[str]] = frozenset({OAUTH_START_PATH, OAUTH_CALLBACK_PATH})
LOOPBACK_HOSTNAMES: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost", "::1"})
_BEARER_PREFIX: Final = "bearer "


def _host_allowed(host_header: str | None, allowed: Collection[str]) -> bool:
    """Accept ``<allowed-hostname>:<port>`` only.

    Any numeric port is accepted: the container sees the *published* port, which differs from
    its own
    listening port. A DNS-rebinding attacker cannot choose the hostname, only the port, so the
    hostname allow-list is what matters.
    """
    if not host_header:
        return False
    value = host_header.strip()
    if value.startswith("["):
        # Bracketed IPv6 literal: [::1] or [::1]:8770.
        closing = value.find("]")
        if closing < 0:
            return False
        hostname = value[1:closing]
        remainder = value[closing + 1 :]
        if remainder == "":
            return hostname.lower() in allowed
        if remainder.startswith(":") and remainder[1:].isascii() and remainder[1:].isdigit():
            return hostname.lower() in allowed
        return False
    hostname, sep, port = value.rpartition(":")
    if not sep:
        # No port: accept a bare loopback hostname, which is what a local curl sends.
        return value.lower() in allowed
    # Unbracketed and contains ':'. An unbracketed IPv6 (``::1``) has no separable numeric port and
    # is
    # ambiguous against the allowlist, so it is rejected rather than guessed; a real ``host:port``
    # has
    # a pure-numeric port and no further ':' in the hostname.
    if ":" in hostname:
        return False
    return hostname.lower() in allowed and port.isascii() and port.isdigit()


def _origin_allowed(origin: str | None, allowed: Collection[str]) -> bool:
    if origin is None:
        return True  # non-browser clients (such as MCP desktop agents) send no Origin
    try:
        parts = urlsplit(origin)
        hostname = parts.hostname
    except ValueError:
        return False
    if hostname is None or parts.scheme not in {"http", "https"}:
        return False
    return hostname.lower().strip("[]") in allowed


class HostGuardMiddleware:
    """Reject requests whose Host (or Origin, if present) is not loopback (R5)."""

    def __init__(self, app: ASGIApp, allowed_hostnames: Collection[str] = LOOPBACK_HOSTNAMES):
        self.app = app
        self.allowed = frozenset(h.lower().strip("[]") for h in allowed_hostnames)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if not _host_allowed(headers.get("host"), self.allowed):
            logger.warning("rejected request", extra={"status": 421, "outcome": "bad_host"})
            await PlainTextResponse("Misdirected Request", status_code=421)(scope, receive, send)
            return
        if not _origin_allowed(headers.get("origin"), self.allowed):
            logger.warning("rejected request", extra={"status": 403, "outcome": "bad_origin"})
            await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


class BearerAuthMiddleware:
    """Require ``Authorization: Bearer <MCP_AUTH_TOKEN>`` on every route except ``GET /healthz``.

    ``open_paths`` are additional paths answered without a bearer token, for ``GET`` only
    (R47). The caller passes the two ``/oauth/*`` routes here only when user OAuth is on; with
    user OAuth off the
    default is empty and no route other than ``/healthz`` is ever reachable unauthenticated.
    """

    def __init__(self, app: ASGIApp, token: str, open_paths: Collection[str] = frozenset()) -> None:
        self.app = app
        self._token = token.encode("utf-8")
        self._open_paths = frozenset(open_paths)

    def _authorised(self, scope: Scope) -> bool:
        header = Headers(scope=scope).get("authorization", "")
        presented = (
            header[len(_BEARER_PREFIX) :] if header.lower().startswith(_BEARER_PREFIX) else ""
        )
        # Always run the constant-time comparison, even when the header is absent or malformed, so
        # every rejection takes the same code path.
        match = hmac.compare_digest(presented.strip().encode("utf-8"), self._token)
        return match and bool(presented)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self.app(scope, receive, send)
            return
        if kind != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        method = scope.get("method", "")
        path = scope.get("path", "")
        if path == HEALTH_PATH and method in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        if method == "GET" and path in self._open_paths:
            await self.app(scope, receive, send)
            return
        if not self._authorised(scope):
            logger.warning("rejected request", extra={"status": 401, "outcome": "unauthorized"})
            response = JSONResponse(
                {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
