"""R30-R31: the structured error shape, the one-line text and the kind vocabulary."""

from __future__ import annotations

import pytest

from discord_mcp.errors import DEFAULT_HINTS, ERROR_KINDS, DiscordMcpError


def test_error_kinds_are_the_r31_vocabulary() -> None:
    assert set(ERROR_KINDS) == {
        "validation",
        "forbidden_guild",
        "permission",
        "not_found",
        "rate_limited",
        "index_pending",
        "auth",
        "upstream",
        "unavailable",
    }
    assert set(DEFAULT_HINTS) == ERROR_KINDS


def test_text_is_kind_message_and_hint() -> None:
    error = DiscordMcpError("permission", "Missing VIEW_CHANNEL", hint="Grant it")
    assert error.to_text() == "permission: Missing VIEW_CHANNEL Hint: Grant it"


def test_default_hint_is_used_when_none_given() -> None:
    error = DiscordMcpError("auth", "rejected")
    assert error.hint == DEFAULT_HINTS["auth"]
    assert "scripts/restart.sh --recreate" in error.hint


def test_structured_payload_has_exactly_the_r30_fields() -> None:
    error = DiscordMcpError(
        "rate_limited",
        "slow down",
        code=20028,
        http_status=429,
        retryable=True,
        retry_after=1.5,
        hint="wait",
    )
    assert error.to_payload() == {
        "error": {
            "kind": "rate_limited",
            "message": "slow down",
            "code": 20028,
            "http_status": 429,
            "retryable": True,
            "retry_after": 1.5,
            "hint": "wait",
        }
    }


@pytest.mark.parametrize("kind", sorted(ERROR_KINDS))
def test_every_kind_produces_a_payload(kind: str) -> None:
    payload = DiscordMcpError(kind, "m").to_payload()["error"]  # type: ignore[arg-type]
    assert payload["kind"] == kind
    assert payload["retryable"] is False
    assert payload["code"] is None
