from __future__ import annotations

import base64
from email import message_from_bytes, policy
from typing import Any

import pytest

from gmail_mcp import tools
from gmail_mcp.errors import GmailApiError
from gmail_mcp.gmail_client import GmailClient
from tests.conftest import FakeGmail, make_message


def _client(fake: FakeGmail) -> GmailClient:
    return fake  # type: ignore[return-value]  # structural stand-in


def _parsed_draft(fake: FakeGmail) -> Any:
    raw, _thread = fake.created[-1]
    return message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)


async def test_search_threads_summarises_and_pages(fake_gmail: FakeGmail) -> None:
    fake_gmail.thread_listing = {
        "threads": [{"id": "t1", "snippet": "Don&#39;t panic"}],
        "nextPageToken": "NEXT",
    }
    fake_gmail.threads["t1"] = {
        "id": "t1",
        "messages": [
            make_message("m1", "t1", subject="Original", sender="a@example.com", date="D1"),
            make_message("m2", "t1", subject="Re: Original", sender="b@example.com", date="D2"),
        ],
    }
    result = await tools.search_threads(
        _client(fake_gmail), query="from:a", max_results=10, page_token=None, results_cap=50
    )
    assert result.next_page_token == "NEXT"
    (row,) = result.threads
    assert (row.id, row.subject, row.sender, row.date, row.message_count) == (
        "t1",
        "Original",
        "a@example.com",
        "D2",
        2,
    )
    assert row.snippet == "Don't panic"
    assert ("get_thread", "t1", "metadata", ("Subject", "From", "Date")) in fake_gmail.calls


async def test_search_threads_empty(fake_gmail: FakeGmail) -> None:
    result = await tools.search_threads(
        _client(fake_gmail), query="zzz", max_results=10, page_token=None, results_cap=50
    )
    assert result.threads == []
    assert result.next_page_token is None


async def test_search_threads_clamps_to_cap(fake_gmail: FakeGmail) -> None:
    await tools.search_threads(
        _client(fake_gmail), query="", max_results=40, page_token="p", results_cap=7
    )
    assert fake_gmail.calls[0] == ("list_threads", "", 7, "p")


async def test_get_thread_full(fake_gmail: FakeGmail) -> None:
    fake_gmail.threads["t1"] = {
        "id": "t1",
        "messages": [make_message("m1", "t1", attachments=[("a.pdf", "application/pdf", 9)])],
    }
    thread = await tools.get_thread(
        _client(fake_gmail), thread_id="t1", fmt="full", max_body_chars=1000
    )
    assert thread.id == "t1"
    assert thread.messages[0].body == "Plain body"
    assert thread.messages[0].attachments[0].filename == "a.pdf"
    assert thread.truncated is False
    assert fake_gmail.calls[0][2] == "full"


async def test_get_thread_metadata_has_no_bodies(fake_gmail: FakeGmail) -> None:
    fake_gmail.threads["t1"] = {"id": "t1", "messages": [make_message("m1", "t1")]}
    thread = await tools.get_thread(
        _client(fake_gmail), thread_id="t1", fmt="metadata", max_body_chars=1000
    )
    assert thread.messages[0].body is None
    assert thread.messages[0].subject == "Hello"
    assert fake_gmail.calls[0][2:] == ("metadata", tools.METADATA_HEADERS)


async def test_get_thread_truncates_body_and_sets_flag(fake_gmail: FakeGmail) -> None:
    fake_gmail.threads["t1"] = {
        "id": "t1",
        "messages": [make_message("m1", "t1", text="y" * 500)],
    }
    thread = await tools.get_thread(
        _client(fake_gmail), thread_id="t1", fmt="full", max_body_chars=100
    )
    assert thread.messages[0].body == "y" * 100
    assert thread.messages[0].truncated is True
    assert thread.truncated is True


async def test_get_thread_total_budget_bounds_output(fake_gmail: FakeGmail) -> None:
    messages = [make_message(f"m{i}", "t1", text="z" * 100) for i in range(20)]
    fake_gmail.threads["t1"] = {"id": "t1", "messages": messages}
    thread = await tools.get_thread(
        _client(fake_gmail), thread_id="t1", fmt="full", max_body_chars=100
    )
    total = sum(len(m.body or "") for m in thread.messages)
    assert total <= 100 * tools.THREAD_BODY_BUDGET_FACTOR
    assert thread.truncated is True


async def test_get_thread_keeps_most_recent_messages(fake_gmail: FakeGmail) -> None:
    messages = [
        make_message(f"m{i}", "t1", text="x") for i in range(tools.THREAD_MESSAGE_LIMIT + 5)
    ]
    fake_gmail.threads["t1"] = {"id": "t1", "messages": messages}
    thread = await tools.get_thread(
        _client(fake_gmail), thread_id="t1", fmt="full", max_body_chars=1000
    )
    assert len(thread.messages) == tools.THREAD_MESSAGE_LIMIT
    assert thread.messages[-1].id == f"m{tools.THREAD_MESSAGE_LIMIT + 4}"
    assert thread.truncated is True


async def test_get_message_lists_attachments_without_content(fake_gmail: FakeGmail) -> None:
    fake_gmail.messages["m1"] = make_message(
        "m1", attachments=[("secret.docx", "application/vnd.x", 2048)]
    )
    message = await tools.get_message(_client(fake_gmail), message_id="m1", max_body_chars=1000)
    assert [(a.filename, a.size) for a in message.attachments] == [("secret.docx", 2048)]
    assert "attachmentId" not in message.model_dump_json()
    assert fake_gmail.calls == [("get_message", "m1", "full", ())]


async def test_list_labels(fake_gmail: FakeGmail) -> None:
    fake_gmail.labels = {"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"}]}
    result = await tools.list_labels(_client(fake_gmail))
    assert [(label.id, label.name, label.type) for label in result.labels] == [
        ("INBOX", "INBOX", "system")
    ]
    fake_gmail.labels = {}
    assert (await tools.list_labels(_client(fake_gmail))).labels == []


async def test_list_drafts(fake_gmail: FakeGmail) -> None:
    fake_gmail.draft_listing = {"drafts": [{"id": "d1"}], "nextPageToken": "N"}
    fake_gmail.drafts["d1"] = {
        "id": "d1",
        "message": make_message("m1", subject="Draft subj", to="c@example.com"),
    }
    result = await tools.list_drafts(
        _client(fake_gmail), max_results=100, page_token=None, results_cap=10
    )
    assert result.next_page_token == "N"
    (draft,) = result.drafts
    assert (draft.id, draft.subject, draft.to, draft.snippet) == (
        "d1",
        "Draft subj",
        "c@example.com",
        "A 'snippet'",
    )
    assert fake_gmail.calls[0] == ("list_drafts", 10, None)


async def test_create_draft_builds_mime_and_returns_url(fake_gmail: FakeGmail) -> None:
    result = await tools.create_draft(
        _client(fake_gmail),
        to=["bob@example.com", "carol@example.com"],
        cc=["dave@example.com"],
        bcc=["erin@example.com"],
        subject="Réunion ☕",
        body="Héllo\nWorld",
        max_recipients=50,
    )
    assert result.draft_id == "d1"
    assert result.message_id == "dm1"
    assert result.url.endswith("#drafts?compose=dm1")
    _raw, thread_id = fake_gmail.created[0]
    assert thread_id is None
    msg = _parsed_draft(fake_gmail)
    assert msg["To"] == "bob@example.com, carol@example.com"
    assert msg["Cc"] == "dave@example.com"
    assert msg["Bcc"] == "erin@example.com"
    assert msg["Subject"] == "Réunion ☕"
    assert msg.get_content().replace("\r\n", "\n").strip() == "Héllo\nWorld"
    assert msg["In-Reply-To"] is None


async def test_create_draft_reply_sets_thread_and_reference_headers(fake_gmail: FakeGmail) -> None:
    fake_gmail.messages["orig"] = make_message(
        "orig",
        "thread42",
        extra_headers=[("Message-ID", "<abc@mail.example>"), ("References", "<root@mail.example>")],
    )
    await tools.create_draft(
        _client(fake_gmail),
        to=["bob@example.com"],
        subject="Re: Hello",
        body="Thanks",
        reply_to_message_id="orig",
        max_recipients=50,
    )
    _raw, thread_id = fake_gmail.created[0]
    assert thread_id == "thread42"
    msg = _parsed_draft(fake_gmail)
    assert msg["In-Reply-To"] == "<abc@mail.example>"
    assert msg["References"] == "<root@mail.example> <abc@mail.example>"
    assert fake_gmail.calls[0] == ("get_message", "orig", "metadata", ("Message-ID", "References"))


async def test_create_draft_reply_to_message_without_message_id(fake_gmail: FakeGmail) -> None:
    fake_gmail.messages["orig"] = make_message("orig", "thread42")
    await tools.create_draft(
        _client(fake_gmail),
        to=["bob@example.com"],
        subject="s",
        body="b",
        reply_to_message_id="orig",
        max_recipients=50,
    )
    assert fake_gmail.created[0][1] == "thread42"
    assert _parsed_draft(fake_gmail)["In-Reply-To"] is None


async def test_create_draft_requires_recipient(fake_gmail: FakeGmail) -> None:
    with pytest.raises(GmailApiError, match="recipient"):
        await tools.create_draft(
            _client(fake_gmail), to=[], subject="s", body="b", max_recipients=50
        )
    assert fake_gmail.created == []


async def test_create_draft_limits_combined_recipients(fake_gmail: FakeGmail) -> None:
    many = [f"u{i}@example.com" for i in range(30)]
    with pytest.raises(GmailApiError, match="50"):
        await tools.create_draft(
            _client(fake_gmail), to=many, cc=many, subject="s", body="b", max_recipients=50
        )
    assert fake_gmail.created == []


def test_build_mime_refuses_header_injection_even_if_validation_were_bypassed() -> None:
    with pytest.raises(ValueError):
        tools.build_mime(
            to=["a@example.com"],
            cc=[],
            bcc=[],
            subject="hi\r\nBcc: evil@example.com",
            body="b",
        )
    with pytest.raises(ValueError):
        tools.build_mime(
            to=["a@example.com\r\nBcc: evil@example.com"], cc=[], bcc=[], subject="s", body="b"
        )
