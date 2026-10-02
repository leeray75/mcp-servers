"""Tool implementations: plain async functions over :class:`GmailClient`.

Registration with MCP (names, annotations, instrumentation) lives in ``server``.
This module has no send, delete, trash or modify code path by design.
"""

from __future__ import annotations

import base64
import html
from collections.abc import Sequence
from email import policy
from email.message import EmailMessage
from typing import Any, Final

from gmail_mcp.errors import GmailApiError
from gmail_mcp.gmail_client import GmailClient
from gmail_mcp.models import (
    DraftCreated,
    DraftSummary,
    Label,
    LabelsResult,
    ListDraftsResult,
    MessageContent,
    SearchThreadsResult,
    ThreadContent,
    ThreadFormat,
    ThreadSummary,
)
from gmail_mcp.parsing import clean_header, header_map, message_to_content

SUMMARY_HEADERS: Final = ("Subject", "From", "Date")
METADATA_HEADERS: Final = ("Subject", "From", "To", "Cc", "Date")
THREAD_MESSAGE_LIMIT: Final = 50
THREAD_BODY_BUDGET_FACTOR: Final = 5
MAX_REFERENCES_CHARS: Final = 2000
DRAFT_URL: Final = "https://mail.google.com/mail/u/0/#drafts?compose={message_id}"


def _snippet(raw: object) -> str:
    return clean_header(html.unescape(str(raw or "")))


async def search_threads(
    client: GmailClient,
    *,
    query: str,
    max_results: int,
    page_token: str | None,
    results_cap: int,
) -> SearchThreadsResult:
    """List threads matching a Gmail search query, newest first.

    Subject and sender come from the thread's first message, the date from its last.
    """
    listing = await client.list_threads(query, min(max_results, results_cap), page_token)
    summaries: list[ThreadSummary] = []
    for stub in listing.get("threads") or []:
        thread = await client.get_thread(str(stub["id"]), "metadata", SUMMARY_HEADERS)
        messages: list[dict[str, Any]] = thread.get("messages") or []
        first = header_map(messages[0].get("payload") or {}) if messages else {}
        last = header_map(messages[-1].get("payload") or {}) if messages else {}
        summaries.append(
            ThreadSummary(
                id=str(stub["id"]),
                snippet=_snippet(stub.get("snippet")),
                subject=first.get("subject", ""),
                sender=first.get("from", ""),
                date=last.get("date", ""),
                message_count=len(messages),
            )
        )
    return SearchThreadsResult(threads=summaries, next_page_token=listing.get("nextPageToken"))


async def get_thread(
    client: GmailClient, *, thread_id: str, fmt: ThreadFormat, max_body_chars: int
) -> ThreadContent:
    """Fetch a thread; bodies are plain text, bounded per message and per thread.

    At most the most recent ``THREAD_MESSAGE_LIMIT`` messages are returned.
    """
    full = fmt == "full"
    thread = await client.get_thread(
        thread_id, "full" if full else "metadata", None if full else METADATA_HEADERS
    )
    raw_messages: list[dict[str, Any]] = thread.get("messages") or []
    kept = raw_messages[-THREAD_MESSAGE_LIMIT:]
    budget = max_body_chars * THREAD_BODY_BUDGET_FACTOR
    messages: list[MessageContent] = []
    any_truncated = len(kept) < len(raw_messages)
    for raw in kept:
        content = message_to_content(
            raw,
            body_limit=min(max_body_chars, budget),
            include_body=full,
            include_attachments=full,
        )
        budget -= len(content.body or "")
        any_truncated = any_truncated or content.truncated
        messages.append(content)
    return ThreadContent(
        id=str(thread.get("id", thread_id)), messages=messages, truncated=any_truncated
    )


async def get_message(
    client: GmailClient, *, message_id: str, max_body_chars: int
) -> MessageContent:
    """Fetch one message with its plain-text body and attachment metadata."""
    message = await client.get_message(message_id, "full")
    return message_to_content(
        message, body_limit=max_body_chars, include_body=True, include_attachments=True
    )


async def list_labels(client: GmailClient) -> LabelsResult:
    """List the mailbox's labels."""
    response = await client.list_labels()
    return LabelsResult(
        labels=[
            Label(
                id=str(item.get("id", "")),
                name=clean_header(str(item.get("name", ""))),
                type=str(item.get("type", "")),
            )
            for item in response.get("labels") or []
        ]
    )


async def list_drafts(
    client: GmailClient, *, max_results: int, page_token: str | None, results_cap: int
) -> ListDraftsResult:
    """List drafts with subject, recipients and snippet."""
    listing = await client.list_drafts(min(max_results, results_cap), page_token)
    drafts: list[DraftSummary] = []
    for stub in listing.get("drafts") or []:
        draft = await client.get_draft(str(stub["id"]))
        message: dict[str, Any] = draft.get("message") or {}
        headers = header_map(message.get("payload") or {})
        drafts.append(
            DraftSummary(
                id=str(stub["id"]),
                subject=headers.get("subject", ""),
                to=headers.get("to", ""),
                snippet=_snippet(message.get("snippet")),
            )
        )
    return ListDraftsResult(drafts=drafts, next_page_token=listing.get("nextPageToken"))


def build_mime(
    *,
    to: Sequence[str],
    cc: Sequence[str],
    bcc: Sequence[str],
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> EmailMessage:
    """Assemble the draft MIME message. The stdlib SMTP policy refuses CR/LF in headers."""
    message = EmailMessage(policy=policy.SMTP)
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    if bcc:
        message["Bcc"] = ", ".join(bcc)
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message.set_content(body, subtype="plain", charset="utf-8", cte="quoted-printable")
    return message


async def create_draft(
    client: GmailClient,
    *,
    to: Sequence[str],
    subject: str,
    body: str,
    cc: Sequence[str] = (),
    bcc: Sequence[str] = (),
    reply_to_message_id: str | None = None,
    max_recipients: int,
) -> DraftCreated:
    """Create a Gmail draft (never sends). Optionally threads it under an existing message."""
    if not to:
        raise GmailApiError("At least one recipient is required in 'to'.")
    if len(to) + len(cc) + len(bcc) > max_recipients:
        raise GmailApiError(f"At most {max_recipients} recipients are allowed in total.")

    thread_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    if reply_to_message_id:
        original = await client.get_message(
            reply_to_message_id, "metadata", ("Message-ID", "References")
        )
        headers = header_map(original.get("payload") or {})
        thread_id = str(original["threadId"]) if original.get("threadId") else None
        in_reply_to = headers.get("message-id") or None
        if in_reply_to:
            chain = f"{headers.get('references', '')} {in_reply_to}".strip()
            references = chain[-MAX_REFERENCES_CHARS:]

    mime = build_mime(
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body=body,
        in_reply_to=in_reply_to,
        references=references,
    )
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
    draft = await client.create_draft(raw, thread_id)
    message: dict[str, Any] = draft.get("message") or {}
    message_id = str(message.get("id", ""))
    return DraftCreated(
        draft_id=str(draft.get("id", "")),
        message_id=message_id,
        thread_id=str(message.get("threadId", thread_id or "")),
        url=DRAFT_URL.format(message_id=message_id),
    )
