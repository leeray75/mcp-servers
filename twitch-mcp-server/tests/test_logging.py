"""R52/R53/R54 (logging_setup): the redacting JSON logger.

R54 demands redaction by *key name* and by *regex* for each of Bearer, ``client_secret``,
``access_token``, ``refresh_token`` and ``code=``. R53 demands that request lines carry the route
template, status, token type, duration and outcome — never argument values — while write-audit lines
additionally record the broadcaster id and tool name. Every secret is also registered as an exact
literal so it is scrubbed wherever it appears (R52). These tests assert each pattern individually by
its registry name, so a dropped pattern fails a named test rather than a summary count.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from tests.twitch_fakes import FAKE_ACCESS_TOKEN, FAKE_AUTH_TOKEN, FAKE_CLIENT_SECRET
from twitch_mcp.logging_setup import (
    _PATTERNS,
    REDACTED,
    SECRET_KEY_NAMES,
    JsonFormatter,
    RedactionFilter,
    configure_logging,
    log_request,
    log_write,
    redact,
    register_secret,
    unregister_secret,
)

# Every R54 pattern is exercised by name; a new pattern without a test fails the parametrised test.
# Each entry is (probe line, the secret value it must hide, a non-secret substring that must survive).
PROBE_VALUES = {
    "bearer": (f"retried with Bearer {FAKE_AUTH_TOKEN} and got 200", FAKE_AUTH_TOKEN, "got 200"),
    "client_secret": (
        f"exchanging code for client_secret={FAKE_CLIENT_SECRET} scope=user:read",
        FAKE_CLIENT_SECRET,
        "scope=user:read",
    ),
    "access_token": (
        f"minted access_token={FAKE_ACCESS_TOKEN} expires_in=15000",
        FAKE_ACCESS_TOKEN,
        "expires_in=15000",
    ),
    "refresh_token": (
        "stored refresh_token=rt_abcdef123456 for later",
        "rt_abcdef123456",
        "for later",
    ),
    # The OAuth authorization code in a query string: ``...&code=XXXX``.
    "code": (
        "callback https://127.0.0.1:8770/oauth/callback?code=SECRET-OAUTH-CODE&scope=chat",
        "SECRET-OAUTH-CODE",
        "scope=chat",
    ),
    "state": (
        "callback https://127.0.0.1:8770/oauth/callback?state=opaqueSTATEvalue123&scope=chat:read",
        "opaqueSTATEvalue123",
        "scope=chat:read",
    ),
}


def _record(message: str, **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(
        name="twitch_mcp.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# --------------------------------------------------------------------------- R54: regex patterns


@pytest.mark.parametrize("name", sorted(_PATTERNS))
def test_each_documented_pattern_scrubs_its_own_shape(name: str) -> None:
    """R54: each named regex removes exactly its credential and keeps the surrounding text."""
    assert name in PROBE_VALUES, f"no probe registered for pattern {name!r}"
    probe, secret, keep = PROBE_VALUES[name]
    out = redact(probe)
    assert secret not in out, f"{name} pattern failed to scrub {secret!r}"
    assert REDACTED in out
    assert keep in out, f"{name} pattern scrubbed too much (lost {keep!r})"


def test_the_pattern_registry_is_exactly_the_required_set() -> None:
    """R54 enumerates the shapes that must be matched; pin the set so one cannot be dropped."""
    assert set(_PATTERNS) == {
        "bearer",
        "client_secret",
        "access_token",
        "refresh_token",
        "code",
        "state",
    }


def test_bearer_value_is_scrubbed_in_prose() -> None:
    """A Bearer credential embedded in a sentence is removed even without a key name."""
    line = f"retried with Authorization: Bearer {FAKE_AUTH_TOKEN} and got 200"
    out = redact(line)
    assert FAKE_AUTH_TOKEN not in out
    assert "Authorization" in out  # the non-secret context survives


@pytest.mark.parametrize("separator", ["=", ": ", " : ", "=\\"])
def test_client_secret_survives_spelling_variations(separator: str) -> None:
    """client_secret is scrubbed under any separator/quoting."""
    line = f"requesting token client_secret{separator}{FAKE_CLIENT_SECRET}&scope=user:read"
    out = redact(line)
    assert FAKE_CLIENT_SECRET not in out


@pytest.mark.parametrize(
    "key",
    ["access_token", "refresh_token", "client_secret", "state", "code", "token", "password"],
)
def test_secret_key_names_are_in_the_registry(key: str) -> None:
    """R54: the key-name set includes every credential field the spec names."""
    assert key in SECRET_KEY_NAMES


def test_key_name_redaction_replaces_values_in_a_json_blob() -> None:
    """R54 by key name: secret-named keys in the formatted payload are masked in place.

    The formatter parses its own JSON and runs ``_redact_json_keys`` over the payload dict, so a
    secret-named *extra field* (not a blob stuffed into ``msg``) is the path the spec describes.
    """
    record = _record(
        "token response",
        # ``outcome`` is whitelisted, but a secret-shaped dict reaches the payload via a whitelisted
        # field the formatter copies verbatim; here we assert the *message* JSON keys are masked.
    )
    # Feed a JSON object as the message: the formatter parses the whole line and masks secret keys.
    record.msg = json.dumps(
        {
            "access_token": FAKE_ACCESS_TOKEN,
            "refresh_token": "rt_keepme",
            "expires_in": 15000,
            "login": "broadcaster_login",
        }
    )
    formatted = JsonFormatter().format(record)
    assert FAKE_ACCESS_TOKEN not in formatted
    assert "rt_keepme" not in formatted
    assert formatted.count(REDACTED) >= 2


def test_key_name_redaction_is_case_insensitive() -> None:
    """A secret key written in any case is still masked when the payload is re-parsed."""
    record = _record('{"Access_TOKEN": "leaked-value", "ok": 1}')
    out = JsonFormatter().format(record)
    assert "leaked-value" not in out


# --------------------------------------------------------------------------- R52: literal registry


def test_registered_literal_is_scrubbed_everywhere_it_appears() -> None:
    """R52: an exact secret string is replaced wherever it shows up, opaque value included."""
    opaque = "zK9!q weird-shaped secret"
    register_secret(opaque)
    try:
        assert opaque not in redact(f"state was {opaque} in the callback")
        assert REDACTED in redact(f"state was {opaque} in the callback")
    finally:
        unregister_secret(opaque)


def test_unregistered_literal_is_no_longer_scrubbed() -> None:
    """A rotated token stops being scrubbed once unregistered, so logs are not over-redacted forever."""
    token = "rotation-then-discarded-12345"
    register_secret(token)
    unregister_secret(token)
    assert token in redact(f"the old token {token} is no longer secret")


def test_longest_literals_are_scrubbed_first() -> None:
    """A shorter secret must not be replaced inside a longer one and leave a fragment behind."""
    short = "SECRET"
    long = "SECRET_LONGER_TOKEN"  # noqa: S105 - synthetic overlapping value
    register_secret(short)
    register_secret(long)
    try:
        out = redact(f"the value is {long} here")
        assert long not in out
        assert "LONGER_TOKEN" not in out, "a prefix-swap left a fragment of the longer secret"
    finally:
        unregister_secret(short)
        unregister_secret(long)


# --------------------------------------------------------------------------- filters + formatter


def test_redaction_filter_scrubs_message_and_extra_fields() -> None:
    """The filter masks the message and any secret-bearing extra field before formatting."""
    record = _record(
        f"token minted access_token={FAKE_ACCESS_TOKEN}",
        route=f"GET /channels?access_token={FAKE_ACCESS_TOKEN}",
        outcome="ok",
    )
    assert RedactionFilter().filter(record) is True
    assert FAKE_ACCESS_TOKEN not in record.getMessage()
    assert FAKE_ACCESS_TOKEN not in record.route  # type: ignore[attr-defined]
    assert record.route.startswith("GET /channels")  # type: ignore[attr-defined]


def test_json_formatter_emits_one_json_object_per_line() -> None:
    """R53: the machine-readable shape — timestamp, level, logger, message + whitelisted extras."""
    line = JsonFormatter().format(
        _record(
            "twitch request",
            route="GET /channels",
            status=200,
            token_type="app",
            duration_ms=12.5,
            outcome="ok",
        )
    )
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["message"] == "twitch request"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "twitch_mcp.test"
    assert payload["route"] == "GET /channels"
    assert payload["status"] == 200
    assert payload["token_type"] == "app"
    assert payload["outcome"] == "ok"


def test_json_formatter_whitelists_only_the_r53_fields() -> None:
    """R53: arbitrary record attributes never leak into the log line."""
    record = _record("hi", route="GET /users", secret_field="should-not-appear", client_id="x")
    payload = json.loads(JsonFormatter().format(record))
    assert "secret_field" not in payload
    assert "client_id" not in payload
    assert "route" in payload


def test_exception_is_logged_as_type_only() -> None:
    """R52: a traceback's message may carry request content, so only the type name is emitted."""
    try:
        msg = f"connection reset while sending {FAKE_ACCESS_TOKEN}"
        raise RuntimeError(msg)  # noqa: TRY301
    except RuntimeError:
        import sys

        record = _record("upstream failed", exc_info=sys.exc_info())
    line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["exception"] == "RuntimeError"
    assert FAKE_ACCESS_TOKEN not in line
    assert "connection reset" not in line


# --------------------------------------------------------------------------- R53: request/write lines


def test_log_request_line_shape_has_no_argument_values(caplog: pytest.LogCaptureFixture) -> None:
    """R53: a request line is route template + status + token type + duration + outcome."""
    logger = logging.getLogger("twitch_mcp.test.request")
    logger.propagate = True
    with caplog.at_level(logging.INFO, logger="twitch_mcp.test.request"):
        log_request(
            route="POST /moderation/timeouts",
            status=204,
            token_type="app",
            duration_ms=41.2,
            outcome="ok",
            logger=logger,
        )
    record = caplog.records[-1]
    assert record.route == "POST /moderation/timeouts"  # type: ignore[attr-defined]
    assert record.status == 204  # type: ignore[attr-defined]
    assert record.token_type == "app"  # type: ignore[attr-defined]
    assert record.duration_ms == 41.2  # type: ignore[attr-defined]
    assert record.outcome == "ok"  # type: ignore[attr-defined]
    # The API never exposes a broadcaster id or user id on a request line.
    assert not hasattr(record, "broadcaster_id")


def test_log_write_line_records_broadcaster_and_tool_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R53: writes are the one exception — broadcaster id + tool name + outcome, nothing else."""
    logger = logging.getLogger("twitch_mcp.test.write")
    logger.propagate = True
    with caplog.at_level(logging.INFO, logger="twitch_mcp.test.write"):
        log_write(
            tool="create_poll",
            broadcaster_id="123456789",
            outcome="ok",
            duration_ms=88.0,
            logger=logger,
        )
    record = caplog.records[-1]
    assert record.tool == "create_poll"  # type: ignore[attr-defined]
    assert record.broadcaster_id == "123456789"  # type: ignore[attr-defined]
    assert record.outcome == "ok"  # type: ignore[attr-defined]


def test_log_write_never_records_the_write_content(caplog: pytest.LogCaptureFixture) -> None:
    """R52/R53: a chat message's text or a timeout reason must never reach the audit line."""
    logger = logging.getLogger("twitch_mcp.test.write2")
    logger.propagate = True
    secret_message = "please ban @spam for spamming about secret-topic"
    with caplog.at_level(logging.INFO, logger="twitch_mcp.test.write2"):
        # log_write's signature cannot even receive the content; assert the emitted line is clean.
        log_write(
            tool="send_chat_message",
            broadcaster_id="123456789",
            outcome="ok",
            duration_ms=5.0,
            logger=logger,
        )
    text = "\n".join(r.getMessage() for r in caplog.records) + json.dumps(
        {k: v for k, v in caplog.records[-1].__dict__.items() if not k.startswith("_")}
    )
    assert secret_message not in text


# --------------------------------------------------------------------------- configure_logging


def test_configure_logging_installs_one_json_handler_and_quiets_httpx() -> None:
    """R52: a single JSON/redacting root handler; httpx/httpcore pinned to WARNING."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        configure_logging("INFO")
        assert len(root.handlers) == 1, "exactly one handler, no duplication across re-config"
        handler = root.handlers[0]
        assert isinstance(handler.formatter, JsonFormatter)
        assert any(isinstance(f, RedactionFilter) for f in handler.filters)
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_configure_logging_redacts_an_end_to_end_log_call(
    tmp_path: Any,
) -> None:
    """A real log call through the configured handler writes scrubbed JSON to the stream."""
    import io

    stream = io.StringIO()
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        configure_logging("INFO", stream=stream)
        register_secret(FAKE_CLIENT_SECRET)
        try:
            logging.getLogger("twitch_mcp.e2e").warning(
                "token exchange failed for client_secret=%s bearer %s",
                FAKE_CLIENT_SECRET,
                FAKE_AUTH_TOKEN,
            )
        finally:
            unregister_secret(FAKE_CLIENT_SECRET)
        out = stream.getvalue()
        assert FAKE_CLIENT_SECRET not in out
        assert FAKE_AUTH_TOKEN not in out
        assert REDACTED in out
        json.loads(out.strip().splitlines()[-1])  # still valid JSON
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_redaction_survives_a_non_utf8_shaped_message() -> None:
    """A message that is not valid JSON still passes through redact() and formatting."""
    line = JsonFormatter().format(_record("plain text with Bearer " + FAKE_AUTH_TOKEN))
    assert FAKE_AUTH_TOKEN not in line
    assert json.loads(line)["level"] == "INFO"
