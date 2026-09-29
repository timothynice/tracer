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


# The alpha (0..255) under which a pixel's colour is not its own. A rasteriser
# works premultiplied and unpremultiplies for the file, so under alpha a the
# straight colour is quantised to steps of 255/a per channel: at alpha 1 it is
# 0 or 255, at 2 it is 0, 128 or 255 (the white, magenta, black and
# (128,128,255) under the soft shadow of a fluent-color emoji). Over the corpus
# and the held-out set the colour difference between a pixel and its neighbours
# of higher alpha runs at about 45/a ΔE: 46 at alpha 1, 18 at 2, 11 at 4, 6 at
# 8, 3.4 at 16, 1.5 at 32. The partition seeds where the discontinuity is under
# 8 ΔE per pixel (`seed_mask`), so below alpha 8 a halo is all ridge and no
# seed, and the watershed floods it from whichever shape it rings: the balloon's
# outline was placed at the halo's far end, its fill fitted to fade out, and the
# halo carved back out as an "invisible" region that took the shape's real rim
# with it. Such a pixel's colour is read from the nearest pixel that shows,
# like the colour under alpha 0; its alpha is kept, and the visible weight of
# the colour it loses is at most 8/255 of the difference.
COLOUR_ALPHA_FLOOR = 8


def inpaint_transparent(rgb: np.ndarray, alpha: np.ndarray, floor: int = COLOUR_ALPHA_FLOOR) -> np.ndarray:
    """Replace RGB under alpha below `floor`/255 with the nearest pixel's colour
    whose alpha reaches it.

    PNG encoders store arbitrary (often black) RGB under transparent pixels,
    and quantisation noise under nearly transparent ones (`COLOUR_ALPHA_FLOOR`);
    letting that leak into gradient fits or edge detection would be wrong.
    `alpha` is float32 a/255, which scaled back in float32 is `a` exactly.
    """
    invisible = alpha * 255.0 < floor
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
    rgb = inpaint_transparent(rgb, alpha)
    lab = rgb2lab(rgb / 255.0).astype(np.float32)
    # Colour is kept at full strength everywhere (inpainted under transparency):
    # anti-aliased rims against transparency then differ from the ink only in
    # alpha and stay attached to it. The nearest-colour seams this leaves deep
    # inside transparent areas produce invisible fragments, which the engine
    # collapses into a single transparent region after fitting.
    features = np.concatenate([lab, (alpha * ALPHA_FEATURE_SCALE)[..., None]], axis=-1).astype(np.float32)
    return Prepared(rgb=rgb, alpha=alpha, lab=lab, features=features)
