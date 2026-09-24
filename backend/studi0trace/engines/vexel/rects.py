"""Stage 7a: rounded rectangles, drawn the way a designer draws them.

A designer who draws four small squares with rounded corners gives every
corner of a square one radius, gives squares of one size one radius, puts the
edges of neighbouring squares on shared guides and makes a square square. The
trace sees none of that: each corner is fitted on its own from four to seven
placed vertices, so one comes out a quarter circle, the next a chamfer, the
next nearly sharp, and edges that were drawn on one guide sit a tenth of a
pixel apart.

This module is the geometry: a model of an axis-aligned rectangle with a radius
per corner, fitted to a ring's placed vertices; the model's outline as lines
and quarter circles, walked from any point on it to any other; and the 1-D
clustering that decides which radii, sizes and edges are one. The graph side —
which rings are tested, how the model is written back into the shared arcs so
that the neighbour across every edge gets the same curve — is
`topology._rectify`.

The model is read from the placement, not from corner detection: the sides are
`curves.line_runs` (the residual test), and each corner's radius is the
least-squares radius of the circle tangent to its two sides through the
vertices between them. The old test (`curves.try_rounded_rect`) asked for three
vertices at least half a pixel inside both sides before it would read a radius;
a 3 px corner has one or two, so no small rounded square was ever a `<rect>`.

A soft image rounds every corner: the placement follows the half-way contour,
and blur rounds that. So the blur is read across the model's own straight
sides (`edge_sigma`) and taken out of each radius (`deblur`), and the model
is checked against the vertices as the placement would read it (`apparent`).
Without it a sharp square under a 0.8 px blur came out as `rx="1.9"`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from studi0trace.engines.vexel.curves import CircArc, Line, Segment, line_runs

CORNERS = ("LT", "RT", "RB", "LB")  # clockwise on screen, from the top left
RECT_MIN_SIDE = 4.0     # px; a model narrower than this says nothing
RECT_RMS = 0.15         # px; the trusted vertices' RMS distance from the model (placement noise is ~0.06)
SHARP_R = 0.6           # px; a corner radius under this is a sharp corner
# px; what a sharp corner can read as: the placement cuts it with the lattice's
# half-pixel chamfer, which a least-squares circle reads as up to 1.44 px at a
# half-pixel offset (0 on the grid). A fillet (`topology._fillets`) that reads
# no more than this is left as it was fitted.
CHAMFER_R = 1.5
# A blurred corner reads rounder than it is: the placement follows the image's
# half-way contour, and on a corner of radius r under a Gaussian blur σ that
# contour's radius is close to √(r² + (1.86σ)²) (E5 in the research notes;
# BLUR_K = 1.86²). The placement itself reads READ_C px² more on top (a 3 px
# corner of a hard-edged render reads 3.24, a 2 px one 2.22), fitted over 240
# rendered squares, r 0–6 px, σ 0–0.9 px, four sub-pixel offsets, where it
# brings the mean error of a shape's radius from +0.47 px to +0.04. A shape
# whose corners, so corrected, average no more than SHARP_SHAPE_R is sharp:
# over those squares that calls every sharp one sharp (the uncorrected
# CHAMFER_R rule called 6 of 20 sharp squares at σ ≥ 0.7 rounded) and all but
# one of those with a 1.5 px radius rounded.
BLUR_K = 1.86 ** 2
READ_C = 0.58
SHARP_SHAPE_R = 1.1
MERGE_LEVEL = 0.3       # px; two runs of one side on one level are one side
LEVEL_STRAY = 0.3       # px; a run vertex this far from the run's median is not on the side
CORNER_READ = 2         # run-end vertices either side of a gap that its radius is also read from
RADIUS_ITER = 60        # golden-section steps for a corner radius
# How far a regularity snap may move the outline, as a share of the curve
# tolerance: the snaps spend half the tolerance, the placement the other half.
MOVE_SHARE = 0.5
# Moving a corner's radius by dr moves its outline by dr·(√2 − 1) on the diagonal.
RADIUS_GAIN = math.sqrt(2.0) - 1.0
# px; how well a corner's radius is read at all: the same 3 px corner reads
# 2.9 to 3.6 as it moves across the pixel grid (the placement chamfers the
# pixel on the diagonal). Radii closer than this are one radius whatever the
# tolerance, since the reading cannot tell them apart.
RADIUS_NOISE = 0.4

NODE_ON = 0.2           # px; a node this close to the model's outline is on it
# The edge profile, read across the model's sides to say how soft the image is
# (`edge_sigma`): pixels whose centres lie within EDGE_REACH px of a side,
# along the middle of the side (EDGE_END px clear of its corners' arcs); the
# two colours are read EDGE_PLATEAU px either side of it.
EDGE_REACH = 2.0
EDGE_PLATEAU = (2.0, 3.5)
EDGE_END = 2.0
EDGE_SIGMA_MAX = 2.0     # px; the search range for the blur


def deblur(r: float, sigma: float) -> float:
    """The radius a corner read as `r` has under blur `sigma` (see BLUR_K)."""
    return math.sqrt(max(0.0, r * r - BLUR_K * sigma * sigma - READ_C))


def reblur(r: float, sigma: float) -> float:
    """The radius the placement reads for a corner of radius `r` under blur `sigma`."""
    return math.sqrt(r * r + BLUR_K * sigma * sigma + READ_C)


def apparent(m: Model, sigma: float) -> Model:
    """The model as the placement would read it: each rounded corner reblurred.
    A sharp corner stays sharp; its reading is the lattice's chamfer, which the
    hold test's tolerance already allows."""
    out = m.copy()
    out.r = [reblur(r, sigma) if r > 0.0 else 0.0 for r in m.r]
    cap = 0.5 * min(out.w, out.h)
    out.r = [min(r, cap) for r in out.r]
    return out


def radius_move(tol: float) -> float:
    """How far a corner's radius may move to join its group."""
    return max(MOVE_SHARE * tol / RADIUS_GAIN, RADIUS_NOISE)


@dataclass
class Model:
    x0: float
    y0: float
    x1: float
    y1: float
    r: list[float]          # per corner, in CORNERS order
    votes: list[float]      # trusted vertices behind each corner's radius (0 = not read)
    gaps: list[np.ndarray]  # per corner, the ring's vertex indices it is read from

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def copy(self) -> Model:
        return Model(self.x0, self.y0, self.x1, self.y1, list(self.r), list(self.votes), list(self.gaps))


def _corner_dist(u: np.ndarray, v: np.ndarray, r: float) -> np.ndarray:
    """Distance from points at inside distances (u, v) from a corner's two
    sides to the corner rounded with radius r: the quarter circle tangent to
    both sides where both distances are under r, the side beyond it."""
    d = np.empty_like(u)
    arc = (u < r) & (v < r)
    d[arc] = np.abs(np.hypot(u[arc] - r, v[arc] - r) - r)
    side = ~arc
    d[side] = np.minimum(np.where(u[side] >= r, np.abs(v[side]), np.inf), np.where(v[side] >= r, np.abs(u[side]), np.inf))
    return d


def _fit_radius(u: np.ndarray, v: np.ndarray, r_max: float) -> float:
    """The least-squares corner radius in [0, r_max], by golden section (the
    cost is not smooth where a vertex changes from arc to side, but it is
    unimodal over the few pixels a corner spans)."""
    lo, hi = 0.0, r_max
    phi = (math.sqrt(5.0) - 1.0) / 2.0

    def cost(r: float) -> float:
        e = _corner_dist(u, v, r)
        return float(e @ e)

    a, b = hi - phi * (hi - lo), lo + phi * (hi - lo)
    fa, fb = cost(a), cost(b)
    for _ in range(RADIUS_ITER):
        if fa <= fb:
            hi, b, fb = b, a, fa
            a = hi - phi * (hi - lo)
            fa = cost(a)
        else:
            lo, a, fa = a, b, fb
            b = lo + phi * (hi - lo)
            fb = cost(b)
    return 0.5 * (lo + hi)


def _corner_frame(m: Model, k: int) -> tuple[float, float, float, float]:
    """(corner x, corner y, inward x sign, inward y sign) of corner k."""
    name = CORNERS[k]
    cx, sx = (m.x0, 1.0) if name[0] == "L" else (m.x1, -1.0)
    cy, sy = (m.y0, 1.0) if name[1] == "T" else (m.y1, -1.0)
    return cx, cy, sx, sy


def outline_distance(pts: np.ndarray, m: Model) -> np.ndarray:
    """Distance from each point to the model's outline."""
    best = np.full(len(pts), np.inf)
    x, y = pts[:, 0], pts[:, 1]
    for k in range(4):
        cx, cy, sx, sy = _corner_frame(m, k)
        u = sx * (x - cx)
        v = sy * (y - cy)
        # each corner answers for its own quadrant of the rectangle; the sides
        # are shared by two corners and come out the same from either
        half_w, half_h = 0.5 * m.w, 0.5 * m.h
        mine = (u <= half_w + 1e-9) & (v <= half_h + 1e-9)
        d = _corner_dist(u, v, m.r[k])
        best = np.where(mine, np.minimum(best, d), best)
    return best


def _level(along: np.ndarray, mean: float) -> float:
    """A side's level from its run's vertices (`along` the side's normal axis):
    the run's own mean, `mean`, unless some vertex is more than LEVEL_STRAY
    from the run's median, and then the mean of the others. At a hard corner
    the crack walk can give the corner pixel's two edges out of order, and the
    one belonging to the next side then lands inside this run, half a pixel off
    it: a 32 x 24 rectangle on whole pixels came out 23.98 tall."""
    keep = np.abs(along - np.median(along)) <= LEVEL_STRAY
    if keep.all():
        return mean
    return float(np.mean(along[keep]))


def fit_sides(poly: np.ndarray, snap_axis_deg: float) -> Model | None:
    """The four sides of the axis-aligned rectangle through a closed ring, or
    None: four straight runs (`curves.line_runs`) on alternating axes, at
    their total-least-squares levels, with the vertices of each gap between
    two runs (and CORNER_READ run-end vertices either side) as that corner's.
    The radii are left at zero for `fit_radii`."""
    n = len(poly)
    if n < 16:
        return None
    # start at the vertex farthest from the centroid: on a rounded rectangle
    # that is the middle of a corner, so every run lies whole in the sequence
    start = int(np.argmax(np.linalg.norm(poly - poly.mean(axis=0), axis=1)))
    order = np.concatenate([np.arange(start, n), np.arange(0, start)])
    rolled = poly[order]
    closed = np.vstack([rolled, rolled[:1]])
    runs = line_runs(closed)
    sides: list[tuple[int, int, int, float]] = []  # (i, j, axis, level): axis 0 = horizontal
    for i, j, c, d in runs:
        ang = math.degrees(math.atan2(d[1], d[0])) % 180.0
        if min(ang, 180.0 - ang) <= snap_axis_deg:
            axis, level = 0, _level(closed[i:j + 1, 1], float(c[1]))
        elif abs(ang - 90.0) <= snap_axis_deg:
            axis, level = 1, _level(closed[i:j + 1, 0], float(c[0]))
        else:
            return None
        if sides and sides[-1][2] == axis and abs(sides[-1][3] - level) <= MERGE_LEVEL:
            # a node on a side can split its run in two
            pi, pj, _pa, plevel = sides[-1]
            wa, wb = pj - pi, j - i
            sides[-1] = (pi, j, axis, (plevel * wa + level * wb) / max(wa + wb, 1))
            continue
        sides.append((i, j, axis, level))
    if len(sides) == 5 and sides[0][2] == sides[-1][2] and abs(sides[0][3] - sides[-1][3]) <= MERGE_LEVEL:
        # a side cut by the loop's start: its two runs are one
        i, _j, axis, level = sides.pop()
        sides[0] = (i - n, sides[0][1], axis, 0.5 * (level + sides[0][3]))
    if len(sides) != 4 or any(sides[k][2] == sides[(k + 1) % 4][2] for k in range(4)):
        return None
    xs = sorted(s[3] for s in sides if s[2] == 1)
    ys = sorted(s[3] for s in sides if s[2] == 0)
    m = Model(xs[0], ys[0], xs[1], ys[1], [0.0] * 4, [0.0] * 4, [np.zeros(0, dtype=np.int64)] * 4)
    if m.w < RECT_MIN_SIDE or m.h < RECT_MIN_SIDE:
        return None
    for k in range(4):
        a, b = sides[k], sides[(k + 1) % 4]
        lo, hi = a[1] - CORNER_READ, b[0] + CORNER_READ
        if hi < lo:
            hi += n
        x_side = a[3] if a[2] == 1 else b[3]
        y_side = a[3] if a[2] == 0 else b[3]
        name = ("L" if abs(x_side - m.x0) <= abs(x_side - m.x1) else "R") + ("T" if abs(y_side - m.y0) <= abs(y_side - m.y1) else "B")
        m.gaps[CORNERS.index(name)] = order[np.arange(lo, hi + 1) % n]
    return m


def fit_radii(m: Model, poly: np.ndarray, trusted: np.ndarray) -> None:
    """Each corner's radius: the least-squares radius of the circle tangent to
    both sides (at the model's levels) through the corner's trusted vertices.
    A radius under SHARP_R is a sharp corner. `votes` counts the vertices that
    lie on the arc, the radius's weight when radii are pooled."""
    r_max = 0.5 * min(m.w, m.h)
    for c in range(4):
        idx = m.gaps[c]
        gap = poly[idx][trusted[idx]]
        m.r[c], m.votes[c] = 0.0, 0.0
        if len(gap) == 0:
            continue
        cx, cy, sx, sy = _corner_frame(m, c)
        u = sx * (gap[:, 0] - cx)
        v = sy * (gap[:, 1] - cy)
        r = _fit_radius(u, v, r_max)
        m.r[c] = 0.0 if r < SHARP_R else r
        m.votes[c] = float(np.count_nonzero((u < r) & (v < r)))


def holds(m: Model, poly: np.ndarray, trusted: np.ndarray, tol: float, worst: float | None = None) -> bool:
    """The trusted vertices on the outline as a circular arc must be on its
    circle (`curves.fit_arc_run`): the 95th percentile within `tol`, the worst
    within `worst` (2·tol), and the RMS within RECT_RMS, the placement's own
    noise with room for a soft edge."""
    dist = outline_distance(poly[trusted], m)
    if len(dist) == 0:
        return False
    worst = 2.0 * tol if worst is None else worst
    return (float(np.percentile(dist, 95)) <= tol and float(dist.max()) <= worst
            and float(np.sqrt(np.mean(dist * dist))) <= RECT_RMS)


def fit_model(poly: np.ndarray, trusted: np.ndarray, snap_axis_deg: float, tol: float) -> Model | None:
    """The axis-aligned rounded rectangle through a closed ring, or None:
    `fit_sides`, then `fit_radii`, kept when it `holds` the trusted vertices
    within `tol`. `trusted` is False for vertices inside a node's approach
    window, which the arc fit does not believe either (`topology.NODE_TRIM`)."""
    m = fit_sides(poly, snap_axis_deg)
    if m is None:
        return None
    fit_radii(m, poly, trusted)
    return m if holds(m, poly, trusted, tol) else None


# --- the outline as segments -------------------------------------------------------


def _pieces(m: Model) -> list[tuple[str, np.ndarray, np.ndarray, np.ndarray | None, float]]:
    """The outline clockwise on screen from the top side's left end, as
    (kind, start, end, centre, radius) with kind "L" or "A"; empty pieces
    (a sharp corner's arc, a side eaten by two radii) are left out."""
    rlt, rrt, rrb, rlb = m.r
    x0, y0, x1, y1 = m.x0, m.y0, m.x1, m.y1
    pts = [
        np.array([x0 + rlt, y0]), np.array([x1 - rrt, y0]),
        np.array([x1, y0 + rrt]), np.array([x1, y1 - rrb]),
        np.array([x1 - rrb, y1]), np.array([x0 + rlb, y1]),
        np.array([x0, y1 - rlb]), np.array([x0, y0 + rlt]),
    ]
    centres = [np.array([x1 - rrt, y0 + rrt]), np.array([x1 - rrb, y1 - rrb]), np.array([x0 + rlb, y1 - rlb]), np.array([x0 + rlt, y0 + rlt])]
    radii = [rrt, rrb, rlb, rlt]
    out = []
    for k in range(4):
        a, b = pts[2 * k], pts[2 * k + 1]
        if float(np.linalg.norm(b - a)) > 1e-9:
            out.append(("L", a, b, None, 0.0))
        c, d = pts[2 * k + 1], pts[(2 * k + 2) % 8]
        if radii[k] > 0.0:
            out.append(("A", c, d, centres[k], radii[k]))
    return out


def _piece_length(piece) -> float:
    kind, a, b, _c, r = piece
    return float(np.linalg.norm(b - a)) if kind == "L" else 0.5 * math.pi * r


def _piece_point(piece, t: float) -> np.ndarray:
    kind, a, b, c, r = piece
    if kind == "L":
        return a + (b - a) * t
    a0 = math.atan2(a[1] - c[1], a[0] - c[0])
    ang = a0 + 0.5 * math.pi * t  # clockwise on screen: the angle grows
    return np.array([c[0] + r * math.cos(ang), c[1] + r * math.sin(ang)])


def _piece_project(piece, p: np.ndarray) -> tuple[float, float]:
    """(t in [0, 1], distance) of the nearest point of the piece to p."""
    kind, a, b, c, r = piece
    if kind == "L":
        d = b - a
        t = float(np.clip((p - a) @ d / max(float(d @ d), 1e-18), 0.0, 1.0))
        return t, float(np.linalg.norm(a + d * t - p))
    a0 = math.atan2(a[1] - c[1], a[0] - c[0])
    ang = math.atan2(p[1] - c[1], p[0] - c[0])
    t = ((ang - a0) % (2.0 * math.pi)) / (0.5 * math.pi)
    if t > 1.0:
        # outside the quarter: the nearer end
        t = 1.0 if t < 2.5 else 0.0
    q = _piece_point(piece, t)
    return t, float(np.linalg.norm(q - p))


def perimeter(m: Model) -> float:
    return sum(_piece_length(pc) for pc in _pieces(m))


def project(m: Model, p: np.ndarray) -> tuple[float, float]:
    """(arc-length position clockwise from the top side's left end, distance)
    of the nearest outline point to p. Ties go to the earlier piece."""
    s = 0.0
    best: tuple[float, float] | None = None
    for piece in _pieces(m):
        length = _piece_length(piece)
        t, d = _piece_project(piece, p)
        if best is None or d < best[1] - 1e-12:
            best = (s + t * length, d)
        s += length
    assert best is not None
    return best


def point_at(m: Model, s: float) -> np.ndarray:
    pieces = _pieces(m)
    total = sum(_piece_length(pc) for pc in pieces)
    s = s % total
    for piece in pieces:
        length = _piece_length(piece)
        if s <= length + 1e-12:
            return _piece_point(piece, min(s / max(length, 1e-18), 1.0))
        s -= length
    return _piece_point(pieces[-1], 1.0)


def subpath(m: Model, s0: float, s1: float, whole: bool = False) -> list[Segment]:
    """The outline clockwise from position s0 to s1 (the whole loop when
    `whole`), as lines and quarter-circle (or shorter) arcs."""
    pieces = _pieces(m)
    total = sum(_piece_length(pc) for pc in pieces)
    s0 = s0 % total
    span = total if whole else (s1 - s0) % total
    out: list[Segment] = []
    s = 0.0
    # walk the pieces twice round, so a span that wraps is one pass
    for lap in range(2):
        for piece in pieces:
            length = _piece_length(piece)
            a, b = s, s + length
            s = b
            lo, hi = max(a, s0), min(b, s0 + span)
            if hi - lo <= 1e-9:
                continue
            p = _piece_point(piece, (lo - a) / length)
            q = _piece_point(piece, (hi - a) / length)
            if piece[0] == "L":
                out.append(Line(p, q))
            else:
                out.append(CircArc(p, q, float(piece[4]), False, True))
    return out


# --- clustering ------------------------------------------------------------------


def cluster_1d(values: list[float], weights: list[float], move: float) -> list[tuple[float, list[int]]]:
    """Groups of values that are one value, and that value.

    Sorted values are linked where neighbours are within 2·`move`; a group is
    kept whole when every member is within `move` of its weighted mean, and is
    otherwise split at its widest gap and each part tried again. Returns
    (value, member indices) per group, singletons included. Ties in the sort
    are broken by index, so the answer does not depend on the input order of
    equal values."""
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    groups: list[list[int]] = []
    for i in order:
        if groups and values[i] - values[groups[-1][-1]] <= 2.0 * move:
            groups[-1].append(i)
        else:
            groups.append([i])
    out: list[tuple[float, list[int]]] = []

    def settle(members: list[int]) -> None:
        w = sum(weights[i] for i in members)
        mean = sum(values[i] * weights[i] for i in members) / w if w > 0 else sum(values[i] for i in members) / len(members)
        if len(members) == 1 or all(abs(values[i] - mean) <= move for i in members):
            out.append((mean, members))
            return
        gaps = [values[members[k + 1]] - values[members[k]] for k in range(len(members) - 1)]
        cut = max(range(len(gaps)), key=lambda k: (gaps[k], -k)) + 1
        settle(members[:cut])
        settle(members[cut:])

    for g in groups:
        settle(g)
    return out


# --- fillets: a rounded corner between two lines, anywhere in the graph --------


def fillet_frame(da: np.ndarray, db: np.ndarray) -> tuple[float, np.ndarray]:
    """(half the interior angle, unit bisector into the corner) of the corner
    between a line arriving along `da` and one leaving along `db`."""
    turn = math.acos(max(-1.0, min(1.0, float(da @ db))))
    bis = db - da
    return 0.5 * (math.pi - turn), bis / max(float(np.linalg.norm(bis)), 1e-12)


def fillet_dist(pts: np.ndarray, x: np.ndarray, da: np.ndarray, db: np.ndarray, r: float) -> np.ndarray:
    """Distance from points to the corner at x rounded with radius r: the arc
    tangent to both lines between its tangent points, the lines beyond."""
    half, bis = fillet_frame(da, db)
    reach = r / math.tan(half)
    rel = pts - x
    sa = rel @ da
    sb = rel @ db
    na = np.abs(rel @ np.array([-da[1], da[0]]))
    nb = np.abs(rel @ np.array([-db[1], db[0]]))
    on_a = sa <= -reach
    on_b = sb >= reach
    c = x + bis * (r / math.sin(half))
    arc = np.abs(np.hypot(pts[:, 0] - c[0], pts[:, 1] - c[1]) - r)
    d = np.where(on_a | on_b, np.inf, arc)
    d = np.minimum(d, np.where(on_a, na, np.inf))
    return np.minimum(d, np.where(on_b, nb, np.inf))


def fit_fillet(pts: np.ndarray, x: np.ndarray, da: np.ndarray, db: np.ndarray, r_max: float) -> float:
    """The least-squares fillet radius in [0, r_max], by golden section."""
    lo, hi = 0.0, r_max
    phi = (math.sqrt(5.0) - 1.0) / 2.0

    def cost(r: float) -> float:
        e = fillet_dist(pts, x, da, db, r)
        return float(e @ e)

    a, b = hi - phi * (hi - lo), lo + phi * (hi - lo)
    fa, fb = cost(a), cost(b)
    for _ in range(RADIUS_ITER):
        if fa <= fb:
            hi, b, fb = b, a, fa
            a = hi - phi * (hi - lo)
            fa = cost(a)
        else:
            lo, a, fa = a, b, fb
            b = lo + phi * (hi - lo)
            fb = cost(b)
    return 0.5 * (lo + hi)


def fillet_points(x: np.ndarray, da: np.ndarray, db: np.ndarray, r: float) -> tuple[np.ndarray, np.ndarray, bool]:
    """The fillet's two tangent points, and its SVG sweep flag."""
    half, _bis = fillet_frame(da, db)
    reach = r / math.tan(half)
    sweep = float(da[0] * db[1] - da[1] * db[0]) > 0.0  # turning clockwise on screen
    return x - da * reach, x + db * reach, sweep


# --- how soft the image is -------------------------------------------------------


def _ndtr(z: np.ndarray) -> np.ndarray:
    """The standard normal CDF, by Abramowitz & Stegun 7.1.26 for erf (absolute
    error under 1.5e-7): explicit constants, so the Rust twin computes the
    same numbers without a special-functions library."""
    x = np.abs(z) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * x)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1.0 - poly * np.exp(-x * x)
    return 0.5 * (1.0 + np.sign(z) * erf)


def _inside(d: np.ndarray, sigma: float) -> np.ndarray:
    """How much of a pixel whose centre lies d px outside a straight edge is
    inside it, when the edge is blurred by a Gaussian of `sigma` and then
    averaged over the pixel's own square: the integral of Φ over the pixel,
    which has a closed form, x·Φ(x/σ) + σ·φ(x/σ)."""
    if sigma < 1e-3:
        return np.clip(0.5 - d, 0.0, 1.0)

    def g(x: np.ndarray) -> np.ndarray:
        z = x / sigma
        return x * _ndtr(z) + sigma * np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)

    return g(0.5 - d) - g(-0.5 - d)


def edge_sigma(rgb: np.ndarray, m: Model) -> tuple[float, int] | None:
    """The blur of the image along the model's four sides, and the number of
    pixels it was read from: pixels near the middle of each side, their colour
    put on the line between the two colours read either side of it, fitted
    with a blurred edge at the side's level (`_inside`) by golden section on
    the Gaussian's σ. The pixel's own square is in the model, so a hard edge
    reads 0. None when the sides are too short to read."""
    h, w = rgb.shape[:2]
    ds: list[np.ndarray] = []
    qs: list[np.ndarray] = []
    rmax = max(m.r) if m.r else 0.0
    for side in range(4):
        axis = 0 if side < 2 else 1          # the coordinate the side fixes (0: x)
        level = (m.x0, m.x1, m.y0, m.y1)[side]
        out = -1.0 if side in (0, 2) else 1.0  # outward along that coordinate
        lo, hi = (m.y0, m.y1) if axis == 0 else (m.x0, m.x1)
        rows = np.arange(int(math.ceil(lo + rmax + EDGE_END - 0.5)), int(math.floor(hi - rmax - EDGE_END - 0.5)) + 1)
        if len(rows) < 3:
            continue
        cols = np.arange(int(math.floor(level - EDGE_PLATEAU[1] - 0.5)), int(math.ceil(level + EDGE_PLATEAU[1] - 0.5)) + 1)
        if rows[0] < 0 or cols[0] < 0 or (rows[-1] >= h if axis == 0 else rows[-1] >= w) or (cols[-1] >= w if axis == 0 else cols[-1] >= h):
            continue
        patch = rgb[rows[:, None], cols[None, :]] if axis == 0 else rgb[cols[None, :], rows[:, None]]
        d = out * ((cols + 0.5) - level)     # px outside the side, per column
        inner = (d <= -EDGE_PLATEAU[0]) & (d >= -EDGE_PLATEAU[1])
        outer = (d >= EDGE_PLATEAU[0]) & (d <= EDGE_PLATEAU[1])
        if not inner.any() or not outer.any():
            continue
        c_in = np.median(patch[:, inner].reshape(-1, 3), axis=0)
        c_out = np.median(patch[:, outer].reshape(-1, 3), axis=0)
        axis_c = c_in - c_out
        n2 = float(axis_c @ axis_c)
        if n2 < 100.0:                       # under 10 levels of contrast: nothing to read
            continue
        near = np.abs(d) <= EDGE_REACH
        q = ((patch[:, near] - c_out) @ axis_c) / n2
        ds.append(np.broadcast_to(d[near], q.shape).ravel())
        qs.append(q.ravel())
    if not ds:
        return None
    d = np.concatenate(ds)
    q = np.concatenate(qs)

    def cost(sig: float) -> float:
        e = _inside(d, sig) - q
        return float(e @ e)

    lo, hi = 0.0, EDGE_SIGMA_MAX
    phi = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = hi - phi * (hi - lo), lo + phi * (hi - lo)
    fa, fb = cost(a), cost(b)
    for _ in range(RADIUS_ITER):
        if fa <= fb:
            hi, b, fb = b, a, fa
            a = hi - phi * (hi - lo)
            fa = cost(a)
        else:
            lo, a, fa = a, b, fb
            b = lo + phi * (hi - lo)
            fb = cost(b)
    return 0.5 * (lo + hi), int(len(d))
