"""R5.1 (config switch), R6.1 (sign-in success copy) and R7 (documentation mentions)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gmail_mcp import signin
from gmail_mcp.config import Settings
from tests.conftest import FAKE_AUTH_TOKEN, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET

ROOT = Path(__file__).resolve().parents[1]


def _env(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", FAKE_CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", FAKE_CLIENT_SECRET)
    monkeypatch.setenv("MCP_AUTH_TOKEN", FAKE_AUTH_TOKEN)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)


def test_url_elicitation_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    assert Settings().auth_url_elicitation is True  # type: ignore[call-arg]


@pytest.mark.parametrize("raw", ["false", "0", "False"])
def test_url_elicitation_can_be_switched_off(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    _env(monkeypatch, AUTH_URL_ELICITATION=raw)
    assert Settings().auth_url_elicitation is False  # type: ignore[call-arg]


def test_success_page_copy_is_client_neutral() -> None:
    body = signin._PAGES["succeeded"][2]
    assert "Gmail is connected. You can close this tab and return to your assistant." in body
    assert "Goose" not in body


def test_all_sign_in_pages_stay_free_of_client_brands() -> None:
    for _status, title, body in signin._PAGES.values():
        assert "Goose" not in title + body
        assert "<script" not in body


def test_env_example_documents_the_switch() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "AUTH_URL_ELICITATION" in text


def test_readme_documents_the_new_features() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "AUTH_URL_ELICITATION",
        "auth_status",
        "gmail://auth/status",
        "In production",
        "7 days",
    ):
        assert phrase in text


def test_changelog_records_the_additions() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released = text.split("## [0.2.0]", 1)[1].split("## [", 1)[0]
    for phrase in ("auth_status", "gmail://", "AUTH_URL_ELICITATION"):
        assert phrase in released


HARDENING_FIELDS = ("ready_to_retry", "checked_at", "sign_in_in_progress")


def test_readme_documents_the_guardrails_and_fields() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in (*HARDENING_FIELDS, "browser-automation", "copy and paste"):
        assert phrase in text


def test_changelog_records_the_hardening() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released = text.split("## [0.2.0]", 1)[1].split("## [", 1)[0]
    for phrase in (*HARDENING_FIELDS, "browser-automation"):
        assert phrase in released


def test_spec_and_claude_md_document_the_hardening() -> None:
    spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for phrase in HARDENING_FIELDS:
        assert phrase in spec
        assert phrase in claude
    assert "description" in claude


def test_package_version_matches_pyproject() -> None:
    import tomllib

    import gmail_mcp

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert gmail_mcp.__version__ == declared == "0.2.0"
