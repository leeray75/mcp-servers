from __future__ import annotations

from gmail_mcp.parsing import (
    clean_header,
    extract_text_body,
    header_map,
    html_to_text,
    message_to_content,
    truncate,
)
from tests.conftest import b64, make_message


def test_html_to_text_strips_tags_scripts_and_styles() -> None:
    html = (
        "<html><head><title>T</title><style>p{color:red}</style></head><body>"
        "<script>alert('x')</script><p>Hello &amp; <b>welcome</b></p><div>Line 2<br>Line 3</div>"
        "</body></html>"
    )
    text = html_to_text(html)
    assert "alert" not in text
    assert "color" not in text
    assert "<" not in text
    assert "Hello & welcome" in text
    assert text.splitlines()[-2:] == ["Line 2", "Line 3"]


def test_plain_text_preferred_over_html() -> None:
    msg = make_message(text="plain wins", html="<p>html loses</p>")
    assert extract_text_body(msg["payload"]) == "plain wins"


def test_html_used_when_no_plain_part() -> None:
    msg = make_message(text=None, html="<p>only <i>html</i></p>")
    assert extract_text_body(msg["payload"]) == "only html"


def test_attachment_parts_are_never_body_text() -> None:
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "text/plain",
                "filename": "notes.txt",
                "body": {"data": b64("ATTACHMENT CONTENT"), "size": 18},
            }
        ],
    }
    assert extract_text_body(payload) == ""


def test_charset_is_honoured_and_bad_charset_falls_back() -> None:
    data = "café".encode("latin-1")
    import base64

    encoded = base64.urlsafe_b64encode(data).decode()
    payload = {
        "mimeType": "text/plain",
        "headers": [{"name": "Content-Type", "value": 'text/plain; charset="iso-8859-1"'}],
        "body": {"data": encoded},
    }
    assert extract_text_body(payload) == "café"
    payload["headers"] = [{"name": "Content-Type", "value": "text/plain; charset=bogus-xx"}]
    assert "caf" in extract_text_body(payload)


def test_invalid_base64_yields_empty_body() -> None:
    payload = {"mimeType": "text/plain", "body": {"data": "!!!not base64!!!"}}
    assert extract_text_body(payload) == ""


def test_deeply_nested_parts_are_bounded() -> None:
    payload: dict[str, object] = {"mimeType": "text/plain", "body": {"data": b64("deep")}}
    for _ in range(50):
        payload = {"mimeType": "multipart/mixed", "parts": [payload]}
    assert extract_text_body(payload) == ""  # beyond MAX_PART_DEPTH, so not reached


def test_truncate() -> None:
    assert truncate("abcdef", 3) == ("abc", True)
    assert truncate("abc", 3) == ("abc", False)
    assert truncate("abc", 0) == ("", True)
    assert truncate("abc", -5) == ("", True)


def test_clean_header_strips_control_chars_and_caps_length() -> None:
    assert clean_header("a\r\nb\x00c") == "a  b c"
    assert len(clean_header("x" * 5000)) == 1000


def test_header_map_first_wins_and_lowercases() -> None:
    payload = {
        "headers": [
            {"name": "Subject", "value": "first"},
            {"name": "SUBJECT", "value": "second"},
            {"name": "", "value": "ignored"},
        ]
    }
    assert header_map(payload) == {"subject": "first"}


def test_message_to_content_full() -> None:
    msg = make_message(
        html=None,
        attachments=[("invoice.pdf", "application/pdf", 1234)],
    )
    content = message_to_content(msg, body_limit=100, include_attachments=True)
    assert content.subject == "Hello"
    assert content.sender == "alice@example.com"
    assert content.body == "Plain body"
    assert content.truncated is False
    assert content.snippet == "A 'snippet'"
    assert content.labels == ["INBOX"]
    assert [(a.filename, a.mime_type, a.size) for a in content.attachments] == [
        ("invoice.pdf", "application/pdf", 1234)
    ]


def test_message_to_content_truncates_and_flags() -> None:
    msg = make_message(text="x" * 500)
    content = message_to_content(msg, body_limit=100)
    assert content.body == "x" * 100
    assert content.truncated is True


def test_message_to_content_metadata_only_has_no_body() -> None:
    content = message_to_content(make_message(), body_limit=100, include_body=False)
    assert content.body is None
    assert content.attachments == []


def test_clean_header_flattens_unicode_line_separators() -> None:
    assert clean_header("a\u2028b\u0085c") == "a b c"
