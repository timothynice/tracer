import math

import numpy as np

from studi0trace.engines.vexel import curves
from studi0trace.engines.vexel.curves import (
    Circle, Cubic, CurveParams, Ellipse, Line, PathShape, Rect, _bezier, find_corners, fit_open,
    RoundedRect,
    fit_closed, fit_shape, fit_stretch, line_runs, path_d, shape_svg,
    fit_contour_segments,
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
    # a "D" shape: straight left side, semicircle right (r = 60: one cubic
    # cannot hold a semicircle that big inside 0.4 px, two arcs tie two cubics
    # and the exact shape wins the tie)
    t = np.linspace(-math.pi / 2, math.pi / 2, 360)
    arc = np.column_stack([80 + 60 * np.cos(t), 70 + 60 * np.sin(t)])
    left = np.column_stack([np.full(180, 80.0), np.linspace(130, 10, 180)])
    poly = np.vstack([arc, left[1:-1]])
    shape = fit_shape([poly], P)
    assert isinstance(shape, PathShape)
    kinds = {type(s).__name__ for s in shape.contours[0]}
    assert kinds == {"Line", "CircArc"}, kinds
    assert len(shape.contours[0]) <= 3


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

    assert isinstance(fit_shape([poly], P), RoundedRect), "a rounded square is a <rect rx>"
    segs = fit_contour_segments(poly, P)[0]
    lines = [s for s in segs if isinstance(s, Line)]
    assert len(lines) == 4, f"a rounded square came out with {len(lines)} straight sides: {''.join('L' if isinstance(s, Line) else 'C' for s in segs)}"
    for s in lines:
        length = float(np.hypot(*(s.p1 - s.p0)))
        assert length > 10.0, "a side came out chopped into fragments"


def test_a_circle_is_not_chopped_into_lines():
    """A curve must not be polygonised. Chords of a big circle pass the residual
    test one at a time; what keeps them out is that chords turn a little against
    each other and a polygon's sides do not, and that the curve fit is cheaper."""
    t = np.linspace(0, 2 * np.pi, 400, endpoint=False)
    for radius in (20.0, 60.0, 150.0, 300.0):
        circle = np.column_stack([radius * np.cos(t), radius * np.sin(t)])
        segs = fit_closed(circle, 0.4)
        assert not any(isinstance(s, Line) for s in segs), f"a circle of radius {radius} was cut into lines"
        quarter = circle[:100]
        assert not any(isinstance(s, Line) for s in fit_stretch(quarter, 0.4)), f"a quarter arc of radius {radius} was cut into lines"


def test_line_runs_finds_a_straight_edge_despite_end_noise():
    """The corners are the least certain points on an outline. A line test that
    measured against the chord between them failed a straight edge whenever a
    corner sat a third of a pixel off; the residuals about the run's own line
    do not care where the ends are."""
    t = np.linspace(0, 1, 143)[:, None]
    pts = np.array([[0.0, 0.0]]) * (1 - t) + np.array([[100.0, 3.0]]) * t
    rng = np.random.default_rng(1)
    pts = pts + rng.normal(0, 0.06, pts.shape)
    pts[0] += (0.0, 0.35)
    pts[-1] += (0.0, -0.3)
    runs = line_runs(pts)
    assert len(runs) == 1 and runs[0][0] <= 1 and runs[0][1] >= len(pts) - 2, runs
    segs = fit_stretch(pts, 0.4)
    assert len(segs) == 1 and isinstance(segs[0], Line)
    assert np.allclose(segs[0].p0, pts[0]) and np.allclose(segs[0].p1, pts[-1])


def test_a_rounded_corner_is_line_curve_line():
    rng = np.random.default_rng(2)
    side = np.column_stack([np.linspace(0, 40, 60), np.zeros(60)])
    for r in (10.0, 25.0, 60.0):
        th = np.linspace(-np.pi / 2, 0, max(25, int(r)))[1:]
        corner = np.column_stack([40 + r * np.cos(th), r + r * np.sin(th)])
        up = np.column_stack([np.full(60, 40.0 + r), np.linspace(r, r + 40, 60)])
        pts = np.vstack([side, corner, up[1:]]) + rng.normal(0, 0.05, (len(side) + len(corner) + 59, 2))
        segs = fit_stretch(pts, 0.4)
        kinds = "".join("L" if isinstance(s, Line) else "C" for s in segs)
        assert kinds.startswith("L") and kinds.endswith("L") and "C" in kinds and kinds.count("L") == 2, f"r={r}: {kinds}"


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


def _noisy_s(n: int = 300, sigma: float = 0.05, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 120, n)
    return np.column_stack([x, 25 * np.sin(x / 120 * 2 * np.pi)]) + rng.normal(0, sigma, (n, 2))


def _span_max_errors(pts: np.ndarray, cubics) -> list[float]:
    """Each point's distance to the nearest span, credited to that span."""
    t = np.linspace(0, 1, 400)[:, None]  # 0.1 px steps on a 40 px span, so the sampling overstates by a few hundredths
    curves_ = [(1 - t) ** 3 * c.p0 + 3 * (1 - t) ** 2 * t * c.c1 + 3 * (1 - t) * t ** 2 * c.c2 + t ** 3 * c.p1 for c in cubics]
    dists = np.stack([np.sqrt(((pts[:, None, :] - q[None, :, :]) ** 2).sum(-1)).min(axis=1) for q in curves_], axis=1)
    owner = dists.argmin(axis=1)
    return [float(dists[owner == k, k].max()) if (owner == k).any() else 0.0 for k in range(len(cubics))]


def test_spline_error_is_spread_evenly_across_spans():
    """One span at the tolerance and its neighbour at nothing is a knot in the
    wrong place. After the fit the knots are moved toward the error."""
    pts = _noisy_s()
    t1 = curves._normalize(pts[3] - pts[0])
    t2 = curves._normalize(pts[-4] - pts[-1])
    cubics = curves.fit_c2(pts, t1, t2, 0.4)
    assert cubics is not None and len(cubics) >= 3
    errs = _span_max_errors(pts, cubics)
    assert max(errs) < 0.45, errs
    assert max(errs) - min(errs) < 0.16, errs


def test_no_spline_span_has_a_bump():
    pts = _noisy_s(seed=5)
    t1 = curves._normalize(pts[3] - pts[0])
    t2 = curves._normalize(pts[-4] - pts[-1])
    cubics = curves.fit_c2(pts, t1, t2, 0.4)
    assert cubics is not None
    for c in cubics:
        chord = np.linalg.norm(c.p1 - c.p0)
        assert np.linalg.norm(c.c1 - c.p0) <= curves.BUMP_RATIO * chord
        assert np.linalg.norm(c.c2 - c.p1) <= curves.BUMP_RATIO * chord


# --- the kind of a stretch does not depend on the tolerance ----------------------------


def _ribbon(seed: int, flat: float = 38.0, r: float = 45.0, deg: float = 40.0) -> np.ndarray:
    """The wordmark's ribbon edge: a flat run heading -x that turns, tangentially,
    into a circular arc of radius 45 curving down."""
    rng = np.random.default_rng(seed)
    xs = np.arange(0.0, flat, 1.0)
    line = np.column_stack([-xs, np.zeros_like(xs)])
    a = np.radians(np.arange(0.0, deg + 1e-9, 180.0 / (math.pi * r)))
    arc = np.column_stack([-flat - r * np.sin(a), r - r * np.cos(a)])
    pts = np.vstack([line, arc])
    return pts + rng.normal(0.0, 0.02, pts.shape)


def _samples(segs, n=200):
    out = []
    for s in segs:
        if isinstance(s, Cubic):
            out.append(_bezier(s, np.linspace(0, 1, n)))
        elif isinstance(s, Line):
            out.append(np.linspace(s.p0, s.p1, n))
        else:
            out.append(curves.arc_points(s, n))
    return np.vstack(out)


def test_a_line_running_into_an_arc_stays_straight_at_a_loose_tolerance():
    """At 0.6 px one cubic reaches across the flat run and the arc it turns into
    (cost 1 against line + curve's 1.5) and bows the flat run by half a pixel:
    the logo preset's wavy ribbon. The run is straight to 0.02 px; at every
    tolerance it is a line."""
    for seed in range(4):
        pts = _ribbon(seed)
        for tol in (0.4, 0.6, 1.0):
            segs = fit_stretch(pts, tol)
            assert isinstance(segs[0], Line), (seed, tol, segs)
            d = _samples(segs)
            flat = (d[:, 0] < -4.0) & (d[:, 0] > -34.0)
            assert np.abs(d[flat, 1]).max() < 0.06, (seed, tol)


def test_lines_first_at_0_4_stays_lines_first_when_looser():
    """A stretch drawn lines first at 0.4 px is drawn lines first at 0.6, with
    the same lines; a curve that is a curve at 0.4 does not become lines."""
    kinds = lambda segs: [type(s).__name__ for s in segs if isinstance(s, Line)]
    for seed in range(4):
        pts = _ribbon(seed)
        assert kinds(fit_stretch(pts, 0.6)) == kinds(fit_stretch(pts, 0.4))
    t = np.linspace(0, 2 * np.pi, 400, endpoint=False)
    for radius in (20.0, 60.0, 150.0):
        quarter = np.column_stack([radius * np.cos(t), radius * np.sin(t)])[:100]
        assert not any(isinstance(s, Line) for s in fit_stretch(quarter, 0.6)), radius


def test_a_rounded_square_keeps_its_sides_straight_at_a_loose_tolerance():
    """The same rounded square as above, fitted as one closed contour at the
    logo preset's 0.6 px: four straight sides, not a pillow."""
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
    for tol in (0.4, 0.6):
        segs = fit_closed(poly, tol)
        assert len([s for s in segs if isinstance(s, Line)]) == 4, tol


# --- a two-point cubic has no inflection -----------------------------------------------


def _counter_sampled(c: Cubic, n: int = 2001) -> float:
    """counter_turn_deg the slow way: the tangent angle sampled densely."""
    t = np.linspace(0.0, 1.0, n)[:, None]
    d = 3 * (1 - t) ** 2 * (c.c1 - c.p0) + 6 * (1 - t) * t * (c.c2 - c.c1) + 3 * t ** 2 * (c.p1 - c.c2)
    turn = np.diff(np.unwrap(np.arctan2(d[:, 1], d[:, 0])))
    return math.degrees((np.abs(turn).sum() - abs(turn.sum())) / 2.0)


def test_counter_turn_reads_an_s_and_not_a_c():
    s = Cubic(np.array([0.0, 0.0]), np.array([4.0, 2.0]), np.array([6.0, -2.0]), np.array([10.0, 0.0]))
    c = Cubic(np.array([0.0, 0.0]), np.array([0.0, 4.0]), np.array([2.0, 6.0]), np.array([6.0, 6.0]))
    w = Cubic(np.array([0.0, 0.0]), np.array([5.0, 1.0]), np.array([5.0, -1.0]), np.array([10.0, 0.0]))  # two inflections
    assert curves.counter_turn_deg(c) == 0.0
    for cubic in (s, w):
        assert curves.counter_turn_deg(cubic) > 5.0
        assert abs(curves.counter_turn_deg(cubic) - _counter_sampled(cubic)) < 0.05


def test_a_two_point_cubic_is_held_inside_its_tangents():
    """Two vertices say nothing about curvature. Tangents that meet 0.6 px
    along the first one, with chord/3 (1 px) arms, overshoot the meeting point
    and come back: a hook. Held to it, the cubic is convex, and still leaves
    and arrives along the pinned tangents."""
    p0, p1, v = np.array([0.0, 0.0]), np.array([3.0, 0.0]), np.array([0.5, 0.3])
    t1, t2 = curves._normalize(v - p0), curves._normalize(v - p1)
    assert curves.counter_turn_deg(Cubic(p0, p0 + t1, p1 + t2, p1)) > 3.0
    (c,) = curves.fit_cubics(np.vstack([p0, p1]), t1, t2, 0.4)
    assert curves.counter_turn_deg(c) <= curves.INFL_DEG
    assert np.allclose(c.p0, p0) and np.allclose(c.p1, p1)
    assert np.allclose(curves._normalize(c.c1 - c.p0), t1) and np.allclose(curves._normalize(c.c2 - c.p1), t2)
    # tangents that diverge allow only an S, and keep it
    t2s = curves._normalize(np.array([-1.0, -0.3]))
    (s,) = curves.fit_cubics(np.vstack([p0, p1]), t1, t2s, 0.4)
    assert np.allclose(s.c2, p1 + t2s * 1.0)


def test_an_isotropic_scatter_runs_first_to_last():
    """Four staircase vertices at the corners of a square fit every direction
    equally; the solver's own pick (the SVD's horizontal, the Rust's vertical)
    moved a node 0.6 px between the engines. They run the way they were given."""
    pts = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    centre, direction = curves._line_through(pts)
    assert np.allclose(centre, [0.5, 0.5])
    assert np.allclose(direction, [math.sqrt(0.5), math.sqrt(0.5)])
    # an ordinary scatter keeps its principal direction
    _c, d = curves._line_through(np.array([[0.0, 0.0], [2.0, 0.1], [4.0, 0.0], [6.0, 0.1]]))
    assert abs(d[1]) < 0.05
