"""Server-hosted Google sign-in: ``GET /auth`` and ``GET /oauth2callback``.

The routes are reachable without the bearer token (a browser cannot send it) but only
through the loopback Host/Origin guard. Protection comes from a single-use, expiring,
bounded ``state``, PKCE, and refusing to save a token whose scopes are incomplete.
Pages are static, script-free HTML and never reflect request data.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import anyio.to_thread
from google_auth_oauthlib.flow import Flow
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from gmail_mcp.auth import GOOGLE_AUTH_URI, GOOGLE_TOKEN_URI, write_token
from gmail_mcp.config import Settings
from gmail_mcp.security import AUTH_PATH, CALLBACK_PATH

logger = logging.getLogger(__name__)

PENDING_TTL_SECONDS: Final = 600.0
MAX_PENDING: Final = 8
_ALL_METHODS: Final = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_SECURITY_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
_PERMISSIONS_URL: Final = "https://myaccount.google.com/permissions"

_PAGE: Final = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    "<title>Gmail MCP sign-in</title></head><body><h1>{title}</h1>{body}</body></html>"
)
_RETRY: Final = f'<p><a href="{AUTH_PATH}">Try again</a></p>'
# outcome -> (HTTP status, heading, body HTML). Static: no request data is ever included.
_PAGES: Final[dict[str, tuple[int, str, str]]] = {
    "succeeded": (
        200,
        "Gmail is connected",
        "<p>Gmail is connected. You can close this tab and return to your assistant.</p>",
    ),
    "denied": (400, "Sign-in cancelled", "<p>Access was not granted.</p>" + _RETRY),
    "bad_state": (
        400,
        "Sign-in link expired",
        "<p>This sign-in attempt is unknown, expired or already used.</p>" + _RETRY,
    ),
    "bad_request": (
        400,
        "Sign-in incomplete",
        "<p>The sign-in response was incomplete.</p>" + _RETRY,
    ),
    "no_refresh_token": (
        400,
        "No refresh token received",
        f"<p>Google did not return a refresh token. Revoke this app at {_PERMISSIONS_URL} "
        "and try again.</p>" + _RETRY,
    ),
    "scope_missing": (
        400,
        "Permissions missing",
        "<p>Not every requested permission was granted. Tick every permission box on the "
        "consent screen.</p>" + _RETRY,
    ),
    "failed": (500, "Sign-in failed", "<p>Sign-in failed. See the server logs.</p>" + _RETRY),
    "forbidden": (403, "Request refused", "<p>Open the sign-in link directly in your browser.</p>"),
    "method": (405, "Method not allowed", "<p>Use GET.</p>"),
}


def _page(key: str) -> HTMLResponse:
    status, title, body = _PAGES[key]
    headers = dict(_SECURITY_HEADERS)
    if key == "method":
        headers["Allow"] = "GET, HEAD"
    return HTMLResponse(_PAGE.format(title=title, body=body), status_code=status, headers=headers)


def _browser_request_allowed(request: Request) -> bool:
    """Refuse scripted cross-site requests; allow user-activated top-level navigations.

    Requests without ``Sec-Fetch-Site`` (non-browser or old clients) are allowed.
    """
    headers = request.headers
    site = headers.get("sec-fetch-site")
    if site is None or site in {"none", "same-origin"}:
        return True
    return (
        headers.get("sec-fetch-user") == "?1"
        and headers.get("sec-fetch-mode") == "navigate"
        and headers.get("sec-fetch-dest", "document") == "document"
    )


@dataclass(frozen=True)
class PendingSignIn:
    """One in-flight authorisation attempt."""

    code_verifier: str
    redirect_uri: str
    expires_at: float


class PendingStore:
    """Bounded, expiring, in-memory map of ``state`` -> pending attempt (never persisted)."""

    def __init__(
        self,
        ttl: float = PENDING_TTL_SECONDS,
        capacity: int = MAX_PENDING,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl
        self._capacity = capacity
        self._clock = clock
        self._items: OrderedDict[str, PendingSignIn] = OrderedDict()
        self._lock = threading.Lock()

    def _purge(self) -> None:
        now = self._clock()
        for state in [s for s, p in self._items.items() if p.expires_at <= now]:
            del self._items[state]

    def add(self, state: str, code_verifier: str, redirect_uri: str) -> None:
        """Store an attempt, evicting the oldest ones beyond the capacity."""
        with self._lock:
            self._purge()
            while len(self._items) >= self._capacity:
                self._items.popitem(last=False)
            self._items[state] = PendingSignIn(
                code_verifier, redirect_uri, self._clock() + self._ttl
            )

    def pop(self, state: str) -> PendingSignIn | None:
        """Consume ``state`` (single use). Unknown or expired states yield ``None``."""
        with self._lock:
            self._purge()
            return self._items.pop(state, None)

    def has_pending(self) -> bool:
        """True while any unexpired attempt exists (read-only apart from the expiry purge)."""
        with self._lock:
            self._purge()
            return bool(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class SignInService:
    """Owns the pending store and the two sign-in route handlers."""

    def __init__(
        self,
        settings: Settings,
        on_signed_in: Callable[[], None],
        store: PendingStore | None = None,
    ) -> None:
        self._settings = settings
        self._on_signed_in = on_signed_in
        self.store = PendingStore() if store is None else store
        self._write_lock = threading.Lock()
        self._redirect_uri = f"http://127.0.0.1:{settings.host_port}{CALLBACK_PATH}"

    def routes(self) -> list[BaseRoute]:
        """Starlette routes; every method is routed so non-GET gets a headered 405."""
        return [
            Route(AUTH_PATH, self.start, methods=_ALL_METHODS),
            Route(CALLBACK_PATH, self.callback, methods=_ALL_METHODS),
        ]

    def _flow(self, code_verifier: str | None = None) -> Any:
        client_config = {
            "installed": {
                "client_id": self._settings.google_client_id.get_secret_value(),
                "client_secret": self._settings.google_client_secret.get_secret_value(),
                "auth_uri": GOOGLE_AUTH_URI,
                "token_uri": GOOGLE_TOKEN_URI,
                "redirect_uris": [self._redirect_uri],
            }
        }
        return Flow.from_client_config(
            client_config,
            scopes=self._settings.scopes,
            redirect_uri=self._redirect_uri,
            code_verifier=code_verifier,
            autogenerate_code_verifier=code_verifier is None,
        )

    async def start(self, request: Request) -> Response:
        """``GET /auth``: redirect to Google's consent screen."""
        if request.method not in {"GET", "HEAD"}:
            return _page("method")
        if request.method == "HEAD":  # liveness probe: no side effects, nothing stored
            return Response(status_code=200, headers=dict(_SECURITY_HEADERS))
        if not _browser_request_allowed(request):
            logger.info("sign-in", extra={"outcome": "cross_site"})
            return _page("forbidden")
        flow = self._flow()
        state = secrets.token_urlsafe(32)
        url, _ = flow.authorization_url(state=state, access_type="offline", prompt="consent")
        self.store.add(state, str(flow.code_verifier), self._redirect_uri)
        logger.info("sign-in", extra={"outcome": "started"})
        return RedirectResponse(url, status_code=302, headers=dict(_SECURITY_HEADERS))

    async def callback(self, request: Request) -> Response:
        """``GET /oauth2callback``: validate state, exchange the code, store the token."""
        if request.method not in {"GET", "HEAD"}:
            return _page("method")
        params = request.query_params
        state = params.get("state", "")
        pending = self.store.pop(state) if state else None  # consumed whatever happens next
        if "error" in params:
            return self._finish("denied")
        if pending is None:
            return self._finish("bad_state")
        code = params.get("code", "")
        if not code:
            return self._finish("bad_request")
        try:
            outcome = await anyio.to_thread.run_sync(self._exchange, pending, code)
        except Exception as exc:
            logger.info("sign-in", extra={"outcome": f"failed:{type(exc).__name__}"})
            return _page("failed")
        return self._finish(outcome)

    def _finish(self, outcome: str) -> HTMLResponse:
        logger.info("sign-in", extra={"outcome": outcome})
        return _page(outcome)

    def _exchange(self, pending: PendingSignIn, code: str) -> str:
        """Blocking: swap the code for tokens, verify scopes, write the token, reset Gmail."""
        flow = self._flow(pending.code_verifier)
        try:
            flow.fetch_token(code=code)
        except Warning:
            # oauthlib raises ``Warning`` when Google grants fewer scopes than requested.
            return "scope_missing"
        credentials = flow.credentials
        if not credentials.refresh_token:
            return "no_refresh_token"
        granted = set(credentials.granted_scopes or credentials.scopes or [])
        if not set(self._settings.scopes) <= granted:
            return "scope_missing"
        with self._write_lock:
            write_token(Path(self._settings.token_path), credentials.refresh_token, granted)
            self._on_signed_in()
        return "succeeded"
