"""Direct ASGI-level tests for paths the HTTP client tests cannot reach."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from gmail_mcp.security import BearerAuthMiddleware, HostGuardMiddleware, _origin_allowed


class Recorder:
    def __init__(self) -> None:
        self.scopes: list[str] = []
        self.sent: list[Message] = []

    async def app(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scopes.append(scope["type"])

    async def send(self, message: Message) -> None:
        self.sent.append(message)


async def _receive() -> Message:
    return {"type": "websocket.connect"}


async def test_websocket_connections_are_refused() -> None:
    rec = Recorder()
    middleware = BearerAuthMiddleware(rec.app, token="t" * 40)
    await middleware({"type": "websocket", "path": "/mcp"}, _receive, rec.send)
    assert rec.sent == [{"type": "websocket.close", "code": 1008}]
    assert rec.scopes == []


@pytest.mark.parametrize("middleware_cls", ["bearer", "host"])
async def test_lifespan_scope_passes_through(middleware_cls: str) -> None:
    rec = Recorder()
    middleware: Any = (
        BearerAuthMiddleware(rec.app, token="t" * 40)
        if middleware_cls == "bearer"
        else HostGuardMiddleware(rec.app)
    )
    await middleware({"type": "lifespan"}, _receive, rec.send)
    assert rec.scopes == ["lifespan"]


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (None, True),
        ("http://127.0.0.1:3000", True),
        ("https://localhost", True),
        ("http://evil.example.com", False),
        ("null", False),
        ("ftp://localhost", False),
        ("http://[::1", False),
        ("http://localhost.evil.com", False),
    ],
)
def test_origin_allowed(origin: str | None, expected: bool) -> None:
    assert _origin_allowed(origin, {"127.0.0.1", "localhost"}) is expected
