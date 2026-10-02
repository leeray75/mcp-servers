"""Google credential handling: token storage, loading, refresh and OAuth flow helpers.

Only the refresh token and its granted scopes are persisted; the OAuth client
id/secret always come from configuration. Access tokens live in memory only.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from gmail_mcp.config import Settings
from gmail_mcp.errors import AuthRequiredError

GOOGLE_AUTH_URI: Final = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI: Final = "https://oauth2.googleapis.com/token"  # noqa: S105 - public endpoint
TOKEN_FILE_MODE: Final = 0o600


def write_token(path: Path, refresh_token: str, scopes: Iterable[str]) -> None:
    """Atomically write the token file with mode 0600.

    The data is written to a sibling temp file that ``mkstemp`` creates with mode 0600
    from the start (no window with looser permissions), fsynced, then renamed over
    ``path``. There is deliberately no follow-up ``chmod``: it adds nothing, and it
    fails with EPERM on WSL ``/mnt/c`` (9p/drvfs) bind mounts, where POSIX modes are
    not enforced at all.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"refresh_token": refresh_token, "scopes": sorted(set(scopes))})
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".token-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def read_token(path: Path) -> tuple[str, frozenset[str]]:
    """Read the stored refresh token and granted scopes.

    Raises:
        AuthRequiredError: the file is missing, unreadable or malformed.
    """
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        refresh_token = data["refresh_token"]
        scopes = data["scopes"]
    except FileNotFoundError:
        raise AuthRequiredError("No Google token found.") from None
    except (OSError, ValueError, KeyError, TypeError):
        raise AuthRequiredError("The stored Google token is unreadable.") from None
    if (
        not isinstance(refresh_token, str)
        or not refresh_token
        or not isinstance(scopes, list)
        or not all(isinstance(s, str) for s in scopes)
    ):
        raise AuthRequiredError("The stored Google token is malformed.")
    return refresh_token, frozenset(scopes)


def load_credentials(settings: Settings) -> Credentials:
    """Build credentials from the stored refresh token (no network access).

    Raises:
        AuthRequiredError: no token, or it lacks a required scope.
    """
    refresh_token, granted = read_token(Path(settings.token_path))
    if not set(settings.scopes) <= granted:
        raise AuthRequiredError("The stored token does not cover the configured scopes.")
    return Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=refresh_token,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=settings.google_client_id.get_secret_value(),
        client_secret=settings.google_client_secret.get_secret_value(),
        scopes=settings.scopes,
    )


def build_flow(settings: Settings, redirect_port: int) -> InstalledAppFlow:
    """Create the installed-app OAuth flow (PKCE is enabled by default)."""
    client_config = {
        "installed": {
            "client_id": settings.google_client_id.get_secret_value(),
            "client_secret": settings.google_client_secret.get_secret_value(),
            "auth_uri": GOOGLE_AUTH_URI,
            "token_uri": GOOGLE_TOKEN_URI,
            "redirect_uris": [f"http://127.0.0.1:{redirect_port}/"],
        }
    }
    return InstalledAppFlow.from_client_config(client_config, scopes=settings.scopes)
