from __future__ import annotations

from typing import Any

import pytest
import uvicorn

from sports_mcp import __main__ as entry
from tests.conftest import FAKE_AUTH_TOKEN


def test_invalid_config_exits_2_with_secret_free_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "too-short-secret")
    assert entry.main([]) == 2
    err = capsys.readouterr().err
    assert "Invalid configuration" in err
    assert "too-short-secret" not in err


def test_missing_token_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main([]) == 2
    assert "MCP_AUTH_TOKEN" in capsys.readouterr().err


def test_unknown_arguments_print_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["--bogus"]) == 2
    assert "usage" in capsys.readouterr().err


def test_valid_config_starts_uvicorn_bound_as_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", FAKE_AUTH_TOKEN)
    monkeypatch.setenv("PORT", "9123")
    seen: dict[str, Any] = {}

    def fake_run(app: object, **kwargs: Any) -> None:
        seen.update(kwargs)
        seen["app"] = app

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(entry, "configure_logging", lambda level, stream=None: None)
    assert entry.main([]) == 0
    assert seen["port"] == 9123
    assert seen["host"] == "0.0.0.0"
    assert seen["access_log"] is False
    assert seen["proxy_headers"] is False
    assert seen["server_header"] is False


def test_stdio_mode_runs_without_an_http_token(monkeypatch: pytest.MonkeyPatch) -> None:
    started: dict[str, Any] = {}

    class FakeServer:
        def run(self, transport: str) -> None:
            started["transport"] = transport

    monkeypatch.setattr(entry, "create_server", lambda settings: FakeServer())
    configured: dict[str, Any] = {}
    monkeypatch.setattr(
        entry, "configure_logging", lambda level, stream=None: configured.update(stream=stream)
    )
    assert entry.main(["--stdio"]) == 0
    assert started == {"transport": "stdio"}
    assert configured["stream"] is not None  # logs must not share stdout with the protocol
