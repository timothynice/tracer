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
from scipy import ndimage
from skimage.segmentation import relabel_sequential

from studi0trace.engines.vexel.fills import Fill, FitParams, Solid, fit_fill
from studi0trace.engines.vexel.merge import adjacency

FitFn = Callable[[np.ndarray], tuple[Fill, float]]


def _weights(mask: np.ndarray) -> np.ndarray:
    return (np.clip(ndimage.distance_transform_edt(mask), 0.5, 2.0) / 2.0)[mask]


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

    def fit(mask: np.ndarray) -> tuple[Fill, float]:
        w = _weights(mask)
        f = fit_fill(xs[mask], ys[mask], rgba255[mask], params, weights=w)
        return f, fill_rms(f, xs[mask], ys[mask], rgba255[mask], w)

    rms: dict[int, float] = {}
    smooth: set[int] = set()
    for lab, fill in fills.items():
        m = labels == lab
        rms[lab] = fill_rms(fill, xs[m], ys[m], rgba255[m], _weights(m))
        if not isinstance(fill, Solid) or rms[lab] > params.tol:
            smooth.add(lab)
    if len(smooth) < 1:
        return labels, fills, False

    edges = adjacency(labels, grad)
    pairs = [
        (cnt, a, b) for (a, b), (cnt, gsum) in edges.items()
        if (a in smooth or b in smooth) and cnt > 0 and gsum / cnt <= edge_limit
    ]
    pairs.sort(key=lambda t: -t[0])  # longest shared boundary first

    changed = False
    attempts = 0
    alias: dict[int, int] = {}

    def root(i: int) -> int:
        while i in alias:
            i = alias[i]
        return i

    for _, a, b in pairs:
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
        if r_union <= max(params.tol, 1.15 * max(rms[a], rms[b])):
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
