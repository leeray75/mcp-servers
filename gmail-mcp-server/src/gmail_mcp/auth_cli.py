"""One-time interactive Google sign-in (``python -m gmail_mcp.auth_cli``).

Runs inside a throwaway container. There is no browser in the container, so the
authorisation URL is printed for the user to open on the host; Google then
redirects to the loopback listener (published to host loopback only).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

from gmail_mcp.auth import build_flow, write_token
from gmail_mcp.config import ConfigError, Settings, load_settings

REDIRECT_PORT: Final = 8400
_PROMPT: Final = (
    "\nOpen this URL in a browser on this computer and approve access:\n\n{url}\n\n"
    f"Waiting for Google to redirect to http://127.0.0.1:{REDIRECT_PORT}/ ...\n"
)
_SUCCESS: Final = "Sign-in complete. You can close this tab and return to the terminal."


def main() -> int:
    """Run the OAuth flow and store the refresh token. Returns a process exit code."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    try:
        return _sign_in(settings)
    except Exception as exc:  # report the class only: messages may hold secrets
        print(
            f"Sign-in failed ({type(exc).__name__}). If you changed the requested "
            "permissions, tick every box on the consent screen and run the sign-in again.",
            file=sys.stderr,
        )
        return 1


def _sign_in(settings: Settings) -> int:
    flow = build_flow(settings, REDIRECT_PORT)
    credentials = flow.run_local_server(
        host="127.0.0.1",  # redirect URI host, which the browser on the host machine uses
        bind_addr="0.0.0.0",  # noqa: S104 - listen inside the container; docker publishes to loopback
        port=REDIRECT_PORT,
        open_browser=False,
        authorization_prompt_message=_PROMPT,
        success_message=_SUCCESS,
        access_type="offline",
        prompt="consent",
    )

    if not credentials.refresh_token:
        print(
            "Google did not return a refresh token. Revoke the app's access at "
            "https://myaccount.google.com/permissions and run the sign-in again.",
            file=sys.stderr,
        )
        return 1

    granted = set(credentials.granted_scopes or credentials.scopes or [])
    missing = set(settings.scopes) - granted
    if missing:
        print(
            f"Sign-in did not grant {len(missing)} required scope(s). "
            "Run it again and tick every permission box.",
            file=sys.stderr,
        )
        return 1

    write_token(Path(settings.token_path), credentials.refresh_token, granted)
    print("Token saved. Granted scopes:")
    for scope in sorted(granted):
        print(f"  {scope}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
