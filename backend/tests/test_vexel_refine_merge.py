"""`refine_merge` joins the fragments of one smooth fill. Its edge veto — no
join across a boundary whose mean discontinuity exceeds `edge_limit` — is
there so a gradient never "explains" a visible step; a glow's own slope is not
a step, and under a low `detail` it was vetoing the join of a glow's core with
its halo (glow-512-ds under Detailed: the union radial fitted better than either
part, and the core was drawn as a second shape with a wobbling circular edge)."""
from __future__ import annotations

import numpy as np

from studi0trace.engines.vexel.fills import FitParams, fit_fill
from studi0trace.engines.vexel.merge import adjacency
from studi0trace.engines.vexel.partition import discontinuity
from studi0trace.engines.vexel.prepare import prepare
from studi0trace.engines.vexel.refine import refine_merge
from studi0trace.engines.vexel.weights import interior

SIZE = 160
CUT = 34.0  # px; where the labels part the core from the halo


def _inputs(rgb: np.ndarray):
    rgba = np.concatenate([rgb, np.full(rgb.shape[:2] + (1,), 255)], axis=-1).astype(np.uint8)
    prep = prepare(rgba)
    grad = discontinuity(prep.features)
    ys, xs = np.mgrid[0:SIZE, 0:SIZE]
    xs, ys = xs.astype(np.float64) + 0.5, ys.astype(np.float64) + 0.5
    rgba255 = np.concatenate([prep.rgb, (prep.alpha * 255.0)[..., None]], axis=-1)
    return xs, ys, rgba255, grad


def _fitted(labels: np.ndarray, xs, ys, rgba255, params: FitParams) -> dict:
    fills = {}
    for lab in np.unique(labels):
        m = labels == lab
        w, core = interior(m)
        fills[int(lab)] = fit_fill(xs[m], ys[m], rgba255[m], params, weights=w, core=core)
    return fills


def _radius() -> np.ndarray:
    ys, xs = np.mgrid[0:SIZE, 0:SIZE]
    return np.hypot(xs + 0.5 - SIZE / 2, ys + 0.5 - SIZE / 2)


def test_a_glow_cut_by_the_partition_is_joined_across_its_own_slope():
    """A Gaussian glow, dark core to a violet halo, cut at CUT px from the
    centre. Its slope there is steeper than the veto allows at a low detail,
    but it is a ramp, as steep beside the cut as on it — not a ridge."""
    r = _radius()
    t = np.exp(-0.5 * (r / 28.0) ** 2)[..., None]
    rgb = (1.0 - t) * np.array([130.0, 60.0, 230.0]) + t * np.array([0.0, 70.0, 10.0])
    labels = np.where(r <= CUT, 2, 1).astype(np.int32)
    xs, ys, rgba255, grad = _inputs(rgb)
    params = FitParams(gradients=True, max_stops=6, tol=2.0)
    fills = _fitted(labels, xs, ys, rgba255, params)
    (cnt, gsum), = adjacency(labels, grad).values()
    edge_limit = 2.1
    assert gsum / cnt > edge_limit, gsum / cnt  # the case the veto used to catch
    out, out_fills, changed = refine_merge(labels, xs, ys, rgba255, grad, fills, params, edge_limit)
    assert changed and len(np.unique(out)) == 1, (changed, np.unique(out))


def test_a_step_under_a_gradient_is_still_never_explained_away():
    """The veto's reason: a flat disc sixteen levels darker than a gradient
    around it. The union is a gradient too, and it must not be allowed."""
    r = _radius()
    ys, xs = np.mgrid[0:SIZE, 0:SIZE]
    ramp = (xs / SIZE)[..., None]
    rgb = (1.0 - ramp) * np.array([90.0, 120.0, 200.0]) + ramp * np.array([120.0, 150.0, 230.0])
    rgb = np.where((r <= CUT)[..., None], rgb - 16.0, rgb)
    labels = np.where(r <= CUT, 2, 1).astype(np.int32)
    xs, ys, rgba255, grad = _inputs(rgb)
    params = FitParams(gradients=True, max_stops=6, tol=2.0)
    fills = _fitted(labels, xs, ys, rgba255, params)
    (cnt, gsum), = adjacency(labels, grad).values()
    assert gsum / cnt > 2.1, gsum / cnt
    out, out_fills, changed = refine_merge(labels, xs, ys, rgba255, grad, fills, params, 2.1)
    assert not changed and len(np.unique(out)) == 2


def test_a_ramp_lifts_the_veto_only_along_a_boundary_long_enough_to_read(monkeypatch):
    """The glow of the first test with a speck of it cut out on the slope: the
    same ramp, a union the fit explains, along fewer than two dozen pairs.
    Its ridge share is decided by a few pixels, so it is not read."""
    from studi0trace.engines.vexel import refine as rf

    r = _radius()
    t = np.exp(-0.5 * (r / 28.0) ** 2)[..., None]
    rgb = (1.0 - t) * np.array([130.0, 60.0, 230.0]) + t * np.array([0.0, 70.0, 10.0])
    ys, xs0 = np.mgrid[0:SIZE, 0:SIZE]
    small = np.hypot(xs0 + 0.5 - (SIZE / 2 + CUT), ys + 0.5 - SIZE / 2) <= 2.5   # a speck on the slope, ~20 px
    labels = np.where(small, 2, 1).astype(np.int32)
    xs, ys, rgba255, grad = _inputs(rgb)
    params = FitParams(gradients=True, max_stops=6, tol=2.0)
    fills = _fitted(labels, xs, ys, rgba255, params)
    ((cnt, gsum),) = adjacency(labels, grad).values()
    assert gsum / cnt > 2.1 and cnt < rf.RAMP_MIN_PAIRS, (gsum / cnt, cnt)
    out, _, changed = refine_merge(labels, xs, ys, rgba255, grad, fills, params, 2.1)
    assert not changed
    monkeypatch.setattr(rf, "RAMP_MIN_PAIRS", 0)
    out, _, changed = refine_merge(labels, xs, ys, rgba255, grad, fills, params, 2.1)
    assert changed, "with no minimum the same ramp would have been read and joined"


def test_a_small_region_across_a_step_is_a_ridge_against_its_quiet_side():
    """A 14 px disc forty levels off a flat backdrop: the sample four pixels
    into the disc is its far edge, and against the higher of the two sides the
    boundary read as a ramp (thin-mark-128's core joined its backdrop across a
    step of 54). Against the lower side it is the ridge it is."""
    from studi0trace.engines.vexel.merge import boundary_ridges

    r = _radius()
    rgb = np.full((SIZE, SIZE, 3), 120.0)
    rgb = np.where((r <= 7.0)[..., None], rgb - 40.0, rgb)
    labels = np.where(r <= 7.0, 2, 1).astype(np.int32)
    xs, ys, rgba255, grad = _inputs(rgb)
    (share,) = boundary_ridges(labels, grad).values()
    assert share > 0.5, share
