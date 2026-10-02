from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from gmail_mcp.models import (
    Body,
    EmailAddress,
    GmailId,
    PageToken,
    SearchQuery,
    Subject,
    contains_control_chars,
)


def _ok(adapter_type: object, value: object) -> object:
    return TypeAdapter(adapter_type).validate_python(value)


@pytest.mark.parametrize("value", ["a" * 64, "AbC_123-xyz", "18c5f0a1b2c3d4e5"])
def test_valid_ids(value: str) -> None:
    assert _ok(GmailId, value) == value


@pytest.mark.parametrize(
    "value",
    ["", "a" * 65, "has space", "semi;colon", "slash/../x", "new\nline", "ünï", "a b", "id!"],
)
def test_invalid_ids(value: str) -> None:
    with pytest.raises(ValidationError):
        _ok(GmailId, value)


def test_id_regex_does_not_allow_trailing_newline() -> None:
    with pytest.raises(ValidationError):
        _ok(GmailId, "abc\n")


def test_valid_address_is_normalised() -> None:
    assert _ok(EmailAddress, "  Bob@Example.COM ") == "Bob@example.com"


@pytest.mark.parametrize(
    "value",
    [
        "bob@example.com\nBcc: evil@example.com",
        "bob@example.com\r\nX: y",
        "bob@example.com\x00",
        "not-an-address",
        "Bob <bob@example.com>",
        "bob@",
        "",
        "bob@exam ple.com",
        "bob@example.com," + "x" * 300,
    ],
)
def test_invalid_addresses(value: str) -> None:
    with pytest.raises(ValidationError):
        _ok(EmailAddress, value)


@pytest.mark.parametrize(
    "value", ["Hi\r\nBcc: evil@example.com", "Hi\nthere", "Hi\rthere", "a\x00b"]
)
def test_subject_rejects_header_injection(value: str) -> None:
    with pytest.raises(ValidationError):
        _ok(Subject, value)


def test_subject_allows_unicode_and_tab() -> None:
    assert _ok(Subject, "Réunion\t☕") == "Réunion\t☕"


def test_subject_length_cap() -> None:
    with pytest.raises(ValidationError):
        _ok(Subject, "s" * 999)


def test_body_allows_newlines_but_not_nul() -> None:
    assert _ok(Body, "line1\r\nline2\n") == "line1\r\nline2\n"
    with pytest.raises(ValidationError):
        _ok(Body, "a\x00b")


def test_body_size_limit_is_in_bytes() -> None:
    assert _ok(Body, "x" * 102_400)
    with pytest.raises(ValidationError):
        _ok(Body, "x" * 102_401)
    with pytest.raises(ValidationError):
        _ok(Body, "é" * 60_000)  # 120,000 bytes but only 60,000 characters


def test_query_limits() -> None:
    assert _ok(SearchQuery, "from:alice " * 10)
    with pytest.raises(ValidationError):
        _ok(SearchQuery, "q" * 501)
    with pytest.raises(ValidationError):
        _ok(SearchQuery, "a\nb")


@pytest.mark.parametrize("value", ["", "has space", "x" * 513, "tab\t", "ünï"])
def test_page_token_rejects_bad_values(value: str) -> None:
    with pytest.raises(ValidationError):
        _ok(PageToken, value)


def test_page_token_accepts_typical_value() -> None:
    assert _ok(PageToken, "09876543210987654321") == "09876543210987654321"


def test_contains_control_chars() -> None:
    assert contains_control_chars("a\nb")
    assert contains_control_chars("a\x7fb")
    assert not contains_control_chars("a\tb")
    assert not contains_control_chars("a\nb", allow_newlines=True)
