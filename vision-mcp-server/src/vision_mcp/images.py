"""Locating, validating and preparing images.

Agents pass file paths as they see them (Windows, WSL or container spelling). Every
path is mapped into one allowed folder, resolved (symlinks included) and checked to
stay inside it before anything is read. Files are verified with Pillow rather than
trusted by extension, and are re-encoded/downscaled when needed.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from PIL import Image, UnidentifiedImageError

from vision_mcp.config import Settings
from vision_mcp.errors import ImageAccessError, ImageFormatError

ALLOWED_EXTENSIONS: Final = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
# Formats sent to the backend unchanged (when not oversized); anything else is re-encoded.
PASSTHROUGH_FORMATS: Final = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
MAX_PIXELS: Final = 60_000_000  # decompression-bomb guard
MAX_PATH_CHARS: Final = 4096

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def not_found_message(settings: Settings) -> str:
    """The single error for missing and out-of-bounds images, telling the agent what to do.

    It is identical for both cases so it cannot be used to probe the filesystem, and it
    names the allowed folder (configuration, not a secret) so the agent can fix the problem
    itself, for example by copying a screenshot there.
    """
    return (
        "Image not found, or it is outside the folder this server may read. "
        f"Images must be inside {settings.images_folder_hint}: copy or save the file there "
        "(for example with your shell tool), then call again with its new path."
    )


@dataclass(frozen=True)
class PreparedImage:
    """An image ready to embed in a chat request."""

    data_url: str
    mime: str
    width: int
    height: int
    resized: bool


def _norm(path: str) -> str:
    return path.replace("\\", "/").rstrip("/")


def _strip_prefix(raw: str, prefix: str | None) -> str | None:
    """Return the part of ``raw`` below ``prefix`` (case-insensitive), or None if not under it."""
    if not prefix:
        return None
    norm_raw, norm_prefix = _norm(raw), _norm(prefix)
    if norm_raw.casefold() == norm_prefix.casefold():
        return ""
    if norm_raw.casefold().startswith(norm_prefix.casefold() + "/"):
        return norm_raw[len(norm_prefix) + 1 :]
    return None


def resolve_image_path(raw: str, settings: Settings) -> Path:
    """Map an agent-supplied path into the images folder and return its resolved location.

    Raises:
        ImageAccessError: for anything empty, malformed, missing or outside the folder.
            The message is deliberately identical for "missing" and "outside" so the
            tool cannot be used to probe the filesystem.
    """
    candidate = raw.strip().strip("\"'")
    if not candidate or len(candidate) > MAX_PATH_CHARS or "\x00" in candidate:
        raise ImageAccessError("Invalid image path.")

    root = Path(settings.images_dir)
    try:
        root_resolved = root.resolve(strict=True)
    except OSError:
        raise ImageAccessError("The images folder is not available on this server.") from None

    relative = _strip_prefix(candidate, settings.windows_images_dir)
    if relative is None:
        relative = _strip_prefix(candidate, settings.host_images_dir)
    if relative is None:
        relative = _strip_prefix(candidate, str(root))
    if relative is None:
        if PurePosixPath(_norm(candidate)).is_absolute() or (
            len(candidate) > 1 and candidate[1] == ":"
        ):
            raise ImageAccessError(not_found_message(settings))
        relative = _norm(candidate)

    try:
        resolved = (root_resolved / relative).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise ImageAccessError(not_found_message(settings)) from None
    if not resolved.is_relative_to(root_resolved) or not resolved.is_file():
        raise ImageAccessError(not_found_message(settings))
    return resolved


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA", "PA"} or (
        image.mode == "P" and "transparency" in image.info
    )


def _encode(image: Image.Image) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    if _has_alpha(image):
        image.convert("RGBA").save(buffer, "PNG", optimize=True)
        return buffer.getvalue(), "image/png"
    image.convert("RGB").save(buffer, "JPEG", quality=90)
    return buffer.getvalue(), "image/jpeg"


def prepare_image(path: Path, settings: Settings) -> PreparedImage:
    """Validate ``path`` as an image and return it as a base64 data URL.

    Raises:
        ImageFormatError: wrong extension, too large, not a real image, or corrupt.
    """
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ImageFormatError(
            "Unsupported file type; use one of: " + ", ".join(sorted(ALLOWED_EXTENSIONS)) + "."
        )
    try:
        size = path.stat().st_size
        if size > settings.max_image_bytes:
            limit_kib = settings.max_image_bytes // 1024
            raise ImageFormatError(
                f"Image is too large ({size // 1024} KiB; limit {limit_kib} KiB)."
            )
        data = path.read_bytes()
    except OSError:
        raise ImageAccessError(not_found_message(settings)) from None

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            fmt = image.format or ""
            width, height = image.size
            oversized = max(width, height) > settings.max_image_side
            if fmt in PASSTHROUGH_FORMATS and not oversized:
                payload, mime, resized = data, PASSTHROUGH_FORMATS[fmt], False
            else:
                working = image.copy()
                if oversized:
                    working.thumbnail((settings.max_image_side, settings.max_image_side))
                payload, mime = _encode(working)
                width, height = working.size
                resized = oversized
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        raise ImageFormatError("The file is not a valid, supported image.") from None

    encoded = base64.b64encode(payload).decode("ascii")
    return PreparedImage(f"data:{mime};base64,{encoded}", mime, width, height, resized)
