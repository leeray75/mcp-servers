"""Exception types shared across modules. Messages are safe to show to an MCP client."""

from __future__ import annotations

DEFAULT_HOST_PORT = 8765


def auth_url(host_port: int = DEFAULT_HOST_PORT) -> str:
    """The server-hosted sign-in link a browser on this computer can open."""
    return f"http://127.0.0.1:{host_port}/auth"


def auth_hint(host_port: int = DEFAULT_HOST_PORT) -> str:
    """Actionable sign-in instructions containing the clickable link."""
    return (
        f"Gmail is not authorised. Open {auth_url(host_port)} in your browser to sign in "
        "(or run scripts/auth.ps1 on Windows / scripts/auth.sh in WSL)."
    )


AUTH_HINT = auth_hint()


class GmailMcpError(Exception):
    """Base class for errors whose message may be returned to the MCP client verbatim."""

    def user_message(self, host_port: int = DEFAULT_HOST_PORT) -> str:
        """The text shown to the MCP client; ``host_port`` shapes any sign-in link."""
        return str(self)


class AuthRequiredError(GmailMcpError):
    """No usable Google credentials: sign-in is missing, revoked or expired."""

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail
        super().__init__(f"{detail} {AUTH_HINT}" if detail else AUTH_HINT)

    def user_message(self, host_port: int = DEFAULT_HOST_PORT) -> str:
        hint = auth_hint(host_port)
        return f"{self.detail} {hint}" if self.detail else hint


class GmailApiError(GmailMcpError):
    """A Gmail API call failed. Carries a concise, content-free description."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
