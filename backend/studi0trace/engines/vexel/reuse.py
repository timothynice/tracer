"""Stage 10b: repeated shapes as `<use>`.

A dot grid, a row of bullets, a tiled pattern: the same shape painted many
times. Each copy is traced on its own and the copies agree to a few hundredths
of a pixel, so the file says so — the first copy's geometry goes into `<defs>`
once, with no fill of its own, and every copy is a `<use>` that names it,
carries its own translation and its own fill. Painter's order is kept: each
`<use>` stands exactly where the copy's element would have.

Two shapes are copies when they are the same kind of primitive with the same
dimensions to USE_TOL, or two paths whose outlines, one moved onto the other
by the difference of their bounding boxes, lie within USE_TOL of each other
both ways (mean nearest distance).
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from studi0trace.engines.vexel.curves import (
    CircArc,
    Circle,
    Cubic,
    Ellipse,
    Line,
    PathShape,
    Rect,
    RoundedRect,
    Shape,
    _f,
    arc_points,
    path_d,
)

USE_TOL = 0.10        # px; copies agree to this
USE_MIN_POINTS = 8    # a path this small is not worth a reference


def _sample(shape: PathShape, per_segment: int = 12) -> np.ndarray:
    t = np.linspace(0.0, 1.0, per_segment)[:, None]
    out = []
    for contour in shape.contours:
        for seg in contour:
            if isinstance(seg, Line):
                out.append(seg.p0 * (1 - t) + seg.p1 * t)
            elif isinstance(seg, CircArc):
                out.append(arc_points(seg, per_segment))
            else:
                out.append((1 - t) ** 3 * seg.p0 + 3 * (1 - t) ** 2 * t * seg.c1 + 3 * (1 - t) * t ** 2 * seg.c2 + t ** 3 * seg.p1)
    return np.vstack(out) if out else np.zeros((0, 2))


def _signature(shape: Shape) -> tuple:
    if isinstance(shape, PathShape):
        return ("path", tuple(tuple(type(s).__name__ for s in c) for c in shape.contours))
    return (type(shape).__name__,)


def _origin(shape: Shape, pts: np.ndarray | None) -> np.ndarray:
    if isinstance(shape, (Circle, Ellipse)):
        return np.array([shape.cx, shape.cy])
    if isinstance(shape, (Rect, RoundedRect)):
        return np.array([shape.x, shape.y])
    return pts.min(axis=0)


def _same(a: Shape, b: Shape, pa: np.ndarray | None, pb: np.ndarray | None) -> np.ndarray | None:
    """The translation carrying `a` onto `b` when they are copies, else None."""
    if _signature(a) != _signature(b):
        return None
    if isinstance(a, Circle):
        return np.array([b.cx - a.cx, b.cy - a.cy]) if abs(a.r - b.r) <= USE_TOL else None
    if isinstance(a, Ellipse):
        ok = abs(a.rx - b.rx) <= USE_TOL and abs(a.ry - b.ry) <= USE_TOL and abs(a.angle_deg - b.angle_deg) <= 0.5
        return np.array([b.cx - a.cx, b.cy - a.cy]) if ok else None
    if isinstance(a, Rect):
        ok = abs(a.w - b.w) <= USE_TOL and abs(a.h - b.h) <= USE_TOL
        return np.array([b.x - a.x, b.y - a.y]) if ok else None
    if isinstance(a, RoundedRect):
        ok = abs(a.w - b.w) <= USE_TOL and abs(a.h - b.h) <= USE_TOL and abs(a.rx - b.rx) <= USE_TOL
        return np.array([b.x - a.x, b.y - a.y]) if ok else None
    if len(pa) < USE_MIN_POINTS or len(pb) != len(pa):
        return None
    shift = pb.min(axis=0) - pa.min(axis=0)
    moved = pa + shift
    if float(cKDTree(pb).query(moved)[0].mean()) > USE_TOL or float(cKDTree(moved).query(pb)[0].mean()) > USE_TOL:
        return None
    return shift


def _translated(shape: Shape, shift: np.ndarray) -> Shape:
    """The shape moved by −shift is the definition; we move the *first* copy to
    the origin of the definition, which is itself, so this is only used to
    write the definition without a fill."""
    return shape


def _geometry(shape: Shape, sid: str, precision: int) -> str:
    """The definition: geometry only, no paint."""
    from studi0trace.engines.vexel.curves import shape_svg

    return shape_svg(shape, f'id="{sid}"', precision)


def emit(pending: list[tuple[Shape, str]], precision: int, first_id: int = 1) -> tuple[list[str], list[str]]:
    """`pending` is (shape, attrs) in paint order. Returns (defs, elements):
    every element is either the shape's own markup or a `<use>` of a definition
    written once into `defs`."""
    samples = [(_sample(s) if isinstance(s, PathShape) else None) for s, _a in pending]
    group_of: list[int | None] = [None] * len(pending)
    shifts: list[np.ndarray | None] = [None] * len(pending)
    groups: list[list[int]] = []
    for i in range(len(pending)):
        if group_of[i] is not None:
            continue
        members = [i]
        for j in range(i + 1, len(pending)):
            if group_of[j] is not None:
                continue
            shift = _same(pending[i][0], pending[j][0], samples[i], samples[j])
            if shift is not None:
                group_of[j] = len(groups)
                shifts[j] = shift
                members.append(j)
        if len(members) > 1:
            group_of[i] = len(groups)
            shifts[i] = np.zeros(2)
            groups.append(members)
    defs: list[str] = []
    elements: list[str] = []
    ids: dict[int, str] = {}
    for g, members in enumerate(groups):
        sid = f"u{first_id + g}"
        ids[g] = sid
        defs.append(_geometry(pending[members[0]][0], sid, precision))
    for i, (shape, attrs) in enumerate(pending):
        g = group_of[i]
        if g is None:
            from studi0trace.engines.vexel.curves import shape_svg

            elements.append(shape_svg(shape, attrs, precision))
            continue
        dx, dy = shifts[i]
        pos = "" if abs(dx) < 1e-9 and abs(dy) < 1e-9 else f' x="{_f(float(dx), precision)}" y="{_f(float(dy), precision)}"'
        elements.append(f'<use href="#{ids[g]}"{pos} {attrs}/>')
    return defs, elements
