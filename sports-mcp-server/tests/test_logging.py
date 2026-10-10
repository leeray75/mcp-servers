from __future__ import annotations

import io
import json
import logging

import pytest

from sports_mcp.logging_setup import (
    REDACTED,
    JsonFormatter,
    RedactionFilter,
    configure_logging,
    redact,
    register_secret,
)


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcDEF123_456-xyz",
        "sk-change-me-in-production",
        "Bearer abc.def-ghi_jkl==",
        "bearer lowercase-token-123",
    ],
)
def test_patterns_redacted(secret: str) -> None:
    out = redact(f"prefix {secret} suffix")
    assert secret.split()[-1] not in out
    assert REDACTED in out
    assert out.startswith("prefix")
    assert out.endswith("suffix")


def test_registered_literal_redacted() -> None:
    register_secret("my-very-literal-secret-value")
    assert "literal-secret" not in redact("x my-very-literal-secret-value y")
    register_secret("")  # ignored


def _logger_with_stream() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    log = logging.getLogger("test.redaction")
    log.handlers[:] = [handler]
    log.propagate = False
    log.setLevel(logging.INFO)
    return log, stream


def test_filter_and_formatter_scrub_message_args_and_extras() -> None:
    log, stream = _logger_with_stream()
    log.info("token %s here", "sk-SECRETACCESSTOKEN", extra={"tool": "sk-leaky-name-12345"})
    line = json.loads(stream.getvalue())
    assert "SECRETACCESSTOKEN" not in json.dumps(line)
    assert "sk-leaky-name-12345" not in json.dumps(line)
    assert line["level"] == "INFO"
    assert line["logger"] == "test.redaction"
    assert "timestamp" in line


def test_extra_fields_emitted() -> None:
    log, stream = _logger_with_stream()
    log.info("tool call", extra={"tool": "get_scoreboard", "duration_ms": 1.5, "outcome": "ok"})
    line = json.loads(stream.getvalue())
    assert (line["tool"], line["duration_ms"], line["outcome"]) == ("get_scoreboard", 1.5, "ok")


def test_exception_logs_type_only() -> None:
    log, stream = _logger_with_stream()
    try:
        raise ValueError("body: dear Bob, sk-LEAK12345678")
    except ValueError:
        log.exception("failed")
    out = stream.getvalue()
    assert "ValueError" in out
    assert "dear Bob" not in out


def test_configure_logging_installs_single_json_handler(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    logging.getLogger("x").info("hello")
    captured = capsys.readouterr().out.strip().splitlines()
    assert json.loads(captured[-1])["message"] == "hello"
    assert len(logging.getLogger().handlers) == 1


def test_configure_logging_can_target_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    import sys

    configure_logging("INFO", stream=sys.stderr)
    logging.getLogger("x").info("to-stderr")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err.strip().splitlines()[-1])["message"] == "to-stderr"


def test_httpx_request_urls_are_not_logged(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    logging.getLogger("httpx").info("HTTP Request: GET https://site.api.espn.com/x?event=1")
    logging.getLogger("httpx").warning("kept: warnings still show")
    out = capsys.readouterr().out
    assert "HTTP Request" not in out and "kept: warnings still show" in out
