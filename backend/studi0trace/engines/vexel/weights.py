"""Interior weights: how far a region pixel is from the region's boundary.

Boundary pixels are anti-aliasing mixtures of two fills, so a region's own
colour has to be read from its interior. The weight is the distance into the
region, clamped to [0.5, 3] px, normalised and **squared**.

The square matters. With a linear falloff a one-pixel rim still carries a
quarter of its weight, and on a compact shape there are enough rim pixels for
that quarter to decide things: an opaque disc came out as
``fill-opacity="0.998"`` because its own anti-aliasing dragged the fitted alpha
under the threshold, and a sub-pixel line inside a transparent field was partly
absorbed into that field's fitted colour, so the residual pass stopped seeing it
as an outlier and no longer rescued it. Sharpening the falloff is worth about
0.04 ΔE on the logo class and 0.03 on flat, and loses on none
(``docs/superpowers/specs/2026-09-20-vexel-rust-port-design.md``).

Computed on the region's bounding box, not the full frame, because it is called
once per region.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

REACH = 3.0  # distance at which a pixel counts fully as interior


def interior_weights(mask: np.ndarray) -> np.ndarray:
    """Weights for `mask`'s True pixels in row-major order (same order as mask[mask])."""
    rows = np.nonzero(mask.any(axis=1))[0]
    cols = np.nonzero(mask.any(axis=0))[0]
    if rows.size == 0:
        return np.zeros(0)
    r0, r1 = rows[0], rows[-1] + 1
    c0, c1 = cols[0], cols[-1] + 1
    crop = np.pad(mask[r0:r1, c0:c1], 1, mode="constant", constant_values=False)
    dist = ndimage.distance_transform_edt(crop)[1:-1, 1:-1]
    return ((np.clip(dist, 0.5, REACH) / REACH) ** 2)[mask[r0:r1, c0:c1]]
