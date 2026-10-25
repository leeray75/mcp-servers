"""R4: the AUTH_REQUIRED text contract, checked over every cause."""

from __future__ import annotations

import pytest

from gmail_mcp.errors import REJECTED_REASON, AuthRequiredError, auth_url

CAUSES = [
    ("No Google token found.", "no token"),
    ("The stored Google token is unreadable.", "unreadable token"),
    ("The stored Google token is malformed.", "malformed token"),
    ("The stored token does not cover the configured scopes.", "scope shortfall"),
    (REJECTED_REASON, "rejected credentials"),
]


@pytest.mark.parametrize(("reason", "label"), CAUSES, ids=[c[1] for c in CAUSES])
@pytest.mark.parametrize("port", [8765, 9001])
def test_auth_required_text_follows_the_contract(reason: str, label: str, port: int) -> None:
    text = AuthRequiredError(reason).user_message(port)
    url = auth_url(port)
    assert text.startswith(f"AUTH_REQUIRED: {reason}\n")
    assert len(text) <= 700
    assert "scripts/auth" not in text
    # Exactly one bare, unwrapped occurrence (followed by a space); the per-OS commands quote it.
    assert text.count(f"{url} ") == 1
    assert f'Start-Process "{url}"' in text
    assert f'open "{url}"' in text
    assert f'xdg-open "{url}"' in text
    assert "Do not retry yet." in text
    assert "call auth_status and retry." in text


def test_url_is_built_from_host_port_not_a_constant() -> None:
    assert auth_url(9001) == "http://127.0.0.1:9001/auth"
    assert "http://127.0.0.1:9001/auth " in AuthRequiredError(
        "No Google token found."
    ).user_message(9001)


def test_rejected_reason_names_the_seven_day_testing_expiry() -> None:
    assert "7 days after sign-in" in REJECTED_REASON
    assert "Testing status" in REJECTED_REASON


def test_default_reason_still_yields_the_contract_text() -> None:
    text = AuthRequiredError().user_message()
    assert text.startswith("AUTH_REQUIRED: ")
    assert len(text) <= 700
