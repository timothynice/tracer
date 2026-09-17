import io

import pytest
from PIL import Image

from studi0trace.imaging.intake import IntakeError, load_upload
from tests.conftest import black_square_on_transparent, encode, make_png

LIMITS = dict(max_bytes=20 * 1024 * 1024, max_pixels=40_000_000)


def test_accepts_png_and_normalises_to_rgba():
    result = load_upload(make_png(32, 16, "RGB", (10, 20, 30)), **LIMITS)
    assert result.image.mode == "RGBA"
    assert (result.width, result.height) == (32, 16)
    assert result.source_format == "PNG"
    assert result.image.getpixel((0, 0)) == (10, 20, 30, 255)


def test_accepts_jpeg_gif_webp_bmp():
    for fmt in ("JPEG", "GIF", "WEBP", "BMP"):
        img = Image.new("RGB", (8, 8), (200, 100, 50))
        result = load_upload(encode(img, fmt), **LIMITS)
        assert result.source_format == fmt, fmt
        assert result.image.mode == "RGBA"


def test_rejects_garbage_with_png_magic():
    data = b"\x89PNG\r\n\x1a\n" + b"not really a png" * 10
    with pytest.raises(IntakeError) as exc:
        load_upload(data, **LIMITS)
    assert exc.value.code == "unsupported_format"


def test_rejects_non_image():
    with pytest.raises(IntakeError) as exc:
        load_upload(b"hello world", **LIMITS)
    assert exc.value.code == "unsupported_format"


def test_rejects_disallowed_format():
    # TIFF decodes fine with Pillow but is not on the allowlist
    data = encode(Image.new("RGB", (4, 4)), "TIFF")
    with pytest.raises(IntakeError) as exc:
        load_upload(data, **LIMITS)
    assert exc.value.code == "unsupported_format"


def test_rejects_oversize():
    data = make_png(8, 8)
    with pytest.raises(IntakeError) as exc:
        load_upload(data, max_bytes=len(data) - 1, max_pixels=40_000_000)
    assert exc.value.code == "too_large"


def test_rejects_too_many_pixels():
    data = make_png(2000, 2000, "L", 0)
    with pytest.raises(IntakeError) as exc:
        load_upload(data, max_bytes=20 * 1024 * 1024, max_pixels=1_000_000)
    assert exc.value.code == "too_many_pixels"


def test_preserves_alpha():
    result = load_upload(black_square_on_transparent(64, 16), **LIMITS)
    assert result.image.getpixel((0, 0))[3] == 0
    assert result.image.getpixel((32, 32)) == (0, 0, 0, 255)


def test_animated_gif_uses_first_frame():
    f0 = Image.new("P", (8, 8))
    f0.putpalette([255, 0, 0] + [0] * 765)
    f1 = Image.new("P", (8, 8))
    f1.putpalette([0, 0, 255] + [0] * 765)
    buf = io.BytesIO()
    f0.save(buf, "GIF", save_all=True, append_images=[f1], duration=100, loop=0)
    result = load_upload(buf.getvalue(), **LIMITS)
    assert result.image.getpixel((0, 0))[:3] == (255, 0, 0)


def test_jpeg_exif_orientation_is_applied():
    img = Image.new("RGB", (30, 10), (0, 128, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW on display
    data = encode(img, "JPEG", exif=exif)
    result = load_upload(data, **LIMITS)
    assert (result.width, result.height) == (10, 30)


def test_source_bytes_are_kept():
    data = make_png()
    assert load_upload(data, **LIMITS).source_bytes == data
