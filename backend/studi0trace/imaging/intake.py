"""Turn an untrusted upload into a validated RGBA `TraceInput`.

The client's declared content type is never consulted: the bytes are decoded
and the real format is taken from Pillow.
"""
from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

from studi0trace.engines.base import TraceInput

ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "GIF", "WEBP", "BMP"})


class IntakeError(Exception):
    """A rejected upload. `code` is stable and safe to send to clients."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def load_upload(data: bytes, *, max_bytes: int, max_pixels: int) -> TraceInput:
    if len(data) > max_bytes:
        raise IntakeError("too_large", f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")

    try:
        img = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise IntakeError("unsupported_format", "File is not a recognised image") from exc

    fmt = img.format or ""
    if fmt not in ALLOWED_FORMATS:
        raise IntakeError("unsupported_format", f"Unsupported image format: {fmt or 'unknown'}")

    # Pillow's open() is lazy: header only. Check dimensions before decoding pixels.
    if img.width * img.height > max_pixels:
        raise IntakeError("too_many_pixels", f"Image exceeds the {max_pixels // 1_000_000} megapixel limit")

    try:
        img.load()  # first frame for animated formats
        img = ImageOps.exif_transpose(img) or img
        rgba = img.convert("RGBA")
    except (OSError, SyntaxError, ValueError) as exc:
        raise IntakeError("corrupt_image", "Image data is corrupt or truncated") from exc

    return TraceInput(image=rgba, width=rgba.width, height=rgba.height, source_bytes=data, source_format=fmt)
