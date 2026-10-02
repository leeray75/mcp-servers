"""Exception types shared across modules. Messages are safe to show to an MCP client."""

from __future__ import annotations

AUTH_HINT = "Gmail is not authorised. Run scripts/auth.sh (scripts/auth.ps1 on Windows) to sign in."


class GmailMcpError(Exception):
    """Base class for errors whose message may be returned to the MCP client verbatim."""


class AuthRequiredError(GmailMcpError):
    """No usable Google credentials: sign-in is missing, revoked or expired."""

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(f"{detail} {AUTH_HINT}" if detail else AUTH_HINT)


class GmailApiError(GmailMcpError):
    """A Gmail API call failed. Carries a concise, content-free description."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
