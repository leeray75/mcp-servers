from __future__ import annotations

import io
import json
import logging

import pytest

from gmail_mcp.logging_setup import (
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
        "GOCSPX-abcDEF123_456-xyz",
        "ya29.a0AfH6SMBx-fake_token.value",
        "1//0gFAKEREFRESHTOKEN-abcdefghijklmnop",
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
    log.info("token %s here", "ya29.SECRETACCESSTOKEN", extra={"tool": "GOCSPX-leaky-name"})
    line = json.loads(stream.getvalue())
    assert "SECRETACCESSTOKEN" not in json.dumps(line)
    assert "GOCSPX-leaky-name" not in json.dumps(line)
    assert line["level"] == "INFO"
    assert line["logger"] == "test.redaction"
    assert "timestamp" in line


def test_extra_fields_emitted() -> None:
    log, stream = _logger_with_stream()
    log.info("tool call", extra={"tool": "list_labels", "duration_ms": 1.5, "outcome": "ok"})
    line = json.loads(stream.getvalue())
    assert (line["tool"], line["duration_ms"], line["outcome"]) == ("list_labels", 1.5, "ok")


def test_exception_logs_type_only() -> None:
    log, stream = _logger_with_stream()
    try:
        raise ValueError("body: dear Bob, ya29.LEAK")
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


def test_google_authorization_codes_are_scrubbed() -> None:
    code = "4/0AbCdEfGhIjKlMnOpQr_stUv-123"
    out = redact(f"callback ?code={code}&x=1 and 14/05 stays")
    assert code not in out
    assert "14/05 stays" in out
