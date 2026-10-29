"""R32 (no secrets or content in logs or errors), R38 (middleware and redacting logger)."""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from discord_mcp.logging_setup import (
    REDACTED,
    JsonFormatter,
    RedactionFilter,
    configure_logging,
    redact,
    register_secret,
)
from discord_mcp.security import BearerAuthMiddleware, HostGuardMiddleware
from tests.conftest import (
    BOT,
    FAKE_AUTH_TOKEN,
    FAKE_BOT_TOKEN,
    GUILD,
    TEXT,
    FakeClock,
    FakeDiscord,
    make_settings,
)
from tests.test_server import boot_server

# ------------------------------------------------------------------ redaction


def test_registered_secrets_are_scrubbed_everywhere() -> None:
    register_secret(FAKE_BOT_TOKEN)
    text = f"token={FAKE_BOT_TOKEN} and header Bot {FAKE_BOT_TOKEN}"
    assert FAKE_BOT_TOKEN not in redact(text)
    assert REDACTED in redact(text)


def test_authorization_and_token_shapes_are_scrubbed() -> None:
    samples = [
        "Authorization: Bot MTAAAAAAAAAAAAAAAAAAAAAA.abcd.ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ",
        "Bearer abcdefghijklmnopqrstuvwxyz",
        # Assembled at runtime so secret scanners don't flag this synthetic token-shaped value.
        ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4", "GhIjKl", "abcdefghijklmnopqrstuvwxyz0123"]),
    ]
    for sample in samples:
        assert REDACTED in redact(sample)


def test_empty_secret_registration_is_ignored() -> None:
    register_secret("")
    assert redact("plain text") == "plain text"


def test_redaction_filter_scrubs_message_and_extra_fields() -> None:
    register_secret(FAKE_AUTH_TOKEN)
    record = logging.makeLogRecord(
        {
            "name": "x",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "pathname": __file__,
            "lineno": 1,
            "msg": f"msg {FAKE_AUTH_TOKEN}",
            "args": (),
            "route": f"GET /x/{FAKE_AUTH_TOKEN}",
        }
    )
    RedactionFilter().filter(record)
    assert FAKE_AUTH_TOKEN not in record.getMessage()
    assert FAKE_AUTH_TOKEN not in str(record.__dict__["route"])


def test_json_formatter_emits_only_the_allowed_fields() -> None:
    record = logging.makeLogRecord(
        {
            "name": "discord_mcp",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "pathname": __file__,
            "lineno": 1,
            "msg": "discord request",
            "args": (),
            "route": "GET /channels/{channel_id}/messages",
            "status": 200,
            "secret_field": "must-not-appear",
        }
    )
    line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["route"] == "GET /channels/{channel_id}/messages"
    assert payload["status"] == 200
    assert "secret_field" not in payload


def test_exception_logging_records_type_only() -> None:
    try:
        raise ValueError("payload with message content")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert payload["exception"] == "ValueError"
    assert "message content" not in json.dumps(payload)


def test_configure_logging_installs_one_json_handler() -> None:
    buffer = io.StringIO()
    configure_logging("DEBUG", stream=buffer)
    logging.getLogger("discord_mcp.test").info("hello", extra={"outcome": "ok"})
    line = buffer.getvalue().strip().splitlines()[-1]
    assert json.loads(line)["outcome"] == "ok"
    assert logging.getLogger("httpx").level == logging.WARNING


# ------------------------------------------------------------------ logs during a real tool call


async def test_tool_call_logs_hold_no_token_url_or_response_body(
    fake: FakeDiscord, fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    from mcp import Client

    settings = make_settings()
    server, _svc, _plan = await boot_server(settings, fake, fclock)
    buffer = io.StringIO()
    configure_logging("DEBUG", stream=buffer)
    async with Client(server) as client:
        await client.call_tool("list_messages", {"channel_id": TEXT, "limit": 2})
        await client.call_tool("get_guild", {"guild_id": GUILD})
    output = buffer.getvalue() + "\n".join(r.getMessage() for r in caplog.records)
    assert FAKE_AUTH_TOKEN not in output
    assert FAKE_BOT_TOKEN not in output
    assert "discord.com" not in output and "https://" not in output
    assert "Release notes are in the thread" not in output  # message content never logged
    assert "tool call" in output and "discord request" in output


async def test_refused_call_logs_do_not_echo_the_refused_id(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    from mcp import Client

    server, _svc, _plan = await boot_server(make_settings(), fake, fclock)
    buffer = io.StringIO()
    configure_logging("DEBUG", stream=buffer)
    async with Client(server) as client:
        result = await client.call_tool("get_guild", {"guild_id": "777777777777777777"})
    assert result.is_error is True
    assert "777777777777777777" not in buffer.getvalue()
    assert BOT not in buffer.getvalue()


# ------------------------------------------------------------------ ASGI middleware (R38)


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


async def test_websocket_is_refused() -> None:
    rec = Recorder()
    await BearerAuthMiddleware(rec.app, token="t" * 40)(
        {"type": "websocket", "path": "/mcp"}, _receive, rec.send
    )
    assert rec.sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.parametrize("cls", ["bearer", "host"])
async def test_lifespan_passes_through(cls: str) -> None:
    rec = Recorder()
    middleware: Any = (
        BearerAuthMiddleware(rec.app, token="t" * 40)
        if cls == "bearer"
        else HostGuardMiddleware(rec.app)
    )
    await middleware({"type": "lifespan"}, _receive, rec.send)
    assert rec.scopes == ["lifespan"]


async def test_healthz_skips_bearer_but_other_paths_require_it() -> None:
    rec = Recorder()
    middleware = BearerAuthMiddleware(rec.app, token="t" * 40)
    scope = {"type": "http", "path": "/healthz", "method": "GET", "headers": []}
    await middleware(scope, _receive, rec.send)
    assert rec.scopes == ["http"]
    rec.scopes.clear()
    await middleware(
        {"type": "http", "path": "/mcp", "method": "POST", "headers": []}, _receive, rec.send
    )
    assert rec.scopes == []
    assert rec.sent[0]["status"] == 401
