"""Rasterise SVG back to pixels (resvg) and load PNGs, all as RGBA uint8 arrays."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import resvg_py
from PIL import Image


def rasterize(svg: str, width: int, height: int) -> np.ndarray:
    """Render `svg` at exactly width×height. Returns (H, W, 4) uint8 RGBA."""
    png = resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height)
    img = Image.open(io.BytesIO(bytes(png))).convert("RGBA")
    if img.size != (width, height):  # resvg honours aspect ratio; force exact size
        img = img.resize((width, height), Image.Resampling.LANCZOS)
    return np.asarray(img, dtype=np.uint8)


def load_png(path: Path | str) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)


def to_rgb_on_white(rgba: np.ndarray) -> np.ndarray:
    """Alpha-composite over white. Returns (H, W, 3) uint8."""
    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    out = rgb * alpha + 255.0 * (1.0 - alpha)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def luminance(rgb: np.ndarray) -> np.ndarray:
    """Rec. 601 luma as float32 in 0..255."""
    r, g, b = (rgb[..., i].astype(np.float32) for i in range(3))
    return 0.299 * r + 0.587 * g + 0.114 * b
