"""R5.1 (config switch), R6.1 (sign-in success copy) and R7 (documentation mentions)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

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


# --- Container DNS and healthcheck (container-dns-resilience R1, R4, R7, R8) -------------------
# PyYAML is not a dependency of this project, so compose is read with a small stdlib extractor.
# Flow-style lists in docker-compose.yml are valid JSON and are decoded with json.loads.

COMPOSE = ROOT / "docker-compose.yml"
DOCKERFILE = ROOT / "Dockerfile"
HEALTH_COMMAND = ["python", "-m", "gmail_mcp.healthcheck"]


def _top_block(text: str, key: str) -> str:
    """Lines under a top-level key (zero indent), up to the next top-level key."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if re.match(rf"^{re.escape(key)}:", line))
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" ") and not line.startswith("#"):
            break
        body.append(line)
    return "\n".join(body)


def _service(name: str) -> str:
    """Lines of one service under ``services:`` (two-space indent)."""
    lines = _top_block(COMPOSE.read_text(encoding="utf-8"), "services").splitlines()
    start = next(i for i, line in enumerate(lines) if line == f"  {name}:")
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("  ") and not line.startswith("   ") and line.strip():
            break
        body.append(line)
    return "\n".join(body)


def _flow_value(block: str, key: str, indent: int) -> Any:
    match = re.search(rf"^{' ' * indent}{key}: (\[.*\])$", block, re.MULTILINE)
    assert match is not None, f"{key} missing"
    return json.loads(match.group(1))


def _dockerfile_healthcheck() -> list[str]:
    text = re.sub(r"\\\n\s*", " ", DOCKERFILE.read_text(encoding="utf-8"))
    line = next(line for line in text.splitlines() if line.startswith("HEALTHCHECK"))
    match = re.search(r"CMD (\[.*\])$", line)
    assert match is not None
    return list(json.loads(match.group(1)))


@pytest.mark.parametrize("service", ["gmail-mcp", "auth"])
def test_compose_sets_dns_on_both_services(service: str) -> None:
    dns = _flow_value(_service(service), "dns", 4)
    assert dns == ["${CONTAINER_DNS_1:-8.8.8.8}", "${CONTAINER_DNS_2:-1.1.1.1}"]
    assert all(item.strip() for item in dns)


def test_compose_healthcheck_matches_dockerfile_command() -> None:
    compose_test = _flow_value(_service("gmail-mcp"), "test", 6)
    assert compose_test == ["CMD", *HEALTH_COMMAND]
    assert _dockerfile_healthcheck() == HEALTH_COMMAND


def test_dockerfile_healthcheck_timings_unchanged() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "--interval=30s --timeout=5s --start-period=30s --start-interval=2s --retries=3" in text


def test_compose_healthcheck_timings_unchanged() -> None:
    block = _service("gmail-mcp")
    for line in (
        "interval: 30s",
        "timeout: 5s",
        "start_period: 30s",
        "start_interval: 2s",
        "retries: 3",
    ):
        assert line in block


def test_auth_service_keeps_healthcheck_disabled() -> None:
    assert "disable: true" in _service("auth")


def test_compose_hardening_and_loopback_binding_preserved() -> None:
    hardening = _top_block(COMPOSE.read_text(encoding="utf-8"), "x-hardening")
    for fragment in (
        "read_only: true",
        "cap_drop: [ALL]",
        "no-new-privileges:true",
        "init: true",
        "pids_limit: 128",
        "mem_limit: 512m",
        "cpus: 1.0",
    ):
        assert fragment in hardening
    for name in ("gmail-mcp", "auth"):
        assert "<<: *hardening" in _service(name)
    gmail = _service("gmail-mcp")
    assert '"127.0.0.1:${HOST_PORT:-8765}:8000"' in gmail
    assert "- ./data:/data" in gmail
    assert "USER 10001:10001" in DOCKERFILE.read_text(encoding="utf-8")


def test_settings_load_with_container_dns_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    from gmail_mcp.config import load_settings

    _env(monkeypatch)
    monkeypatch.setenv("CONTAINER_DNS_1", "192.0.2.53")
    monkeypatch.setenv("CONTAINER_DNS_2", "192.0.2.54")
    assert load_settings().port == 8000


def test_env_example_documents_container_dns() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "# CONTAINER_DNS_1=8.8.8.8" in text
    assert "# CONTAINER_DNS_2=1.1.1.1" in text


# --- network-failure-diagnostics R6: the DNS-failure contract is documented -----------------------


def test_readme_documents_network_dns_failure() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in ("network: dns_failure", "NETWORK_UNREACHABLE", "scripts/restart."):
        assert phrase in text


def test_changelog_records_network_dns_failure() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)[1].split("## [", 1)[0]
    for phrase in ("NETWORK_UNREACHABLE", "network", "auth_status"):
        assert phrase in unreleased


def test_claude_md_no_longer_claims_auth_status_is_network_free() -> None:
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "no network" not in text.split("`auth_state.py`", 1)[1].split("`resources.py`", 1)[0]
    assert "network" in text
