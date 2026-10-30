"""R43/R44: the structured error shape, the one-line text, and the kind taxonomy.

The taxonomy is documented in :mod:`twitch_mcp.errors`; these tests pin it so a kind cannot be
renamed or dropped silently, and pin the seven-field payload shape the agent receives.
"""

from __future__ import annotations

import pytest

from twitch_mcp.errors import (
    DEFAULT_HINTS,
    ERROR_KINDS,
    PASS_THROUGH_KINDS,
    TwitchMcpError,
)

SPEC_KINDS = {
    "permission",
    "forbidden_channel",
    "not_found",
    "not_authorized",
    "confirmation_required",
    "stale_confirmation",
    "rate_limited",
    "unauthenticated",
    "too_large",
    "unknown_outcome",
    "disabled",
    "invalid_arguments",
    "upstream",
}


def test_error_kinds_are_the_r43_vocabulary() -> None:
    assert set(ERROR_KINDS) == SPEC_KINDS
    assert set(DEFAULT_HINTS) == ERROR_KINDS


def test_pass_through_kinds_are_the_r27_set() -> None:
    """R27: only these surface unchanged from a guard lookup; everything else becomes a denial."""
    assert set(PASS_THROUGH_KINDS) == {"rate_limited", "unauthenticated", "upstream"}
    assert PASS_THROUGH_KINDS <= ERROR_KINDS


def test_text_is_kind_message_and_hint() -> None:
    error = TwitchMcpError("permission", "Missing scope", hint="Grant channel:manage:broadcast")
    assert error.to_text() == "permission: Missing scope Hint: Grant channel:manage:broadcast"


def test_default_hint_is_used_when_none_given() -> None:
    error = TwitchMcpError("unknown_outcome", "The write response was lost")
    assert error.hint == DEFAULT_HINTS["unknown_outcome"]
    assert "Do not re-issue" in error.hint


def test_structured_payload_has_exactly_the_r43_fields() -> None:
    error = TwitchMcpError(
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
    payload = TwitchMcpError(kind, "m").to_payload()["error"]  # type: ignore[arg-type]
    assert payload["kind"] == kind
    assert payload["retryable"] is False
    assert payload["code"] is None
    assert payload["http_status"] is None
    assert payload["retry_after"] is None
    assert payload["hint"]  # every kind documents a usable next step
    assert set(payload) == {
        "kind",
        "message",
        "code",
        "http_status",
        "retryable",
        "retry_after",
        "hint",
    }


def test_error_is_an_exception_with_the_message_as_its_text() -> None:
    error = TwitchMcpError("disabled", "The polls group is off")
    assert isinstance(error, Exception)
    assert str(error) == "The polls group is off"


def test_permission_hint_names_both_sides_of_the_scope_gap() -> None:
    """R45: the hint must be able to carry the scope Twitch named plus what the token holds."""
    hint = "Missing scope: moderator:manage:banned_users. This token holds: user:read:email."
    error = TwitchMcpError(
        "permission", "Not authorized for this channel", http_status=403, hint=hint
    )
    assert error.retryable is False
    assert "moderator:manage:banned_users" in error.hint
    assert "user:read:email" in error.hint


def test_unauthenticated_is_never_retryable_and_carries_the_401() -> None:
    """R24: the circuit answer is terminal for this process, so retryable must stay False."""
    error = TwitchMcpError(
        "unauthenticated", "Twitch rejected this server's token", http_status=401
    )
    assert error.retryable is False
    assert error.http_status == 401


def test_unknown_outcome_hint_forbids_a_re_issue() -> None:
    """R16: a lost write response must steer the human to check, not to re-send."""
    hint = DEFAULT_HINTS["unknown_outcome"].lower()
    assert "do not re-issue" in hint
    assert "check the channel" in hint


def test_confirmation_required_hint_describes_the_second_turn() -> None:
    """R31: the first turn tells the caller how to actually perform the write."""
    assert "confirm" in DEFAULT_HINTS["confirmation_required"].lower()
    assert "single-use" in DEFAULT_HINTS["confirmation_required"].lower()


def test_message_never_contains_the_kind_or_hint_prefix() -> None:
    error = TwitchMcpError("upstream", "Twitch returned 503")
    assert error.message == "Twitch returned 503"
    assert error.to_text().startswith("upstream: ")
