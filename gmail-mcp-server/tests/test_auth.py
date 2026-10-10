from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gmail_mcp import auth_cli
from gmail_mcp.auth import build_flow, load_credentials, read_token, write_token
from gmail_mcp.config import SCOPE_COMPOSE, SCOPE_READONLY, Settings
from gmail_mcp.errors import AUTH_HINT, AuthRequiredError
from tests.conftest import FAKE_CLIENT_ID, FAKE_REFRESH_TOKEN

SCOPES = [SCOPE_READONLY, SCOPE_COMPOSE]


def test_write_token_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "data" / "token.json"
    write_token(path, FAKE_REFRESH_TOKEN, SCOPES)
    assert read_token(path) == (FAKE_REFRESH_TOKEN, frozenset(SCOPES))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_write_token_mode_is_0600(tmp_path: Path) -> None:
    path = tmp_path / "token.json"
    write_token(path, FAKE_REFRESH_TOKEN, SCOPES)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_token_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "token.json"
    write_token(path, "first-token", SCOPES)
    write_token(path, "second-token", SCOPES)
    assert read_token(path)[0] == "second-token"
    assert [p.name for p in tmp_path.iterdir()] == ["token.json"]


def test_failed_write_keeps_old_token_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "token.json"
    write_token(path, "old-token", SCOPES)

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_token(path, "new-token", SCOPES)
    assert read_token(path)[0] == "old-token"
    assert [p.name for p in tmp_path.iterdir()] == ["token.json"]


def test_read_token_missing_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(AuthRequiredError) as info:
        read_token(tmp_path / "absent.json")
    assert AUTH_HINT in str(info.value)


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        "{}",
        json.dumps({"refresh_token": "", "scopes": []}),
        json.dumps({"refresh_token": 5, "scopes": []}),
        json.dumps({"refresh_token": "x", "scopes": "nope"}),
        json.dumps({"refresh_token": "x", "scopes": [1]}),
    ],
)
def test_read_token_malformed(tmp_path: Path, content: str) -> None:
    path = tmp_path / "token.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(AuthRequiredError):
        read_token(path)


def test_load_credentials_uses_refresh_token_without_network(settings: Settings) -> None:
    write_token(Path(settings.token_path), FAKE_REFRESH_TOKEN, SCOPES)
    creds = load_credentials(settings)
    assert creds.refresh_token == FAKE_REFRESH_TOKEN
    assert creds.token is None  # access token is only ever held in memory
    assert creds.client_id == FAKE_CLIENT_ID


def test_load_credentials_requires_all_configured_scopes(settings: Settings) -> None:
    write_token(Path(settings.token_path), FAKE_REFRESH_TOKEN, [SCOPE_READONLY])
    with pytest.raises(AuthRequiredError, match="scopes"):
        load_credentials(settings)


def test_token_file_never_contains_client_secret(settings: Settings) -> None:
    write_token(Path(settings.token_path), FAKE_REFRESH_TOKEN, SCOPES)
    text = Path(settings.token_path).read_text(encoding="utf-8")
    assert settings.google_client_secret.get_secret_value() not in text


def test_build_flow_uses_loopback_redirect_and_pkce(settings: Settings) -> None:
    flow = build_flow(settings, 8400)
    url, _state = flow.authorization_url(access_type="offline", prompt="consent")
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert flow.client_config["redirect_uris"] == ["http://127.0.0.1:8400/"]


class _FakeCreds:
    def __init__(self, refresh_token: str | None, granted: list[str]) -> None:
        self.refresh_token = refresh_token
        self.granted_scopes = granted
        self.scopes = granted


def _patch_main(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, creds: _FakeCreds
) -> MagicMock:
    flow = MagicMock()
    flow.run_local_server.return_value = creds
    monkeypatch.setattr(auth_cli, "load_settings", lambda: settings)
    monkeypatch.setattr(auth_cli, "build_flow", lambda s, port: flow)
    return flow


def test_auth_cli_success_stores_token_and_prints_only_scopes(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    flow = _patch_main(monkeypatch, settings, _FakeCreds(FAKE_REFRESH_TOKEN, SCOPES))
    assert auth_cli.main() == 0
    out = capsys.readouterr()
    assert FAKE_REFRESH_TOKEN not in out.out + out.err
    assert SCOPE_READONLY in out.out
    assert read_token(Path(settings.token_path))[0] == FAKE_REFRESH_TOKEN
    kwargs = flow.run_local_server.call_args.kwargs
    assert kwargs["open_browser"] is False
    assert kwargs["access_type"] == "offline"
    assert kwargs["prompt"] == "consent"
    assert kwargs["port"] == 8400
    assert kwargs["host"] == "127.0.0.1"


def test_auth_cli_fails_without_refresh_token(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_main(monkeypatch, settings, _FakeCreds(None, SCOPES))
    assert auth_cli.main() == 1
    assert "refresh token" in capsys.readouterr().err
    assert not Path(settings.token_path).exists()


def test_auth_cli_fails_when_scope_not_granted(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_main(monkeypatch, settings, _FakeCreds(FAKE_REFRESH_TOKEN, [SCOPE_READONLY]))
    assert auth_cli.main() == 1
    assert "scope" in capsys.readouterr().err
    assert not Path(settings.token_path).exists()


def test_auth_cli_config_error_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert auth_cli.main() == 2
    assert "Invalid configuration" in capsys.readouterr().err


def test_write_token_survives_filesystems_that_reject_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WSL /mnt/c (9p/drvfs) bind mounts answer chmod with EPERM; sign-in must still work."""

    def deny(*args: object, **kwargs: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "chmod", deny)
    monkeypatch.setattr(os, "fchmod", deny, raising=False)
    path = tmp_path / "token.json"
    write_token(path, FAKE_REFRESH_TOKEN, SCOPES)
    assert read_token(path)[0] == FAKE_REFRESH_TOKEN


class _ExplodingFlow:
    def run_local_server(self, **_: object) -> None:
        raise Warning("Scope has changed from X to Y ya29.SECRET")


def test_auth_cli_flow_exception_is_concise_and_secret_free(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(auth_cli, "load_settings", lambda: settings)
    monkeypatch.setattr(auth_cli, "build_flow", lambda s, port: _ExplodingFlow())
    assert auth_cli.main() == 1
    err = capsys.readouterr().err
    assert "Sign-in failed (Warning)" in err
    assert "SECRET" not in err
    assert not Path(settings.token_path).exists()


def test_auth_cli_token_write_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_main(monkeypatch, settings, _FakeCreds(FAKE_REFRESH_TOKEN, SCOPES))

    def fail(*args: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(auth_cli, "write_token", fail)
    assert auth_cli.main() == 1
    assert "Sign-in failed (OSError)" in capsys.readouterr().err
