"""Interior weights: how far a region pixel is from the region's boundary.

Boundary pixels are anti-aliasing mixtures; weighting by distance into the
region (clamped to [0.5, 2] px, scaled to [0.25, 1]) lets fills and
visibility tests be driven by pure pixels. Computed on the region's bounding
box, not the full frame, because it is called once per region.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage


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
    return (np.clip(dist, 0.5, 2.0) / 2.0)[mask[r0:r1, c0:c1]]
