"""Shared fixtures: small in-memory images so tests need no binary assets."""
from __future__ import annotations

import io

import numpy as np
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


def noisy_halo_disc(size: int = 96, radius: float = 30.0, rgb=(218, 218, 218)) -> np.ndarray:
    """An anti-aliased light grey disc on a transparent canvas, ringed by the
    faint halo a soft shadow leaves when the file is unpremultiplied: alpha 2
    for seven pixels out, then alpha 1 for seven more, with the colour under
    it the quantisation noise such a file carries (0, 128 or 255 per channel).
    Returns RGBA (H, W, 4) uint8."""
    ys, xs = np.mgrid[0:size, 0:size]
    d = np.hypot(xs + 0.5 - size / 2, ys + 0.5 - size / 2)
    cover = np.clip(radius + 0.5 - d, 0.0, 1.0)
    rgba = np.zeros((size, size, 4), np.uint8)
    rgba[..., :3] = rgb
    rgba[..., 3] = np.round(cover * 255).astype(np.uint8)
    halo = (cover == 0) & (d < radius + 14)
    rng = np.random.default_rng(7)
    inner = halo & (d < radius + 7)  # alpha 2: 0, 128 or 255 per channel
    outer = halo & ~inner  # alpha 1: 0 or 255 only
    rgba[inner, :3] = rng.choice(np.array([0, 128, 255], np.uint8), size=(int(inner.sum()), 3))
    rgba[outer, :3] = rng.choice(np.array([0, 255], np.uint8), size=(int(outer.sum()), 3))
    rgba[inner, 3] = 2
    rgba[outer, 3] = 1
    return rgba


@pytest.fixture
def png_bytes() -> bytes:
    return black_square_on_transparent()


@pytest.fixture
def two_colour_png() -> bytes:
    return two_colour_image()
