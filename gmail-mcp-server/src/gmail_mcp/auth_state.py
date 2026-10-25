"""Read-only sign-in state behind ``auth_status`` and the ``gmail://auth/status`` resource.

Pure by design: it reads the token file's metadata and scopes through :func:`auth.read_token`,
never contacts Google and never returns token values, client credentials or the bearer token.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from gmail_mcp.auth import read_token
from gmail_mcp.config import Settings
from gmail_mcp.errors import AuthRequiredError, auth_url

SECONDS_PER_DAY: Final = 86_400.0
EXPIRY_WARNING_DAYS: Final = 6.0
EXPIRY_WARNING: Final = (
    "Google expires refresh tokens 7 days after sign-in while the OAuth consent screen is in "
    "Testing status. Publish the consent screen to In production to prevent this (see the README)."
)
REJECTED_CAUSES_WARNING: Final = (
    "Likely causes: the 7-day expiry of Testing-mode consent screens; access revoked at "
    "myaccount.google.com/permissions; or a password change. Sign in again to fix it."
)
AuthState = Literal[
    "signed_in", "not_signed_in", "token_unreadable", "scopes_insufficient", "rejected"
]


class AuthStatus(BaseModel):
    """The sign-in state. Contains only booleans, scope names, ages, timestamps and text."""

    model_config = ConfigDict(frozen=True)

    state: AuthState
    signed_in: bool
    sign_in_url: str
    scopes_granted: list[str]
    scopes_missing: list[str]
    token_age_days: float | None
    rejected_at: str | None
    warnings: list[str]
    next_step: str
    open_commands: dict[str, str]


def _token_mtime(path: Path) -> float | None:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _token_age_days(path: Path) -> float | None:
    mtime = _token_mtime(path)
    if mtime is None:
        return None
    return max(0.0, (dt.datetime.now(dt.UTC).timestamp() - mtime) / SECONDS_PER_DAY)


def _effective_rejection(path: Path, rejection: dt.datetime | None) -> dt.datetime | None:
    """Drop a rejection older than the token file: a later sign-in (even by the CLI, which
    never reaches the server's GmailClient) has replaced the credentials that were rejected."""
    mtime = _token_mtime(path)
    if rejection is None or mtime is None:
        return rejection
    return None if rejection.timestamp() < mtime else rejection


def _base_state(path: Path, settings: Settings) -> tuple[AuthState, list[str], list[str]]:
    """The state from the token file alone, with granted and missing scopes."""
    if not path.exists():
        return "not_signed_in", [], []
    try:
        _, granted = read_token(path)
    except AuthRequiredError:
        return "token_unreadable", [], []
    missing = sorted(set(settings.scopes) - granted)
    state: AuthState = "scopes_insufficient" if missing else "signed_in"
    return state, sorted(granted), missing


def compute_auth_status(settings: Settings, rejection: dt.datetime | None) -> AuthStatus:
    """Describe the sign-in state and the exact next step. Never raises for auth problems.

    Precedence: ``rejected`` > ``token_unreadable`` > ``scopes_insufficient`` >
    ``not_signed_in`` > ``signed_in``. ``rejection`` is the UTC time Google last rejected the
    credentials since the last successful sign-in (``GmailClient.rejected_at``).
    """
    path = Path(settings.token_path)
    rejection = _effective_rejection(path, rejection)
    base, granted, missing = _base_state(path, settings)
    age = _token_age_days(path)
    state: AuthState = "rejected" if rejection is not None else base
    url = auth_url(settings.host_port)

    warnings: list[str] = []
    if age is not None and age >= EXPIRY_WARNING_DAYS:
        warnings.append(EXPIRY_WARNING)
    if state == "rejected":
        warnings.append(REJECTED_CAUSES_WARNING)
    signed_in = state == "signed_in"
    next_step = (
        "No action needed."
        if signed_in
        else (
            f"Ask the user to open {url} in their browser, have them approve access, "
            "then call auth_status and retry."
        )
    )
    return AuthStatus(
        state=state,
        signed_in=signed_in,
        sign_in_url=url,
        scopes_granted=granted,
        scopes_missing=missing,
        token_age_days=None if age is None else round(age, 2),
        rejected_at=None if rejection is None else rejection.isoformat(),
        warnings=warnings,
        next_step=next_step,
        open_commands={
            "windows": f'Start-Process "{url}"',
            "macos": f'open "{url}"',
            "linux": f'xdg-open "{url}"',
        },
    )
