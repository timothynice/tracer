"""Stage 1: colour spaces and alpha inpainting."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from skimage.color import rgb2lab

ALPHA_FEATURE_SCALE = 100.0  # alpha 0..1 → 0..100, comparable to L*
# 8-bit alpha below which a pixel's stored colour is not believed on its own:
# straight alpha quantises the colour to ±128/alpha levels, ±4 here. Over the
# corpus and the held-out set the median error of a pixel's colour against the
# nearest solid pixel's is 68/44/30/11/4/3 levels for alpha 1–3/4–7/8–15/16–31/
# 32–63/64–127 — the quantisation, and at 32 it is inside the fill tolerance.
INPAINT_ALPHA = 32
# px; the neighbourhood (a (2·RIM_REACH+1)² square) whose alpha-weighted mean
# colour such a pixel takes (`settle_rim`): two pixels reaches through a
# resampled edge's noise band to the ink behind it.
RIM_REACH = 2


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


def settle_rim(rgb: np.ndarray, alpha8: np.ndarray) -> np.ndarray:
    """Give every pixel below INPAINT_ALPHA the alpha-weighted mean colour of
    the RIM_REACH neighbourhood round it.

    8-bit straight alpha stores a pixel's colour to ±128/alpha levels, and a
    resampled asset rings every edge with alpha 1–15 pixels whose colour is
    noise (G=255 at alpha 1 beside a green ink; over the corpus the median
    error against the nearest solid pixel is 68/44/30/11/4 levels for alpha
    1–3/4–7/8–15/16–31/32–63). Inpainted from those, the transparent field
    carried seams of that noise with a gradient the size of a real edge, and
    the partition read them: a triangle's sides came out serrated every
    staircase step, and a 2 px ring was swallowed into the canvas and came
    back as 39 fragments. The weighted mean is the premultiplied colour of the
    neighbourhood divided by its alpha: beside an ink it is the ink, and in a
    faint field (a shadow's halo, the tail of a fade) it is the field's own
    colour, which the nearest solid pixel — the caster — is not; taking that
    instead moved two shadows' outer bands to the caster's colour. Pixels with
    no alpha at all in reach keep what they have."""
    low = (alpha8 > 0) & (alpha8 < INPAINT_ALPHA)
    if not low.any():
        return rgb
    h, w = alpha8.shape
    a = np.pad(alpha8.astype(np.float64), RIM_REACH)
    c = np.pad(rgb.astype(np.float64), ((RIM_REACH, RIM_REACH), (RIM_REACH, RIM_REACH), (0, 0)))
    mass = np.zeros((h, w))
    premul = np.zeros((h, w, 3))
    # one offset at a time, in raster order: the Rust adds in the same order
    for dr in range(2 * RIM_REACH + 1):
        for dc in range(2 * RIM_REACH + 1):
            av = a[dr:dr + h, dc:dc + w]
            mass = mass + av
            premul = premul + av[..., None] * c[dr:dr + h, dc:dc + w]
    out = rgb.copy()
    fix = low & (mass > 0.0)
    out[fix] = (premul[fix] / mass[fix][:, None]).astype(rgb.dtype)
    return out


def inpaint_transparent(rgb: np.ndarray, alpha8: np.ndarray) -> np.ndarray:
    """Replace RGB under alpha == 0 with the nearest visible pixel's colour —
    that pixel's settled colour (`settle_rim`) where its alpha is below
    INPAINT_ALPHA. PNG encoders store arbitrary (often black) RGB under
    transparent pixels, and letting that into gradient fits or edge detection
    would be wrong; the field's colour is read from nearly transparent pixels
    only through their neighbourhood, so the noise a resampled edge carries
    there does not become seams across the field. Visible pixels keep the
    colour they have, noise and all: a faint halo beside a caster is its own
    black, not the caster's red, and a fade's tail is its own ramp."""
    invisible = alpha8 == 0
    if not invisible.any() or invisible.all():
        return rgb
    settled = settle_rim(rgb, alpha8)
    _, (rows, cols) = ndimage.distance_transform_edt(invisible, return_indices=True)
    out = rgb.copy()
    out[invisible] = settled[rows[invisible], cols[invisible]]
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
