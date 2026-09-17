"""Stage 7: corners, whole-shape fitting, and piecewise cubic Bézier fitting.

Input polylines come from marching squares (a vertex every ≤ 1 px). Corners
are turning angles that persist across two chord scales; between corners,
Schneider's least-squares cubic fitting with recursive splitting produces
G1-continuous curves within `tol`. Closed contours with no corners are tried
as circles/ellipses first, four axis-aligned corners as rectangles.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


# --- primitives ------------------------------------------------------------------


@dataclass
class Line:
    p0: np.ndarray
    p1: np.ndarray


@dataclass
class Cubic:
    p0: np.ndarray
    c1: np.ndarray
    c2: np.ndarray
    p1: np.ndarray


Segment = Line | Cubic


@dataclass
class Circle:
    cx: float
    cy: float
    r: float


@dataclass
class Ellipse:
    cx: float
    cy: float
    rx: float
    ry: float
    angle_deg: float


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float


@dataclass
class PathShape:
    contours: list[list[Segment]] = field(default_factory=list)  # each closed


Shape = Circle | Ellipse | Rect | PathShape


@dataclass(frozen=True)
class CurveParams:
    corner_threshold: float = 60.0  # degrees
    tol: float = 0.4  # px
    shape_fitting: bool = True
    snap_axis_deg: float = 1.5


# --- helpers ----------------------------------------------------------------------


def _arc_lengths(poly: np.ndarray, closed: bool) -> np.ndarray:
    pts = np.vstack([poly, poly[:1]]) if closed else poly
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(seg)])


def _point_at_arc(poly: np.ndarray, cum: np.ndarray, s: float, closed: bool) -> np.ndarray:
    total = cum[-1]
    if total <= 0:
        return poly[0]
    if closed:
        s = s % total
    else:
        s = min(max(s, 0.0), total)
    i = int(np.searchsorted(cum, s, side="right") - 1)
    i = min(max(i, 0), len(cum) - 2)
    a = poly[i]
    b = poly[(i + 1) % len(poly)] if closed else poly[i + 1]
    span = cum[i + 1] - cum[i]
    f = 0.0 if span <= 0 else (s - cum[i]) / span
    return a + f * (b - a)


def _turning_angle(pb: np.ndarray, p: np.ndarray, pf: np.ndarray) -> float:
    v1 = p - pb
    v2 = pf - p
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    c = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    return math.degrees(math.acos(c))


def _points_at_arcs(poly: np.ndarray, cum: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Vectorised closed-polyline interpolation at arc lengths `s` (wraps around)."""
    total = cum[-1]
    s = np.mod(s, total)
    ext = np.vstack([poly, poly[:1]])
    return np.column_stack([np.interp(s, cum, ext[:, 0]), np.interp(s, cum, ext[:, 1])])


def find_corners(poly: np.ndarray, threshold_deg: float, scales: tuple[float, ...] = (2.0, 4.0)) -> list[int]:
    """Indices of vertices where the contour turns by more than `threshold_deg`
    at every chord scale in `scales` (closed polyline)."""
    n = len(poly)
    if n < 4:
        return []
    cum = _arc_lengths(poly, closed=True)
    total = cum[-1]
    if total < 2 * max(scales):
        return []
    here = cum[:-1]
    angles = np.full(n, np.inf)
    for s in scales:
        pb = _points_at_arcs(poly, cum, here - s)
        pf = _points_at_arcs(poly, cum, here + s)
        v1 = poly - pb
        v2 = pf - poly
        n1 = np.linalg.norm(v1, axis=1)
        n2 = np.linalg.norm(v2, axis=1)
        cosang = np.sum(v1 * v2, axis=1) / np.maximum(n1 * n2, 1e-12)
        ang = np.degrees(np.arccos(np.clip(cosang, -1.0, 1.0)))
        ang[(n1 < 1e-9) | (n2 < 1e-9)] = 0.0
        angles = np.minimum(angles, ang)
    cand = np.nonzero(angles > threshold_deg)[0]
    if cand.size == 0:
        return []
    # non-maximum suppression within the smallest scale along the arc
    window = min(scales)
    pos = here[cand]
    d = np.abs(pos[:, None] - pos[None, :])
    d = np.minimum(d, total - d)
    a = angles[cand]
    beaten = (d <= window) & ((a[None, :] > a[:, None]) | ((a[None, :] == a[:, None]) & (cand[None, :] < cand[:, None])))
    np.fill_diagonal(beaten, False)
    return [int(i) for i in cand[~beaten.any(axis=1)]]


# --- whole shapes ------------------------------------------------------------------


def fit_circle(poly: np.ndarray) -> tuple[Circle, float]:
    """Kåsa algebraic fit. Returns (circle, 95th-percentile radial deviation)."""
    x, y = poly[:, 0], poly[:, 1]
    A = np.column_stack([x, y, np.ones_like(x)])
    b = -(x * x + y * y)
    (a, bb, c), *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = -a / 2, -bb / 2
    r2 = cx * cx + cy * cy - c
    if r2 <= 0:
        return Circle(cx, cy, 0.0), np.inf
    r = math.sqrt(r2)
    dev = np.abs(np.hypot(x - cx, y - cy) - r)
    return Circle(float(cx), float(cy), float(r)), float(np.percentile(dev, 95))


def fit_ellipse(poly: np.ndarray) -> tuple[Ellipse | None, float]:
    """Direct least-squares conic fit (Halir & Flusser 1998), converted to
    centre / semi-axes / angle. Returns (None, inf) when the conic is not an ellipse.
    The deviation is the radial geometric distance, 95th percentile."""
    pts = poly.astype(float)
    mx, my = pts.mean(axis=0)
    x, y = pts[:, 0] - mx, pts[:, 1] - my
    d1 = np.column_stack([x * x, x * y, y * y])
    d2 = np.column_stack([x, y, np.ones_like(x)])
    s1, s2, s3 = d1.T @ d1, d1.T @ d2, d2.T @ d2
    try:
        t = -np.linalg.solve(s3, s2.T)
    except np.linalg.LinAlgError:
        return None, np.inf
    m = s1 + s2 @ t
    m = np.array([m[2] / 2.0, -m[1], m[0] / 2.0])
    evals, evecs = np.linalg.eig(m)
    cond = 4 * evecs[0] * evecs[2] - evecs[1] ** 2
    ok = np.nonzero(np.real(cond) > 0)[0]
    if ok.size == 0:
        return None, np.inf
    a1 = np.real(evecs[:, ok[0]])
    A, B, C = a1
    D, E, F = t @ a1

    Q = np.array([[A, B / 2.0], [B / 2.0, C]])
    try:
        centre = -0.5 * np.linalg.solve(Q, np.array([D, E]))
    except np.linalg.LinAlgError:
        return None, np.inf
    k = -(F + 0.5 * (D * centre[0] + E * centre[1]))
    lam, vec = np.linalg.eigh(Q)
    if k <= 0 or (lam <= 0).any():
        # (p−c)ᵀQ(p−c) = k must be positive definite in the right sign
        if k < 0 and (lam < 0).all():
            k, lam = -k, -lam
        else:
            return None, np.inf
    axes = np.sqrt(k / lam)
    order = np.argsort(-axes)  # major first
    rx, ry = float(axes[order[0]]), float(axes[order[1]])
    major = vec[:, order[0]]
    angle = math.degrees(math.atan2(major[1], major[0]))
    cx, cy = float(centre[0] + mx), float(centre[1] + my)
    if not all(np.isfinite([cx, cy, rx, ry, angle])) or rx <= 0 or ry <= 0:
        return None, np.inf

    # radial geometric deviation in the ellipse frame
    th = math.radians(angle)
    dx, dy = pts[:, 0] - cx, pts[:, 1] - cy
    u = dx * math.cos(th) + dy * math.sin(th)
    v = -dx * math.sin(th) + dy * math.cos(th)
    rho = np.sqrt((u / rx) ** 2 + (v / ry) ** 2)
    dev = np.abs(np.hypot(u, v) * (1.0 - 1.0 / np.maximum(rho, 1e-9)))
    return Ellipse(cx, cy, rx, ry, angle), float(np.percentile(dev, 95))


def _chord_deviation(points: np.ndarray) -> float:
    a, b = points[0], points[-1]
    d = b - a
    n = np.linalg.norm(d)
    if n < 1e-9:
        return float(np.linalg.norm(points - a, axis=1).max())
    return float(np.abs((points[:, 0] - a[0]) * d[1] - (points[:, 1] - a[1]) * d[0]).max() / n)


def _angle_deg(p0: np.ndarray, p1: np.ndarray) -> float:
    return math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0]))


def try_rect(poly: np.ndarray, corners: list[int], params: CurveParams) -> Rect | None:
    if len(corners) != 4:
        return None
    pieces = split_pieces(poly, corners)
    pts = np.array([piece[0] for piece in pieces])
    for k, side in enumerate(pieces):
        if _chord_deviation(side) > params.tol:
            return None
        ang = _angle_deg(pts[k], pts[(k + 1) % 4]) % 180.0
        if min(ang, abs(ang - 90.0), abs(ang - 180.0)) > params.snap_axis_deg:
            return None
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    return Rect(float(x0), float(y0), float(x1 - x0), float(y1 - y0))


# --- Schneider cubic fitting -------------------------------------------------------


def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def _bezier(c: Cubic, t: np.ndarray) -> np.ndarray:
    mt = 1 - t
    return (mt**3)[:, None] * c.p0 + (3 * mt**2 * t)[:, None] * c.c1 + (3 * mt * t**2)[:, None] * c.c2 + (t**3)[:, None] * c.p1


def _bezier_d1(c: Cubic, t: np.ndarray) -> np.ndarray:
    mt = 1 - t
    return 3 * ((mt**2)[:, None] * (c.c1 - c.p0) + (2 * mt * t)[:, None] * (c.c2 - c.c1) + (t**2)[:, None] * (c.p1 - c.c2))


def _bezier_d2(c: Cubic, t: np.ndarray) -> np.ndarray:
    mt = 1 - t
    return 6 * (mt[:, None] * (c.c2 - 2 * c.c1 + c.p0) + t[:, None] * (c.p1 - 2 * c.c2 + c.c1))


def _chord_params(points: np.ndarray) -> np.ndarray:
    d = np.linalg.norm(np.diff(points, axis=0), axis=1)
    u = np.concatenate([[0.0], np.cumsum(d)])
    return u / u[-1] if u[-1] > 0 else np.linspace(0, 1, len(points))


def _generate_bezier(points: np.ndarray, u: np.ndarray, t1: np.ndarray, t2: np.ndarray) -> Cubic:
    p0, p3 = points[0], points[-1]
    mt = 1 - u
    b1 = 3 * mt**2 * u
    b2 = 3 * mt * u**2
    A1 = t1[None, :] * b1[:, None]
    A2 = t2[None, :] * b2[:, None]
    c11 = np.sum(A1 * A1)
    c12 = np.sum(A1 * A2)
    c22 = np.sum(A2 * A2)
    tmp = points - ((mt**3)[:, None] * p0 + (u**3)[:, None] * p3) - (b1[:, None] * p0) - (b2[:, None] * p3)
    x1 = np.sum(A1 * tmp)
    x2 = np.sum(A2 * tmp)
    det = c11 * c22 - c12 * c12
    seg_len = np.linalg.norm(p3 - p0)
    if abs(det) > 1e-12:
        alpha1 = (x1 * c22 - x2 * c12) / det
        alpha2 = (c11 * x2 - c12 * x1) / det
    else:
        alpha1 = alpha2 = seg_len / 3.0
    eps = 1e-6 * max(seg_len, 1.0)
    if alpha1 < eps or alpha2 < eps or alpha1 > 3 * seg_len or alpha2 > 3 * seg_len:
        alpha1 = alpha2 = seg_len / 3.0
    return Cubic(p0.copy(), p0 + t1 * alpha1, p3 + t2 * alpha2, p3.copy())


def _max_error(points: np.ndarray, c: Cubic, u: np.ndarray) -> tuple[float, int]:
    d = np.linalg.norm(_bezier(c, u) - points, axis=1)
    i = int(np.argmax(d[1:-1]) + 1) if len(d) > 2 else len(d) // 2
    return float(d.max()), i


def _reparametrize(points: np.ndarray, c: Cubic, u: np.ndarray) -> np.ndarray:
    q = _bezier(c, u) - points
    d1 = _bezier_d1(c, u)
    d2 = _bezier_d2(c, u)
    num = np.sum(q * d1, axis=1)
    den = np.sum(d1 * d1, axis=1) + np.sum(q * d2, axis=1)
    step = np.where(np.abs(den) > 1e-12, num / np.where(np.abs(den) > 1e-12, den, 1.0), 0.0)
    return np.clip(u - step, 0.0, 1.0)


def fit_cubics(points: np.ndarray, t1: np.ndarray, t2: np.ndarray, tol: float, depth: int = 0) -> list[Cubic]:
    """Schneider: fit one cubic to `points` with end tangents t1 (at start) and t2 (at end,
    pointing backwards); split at the worst point and recurse when needed."""
    if len(points) == 2:
        d = np.linalg.norm(points[1] - points[0]) / 3.0
        return [Cubic(points[0].copy(), points[0] + t1 * d, points[1] + t2 * d, points[1].copy())]
    u = _chord_params(points)
    c = _generate_bezier(points, u, t1, t2)
    err, split = _max_error(points, c, u)
    if err < tol:
        return [c]
    if err < tol * tol * 4 + tol:  # worth trying reparametrisation
        for _ in range(4):
            u = _reparametrize(points, c, u)
            c = _generate_bezier(points, u, t1, t2)
            err, split = _max_error(points, c, u)
            if err < tol:
                return [c]
    if depth > 24 or len(points) < 4:
        return [c]
    split = min(max(split, 1), len(points) - 2)
    centre_t = _normalize(points[split - 1] - points[split + 1])
    left = fit_cubics(points[: split + 1], t1, centre_t, tol, depth + 1)
    right = fit_cubics(points[split:], -centre_t, t2, tol, depth + 1)
    return left + right


def _end_tangent(points: np.ndarray, at_start: bool, k: int = 3) -> np.ndarray:
    k = min(k, len(points) - 1)
    return _normalize(points[k] - points[0]) if at_start else _normalize(points[-1 - k] - points[-1])


def fit_open(points: np.ndarray, tol: float, t_start: np.ndarray | None = None, t_end: np.ndarray | None = None) -> list[Segment]:
    """Fit an open polyline as a line or a chain of cubics."""
    if len(points) < 2:
        return []
    if _chord_deviation(points) <= tol:
        return [Line(points[0].copy(), points[-1].copy())]
    t1 = t_start if t_start is not None else _end_tangent(points, True)
    t2 = t_end if t_end is not None else _end_tangent(points, False)
    return list(fit_cubics(points, t1, t2, tol))


def fit_closed_smooth(poly: np.ndarray, tol: float) -> list[Segment]:
    """Closed contour without corners: start at the vertex of least curvature, G1 at the seam."""
    n = len(poly)
    if n < 3:
        return []
    # start where the contour is straightest so the seam tangent is well defined
    prev = np.roll(poly, 1, axis=0)
    nxt = np.roll(poly, -1, axis=0)
    turn = np.array([_turning_angle(prev[i], poly[i], nxt[i]) for i in range(n)])
    start = int(np.argmin(turn))
    pts = np.vstack([poly[start:], poly[:start], poly[start : start + 1]])
    t = _normalize(pts[1] - pts[-2])
    return fit_open(pts, tol, t_start=t, t_end=-t)


# --- regularity ------------------------------------------------------------------------


def snap_axis_lines(segments: list[Segment], snap_deg: float) -> list[Segment]:
    """Make nearly horizontal/vertical lines exactly so, moving shared endpoints with them."""
    n = len(segments)
    for i, seg in enumerate(segments):
        if not isinstance(seg, Line):
            continue
        ang = _angle_deg(seg.p0, seg.p1) % 180.0
        if min(ang, 180.0 - ang) <= snap_deg:  # horizontal
            y = (seg.p0[1] + seg.p1[1]) / 2
            seg.p0[1] = seg.p1[1] = y
        elif abs(ang - 90.0) <= snap_deg:  # vertical
            x = (seg.p0[0] + seg.p1[0]) / 2
            seg.p0[0] = seg.p1[0] = x
        else:
            continue
        segments[(i - 1) % n].p1 = seg.p0.copy()
        segments[(i + 1) % n].p0 = seg.p1.copy()
    return segments


# --- top level -------------------------------------------------------------------------


def _line_through(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Total-least-squares line: (point, unit direction)."""
    c = points.mean(axis=0)
    if len(points) < 2:
        return c, np.array([1.0, 0.0])
    _, _, vt = np.linalg.svd(points - c, full_matrices=False)
    return c, vt[0]


def _intersect(p: np.ndarray, d: np.ndarray, q: np.ndarray, e: np.ndarray) -> np.ndarray | None:
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-9:
        return None
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return p + t * d


def split_pieces(poly: np.ndarray, corners: list[int], reach: float = 3.0, trim: float = 0.8) -> list[np.ndarray]:
    """Open polylines between consecutive corners, with each corner *sharpened*:
    marching squares chamfers a hard corner by half a pixel, so the true corner is
    recovered as the intersection of lines fitted to the two adjacent sides."""
    corners = sorted(corners)
    n = len(poly)
    cum = _arc_lengths(poly, closed=True)

    def piece_between(i: int, j: int) -> np.ndarray:
        return poly[i : j + 1] if j > i else np.vstack([poly[i:], poly[: j + 1]])

    raw = [piece_between(corners[k], corners[(k + 1) % len(corners)]) for k in range(len(corners))]

    def near(points: np.ndarray, from_start: bool) -> np.ndarray:
        # points within `reach` px of the corner end, excluding the chamfer (< trim px)
        d = np.linalg.norm(points - (points[0] if from_start else points[-1]), axis=1)
        sel = (d >= trim) & (d <= reach)
        if sel.sum() < 2:
            sel = (d > 0) & (d <= reach * 2)
        return points[sel]

    sharp: list[np.ndarray] = []
    for k in range(len(corners)):
        incoming = raw[k - 1]
        outgoing = raw[k]
        corner = poly[corners[k]]
        a_pts, b_pts = near(incoming, False), near(outgoing, True)
        if len(a_pts) >= 2 and len(b_pts) >= 2:
            p, d = _line_through(a_pts)
            q, e = _line_through(b_pts)
            x = _intersect(p, d, q, e)
            if x is not None and np.linalg.norm(x - corner) <= 1.5:
                sharp.append(x)
                continue
        sharp.append(corner.copy())

    pieces: list[np.ndarray] = []
    for k, pts in enumerate(raw):
        start, end = sharp[k], sharp[(k + 1) % len(corners)]
        inner = pts[1:-1]
        if len(inner):
            keep = (np.linalg.norm(inner - pts[0], axis=1) >= trim) & (np.linalg.norm(inner - pts[-1], axis=1) >= trim)
            inner = inner[keep]
        pieces.append(np.vstack([start[None, :], inner, end[None, :]]))
    return pieces


def fit_contour_segments(poly: np.ndarray, params: CurveParams) -> tuple[list[Segment], list[int]]:
    corners = find_corners(poly, params.corner_threshold)
    if not corners:
        return fit_closed_smooth(poly, params.tol), corners
    segments: list[Segment] = []
    for piece in split_pieces(poly, corners):
        if len(piece) < 2:
            continue
        segments.extend(fit_open(piece, params.tol))
    return snap_axis_lines(segments, params.snap_axis_deg), sorted(corners)


def fit_shape(contours: list[np.ndarray], params: CurveParams) -> Shape:
    """Fit a region's contours (outer first). Whole-shape primitives only for single contours."""
    if len(contours) == 1 and params.shape_fitting:
        poly = contours[0]
        corners = find_corners(poly, params.corner_threshold)
        if not corners and len(poly) >= 8:
            circle, dev = fit_circle(poly)
            if dev <= params.tol and circle.r > 1.0:
                return circle
            ellipse, dev = fit_ellipse(poly)
            if ellipse is not None and dev <= params.tol:
                return ellipse
        rect = try_rect(poly, corners, params)
        if rect is not None:
            return rect
    return PathShape(contours=[fit_contour_segments(poly, params)[0] for poly in contours])


# --- serialisation ----------------------------------------------------------------------


def _f(v: float, precision: int) -> str:
    s = f"{v:.{precision}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def path_d(contours: list[list[Segment]], precision: int) -> str:
    parts: list[str] = []
    for segs in contours:
        if not segs:
            continue
        p = segs[0].p0
        parts.append(f"M{_f(p[0], precision)} {_f(p[1], precision)}")
        for s in segs:
            if isinstance(s, Line):
                parts.append(f"L{_f(s.p1[0], precision)} {_f(s.p1[1], precision)}")
            else:
                parts.append(
                    f"C{_f(s.c1[0], precision)} {_f(s.c1[1], precision)} {_f(s.c2[0], precision)} {_f(s.c2[1], precision)} "
                    f"{_f(s.p1[0], precision)} {_f(s.p1[1], precision)}"
                )
        parts.append("Z")
    return "".join(parts)


def shape_svg(shape: Shape, attrs: str, precision: int) -> str:
    p = precision
    if isinstance(shape, Circle):
        return f'<circle cx="{_f(shape.cx, p)}" cy="{_f(shape.cy, p)}" r="{_f(shape.r, p)}" {attrs}/>'
    if isinstance(shape, Ellipse):
        rot = f' transform="rotate({_f(shape.angle_deg, 2)} {_f(shape.cx, p)} {_f(shape.cy, p)})"' if abs(shape.angle_deg) > 0.05 else ""
        return f'<ellipse cx="{_f(shape.cx, p)}" cy="{_f(shape.cy, p)}" rx="{_f(shape.rx, p)}" ry="{_f(shape.ry, p)}" {attrs}{rot}/>'
    if isinstance(shape, Rect):
        return f'<rect x="{_f(shape.x, p)}" y="{_f(shape.y, p)}" width="{_f(shape.w, p)}" height="{_f(shape.h, p)}" {attrs}/>'
    rule = ' fill-rule="evenodd"' if len(shape.contours) > 1 else ""
    return f'<path d="{path_d(shape.contours, p)}" {attrs}{rule}/>'
