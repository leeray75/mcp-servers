"""Structured JSON logging with defense-in-depth secret redaction.

Application code must never log message content, addresses or credentials in
the first place; the redaction here is a safety net, not the primary control.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from datetime import UTC, datetime
from typing import Any, Final, TextIO

REDACTED: Final = "[REDACTED]"

_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bsk-[A-Za-z0-9._-]{8,}"),  # OpenAI-style / LiteLLM API keys
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),  # Authorization header values
)

# Extra literal secrets (for example the configured MCP auth token).
_literals: set[str] = set()
_literals_lock = threading.Lock()

# Record attributes emitted by the formatter; everything else on a record is ignored.
_EXTRA_FIELDS: Final = ("tool", "duration_ms", "outcome", "status", "path", "images")


def register_secret(value: str) -> None:
    """Make :func:`redact` scrub an exact secret string wherever it appears."""
    if value:
        with _literals_lock:
            _literals.add(value)


def redact(text: str) -> str:
    """Replace anything resembling a credential in ``text`` with a marker."""
    with _literals_lock:
        literals = sorted(_literals, key=len, reverse=True)
    for literal in literals:
        text = text.replace(literal, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


class RedactionFilter(logging.Filter):
    """Scrub secrets from the message of every record passing through a handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        for name in _EXTRA_FIELDS:
            value = getattr(record, name, None)
            if isinstance(value, str):
                setattr(record, name, redact(value))
        return True


class JsonFormatter(logging.Formatter):
    """Render records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in _EXTRA_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            # Exception type only: messages and tracebacks can carry request content.
            payload["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return redact(json.dumps(payload, default=str, ensure_ascii=False))


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Install a single JSON/redacting handler on the root logger (stdout unless ``stream``)."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers[:] = []
        lib_logger.propagate = True
