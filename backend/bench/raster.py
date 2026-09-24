"""Rasterise SVG back to pixels (resvg) and load PNGs, all as RGBA uint8 arrays."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

# One renderer for the bench and for Auto's scoring (studi0trace.imaging.quality).
from studi0trace.imaging.quality import luminance, rasterize, render, to_rgb_on_white  # noqa: F401


def load_png(path: Path | str) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)
