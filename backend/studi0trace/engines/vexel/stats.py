"""Region sufficient statistics and closed-form colour-model fits.

A region is summarised by position moments up to 4th order and colour-weighted
moments up to 2nd order. Adding two regions' vectors gives the union's
statistics, and the least-squares error of a solid / planar / quadratic colour
model follows from the normal equations — so region merging never revisits
pixels. Positions are normalised to [-1, 1] for conditioning.

Layout of a stats row (float64, length 15 + 6·C + C for C channels):
  [0:15]   Σ x^a y^b for (a,b) in MOMENT_ORDER
  [15:15+6C]  Σ c·φ  with φ = [1, x, y, x², xy, y²], channel-major
  [15+6C:]    Σ c²
"""
from __future__ import annotations

import numpy as np

MOMENT_ORDER: list[tuple[int, int]] = [
    (0, 0), (1, 0), (0, 1), (2, 0), (1, 1), (0, 2),
    (3, 0), (2, 1), (1, 2), (0, 3),
    (4, 0), (3, 1), (2, 2), (1, 3), (0, 4),
]
_MOMENT_INDEX = {ab: i for i, ab in enumerate(MOMENT_ORDER)}
PHI_DEGREES: list[tuple[int, int]] = MOMENT_ORDER[:6]
N_MOMENTS = 15

# A[i, j] = Σ φ_i φ_j  →  index into the moment vector
_NORMAL_IDX = np.array(
    [[_MOMENT_INDEX[(ai + aj, bi + bj)] for (aj, bj) in PHI_DEGREES] for (ai, bi) in PHI_DEGREES], dtype=np.intp
)

# Model "size" weights used in the penalty (solid, planar, quadratic).
MODEL_K = np.array([0.0, 2.0, 5.0])
MODEL_ORDER = (1, 3, 6)  # basis size per model


def normalised_coords(height: int, width: int) -> tuple[np.ndarray, np.ndarray, float, tuple[float, float]]:
    """Pixel-centre coordinates mapped to [-1, 1]. Returns (xs, ys, scale, (cx, cy))."""
    cx, cy = width / 2.0, height / 2.0
    scale = max(width, height) / 2.0
    ys, xs = np.mgrid[0:height, 0:width]
    xn = ((xs + 0.5) - cx) / scale
    yn = ((ys + 0.5) - cy) / scale
    return xn.astype(np.float64), yn.astype(np.float64), scale, (cx, cy)


def phi(xn: np.ndarray, yn: np.ndarray) -> np.ndarray:
    """Quadratic basis, shape (..., 6)."""
    return np.stack([np.ones_like(xn), xn, yn, xn * xn, xn * yn, yn * yn], axis=-1)


# Per pixel the moment/cross/square rows come to ~850 bytes of float64. Built
# for a whole image at once that is ~0.9 GB at one megapixel, which is what took
# the service down; chunking bounds it without changing the summation order, so
# the result stays bit-identical.
_CHUNK = 1 << 16


def accumulate(labels: np.ndarray, xn: np.ndarray, yn: np.ndarray, colours: np.ndarray) -> np.ndarray:
    """Stats rows for labels 0..K (row 0 unused). colours: (H, W, C)."""
    k = int(labels.max()) + 1
    n_ch = colours.shape[-1]
    lab = labels.ravel()
    x = xn.ravel()
    y = yn.ravel()
    col = colours.reshape(-1, n_ch)

    out = np.zeros((k, N_MOMENTS + 6 * n_ch + n_ch))
    for start in range(0, x.size, _CHUNK):
        stop = min(start + _CHUNK, x.size)
        xc = x[start:stop]
        yc = y[start:stop]
        cc = col[start:stop].astype(np.float64)

        moments = np.empty((stop - start, N_MOMENTS))
        for i, (a, b) in enumerate(MOMENT_ORDER):
            moments[:, i] = (xc**a) * (yc**b)
        basis = moments[:, :6]  # φ
        cross = (cc[:, :, None] * basis[:, None, :]).reshape(stop - start, -1)  # channel-major (c, φ)
        rows = np.concatenate([moments, cross, cc * cc], axis=1)
        np.add.at(out, lab[start:stop], rows)
    return out


def split_stats(stats: np.ndarray, n_ch: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(moments[..., 15], cross[..., C, 6], sq[..., C]) views of stats rows (..., L)."""
    moments = stats[..., :N_MOMENTS]
    cross = stats[..., N_MOMENTS : N_MOMENTS + 6 * n_ch].reshape(*stats.shape[:-1], n_ch, 6)
    sq = stats[..., N_MOMENTS + 6 * n_ch :]
    return moments, cross, sq


def _sse_for_order(moments: np.ndarray, cross: np.ndarray, sq: np.ndarray, order: int) -> np.ndarray:
    """Σ over channels of least-squares SSE for the first `order` basis functions. Batched over leading dims."""
    n = moments[..., 0]
    if order == 1:
        nn = np.maximum(n, 1e-12)[..., None]
        return np.maximum(np.sum(sq - np.where(n[..., None] > 0, cross[..., 0] ** 2 / nn, 0.0), axis=-1), 0.0)
    idx = _NORMAL_IDX[:order, :order]
    A = moments[..., idx]  # (..., order, order)
    B = np.swapaxes(cross[..., :order], -1, -2)  # (..., order, C)
    # Tikhonov-regularise very slightly for rank-deficient regions (e.g. collinear pixels)
    eye = np.eye(order) * 1e-9
    try:
        coef = np.linalg.solve(A + eye, B)
    except np.linalg.LinAlgError:
        coef = np.linalg.pinv(A + eye) @ B
    explained = np.sum(coef * B, axis=(-1, -2))
    return np.maximum(np.sum(sq, axis=-1) - explained, 0.0)


def model_sse(stats: np.ndarray, n_ch: int) -> np.ndarray:
    """SSE (summed over channels) for solid, planar, quadratic models. Shape (..., 3).

    Models the region is too small to support get +inf.
    """
    moments, cross, sq = split_stats(stats, n_ch)
    n = moments[..., 0]
    out = np.empty(stats.shape[:-1] + (3,))
    out[..., 0] = _sse_for_order(moments, cross, sq, 1)
    out[..., 1] = np.where(n >= 4, _sse_for_order(moments, cross, sq, 3), np.inf)
    out[..., 2] = np.where(n >= 10, _sse_for_order(moments, cross, sq, 6), np.inf)
    return out


def region_cost(stats: np.ndarray, n_ch: int, mu: float, allow_gradients: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """min over models of SSE + k·mu·n. Returns (cost, best_model_index)."""
    sse = model_sse(stats, n_ch)
    n = stats[..., 0]
    penalised = sse + MODEL_K * mu * n[..., None]
    if not allow_gradients:
        penalised[..., 1:] = np.inf
    best = np.argmin(penalised, axis=-1)
    return np.take_along_axis(penalised, best[..., None], axis=-1)[..., 0], best


def merge_distance(cost_union: float, cost_a: float, cost_b: float, n_a: float, n_b: float) -> float:
    """Effective colour distance (ΔE-like) implied by merging: equals the mean
    colour difference for two flat regions and ≈ 0 for pieces of one gradient."""
    increase = max(cost_union - cost_a - cost_b, 0.0)
    return float(np.sqrt(increase * (n_a + n_b) / max(n_a * n_b, 1e-12)))
