"""Shared fixtures. Nothing here touches the network or real credentials."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from vision_mcp.config import Settings
from vision_mcp.vision_client import VisionClient

FAKE_AUTH_TOKEN = "t" * 16 + "FAKE-MCP-TOKEN" + "u" * 16  # 46 chars, obviously fake
FAKE_API_KEY = "sk-FAKE-vision-key-for-tests"
BACKEND_URL = "http://vision.test/v1"

_ENV_NAMES = (
    "VISION_BASE_URL",
    "VISION_API_KEY",
    "VISION_MODEL",
    "VISION_TIMEOUT_SECONDS",
    "VISION_MAX_TOKENS",
    "VISION_TEMPERATURE",
    "IMAGES_DIR",
    "WINDOWS_IMAGES_DIR",
    "HOST_IMAGES_DIR",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_SIDE",
    "MAX_IMAGES_PER_CALL",
    "MAX_ANSWER_CHARS",
    "MCP_AUTH_TOKEN",
    "HOST",
    "PORT",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure the developer's real environment never leaks into tests."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def images_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "images"
    folder.mkdir()
    return folder


@pytest.fixture
def settings(images_dir: Path) -> Settings:
    return Settings(
        vision_base_url=BACKEND_URL,
        vision_api_key=FAKE_API_KEY,  # type: ignore[arg-type]
        mcp_auth_token=FAKE_AUTH_TOKEN,  # type: ignore[arg-type]
        images_dir=str(images_dir),
    )


def make_image(
    path: Path,
    size: tuple[int, int] = (64, 48),
    color: tuple[int, ...] = (200, 30, 30),
    mode: str = "RGB",
) -> Path:
    """Write a real image; the format follows the file extension."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, color).save(path)
    return path


class FakeBackend:
    """Stands in for the vision endpoint; records every request it receives."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.content: Any = "A red square."
        self.finish_reason = "stop"
        self.raw_body: bytes | None = None
        self.raises: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        if self.raw_body is not None:
            return httpx.Response(self.status, content=self.raw_body)
        body = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": self.content},
                    "finish_reason": self.finish_reason,
                }
            ]
        }
        return httpx.Response(self.status, json=body)

    @property
    def last_json(self) -> dict[str, Any]:
        parsed: dict[str, Any] = json.loads(self.requests[-1].content)
        return parsed

    def client(self, settings: Settings) -> VisionClient:
        return VisionClient(settings, transport=httpx.MockTransport(self.handler))


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def make_client(settings: Settings, backend: FakeBackend) -> Callable[[], VisionClient]:
    return lambda: backend.client(settings)
