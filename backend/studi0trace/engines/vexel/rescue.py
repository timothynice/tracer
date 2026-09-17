"""Residual rescue: recover small features swallowed by a larger region.

Thin strokes (≈ 1–3 px) and small high-contrast details can lose their seed
in the partition and end up inside a neighbouring region. After fills are
fitted they stand out as pixels whose colour disagrees strongly with their
region's fill. Those pixels — excluding the anti-aliasing ring along region
boundaries, which disagrees for a different reason — are grouped into
connected components and promoted to regions of their own.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage
from skimage.measure import label as cc_label
from skimage.segmentation import relabel_sequential

_CROSS = ndimage.generate_binary_structure(2, 1)


def boundary_band(labels: np.ndarray) -> np.ndarray:
    """Pixels within one pixel of a label change."""
    edge = np.zeros(labels.shape, bool)
    edge[:, :-1] |= labels[:, :-1] != labels[:, 1:]
    edge[:, 1:] |= labels[:, :-1] != labels[:, 1:]
    edge[:-1, :] |= labels[:-1, :] != labels[1:, :]
    edge[1:, :] |= labels[:-1, :] != labels[1:, :]
    return ndimage.binary_dilation(edge, _CROSS)


def rescue_features(
    labels: np.ndarray,
    residual: np.ndarray,
    threshold: float,
    min_region: int,
) -> tuple[np.ndarray, list[int]]:
    """Promote connected components of high-residual interior pixels to new regions.

    residual: per-pixel distance between the image and the fitted fill (0–255 units).
    Returns (new labels, ids of the rescued regions).
    """
    candidates = (residual > threshold) & ~boundary_band(labels)
    if not candidates.any():
        return labels, []
    # Components are formed on a one-pixel dilation so a stroke broken by
    # anti-aliasing gaps or junctions is rescued as one feature, not as shards.
    comps = cc_label(ndimage.binary_dilation(candidates, _CROSS), connectivity=2)
    comps[~candidates] = 0
    sizes = np.bincount(comps.ravel())
    keep = sizes >= max(min_region, 3)
    keep[0] = False
    if not keep.any():
        return labels, []
    out = labels.copy()
    next_id = int(labels.max()) + 1
    rescued: list[int] = []
    for comp in np.nonzero(keep)[0]:
        m = comps == comp
        # slightly grow the component so its own anti-aliasing pixels come along
        m = ndimage.binary_dilation(m, _CROSS) & ((residual > threshold * 0.5) | m)
        out[m] = next_id
        rescued.append(next_id)
        next_id += 1
    out, fwd, _ = relabel_sequential(out)
    rescued = [int(fwd[i]) for i in rescued]
    return out.astype(np.int32), rescued
