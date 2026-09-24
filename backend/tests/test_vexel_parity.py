"""The choices the two engines once made differently because nothing defined
them: a count at a rounding-level integer, an image equidistant from two
vertices, the principal axes of an isotropic ring, which pixels a large region
is fitted from, and knots that fit a ramp equally well. Each test holds the
rule both engines now follow; `tools/diffcheck.py` holds them to each other
over the corpus."""
from __future__ import annotations

import math

import numpy as np
import pytest

from studi0trace.engines.vexel import fills, symmetry, topology
from studi0trace.engines.vexel.fills import FitParams, fit_fill

try:
    import vexel_rs
except ImportError:  # pragma: no cover - the extension is optional
    vexel_rs = None

needs_rust = pytest.mark.skipif(vexel_rs is None, reason="the vexel_rs extension is not built")


def test_a_length_a_whole_number_of_steps_up_to_rounding_is_that_many():
    # a rectangle side on the pixel grid, as the two engines' node arithmetic
    # leaves it: 16 px either way, so 16 steps (17 vertices) either way
    assert topology._steps(16.000000000000004) == 16
    assert topology._steps(15.999999999999996) == 16
    assert topology._steps(16.0) == 16
    assert topology._steps(16.01) == 17
    assert topology._steps(8.000000000000002, 0.5) == 16


def test_an_image_between_two_vertices_matches_the_lower_index():
    poly = np.array([[2.0, 0.0], [0.0, 0.0], [5.0, 5.0], [9.0, 1.0]])
    images = np.array([[1.0, 0.0], [1.0, 1e-13], [5.0, 4.0]])
    dist, idx = symmetry._nearest(poly, images)
    assert idx.tolist() == [0, 0, 2]
    assert dist[0] == pytest.approx(1.0)


def test_an_image_among_many_tied_vertices_matches_the_lowest_index():
    # more tied vertices than the k-d tree is asked for
    ring = np.array([[math.cos(a), math.sin(a)] for a in np.linspace(0, 2 * math.pi, 12, endpoint=False)])
    poly = np.vstack([np.array([[9.0, 9.0]]), ring[::-1]])
    _dist, idx = symmetry._nearest(poly, np.array([[0.0, 0.0]]))
    assert idx.tolist() == [1]


def _square_ring(side: float = 32.0, step: float = 1.0) -> np.ndarray:
    k = int(side / step)
    edge = np.arange(k) * step
    return np.vstack([
        np.column_stack([edge, np.zeros(k)]),
        np.column_stack([np.full(k, side), edge]),
        np.column_stack([side - edge, np.full(k, side)]),
        np.column_stack([np.zeros(k), side - edge]),
    ]) + 100.0


def test_an_isotropic_ring_has_no_principal_axes_only_the_grid():
    axes = symmetry.mirror_axes(_square_ring())
    angles = [round(math.degrees(math.atan2(d[1], d[0])), 9) % 180.0 for _c, d in axes]
    assert angles == [15.0 * k for k in range(12)]
    assert axes[0][1].tolist() == [1.0, 0.0]


def test_an_elongated_ring_leads_with_its_major_axis_then_the_minor_a_quarter_turn_on():
    ring = _square_ring()
    ring[:, 0] = 100.0 + (ring[:, 0] - 100.0) * 2.0  # 64 x 32
    turn = math.radians(20.0)
    c = ring.mean(axis=0)
    rot = np.array([[math.cos(turn), -math.sin(turn)], [math.sin(turn), math.cos(turn)]])
    axes = symmetry.mirror_axes(c + (ring - c) @ rot.T)
    angles = [math.degrees(math.atan2(d[1], d[0])) % 180.0 for _c, d in axes[:4]]
    assert angles == pytest.approx([20.0, 110.0, 65.0, 155.0], abs=1e-6)


@needs_rust
@pytest.mark.parametrize("pop,size", [(25_001, 25_000), (170_200, 25_000), (1_250_049, 25_000),
                                      (1_250_050, 25_000), (1_300_000, 25_000), (9_000, 2_500), (200_000, 2_500)])
def test_the_rust_subsample_is_numpys_draw_on_both_of_its_roads(pop, size):
    """numpy's `choice(replace=False)` tail-shuffles the whole range when more
    than a fiftieth of a population over 10000 is wanted, and uses Floyd's
    algorithm otherwise; the fill fit's 25000 samples of a region up to 1.25 M
    pixels take the first road, which the Rust once did not have."""
    want = np.random.default_rng(1234).choice(pop, size, replace=False)
    assert np.array_equal(np.asarray(vexel_rs._rng_choice(pop, size)), want)


def _gapped_ramp(seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Two rows of a ramp whose pixels leave a wide gap along it and whose
    colours are noise: several knot candidates fit it identically."""
    rng = np.random.default_rng(seed)
    n = int(rng.integers(8, 20))
    t = np.sort(np.concatenate([[0.0, 1.0], rng.random(n - 2)]))
    t = np.sort(np.where(t > 0.55, np.maximum(t, 0.9), t))
    col = np.column_stack([rng.random(n) * 200, rng.random(n) * 200, rng.random(n) * 200, np.full(n, 255.0)])
    xs = np.concatenate([t * 40 + 0.5, t * 40 + 0.5])
    ys = np.concatenate([np.full(n, 0.5), np.full(n, 1.5)])
    return xs, ys, np.vstack([col, col])


@pytest.mark.parametrize("seed", [12, 14, 16])
def test_tied_knots_take_the_lower_candidate(seed, monkeypatch):
    xs, ys, col = _gapped_ramp(seed)
    params = FitParams(gradients=True, max_stops=6, tol=0.5)
    fill = fit_fill(xs, ys, col, params)
    assert fill.kind == "linear"
    monkeypatch.setattr(fills, "RAMP_TIE", 0.0)
    strict = fit_fill(xs, ys, col, params)
    # the ties are real: which of them a strict `<` keeps is rounding's call
    assert [s.offset for s in strict.stops] != [s.offset for s in fill.stops]
    # and the rule keeps the lowest of them
    assert min(s.offset for s in fill.stops if s.offset not in [t.offset for t in strict.stops]) < \
        min(s.offset for s in strict.stops if s.offset not in [t.offset for t in fill.stops])


@needs_rust
@pytest.mark.parametrize("seed", [12, 14, 16])
def test_both_engines_fit_a_tied_ramp_the_same_way(seed):
    from tools.diffcheck import _rust_fill

    xs, ys, col = _gapped_ramp(seed)
    fill = fit_fill(xs, ys, col, FitParams(gradients=True, max_stops=6, tol=0.5))
    kind, vals = vexel_rs._fit_fill(xs.tolist(), ys.tolist(), col.ravel().tolist(), [1.0] * xs.size,
                                    True, 6, 0.5, None)
    rust = _rust_fill(kind, vals)
    assert kind == fill.kind
    assert [s.offset for s in rust.stops] == [s.offset for s in fill.stops]
    assert np.abs(rust.evaluate(xs, ys) - fill.evaluate(xs, ys)).max() < 1e-6
