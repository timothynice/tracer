"""Refit-merge: join adjacent smooth regions that one real fill explains.

The merge stage judges unions with polynomial proxies (planar / quadratic).
A Gaussian glow or an off-centre radial gradient is poorly approximated by a
quadratic, so it fragments into rings even though a single multi-stop radial
gradient would reproduce it. Here, for every adjacent pair of *smooth* regions
(one whose fill is a gradient, or whose solid fit is poor) with no visible
edge between them, the actual fill is fitted to the union; if it is as good
as the parts, they are merged.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
from skimage.segmentation import relabel_sequential

from studi0trace.engines.vexel.fills import Fill, FitParams, Solid, fit_fill
from studi0trace.engines.vexel.merge import adjacency, boundary_ridges
from studi0trace.engines.vexel.weights import interior

FitFn = Callable[[np.ndarray], tuple[Fill, float]]

# Fewest pixel pairs a boundary steeper than the edge limit needs before its
# ridge share can lift the veto: "a ridge at more than half its pairs" is a
# statistic, and over fewer than two dozen pairs it is decided by two or three
# pixels, both sides being other shards within reach. A ramp the partition cut
# through one smooth field is a long cut (a glow's core against its halo: 640
# pairs); the shards of an upsampled ring meet along four to nine, read as
# ramps at every second one, and thin-mark-128 came out as 38 shapes with no
# stroke instead of 19 with one.
RAMP_MIN_PAIRS = 24


def fill_rms(fill: Fill, xs: np.ndarray, ys: np.ndarray, rgba255: np.ndarray, w: np.ndarray) -> float:
    pred = fill.evaluate(xs, ys)
    err = ((pred - rgba255) ** 2).sum(axis=1)
    return float(np.sqrt(np.sum(w * err) / max(np.sum(w) * 4.0, 1e-12)))


def refine_merge(
    labels: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
    rgba255: np.ndarray,
    grad: np.ndarray,
    fills: dict[int, Fill],
    params: FitParams,
    edge_limit: float,
    max_attempts: int = 60,
    rescued: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[int, Fill], bool]:
    """Returns (labels, fills, changed). Fills of merged regions are refitted.

    `rescued` marks the pixels the rescue promoted: a region most of whose
    pixels are among them is a rescued band, and a steep ramp between it and
    its ground is never joined here, whatever the union fit says — it is the
    shadow stage's to explain first (the bands of a drop shadow beside a flat
    backdrop joined it as radials and no filter was left to find; radii-512
    lost all four of its shadows). A ramp the partition cut through one
    smooth field, a glow's core against its halo, is joined as before."""
    if not params.gradients:
        return labels, fills, False
    band_labels: set[int] = set()
    if rescued is not None:
        counts = np.bincount(labels.ravel())
        inside = np.bincount(labels.ravel(), weights=rescued.ravel().astype(np.float64), minlength=counts.size)
        band_labels = {int(k) for k in range(1, counts.size) if counts[k] and 2.0 * inside[k] > counts[k]}

    # A fill is fitted to its region's core (`weights.fill_core`), so it is
    # judged there too. Scored over every pixel, a small part's own rim — which
    # its fill does not try to explain — inflated its error, and a union merely
    # as bad as a rim-inflated part passed "as good as the parts".
    def core_rms(fill: Fill, mask: np.ndarray) -> float:
        w, core = interior(mask)
        return fill_rms(fill, xs[mask][core], ys[mask][core], rgba255[mask][core], w[core])

    def fit(mask: np.ndarray) -> tuple[Fill, float]:
        w, core = interior(mask)
        f = fit_fill(xs[mask], ys[mask], rgba255[mask], params, weights=w, core=core)
        return f, core_rms(f, mask)

    rms: dict[int, float] = {}
    smooth: set[int] = set()
    for lab, fill in fills.items():
        m = labels == lab
        rms[lab] = core_rms(fill, m)
        if not isinstance(fill, Solid) or rms[lab] > params.tol:
            smooth.add(lab)
    if len(smooth) < 1:
        return labels, fills, False

    # No join across a visible edge: a boundary steeper than `edge_limit` that
    # is a ridge of the discontinuity (`boundary_ridges`: a step, at more than
    # half of its pairs). A glow's or a shadow's own slope can be as steep,
    # and is as steep beside the boundary as on it — a ramp the partition cut
    # somewhere along it, which the union fit is the judge of (glow-512-ds
    # under Detailed: 2.44 against a limit of 2.1, the union radial fitting
    # better than either part, and the core drawn as a second shape).
    # A pair let through only because its boundary is a ramp is held to a
    # higher bar: one fill has to explain the union at least as well as two
    # explain the parts, with no tolerance to spare. Within the tolerance
    # alone, a drop shadow's band joined its flat backdrop as a radial that
    # was "good enough", and there was no band left for the shadow stage to
    # read as a filter (tests/test_vexel_shadows.py, the card on a backdrop);
    # the glow's union is better than either of its parts.
    edges = adjacency(labels, grad)
    ridges = boundary_ridges(labels, grad)
    pairs = [
        (cnt, a, b, gsum / cnt > edge_limit) for (a, b), (cnt, gsum) in edges.items()
        if (a in smooth or b in smooth) and cnt > 0
        and (gsum / cnt <= edge_limit
             or (cnt >= RAMP_MIN_PAIRS and ridges[(a, b)] <= 0.5 and a not in band_labels and b not in band_labels))
    ]
    pairs.sort(key=lambda t: -t[0])  # longest shared boundary first

    changed = False
    attempts = 0
    alias: dict[int, int] = {}

    def root(i: int) -> int:
        while i in alias:
            i = alias[i]
        return i

    for _, a, b, steep in pairs:
        if attempts >= max_attempts:
            break
        a, b = root(a), root(b)
        if a == b:
            continue
        attempts += 1
        union = (labels == a) | (labels == b)
        f_union, r_union = fit(union)
        if isinstance(f_union, Solid) and not (isinstance(fills[a], Solid) and isinstance(fills[b], Solid)):
            continue  # a gradient collapsing to a solid is not "explained"
        bar = max(rms[a], rms[b]) if steep else max(params.tol, 1.15 * max(rms[a], rms[b]))
        if r_union <= bar:
            labels = np.where(labels == b, a, labels)
            fills[a] = f_union
            rms[a] = r_union
            fills.pop(b, None)
            rms.pop(b, None)
            alias[b] = a
            changed = True

    if changed:
        old_ids = sorted(fills)
        labels, fwd, _ = relabel_sequential(labels)
        fills = {int(fwd[i]): fills[i] for i in old_ids}
        labels = labels.astype(np.int32)
    return labels, fills, changed
