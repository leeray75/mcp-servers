"""Pydantic models and validated types for tool inputs and outputs."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

MAX_QUERY_CHARS: Final = 500
MAX_PAGE_TOKEN_CHARS: Final = 512
MAX_SUBJECT_CHARS: Final = 998
MAX_BODY_BYTES: Final = 100 * 1024
MAX_RECIPIENTS: Final = 50
MAX_ADDRESS_CHARS: Final = 254

_CONTROL_CHARS: Final = frozenset(chr(c) for c in range(32) if c != 9) | {chr(127)}
# Unicode line boundaries recognised by str.splitlines() beyond ASCII (NEL, LS, PS).
_UNICODE_LINE_BREAKS: Final = frozenset({"\u0085", "\u2028", "\u2029"})


def contains_control_chars(value: str, *, allow_newlines: bool = False) -> bool:
    """True if ``value`` has control characters or line breaks that must not reach a header.

    Covers ASCII controls (CR/LF/NUL, ... but not tab), DEL, and the Unicode line
    separators U+0085/U+2028/U+2029. With ``allow_newlines`` (message bodies) CR, LF and
    the Unicode separators are permitted; other controls still are not.
    """
    allowed = {"\n", "\r"} | _UNICODE_LINE_BREAKS if allow_newlines else set()
    return any(
        (ch in _CONTROL_CHARS or ch in _UNICODE_LINE_BREAKS) and ch not in allowed for ch in value
    )


def _validate_address(value: str) -> str:
    if contains_control_chars(value):
        raise ValueError("email address contains control characters")
    candidate = value.strip()
    try:
        parsed = validate_email(candidate, check_deliverability=False, allow_smtputf8=False)
    except EmailNotValidError:
        raise ValueError("not a valid email address") from None
    return parsed.normalized


def _validate_subject(value: str) -> str:
    if contains_control_chars(value):
        raise ValueError("subject must be a single line without control characters")
    return value


def _validate_body(value: str) -> str:
    if contains_control_chars(value, allow_newlines=True):
        raise ValueError("body contains disallowed control characters")
    if len(value.encode("utf-8")) > MAX_BODY_BYTES:
        raise ValueError(f"body exceeds {MAX_BODY_BYTES} bytes")
    return value


def _validate_query(value: str) -> str:
    if contains_control_chars(value):
        raise ValueError("query contains control characters")
    return value


def _validate_page_token(value: str) -> str:
    if not value.isascii() or not value.isprintable() or " " in value:
        raise ValueError("invalid page token")
    return value


GmailId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$"),
    Field(description="A Gmail thread/message identifier (letters, digits, '-' and '_')."),
]
EmailAddress = Annotated[
    str, Field(max_length=MAX_ADDRESS_CHARS), AfterValidator(_validate_address)
]
Subject = Annotated[str, Field(max_length=MAX_SUBJECT_CHARS), AfterValidator(_validate_subject)]
Body = Annotated[str, Field(max_length=MAX_BODY_BYTES), AfterValidator(_validate_body)]
PageToken = Annotated[
    str, Field(min_length=1, max_length=MAX_PAGE_TOKEN_CHARS), AfterValidator(_validate_page_token)
]
SearchQuery = Annotated[
    str,
    Field(max_length=MAX_QUERY_CHARS),
    AfterValidator(_validate_query),
]


class _Output(BaseModel):
    model_config = ConfigDict(frozen=True)


class ThreadSummary(_Output):
    """One row of a thread search result."""

    id: str
    snippet: str
    subject: str
    sender: str
    date: str
    message_count: int


class SearchThreadsResult(_Output):
    """Page of matching threads."""

    threads: list[ThreadSummary]
    next_page_token: str | None = None


class AttachmentInfo(_Output):
    """Attachment metadata. Content is never returned."""

    filename: str
    mime_type: str
    size: int


class MessageContent(_Output):
    """A message with headers and plain-text body. All fields are untrusted data."""

    id: str
    thread_id: str
    subject: str
    sender: str
    to: str
    cc: str
    date: str
    labels: list[str] = Field(default_factory=list)
    snippet: str
    body: str | None = None
    truncated: bool = False
    attachments: list[AttachmentInfo] = Field(default_factory=list)


class ThreadContent(_Output):
    """A conversation thread."""

    id: str
    messages: list[MessageContent]
    truncated: bool = False


class Label(_Output):
    """A Gmail label."""

    id: str
    name: str
    type: str


class LabelsResult(_Output):
    """All labels of the mailbox."""

    labels: list[Label]


class DraftSummary(_Output):
    """One row of a draft listing."""

    id: str
    subject: str
    to: str
    snippet: str


class ListDraftsResult(_Output):
    """Page of drafts."""

    drafts: list[DraftSummary]
    next_page_token: str | None = None


class DraftCreated(_Output):
    """Result of creating a draft. Nothing has been sent."""

    draft_id: str
    message_id: str
    thread_id: str
    url: str


ThreadFormat = Literal["full", "metadata"]
