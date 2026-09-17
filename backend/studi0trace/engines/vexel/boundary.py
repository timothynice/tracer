"""Stage 6: sub-pixel boundaries from anti-aliasing.

For a shape (a boolean mask), every pixel on the one-pixel ring inside and
outside the mask gets a *coverage* estimate: how much of that pixel the shape
covers, inferred by projecting the pixel's colour onto the segment between the
shape's fill and the neighbouring region's fill evaluated at that pixel. The
0.5 iso-contour of the resulting field is the shape's outline at sub-pixel
precision — where the artist drew it, not on a pixel border.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
from scipy import ndimage
from skimage.measure import find_contours

_CROSS = ndimage.generate_binary_structure(2, 1)

FillAt = Callable[[int, np.ndarray, np.ndarray], np.ndarray]
"""(label, xs, ys) -> rgba255 (N, 4): the reconstructed fill of `label` at pixel centres."""


def _outside_label(labels: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """For every pixel, the label of a 4-neighbour outside `mask` (0 where none)."""
    out = np.zeros(labels.shape, dtype=labels.dtype)
    outside = ~mask
    for shift_r, shift_c in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        shifted_labels = np.roll(labels, (shift_r, shift_c), axis=(0, 1))
        shifted_outside = np.roll(outside, (shift_r, shift_c), axis=(0, 1))
        # np.roll wraps; kill wrapped entries
        if shift_r == 1:
            shifted_outside[0, :] = False
        elif shift_r == -1:
            shifted_outside[-1, :] = False
        if shift_c == 1:
            shifted_outside[:, 0] = False
        elif shift_c == -1:
            shifted_outside[:, -1] = False
        take = (out == 0) & shifted_outside
        out[take] = shifted_labels[take]
    return out


def coverage_field(
    mask: np.ndarray,
    label: int,
    labels: np.ndarray,
    rgb: np.ndarray,
    alpha: np.ndarray,
    fill_at: FillAt,
) -> np.ndarray:
    """float32 (H, W): 1 inside, 0 outside, estimated coverage on the boundary ring."""
    field = mask.astype(np.float32)
    inner = mask & ~ndimage.binary_erosion(mask, _CROSS, border_value=1)
    outer = ndimage.binary_dilation(mask, _CROSS) & ~mask
    ring = inner | outer
    if not ring.any():
        return field

    rows, cols = np.nonzero(ring)
    xs = cols + 0.5
    ys = rows + 0.5
    pixel = np.concatenate([rgb[rows, cols], (alpha[rows, cols] * 255.0)[:, None]], axis=1)
    f_in = fill_at(label, xs, ys)

    # the "other side" label: for inner-ring pixels a 4-neighbour outside the mask,
    # for outer-ring pixels the pixel's own label
    other = np.where(mask[rows, cols], _outside_label(labels, mask)[rows, cols], labels[rows, cols])
    f_out = np.empty_like(f_in)
    for lab in np.unique(other):
        sel = other == lab
        if lab == 0:
            f_out[sel] = f_in[sel]  # no neighbour information: fall back to binary
        else:
            f_out[sel] = fill_at(int(lab), xs[sel], ys[sel])

    diff = f_in - f_out
    denom = np.sum(diff * diff, axis=1)
    proj = np.sum((pixel - f_out) * diff, axis=1)
    cov = np.where(denom > 1e-6, proj / np.maximum(denom, 1e-6), field[rows, cols])
    field[rows, cols] = np.clip(cov, 0.0, 1.0)
    return field


def contours(field: np.ndarray, min_area: float = 0.5) -> list[np.ndarray]:
    """Closed 0.5-level iso-contours as (N, 2) xy arrays in SVG pixel space."""
    padded = np.pad(field, 1, mode="constant", constant_values=0.0)
    out: list[np.ndarray] = []
    for c in find_contours(padded, 0.5):
        if len(c) < 4:
            continue
        xy = np.column_stack([c[:, 1] - 1.0 + 0.5, c[:, 0] - 1.0 + 0.5])
        if np.allclose(xy[0], xy[-1]):
            xy = xy[:-1]
        if len(xy) < 3:
            continue
        x, y = xy[:, 0], xy[:, 1]
        area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
        if area < min_area:
            continue
        out.append(xy)
    return out
