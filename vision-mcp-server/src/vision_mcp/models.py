"""Tool input and output types."""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import BaseModel, Field

DEFAULT_QUESTION: Final = (
    "Describe this image in detail: the objects, colors and layout, and transcribe any "
    "visible text exactly."
)

ImagePath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=4096,
        description="Path to an image file (png, jpg, webp, gif, bmp) as you know it.",
    ),
]
Question = Annotated[
    str,
    Field(
        min_length=1,
        max_length=2000,
        description="What you want to know about the image(s). Be specific.",
    ),
]


class ImageInfo(BaseModel):
    """What was actually sent to the vision model for one image."""

    width: int
    height: int
    mime: str
    downscaled: bool


class ImageDescription(BaseModel):
    """The vision model's answer."""

    description: str
    model: str
    images: list[ImageInfo]
    truncated: bool = Field(
        description="True if the answer was cut short; ask a narrower question."
    )
