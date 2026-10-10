from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import FakeBackend, make_image

from vision_mcp import tools
from vision_mcp.config import Settings
from vision_mcp.errors import BackendError, ImageAccessError, ImageFormatError


async def test_single_image_end_to_end(
    settings: Settings, backend: FakeBackend, images_dir: Path
) -> None:
    make_image(images_dir / "a.png", size=(64, 48))
    result = await tools.describe_image(
        settings, backend.client(settings), paths=["a.png"], question="What is it?"
    )
    assert result.description == "A red square."
    assert result.model == "qwen3.8-27b"
    assert result.truncated is False
    (info,) = result.images
    assert (info.width, info.height, info.mime, info.downscaled) == (64, 48, "image/png", False)
    parts = backend.last_json["messages"][0]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url"]


async def test_multiple_images_sent_in_order(
    settings: Settings, backend: FakeBackend, images_dir: Path
) -> None:
    make_image(images_dir / "a.png")
    make_image(images_dir / "b.jpg")
    result = await tools.describe_image(
        settings, backend.client(settings), paths=["a.png", "b.jpg"], question="Compare"
    )
    assert [i.mime for i in result.images] == ["image/png", "image/jpeg"]
    parts = backend.last_json["messages"][0]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png")
    assert parts[2]["image_url"]["url"].startswith("data:image/jpeg")


@pytest.mark.parametrize("count", [0, 5])
async def test_image_count_limits(
    settings: Settings, backend: FakeBackend, images_dir: Path, count: int
) -> None:
    for i in range(count):
        make_image(images_dir / f"{i}.png")
    with pytest.raises(ImageFormatError, match="between 1 and 4"):
        await tools.describe_image(
            settings,
            backend.client(settings),
            paths=[f"{i}.png" for i in range(count)],
            question="q",
        )
    assert backend.requests == []


async def test_bad_image_stops_before_calling_the_backend(
    settings: Settings, backend: FakeBackend, images_dir: Path
) -> None:
    make_image(images_dir / "ok.png")
    with pytest.raises(ImageAccessError):
        await tools.describe_image(
            settings, backend.client(settings), paths=["ok.png", "missing.png"], question="q"
        )
    assert backend.requests == []


async def test_long_answers_are_truncated(
    settings: Settings, backend: FakeBackend, images_dir: Path
) -> None:
    make_image(images_dir / "a.png")
    backend.content = "x" * 5000
    s = settings.model_copy(update={"max_answer_chars": 500})
    result = await tools.describe_image(s, backend.client(s), paths=["a.png"], question="q")
    assert result.truncated is True
    assert result.description.startswith("x" * 500)
    assert result.description.endswith("truncated by the server ...]")


async def test_backend_truncation_is_passed_on(
    settings: Settings, backend: FakeBackend, images_dir: Path
) -> None:
    make_image(images_dir / "a.png")
    backend.finish_reason = "length"
    result = await tools.describe_image(
        settings, backend.client(settings), paths=["a.png"], question="q"
    )
    assert result.truncated is True


async def test_backend_errors_propagate(
    settings: Settings, backend: FakeBackend, images_dir: Path
) -> None:
    make_image(images_dir / "a.png")
    backend.status = 500
    with pytest.raises(BackendError):
        await tools.describe_image(
            settings, backend.client(settings), paths=["a.png"], question="q"
        )
