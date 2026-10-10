from __future__ import annotations

import pytest
from tests.conftest import FAKE_API_KEY, FAKE_AUTH_TOKEN

from vision_mcp.config import ConfigError, Settings, load_settings


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, VISION_API_KEY=FAKE_API_KEY, MCP_AUTH_TOKEN=FAKE_AUTH_TOKEN)
    s = load_settings()
    assert s.vision_model == "qwen3.8-27b"
    assert s.images_dir == "/images"
    assert s.max_images_per_call == 4
    assert s.windows_images_dir is None
    assert s.host_images_dir is None
    assert s.vision_base_url.endswith("/v1")


def test_stdio_mode_does_not_need_the_http_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, VISION_API_KEY=FAKE_API_KEY)
    assert load_settings(require_auth_token=False).mcp_auth_token is None


def test_http_mode_requires_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, VISION_API_KEY=FAKE_API_KEY)
    with pytest.raises(ConfigError, match="MCP_AUTH_TOKEN"):
        load_settings()


def test_api_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, MCP_AUTH_TOKEN=FAKE_AUTH_TOKEN)
    with pytest.raises(ConfigError, match="VISION_API_KEY"):
        load_settings()


@pytest.mark.parametrize("name", ["VISION_API_KEY", "MCP_AUTH_TOKEN"])
def test_placeholders_are_rejected(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    _env(monkeypatch, VISION_API_KEY=FAKE_API_KEY, MCP_AUTH_TOKEN=FAKE_AUTH_TOKEN)
    monkeypatch.setenv(name, "replace-me-" + "x" * 40)
    with pytest.raises(ConfigError, match="placeholder"):
        load_settings()


def test_blank_api_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, VISION_API_KEY="   ", MCP_AUTH_TOKEN=FAKE_AUTH_TOKEN)
    with pytest.raises(ConfigError, match="must not be empty"):
        load_settings()


def test_short_auth_token_rejected_without_echoing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, VISION_API_KEY=FAKE_API_KEY, MCP_AUTH_TOKEN="too-short-secret")
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert "at least 32" in str(info.value)
    assert "too-short-secret" not in str(info.value)


@pytest.mark.parametrize(
    "url", ["ftp://host/v1", "not a url", "http://", "http://user:pw@host/v1", "/v1"]
)
def test_bad_base_urls_rejected(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    _env(monkeypatch, VISION_API_KEY=FAKE_API_KEY, MCP_AUTH_TOKEN=FAKE_AUTH_TOKEN)
    monkeypatch.setenv("VISION_BASE_URL", url)
    with pytest.raises(ConfigError, match="VISION_BASE_URL"):
        load_settings()


def test_base_url_trailing_slash_is_stripped() -> None:
    s = Settings(vision_api_key=FAKE_API_KEY, vision_base_url=" http://h:4000/v1/ ")  # type: ignore[arg-type]
    assert s.vision_base_url == "http://h:4000/v1"


def test_log_level_normalised_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(
        monkeypatch, VISION_API_KEY=FAKE_API_KEY, MCP_AUTH_TOKEN=FAKE_AUTH_TOKEN, LOG_LEVEL="debug"
    )
    assert load_settings().log_level == "DEBUG"
    monkeypatch.setenv("LOG_LEVEL", "loud")
    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        load_settings()


def test_blank_host_dirs_become_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, VISION_API_KEY=FAKE_API_KEY, MCP_AUTH_TOKEN=FAKE_AUTH_TOKEN)
    _env(monkeypatch, WINDOWS_IMAGES_DIR="  ", HOST_IMAGES_DIR="")
    s = load_settings()
    assert s.windows_images_dir is None
    assert s.host_images_dir is None


def test_out_of_range_numbers_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, VISION_API_KEY=FAKE_API_KEY, MCP_AUTH_TOKEN=FAKE_AUTH_TOKEN)
    monkeypatch.setenv("MAX_IMAGES_PER_CALL", "99")
    with pytest.raises(ConfigError, match="MAX_IMAGES_PER_CALL"):
        load_settings()
