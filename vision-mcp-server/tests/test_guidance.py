"""The server tells the agent where images must live: in the error, the description and the
instructions."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp import Client

from tests.conftest import FakeBackend, make_image
from vision_mcp.config import Settings
from vision_mcp.errors import ImageAccessError
from vision_mcp.images import not_found_message, resolve_image_path
from vision_mcp.server import create_server

WIN = "C:\\Users\\me\\Pictures"


def _with(settings: Settings, **changes: object) -> Settings:
    return settings.model_copy(update=changes)


def test_error_names_the_folder_and_says_how_to_fix_it(settings: Settings) -> None:
    message = not_found_message(_with(settings, windows_images_dir=WIN))
    assert WIN in message
    assert "copy or save the file there" in message
    assert "call again" in message


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"windows_images_dir": WIN, "host_images_dir": "/mnt/c/Pics"}, WIN),
        ({"host_images_dir": "/mnt/c/Pics"}, "/mnt/c/Pics"),
        ({}, None),  # falls back to the container folder
    ],
)
def test_folder_hint_prefers_windows_then_wsl_then_container(
    settings: Settings, changes: dict[str, str], expected: str | None
) -> None:
    assert _with(settings, **changes).images_folder_hint == (expected or settings.images_dir)


def test_missing_and_outside_errors_stay_identical_and_leak_no_paths(
    settings: Settings, images_dir: Path
) -> None:
    make_image(images_dir.parent / "outside.png")
    messages = set()
    for raw in ("nope.png", "../outside.png", "/etc/passwd"):
        with pytest.raises(ImageAccessError) as info:
            resolve_image_path(raw, settings)
        messages.add(str(info.value))
    assert len(messages) == 1
    assert "outside.png" not in messages.pop()


async def test_description_and_instructions_name_the_folder(
    settings: Settings, backend: FakeBackend
) -> None:
    s = _with(settings, windows_images_dir=WIN)
    async with Client(create_server(s, backend.client(s))) as client:
        (tool,) = (await client.list_tools()).tools
        instructions = client.instructions or ""
    for text in (tool.description or "", instructions):
        assert WIN in text
        assert "copy or save it" in text


async def test_tool_error_over_mcp_tells_the_agent_what_to_do(
    settings: Settings, backend: FakeBackend
) -> None:
    s = _with(settings, windows_images_dir=WIN)
    async with Client(create_server(s, backend.client(s))) as client:
        result = await client.call_tool("describe_image", {"paths": ["C:\\Temp\\shot.png"]})
    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert WIN in text
    assert "copy or save the file there" in text
    assert "shot.png" not in text  # the agent's own path is not echoed back
    assert backend.requests == []
