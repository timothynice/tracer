"""Stage 3: model-aware greedy region merging on the region adjacency graph."""
from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from skimage.segmentation import relabel_sequential

from studi0trace.engines.vexel import stats as st


@dataclass(frozen=True)
class MergeParams:
    detail: float = 8.0  # merge when the effective colour distance is below this (ΔE-like)
    gradients: bool = True
    # A shared boundary whose mean gradient exceeds edge_veto · detail is a real
    # edge: never merge across it, however well a gradient model would "explain"
    # the union. Scharr + smoothing turn a step of D into a ridge of ≈ 0.6·D.
    edge_veto: float = 0.6

    @property
    def mu(self) -> float:
        """Per-pixel penalty unit for model parameters: a planar fit must cut
        MSE by 2·mu, a quadratic by 5·mu, to be preferred."""
        return (self.detail / 2.0) ** 2 / 4.0


def adjacency(labels: np.ndarray, grad: np.ndarray | None = None) -> dict[tuple[int, int], list[float]]:
    """{(a, b): [boundary_pixels, boundary_gradient_sum]} for a < b."""
    pairs = []
    weights = []
    for la, lb, ga, gb in (
        (labels[:, :-1], labels[:, 1:], None if grad is None else grad[:, :-1], None if grad is None else grad[:, 1:]),
        (labels[:-1, :], labels[1:, :], None if grad is None else grad[:-1, :], None if grad is None else grad[1:, :]),
    ):
        m = la != lb
        a = la[m].astype(np.int64)
        b = lb[m].astype(np.int64)
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        pairs.append(lo * (labels.max() + 1) + hi)
        weights.append(np.zeros(a.size) if grad is None else 0.5 * (ga[m] + gb[m]))
    key = np.concatenate(pairs)
    w = np.concatenate(weights)
    if key.size == 0:
        return {}
    uniq, inv = np.unique(key, return_inverse=True)
    counts = np.bincount(inv)
    gsum = np.bincount(inv, weights=w)
    k = labels.max() + 1
    return {(int(u // k), int(u % k)): [float(c), float(g)] for u, c, g in zip(uniq, counts, gsum)}


# A crisp edge is a real boundary however small its step. `edge_veto` reads
# the boundary's discontinuity against `detail`, so a step under ~detail·1.7
# (2.9 ΔE at Balanced) was never an edge, and a gradient model merged sixteen
# tiles 2.9 ΔE apart into two shapes. Judged against its surroundings instead
# it is unmistakable: the boundary is at least `EDGE_PROMINENCE` times the
# median discontinuity inside either region (a clean flat field's is 0.0, a
# gentle ramp's 0.1-0.2, JPEG's 0.5-1.5, which is why JPEG's block edges pass
# nothing here), at least `EDGE_FLOOR`, and a ridge at more than half its
# pairs (`partition.NECK_RIDGE` against the pixels `NECK_REACH` to either
# side, as `rejoin_ramps` reads a step). Across such an edge only colours
# within `EDGE_SAME` merge, as solids. Surveyed over every adjacent pair of
# both test sets: no JPEG pair and six downsampled pairs qualify, 500-odd
# pairs on clean renders do, all of them crisp steps the truth draws.
EDGE_PROMINENCE = 8.0
EDGE_FLOOR = 0.75
EDGE_SAME = 2.0


def ridge_pairs(labels: np.ndarray, grad: np.ndarray) -> dict[tuple[int, int], int]:
    """{(a, b): boundary pairs that are a ridge} for a < b, the pairs `adjacency` counts."""
    from studi0trace.engines.vexel.partition import NECK_REACH, NECK_RIDGE

    h, w = labels.shape
    g = grad.astype(np.float64)  # the Rust reads the discontinuity in f64; a float32 product flips ties
    keys, hits = [], []
    for axis in (1, 0):
        la, lb = (labels[:, :-1], labels[:, 1:]) if axis == 1 else (labels[:-1, :], labels[1:, :])
        m = la != lb
        ys, xs = np.nonzero(m)
        a, b = la[m].astype(np.int64), lb[m].astype(np.int64)
        if axis == 1:
            centre = 0.5 * (g[ys, xs] + g[ys, xs + 1])
            before, after = g[ys, np.maximum(xs - NECK_REACH, 0)], g[ys, np.minimum(xs + 1 + NECK_REACH, w - 1)]
        else:
            centre = 0.5 * (g[ys, xs] + g[ys + 1, xs])
            before, after = g[np.maximum(ys - NECK_REACH, 0), xs], g[np.minimum(ys + 1 + NECK_REACH, h - 1), xs]
        keys.append(np.minimum(a, b) * (labels.max() + 1) + np.maximum(a, b))
        hits.append((centre >= NECK_RIDGE * np.maximum(before, after)).astype(np.float64))
    key = np.concatenate(keys)
    if key.size == 0:
        return {}
    uniq, inv = np.unique(key, return_inverse=True)
    cnt = np.bincount(inv, weights=np.concatenate(hits))
    k = labels.max() + 1
    return {(int(u // k), int(u % k)): int(c) for u, c in zip(uniq, cnt)}


def interior_floor(labels: np.ndarray, grad: np.ndarray) -> np.ndarray:
    """Per label, the lower-middle median discontinuity over its pixels more
    than a pixel from any label change (over its whole self if it has none)."""
    from studi0trace.engines.vexel.rescue import boundary_band

    k = int(labels.max()) + 1
    out = np.zeros(k)
    flat = labels.ravel().astype(np.int64)
    g = grad.ravel().astype(np.float64)
    seen = np.zeros(k, bool)
    for sel in (~boundary_band(labels).ravel(), np.ones(flat.size, bool)):
        sel = sel & ~seen[flat]
        order = np.lexsort((g[sel], flat[sel]))
        ms, gs = flat[sel][order], g[sel][order]
        ids, first, n = np.unique(ms, return_index=True, return_counts=True)
        out[ids] = gs[first + (n - 1) // 2]
        seen[ids] = True
    return out


def merge_regions(labels: np.ndarray, features: np.ndarray, params: MergeParams, grad: np.ndarray | None = None) -> np.ndarray:
    """Greedy merging by `stats.merge_distance` until no pair is below `params.detail`.

    Returns compact int32 labels 1..K'.
    """
    height, width = labels.shape
    xn, yn, _, _ = st.normalised_coords(height, width)
    n_ch = features.shape[-1]
    stats = st.accumulate(labels, xn, yn, features)
    k = stats.shape[0]
    cost, _ = st.region_cost(stats, n_ch, params.mu, params.gradients)

    edges = adjacency(labels, grad)
    ridges = ridge_pairs(labels, grad) if grad is not None else {}
    floor = interior_floor(labels, grad) if grad is not None else np.zeros(k)
    nbrs: dict[int, set[int]] = defaultdict(set)
    edge_stats: dict[tuple[int, int], list[float]] = {}
    for (a, b), cg in edges.items():
        nbrs[a].add(b)
        nbrs[b].add(a)
        edge_stats[(min(a, b), max(a, b))] = [cg[0], cg[1], float(ridges.get((min(a, b), max(a, b)), 0))]

    def boundary_grad(a: int, b: int) -> float:
        cnt, gsum, _r = edge_stats.get((min(a, b), max(a, b)), [0.0, 0.0, 0.0])
        return gsum / cnt if cnt > 0 else 0.0

    def prominent(a: int, b: int) -> bool:
        cnt, gsum, r = edge_stats.get((min(a, b), max(a, b)), [0.0, 0.0, 0.0])
        if cnt <= 0:
            return False
        bg = gsum / cnt
        return bg >= EDGE_FLOOR and bg >= EDGE_PROMINENCE * max(floor[a], floor[b]) and 2.0 * r > cnt

    veto = params.edge_veto * params.detail if grad is not None else np.inf

    parent = np.arange(k)
    version = np.zeros(k, dtype=np.int64)
    alive = np.ones(k, dtype=bool)
    alive[0] = False

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return int(i)

    def distance(a: int, b: int) -> float:
        union = stats[a] + stats[b]
        na, nb = stats[a, 0], stats[b, 0]
        cu, _ = st.region_cost(union, n_ch, params.mu, params.gradients)
        d_best = st.merge_distance(float(cu), float(cost[a]), float(cost[b]), na, nb)
        strong = boundary_grad(a, b) > veto
        if params.gradients and (strong or prominent(a, b)):
            # A real edge runs between them. Two pieces of the same flat colour
            # (a stroke split by the watershed) may still merge; a gradient model
            # must not be allowed to "explain" a hard step across a visible edge.
            cu_solid, _ = st.region_cost(union, n_ch, params.mu, allow_gradients=False)
            ca_solid, _ = st.region_cost(stats[a], n_ch, params.mu, allow_gradients=False)
            cb_solid, _ = st.region_cost(stats[b], n_ch, params.mu, allow_gradients=False)
            d_solid = st.merge_distance(float(cu_solid), float(ca_solid), float(cb_solid), na, nb)
            # Across a visible edge only near-identical flat colours merge (half the
            # tolerance); pieces of one stroke pass, adjacent palette tiles do not.
            # Across an edge that is only prominent, `EDGE_SAME` at the most.
            limit = 0.5 * params.detail if strong else min(0.5 * params.detail, EDGE_SAME)
            return d_solid if d_solid < limit else np.inf
        return d_best

    heap: list[tuple[float, int, int, int, int]] = []
    for a, b in edges:
        d_ab = distance(a, b)
        if np.isfinite(d_ab):
            heapq.heappush(heap, (d_ab, version[a], version[b], a, b))

    while heap:
        d, va, vb, a, b = heapq.heappop(heap)
        if d >= params.detail:
            break
        if not (alive[a] and alive[b]) or version[a] != va or version[b] != vb:
            continue
        # merge b into a
        stats[a] += stats[b]
        floor[a] = max(floor[a], floor[b])
        cost[a], _ = st.region_cost(stats[a], n_ch, params.mu, params.gradients)
        alive[b] = False
        parent[b] = a
        version[a] += 1
        for c in nbrs.pop(b, set()):
            nbrs[c].discard(b)
            if c != a:
                nbrs[a].add(c)
                nbrs[c].add(a)
                bc = edge_stats.pop((min(b, c), max(b, c)), [0.0, 0.0, 0.0])
                ac = edge_stats.setdefault((min(a, c), max(a, c)), [0.0, 0.0, 0.0])
                ac[0] += bc[0]
                ac[1] += bc[1]
                ac[2] += bc[2]
        edge_stats.pop((min(a, b), max(a, b)), None)
        for c in nbrs[a]:
            d_ac = distance(a, c)
            if np.isfinite(d_ac):
                heapq.heappush(heap, (d_ac, version[a], version[c], a, c))

    roots = np.array([find(i) for i in range(k)])
    merged = roots[labels]
    merged, _, _ = relabel_sequential(merged)
    return merged.astype(np.int32)
