"""Shared fixtures: small in-memory images so tests need no binary assets."""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw


def encode(img: Image.Image, fmt: str = "PNG", **kwargs) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kwargs)
    return buf.getvalue()


def make_png(width: int = 64, height: int = 64, mode: str = "RGBA", color=(0, 0, 0, 255)) -> bytes:
    return encode(Image.new(mode, (width, height), color))


def black_square_on_transparent(size: int = 64, inset: int = 16) -> bytes:
    """A black square centred on a fully transparent canvas."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([inset, inset, size - inset - 1, size - inset - 1], fill=(0, 0, 0, 255))
    return encode(img)


def two_colour_image(size: int = 64) -> bytes:
    """Left half red, right half blue, opaque."""
    img = Image.new("RGBA", (size, size), (255, 0, 0, 255))
    ImageDraw.Draw(img).rectangle([size // 2, 0, size - 1, size - 1], fill=(0, 0, 255, 255))
    return encode(img)


@pytest.fixture
def png_bytes() -> bytes:
    return black_square_on_transparent()


@pytest.fixture
def two_colour_png() -> bytes:
    return two_colour_image()
