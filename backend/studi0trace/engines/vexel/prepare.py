"""Stage 1: colour spaces and alpha inpainting."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from skimage.color import rgb2lab

ALPHA_FEATURE_SCALE = 100.0  # alpha 0..1 → 0..100, comparable to L*


@dataclass(frozen=True)
class Prepared:
    rgb: np.ndarray  # float32 (H, W, 3) 0..255, inpainted where alpha == 0
    alpha: np.ndarray  # float32 (H, W) 0..1
    lab: np.ndarray  # float32 (H, W, 3)
    features: np.ndarray  # float32 (H, W, 4) = [L*, a*, b*, 100·alpha]

    @property
    def height(self) -> int:
        return self.alpha.shape[0]

    @property
    def width(self) -> int:
        return self.alpha.shape[1]


# The alpha (0..255) under which a pixel's colour may be noise rather than its
# own. A rasteriser works premultiplied and unpremultiplies for the file, so
# under alpha a the straight colour is quantised to steps of 255/a per channel:
# at alpha 1 it is 0 or 255, at 2 it is 0, 128 or 255 (the white, magenta,
# black and (128,128,255) under the soft shadow of a fluent-color emoji). Over
# the corpus and the held-out set the colour difference between a pixel and
# its neighbours of higher alpha runs at about 45/a ΔE: 46 at alpha 1, 18 at
# 2, 11 at 4, 6 at 8, 3.4 at 16, 1.5 at 32. The partition seeds where the
# discontinuity is under 8 ΔE per pixel (`seed_mask`), so below alpha 8 a halo
# of it is all ridge and no seed, and the watershed floods it from whichever
# shape it rings: the balloon's outline was placed at the halo's far end, its
# fill fitted to fade out, and the halo carved back out as an "invisible"
# region that took the shape's real rim with it.
COLOUR_ALPHA_FLOOR = 8
# Such noise is known by its grid: every channel of an unpremultiplied colour at
# alpha a is round(k·255/a), within a level of rounding either way. A colour off
# the grid is one a straight-alpha file wrote and means what it says — the tail
# of a synthetic alpha ramp beside a solid disc took the disc's colour when every
# faint pixel was inpainted, and the Lanczos ringing of a 2× upsample carries
# real mixtures at alpha 1–7.
GRID_TOL = 1.0


def unpremultiply_noise(rgb: np.ndarray, alpha255: np.ndarray, floor: int = COLOUR_ALPHA_FLOOR) -> np.ndarray:
    """Pixels under `floor` alpha whose colour lies on the 255/a grid: the
    quantisation an unpremultiplied file leaves, not a colour of their own.
    `alpha255` is the file's alpha, 0..255; alpha 0 is not this (it is inpainted
    regardless)."""
    faint = (alpha255 > 0) & (alpha255 < floor)
    if not faint.any():
        return faint
    a = alpha255[faint].astype(np.float64)
    step = 255.0 / a
    c = rgb[faint].astype(np.float64)
    k = np.rint(c / step[:, None])
    on_grid = (np.abs(c - k * step[:, None]) <= GRID_TOL).all(axis=1)
    out = np.zeros(alpha255.shape, bool)
    out[faint] = on_grid
    return out


def inpaint_transparent(rgb: np.ndarray, alpha: np.ndarray, alpha255: np.ndarray | None = None,
                        floor: int = COLOUR_ALPHA_FLOOR) -> np.ndarray:
    """Replace RGB under alpha == 0, and under alpha below `floor`/255 where the
    colour is unpremultiply noise, with the nearest other pixel's colour.

    PNG encoders store arbitrary (often black) RGB under transparent pixels,
    and a premultiplied pipeline leaves quantisation noise under nearly
    transparent ones (`COLOUR_ALPHA_FLOOR`, `unpremultiply_noise`); letting
    that leak into gradient fits or edge detection would be wrong.
    """
    invisible = alpha <= 0.0
    if alpha255 is not None:
        invisible = invisible | unpremultiply_noise(rgb, alpha255, floor)
    if not invisible.any() or invisible.all():
        return rgb
    _, (rows, cols) = ndimage.distance_transform_edt(invisible, return_indices=True)
    out = rgb.copy()
    out[invisible] = rgb[rows[invisible], cols[invisible]]
    return out


def prepare(rgba: np.ndarray) -> Prepared:
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError("prepare() expects an (H, W, 4) RGBA array")
    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3].astype(np.float32) / 255.0
    rgb = inpaint_transparent(rgb, alpha, rgba[..., 3])
    lab = rgb2lab(rgb / 255.0).astype(np.float32)
    # Colour is kept at full strength everywhere (inpainted under transparency):
    # anti-aliased rims against transparency then differ from the ink only in
    # alpha and stay attached to it. The nearest-colour seams this leaves deep
    # inside transparent areas produce invisible fragments, which the engine
    # collapses into a single transparent region after fitting.
    features = np.concatenate([lab, (alpha * ALPHA_FEATURE_SCALE)[..., None]], axis=-1).astype(np.float32)
    return Prepared(rgb=rgb, alpha=alpha, lab=lab, features=features)
