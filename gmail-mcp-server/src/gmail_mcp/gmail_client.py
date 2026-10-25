"""Thin, thread-safe wrapper over the Gmail REST client.

Only the handful of Gmail calls the MCP tools need are exposed. The wrapper maps
every failure to a concise :class:`GmailMcpError` that never echoes request
bodies, response bodies or credentials.
"""

from __future__ import annotations

import asyncio
import http
import logging
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Final, TypeVar

import httplib2
from google.auth.exceptions import GoogleAuthError, RefreshError
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gmail_mcp.auth import load_credentials
from gmail_mcp.config import Settings
from gmail_mcp.errors import REJECTED_REASON, AuthRequiredError, GmailApiError, GmailMcpError

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS: Final = 30
READ_RETRIES: Final = 2
_T = TypeVar("_T")

ServiceFactory = Callable[[], Any]


def build_service(settings: Settings) -> Any:
    """Create a Gmail API service from the stored refresh token.

    Raises:
        AuthRequiredError: no usable token is stored.
    """
    credentials = load_credentials(settings)
    authed_http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=HTTP_TIMEOUT_SECONDS))
    return build("gmail", "v1", http=authed_http, cache_discovery=False)


_HINTS: Final[dict[int, str]] = {
    http.HTTPStatus.FORBIDDEN: " The token may lack a required scope, or the Gmail API is not "
    "enabled for the Google Cloud project.",
    http.HTTPStatus.NOT_FOUND: " The requested item does not exist.",
    http.HTTPStatus.TOO_MANY_REQUESTS: " Rate limit reached; retry later.",
}


def map_http_error(exc: HttpError) -> GmailMcpError:
    """Convert an ``HttpError`` into a safe error without echoing the response body."""
    status = int(exc.resp.status)
    if status == http.HTTPStatus.UNAUTHORIZED:
        return AuthRequiredError(REJECTED_REASON)
    try:
        phrase = http.HTTPStatus(status).phrase
    except ValueError:
        phrase = "Unknown error"
    return GmailApiError(
        f"Gmail API error: HTTP {status} {phrase}.{_HINTS.get(status, '')}", status=status
    )


class GmailClient:
    """Async facade over a lazily created, lock-protected Gmail service.

    The service is created on first use (and again after an authorisation
    failure) so that running the sign-in script takes effect without restarting
    the server. ``httplib2`` is not thread-safe, so calls are serialised.
    """

    def __init__(self, service_factory: ServiceFactory) -> None:
        self._factory = service_factory
        self._service: Any = None
        self._lock = threading.Lock()
        # Guards only the rejection record, so status reads never wait behind a Gmail call.
        self._state_lock = threading.Lock()
        self._rejected_at: datetime | None = None

    @property
    def rejected_at(self) -> datetime | None:
        """UTC time Google last rejected the credentials since the last sign-in, if any."""
        with self._state_lock:
            return self._rejected_at

    def _record_rejection(self) -> None:
        # Called only while holding ``_lock``; lock order is always _lock, then _state_lock.
        with self._state_lock:
            self._rejected_at = datetime.now(UTC)

    def _clear_rejection(self) -> None:
        with self._state_lock:
            self._rejected_at = None

    def reset(self) -> None:
        """Drop the cached service and any rejection record (called after a sign-in).

        Both happen under ``_lock``, so a call that was in flight when the sign-in completed
        cannot record a rejection after the clear (reviewer m1).
        """
        with self._lock:
            self._service = None
            self._clear_rejection()

    async def _run(self, operation: Callable[[Any], _T]) -> _T:
        return await asyncio.to_thread(self._execute, operation)

    def _execute(self, operation: Callable[[Any], _T]) -> _T:
        with self._lock:
            try:
                if self._service is None:
                    self._service = self._factory()
                result = operation(self._service)
                # A successful call proves the current credentials work (reviewer M1).
                self._clear_rejection()
                return result
            except AuthRequiredError:
                self._service = None
                raise
            except RefreshError:
                self._service = None
                self._record_rejection()
                raise AuthRequiredError(REJECTED_REASON) from None
            except HttpError as exc:
                mapped = map_http_error(exc)
                if isinstance(mapped, AuthRequiredError):
                    self._service = None
                    self._record_rejection()
                raise mapped from None
            except (GoogleAuthError, httplib2.HttpLib2Error, OSError, TimeoutError) as exc:
                logger.warning("gmail transport failure", extra={"outcome": type(exc).__name__})
                raise GmailApiError("Could not reach the Gmail API; try again shortly.") from None

    async def list_threads(
        self, query: str, max_results: int, page_token: str | None
    ) -> dict[str, Any]:
        """``users.threads.list``."""
        return await self._run(
            lambda svc: (
                svc.users()
                .threads()
                .list(userId="me", q=query, maxResults=max_results, pageToken=page_token)
                .execute(num_retries=READ_RETRIES)
            )
        )

    async def get_thread(
        self, thread_id: str, fmt: str, metadata_headers: Sequence[str] | None = None
    ) -> dict[str, Any]:
        """``users.threads.get``."""
        return await self._run(
            lambda svc: (
                svc.users()
                .threads()
                .get(
                    userId="me",
                    id=thread_id,
                    format=fmt,
                    metadataHeaders=list(metadata_headers) if metadata_headers else None,
                )
                .execute(num_retries=READ_RETRIES)
            )
        )

    async def get_message(
        self, message_id: str, fmt: str, metadata_headers: Sequence[str] | None = None
    ) -> dict[str, Any]:
        """``users.messages.get``."""
        return await self._run(
            lambda svc: (
                svc.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format=fmt,
                    metadataHeaders=list(metadata_headers) if metadata_headers else None,
                )
                .execute(num_retries=READ_RETRIES)
            )
        )

    async def list_labels(self) -> dict[str, Any]:
        """``users.labels.list``."""
        return await self._run(
            lambda svc: svc.users().labels().list(userId="me").execute(num_retries=READ_RETRIES)
        )

    async def list_drafts(self, max_results: int, page_token: str | None) -> dict[str, Any]:
        """``users.drafts.list``."""
        return await self._run(
            lambda svc: (
                svc.users()
                .drafts()
                .list(userId="me", maxResults=max_results, pageToken=page_token)
                .execute(num_retries=READ_RETRIES)
            )
        )

    async def get_draft(self, draft_id: str) -> dict[str, Any]:
        """``users.drafts.get`` with metadata only."""
        return await self._run(
            lambda svc: (
                svc.users()
                .drafts()
                .get(userId="me", id=draft_id, format="metadata")
                .execute(num_retries=READ_RETRIES)
            )
        )

    async def create_draft(self, raw_message: str, thread_id: str | None) -> dict[str, Any]:
        """``users.drafts.create``. Creates a draft only; this module has no send call.

        Not retried: creating a draft is not idempotent.
        """
        message: dict[str, str] = {"raw": raw_message}
        if thread_id:
            message["threadId"] = thread_id
        return await self._run(
            lambda svc: (
                svc.users().drafts().create(userId="me", body={"message": message}).execute()
            )
        )
