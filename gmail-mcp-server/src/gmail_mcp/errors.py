"""Exception types shared across modules. Messages are safe to show to an MCP client."""

from __future__ import annotations

from typing import Final

DEFAULT_HOST_PORT = 8765
REJECTED_REASON: Final = (
    "Google rejected the stored credentials; this commonly happens 7 days after sign-in "
    "while the OAuth consent screen is in Testing status."
)


def auth_url(host_port: int = DEFAULT_HOST_PORT) -> str:
    """The server-hosted sign-in link a browser on this computer can open."""
    return f"http://127.0.0.1:{host_port}/auth"


def auth_required_message(reason: str, host_port: int = DEFAULT_HOST_PORT) -> str:
    """The R4 ``AUTH_REQUIRED`` text: the reason, then the exact sign-in steps and link.

    ``reason`` is a complete sentence ending in a period. The URL appears as a bare token
    once (copy-friendly) and quoted inside the per-OS open commands.
    """
    url = auth_url(host_port)
    return (
        f"AUTH_REQUIRED: {reason}\n"
        "Do not retry yet. Sign-in is needed: run the open command for the user's OS in a shell\n"
        f'(Windows: Start-Process "{url}"; macOS: open "{url}"; Linux: xdg-open "{url}"),\n'
        f"or give the user this link to copy into their browser: {url} and have them approve.\n"
        "Never use browser automation (DevTools, Playwright, Selenium) on that page;\n"
        "never type or capture credentials. After the user finishes, call auth_status and\n"
        "retry only when it reports ready_to_retry: true."
    )


class GmailMcpError(Exception):
    """Base class for errors whose message may be returned to the MCP client verbatim."""

    def user_message(self, host_port: int = DEFAULT_HOST_PORT) -> str:
        """The text shown to the MCP client; ``host_port`` shapes any sign-in link."""
        return str(self)


class AuthRequiredError(GmailMcpError):
    """No usable Google credentials: sign-in is missing, revoked or expired."""

    def __init__(self, reason: str = "No usable Google credentials are stored.") -> None:
        self.reason = reason
        super().__init__(reason)

    def user_message(self, host_port: int = DEFAULT_HOST_PORT) -> str:
        return auth_required_message(self.reason, host_port)


class GmailApiError(GmailMcpError):
    """A Gmail API call failed. Carries a concise, content-free description."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
