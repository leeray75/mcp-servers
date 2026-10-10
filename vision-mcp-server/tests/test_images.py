from __future__ import annotations

import base64
import io
import os
from pathlib import Path

import pytest
from PIL import Image

from tests.conftest import make_image
from vision_mcp.config import Settings
from vision_mcp.errors import ImageAccessError, ImageFormatError
from vision_mcp.images import not_found_message, prepare_image, resolve_image_path


def _with(settings: Settings, **changes: object) -> Settings:
    return settings.model_copy(update=changes)


# ------------------------------------------------------------------ path mapping


def test_relative_and_absolute_container_paths(settings: Settings, images_dir: Path) -> None:
    target = make_image(images_dir / "a.png")
    assert resolve_image_path("a.png", settings) == target.resolve()
    assert resolve_image_path(str(target), settings) == target.resolve()


def test_surrounding_quotes_and_spaces_are_ignored(settings: Settings, images_dir: Path) -> None:
    target = make_image(images_dir / "my shot.png")
    assert resolve_image_path('  "my shot.png" ', settings) == target.resolve()


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\Users\me\Pictures\sub\a.png",
        "c:/users/ME/pictures/sub/a.png",
        "C:\\Users\\me\\Pictures\\sub\\a.png",
    ],
)
def test_windows_paths_map_into_the_folder(settings: Settings, images_dir: Path, raw: str) -> None:
    target = make_image(images_dir / "sub" / "a.png")
    s = _with(settings, windows_images_dir=r"C:\Users\me\Pictures")
    assert resolve_image_path(raw, s) == target.resolve()


def test_wsl_paths_map_into_the_folder(settings: Settings, images_dir: Path) -> None:
    target = make_image(images_dir / "a.png")
    s = _with(settings, host_images_dir="/mnt/c/Users/me/Pictures/")
    assert resolve_image_path("/mnt/c/Users/me/Pictures/a.png", s) == target.resolve()


def test_prefix_must_match_whole_path_segments(settings: Settings, images_dir: Path) -> None:
    make_image(images_dir / "a.png")
    s = _with(settings, windows_images_dir=r"C:\Users\me\Pictures")
    with pytest.raises(ImageAccessError, match="not found"):
        resolve_image_path(r"C:\Users\me\PicturesEvil\a.png", s)


@pytest.mark.parametrize(
    "raw",
    [
        "../secret.png",
        "sub/../../secret.png",
        "/etc/passwd",
        "C:\\Windows\\win.png",
        "http://evil.example/x.png",
        "file:///etc/passwd",
        "missing.png",
    ],
)
def test_outside_or_missing_paths_get_the_same_generic_error(
    settings: Settings, images_dir: Path, raw: str
) -> None:
    make_image(images_dir.parent / "secret.png")  # exists, but outside the folder
    with pytest.raises(ImageAccessError) as info:
        resolve_image_path(raw, settings)
    assert str(info.value) == not_found_message(settings)


@pytest.mark.parametrize("raw", ["", "   ", "bad\x00name.png", "a" * 5000])
def test_malformed_paths_rejected(settings: Settings, raw: str) -> None:
    with pytest.raises(ImageAccessError, match="Invalid image path"):
        resolve_image_path(raw, settings)


def test_directory_is_not_an_image(settings: Settings, images_dir: Path) -> None:
    (images_dir / "folder.png").mkdir()
    with pytest.raises(ImageAccessError, match="not found"):
        resolve_image_path("folder.png", settings)


def test_symlink_escaping_the_folder_is_refused(settings: Settings, images_dir: Path) -> None:
    outside = make_image(images_dir.parent / "outside.png")
    link = images_dir / "link.png"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    with pytest.raises(ImageAccessError) as info:
        resolve_image_path("link.png", settings)
    assert str(info.value) == not_found_message(settings)


def test_symlink_inside_the_folder_is_allowed(settings: Settings, images_dir: Path) -> None:
    real = make_image(images_dir / "real.png")
    link = images_dir / "alias.png"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    assert resolve_image_path("alias.png", settings) == real.resolve()


def test_missing_images_folder_is_reported(settings: Settings, tmp_path: Path) -> None:
    s = _with(settings, images_dir=str(tmp_path / "nope"))
    with pytest.raises(ImageAccessError, match="images folder is not available"):
        resolve_image_path("a.png", s)


# ------------------------------------------------------------------ preparation


def _decode(data_url: str) -> Image.Image:
    header, _, payload = data_url.partition(",")
    assert header.startswith("data:") and header.endswith(";base64")
    return Image.open(io.BytesIO(base64.b64decode(payload)))


def test_png_and_jpeg_pass_through_unchanged(settings: Settings, images_dir: Path) -> None:
    png = make_image(images_dir / "a.png")
    jpg = make_image(images_dir / "b.jpg")
    p = prepare_image(png, settings)
    j = prepare_image(jpg, settings)
    assert (p.mime, p.width, p.height, p.resized) == ("image/png", 64, 48, False)
    assert j.mime == "image/jpeg"
    assert base64.b64decode(p.data_url.split(",", 1)[1]) == png.read_bytes()


def test_format_is_detected_from_content_not_extension(
    settings: Settings, images_dir: Path
) -> None:
    disguised = images_dir / "photo.png"
    Image.new("RGB", (10, 10), (1, 2, 3)).save(disguised, format="JPEG")
    assert prepare_image(disguised, settings).mime == "image/jpeg"


def test_oversized_images_are_downscaled(settings: Settings, images_dir: Path) -> None:
    big = make_image(images_dir / "big.png", size=(3000, 1500))
    s = _with(settings, max_image_side=1000)
    result = prepare_image(big, s)
    assert result.resized is True
    assert max(result.width, result.height) == 1000
    assert _decode(result.data_url).size == (result.width, result.height)


def test_transparency_survives_downscaling(settings: Settings, images_dir: Path) -> None:
    big = make_image(images_dir / "alpha.png", size=(800, 400), color=(0, 0, 255, 90), mode="RGBA")
    result = prepare_image(big, _with(settings, max_image_side=400))
    assert result.mime == "image/png"
    assert _decode(result.data_url).mode == "RGBA"


@pytest.mark.parametrize("name", ["a.gif", "a.bmp"])
def test_gif_and_bmp_are_converted(settings: Settings, images_dir: Path, name: str) -> None:
    path = make_image(images_dir / name)
    result = prepare_image(path, settings)
    assert result.mime in {"image/png", "image/jpeg"}
    assert _decode(result.data_url).size == (64, 48)


def test_palette_png_with_transparency_keeps_alpha(settings: Settings, images_dir: Path) -> None:
    path = images_dir / "pal.gif"
    image = Image.new("P", (20, 20), 0)
    image.info["transparency"] = 0
    image.save(path, transparency=0)
    assert prepare_image(path, settings).mime == "image/png"


def test_wrong_extension_rejected(settings: Settings, images_dir: Path) -> None:
    path = images_dir / "notes.txt"
    path.write_text("hello")
    with pytest.raises(ImageFormatError, match="Unsupported file type"):
        prepare_image(path, settings)


def test_non_image_content_with_image_extension_rejected(
    settings: Settings, images_dir: Path
) -> None:
    path = images_dir / "fake.png"
    path.write_bytes(b"this is definitely not a png")
    with pytest.raises(ImageFormatError, match="not a valid, supported image"):
        prepare_image(path, settings)


def test_truncated_image_rejected(settings: Settings, images_dir: Path) -> None:
    good = make_image(images_dir / "good.png", size=(200, 200))
    cut = images_dir / "cut.png"
    cut.write_bytes(good.read_bytes()[:60])
    with pytest.raises(ImageFormatError):
        prepare_image(cut, settings)


def test_file_size_limit(settings: Settings, images_dir: Path) -> None:
    path = make_image(images_dir / "a.png", size=(300, 300))
    with pytest.raises(ImageFormatError, match="too large"):
        prepare_image(path, _with(settings, max_image_bytes=100))


def test_decompression_bomb_rejected(
    settings: Settings, images_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_image(images_dir / "a.png", size=(100, 100))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 50)
    with pytest.raises(ImageFormatError):
        prepare_image(path, settings)


def test_unreadable_file_reported_generically(settings: Settings, images_dir: Path) -> None:
    with pytest.raises(ImageAccessError, match="not found"):
        prepare_image(images_dir / "gone.png", settings)
