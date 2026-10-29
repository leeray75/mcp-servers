"""ASGI middleware: DNS-rebinding (Host/Origin) guard and bearer-token authentication.

Both are pure ASGI middleware so they wrap the MCP transport without touching
its streaming responses. Rejections carry no detail about which check failed.
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
LOOPBACK_HOSTNAMES: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost"})
_BEARER_PREFIX: Final = "bearer "


def _host_allowed(host_header: str | None, allowed: Collection[str]) -> bool:
    """Accept ``<allowed-hostname>:<port>`` only.

    Any numeric port is accepted: the container sees the *published* port, which
    differs from its own listening port. A DNS-rebinding attacker cannot choose
    the hostname, only the port, so the hostname allow-list is what matters.
    """
    if not host_header:
        return False
    hostname, sep, port = host_header.rpartition(":")
    return bool(sep) and hostname.lower() in allowed and port.isascii() and port.isdigit()


def _origin_allowed(origin: str | None, allowed: Collection[str]) -> bool:
    if origin is None:
        return True  # non-browser clients (such as MCP desktop agents) send no Origin
    try:
        parts = urlsplit(origin)
        hostname = parts.hostname
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and hostname is not None and hostname in allowed


class HostGuardMiddleware:
    """Reject requests whose Host (or Origin, if present) is not loopback."""

    def __init__(self, app: ASGIApp, allowed_hostnames: Collection[str] = LOOPBACK_HOSTNAMES):
        self.app = app
        self.allowed = frozenset(h.lower() for h in allowed_hostnames)

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
    """Require ``Authorization: Bearer <token>`` on every route except ``GET /healthz``.

    ``open_paths`` are additional paths answered without a bearer token, for ``GET`` only (U15).
    The caller passes the two OAuth routes here only when user OAuth is on; the Host and Origin
    guard still applies to them.
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
        # Always run the constant-time comparison, even when the header is absent
        # or malformed, so every rejection takes the same code path.
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
        if scope["path"] == HEALTH_PATH and scope["method"] in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        if scope["method"] == "GET" and scope["path"] in self._open_paths:
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
