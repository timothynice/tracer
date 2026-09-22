"""Stage 7: corners, whole-shape fitting, and piecewise cubic Bézier fitting.

Input polylines come from the boundary graph (a vertex every ≤ 1 px). Corners
are turning angles that persist across two chord scales; between corners a
stretch is fitted lines first — straight runs found from the residuals about
their own total-least-squares line, gaps between them as cubics — and kept
that way when it costs no more segments than the plain curve fit, which is
Schneider's least-squares cubics with recursive splitting (or one C2 spline)
within `tol`. Closed contours with no corners are tried as circles/ellipses
first, four axis-aligned corners as rectangles.
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


def reverse_segments(segments: list[Segment]) -> list[Segment]:
    """The same curve walked the other way — what the region on the far side of
    a shared boundary needs, so that both describe one geometry."""
    out: list[Segment] = []
    for seg in reversed(segments):
        if isinstance(seg, Line):
            out.append(Line(seg.p1.copy(), seg.p0.copy()))
        else:
            out.append(Cubic(seg.p1.copy(), seg.c2.copy(), seg.c1.copy(), seg.p0.copy()))
    return out


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


# --- lines first ---------------------------------------------------------------------
#
# A straight edge is found by fitting a line and looking at the residuals, not by
# measuring vertices against a chord between two pre-placed corners: the corners
# are the least certain points on the outline, and a chord that tilts by a third
# of a pixel made a perfectly straight edge fail the old test and come out as
# cubics that bow. The noise model, measured at every angle from 5 to 100 degrees
# and for every colour pair: vertex RMS 0.046-0.063 px, p95 <= 0.12 px.
LINE_RMS = 0.10       # RMS residual about the total-least-squares line
LINE_P98 = 0.30       # 98th percentile of |residual|
LINE_MIN = 8.1        # px; shorter runs are curves, which keeps a small round corner round (off a half: see topology.NODE_TRIM)
LINE_SAG = 0.10       # px; a run whose residuals bow systematically by more than this is an arc, not a line
LINE_END = 0.15       # px; a run sheds an end vertex that sits further than this off the line - the start of a bend
MERGE_DEG = 2.0       # consecutive lines within this are one line
GAP_MIN = 1.6         # px; a line this short is a stub, not an edge
CORNER_GAP = 2.1      # px; a gap this short between two lines is their corner (a lattice chamfer is three vertices, 1.6 px)
CORNER_REACH = 3.1    # px; a line run ending within this of a corner places the corner
SNAP_END = 0.5        # px; a piece end this close to its line is taken onto the line rather than left as a jog
CHORD_TURN = 20.0     # degrees; runs turning this little against each other are chords of one curve
CHORD_GAP = 12.1      # px; runs this close along the arc are neighbours for that test (a chord sits 5-8 px past the side it follows)
CHORD_END = 2.0 * LINE_MIN  # px; a chord at the end of such a chain is kept as a line only when this long
LINE_COST = 0.5       # a line is cheaper than a cubic when the two answers are weighed


def _prefix(pts: np.ndarray) -> tuple[np.ndarray, ...]:
    x, y = pts[:, 0], pts[:, 1]
    z = np.zeros(1)
    return (
        np.concatenate([z, np.cumsum(x)]),
        np.concatenate([z, np.cumsum(y)]),
        np.concatenate([z, np.cumsum(x * x)]),
        np.concatenate([z, np.cumsum(x * y)]),
        np.concatenate([z, np.cumsum(y * y)]),
    )


def _tls_from_prefix(prefix: tuple[np.ndarray, ...], i: int, j: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Centre, unit direction and RMS residual of the TLS line through pts[i..j]
    inclusive, from prefix sums, so growing a run costs O(1) a step. Written as
    plain sums, in this order, so the Rust side lands on the same bits."""
    n = float(j + 1 - i)
    sx, sy, sxx, sxy, syy = (float(p[j + 1] - p[i]) for p in prefix)
    mx, my = sx / n, sy / n
    cxx, cxy, cyy = sxx / n - mx * mx, sxy / n - mx * my, syy / n - my * my
    half = (cxx + cyy) / 2.0
    spread = math.hypot((cxx - cyy) / 2.0, cxy)
    lam_min = max(half - spread, 0.0)
    lam_max = half + spread
    if abs(cxy) > 1e-15:
        d = np.array([lam_max - cyy, cxy])
    else:
        d = np.array([1.0, 0.0]) if cxx >= cyy else np.array([0.0, 1.0])
    norm = math.hypot(d[0], d[1])
    d = d / norm if norm > 0.0 else np.array([1.0, 0.0])
    return np.array([mx, my]), d, math.sqrt(lam_min)


def _percentile98(values: np.ndarray) -> float:
    """numpy's default (linear) 98th percentile, spelled out for the Rust port."""
    v = np.sort(values)
    if len(v) == 1:
        return float(v[0])
    pos = 0.98 * (len(v) - 1)
    lo = int(math.floor(pos))
    frac = pos - lo
    hi = min(lo + 1, len(v) - 1)
    return float(v[lo] + (v[hi] - v[lo]) * frac)


def _sag(pts: np.ndarray, c: np.ndarray, d: np.ndarray) -> float:
    """How much the run bows: the quadratic term of a parabola fitted to the
    residuals along the run, as its rise over the run's half-length. Noise
    averages out of it; an arc of a circle does not — a 12 px chord on a 60 px
    radius passes the RMS test at 0.09 and shows up here at 0.30."""
    along = (pts - c) @ d
    off = (pts - c) @ np.array([-d[1], d[0]])
    s = along - float(np.sum(along)) / len(along)
    s2 = s * s
    # least squares for off = a*s^2 + b*s + e, written as plain sums
    n = float(len(s))
    S2, S3, S4 = float(np.sum(s2)), float(np.sum(s2 * s)), float(np.sum(s2 * s2))
    O, O1, O2 = float(np.sum(off)), float(np.sum(off * s)), float(np.sum(off * s2))
    # normal equations for (a, b, e): rows [S4 S3 S2; S3 S2 0; S2 0 n]
    det = S4 * (S2 * n) - S3 * (S3 * n) + S2 * (0.0 - S2 * S2)
    if abs(det) < 1e-18:
        return 0.0
    a = (O2 * (S2 * n) - S3 * (O1 * n) + S2 * (0.0 - O * S2)) / det
    half = 0.5 * float(np.max(along) - np.min(along))
    return abs(a) * half * half


def line_runs(pts: np.ndarray) -> list[tuple[int, int, np.ndarray, np.ndarray]]:
    """Maximal straight runs `(i, j, centre, direction)` of an open polyline.

    A run grows from `i` while the RMS residual about its own line stays under
    LINE_RMS, is trimmed from the far end until its 98th-percentile residual is
    under LINE_P98, and is kept if it spans LINE_MIN px and does not bow by
    more than LINE_SAG (a chord of a gentle arc passes the RMS test; its
    residuals are a parabola, and noise's are not). Runs never overlap; the
    scan resumes at the run's end, or one vertex on where no run was found.
    """
    n = len(pts)
    if n < 3:
        return []
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    prefix = _prefix(pts)
    runs: list[tuple[int, int, np.ndarray, np.ndarray]] = []
    i = 0
    while i < n - 2:
        best: tuple[int, np.ndarray, np.ndarray] | None = None
        j = i + 2
        while j < n:
            c, d, rms = _tls_from_prefix(prefix, i, j)
            if rms > LINE_RMS:
                break
            best = (j, c, d)
            j += 1
        if best is not None:
            j, c, d = best
            while j > i + 1:
                off = np.abs((pts[i:j + 1] - c) @ np.array([-d[1], d[0]]))
                if _percentile98(off) <= LINE_P98:
                    break
                j -= 1
                c, d, _ = _tls_from_prefix(prefix, i, j)
            # A run that grew into the start of a bend bows; shed the far end
            # until what is left is straight, rather than losing the side too.
            while j > i + 1 and _sag(pts[i:j + 1], c, d) > LINE_SAG:
                j -= 1
                c, d, _ = _tls_from_prefix(prefix, i, j)
            # A run may also *begin* inside a bend, where the scan resumed after
            # the last run: the bend's vertices at either end tilt the line by a
            # pixel across a card's side. Shed end vertices that sit off it.
            a = i
            while j > a + 1:
                normal = np.array([-d[1], d[0]])
                head = abs(float((pts[a] - c) @ normal))
                tail = abs(float((pts[j] - c) @ normal))
                if head <= LINE_END and tail <= LINE_END:
                    break
                if head > tail:
                    a += 1
                else:
                    j -= 1
                c, d, _ = _tls_from_prefix(prefix, a, j)
            if j > a + 1 and cum[j] - cum[a] >= LINE_MIN:
                # the eigenvector's sign is arbitrary: point it along the run,
                # or a run read backwards looks like a 176 degree turn
                if float((pts[j] - pts[a]) @ d) < 0.0:
                    d = -d
                runs.append((a, j, c, d))
                i = j
                continue
        i += 1
    return runs


def _project(c: np.ndarray, d: np.ndarray, p: np.ndarray) -> np.ndarray:
    return c + d * float((p - c) @ d)


def _turn_deg(a: np.ndarray, b: np.ndarray) -> float:
    return math.degrees(math.acos(min(1.0, max(-1.0, float(a @ b)))))


def lines_first(pts: np.ndarray, tol: float, t_start: np.ndarray | None = None, t_end: np.ndarray | None = None) -> list[Segment] | None:
    """The stretch as its straight runs, each one Line, with the gaps between them
    fitted as cubics that leave and arrive along the neighbouring lines. Two
    lines meeting across a gap shorter than GAP_MIN meet at their intersection:
    a corner. Consecutive runs within MERGE_DEG of one direction, and touching,
    are one line. Returns None where no run was found.

    The first and last points of the stretch are kept exactly: they are nodes
    or corners that the neighbouring stretches have already been fitted to.
    """
    runs = line_runs(pts)
    if not runs:
        return None
    n = len(pts)
    prefix = _prefix(pts)
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    merged: list[tuple[int, int, np.ndarray, np.ndarray]] = []
    for run in runs:
        if merged:
            i0, j0, _c0, d0 = merged[-1]
            i1, j1, _c1, d1 = run
            if _turn_deg(d0, d1) <= MERGE_DEG and cum[i1] - cum[j0] <= CHORD_GAP:
                c, d, rms = _tls_from_prefix(prefix, i0, j1)
                # two degrees over fifty pixels is most of a pixel at the joint:
                # the runs are one line only if the joint fit says so too
                if rms <= LINE_RMS:
                    merged[-1] = (i0, j1, c, d)
                    continue
        merged.append(run)
    runs = merged
    # A big round corner passes the residual test in 10 px chords. A chord in
    # the middle of a curve turns a little against both its neighbours; a
    # polygon side turns a lot against at least one, or has none. A chord at
    # the end of such a chain turns a little against one neighbour only, and is
    # kept as a line only when it is long enough to be an edge in its own right.
    if len(runs) > 1:
        small = [
            cum[runs[k + 1][0]] - cum[runs[k][1]] <= CHORD_GAP and _turn_deg(runs[k][3], runs[k + 1][3]) <= CHORD_TURN
            for k in range(len(runs) - 1)
        ]
        keep = []
        for k, (i, j, _c, _d) in enumerate(runs):
            before = k > 0 and small[k - 1]
            after = k < len(runs) - 1 and small[k]
            length = float(np.linalg.norm(pts[j] - pts[i]))
            if before and after:
                continue
            if (before or after) and length < CHORD_END:
                continue
            keep.append(runs[k])
        runs = keep
        if not runs:
            return None

    segs: list[Segment] = []
    prev: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None  # end point, direction and centre of the last line
    cursor = 0

    def on_line(a: int, b: int, c: np.ndarray, d: np.ndarray) -> bool:
        """Do pts[a..b] all lie within LINE_P98 of the line (c, d)? Then the gap is
        the line's own end, trimmed off the run by the residual test, not a curve."""
        off = (pts[a:b + 1] - c) @ np.array([-d[1], d[0]])
        return bool(np.abs(off).max() <= LINE_P98)

    def snaps(end: np.ndarray, a: int, b: int, c: np.ndarray, d: np.ndarray) -> bool:
        """A short jog whose end point sits within SNAP_END of the line: the line
        takes the end. A jog that reaches further is a real feature and stays."""
        off = abs(float((end - c) @ np.array([-d[1], d[0]])))
        return cum[b] - cum[a] < CORNER_REACH and off <= SNAP_END

    def gap(a: int, b: int, ta: np.ndarray | None, tb: np.ndarray | None) -> list[Segment] | None:
        """Cubics through pts[a..b]; None means "a corner", the two lines meet."""
        run = pts[a:b + 1]
        if len(run) < 2 or float(np.linalg.norm(run[-1] - run[0])) < 1e-9:
            return []
        if float(np.linalg.norm(run[-1] - run[0])) < CORNER_GAP:
            if ta is not None and tb is not None:
                return None
            return [Line(run[0].copy(), run[-1].copy())]
        if len(run) == 2:
            return [Line(run[0].copy(), run[-1].copy())]
        t1 = ta if ta is not None else _end_tangent(run, True)
        t2 = tb if tb is not None else _end_tangent(run, False)
        return list(fit_cubics(run, t1, t2, tol))

    for k, (i, j, c, d) in enumerate(runs):
        if float((pts[j] - pts[i]) @ d) < 0.0:
            d = -d
        start = pts[0].copy() if i == 0 else _project(c, d, pts[i])
        end = pts[n - 1].copy() if j == n - 1 else _project(c, d, pts[j])
        if i > cursor and cursor == 0:
            if on_line(0, i, c, d) or snaps(pts[0], 0, i, c, d):
                start = pts[0].copy()      # the run's own beginning, trimmed by the residual test
            else:
                # the piece starts with a short jog to its corner or node: keep
                # it as one, since snapping the line's start onto that point
                # would tilt the whole line by the jog
                before = gap(0, i, t_start, -d)
                if not before:
                    segs.append(Line(pts[0].copy(), start.copy()))
                else:
                    before[-1].p1 = start.copy()
                    segs.extend(before)
        elif i > cursor:
            absorbed = (prev is not None and on_line(cursor, i, prev[2], prev[1])) or on_line(cursor, i, c, d)
            before = None if (absorbed and prev is not None) else gap(cursor, i, t_start if cursor == 0 else (prev[1] if prev else None), -d)
            if before is None:
                # a corner between two lines: they meet where they cross
                x = _intersect(prev[0], prev[1], c, d)
                if x is not None and float(np.linalg.norm(x - pts[i])) <= 3.0:
                    segs[-1].p1 = x.copy()
                    start = x.copy()
                else:
                    segs.append(Line(segs[-1].p1.copy(), start.copy()))
            else:
                if before and segs:
                    before[0].p0 = segs[-1].p1.copy()
                if before:
                    before[-1].p1 = start.copy()
                segs.extend(before)
        elif segs:
            start = segs[-1].p1.copy()
        segs.append(Line(start, end.copy()))
        prev = (end, d, c)
        cursor = j
    if cursor < n - 1:
        if on_line(cursor, n - 1, prev[2], prev[1]) or snaps(pts[n - 1], cursor, n - 1, prev[2], prev[1]):
            if isinstance(segs[-1], Line):
                segs[-1].p1 = pts[n - 1].copy()
            else:
                segs.append(Line(prev[0].copy(), pts[n - 1].copy()))
        else:
            # a short tail that is not on the line is a jog to a node and stays
            # one: snapping the line's end onto that node tilted a 50 px edge by 2 px
            after = gap(cursor, n - 1, prev[1], t_end)
            if not after:
                segs.append(Line(prev[0].copy(), pts[n - 1].copy()))
            else:
                after[0].p0 = prev[0].copy()
                after[-1].p1 = pts[n - 1].copy()
                segs.extend(after)
    if not np.allclose(segs[0].p0, pts[0]):
        if isinstance(segs[0], Line) and float(np.linalg.norm(segs[0].p0 - pts[0])) <= 3.0:
            segs[0].p0 = pts[0].copy()
        else:
            segs.insert(0, Line(pts[0].copy(), segs[0].p0.copy()))
    return merge_lines(segs)


def merge_lines(segs: list[Segment]) -> list[Segment]:
    """Tidy the lines of a stretch. A line within MERGE_DEG of the line before it
    joins it. A stub shorter than GAP_MIN joins the line before it only if it lies
    on that line; a stub that turns — the chamfer of a lattice corner — between
    two lines is replaced by their intersection, and anywhere else is kept, since
    folding it would swing the neighbouring line's end off its edge by the
    stub's own length (half a pixel across a whole canvas border, once)."""
    out: list[Segment] = []
    k = 0
    while k < len(segs):
        seg = segs[k]
        if out and isinstance(seg, Line) and isinstance(out[-1], Line):
            prev = out[-1]
            a = prev.p1 - prev.p0
            b = seg.p1 - seg.p0
            la, lb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
            if la > 0.0 and lb > 0.0:
                da, db = a / la, b / lb
                off_end = abs(float((seg.p1 - prev.p0) @ np.array([-da[1], da[0]])))
                joined = seg.p1 - prev.p0
                lj = float(np.linalg.norm(joined))
                # the joint must lie on the merged line: an angle alone lets a
                # two-degree bend over fifty pixels move it most of a pixel
                joint_off = abs(float((seg.p0 - prev.p0) @ np.array([-joined[1], joined[0]]) / lj)) if lj > 0 else 0.0
                if (_turn_deg(da, db) <= MERGE_DEG and joint_off <= LINE_END) or (lb < GAP_MIN and off_end <= LINE_END):
                    out[-1] = Line(prev.p0.copy(), seg.p1.copy())
                    k += 1
                    continue
                nxt = segs[k + 1] if k + 1 < len(segs) else None
                if lb < GAP_MIN and isinstance(nxt, Line):
                    c = nxt.p1 - nxt.p0
                    lc = float(np.linalg.norm(c))
                    if lc > 0.0:
                        x = _intersect(prev.p0, da, nxt.p0, c / lc)
                        if x is not None and float(np.linalg.norm(x - seg.p0)) <= 3.0:
                            out[-1] = Line(prev.p0.copy(), x.copy())
                            segs[k + 1] = Line(x.copy(), nxt.p1.copy())
                            k += 1
                            continue
        out.append(seg)
        k += 1
    return out


def corners_from_runs(pieces: list[np.ndarray], closed: bool) -> None:
    """Move each corner shared by two pieces to where their adjacent line runs
    cross. The corner arrived sharpened from a 0.8-3 px window either side, and
    at an acute tip those few vertices are anti-aliasing mixtures pulled inward:
    a triangle's apex sat 0.57 px inside, and a line drawn from it ran inside the
    whole edge. Two runs of tens of pixels place the crossing to a few
    hundredths. Only corners with a run ending within CORNER_REACH on both sides
    move, and never by more than 1.5 px. Pieces are modified in place; the
    corner stays one point for both."""
    n = len(pieces)
    if n < 2:
        return
    runs = [line_runs(piece) for piece in pieces]
    for idx in (range(n) if closed else range(1, n)):
        prev, nxt = pieces[idx - 1], pieces[idx]
        rp, rn = runs[idx - 1], runs[idx]
        if not rp or not rn:
            continue
        _i0, j0, c0, d0 = rp[-1]
        i1, _j1, c1, d1 = rn[0]
        tail = float(np.sum(np.linalg.norm(np.diff(prev[j0:], axis=0), axis=1))) if j0 < len(prev) - 1 else 0.0
        head = float(np.sum(np.linalg.norm(np.diff(nxt[:i1 + 1], axis=0), axis=1))) if i1 > 0 else 0.0
        if tail > CORNER_REACH or head > CORNER_REACH:
            continue
        x = _intersect(c0, d0, c1, d1)
        if x is None or float(np.linalg.norm(x - prev[-1])) > 1.5:
            continue
        prev[-1] = x
        nxt[0] = x


def fit_stretch(pts: np.ndarray, tol: float, t_start: np.ndarray | None = None, t_end: np.ndarray | None = None) -> list[Segment]:
    """One run between two breaks, as lines first or as a curve.

    Both are fitted; the lines-first answer is kept when it needs no more
    segments than the curve. A circle chopped into 9 px runs loses to its one
    cubic; a square's side, one line, ties with one cubic and stays a line; a
    rounded corner between two sides, line-cubic-line, beats the four cubics the
    smooth fitter needs for it. A pinned tangent is honoured by the cubics; a
    line at a pinned end follows its own vertices, which at a wedge tip is the
    tangent that was pinned, and elsewhere is within a degree of it.
    """
    if len(pts) < 2:
        return []
    if t_start is None and t_end is None:
        curve = fit_open(pts, tol)
    else:
        t1 = t_start if t_start is not None else _end_tangent(pts, True)
        t2 = t_end if t_end is not None else _end_tangent(pts, False)
        curve = list(fit_cubics(pts, t1, t2, tol))
    lines = lines_first(pts, tol, t_start, t_end)
    if lines is not None and _cost(lines) <= _cost(curve):
        return lines
    return curve


def _cost(segs: list[Segment]) -> float:
    return sum(LINE_COST if isinstance(s, Line) else 1.0 for s in segs)


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


SPLINE_MIN = 16
SPLINE_SPANS = 24
SPLINE_ROUNDS = 5
SPLINE_CROWD = 0.2


def _knot_vector(interior: list[float]) -> np.ndarray:
    """Clamped cubic knot vector over [0, 1]."""
    return np.concatenate([np.zeros(4), np.asarray(interior, dtype=float), np.ones(4)])


def _bspline_basis(u: np.ndarray, knots: np.ndarray, n_ctrl: int) -> np.ndarray:
    """Cox-de Boor, one row per sample and one column per control point."""
    cur = np.zeros((len(u), len(knots) - 1))
    for j in range(cur.shape[1]):
        if knots[j + 1] > knots[j]:
            cur[:, j] = (u >= knots[j]) & (u < knots[j + 1])
    cur[u >= knots[-1] - 1e-12, int(np.max(np.nonzero(np.diff(knots) > 0)))] = 1.0
    for degree in range(1, 4):
        nxt = np.zeros((len(u), cur.shape[1] - 1))
        for j in range(nxt.shape[1]):
            lo = knots[j + degree] - knots[j]
            hi = knots[j + degree + 1] - knots[j + 1]
            if lo > 0:
                nxt[:, j] += (u - knots[j]) / lo * cur[:, j]
            if hi > 0:
                nxt[:, j] += (knots[j + degree + 1] - u) / hi * cur[:, j + 1]
        cur = nxt
    return cur[:, :n_ctrl]


def _solve(matrix: np.ndarray, rhs: np.ndarray) -> np.ndarray | None:
    """Gauss-Jordan with partial pivoting, written the long way round so the Rust
    side does the same arithmetic."""
    n = len(rhs)
    a = np.concatenate([matrix, rhs[:, None]], axis=1)
    for col in range(n):
        pivot = col + int(np.argmax(np.abs(a[col:, col])))
        if abs(a[pivot, col]) < 1e-12:
            return None
        if pivot != col:
            a[[col, pivot]] = a[[pivot, col]]
        a[col] = a[col] / a[col, col]
        for row in range(n):
            if row != col and a[row, col] != 0.0:
                a[row] = a[row] - a[row, col] * a[col]
    return a[:, n]


def _spline_controls(
    points: np.ndarray, u: np.ndarray, knots: np.ndarray, n_ctrl: int, t1: np.ndarray, t2: np.ndarray
) -> np.ndarray | None:
    """Least-squares control points, with both ends and both end tangents pinned.

    The first and last control points are the run's own ends; the second and the
    second to last are free only along the pinned tangents, which is what keeps
    the join to the neighbouring arc smooth. Everything between is free.
    """
    basis = _bspline_basis(u, knots, n_ctrl)
    head, tail = points[0], points[-1]
    free = n_ctrl - 4
    design = np.zeros((2 * len(u), 2 + 2 * free))
    design[0::2, 0], design[1::2, 0] = basis[:, 1] * t1[0], basis[:, 1] * t1[1]
    design[0::2, 1] = basis[:, n_ctrl - 2] * t2[0]
    design[1::2, 1] = basis[:, n_ctrl - 2] * t2[1]
    for k in range(free):
        design[0::2, 2 + 2 * k] = basis[:, 2 + k]
        design[1::2, 3 + 2 * k] = basis[:, 2 + k]
    fixed = (basis[:, 0:1] + basis[:, 1:2]) * head
    fixed = fixed + (basis[:, n_ctrl - 2:n_ctrl - 1] + basis[:, n_ctrl - 1:n_ctrl]) * tail
    want = np.empty(2 * len(u))
    want[0::2], want[1::2] = (points - fixed)[:, 0], (points - fixed)[:, 1]
    x = _solve(design.T @ design, design.T @ want)
    if x is None or not np.all(np.isfinite(x)):
        return None
    ctrl = np.empty((n_ctrl, 2))
    ctrl[0], ctrl[n_ctrl - 1] = head, tail
    ctrl[1] = head + t1 * x[0]
    ctrl[n_ctrl - 2] = tail + t2 * x[1]
    for k in range(free):
        ctrl[2 + k] = x[2 + 2 * k: 4 + 2 * k]
    return ctrl


def _insert_knot(ctrl: np.ndarray, knots: np.ndarray, at: float) -> tuple[np.ndarray, np.ndarray]:
    """Boehm's knot insertion, once, leaving the curve exactly where it was."""
    k = int(np.searchsorted(knots, at, side="right") - 1)
    out = [ctrl[i] for i in range(k - 2)]
    for i in range(k - 2, k + 1):
        span = knots[i + 3] - knots[i]
        w = 0.0 if span <= 0 else (at - knots[i]) / span
        out.append((1 - w) * ctrl[i - 1] + w * ctrl[i])
    out.extend(ctrl[i] for i in range(k, len(ctrl)))
    return np.array(out), np.insert(knots, k + 1, at)


def _spline_cubics(ctrl: np.ndarray, knots: np.ndarray) -> list[Cubic]:
    """The spline's spans as Bezier segments: insert each interior knot until it
    is threefold, and every four control points are then one cubic."""
    for at in sorted({float(k) for k in knots[4:-4]}):
        while int(np.sum(np.isclose(knots, at))) < 3:
            ctrl, knots = _insert_knot(ctrl, knots, at)
    spans = len(ctrl) // 3
    return [Cubic(*(ctrl[3 * i + j].copy() for j in range(4))) for i in range(spans)]


def _reparametrize_spline(points: np.ndarray, ctrl: np.ndarray, knots: np.ndarray, u: np.ndarray) -> np.ndarray:
    step = 1e-4
    up, um = np.clip(u + step, 0.0, 1.0), np.clip(u - step, 0.0, 1.0)
    here = _bspline_basis(u, knots, len(ctrl)) @ ctrl
    ahead = _bspline_basis(up, knots, len(ctrl)) @ ctrl
    behind = _bspline_basis(um, knots, len(ctrl)) @ ctrl
    q = here - points
    d1 = (ahead - behind) / (up - um)[:, None]
    d2 = (ahead - 2 * here + behind) / (step * step)
    num = np.sum(q * d1, axis=1)
    den = np.sum(d1 * d1, axis=1) + np.sum(q * d2, axis=1)
    move = np.where(np.abs(den) > 1e-12, num / np.where(np.abs(den) > 1e-12, den, 1.0), 0.0)
    return np.clip(u - move, 0.0, 1.0)


def fit_c2(points: np.ndarray, t1: np.ndarray, t2: np.ndarray, tol: float) -> list[Cubic] | None:
    """Fit a run as one clamped cubic B-spline, returned as its Bezier spans.

    A chain built by splitting and recursing is only G1 where it joins: the two
    halves are handed the same tangent direction, but nothing ties their
    curvature, so the curve can bend one way and then abruptly the other at a
    point that is not a corner. That is the hitch. A cubic B-spline is C2
    everywhere by construction, so between one corner and the next it cannot
    kink at all, however many spans it takes. Returns None where no spline
    inside `tol` was found, and the split-and-recurse fit answers instead.
    """
    walk = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))])
    u = walk / walk[-1] if walk[-1] > 0 else np.linspace(0.0, 1.0, len(points))
    interior: list[float] = []
    for _ in range(SPLINE_SPANS):
        n_ctrl = len(interior) + 4
        if len(points) < n_ctrl + 1:
            return None
        knots = _knot_vector(interior)
        moved = u.copy()
        worst = 0
        for _ in range(SPLINE_ROUNDS):
            ctrl = _spline_controls(points, moved, knots, n_ctrl, t1, t2)
            if ctrl is None:
                return None
            off = np.linalg.norm(_bspline_basis(moved, knots, n_ctrl) @ ctrl - points, axis=1)
            worst = int(np.argmax(off))
            if float(off[worst]) < tol:
                return _spline_cubics(ctrl, knots)
            moved = _reparametrize_spline(points, ctrl, knots, moved)
        # One more span, cut where the fit is furthest out - the same place the
        # split-and-recurse fit would have cut, except that the spline stays one
        # curve across it. Crowding a knot against its neighbour buys no freedom
        # and makes the solve ill-conditioned, so a cut landing near one halves
        # the span instead.
        cut = float(np.clip(u[worst], 0.0, 1.0))
        lo = max([0.0, *(k for k in interior if k < cut)])
        hi = min([1.0, *(k for k in interior if k > cut)])
        if hi - lo < 1e-6:
            return None
        if cut - lo < SPLINE_CROWD * (hi - lo) or hi - cut < SPLINE_CROWD * (hi - lo):
            cut = 0.5 * (lo + hi)
        if any(abs(cut - k) < 1e-9 for k in interior):
            return None
        interior = sorted([*interior, cut])
    return None


SPLIT_REACH = 2
TANGENT_SCATTER = 0.10


def _local_tangent(points: np.ndarray, at: int, reach: int) -> np.ndarray:
    """Which way the curve is going at `points[at]`, from a least-squares line in
    arc length through the few vertices either side of it.

    The obvious answer - the chord from one neighbour to the other - is a chord
    two pixels long, and two pixels of a sub-pixel outline is mostly noise. The
    join then leaves a few degrees off the direction the curve is really
    travelling, and that is the hitch you see when you zoom in. A longer chord
    is steadier, but it leans towards its own two ends where the curve bends,
    and the vertices are not evenly spaced, so it leans unevenly. Fitting a line
    against arc length uses all five and weights them by where they actually
    fell. Fitting a quadratic instead was tried: with five vertices it spends
    its freedom following the noise rather than smoothing it.
    """
    chord = _normalize(points[at + 1] - points[at - 1])
    r = min(reach, at, len(points) - 1 - at)
    if r < 2:
        return chord
    w = points[at - r: at + r + 1]
    s = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(w, axis=0), axis=1))])
    if s[-1] <= 1e-9:
        return chord
    # Written as plain sums, in this order, so the Rust side can do the same
    # arithmetic and land on the same bits.
    s = s - float(np.sum(s)) / len(s)
    got = np.array([float(np.sum(s * (w[:, 0] - float(np.sum(w[:, 0])) / len(w)))),
                    float(np.sum(s * (w[:, 1] - float(np.sum(w[:, 1])) / len(w))))])
    if float(got[0] * got[0] + got[1] * got[1]) <= 1e-18:
        return chord
    got = _normalize(got)
    # Only where the five really do lie on a line. Where the placed vertices
    # scramble - a small circle on a small canvas is the usual case - a line
    # through them is a line through noise, and has been seen to come out
    # pointing back the way the curve came. It also declines on a curve tighter
    # than about twenty pixels' radius, where the five span enough of the bend
    # to lean; there the two neighbours are the more local answer anyway, and
    # measuring says the same.
    off = (w - w.mean(axis=0)) - np.outer((w - w.mean(axis=0)) @ got, got)
    scatter = float(np.sqrt(float(np.sum(off * off)) / len(w)))
    return got if scatter <= TANGENT_SCATTER else chord


def fit_cubics(points: np.ndarray, t1: np.ndarray, t2: np.ndarray, tol: float, depth: int = 0) -> list[Cubic]:
    """Schneider: fit one cubic to `points` with end tangents t1 (at start) and t2 (at end,
    pointing backwards); split at the worst point and recurse when needed."""
    if len(points) == 2:
        d = np.linalg.norm(points[1] - points[0]) / 3.0
        return [Cubic(points[0].copy(), points[0] + t1 * d, points[1] + t2 * d, points[1].copy())]
    if depth == 0 and len(points) >= SPLINE_MIN:
        spline = fit_c2(points, t1, t2, tol)
        if spline is not None:
            return spline
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
    centre_t = -_local_tangent(points, split, SPLIT_REACH)
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
        return fit_closed(poly, params.tol), corners
    pieces = [piece for piece in split_pieces(poly, corners) if len(piece) >= 2]
    corners_from_runs(pieces, closed=True)
    segments: list[Segment] = []
    for piece in pieces:
        segments.extend(fit_stretch(piece, params.tol))
    return snap_axis_lines(segments, params.snap_axis_deg), sorted(corners)


def fit_closed(poly: np.ndarray, tol: float) -> list[Segment]:
    """Closed contour without corners: lines first where it has straight runs,
    else the smooth closed fit. A rounded square has no corner sharp enough to
    split it and used to go to the smooth fit whole, sides barrelled; its sides
    are straight runs, and the loop is opened inside the first of them so the
    seam falls on a line. Two collinear lines meeting at the seam become one."""
    smooth = fit_closed_smooth(poly, tol)
    n = len(poly)
    if n < 4:
        return smooth
    runs = line_runs(np.vstack([poly, poly[:1]]))
    if not runs:
        return smooth
    # open the loop in the middle of the longest run: a seam at a run's first
    # vertex can sit in a corner's chamfer, and the two half-lines then meet
    # there with a stub between them
    i, j, _c, _d = max(runs, key=lambda r: r[1] - r[0])
    start = ((i + j) // 2) % n
    rolled = np.vstack([poly[start:], poly[:start], poly[start:start + 1]])
    lines = lines_first(rolled, tol)
    if lines is None:
        return smooth
    if len(lines) > 1 and isinstance(lines[0], Line) and isinstance(lines[-1], Line):
        a = lines[0].p1 - lines[0].p0
        b = lines[-1].p1 - lines[-1].p0
        la, lb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
        if la > 0 and lb > 0 and _turn_deg(a / la, b / lb) <= MERGE_DEG:
            lines[-1] = Line(lines[-1].p0.copy(), lines[0].p1.copy())
            lines = lines[1:]
    return lines if _cost(lines) <= _cost(smooth) else smooth


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
