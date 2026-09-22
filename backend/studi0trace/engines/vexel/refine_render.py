"""Stage 9b (opt-in): render-and-compare refinement of the boundary graph.

Everything upstream places an edge from a model of anti-aliasing; this stage
asks the renderer. For each arc, the two shapes on either side are drawn as
they currently stand into a small crop around a control point, at 4× and
averaged back to source pixels, and compared with the source inside a band
along the arc. Each interior control point is then nudged along its normal by
`step` and the nudge is kept when the band error falls by more than a noise
floor. Nodes never move (every arc at them agrees on them), lines stay lines
(only joints move, and a joint carries the adjacent control arms with it so
the curve stays G1), circular arcs are left as the exact circles they are.

The crop's origin is integer-aligned: a fractional origin shifts the 4×
sampling grid against the source pixels and the optimiser then chases the
misalignment (a spike that did so made an exact square worse).
"""
from __future__ import annotations

import io
from collections.abc import Callable

import numpy as np
import resvg_py
from PIL import Image
from scipy.spatial import cKDTree

from studi0trace.engines.vexel.curves import CircArc, Cubic, Line, Segment, Shape, shape_svg

SCALE = 4          # render oversampling
HALF = 8           # px; crop reaches this far from the point being moved
BAND = 2.0         # px; source pixels within this of the arc are compared
NOISE = 0.02       # mean grey levels; a move has to beat this to be kept
SVG_NS = 'xmlns="http://www.w3.org/2000/svg"'


def _render(doc: str, w: int, h: int) -> np.ndarray:
    png = resvg_py.svg_to_bytes(svg_string=doc, width=w * SCALE, height=h * SCALE)
    img = np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"), dtype=np.float32)
    return img.reshape(h, SCALE, w, SCALE, 4).mean(axis=(1, 3))


def _on_white(rgba: np.ndarray) -> np.ndarray:
    a = rgba[..., 3:4] / 255.0
    return rgba[..., :3] * a + 255.0 * (1.0 - a)


class _Crop:
    """One integer-aligned window around a point, with its band mask."""

    def __init__(self, at: np.ndarray, size: tuple[int, int], arc_pts: np.ndarray, src: np.ndarray):
        height, width = size
        self.x0 = int(max(0, np.floor(at[0] - HALF)))
        self.y0 = int(max(0, np.floor(at[1] - HALF)))
        x1 = int(min(width, np.ceil(at[0] + HALF)))
        y1 = int(min(height, np.ceil(at[1] + HALF)))
        self.w, self.h = x1 - self.x0, y1 - self.y0
        ys, xs = np.mgrid[self.y0:y1, self.x0:x1]
        centres = np.column_stack([xs.ravel() + 0.5, ys.ravel() + 0.5])
        dist = cKDTree(arc_pts).query(centres)[0]
        self.band = (dist <= BAND).reshape(self.h, self.w)
        self.src = _on_white(src[self.y0:y1, self.x0:x1].astype(np.float32))

    def error(self, defs: str, elements: list[str]) -> float:
        if self.w <= 0 or self.h <= 0 or not self.band.any():
            return 0.0
        doc = (f'<svg {SVG_NS} viewBox="{self.x0} {self.y0} {self.w} {self.h}">'
               f'{"<defs>" + defs + "</defs>" if defs else ""}{"".join(elements)}</svg>')
        got = _on_white(_render(doc, self.w, self.h))
        return float(np.abs(got - self.src)[self.band].mean())


def _normal(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    d = b - a
    n = float(np.hypot(d[0], d[1]))
    return np.array([-d[1], d[0]]) / n if n > 1e-9 else np.array([0.0, 1.0])


def _moves(segments: list[Segment]) -> list[tuple[str, int, np.ndarray]]:
    """What may move in an arc: ('c1'|'c2', k, normal) for a cubic's arms and
    ('joint', k, normal) for the joint between segments k and k+1."""
    out: list[tuple[str, int, np.ndarray]] = []
    for k, seg in enumerate(segments):
        if isinstance(seg, Cubic):
            n = _normal(seg.p0, seg.p1)
            out.append(("c1", k, n))
            out.append(("c2", k, n))
        if k + 1 < len(segments):
            nxt = segments[k + 1]
            if isinstance(seg, CircArc) or isinstance(nxt, CircArc):
                continue
            t_in = seg.p1 - (seg.c2 if isinstance(seg, Cubic) else seg.p0)
            t_out = (nxt.c1 if isinstance(nxt, Cubic) else nxt.p1) - nxt.p0
            t = t_in / max(float(np.hypot(*t_in)), 1e-9) + t_out / max(float(np.hypot(*t_out)), 1e-9)
            n = np.array([-t[1], t[0]])
            nn = float(np.hypot(*n))
            out.append(("joint", k, n / nn if nn > 1e-9 else _normal(seg.p0, seg.p1)))
    return out


def _apply(segments: list[Segment], move: tuple[str, int, np.ndarray], delta: np.ndarray) -> None:
    kind, k, _n = move
    seg = segments[k]
    if kind == "c1":
        seg.c1 = seg.c1 + delta
    elif kind == "c2":
        seg.c2 = seg.c2 + delta
    else:
        nxt = segments[k + 1]
        seg.p1 = seg.p1 + delta
        nxt.p0 = nxt.p0 + delta
        if isinstance(seg, Cubic):
            seg.c2 = seg.c2 + delta
        if isinstance(nxt, Cubic):
            nxt.c1 = nxt.c1 + delta


def _point(segments: list[Segment], move: tuple[str, int, np.ndarray]) -> np.ndarray:
    kind, k, _n = move
    seg = segments[k]
    return seg.c1 if kind == "c1" else seg.c2 if kind == "c2" else seg.p1


def _node_ends(arcs: list) -> dict[int, list[tuple[int, bool]]]:
    """Node id → the (arc index, at_start) ends that meet there."""
    out: dict[int, list[tuple[int, bool]]] = {}
    for i, arc in enumerate(arcs):
        if arc.closed or not arc.segments:
            continue
        out.setdefault(arc.n0, []).append((i, True))
        out.setdefault(arc.n1, []).append((i, False))
    return out


def _apply_node(arcs: list, ends: list[tuple[int, bool]], delta: np.ndarray) -> None:
    """Move a node: every arc's end there, and the arm next to it, together."""
    for i, at_start in ends:
        segs = arcs[i].segments
        if at_start:
            seg = segs[0]
            seg.p0 = seg.p0 + delta
            if isinstance(seg, Cubic):
                seg.c1 = seg.c1 + delta
        else:
            seg = segs[-1]
            seg.p1 = seg.p1 + delta
            if isinstance(seg, Cubic):
                seg.c2 = seg.c2 + delta


def refine(
    arcs: list,
    neighbours: Callable[[tuple[int, ...]], tuple[str, list[str]]],
    src: np.ndarray,
    iterations: int = 3,
    step: float = 0.1,
) -> int:
    """Refine the graph's fitted segments in place: a node moves with every
    arc that meets it, an interior control point along its normal. Nodes on the
    canvas frame and arcs against the outside stay. `neighbours(labels)` renders
    the defs and the elements of the shapes painting any of `labels` from the
    graph as it currently stands. Returns the number of moves kept."""
    height, width = src.shape[:2]
    kept = 0
    nodes = _node_ends(arcs)
    for _ in range(iterations):
        # nodes first: every arc at one agrees on it, and a node a tenth of a
        # pixel off tilts every straight side that ends there
        for node, ends in nodes.items():
            incident = [arcs[i] for i, _s in ends]
            if any(0 in a.pair for a in incident):
                continue  # on the frame, or against the outside
            if any((arcs[i].tip0 if at_start else arcs[i].tip1) for i, at_start in ends):
                # a wedge tip: pixels barely change along its bisector, so the
                # renderer cannot place it, and the approach lines already did
                continue
            labels = tuple(sorted({lab for a in incident for lab in a.pair}))
            first, at_start = ends[0]
            seg = arcs[first].segments[0] if at_start else arcs[first].segments[-1]
            at = (seg.p0 if at_start else seg.p1).copy()
            defs, elements = neighbours(labels)
            if len(elements) < 2:
                continue
            crop = _Crop(at, (height, width), np.vstack([a.pts for a in incident]), src)
            if not crop.band.any():
                continue
            best = crop.error(defs, elements)
            for delta in (np.array([step, 0.0]), np.array([-step, 0.0]), np.array([0.0, step]), np.array([0.0, -step])):
                _apply_node(arcs, ends, delta)
                defs, elements = neighbours(labels)
                err = crop.error(defs, elements)
                if err < best - NOISE:
                    best = err
                    kept += 1
                    continue
                _apply_node(arcs, ends, -delta)
        for arc in arcs:
            if not arc.segments or 0 in arc.pair or len(arc.pts) < 2:
                continue
            defs, elements = neighbours(arc.pair)
            if len(elements) < 2:
                continue
            for move in _moves(arc.segments):
                at = _point(arc.segments, move)
                crop = _Crop(at, (height, width), arc.pts, src)
                if not crop.band.any():
                    continue
                best = crop.error(defs, elements)
                for sign in (1.0, -1.0):
                    delta = move[2] * (sign * step)
                    _apply(arc.segments, move, delta)
                    defs, elements = neighbours(arc.pair)
                    err = crop.error(defs, elements)
                    if err < best - NOISE:
                        best = err
                        kept += 1
                        break
                    _apply(arc.segments, move, -delta)
                    defs, elements = neighbours(arc.pair)
    return kept


def element_markup(shape: Shape, attrs: str, precision: int) -> str:
    return shape_svg(shape, attrs, precision)
