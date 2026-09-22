import math

import numpy as np

from studi0trace.engines.vexel import curves
from studi0trace.engines.vexel.curves import (
    Circle, Cubic, CurveParams, Ellipse, Line, PathShape, Rect, _bezier, find_corners, fit_open,
    fit_shape, path_d, shape_svg, straight_runs,
)

P = CurveParams(corner_threshold=60, tol=0.4, shape_fitting=True)


def dense_polygon(vertices, step=0.5):
    pts = []
    v = np.asarray(vertices, float)
    for i in range(len(v)):
        a, b = v[i], v[(i + 1) % len(v)]
        n = max(int(np.linalg.norm(b - a) / step), 1)
        for k in range(n):
            pts.append(a + (b - a) * k / n)
    return np.array(pts)


def circle_poly(cx, cy, r, n=200):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)])


def test_square_has_four_corners_and_becomes_rect():
    poly = dense_polygon([(10, 10), (40, 10), (40, 30), (10, 30)])
    corners = find_corners(poly, 60)
    assert len(corners) == 4
    assert sorted(tuple(np.round(poly[c])) for c in corners) == [(10, 10), (10, 30), (40, 10), (40, 30)]
    shape = fit_shape([poly], P)
    assert isinstance(shape, Rect)
    assert (shape.x, shape.y, shape.w, shape.h) == (10, 10, 30, 20)
    assert shape_svg(shape, 'fill="#000"', 2) == '<rect x="10" y="10" width="30" height="20" fill="#000"/>'


def test_circle_polyline_is_a_circle_primitive():
    shape = fit_shape([circle_poly(31.7, 32.4, 20.3)], P)
    assert isinstance(shape, Circle)
    assert abs(shape.cx - 31.7) < 0.02 and abs(shape.cy - 32.4) < 0.02 and abs(shape.r - 20.3) < 0.02
    assert find_corners(circle_poly(0, 0, 20), 60) == []


def test_ellipse_polyline_is_an_ellipse_primitive():
    t = np.linspace(0, 2 * math.pi, 240, endpoint=False)
    ang = math.radians(20)
    x = 30 * np.cos(t)
    y = 12 * np.sin(t)
    poly = np.column_stack([50 + x * math.cos(ang) - y * math.sin(ang), 40 + x * math.sin(ang) + y * math.cos(ang)])
    shape = fit_shape([poly], P)
    assert isinstance(shape, Ellipse)
    assert abs(shape.cx - 50) < 0.1 and abs(shape.cy - 40) < 0.1
    assert abs(max(shape.rx, shape.ry) - 30) < 0.1 and abs(min(shape.rx, shape.ry) - 12) < 0.1


def test_rotated_square_is_four_lines_not_a_rect():
    poly = dense_polygon([(30, 10), (50, 30), (30, 50), (10, 30)])
    shape = fit_shape([poly], P)
    assert isinstance(shape, PathShape)
    segs = shape.contours[0]
    assert len(segs) == 4 and all(isinstance(s, Line) for s in segs)
    d = path_d(shape.contours, 1)
    assert d.startswith("M") and d.endswith("Z") and d.count("L") == 4


def test_smooth_curve_fits_within_tolerance_with_few_cubics():
    src = Cubic(np.array([0.0, 0.0]), np.array([30.0, 60.0]), np.array([70.0, -40.0]), np.array([100.0, 20.0]))
    pts = _bezier(src, np.linspace(0, 1, 300))
    segs = fit_open(pts, tol=0.4)
    assert 1 <= len(segs) <= 3
    # Sample the fitted chain and check distance to the source samples. Sample
    # it densely: with one span covering a hundred pixels, a hundred samples are
    # a pixel apart and the measurement is mostly its own step size.
    fitted = np.vstack([_bezier(s, np.linspace(0, 1, 800)) if isinstance(s, Cubic) else np.linspace(s.p0, s.p1, 800) for s in segs])
    d = np.min(np.linalg.norm(pts[:, None, :] - fitted[None, :, :], axis=2), axis=1)
    assert d.max() < 0.6


def test_straight_polyline_is_a_line_and_near_axis_lines_snap():
    pts = np.column_stack([np.linspace(0, 40, 50), np.zeros(50) + 0.02 * np.sin(np.linspace(0, 6, 50))])
    (seg,) = fit_open(pts, tol=0.4)
    assert isinstance(seg, Line)

    slightly_off = dense_polygon([(10, 10.0), (40, 10.4), (40, 30), (10, 30.3)])
    shape = fit_shape([slightly_off], CurveParams(shape_fitting=False))
    lines = [s for s in shape.contours[0] if isinstance(s, Line)]
    horizontals = [s for s in lines if abs(s.p0[1] - s.p1[1]) < 1e-9]
    assert len(horizontals) >= 2, "near-horizontal sides should be snapped exactly horizontal"


def test_rounded_shape_with_corners_mixes_lines_and_cubics():
    # a "D" shape: straight left side, semicircle right
    t = np.linspace(-math.pi / 2, math.pi / 2, 120)
    arc = np.column_stack([40 + 20 * np.cos(t), 30 + 20 * np.sin(t)])
    left = np.column_stack([np.full(60, 40.0), np.linspace(50, 10, 60)])
    poly = np.vstack([arc, left[1:-1]])
    shape = fit_shape([poly], P)
    assert isinstance(shape, PathShape)
    kinds = {type(s).__name__ for s in shape.contours[0]}
    assert kinds == {"Line", "Cubic"}
    assert len(shape.contours[0]) <= 5


def test_a_rounded_square_keeps_its_sides_straight():
    """The sides of a rounded square are straight and its corners are not, and
    the trace has to say so. Fitted as one smooth loop — which is what happens
    when no corner is sharp enough to split it — the sides come out barrelled
    and the corners far rounder than they are."""
    r = 6.0
    pts = []
    for cx, cy, a0 in ((44, 44, 0), (16, 44, 90), (16, 16, 180), (44, 16, 270)):
        for t in np.linspace(a0, a0 + 90, 14):
            pts.append([cx + r * np.cos(np.radians(t)), cy + r * np.sin(np.radians(t))])
        nxt = {0: (16, 44), 90: (16, 16), 180: (44, 16), 270: (44, 44)}[a0]
        here = pts[-1]
        far = np.array(nxt) + r * np.array([np.cos(np.radians(a0 + 90)), np.sin(np.radians(a0 + 90))])
        for f in np.linspace(0, 1, 26)[1:-1]:
            pts.append(list(np.array(here) + f * (far - np.array(here))))
    poly = np.array(pts)

    cuts = straight_runs(poly)
    assert len(cuts) >= 6, f"the flat sides were not found: {cuts}"

    segs = fit_shape([poly], P).contours[0]
    lines = [s for s in segs if isinstance(s, Line)]
    assert len(lines) >= 3, f"a rounded square came out with {len(lines)} straight sides"
    for s in lines:
        length = float(np.hypot(*(s.p1 - s.p0)))
        assert length > 10.0, "a side came out chopped into fragments"


def test_a_circle_is_not_chopped_into_straight_runs():
    """A curve must not be polygonised. The bound on how far a run may bend over
    its own chord is what keeps it out: nothing under a radius of a few hundred
    pixels can hold a straight run long enough to qualify."""
    t = np.linspace(0, 2 * np.pi, 400, endpoint=False)
    for radius in (20.0, 60.0, 150.0):
        circle = np.column_stack([radius * np.cos(t), radius * np.sin(t)])
        assert straight_runs(circle) == [], f"a circle of radius {radius} was cut into lines"


def test_split_tangent_beats_the_chord_between_the_two_neighbours():
    """The tangent the two halves share at a split is a line fit, not a chord.

    The chord between the split's two neighbours is two pixels long, and two
    pixels of a sub-pixel outline is mostly noise: the join then leaves a few
    degrees off the direction the curve is really travelling, which is the
    hitch you see when you zoom in. Shake the vertices by the few hundredths of
    a pixel the placement stage really has, space them unevenly the way
    crack-following does, and the line fit must hold the true tangent better.
    """
    rng = np.random.default_rng(7)
    th = np.sort(rng.uniform(0.0, np.pi / 2, 200))
    poly = np.column_stack([40 * np.cos(th), 40 * np.sin(th)])
    poly += rng.normal(0.0, 0.04, poly.shape)

    def off_by(got: np.ndarray, at: int) -> float:
        want = np.array([-np.sin(th[at]), np.cos(th[at])])
        return abs(np.degrees(np.arccos(np.clip(abs(float(got @ want)), 0.0, 1.0))))

    at = range(5, 195)
    chord = np.mean([off_by(curves._normalize(poly[k + 1] - poly[k - 1]), k) for k in at])
    fitted = np.mean([off_by(curves._local_tangent(poly, k, curves.SPLIT_REACH), k) for k in at])
    assert fitted < chord / 1.3


def test_split_tangent_declines_when_the_vertices_scatter():
    """Vertices that scramble are not a line, and a line through them can come
    out pointing back the way the curve came. The two neighbours' chord is the
    answer there. Real curvature is nowhere near enough to trip this: a circle
    a gently curving one is fitted, not handed back."""
    scrambled = np.array([[36.862, 23.711], [35.317, 23.834], [35.218, 23.476],
                          [35.611, 23.627], [34.793, 24.315]])
    chord = curves._normalize(scrambled[3] - scrambled[1])
    assert np.allclose(curves._local_tangent(scrambled, 2, curves.SPLIT_REACH), chord)

    th = np.array([-0.050, -0.037, 0.0, 0.008, 0.030])   # curving, unevenly spaced
    arc = np.column_stack([60 * np.cos(th), 60 * np.sin(th)])
    fitted = curves._local_tangent(arc, 2, curves.SPLIT_REACH)
    chord = curves._normalize(arc[3] - arc[1])
    want = np.array([0.0, 1.0])

    def off_by(v):
        return abs(np.degrees(np.arccos(np.clip(float(v @ want), -1.0, 1.0))))

    assert not np.allclose(fitted, chord)
    assert off_by(fitted) < off_by(chord)


def _curvature(cubic: Cubic, at_end: bool) -> float:
    p = np.array([cubic.p0, cubic.c1, cubic.c2, cubic.p1], dtype=float)
    d1 = 3 * (p[3] - p[2]) if at_end else 3 * (p[1] - p[0])
    d2 = 6 * (p[3] - 2 * p[2] + p[1]) if at_end else 6 * (p[2] - 2 * p[1] + p[0])
    speed = float(np.linalg.norm(d1))
    return 0.0 if speed < 1e-9 else abs(d1[0] * d2[1] - d1[1] * d2[0]) / speed**3


def test_a_long_smooth_run_is_fitted_without_a_curvature_jump():
    """Between corners the fit is one C2 spline, so consecutive cubics agree on
    curvature as well as on direction. Splitting and recursing only ever agreed
    on direction, and the jump in curvature is the hitch you see when you zoom
    in on a curve that ought to be smooth."""
    s = np.linspace(0.0, 1.0, 400)
    poly = np.column_stack([220 * s, 60 * np.sin(3.1 * s) + 40 * s * s])
    cubics = [seg for seg in fit_open(poly, 0.4) if isinstance(seg, Cubic)]
    assert len(cubics) >= 3, "this run needs several spans, or the test proves nothing"
    jumps = [abs(_curvature(a, True) - _curvature(b, False)) for a, b in zip(cubics, cubics[1:])]
    assert max(jumps) < 1e-6

    # ...and it is a fit, not just a smooth curve near the run. The fit measures
    # itself at the parameters it settled on, so the true closest distance can
    # come out a little over the tolerance it accepted at.
    drawn = np.concatenate([_bezier(c, np.linspace(0.0, 1.0, 400)) for c in cubics])
    apart = np.linalg.norm(poly[:, None, :] - drawn[None, :, :], axis=2).min(axis=1)
    assert apart.max() < 0.45


def test_the_spline_declines_rather_than_miss_the_tolerance():
    """A run the spline cannot hold inside `tol` is handed back, and the
    split-and-recurse fit answers instead - never a loose spline."""
    rng = np.random.default_rng(3)
    poly = np.column_stack([np.arange(120.0), rng.normal(0.0, 6.0, 120)])
    assert curves.fit_c2(poly, np.array([1.0, 0.0]), np.array([-1.0, 0.0]), 0.05) is None
    assert len(fit_open(poly, 0.05)) > 1
