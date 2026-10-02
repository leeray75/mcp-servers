"""Shared fixtures. Nothing here touches the network or real credentials."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from gmail_mcp.config import Settings

FAKE_AUTH_TOKEN = "t" * 16 + "FAKE-MCP-TOKEN" + "u" * 16  # 46 chars, obviously fake
FAKE_CLIENT_ID = "fake-client-id.apps.example.invalid"
FAKE_CLIENT_SECRET = "FAKE-client-secret-for-tests"
FAKE_REFRESH_TOKEN = "fake-refresh-token-for-tests-0123456789"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure the developer's real environment never leaks into tests."""
    for name in (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "MCP_AUTH_TOKEN",
        "GMAIL_SCOPES",
        "TOKEN_PATH",
        "HOST",
        "PORT",
        "LOG_LEVEL",
        "MAX_RESULTS_CAP",
        "MAX_BODY_CHARS",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        google_client_id=FAKE_CLIENT_ID,  # type: ignore[arg-type]
        google_client_secret=FAKE_CLIENT_SECRET,  # type: ignore[arg-type]
        mcp_auth_token=FAKE_AUTH_TOKEN,  # type: ignore[arg-type]
        token_path=str(tmp_path / "data" / "token.json"),
    )


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def make_message(
    msg_id: str = "m1",
    thread_id: str = "t1",
    *,
    subject: str = "Hello",
    sender: str = "alice@example.com",
    to: str = "bob@example.com",
    date: str = "Mon, 1 Jan 2026 10:00:00 +0000",
    text: str | None = "Plain body",
    html: str | None = None,
    attachments: Sequence[tuple[str, str, int]] = (),
    extra_headers: Sequence[tuple[str, str]] = (),
) -> dict[str, Any]:
    """Build a Gmail ``Message`` resource as the API would return it."""
    parts: list[dict[str, Any]] = []
    if text is not None:
        parts.append({"mimeType": "text/plain", "body": {"size": len(text), "data": b64(text)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"size": len(html), "data": b64(html)}})
    for name, mime, size in attachments:
        parts.append(
            {"filename": name, "mimeType": mime, "body": {"attachmentId": "att1", "size": size}}
        )
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": sender},
        {"name": "To", "value": to},
        {"name": "Date", "value": date},
        *({"name": k, "value": v} for k, v in extra_headers),
    ]
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": ["INBOX"],
        "snippet": "A &#39;snippet&#39;",
        "payload": {"mimeType": "multipart/mixed", "headers": headers, "parts": parts},
    }


class FakeGmail:
    """In-memory stand-in for :class:`gmail_mcp.gmail_client.GmailClient`."""

    def __init__(self) -> None:
        self.threads: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, dict[str, Any]] = {}
        self.thread_listing: dict[str, Any] = {"threads": []}
        self.labels: dict[str, Any] = {"labels": []}
        self.draft_listing: dict[str, Any] = {"drafts": []}
        self.drafts: dict[str, dict[str, Any]] = {}
        self.created: list[tuple[str, str | None]] = []
        self.calls: list[tuple[Any, ...]] = []

    async def list_threads(self, query: str, max_results: int, page_token: str | None) -> Any:
        self.calls.append(("list_threads", query, max_results, page_token))
        return self.thread_listing

    async def get_thread(
        self, thread_id: str, fmt: str, metadata_headers: Sequence[str] | None = None
    ) -> Any:
        self.calls.append(("get_thread", thread_id, fmt, tuple(metadata_headers or ())))
        return self.threads[thread_id]

    async def get_message(
        self, message_id: str, fmt: str, metadata_headers: Sequence[str] | None = None
    ) -> Any:
        self.calls.append(("get_message", message_id, fmt, tuple(metadata_headers or ())))
        return self.messages[message_id]

    async def list_labels(self) -> Any:
        return self.labels

    async def list_drafts(self, max_results: int, page_token: str | None) -> Any:
        self.calls.append(("list_drafts", max_results, page_token))
        return self.draft_listing

    async def get_draft(self, draft_id: str) -> Any:
        return self.drafts[draft_id]

    async def create_draft(self, raw_message: str, thread_id: str | None) -> Any:
        self.created.append((raw_message, thread_id))
        return {"id": "d1", "message": {"id": "dm1", "threadId": thread_id or "newthread"}}


@pytest.fixture
def fake_gmail() -> FakeGmail:
    return FakeGmail()
