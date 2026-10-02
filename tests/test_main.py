from __future__ import annotations

from typing import Any

import pytest
import uvicorn

from gmail_mcp import __main__ as entry
from tests.conftest import FAKE_AUTH_TOKEN, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET


def test_invalid_config_exits_2_with_secret_free_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "too-short-secret")
    assert entry.main() == 2
    err = capsys.readouterr().err
    assert "Invalid configuration" in err
    assert "too-short-secret" not in err


def test_valid_config_starts_uvicorn_bound_as_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", FAKE_CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", FAKE_CLIENT_SECRET)
    monkeypatch.setenv("MCP_AUTH_TOKEN", FAKE_AUTH_TOKEN)
    monkeypatch.setenv("TOKEN_PATH", str(tmp_path / "token.json"))
    monkeypatch.setenv("PORT", "9123")
    seen: dict[str, Any] = {}

    def fake_run(app: object, **kwargs: Any) -> None:
        seen.update(kwargs)
        seen["app"] = app

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(entry, "configure_logging", lambda level: None)
    assert entry.main() == 0
    assert seen["port"] == 9123
    assert seen["host"] == "0.0.0.0"
    assert seen["access_log"] is False
    assert seen["proxy_headers"] is False
