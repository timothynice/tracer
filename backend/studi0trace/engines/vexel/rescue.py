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


# How far an edge's rendering reaches into the regions either side: the
# support radius of the resamplers artwork goes through (bilinear 1, bicubic 2,
# Lanczos-3 3 px). Ringing further out than this is not an edge's doing.
EDGE_REACH = 3.0
# How far along the line from its own fill to the fill across the edge a pixel
# may sit and still be its own region's rendering of that edge: less than half
# way (it is still mostly its own colour), either side (a sharpened edge
# overshoots past its fill as well as blending towards its neighbour).
EDGE_SHARE = 0.5
# How far off that line, as a fraction of the edge's contrast, a mix may stray:
# a linear filter over a step between two colours only ever makes colours on
# the line through them; quantisation, the fills' own error and non-linear
# processing move them off it by a few percent of the step. A colour a fifth of
# the contrast off the line is a colour of its own.
EDGE_CONE = 0.1


def _disk_rings(reach: float) -> list[list[tuple[int, int]]]:
    """The offsets within `reach`, grouped by distance, nearest first."""
    r = int(np.floor(reach))
    rings: dict[int, list[tuple[int, int]]] = {}
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            d2 = dy * dy + dx * dx
            if d2 and d2 <= reach * reach:
                rings.setdefault(d2, []).append((dy, dx))
    return [rings[k] for k in sorted(rings)]


def edge_mix(labels: np.ndarray, colour: np.ndarray, pred: np.ndarray, alpha: np.ndarray, at: np.ndarray,
             reach: float = EDGE_REACH, share: float = EDGE_SHARE, cone: float = EDGE_CONE) -> np.ndarray:
    """Pixels of `at` whose colour the edge beside them explains.

    A pixel within `reach` of another region can differ from its own fill
    because it renders the edge between them: anti-aliasing blends it towards
    the colour across, and a sharpened or resampled edge rings, overshooting
    past its fill and back. Either way its colour stays on the line through the
    two fills and nearer its own: it projects onto the line from its own fill
    (`pred` at the pixel) to the other region's fill (`pred` at that region's
    pixel) at no more than `share` of the way, towards or away, and lies within
    `cone` times the edge's contrast of it. Distances are in the rescue
    residual's metric, colour weighted by the pixel's alpha.

    The edge a pixel renders is the one nearest it: only the regions at the
    least distance within reach are asked (any of them may explain it), not
    every region the reach happens to touch — a thin line the partition kept
    two pixels further along must not explain away the part of it that was
    swallowed. A least distance and an "any of" leave nothing to visiting order.
    """
    h, w = labels.shape
    out = np.zeros((h, w), bool)
    qy, qx = np.nonzero(at)
    if qy.size == 0:
        return out
    a = alpha[qy, qx][:, None]
    wt = np.concatenate([np.repeat(a, 3, axis=1), np.ones_like(a)], axis=1)
    d = (colour[qy, qx] - pred[qy, qx]) * wt
    own = labels[qy, qx]
    hit = np.zeros(qy.size, bool)
    unmet = np.ones(qy.size, bool)  # no other region yet at a smaller distance
    for ring in _disk_rings(reach):
        met = np.zeros(qy.size, bool)
        for dy, dx in ring:
            ny, nx = qy + dy, qx + dx
            inside = (ny >= 0) & (ny < h) & (nx >= 0) & (nx < w)
            ny, nx = np.clip(ny, 0, h - 1), np.clip(nx, 0, w - 1)
            other = unmet & inside & (labels[ny, nx] != own)
            if not other.any():
                continue
            met |= other
            v = (pred[ny, nx] - pred[qy, qx]) * wt
            vv = (v * v).sum(axis=1)
            t = (d * v).sum(axis=1) / np.where(vv > 0, vv, 1.0)
            off = ((d - t[:, None] * v) ** 2).sum(axis=1)
            hit |= other & (vv > 0) & (np.abs(t) <= share) & (off <= cone * cone * vv)
        unmet &= ~met
        if not unmet.any():
            break
    out[qy, qx] = hit
    return out


def rescue_features(
    labels: np.ndarray,
    residual: np.ndarray,
    threshold: float,
    min_region: int,
    explained: np.ndarray | None = None,
    core: np.ndarray | None = None,
) -> tuple[np.ndarray, list[int]]:
    """Promote connected components of high-residual interior pixels to new regions.

    residual: per-pixel distance between the image and the fitted fill (0–255 units).
    explained: pixels whose disagreement the edge beside them accounts for
    (`edge_mix`). A component the edge mostly explains is that edge's
    anti-aliasing or ringing, not a feature, unless what is left unexplained is
    a feature's worth of pixels on its own: the band a sharpened glyph edge
    leaves a pixel or two inside its outline disagrees with the glyph's fill by
    more than a low `detail` allows, and was rescued as a dozen slivers along
    every letter. "Mostly" is more than half, the same measure `EDGE_SHARE`
    puts on a single pixel; a component half of which no edge explains is
    given the benefit of the doubt, as before.
    core: every region's fill core (`weights.fill_core`). A fill is not fitted
    to its region's edge band, so it does not model the band — a sharpening
    halo two or three pixels inside a glyph's edge (a dark undershoot, a light
    rebound) disagrees with it for the edge's reason, not because a stroke was
    swallowed. A component is only evidence of a feature if it reaches into
    the core; edge-band pixels may belong to one that does (the darkest band of
    a drop shadow runs right up to its caster). Without this the rebound ring
    became a sliver region along every glyph at low `detail`.
    Returns (new labels, ids of the rescued regions).
    """
    candidates = (residual > threshold) & ~boundary_band(labels)
    if not candidates.any():
        return labels, []
    # Components are formed on a one-pixel dilation so a stroke broken by
    # anti-aliasing gaps or junctions is rescued as one feature, not as shards.
    comps = cc_label(ndimage.binary_dilation(candidates, _CROSS), connectivity=2)
    comps[~candidates] = 0
    floor = max(min_region, 3)
    sizes = np.bincount(comps.ravel())
    keep = sizes >= floor
    if explained is not None:
        rim = np.bincount(comps[candidates & explained], minlength=sizes.size)
        rest = sizes - rim
        keep &= (rest >= floor) | (rest >= rim)
    if core is not None:
        keep &= np.bincount(comps[core], minlength=sizes.size) > 0
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
