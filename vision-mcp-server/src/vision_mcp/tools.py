"""Tool implementation, independent of the MCP transport."""

from __future__ import annotations

from vision_mcp.config import Settings
from vision_mcp.errors import ImageFormatError
from vision_mcp.images import PreparedImage, prepare_image, resolve_image_path
from vision_mcp.models import ImageDescription, ImageInfo
from vision_mcp.vision_client import VisionClient

TRUNCATION_MARKER = "\n[... answer truncated by the server ...]"


async def describe_image(
    settings: Settings, client: VisionClient, *, paths: list[str], question: str
) -> ImageDescription:
    """Validate the images, ask the vision model ``question`` about them and return its answer."""
    if not 1 <= len(paths) <= settings.max_images_per_call:
        raise ImageFormatError(
            f"Provide between 1 and {settings.max_images_per_call} images per call."
        )
    prepared: list[PreparedImage] = [
        prepare_image(resolve_image_path(raw, settings), settings) for raw in paths
    ]
    answer = await client.describe(prepared, question)

    text, truncated = answer.text, answer.truncated
    if len(text) > settings.max_answer_chars:
        text, truncated = text[: settings.max_answer_chars] + TRUNCATION_MARKER, True
    return ImageDescription(
        description=text,
        model=settings.vision_model,
        images=[
            ImageInfo(width=p.width, height=p.height, mime=p.mime, downscaled=p.resized)
            for p in prepared
        ],
        truncated=truncated,
    )
