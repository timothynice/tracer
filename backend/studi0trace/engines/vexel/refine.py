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
) -> tuple[np.ndarray, dict[int, Fill], bool]:
    """Returns (labels, fills, changed). Fills of merged regions are refitted."""
    if not params.gradients:
        return labels, fills, False

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
        if (a in smooth or b in smooth) and cnt > 0 and (gsum / cnt <= edge_limit or ridges[(a, b)] <= 0.5)
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
