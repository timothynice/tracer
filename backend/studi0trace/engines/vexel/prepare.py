"""Stage 1: colour spaces and alpha inpainting."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from skimage.color import rgb2lab

ALPHA_FEATURE_SCALE = 100.0  # alpha 0..1 → 0..100, comparable to L*
# 8-bit alpha below which a pixel's stored colour is not believed: straight
# alpha quantises the colour to ±128/alpha levels, ±4 here. Over the corpus and
# the held-out set the median error of a pixel's colour against the nearest
# solid pixel's is 68/44/30/11/4/3 levels for alpha 1–3/4–7/8–15/16–31/32–63/
# 64–127 — the quantisation, and at 32 it is inside the fill tolerance.
INPAINT_ALPHA = 32


@dataclass(frozen=True)
class Prepared:
    rgb: np.ndarray  # float32 (H, W, 3) 0..255, inpainted below INPAINT_ALPHA
    alpha: np.ndarray  # float32 (H, W) 0..1
    lab: np.ndarray  # float32 (H, W, 3)
    features: np.ndarray  # float32 (H, W, 4) = [L*, a*, b*, 100·alpha]

    @property
    def height(self) -> int:
        return self.alpha.shape[0]

    @property
    def width(self) -> int:
        return self.alpha.shape[1]


def inpaint_transparent(rgb: np.ndarray, alpha8: np.ndarray) -> np.ndarray:
    """Replace the RGB of every pixel below INPAINT_ALPHA with the colour of the
    nearest pixel at or above it.

    PNG encoders store arbitrary (often black) RGB under transparent pixels;
    letting that leak into gradient fits or edge detection would be wrong. A
    nearly transparent pixel is no better: 8-bit straight alpha stores its
    colour to ±128/alpha levels, and a resampled asset rings every edge with
    alpha 1–15 pixels whose colour is noise (G=255 at alpha 1 beside a green
    ink). Inpainted from those, the transparent field carried seams of that
    noise with a gradient the size of a real edge, and the partition read
    them: a triangle's sides came out serrated every staircase step, and a 2 px
    ring was swallowed into the canvas and came back as 39 fragments. An image
    with nothing at INPAINT_ALPHA (a faint watermark) is inpainted from
    whatever has alpha at all, as before.
    """
    sources = alpha8 >= INPAINT_ALPHA
    if not sources.any():
        sources = alpha8 > 0
    if sources.all() or not sources.any():
        return rgb
    targets = ~sources
    _, (rows, cols) = ndimage.distance_transform_edt(targets, return_indices=True)
    out = rgb.copy()
    out[targets] = rgb[rows[targets], cols[targets]]
    return out


def prepare(rgba: np.ndarray) -> Prepared:
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError("prepare() expects an (H, W, 4) RGBA array")
    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3].astype(np.float32) / 255.0
    rgb = inpaint_transparent(rgb, rgba[..., 3])
    lab = rgb2lab(rgb / 255.0).astype(np.float32)
    # Colour is kept at full strength everywhere (inpainted under transparency):
    # anti-aliased rims against transparency then differ from the ink only in
    # alpha and stay attached to it. The nearest-colour seams this leaves deep
    # inside transparent areas produce invisible fragments, which the engine
    # collapses into a single transparent region after fitting.
    features = np.concatenate([lab, (alpha * ALPHA_FEATURE_SCALE)[..., None]], axis=-1).astype(np.float32)
    return Prepared(rgb=rgb, alpha=alpha, lab=lab, features=features)
