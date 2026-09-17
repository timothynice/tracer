"""Stage 2: discontinuity map and the initial, edge-bounded partition.

A discontinuity is a *ridge* of the colour gradient, not merely a large
gradient: a steep but smooth ramp (a strong linear gradient) has a large,
flat gradient magnitude and must stay one region, while a hard edge has a
peak. Non-maximum suppression along the local gradient orientation separates
the two, exactly as in Canny.

Seeds for the watershed are the pixels at least one pixel away from any ridge
(dilating the ridge closes the one-pixel gaps NMS leaves at junctions, which
would otherwise let neighbouring regions leak into one seed) **plus** valley
pixels — local gradient minima between two ridges — so that a two-pixel
stroke keeps its own seed instead of being flooded by its surroundings.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage
from skimage.filters import scharr, scharr_h, scharr_v
from skimage.measure import label as cc_label
from skimage.segmentation import relabel_sequential, watershed

_CROSS = ndimage.generate_binary_structure(2, 1)


def discontinuity(features: np.ndarray, sigma: float = 0.7) -> np.ndarray:
    """Per-pixel colour change (ΔE-like units per pixel) across all feature channels."""
    g2 = np.zeros(features.shape[:2], dtype=np.float32)
    for c in range(features.shape[2]):
        g = scharr(features[..., c])
        g2 += g * g
    grad = np.sqrt(g2)
    return ndimage.gaussian_filter(grad, sigma) if sigma > 0 else grad


def ridges_and_valleys(features: np.ndarray, grad: np.ndarray, g_low: float) -> tuple[np.ndarray, np.ndarray]:
    """(ridge, valley) boolean masks from non-maximum / non-minimum tests along
    the structure-tensor gradient orientation."""
    jxx = np.zeros(grad.shape, np.float32)
    jyy = np.zeros(grad.shape, np.float32)
    jxy = np.zeros(grad.shape, np.float32)
    for c in range(features.shape[2]):
        gy = scharr_h(features[..., c])  # derivative along rows (y)
        gx = scharr_v(features[..., c])  # derivative along columns (x)
        jxx += gx * gx
        jyy += gy * gy
        jxy += gx * gy
    for j in (jxx, jyy, jxy):
        j[...] = ndimage.gaussian_filter(j, 1.0)
    theta = 0.5 * np.arctan2(2 * jxy, jxx - jyy)  # dominant gradient direction
    dx, dy = np.cos(theta), np.sin(theta)
    rows, cols = np.mgrid[0 : grad.shape[0], 0 : grad.shape[1]].astype(np.float32)
    g_plus = ndimage.map_coordinates(grad, [rows + dy, cols + dx], order=1, mode="nearest")
    g_minus = ndimage.map_coordinates(grad, [rows - dy, cols - dx], order=1, mode="nearest")
    # Prominence is judged 1.5 px out: a real edge (smoothed peak) has fallen to
    # ≈ 40 % there, while the periodic bumps that 8-bit quantisation puts on a
    # steep ramp are only a few percent high. Sampling 1.5 px also keeps a ridge
    # whose true peak falls between two pixels from vanishing.
    g_plus_far = ndimage.map_coordinates(grad, [rows + 1.5 * dy, cols + 1.5 * dx], order=1, mode="nearest")
    g_minus_far = ndimage.map_coordinates(grad, [rows - 1.5 * dy, cols - 1.5 * dx], order=1, mode="nearest")
    # Prominent against the near samples (a thin stroke's ridge: its far sample is
    # the stroke's other ridge) or against the far samples (a wide edge whose peak
    # straddles two pixels). Quantisation bumps fail both.
    prominent = (grad > 1.10 * np.maximum(g_plus, g_minus)) | (grad > 1.10 * np.maximum(g_plus_far, g_minus_far))
    ridge = (grad > g_low) & (grad >= g_plus) & (grad >= g_minus) & prominent
    valley = (grad <= g_plus) & (grad <= g_minus) & ~ridge
    return ridge, valley


def edge_mask(features: np.ndarray, grad: np.ndarray, g_low: float) -> np.ndarray:
    """Ridge pixels dilated by one (closes NMS gaps). Kept for callers that want the edge band."""
    ridge, _ = ridges_and_valleys(features, grad, g_low)
    return ndimage.binary_dilation(ridge, _CROSS)


def seed_mask(features: np.ndarray, grad: np.ndarray, g_low: float) -> np.ndarray:
    ridge, valley = ridges_and_valleys(features, grad, g_low)
    band = ndimage.binary_dilation(ridge, _CROSS)
    return ~band | valley


def _absorb_small(labels: np.ndarray, grad: np.ndarray, min_region: int, rounds: int = 3) -> np.ndarray:
    """Flood regions below `min_region` pixels from their neighbours along low gradient."""
    for _ in range(rounds):
        sizes = np.bincount(labels.ravel())
        small = sizes < min_region
        small[0] = False
        if not small.any():
            break
        markers = labels.copy()
        markers[small[labels]] = 0
        if markers.max() == 0:  # everything was small: keep the largest
            return np.ones_like(labels)
        labels = watershed(grad, markers)
    return labels


def initial_labels(grad: np.ndarray, features: np.ndarray | None = None, min_region: int = 6, g_low: float = 1.5) -> np.ndarray:
    """Watershed on the discontinuity map, seeded by the connected smooth areas.

    With `features` the seeds come from ridge/valley analysis (steep smooth ramps
    and thin strokes keep their seeds); without them the legacy `grad < g_low`
    threshold is used. Returns int32 labels 1..K covering every pixel.
    """
    smooth = seed_mask(features, grad, g_low) if features is not None else grad < g_low
    markers = cc_label(smooth, connectivity=1)
    if markers.max() > 0:
        sizes = np.bincount(markers.ravel())
        keep = sizes >= max(min_region, 2)
        keep[0] = False
        markers[~keep[markers]] = 0
    if markers.max() == 0:
        # No smooth area at all (tiny or extremely noisy image): seed from the
        # local minima of the gradient instead so watershed still has basins.
        minima = ndimage.minimum_filter(grad, size=3) == grad
        markers = cc_label(minima, connectivity=1)
        if markers.max() == 0:
            return np.ones(grad.shape, dtype=np.int32)
    labels = watershed(grad, markers)
    labels = _absorb_small(labels, grad, min_region)
    labels, _, _ = relabel_sequential(labels)
    return labels.astype(np.int32)
