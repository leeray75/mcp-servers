"""R3/R5: container healthcheck. urllib and the DNS probe are monkeypatched; no network."""

from __future__ import annotations

import ast
import http.client
import io
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from gmail_mcp import healthcheck
from gmail_mcp.netcheck import DnsResult, HostResult

ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _dns(ok: bool) -> Callable[..., DnsResult]:
    def fake(**_kwargs: Any) -> DnsResult:
        return DnsResult(ok=ok, hosts=(HostResult(host="gmail.googleapis.com", ok=ok),))

    return fake


def _http(monkeypatch: pytest.MonkeyPatch, outcome: int | BaseException) -> list[tuple[str, float]]:
    seen: list[tuple[str, float]] = []

    def fake(url: str, timeout: float = 0.0, **_kwargs: Any) -> _Response:
        seen.append((url, timeout))
        if isinstance(outcome, BaseException):
            raise outcome
        return _Response(outcome)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def test_healthy_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PORT", "8000")
    seen = _http(monkeypatch, 200)
    monkeypatch.setattr(healthcheck, "resolve_hosts", _dns(True))
    assert healthcheck.main() == 0
    assert seen == [("http://127.0.0.1:8000/healthz", 2.0)]
    assert capsys.readouterr().err == ""


def test_port_comes_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "9123")
    seen = _http(monkeypatch, 200)
    monkeypatch.setattr(healthcheck, "resolve_hosts", _dns(True))
    healthcheck.main()
    assert seen[0][0] == "http://127.0.0.1:9123/healthz"


def test_port_defaults_to_8000(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PORT", raising=False)
    seen = _http(monkeypatch, 200)
    monkeypatch.setattr(healthcheck, "resolve_hosts", _dns(True))
    healthcheck.main()
    assert seen[0][0] == "http://127.0.0.1:8000/healthz"


def test_dns_failure_exits_one_with_content_free_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _http(monkeypatch, 200)
    monkeypatch.setattr(healthcheck, "resolve_hosts", _dns(False))
    assert healthcheck.main() == 1
    err = capsys.readouterr().err.strip()
    assert err == "healthcheck failed: http=ok dns=fail"
    assert "\n" not in err


def test_non_200_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _http(monkeypatch, 503)
    monkeypatch.setattr(healthcheck, "resolve_hosts", _dns(True))
    assert healthcheck.main() == 1
    assert "http=fail dns=ok" in capsys.readouterr().err


def test_unreachable_healthz_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _http(monkeypatch, ConnectionRefusedError("refused"))
    monkeypatch.setattr(healthcheck, "resolve_hosts", _dns(True))
    assert healthcheck.main() == 1


def test_http_error_status_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    error = urllib.error.HTTPError(
        "http://127.0.0.1:8000/healthz", 500, "err", Message(), io.BytesIO()
    )
    _http(monkeypatch, error)
    monkeypatch.setattr(healthcheck, "resolve_hosts", _dns(True))
    assert healthcheck.main() == 1


def test_bad_status_line_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _http(monkeypatch, http.client.BadStatusLine("junk"))
    monkeypatch.setattr(healthcheck, "resolve_hosts", _dns(True))
    assert healthcheck.main() == 1


def test_non_numeric_port_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "not-a-port")
    _http(monkeypatch, ValueError("bad url"))
    monkeypatch.setattr(healthcheck, "resolve_hosts", _dns(True))
    assert healthcheck.main() == 1


def test_healthcheck_does_not_import_config_or_read_secrets() -> None:
    source = (ROOT / "src" / "gmail_mcp" / "healthcheck.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert node.module != "gmail_mcp.config"
        if isinstance(node, ast.Import):
            assert all(alias.name != "gmail_mcp.config" for alias in node.names)
    for secret in ("GOOGLE_CLIENT", "MCP_AUTH_TOKEN"):
        assert secret not in source
