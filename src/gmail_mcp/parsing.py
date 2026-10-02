"""Convert Gmail API message resources into bounded, plain-text tool output.

Everything returned from here is untrusted email content. Bodies are reduced to
plain text (HTML stripped), size-limited, and attachments are described by
metadata only: attachment bytes are never decoded or returned.
"""

from __future__ import annotations

import base64
import binascii
import html
import re
from collections.abc import Iterator, Mapping
from html.parser import HTMLParser
from typing import Any, Final

from gmail_mcp.models import AttachmentInfo, MessageContent, contains_control_chars

MAX_PART_DEPTH: Final = 20
MAX_HEADER_CHARS: Final = 1000
_SKIP_TAGS: Final = frozenset({"script", "style", "head", "title"})
_BREAK_TAGS: Final = frozenset(
    {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table"}
)
_BLANK_LINES: Final = re.compile(r"\n{3,}")
_INLINE_SPACE: Final = re.compile(r"[ \t\f\v\u00a0]+")


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text converter (drops script/style content entirely)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BREAK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BREAK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def html_to_text(markup: str) -> str:
    """Strip tags from ``markup`` and normalise whitespace."""
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()
    lines = (_INLINE_SPACE.sub(" ", line).strip() for line in extractor.text().splitlines())
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def truncate(text: str, limit: int) -> tuple[str, bool]:
    """Cut ``text`` to at most ``limit`` characters; report whether anything was cut."""
    limit = max(limit, 0)
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def clean_header(value: str) -> str:
    """Make an untrusted header value single-line and length-bounded."""
    if contains_control_chars(value):
        value = "".join(" " if contains_control_chars(c) else c for c in value)
    return value[:MAX_HEADER_CHARS]


def header_map(payload: Mapping[str, Any]) -> dict[str, str]:
    """Lower-cased header name to sanitised value (first occurrence wins)."""
    result: dict[str, str] = {}
    for header in payload.get("headers") or []:
        name = str(header.get("name", "")).lower()
        if name and name not in result:
            result[name] = clean_header(str(header.get("value", "")))
    return result


def _walk(part: Mapping[str, Any], depth: int = 0) -> Iterator[Mapping[str, Any]]:
    yield part
    if depth >= MAX_PART_DEPTH:
        return
    for child in part.get("parts") or []:
        yield from _walk(child, depth + 1)


def _charset(part: Mapping[str, Any]) -> str:
    for header in part.get("headers") or []:
        if str(header.get("name", "")).lower() == "content-type":
            match = re.search(r'charset="?([A-Za-z0-9_.:-]+)', str(header.get("value", "")))
            if match:
                return match.group(1)
    return "utf-8"


def _decode_part(part: Mapping[str, Any]) -> str:
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    try:
        raw = base64.urlsafe_b64decode(str(data) + "=" * (-len(str(data)) % 4))
    except (binascii.Error, ValueError):
        return ""
    try:
        return raw.decode(_charset(part), errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def extract_text_body(payload: Mapping[str, Any]) -> str:
    """Plain-text body: ``text/plain`` parts, else the HTML parts converted to text."""
    plain: list[str] = []
    html_parts: list[str] = []
    for part in _walk(payload):
        if part.get("filename"):
            continue  # attachment, never body text
        mime = str(part.get("mimeType", "")).lower()
        if mime == "text/plain":
            plain.append(_decode_part(part))
        elif mime == "text/html":
            html_parts.append(_decode_part(part))
    if any(chunk.strip() for chunk in plain):
        return "\n\n".join(chunk.strip() for chunk in plain if chunk.strip())
    return "\n\n".join(html_to_text(chunk) for chunk in html_parts if chunk.strip())


def list_attachments(payload: Mapping[str, Any]) -> list[AttachmentInfo]:
    """Describe attachments (name, MIME type, size) without touching their content."""
    return [
        AttachmentInfo(
            filename=clean_header(str(part["filename"])),
            mime_type=str(part.get("mimeType", "application/octet-stream")),
            size=int((part.get("body") or {}).get("size", 0) or 0),
        )
        for part in _walk(payload)
        if part.get("filename")
    ]


def message_to_content(
    message: Mapping[str, Any],
    *,
    body_limit: int,
    include_body: bool = True,
    include_attachments: bool = False,
) -> MessageContent:
    """Build a :class:`MessageContent` from a Gmail ``Message`` resource."""
    payload: Mapping[str, Any] = message.get("payload") or {}
    headers = header_map(payload)
    body: str | None = None
    truncated = False
    if include_body:
        body, truncated = truncate(extract_text_body(payload), body_limit)
    return MessageContent(
        id=str(message.get("id", "")),
        thread_id=str(message.get("threadId", "")),
        subject=headers.get("subject", ""),
        sender=headers.get("from", ""),
        to=headers.get("to", ""),
        cc=headers.get("cc", ""),
        date=headers.get("date", ""),
        labels=[str(label) for label in message.get("labelIds") or []],
        snippet=clean_header(html.unescape(str(message.get("snippet", "")))),
        body=body,
        truncated=truncated,
        attachments=list_attachments(payload) if include_attachments else [],
    )
