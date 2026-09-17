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
    nbrs: dict[int, set[int]] = defaultdict(set)
    edge_stats: dict[tuple[int, int], list[float]] = {}
    for (a, b), cg in edges.items():
        nbrs[a].add(b)
        nbrs[b].add(a)
        edge_stats[(min(a, b), max(a, b))] = list(cg)

    def boundary_grad(a: int, b: int) -> float:
        cnt, gsum = edge_stats.get((min(a, b), max(a, b)), [0.0, 0.0])
        return gsum / cnt if cnt > 0 else 0.0

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
        if params.gradients and boundary_grad(a, b) > veto:
            # A real edge runs between them. Two pieces of the same flat colour
            # (a stroke split by the watershed) may still merge; a gradient model
            # must not be allowed to "explain" a hard step across a visible edge.
            cu_solid, _ = st.region_cost(union, n_ch, params.mu, allow_gradients=False)
            ca_solid, _ = st.region_cost(stats[a], n_ch, params.mu, allow_gradients=False)
            cb_solid, _ = st.region_cost(stats[b], n_ch, params.mu, allow_gradients=False)
            d_solid = st.merge_distance(float(cu_solid), float(ca_solid), float(cb_solid), na, nb)
            return d_solid if d_solid < params.detail else np.inf
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
        cost[a], _ = st.region_cost(stats[a], n_ch, params.mu, params.gradients)
        alive[b] = False
        parent[b] = a
        version[a] += 1
        for c in nbrs.pop(b, set()):
            nbrs[c].discard(b)
            if c != a:
                nbrs[a].add(c)
                nbrs[c].add(a)
                bc = edge_stats.pop((min(b, c), max(b, c)), [0.0, 0.0])
                ac = edge_stats.setdefault((min(a, c), max(a, c)), [0.0, 0.0])
                ac[0] += bc[0]
                ac[1] += bc[1]
        edge_stats.pop((min(a, b), max(a, b)), None)
        for c in nbrs[a]:
            d_ac = distance(a, c)
            if np.isfinite(d_ac):
                heapq.heappush(heap, (d_ac, version[a], version[c], a, c))

    roots = np.array([find(i) for i in range(k)])
    merged = roots[labels]
    merged, _, _ = relabel_sequential(merged)
    return merged.astype(np.int32)
