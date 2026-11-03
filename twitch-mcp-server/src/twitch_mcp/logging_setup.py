"""Structured JSON logging with defense-in-depth secret redaction (R52-R54).

Application code must never log message content, credentials or response bodies in the first place;
the redaction here is a safety net, not the primary control. Every secret this server holds
(the MCP bearer token, the client id and secret, app and user access tokens, refresh tokens,
the OAuth ``code`` and ``state``) is also registered as an exact literal so it is scrubbed
wherever it appears.

Redaction works two ways, as R54 demands:

* **by key name** — a ``key: value`` pair whose key is a known secret name has its value replaced
  with the marker (``client_secret``, ``access_token``, ``refresh_token``, ``token``, ``secret``,
  ``state``, ``code``, ``password``, ``client_id``, ``authorization``);
* **by regex** — ``Bearer …``, ``client_secret=``, ``access_token=``, ``refresh_token=``, ``code=``
  (query-string style) and, when an OAuth redirect URI is registered, the ``state=<opaque>``
  parameter carried in a URL — the opaque value is unmatchable by shape, which is why the literal
  registration and the redirect-URI pattern exist.

Log lines carry the route template (``GET /channels``, ``POST /moderation/timeouts``), status, token
type (``app``|``user``), duration and outcome (R53). They never carry argument values — the sole
exception is a write audit line, which records the broadcaster id, tool name and outcome.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final, TextIO

REDACTED: Final = "[REDACTED]"

#: Keys whose value is always a secret (R52/R54). Matched case-insensitively in JSON and in
#: ``key=value`` / ``key: value`` prose.
SECRET_KEY_NAMES: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "client_id",
        "client_secret",
        "access_token",
        "refresh_token",
        "token",
        "secret",
        "password",
        "state",
        "code",
        "id_token",
        "device_token",
        "user_token",
        "app_token",
    }
)

# : Regex patterns, each covering one R54 requirement. Named so the test can assert them
# individually.
_PATTERNS: Final[Mapping[str, re.Pattern[str]]] = {
    # Any Bearer value, however it appears in a line. The value class deliberately excludes ``:`` so
    # the pattern cannot swallow a ``scheme://host`` URL that merely follows the word "Bearer";
    # Twitch
    # and MCP bearer tokens are URL-safe strings without colons.
    "bearer": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~/=-]+"),
    # ``client_secret`` under any spelling of the separator. No leading ``\b``: ``access_token`` and
    # ``refresh_token`` end in a word character but are commonly *prefixed* by nothing (a bare
    # ``access_token=``), while a preceding underscore (``_access_token``) must still match, so the
    # pattern anchors on the key token and its separator instead of a word boundary.
    "client_secret": re.compile(
        r"(?i)(?<![A-Za-z0-9])client_secret(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)",
    ),
    "access_token": re.compile(
        r"(?i)(?<![A-Za-z0-9])access_token(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
    ),
    "refresh_token": re.compile(
        r"(?i)(?<![A-Za-z0-9])refresh_token(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
    ),
    # The OAuth authorization code in a query string: ``...&code=XXXX``.
    "code": re.compile(r"(?i)\bcode\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"),
    # The OAuth state when it is carried inside a URL (opaque value, so matched by position). The
    # ``/oauth/callback`` prefix keeps ``?state=`` out of an unrelated sentence, and the ``[?&]``
    # immediately after it stops the pattern swallowing the path on a longer URL.
    "state": re.compile(r"(?i)(/oauth/callback[?&]state=)[^\s,;&\"']+"),
}

# Exact secret strings registered at boot and on every token mint/refresh.
_literals: set[str] = set()
_literals_lock = threading.Lock()

# Record attributes emitted by the formatter; everything else on a record is ignored. The extra
# fields are the R53 vocabulary: route template, status, token type, duration, outcome, and the
# write-audit pair (tool + broadcaster id).
_EXTRA_FIELDS: Final = (
    "tool",
    "route",
    "duration_ms",
    "outcome",
    "status",
    "token_type",
    "broadcaster_id",
    "scope",
)


def register_secret(value: str) -> None:
    """Make :func:`redact` scrub an exact secret string wherever it appears (R52)."""
    if value:
        with _literals_lock:
            _literals.add(value)


def unregister_secret(value: str) -> None:
    """Stop scrubbing a value that is no longer secret-bearing (e.g. a rotated token)."""
    with _literals_lock:
        _literals.discard(value)


def redact(text: str) -> str:
    """Replace anything resembling a credential in ``text`` with a marker."""
    with _literals_lock:
        literals = sorted(_literals, key=len, reverse=True)
    for literal in literals:
        text = text.replace(literal, REDACTED)
    for pattern in _PATTERNS.values():
        text = pattern.sub(REDACTED, text)
    return text


def _redact_json_keys(value: Any) -> Any:
    """Replace the values of secret-named keys anywhere in a parsed JSON structure."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower() in SECRET_KEY_NAMES else _redact_json_keys(sub))
            for key, sub in value.items()
        }
    if isinstance(value, list):
        return [_redact_json_keys(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


class RedactionFilter(logging.Filter):
    """Scrub secrets from the message and the whitelisted extra fields of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        for name in _EXTRA_FIELDS:
            value = getattr(record, name, None)
            if isinstance(value, str):
                setattr(record, name, redact(value))
        return True


class JsonFormatter(logging.Formatter):
    """Render records as one JSON object per line, with secret-named keys redacted (R54)."""

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
            # Exception type only: messages and tracebacks can carry request content (R52).
            payload["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        rendered = json.dumps(payload, default=str, ensure_ascii=False)
        try:
            decoded = json.loads(rendered)
        except ValueError:
            decoded = None
        if isinstance(decoded, dict):
            rendered = json.dumps(_redact_json_keys(decoded), ensure_ascii=False)
        # A message that is itself a JSON object (a Helix error body, a token response logged
        # verbatim)
        # is caught only if we descend into the string too: key-name redaction must apply to a
        # structured message, not just to top-level payload extras (R54).
        message = payload.get("message")
        if isinstance(message, str) and message.lstrip()[:1] in {"{", "["}:
            try:
                message_obj = json.loads(message)
            except ValueError:
                message_obj = None
            if message_obj is not None:
                payload["message"] = json.dumps(_redact_json_keys(message_obj), ensure_ascii=False)
                rendered = json.dumps(payload, default=str, ensure_ascii=False)
        return redact(rendered)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Install a single JSON/redacting handler on the root logger (stdout unless ``stream``)."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs every request URL at INFO; the URLs carry ids and query strings (R52).
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers[:] = []
        lib_logger.propagate = True


def log_request(
    *,
    route: str,
    status: int | None,
    token_type: str,
    duration_ms: float,
    outcome: str,
    logger: logging.Logger | None = None,
) -> None:
    """Emit one R53 request line: route template, status, token type, duration, outcome.

    The route is the *template* (``GET /channels``), never a concrete URL, and no argument value is
    ever passed here (R53).
    """
    (logger or logging.getLogger(__name__)).info(
        "twitch request",
        extra={
            "route": route,
            "status": status,
            "token_type": token_type,
            "duration_ms": duration_ms,
            "outcome": outcome,
        },
    )


def log_write(
    *,
    tool: str,
    broadcaster_id: str,
    outcome: str,
    duration_ms: float,
    logger: logging.Logger | None = None,
) -> None:
    """Emit one write-audit line (R53): tool name, broadcaster id, outcome.

    A write is irreversible and may act on a third party, so the broadcaster id is recorded for
    auditability. Nothing else about the write is recorded — a chat message's content, a timeout's
    reason, a term's text, and every token never reach the log (R52).
    """
    (logger or logging.getLogger(__name__)).info(
        "twitch write",
        extra={
            "tool": tool,
            "broadcaster_id": broadcaster_id,
            "outcome": outcome,
            "duration_ms": duration_ms,
        },
    )
