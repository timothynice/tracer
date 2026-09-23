"""Stage 6a: the boundary as a planar graph, so neighbours share their edge.

Every earlier version of this stage traced each region on its own: its own
coverage field, its own marching-squares polyline, its own Bézier fit. Two
regions that share an edge therefore described that one edge twice, and the two
descriptions were free to drift apart by up to the fitting tolerance each way.
What falls between them is painted by neither, so the backdrop shows through as
a hairline — dark on a dark preview, white in an exported file.

Here the boundary is built once, as a graph. *Nodes* are the lattice points
where three or more labels meet. *Arcs* are the maximal runs of lattice edges
between two nodes that separate the same pair of labels. Each arc is placed at
sub-pixel precision once and fitted once, and the two regions on either side of
it are handed the same curve, reversed for one of them. They cannot disagree,
at any tolerance, because there is only one description of the edge.

Sub-pixel placement is the measurement `boundary.coverage_field` makes, taken on
an arc instead of on a region: a lattice edge parts two pixels, so the outline
crosses the segment between their centres where the coverage of the label on one
side falls through a half. That is where marching squares would have put its
vertex, which is why this changes the bookkeeping and not where the line lands.

Two things then keep the result smooth. A node is placed where the arcs meeting
there say the junction is, rather than at the half-pixel chamfer each of them
arrives at; and when two arcs run *through* a node — the silhouette of a mark
carries on while a third region ends against it — they are fitted to one shared
tangent, so the curve does not hitch where the colour changes.

One shared curve is still not quite enough to close the seam, because a renderer
anti-aliases each shape separately: two shapes abutting on the very same line
each cover about half of the pixels along it, and a half over a half is three
quarters, not one. So every arc also gets a copy bled a pixel towards whichever
side paints *later*, and the earlier side uses that. The bleed only ever runs
inside the shape that will cover it, never across the shape's own silhouette, so
nothing visible moves — and underneath there is no seam left to show.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from studi0trace.engines.vexel.boundary import FillAt
from studi0trace.engines.vexel.regularity import regularize
from studi0trace.engines.vexel.symmetry import reflect, ring_symmetries
from studi0trace.engines.vexel.curves import (
    CORNER_REACH,
    MERGE_DEG,
    CircArc,
    Cubic,
    CurveParams,
    Line,
    Segment,
    corners_from_runs,
    fit_stretch,
    line_runs,
    merge_lines,
    _intersect,
    fit_contour_segments,
    fit_cubics,
    _line_through,
    _normalize,
    fit_open,
    reverse_segments,
)

# Lattice point (i, j) is the shared corner of padded pixels (i-1, j-1),
# (i-1, j), (i, j-1) and (i, j), and sits at SVG coordinate (j - 1, i - 1).
# The label map is padded by one with 0, standing for outside the canvas.

_RIGHT, _DOWN, _LEFT, _UP = 0, 1, 2, 3
_STEP = ((0, 1), (1, 0), (0, -1), (-1, 0))
# For a directed lattice edge leaving (i, j), the padded pixel on its left and
# on its right. The edge bounds a shape when the shape holds the left pixel and
# not the right one, so every ring below runs with the shape on its left.
_LEFT_PIXEL = ((-1, 0), (0, 0), (0, -1), (-1, -1))
_RIGHT_PIXEL = ((0, 0), (0, -1), (-1, -1), (-1, 0))

# How far a shape reaches under the shapes painted over it. One pixel covers an
# anti-aliased edge, and stays well inside any region wide enough not to have
# been recovered as a stroke instead.
BLEED = 1.0
# Arc length over which that reach eases off at a junction. Zero still pins the
# very last vertex to the node — which is all that is needed to keep the ring
# closed — and bleeds everything else fully; easing over a longer run measurably
# reopens the seam near junctions.
TAPER = 0.0
# The bled copy's fitting tolerance, as a fraction of the bleed. It has to stay
# below it: an error larger than the offset would let the copy wander back over
# the edge it exists to cover.
UNDER_TOL = 0.6
# Largest angle between a region's two arcs at a node that still counts as the
# region closing to a point rather than turning a corner.
WEDGE_ANGLE = 75.0
# A cut-off region is handed back pixels while its share of the mixture holds
# above this, allowing this many misses along the way, and only if the run it
# collects is at least this long — one stray pixel is noise, not a taper.
WEDGE_FLOOR = 0.35
WEDGE_PATIENCE = 2
WEDGE_RUN = 3
# How far past a right angle a turn has to go before it is read as the outline
# doubling back rather than turning a corner: cos of the turn, so 0.5 is 120°.
FOLD = 0.5
# Seen over this many pixels either side, a real corner still turns; a vertex
# that merely reached past its neighbour does not. Above this cosine the wider
# view is straight enough to call it a fold and not a corner.
WIDE = 3.0
CORNER_WIDE = 0.5
# How far past the two pixels either side of a label edge the half-coverage
# search may reach, in pixels.
REACH = 0.75
# An arc's approach to a node is read as a line over `reach` px at least, and
# grows in steps to APPROACH_MAX px while the run stays straight to
# APPROACH_RMS. A longer line has a steadier direction, and at a shallow
# junction the direction is what places the node.
APPROACH_MAX = 12.0
APPROACH_RMS = 0.08
FALLBACK_RMS = 0.5  # the residual a line fitted to whatever vertices were left is reported with
# An approach line starts this far from the node. Not 1.5: vertices placed at
# lattice midpoints sit at distances that are exact multiples of a half, and a
# threshold landing on one is decided by the last bit, differently in Python
# and Rust.
APPROACH_TRIM = 1.6
# Where the incident lines cross is trusted when the placement noise of a
# vertex, PLACEMENT_SIGMA px, projects to no more than NODE_UNCERTAINTY px
# along the weakest direction of the crossing. Two accurate lines meeting at
# 15 degrees pass; two at 5 degrees do not, and the node stays put.
PLACEMENT_SIGMA = 0.06
NODE_UNCERTAINTY = 0.6
# A wedge tip is placed from its two sides alone, and may move further than an
# ordinary node: the label map ends the wedge where the last whole pixel was.
TIP_LIMIT = 4.0
# Vertices this close to a node are not believed. A junction's pixels mix three
# fills, and the two-fill projection that places a vertex is biased there by
# whatever the third fill is doing; the fit then followed that bias faithfully,
# which is the bulge seen where an outline meets another shape. A wedge tip's
# sliver is worse still, and gets the longer trim.
# Thresholds on distances between placed vertices sit off multiples of a half:
# vertices placed at lattice midpoints are exact multiples apart, and a tie is
# decided by the last bit, differently in Python and Rust.
NODE_TRIM = 1.6
TIP_TRIM = 4.1
# ...but never more than this share of the arc's own length from either end.
TRIM_SHARE = 0.3
# An arc shorter than this has no direction worth reading: a corner pixel of a
# tilted square puts two nodes a pixel apart with a one-pixel arc between them,
# and that arc's "line" is the lattice edge it happens to sit on. The nodes are
# placed from the long arcs alone, and then coincide.
SHORT_ARC = 2.1
# Two arcs leaving a node are one smooth curve only if one line or one cubic
# fits SMOOTH_SPAN px of each, node in the middle, within a fraction of the
# tolerance. Pairs turning more than SMOOTH_MAX_TURN are not even tried.
SMOOTH_SPAN = 10.1
SMOOTH_TOL = 0.75
SMOOTH_MAX_TURN = 90.0


@dataclass
class Arc:
    """One run of boundary between two nodes, parting one pair of labels."""

    pair: tuple[int, int]
    pts: np.ndarray  # (N, 2) sub-pixel xy in SVG space
    n0: int | None  # flat lattice index of each end; None on a closed loop
    n1: int | None
    normal: np.ndarray | None = None  # unit vector per vertex, from pair[0] towards pair[1]
    segments: list[Segment] = field(default_factory=list)
    under: list[Segment] = field(default_factory=list)  # the same curve, bled under the later side
    t0: np.ndarray | None = None  # tangent pinned at each end, pointing into the arc
    t1: np.ndarray | None = None
    tip0: bool = False  # this end is the tip of a wedge closing to a point
    tip1: bool = False
    trim0: float = NODE_TRIM  # vertices within this of each end are not believed (widened by the node's move)
    trim1: float = NODE_TRIM
    sliver: np.ndarray | None = None  # per vertex: placed on a pixel handed back to a cut-off wedge (three-fill mixture)
    mirror: tuple[np.ndarray, np.ndarray] | None = None  # a closed arc's mirror axis (point, unit direction), when it has one

    @property
    def closed(self) -> bool:
        return self.n0 is None


@dataclass
class Boundary:
    arcs: list[Arc]
    padded: np.ndarray  # the label map with a one-pixel border of 0
    edge_arc: dict[int, tuple[int, int]]  # undirected edge key -> (arc, position)
    _later_is_b: list[bool] = field(default_factory=list)  # which side of each arc paints later

    def rings(self, labels: frozenset[int]) -> list[list[tuple[int, bool]]]:
        """Closed rings bounding the union of `labels`, as (arc index, reversed)."""
        inside = np.isin(self.padded, list(labels))
        lat_cols = self.padded.shape[1] + 1
        out: list[list[tuple[int, bool]]] = []
        for ring in _directed_rings(self.padded, inside):
            seq = []
            for i, j, d in ring:
                found = self.edge_arc.get(_undirected(i, j, d, lat_cols))
                if found is not None:
                    seq.append(found)
            runs = _runs(seq)
            if runs:
                out.append(runs)
        return out

    def polyline(self, ring: list[tuple[int, bool]]) -> np.ndarray:
        """The ring's sub-pixel polyline, for whole-shape primitive fitting."""
        parts = [self.arcs[i].pts[::-1] if rev else self.arcs[i].pts for i, rev in ring]
        return np.vstack(parts) if parts else np.zeros((0, 2))

    def segments(self, ring: list[tuple[int, bool]], member: frozenset[int] | None = None) -> list[Segment]:
        """The ring's fitted curve: each arc's one fit, reversed where walked backwards.

        Where the region on the other side of an arc is painted *later*, the bled
        copy is used instead. That copy runs a fraction of a pixel inside the
        neighbour, so the neighbour's anti-aliased edge lands on this shape's ink
        rather than on the backdrop. Two abutting shapes each anti-alias their own
        half of a shared edge, and a half over a half is three quarters, not one;
        the missing quarter is a hairline of whatever lies beneath. The bleed is
        hidden under the very shape that causes it, and never leaves the shared
        edge, so nothing visible moves.
        """
        out: list[Segment] = []
        for idx, reverse in ring:
            arc = self.arcs[idx]
            segs = arc.segments
            if member is not None and arc.under:
                a, b = arc.pair
                later = b if self._later_is_b[idx] else a
                if later not in member:
                    segs = arc.under
            out.extend(reverse_segments(segs) if reverse else list(segs))
        return out


def _runs(seq: list[tuple[int, int]]) -> list[tuple[int, bool]]:
    """Collapse a ring's per-edge (arc, position) list into whole-arc traversals."""
    if not seq:
        return []
    # A ring can start part-way along an arc; rotate so it starts at an arc change.
    start = 0
    for k in range(1, len(seq)):
        if seq[k][0] != seq[k - 1][0]:
            start = k
            break
    else:
        # One arc, walked from somewhere along it and wrapping onto its own
        # start, so the last position is no guide to the direction — 2, 3, 0, 1
        # runs forwards. The step to the second edge is.
        step = seq[1][1] - seq[0][1] if len(seq) > 1 else 1
        return [(seq[0][0], step == -1 or step > 1)]
    seq = seq[start:] + seq[:start]

    runs: list[tuple[int, bool]] = []
    k = 0
    while k < len(seq):
        arc = seq[k][0]
        j = k
        while j + 1 < len(seq) and seq[j + 1][0] == arc:
            j += 1
        runs.append((arc, seq[j][1] < seq[k][1]))
        k = j + 1
    return runs


def _edge_key(kind: int, i: int, j: int, lat_cols: int) -> int:
    """Stable key for an undirected lattice edge; kind 0 horizontal, 1 vertical."""
    return (i * lat_cols + j) * 2 + kind


def _undirected(i: int, j: int, d: int, lat_cols: int) -> int:
    if d == _RIGHT:
        return _edge_key(0, i, j, lat_cols)
    if d == _LEFT:
        return _edge_key(0, i, j - 1, lat_cols)
    if d == _DOWN:
        return _edge_key(1, i, j, lat_cols)
    return _edge_key(1, i - 1, j, lat_cols)


def _boundary_edges(padded: np.ndarray) -> tuple[dict[int, list[int]], dict[int, tuple], dict[int, tuple[int, int]]]:
    """Every lattice edge with different labels either side: incidence, pixels, endpoints."""
    lat_cols = padded.shape[1] + 1
    inc: dict[int, list[int]] = {}
    pixels: dict[int, tuple] = {}
    ends: dict[int, tuple[int, int]] = {}

    def add(key: int, a: int, b: int) -> None:
        inc.setdefault(a, []).append(key)
        inc.setdefault(b, []).append(key)
        ends[key] = (a, b)

    rows, cols = np.nonzero(padded[:-1, :] != padded[1:, :])
    for i, j in zip((rows + 1).tolist(), cols.tolist()):
        key = _edge_key(0, i, j, lat_cols)
        pixels[key] = ((i - 1, j), (i, j))
        add(key, i * lat_cols + j, i * lat_cols + j + 1)

    rows, cols = np.nonzero(padded[:, :-1] != padded[:, 1:])
    for i, j in zip(rows.tolist(), (cols + 1).tolist()):
        key = _edge_key(1, i, j, lat_cols)
        pixels[key] = ((i, j - 1), (i, j))
        add(key, i * lat_cols + j, (i + 1) * lat_cols + j)

    return inc, pixels, ends


def _chains(padded: np.ndarray) -> list[dict]:
    """Cut the boundary into arcs: maximal same-pair runs between nodes."""
    inc, pixels, ends = _boundary_edges(padded)

    def pair_of(key: int) -> tuple[int, int]:
        pa, pb = pixels[key]
        a, b = int(padded[pa]), int(padded[pb])
        return (a, b) if a < b else (b, a)

    pair = {key: pair_of(key) for key in pixels}
    # A vertex is a node unless it is a plain pass-through of a single pair.
    nodes = {v for v, keys in inc.items() if len(keys) != 2 or pair[keys[0]] != pair[keys[1]]}

    def step(key: int, v: int) -> int:
        a, b = ends[key]
        return b if a == v else a

    used: set[int] = set()
    chains: list[dict] = []

    # Both the node order and, within a node, the edge order are canonical, so
    # the Rust port cuts the boundary into exactly the same arcs. Where a chain
    # starts decides which way round its points run, and that reaches the fit.
    for v in sorted(nodes):
        for first in sorted(inc[v]):
            if first in used:
                continue
            chain = [first]
            used.add(first)
            cur = step(first, v)
            key = first
            while cur not in nodes:
                nxt = [k for k in inc[cur] if k != key and k not in used]
                if len(nxt) != 1:
                    break
                key = nxt[0]
                used.add(key)
                chain.append(key)
                cur = step(key, cur)
            chains.append({"edges": chain, "pair": pair[first], "n0": v, "n1": cur, "pixels": pixels})

    # What is left is a loop with no node on it: a region wholly inside one neighbour.
    for key in sorted(pixels):
        if key in used:
            continue
        chain = [key]
        used.add(key)
        cur = step(key, ends[key][0])
        cur_key = key
        while True:
            nxt = [k for k in inc[cur] if k != cur_key and k not in used]
            if len(nxt) != 1:
                break
            cur_key = nxt[0]
            used.add(cur_key)
            chain.append(cur_key)
            cur = step(cur_key, cur)
        chains.append({"edges": chain, "pair": pair[key], "n0": None, "n1": None, "pixels": pixels})

    return chains


def _coverage(pad_rgba: np.ndarray, pix: np.ndarray, lab: int, other: int, fill_at: FillAt) -> np.ndarray:
    """How much of each pixel is `lab` rather than `other`, read from its colour."""
    qx = pix[:, 1] - 0.5
    qy = pix[:, 0] - 0.5
    colour = pad_rgba[pix[:, 0], pix[:, 1]]
    f_other = fill_at(other, qx, qy)
    diff = fill_at(lab, qx, qy) - f_other
    denom = np.sum(diff * diff, axis=1)
    proj = np.sum((colour - f_other) * diff, axis=1)
    return np.where(denom > 1e-6, proj / np.maximum(denom, 1e-9), np.nan)


def _crossing(
    pad_rgba: np.ndarray,
    padded: np.ndarray,
    p_in: np.ndarray,
    p_out: np.ndarray,
    a: int,
    b: int,
    fill_at: FillAt,
) -> np.ndarray:
    """Where coverage passes a half along the line joining the two pixel centres,
    as a fraction of the step from the `a` pixel to the `b` pixel.

    Sampling only those two pixels would nail the outline to the label boundary,
    and the label boundary is not always right: the partition chamfers a hard
    corner, dropping the corner pixel into the neighbour even though its colour
    is plainly the shape's. The old per-region coverage field quietly repaired
    that, because its half-level could sit a pixel off the labels. So the search
    reaches one pixel further out on each side — never past a pixel belonging to
    a third region — and the outline goes where the colour says, not where the
    labels happen to break.
    """
    rows, cols = padded.shape
    step = p_out - p_in

    def sample(pix: np.ndarray, want: int, fallback: np.ndarray | None) -> np.ndarray:
        clipped = np.column_stack([np.clip(pix[:, 0], 0, rows - 1), np.clip(pix[:, 1], 0, cols - 1)])
        cov = _coverage(pad_rgba, clipped, a, b, fill_at)
        usable = (padded[clipped[:, 0], clipped[:, 1]] == want) & np.all(clipped == pix, axis=1) & np.isfinite(cov)
        return cov if fallback is None else np.where(usable, cov, fallback)

    here = _coverage(pad_rgba, p_in, a, b, fill_at)
    there = _coverage(pad_rgba, p_out, a, b, fill_at)
    here = np.where(np.isfinite(here), here, 1.0)
    there = np.where(np.isfinite(there), there, 0.0)
    before = sample(p_in - step, a, here)
    after = sample(p_out + step, b, there)

    level = np.stack([before, here, there, after], axis=1)
    at = np.array([-1.0, 0.0, 1.0, 2.0])
    # Of the crossings on offer, the one nearest the label edge wins. Coverage
    # read from colour is not guaranteed monotone across four samples, so more
    # than one interval can hold a crossing; picking by a fixed order would let
    # the answer jump a whole pixel as a distant sample drifted past a half, and
    # the fills these are computed from agree between the two implementations
    # only to about a colour level. The label boundary is the prior, so the
    # crossing closest to it is the one to believe.
    t = np.full(len(p_in), 0.5)
    best = np.full(len(p_in), np.inf)
    slope = np.zeros(len(p_in))
    for k in range(3):
        lo, hi = level[:, k], level[:, k + 1]
        crosses = (lo >= 0.5) & (hi < 0.5)
        if not crosses.any():
            continue
        here_t = at[k] + (lo - 0.5) / np.maximum(np.where(crosses, lo - hi, 1.0), 1e-9)
        nearer = crosses & (np.abs(here_t - 0.5) < best)
        best = np.where(nearer, np.abs(here_t - 0.5), best)
        slope = np.where(nearer, lo - hi, slope)
        t = np.where(nearer, here_t, t)

    # Stepping outside the two pixels either side of the label edge is a claim
    # that the labels are a pixel wrong, and only a steep ramp is entitled to
    # make it. Across a soft edge — a glow, a shallow gradient — coverage barely
    # moves from one pixel to the next, so where it happens to pass a half says
    # more about the last bits of the fitted fills than about the artwork. There
    # the labels are the better answer, and much the steadier one.
    trust = np.clip((slope - 0.15) / 0.35, 0.0, 1.0)
    over = np.clip(t, 0.0, 1.0)
    return np.clip(over + (t - over) * trust, -REACH, 1.0 + REACH)


def _place(
    chains: list[dict],
    padded: np.ndarray,
    rgb: np.ndarray,
    alpha: np.ndarray,
    fill_at: FillAt,
    handed_back: set[tuple[int, int]] | None = None,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Sub-pixel position for every lattice edge of every arc, the step across
    the edge, and which vertices sit on a pixel handed back to a wedge."""
    pad_rgba = np.concatenate(
        [np.pad(rgb, ((1, 1), (1, 1), (0, 0))), (np.pad(alpha, 1) * 255.0)[..., None]], axis=-1
    )
    out: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for ch in chains:
        a, b = ch["pair"]
        pixels = ch["pixels"]
        pa = np.array([pixels[k][0] for k in ch["edges"]])
        pb = np.array([pixels[k][1] for k in ch["edges"]])
        swap = padded[pa[:, 0], pa[:, 1]] != a
        p_in = np.where(swap[:, None], pb, pa)
        p_out = np.where(swap[:, None], pa, pb)
        c_in = np.column_stack([p_in[:, 1] - 0.5, p_in[:, 0] - 0.5])
        c_out = np.column_stack([p_out[:, 1] - 0.5, p_out[:, 0] - 0.5])

        t = np.full(len(ch["edges"]), 0.5)
        if a != 0 and b != 0:
            t = _crossing(pad_rgba, padded, p_in, p_out, a, b, fill_at)
        # `c_in` is always the centre of the pixel labelled `a` and `c_out` that of
        # the pixel labelled `b`, so this step is the direction from one side of
        # the arc to the other. It is one pixel long and axis aligned already.
        step = c_out - c_in
        crowded = np.zeros(len(ch["edges"]), dtype=bool)
        if handed_back:
            # Where a sliver was rebuilt the two boundaries either side of it
            # share a pixel, and reaching outside that pixel is a claim about a
            # neighbour that the other boundary has an equal claim on. Allowed,
            # the two reach past each other and the vertices come out of order
            # along the arc — which a fit reads as a curve that doubles back.
            crowded = np.array([tuple(q) in handed_back or tuple(r) in handed_back
                                for q, r in zip(p_in, p_out)], dtype=bool)
            t = np.where(crowded, np.clip(t, 0.0, 1.0), t)
        pts = _unfold(c_in + t[:, None] * step)
        if handed_back:
            pts = _settle(pts, crowded.tolist())
        out.append((pts, step, crowded))
    return out


def _unfold(pts: np.ndarray) -> np.ndarray:
    """Stop the placed outline doubling back on itself.

    A vertex sits where coverage passes a half along the segment joining two
    pixel centres, and that crossing may reach a little outside those two pixels
    — which is what lets the outline sit where a hard corner really is, rather
    than on the chamfer the labels give it. Where two boundaries run through the
    same pixel, though, both reach, and they can reach past each other:
    consecutive vertices come out in the wrong order along the arc, and the fit
    reads that as a curve that turns back and returns.

    A corner is sharp at every scale. A vertex that has reached past its
    neighbour is sharp only against them — look a few pixels either side and the
    outline is going straight on. So a reversal is only undone where the wider
    view says there is no corner here, which leaves the recovered corners alone.
    """
    n = len(pts)
    if n < 5:
        return pts
    ahead = pts[2:] - pts[1:-1]
    behind = pts[1:-1] - pts[:-2]
    scale = np.linalg.norm(ahead, axis=1) * np.linalg.norm(behind, axis=1)
    tight = np.divide(np.sum(ahead * behind, axis=1), np.maximum(scale, 1e-12))

    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    back = np.column_stack([np.interp(cum - WIDE, cum, pts[:, 0]), np.interp(cum - WIDE, cum, pts[:, 1])])
    fwd = np.column_stack([np.interp(cum + WIDE, cum, pts[:, 0]), np.interp(cum + WIDE, cum, pts[:, 1])])
    u = pts - back
    v = fwd - pts
    span = np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1)
    wide = np.divide(np.sum(u * v, axis=1), np.maximum(span, 1e-12))[1:-1]

    folds = np.nonzero((scale > 1e-12) & (tight < -FOLD) & (wide > CORNER_WIDE))[0] + 1
    if not len(folds):
        return pts
    out = pts.copy()
    for k in folds:
        out[k] = (out[k - 1] + out[k + 1]) / 2.0
    return out


def _settle(pts: np.ndarray, along_sliver: list[bool]) -> np.ndarray:
    """Average a vertex with its neighbours where the arc runs along a sliver
    that was handed back.

    Those vertices are the noisiest the stage produces. The pixel under them is a
    mixture of three fills, not two, so the projection that places them is biased
    by whatever the third one is doing, and the chain of whole pixels the sliver
    was rebuilt from is a staircase. One pass of averaging costs nothing where
    the placement was already smooth and takes the stair-step out of the rest.
    """
    mask = np.array(along_sliver, dtype=bool)
    if len(pts) < 3 or not mask.any():
        return pts
    inner = np.zeros_like(pts)
    inner[1:-1] = (pts[:-2] + 2.0 * pts[1:-1] + pts[2:]) / 4.0
    inner[0], inner[-1] = pts[0], pts[-1]
    smooth = mask.copy()
    smooth[0] = smooth[-1] = False
    return np.where(smooth[:, None], inner, pts)


def _arc_length(pts: np.ndarray) -> float:
    return float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1))) if len(pts) > 1 else 0.0


def _approach(pts: np.ndarray, from_start: bool, reach: float, trim: float,
              grow_to: float = APPROACH_MAX, exclude: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Total-least-squares line through an arc's run-up to one end, skipping the
    half-pixel marching-squares chamfer at the end itself.

    The window starts at `reach` px and grows in 2 px steps while the run stays
    straight to APPROACH_RMS: at a shallow junction the node is placed from the
    lines' directions, and a direction read over 4 px of vertices is a degree
    off, which puts the crossing half a pixel out. Returns (point, direction,
    rms), or None where there is nothing to fit.
    """
    q = pts if from_start else pts[::-1]
    d = np.linalg.norm(q - q[0], axis=1)
    # Vertices placed on a wedge's handed-back sliver sit on three-fill pixels
    # and are biased; a line through them leans by degrees, and at a shallow
    # junction degrees of direction are pixels of position.
    ok = np.ones(len(q), dtype=bool) if exclude is None else ~(exclude if from_start else exclude[::-1])
    best: tuple[np.ndarray, np.ndarray, float] | None = None
    far = reach
    while far <= grow_to + 1e-9:
        sel = (d >= trim) & (d <= far) & ok
        if int(sel.sum()) >= 2:
            centre, direction = _line_through(q[sel])
            off = (q[sel] - centre) @ np.array([-direction[1], direction[0]])
            rms = float(np.sqrt(float(np.sum(off * off)) / len(off)))
            if best is not None and rms > APPROACH_RMS:
                break
            # What the node solve is told is not the residual alone: a line
            # through two vertices a pixel apart has no residual and no
            # authority. Its direction is off by about sigma*sqrt(12/n)/span,
            # and at `reach` from its centre that is a position error the
            # solve must weigh. One misplaced vertex on a short arc otherwise
            # swings the node by pixels.
            n_used = float(int(sel.sum()))
            span = max(float(np.max(d[sel]) - np.min(d[sel])), 0.5)
            lean = PLACEMENT_SIGMA * math.sqrt(12.0 / n_used) * (reach / span)
            best = (centre, direction, math.sqrt(rms * rms + lean * lean))
        far += 2.0
    if best is None:
        sel = (d > 0) & (d <= 2.0 * reach) & ok
        if int(sel.sum()) < 2:
            sel = (d > 0) & (d <= 2.0 * reach)
        if int(sel.sum()) < 2:
            return None
        centre, direction = _line_through(q[sel])
        # the wide fallback is the least reliable line there is, and says so
        best = (centre, direction, FALLBACK_RMS)
    return best


def _smooth_through(pa: np.ndarray, pb: np.ndarray, node: np.ndarray, tol: float,
                    exclude_a: np.ndarray | None = None, exclude_b: np.ndarray | None = None) -> bool:
    """Are two arcs leaving one node a single smooth curve?

    Fit one primitive through SMOOTH_SPAN px of each, the node in the middle;
    smooth if one line or one cubic does it within SMOOTH_TOL of the tolerance.
    A 59 degree kink cannot be one cubic over twenty pixels; a 15 px circle can.
    The angle threshold this replaces — the corner threshold, 60 degrees — was
    pinning a 59 degree meeting to one tangent, and the arc then swung 59
    degrees inside its first few pixels: the hook seen where a stem met an edge.
    Both runs are read from the node outward; vertices inside the node's trim
    and on a handed-back sliver are left out, as the fit itself leaves them out.
    """
    def head(p: np.ndarray, exclude: np.ndarray | None) -> np.ndarray:
        cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))])
        keep = (cum >= APPROACH_TRIM) & (cum <= SMOOTH_SPAN)
        if exclude is not None:
            keep &= ~exclude
        return p[keep]

    a, b = head(pa, exclude_a), head(pb, exclude_b)
    if len(a) < 3 or len(b) < 3:
        return False
    joined = np.vstack([a[::-1], node[None, :], b])
    return len(fit_open(joined, SMOOTH_TOL * tol)) == 1


def _on_border(target: np.ndarray, incident: list[tuple[Arc, int]], reach: float = 6.1) -> np.ndarray:
    """A node on the canvas edge stays on it. An arc that parts a region from the
    outside runs along the border at a constant x or y; a node it meets is on
    that line, whatever the interior arcs' lines say. Off it by a third of a
    pixel, every shape at the edge stopped short and left a hairline of backdrop
    along the frame. Read from the vertices within `reach` of the node, leaving
    out the end vertex itself, which is the chamfer the node replaces, and may
    sit on the other border at a canvas corner."""
    out = target.copy()
    for arc, k in incident:
        if 0 not in arc.pair or len(arc.pts) < 3:
            continue
        q = arc.pts[1:] if k == 0 else arc.pts[:-1]
        near = q[np.linalg.norm(q - target, axis=1) <= reach]
        if len(near) < 2:
            continue
        for axis in (0, 1):
            col = near[:, axis]
            if float(np.max(col) - np.min(col)) < 1e-9:
                out[axis] = float(col[0])
    return out


def _node_estimate(lines: list[tuple[np.ndarray, np.ndarray, float] | None], mean: np.ndarray, limit: float) -> np.ndarray:
    """Where the incident approach lines cross, when they pin it down.

    `acc` is a sum of projectors onto the lines' unit normals, so its smaller
    eigenvalue says how well the lines fix a point: about one when they cross
    squarely, `1 - cos(angle)` for two lines, towards zero as they turn
    parallel. The crossing is used when a vertex's placement noise projects to
    no more than NODE_UNCERTAINTY along that weakest direction; otherwise the
    mean of the arcs' own ends stands. The move is clamped to `limit`.
    """
    usable = [v for v in lines if v is not None]
    if len(usable) < 2:
        return mean
    acc = np.zeros((2, 2))
    rhs = np.zeros(2)
    weighted = np.zeros((2, 2))
    wrhs = np.zeros(2)
    for point, direction, rms in usable:
        normal = np.eye(2) - np.outer(direction, direction)
        acc += normal
        rhs += normal @ point
        # A line read off a straight run pins the node hard; one read off a bend
        # (a curved arc's approach, a sliver) says little about where the node
        # is across it. Weighted by the inverse residual variance, a node where
        # a curve meets a long straight edge lands on the edge.
        w = 1.0 / (rms * rms + PLACEMENT_SIGMA * PLACEMENT_SIGMA)
        weighted += w * normal
        wrhs += w * (normal @ point)
    half = (acc[0, 0] + acc[1, 1]) / 2.0
    spread = np.hypot((acc[0, 0] - acc[1, 1]) / 2.0, acc[0, 1])
    lam_min = half - spread
    if lam_min <= 1e-9:
        return mean
    if PLACEMENT_SIGMA / math.sqrt(lam_min) > NODE_UNCERTAINTY:
        return mean
    if abs(float(np.linalg.det(weighted))) <= 1e-300:
        return mean
    guess = np.linalg.solve(weighted, wrhs)
    move = guess - mean
    away = float(np.linalg.norm(move))
    if away > limit:
        move = move * (limit / away)
    return mean + move


def _wedge(padded: np.ndarray, target: np.ndarray, pairs: list[tuple[int, int]], away: list[np.ndarray],
           limit: float = WEDGE_ANGLE) -> tuple[int, int] | None:
    """The region that ends here, and the arc that carries on past it.

    A region closes to a point when its two arcs leave the node at an acute
    angle *and* the ground between them is its own — a big region with a sharp
    corner has the same angle but the acute sector belongs to its neighbour, and
    reading the label a couple of pixels along the bisector is what tells them
    apart. Returns (region, index of the third arc), or None.
    """
    best: tuple[float, int, list[int]] | None = None
    for lab in sorted({x for pair in pairs for x in pair if x != 0}):
        sides = [k for k, pair in enumerate(pairs) if lab in pair]
        if len(sides) != 2:
            continue
        turn = np.degrees(np.arccos(np.clip(float(np.dot(away[sides[0]], away[sides[1]])), -1.0, 1.0)))
        if best is None or turn < best[0]:
            best = (turn, lab, sides)
    if best is None or best[0] > limit:
        return None
    _turn, lab, sides = best
    bisector = away[sides[0]] + away[sides[1]]
    length = float(np.hypot(*bisector))
    if length < 1e-6:
        return None
    bisector = bisector / length
    inside = False
    for step in (1.5, 2.5, 3.5):
        r = int(np.floor(target[1] + bisector[1] * step)) + 1
        c = int(np.floor(target[0] + bisector[0] * step)) + 1
        if 0 <= r < padded.shape[0] and 0 <= c < padded.shape[1] and padded[r, c] == lab:
            inside = True
            break
    if not inside:
        return None
    through = [k for k in range(len(pairs)) if k not in sides]
    if len(through) != 1:
        return None
    # A shape cut off by the canvas edge is not closing to a point: the arc that
    # would carry on is the canvas border, and pinning the shape's side to it
    # bends the side a pixel where the artwork simply stops.
    if 0 in pairs[through[0]]:
        return None
    # the boundary has to carry on the other way, or this is a corner, not a tip
    if float(np.dot(away[through[0]], -bisector)) < 0.5:
        return None
    return lab, through[0]

def _mix_share(colour: np.ndarray, f_c: np.ndarray, f_a: np.ndarray, f_b: np.ndarray) -> np.ndarray:
    """How much of each colour is the third fill, in a mixture of all three.

    A pixel on a boundary is a mixture of what meets there. Where a region has
    been cut off, the pixels beyond it are a mixture of *three* fills, not two,
    and this is that third share: the part of the pixel the cut-off region still
    owns. Solved as least squares over the two free weights, then folded back
    onto the simplex so no share is negative and the three sum to one.
    """
    u = f_c - f_b
    v = f_a - f_b
    t = colour - f_b
    uu = np.sum(u * u, axis=1)
    vv = np.sum(v * v, axis=1)
    uv = np.sum(u * v, axis=1)
    det = uu * vv - uv * uv
    ok = np.abs(det) > 1e-9
    safe = np.where(ok, det, 1.0)
    sc = np.where(ok, (np.sum(t * u, axis=1) * vv - np.sum(t * v, axis=1) * uv) / safe, 0.0)
    sa = np.where(ok, (np.sum(t * v, axis=1) * uu - np.sum(t * u, axis=1) * uv) / safe, 0.0)
    sc = np.clip(sc, 0.0, 1.0)
    sa = np.clip(sa, 0.0, 1.0)
    total = sc + sa
    share = np.where(total > 1.0, sc / np.maximum(total, 1e-9), sc)
    # Rounded before anyone compares it to a threshold. The share is computed
    # from fitted fills, and the two implementations of those agree only to
    # about a colour level; leaving the last bits in would let a pixel be handed
    # back in one and not the other, and a pixel is a whole arc's worth of
    # difference in the graph that follows.
    return np.round(share, 2)


def _extend_wedges(
    padded: np.ndarray,
    rgb: np.ndarray,
    alpha: np.ndarray,
    fill_at: FillAt,
    params: CurveParams,
) -> np.ndarray:
    """Give a region cut off at a point the pixels its ink still runs through.

    A region that tapers to an acute point cannot be carried all the way by a
    label map: below a pixel wide there is no pixel to give it, so the watershed
    hands those to whichever neighbour is winning and the region stops dead. The
    trace then shows a blunt cut where the artwork has a long fine taper.

    The ink is still there. Along the stretch where the two neighbours now meet
    directly, the pixels are a mixture of three fills, and the third share says
    how much of each is still the region that was cut off. This walks that
    stretch and hands back a chain of pixels while that share holds up, which is
    enough for the rest of the stage to place a real taper: the two sides are
    then ordinary arcs, put where the coverage says, closing on the tip.

    The chain must be four-connected. `_directed_rings` breaks a diagonal touch
    the four-connected way, so a chain that only meets at the corners comes back
    as a swarm of one-pixel islands, each its own ring on the region's path.
    """
    chains = _chains(padded)
    if not chains:
        return padded, set()
    # A provisional graph, placed at the lattice edges' midpoints: enough to say
    # which region closes to a point where, and which stretch carries on.
    arcs = [
        Arc(pair=ch["pair"], pts=_midpoints(ch), normal=None, n0=ch["n0"], n1=ch["n1"])
        for ch in chains
    ]
    ends: dict[int, list[tuple[int, int]]] = {}
    for idx, arc in enumerate(arcs):
        if arc.closed or len(arc.pts) < 2:
            continue
        ends.setdefault(arc.n0, []).append((idx, 0))
        ends.setdefault(arc.n1, []).append((idx, -1))

    rgba255 = np.concatenate(
        [np.pad(rgb, ((1, 1), (1, 1), (0, 0))), (np.pad(alpha, 1) * 255.0)[..., None]], axis=-1
    )
    out = padded.copy()
    taken: set[tuple[int, int]] = set()

    for node in sorted(ends):
        incident = ends[node]
        if len(incident) != 3:
            continue
        away = []
        for i, k in incident:
            pts = arcs[i].pts
            far = pts[min(3, len(pts) - 1)] if k == 0 else pts[max(-4, -len(pts))]
            step = far - (pts[0] if k == 0 else pts[-1])
            length = float(np.hypot(*step))
            away.append(step / length if length > 1e-9 else np.zeros(2))
        here = arcs[incident[0][0]].pts[0 if incident[0][1] == 0 else -1]
        tip = _wedge(padded, here, [arcs[i].pair for i, _ in incident], away)
        if tip is None:
            continue
        lab, through = tip
        idx, at_start = incident[through]
        a, b = arcs[idx].pair
        if a == 0 or b == 0:
            continue
        keys = chains[idx]["edges"]
        pixels = chains[idx]["pixels"]
        order = keys if at_start == 0 else keys[::-1]

        run: list[tuple[int, int]] = []
        misses = 0
        side: int | None = None
        for key in order:
            pa, pb = pixels[key]
            options = []
            for q in (pa, pb):
                if out[q] not in (a, b) or q in taken:
                    continue
                # Steps may go diagonally — at a tip the region's last pixel is
                # usually corner-on to the boundary it ran into — but a diagonal
                # is bridged below, because a chain that only touches at the
                # corners comes back from the ring walk as one-pixel islands.
                if run:
                    if max(abs(q[0] - run[-1][0]), abs(q[1] - run[-1][1])) != 1:
                        continue
                elif not any(
                    0 <= q[0] + dr < out.shape[0] and 0 <= q[1] + dc < out.shape[1]
                    and out[q[0] + dr, q[1] + dc] == lab
                    for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                ):
                    continue
                options.append(q)
            if side is not None:
                on_side = [q for q in options if out[q] == side]
                if on_side:
                    options = on_side
            if not options:
                misses += 1
                if misses > WEDGE_PATIENCE:
                    break
                continue
            rows = np.array([q[0] for q in options])
            cols = np.array([q[1] for q in options])
            qx = cols - 0.5
            qy = rows - 0.5
            share = _mix_share(
                rgba255[rows, cols], fill_at(lab, qx, qy), fill_at(a, qx, qy), fill_at(b, qx, qy)
            )
            pick = int(np.argmax(share))
            if float(share[pick]) < WEDGE_FLOOR:
                misses += 1
                if misses > WEDGE_PATIENCE:
                    break
                continue
            misses = 0
            chosen = options[pick]
            if side is None:
                side = int(out[chosen])
            run.append(chosen)
            taken.add(chosen)
        # One stray pixel is noise; a region that really was cut off leaves a run.
        if len(run) >= WEDGE_RUN:
            for q in _bridged(run, out, lab, a, b, rgba255, fill_at):
                out[q] = lab
        else:
            for q in run:
                taken.discard(q)
    return out, {q for q in taken}


def _bridged(run: list[tuple[int, int]], out: np.ndarray, lab: int, a: int, b: int,
             rgba255: np.ndarray, fill_at: FillAt) -> list[tuple[int, int]]:
    """The run, with a pixel put in wherever it steps diagonally.

    Four-connectivity is not a nicety here: `_directed_rings` breaks a diagonal
    touch the four-connected way, so a chain that only meets at the corners is
    read back as a string of one-pixel islands, each emitted as its own closed
    ring on the region's path. The corner is filled with whichever of the two
    pixels beside it the region's own ink better explains.
    """
    attach = [q for q in _neighbourhood(run[0], out.shape) if out[q] == lab]
    chain = ([attach[0]] if attach else []) + list(run)
    out_run: list[tuple[int, int]] = []
    for k, q in enumerate(chain):
        if k and max(abs(q[0] - chain[k - 1][0]), abs(q[1] - chain[k - 1][1])) == 1 \
                and abs(q[0] - chain[k - 1][0]) + abs(q[1] - chain[k - 1][1]) == 2:
            p = chain[k - 1]
            options = [o for o in ((p[0], q[1]), (q[0], p[1])) if out[o] in (a, b)]
            if options:
                rows = np.array([o[0] for o in options]); cols = np.array([o[1] for o in options])
                qx = cols - 0.5; qy = rows - 0.5
                share = _mix_share(rgba255[rows, cols], fill_at(lab, qx, qy),
                                   fill_at(a, qx, qy), fill_at(b, qx, qy))
                out_run.append(options[int(np.argmax(share))])
        if out[q] != lab:
            out_run.append(q)
    return out_run


def _neighbourhood(q: tuple[int, int], shape: tuple[int, int]) -> list[tuple[int, int]]:
    return [
        (q[0] + dr, q[1] + dc)
        for dr in (-1, 0, 1)
        for dc in (-1, 0, 1)
        if (dr or dc) and 0 <= q[0] + dr < shape[0] and 0 <= q[1] + dc < shape[1]
    ]


def _midpoints(chain: dict) -> np.ndarray:
    pixels = chain["pixels"]
    pa = np.array([pixels[k][0] for k in chain["edges"]], dtype=float)
    pb = np.array([pixels[k][1] for k in chain["edges"]], dtype=float)
    mid = (pa + pb) / 2.0
    return np.column_stack([mid[:, 1] - 0.5, mid[:, 0] - 0.5])


def _junctions(
    arcs: list[Arc],
    padded: np.ndarray,
    corner_threshold: float,
    tol: float = 0.4,
    reach: float = 4.1,
    trim: float = APPROACH_TRIM,
    limit: float = 2.0,
) -> None:
    """Place each node, and give arcs that run through it a shared tangent.

    Marching squares chamfers a junction the way it chamfers a corner, and each
    arc arrives at its own chamfered end. Fitting a line to each arc's approach
    and taking the point closest to all of them recovers the junction and hands
    every arc the same one.

    Then: where a third region merely ends against a boundary that carries on —
    the silhouette of a mark, with the colour changing along it — two of the arcs
    are one smooth curve. Pinning both to a single tangent keeps it smooth, so
    the outline does not hitch where the fill changes.

    Every node is worked out from the arcs as they were placed, and only then are
    any of them moved. An arc two vertices long is most of its own approach line,
    so moving one of its ends would change what the node at the other end is
    told — and which node went first is not something either implementation
    should be deciding.
    """
    ends: dict[int, list[tuple[int, int]]] = {}
    short: set[int] = set()
    for idx, arc in enumerate(arcs):
        if arc.closed or len(arc.pts) < 2:
            continue
        ends.setdefault(arc.n0, []).append((idx, 0))
        ends.setdefault(arc.n1, []).append((idx, -1))
        if _arc_length(arc.pts) < SHORT_ARC:
            short.add(idx)

    # Nodes joined by a short arc are one junction. A corner pixel of a tilted
    # square puts two lattice nodes a pixel apart with a one-pixel arc between
    # them; each on its own has a single long arc and cannot be placed, together
    # they have the two sides of the corner. The short arc then collapses onto
    # the shared node and `_fit_arc` drops it.
    parent: dict[int, int] = {node: node for node in ends}

    def root(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for idx in sorted(short):
        a, b = root(arcs[idx].n0), root(arcs[idx].n1)
        if a != b:
            parent[max(a, b)] = min(a, b)
    groups: dict[int, list[tuple[int, int]]] = {}
    for node in sorted(ends):
        groups.setdefault(root(node), []).extend(ends[node])

    # A group stands only if one point serves every node in it: the long arcs'
    # lines must cross within SHORT_ARC of each of the original ends. Where
    # they do not — the two sides of a stroke narrower than two pixels, joined
    # by its end cap — the nodes are real and are placed one by one.
    resolved: list[list[tuple[int, int]]] = []
    for incident in groups.values():
        members = sorted({arcs[i].n0 if k == 0 else arcs[i].n1 for i, k in incident})
        if len(members) > 1:
            lines = [None if i in short else _approach(arcs[i].pts, k == 0, reach, trim, exclude=arcs[i].sliver) for i, k in incident]
            mean = np.mean([arcs[i].pts[k] for i, k in incident], axis=0)
            target = _node_estimate(lines, mean, limit)
            # The two sides of a stroke a pixel or two wide are joined by a
            # short arc as well, and their lines run parallel: the solve
            # declines to cross them and hands back the mean, which sits within
            # reach of both ends. Merging those would take the stroke's width
            # away at every junction. A corner's nodes are one point only where
            # the long arcs actually cross.
            crossed = bool(np.any(target != mean))
            if not crossed or max(float(np.linalg.norm(arcs[i].pts[k] - target)) for i, k in incident) > SHORT_ARC:
                resolved.extend(ends[node] for node in members)
                continue
        resolved.append(incident)

    moves: list[tuple[list[tuple[int, int]], np.ndarray, dict, set]] = []
    for incident in resolved:
        lines = {(i, k): (None if i in short else _approach(arcs[i].pts, k == 0, reach, trim, exclude=arcs[i].sliver))
                 for i, k in incident}
        mean = np.mean([arcs[i].pts[k] for i, k in incident], axis=0)

        target = _node_estimate(list(lines.values()), mean, limit)
        target = _on_border(target, [(arcs[i], k) for i, k in incident])
        # Which two arcs, if any, are one curve passing through? Only the long
        # arcs have a direction; a short arc's ends just take the node.
        away: dict[tuple[int, int], np.ndarray] = {}
        for i, k in incident:
            if i in short:
                continue
            pts = arcs[i].pts
            far = pts[min(3, len(pts) - 1)] if k == 0 else pts[max(-4, -len(pts))]
            outward = far - target
            line = lines[(i, k)]
            if line is not None and float(np.dot(line[1], outward)) < 0.0:
                away[(i, k)] = _normalize(-line[1])
            elif line is not None and float(np.dot(line[1], outward)) > 0.0:
                away[(i, k)] = _normalize(line[1])
            else:
                away[(i, k)] = _normalize(outward)

        tangents: dict[tuple[int, int], np.ndarray] = {}
        keys = list(away)
        tip = None
        if len(keys) == 3:
            tip = _wedge(padded, target, [arcs[i].pair for i, _ in keys], [away[k] for k in keys])
        tips: set[tuple[int, int]] = set()
        if tip is not None:
            # A region closing to a point does not put a corner in anything. Its
            # two sides run into the tip along the line its neighbours' boundary
            # leaves on, so all three are one tangent: the wedge ends in a cusp
            # and the boundary that carries on stays a single sweeping curve.
            # Without this the neighbour's own outline gets a visible kink at the
            # tip — the shape it paints has a corner the artwork does not.
            _lab, through = tip
            axis = away[keys[through]]
            for k, key in enumerate(keys):
                # The boundary that carries on leaves along its own line. Each
                # side of the wedge leaves along *its* own line: the sides meet
                # at the opening angle, and pinning them to the through axis
                # made each swing that angle in its last few pixels — the
                # flare. A side that really is tangent to the through boundary
                # has an approach direction equal to it, and loses nothing.
                tangents[key] = axis if k == through else away[key]
            # The tip itself is where the wedge's two sides cross. The label map
            # ended the wedge at its last whole pixel, which is short of that by
            # a pixel or more, and the boundary carrying through says nothing
            # about how far along itself the tip sits.
            tips = {key for k, key in enumerate(keys) if k != through}
            # The sides are read again from beyond the tip's trim: the first
            # pixels of a taper are three-fill mixtures whether or not they were
            # handed back, and a degree of lean there is a pixel of tip.
            for key in tips:
                i, k = key
                again = _approach(arcs[i].pts, k == 0, TIP_TRIM + reach, TIP_TRIM, grow_to=2.0 * APPROACH_MAX, exclude=arcs[i].sliver)
                if again is not None:
                    lines[key] = again
                    flip = float(np.dot(again[1], away[key])) < 0.0
                    away[key] = _normalize(-again[1] if flip else again[1])
                    tangents[key] = away[key]
            target = _on_border(_node_estimate([lines[key] for key in tips], mean, TIP_LIMIT), [(arcs[i], k) for i, k in incident])
        best: tuple[float, tuple, tuple] | None = None
        for x in range(len(keys)):
            for y in range(x + 1, len(keys)):
                turn = np.degrees(np.arccos(np.clip(-float(np.dot(away[keys[x]], away[keys[y]])), -1.0, 1.0)))
                if best is None or turn < best[0]:
                    best = (turn, keys[x], keys[y])
        if not tangents and best is not None and best[0] <= SMOOTH_MAX_TURN:
            _turn, ka, kb = best

            def from_node(key: tuple[int, int]) -> tuple[np.ndarray, np.ndarray | None]:
                i, k = key
                pts, sliver = arcs[i].pts, arcs[i].sliver
                if k == 0:
                    return pts, sliver
                return pts[::-1], (None if sliver is None else sliver[::-1])

            (pa, sa), (pb, sb) = from_node(ka), from_node(kb)
            if _smooth_through(pa, pb, target, tol, sa, sb):
                shared = _normalize(away[ka] - away[kb])
                if np.any(shared):
                    tangents[ka] = shared
                    tangents[kb] = -shared
        moves.append((incident, target, tangents, tips))

    for incident, target, tangents, tips in moves:
        for i, k in incident:
            # Vertices between the arc's old end and the node it was given
            # would double back on the curve, so the trim reaches past the move.
            moved = float(np.linalg.norm(target - arcs[i].pts[k]))
            radius = max(TIP_TRIM if (i, k) in tips else NODE_TRIM, moved + 1.0)
            # A short arc keeps its vertices: trimming a third of a pixel off
            # each end of a two-pixel arc leaves a chord, and a thin stroke
            # or a small disc made of such arcs came out as a polygon.
            radius = min(radius, TRIM_SHARE * _arc_length(arcs[i].pts))
            arcs[i].pts[k] = target.copy()
            if k == 0:
                arcs[i].trim0 = radius
            else:
                arcs[i].trim1 = radius
            if (i, k) in tips:
                if k == 0:
                    arcs[i].tip0 = True
                else:
                    arcs[i].tip1 = True
            pinned = tangents.get((i, k))
            if pinned is None:
                continue
            if k == 0:
                arcs[i].t0 = pinned
            else:
                arcs[i].t1 = pinned


def build(
    labels: np.ndarray,
    rgb: np.ndarray,
    alpha: np.ndarray,
    fill_at: FillAt,
    params: CurveParams,
    rank: dict[int, int] | None = None,
    bleed: float | None = None,
    taper: float | None = None,
    extend: bool = True,
) -> Boundary:
    """The whole boundary of the label map, placed sub-pixel and fitted once.

    `rank` is the paint order by label. Given it, each arc also gets a copy bled
    towards whichever side paints later, for the earlier side to use.
    """
    bleed = BLEED if bleed is None else bleed
    taper = TAPER if taper is None else taper
    padded = np.pad(labels.astype(np.int64), 1, constant_values=0)
    handed_back: set[tuple[int, int]] = set()
    if extend:
        padded, handed_back = _extend_wedges(padded, rgb, alpha, fill_at, params)
    chains = _chains(padded)
    placed = _place(chains, padded, rgb, alpha, fill_at, handed_back)

    arcs = [
        Arc(pair=ch["pair"], pts=pts, normal=normal, n0=ch["n0"], n1=ch["n1"], sliver=(sliver if sliver.any() else None))
        for ch, (pts, normal, sliver) in zip(chains, placed)
    ]
    edge_arc: dict[int, tuple[int, int]] = {}
    for idx, ch in enumerate(chains):
        for pos, key in enumerate(ch["edges"]):
            edge_arc[key] = (idx, pos)
    # A region that is mirror- or rotationally symmetric is made exactly so
    # before its nodes are placed and its curves fitted; the vertices live in
    # the shared arcs, so the neighbour across each edge moves with it.
    _symmetrize(Boundary(arcs=arcs, padded=padded, edge_arc=edge_arc, _later_is_b=[]))
    _junctions(arcs, padded, params.corner_threshold, params.tol)
    for arc in arcs:
        arc.segments = _fit_arc(arc, params)
    # Across the graph: lines meant to be parallel, perpendicular or on an axis
    # are made exactly so. Nodes never move, so the ring still closes.
    regularize([(arc.segments, arc.closed) for arc in arcs], params.snap_axis_deg)

    later_is_b: list[bool] = []
    for arc in arcs:
        a, b = arc.pair
        b_later = rank is not None and rank.get(b, -1) > rank.get(a, -1)
        later_is_b.append(b_later)
        if rank is not None and bleed > 0.0 and a != 0 and b != 0:
            # The bled copy is never seen — the shape that causes it covers it —
            # so it is fitted loosely. Holding it to the visible tolerance would
            # spend nodes describing a curve nobody looks at.
            # Fitted no looser than the bleed can absorb: an error larger than
            # the offset would let the copy wander back across the very edge it
            # is there to cover.
            loose = replace(params, tol=min(2.0 * params.tol, UNDER_TOL * bleed))
            arc.under = _fit_under(_bled(arc, bleed if b_later else -bleed, taper), loose)

    return Boundary(arcs=arcs, padded=padded, edge_arc=edge_arc, _later_is_b=later_is_b)


def _symmetrize(bnd: Boundary) -> int:
    """Symmetrise every single-ring region's placed vertices in place; the
    number of regions changed. See `symmetry.symmetrize_ring`."""
    changed = 0
    for lab in np.unique(bnd.padded):
        if lab == 0:
            continue
        rings = bnd.rings(frozenset([int(lab)]))
        if len(rings) != 1:
            continue
        ring = rings[0]
        poly = bnd.polyline(ring)
        # a ring on the canvas frame stays put: its frame vertices are exact
        # and averaging them with a partner would pull them off the edge
        h, w = bnd.padded.shape[0] - 2, bnd.padded.shape[1] - 2
        if (poly[:, 0] <= 0.0).any() or (poly[:, 1] <= 0.0).any() or (poly[:, 0] >= w).any() or (poly[:, 1] >= h).any():
            continue
        sym, axes = ring_symmetries(poly)
        if sym is None:
            continue
        pos = 0
        for idx, rev in ring:
            arc = bnd.arcs[idx]
            k = len(arc.pts)
            piece = sym[pos:pos + k]
            arc.pts = piece[::-1].copy() if rev else piece.copy()
            pos += k
        if len(ring) == 1 and bnd.arcs[ring[0][0]].closed and axes:
            # one closed arc with a mirror axis is fitted on one half and reflected
            bnd.arcs[ring[0][0]].mirror = axes[0]
        changed += 1
    return changed


def _fit_under(moved: Arc, params: CurveParams) -> list[Segment]:
    """Fit a bled copy: its interior as an ordinary arc, joined to the shared
    nodes by two explicit one-pixel jogs.

    The copy's end vertices stay on the nodes so the ring closes, and every
    other vertex sits a pixel inside the later shape; a fit over the whole thing
    sees a run a pixel off its own chord and answers with cubics. The jogs are
    hidden under the shape that causes the bleed, as the copy itself is.
    """
    pts = moved.pts
    if moved.closed or len(pts) < 4:
        return _fit_arc(moved, params)
    inner = replace(
        moved, pts=pts[1:-1], t0=None, t1=None, tip0=False, tip1=False, trim0=0.0, trim1=0.0,
        sliver=None if moved.sliver is None else moved.sliver[1:-1],
        normal=None if moved.normal is None else moved.normal[1:-1],
    )
    return [Line(pts[0].copy(), pts[1].copy()), *_fit_arc(inner, params), Line(pts[-2].copy(), pts[-1].copy())]


def _bled(arc: Arc, amount: float, taper: float = TAPER) -> Arc:
    """The arc pushed `amount` towards one side, pinned back to its own ends.

    The ends are nodes that the other arcs meeting there have been fitted to, so
    the bleed has to reach zero at them or the ring tears open. It reaches zero
    at the end vertex itself, and over `taper` pixels before it.
    """
    pts = arc.pts
    if arc.normal is None or len(pts) < 3:
        return arc
    # The per-vertex step between pixel centres is axis aligned, so it zigzags
    # along a diagonal run and offsetting by it would fold the curve into a
    # staircase. Take the normal from the curve's own tangent instead, and only
    # borrow the step's sign to point it at the right side.
    ahead = np.roll(pts, -1, axis=0) if arc.closed else np.vstack([pts[1:], pts[-1:]])
    behind = np.roll(pts, 1, axis=0) if arc.closed else np.vstack([pts[:1], pts[:-1]])
    tangent = ahead - behind
    length = np.linalg.norm(tangent, axis=1, keepdims=True)
    tangent = np.divide(tangent, np.maximum(length, 1e-9))
    normal = np.column_stack([-tangent[:, 1], tangent[:, 0]])
    flip = np.sign(np.sum(normal * arc.normal, axis=1))
    flip[flip == 0.0] = 1.0
    normal *= flip[:, None]

    scale = np.full(len(pts), 1.0)
    if not arc.closed:
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        edge = np.minimum(cum, cum[-1] - cum)
        scale = np.clip(edge / max(taper, 1e-6), 0.0, 1.0)
    moved = pts + (amount * scale)[:, None] * normal
    return Arc(pair=arc.pair, pts=moved, normal=arc.normal, n0=arc.n0, n1=arc.n1, t0=arc.t0, t1=arc.t1,
               tip0=arc.tip0, tip1=arc.tip1, trim0=arc.trim0, trim1=arc.trim1, sliver=arc.sliver)


def _directed_rings(padded: np.ndarray, inside: np.ndarray) -> list[list[tuple[int, int, int]]]:
    """Closed rings of directed lattice edges with `inside` always on the left.

    Crack following: at each vertex the walk prefers to turn left, then to go
    straight, then to turn right, which resolves a diagonal touch the same way
    four-connected labelling does and leaves every ring closed.
    """
    rows, cols = padded.shape

    def held(i: int, j: int) -> bool:
        return 0 <= i < rows and 0 <= j < cols and bool(inside[i, j])

    def valid(i: int, j: int, d: int) -> bool:
        li, lj = _LEFT_PIXEL[d]
        ri, rj = _RIGHT_PIXEL[d]
        return held(i + li, j + lj) and not held(i + ri, j + rj)

    pending: set[tuple[int, int, int]] = set()
    order: list[tuple[int, int, int]] = []
    ys, xs = np.nonzero(inside)
    for i, j in zip(ys.tolist(), xs.tolist()):
        for d, (vi, vj) in zip((_RIGHT, _DOWN, _LEFT, _UP), ((i + 1, j), (i, j), (i, j + 1), (i + 1, j + 1))):
            if valid(vi, vj, d) and (vi, vj, d) not in pending:
                pending.add((vi, vj, d))
                order.append((vi, vj, d))

    rings: list[list[tuple[int, int, int]]] = []
    for seed in order:
        if seed not in pending:
            continue
        ring: list[tuple[int, int, int]] = []
        i, j, d = seed
        while (i, j, d) in pending:
            pending.discard((i, j, d))
            ring.append((i, j, d))
            di, dj = _STEP[d]
            i, j = i + di, j + dj
            for turn in ((d + 3) % 4, d, (d + 1) % 4, (d + 2) % 4):
                if valid(i, j, turn):
                    d = turn
                    break
            else:
                break
        if ring:
            rings.append(ring)
    return rings


def _open_corners(pts: np.ndarray, threshold_deg: float, scales: tuple[float, ...] = (2.0, 4.0)) -> list[int]:
    """Interior corners of an open arc: a turn that survives every chord scale."""
    n = len(pts)
    if n < 5:
        return []
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    if float(cum[-1]) < 2.0 * max(scales):
        return []
    angles = np.full(n, np.inf)
    for s in scales:
        back = np.column_stack([np.interp(cum - s, cum, pts[:, 0]), np.interp(cum - s, cum, pts[:, 1])])
        fwd = np.column_stack([np.interp(cum + s, cum, pts[:, 0]), np.interp(cum + s, cum, pts[:, 1])])
        v1 = pts - back
        v2 = fwd - pts
        n1 = np.linalg.norm(v1, axis=1)
        n2 = np.linalg.norm(v2, axis=1)
        ang = np.degrees(np.arccos(np.clip(np.sum(v1 * v2, axis=1) / np.maximum(n1 * n2, 1e-12), -1.0, 1.0)))
        ang[(n1 < 1e-9) | (n2 < 1e-9)] = 0.0
        angles = np.minimum(angles, ang)
    # The ends are nodes: already placed, already tangent-matched, and the
    # chord either side of them is truncated, which biases the angle there.
    guard = 1.0
    angles[: max(1, int(np.searchsorted(cum, guard)))] = 0.0
    angles[min(n - 1, int(np.searchsorted(cum, cum[-1] - guard))) :] = 0.0

    cand = np.nonzero(angles > threshold_deg)[0]
    if cand.size == 0:
        return []
    window = min(scales)
    keep: list[int] = []
    for i in cand.tolist():
        if keep and cum[i] - cum[keep[-1]] <= window:
            if angles[i] > angles[keep[-1]]:
                keep[-1] = i
            continue
        keep.append(i)
    return keep


def _fit_arc(arc: Arc, params: CurveParams) -> list[Segment]:
    pts = arc.pts
    if arc.closed:
        if arc.mirror is not None and len(pts) >= 3:
            mirrored = _fit_mirrored(pts, arc.mirror, params)
            if mirrored is not None:
                return mirrored
        # A region wholly inside one neighbour: no node anywhere on it, so this
        # is an ordinary closed contour and the closed fit is the right one. It
        # keeps the curve G1 across the seam and looks for corners around the
        # wrap, neither of which an open fit can do.
        return [] if len(pts) < 3 else fit_contour_segments(pts, params)[0]
    if len(pts) < 2:
        return []
    if float(np.linalg.norm(pts[-1] - pts[0])) < 1e-6 and _arc_length(pts) < 2.0 * SHORT_ARC:
        # Both ends were placed on the same node: the one-pixel arc between two
        # nodes of a corner pixel, collapsed. The ring runs straight through.
        return []
    corners = _open_corners(pts, params.corner_threshold)
    sharp = {k: _sharp_corner(pts, k) for k in corners}
    bounds = sorted({0, len(pts) - 1, *corners})
    pieces: list[tuple[np.ndarray, int, int]] = []
    for k in range(len(bounds) - 1):
        lo, hi = bounds[k], bounds[k + 1]
        piece = _sharpen_piece(pts, lo, hi, corners, (arc.trim0, arc.trim1), sliver=arc.sliver)
        if len(piece) < 2:
            continue
        if lo in sharp:
            piece[0] = sharp[lo]
        if hi in sharp:
            piece[-1] = sharp[hi]
        pieces.append((piece, lo, hi))
    # Interior corners move to where the neighbouring line runs cross; the arc's
    # ends are nodes and stay where every arc at them agreed.
    corners_from_runs([piece for piece, _, _ in pieces], closed=False)
    segments: list[Segment] = []
    for piece, lo, hi in pieces:
        t_start = arc.t0 if lo == 0 else None
        t_end = arc.t1 if hi == len(pts) - 1 else None
        # Lines first: a straight run comes out as one line whatever the
        # pinned tangent says, and the curves between runs honour it.
        segments.extend(fit_stretch(piece, params.tol, t_start, t_end))
    return _snap_axis(segments, params.snap_axis_deg)


MIRROR_CORNER_REACH = 3.1  # px of approach used to decide and sharpen a crossing on the axis


def _fit_mirrored(pts: np.ndarray, axis: tuple[np.ndarray, np.ndarray], params: CurveParams) -> list[Segment] | None:
    """A closed, mirror-symmetric ring fitted on one half and reflected.

    The ring crosses its axis exactly twice. Each crossing is either smooth —
    the tangent there is perpendicular to the axis, and the half is fitted
    with that tangent pinned so the reflected joint is G1 — or a corner on the
    axis (a heart's notch and tip), which is sharpened as the crossing of the
    half's approach line with the axis itself, so it lands on the axis to the
    last digit. The half between the crossings is fitted as an open arc would
    be; the other half is its reflection, so the two sides are one geometry.
    None when the ring does not cross the axis exactly twice.
    """
    c, d = axis
    nrm = np.array([-d[1], d[0]])
    side = (pts - c) @ nrm
    n = len(pts)
    crossings = [i for i in range(n) if (side[i] >= 0.0) != (side[(i + 1) % n] >= 0.0)]
    if len(crossings) != 2:
        return None
    i0, i1 = crossings
    # the half on the positive side, from crossing i0 to crossing i1
    if side[(i0 + 1) % n] < 0.0:
        i0, i1 = i1, i0
    idx = [(i0 + 1 + k) % n for k in range(((i1 - i0) % n))]
    if len(idx) < 4:
        return None
    half_inner = pts[idx]

    def crossing(a: int, b: int) -> np.ndarray:
        pa, pb = pts[a], pts[b]
        sa, sb = float(side[a]), float(side[b])
        f = sa / (sa - sb) if sa != sb else 0.5
        x = pa + f * (pb - pa)
        return c + d * float((x - c) @ d)  # exactly on the axis

    x0 = crossing(i0, (i0 + 1) % n)
    x1 = crossing(i1, (i1 + 1) % n)

    def approach(points: np.ndarray, at: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
        """The line through the vertices 0.8–3.1 px from the crossing (the
        half-pixel chamfer at a corner vertex left out)."""
        dist = np.linalg.norm(points - at, axis=1)
        sel = (dist >= 0.8) & (dist <= MIRROR_CORNER_REACH)
        if int(sel.sum()) < 2:
            sel = (dist > 0) & (dist <= 2.0 * MIRROR_CORNER_REACH)
        if int(sel.sum()) < 2:
            return None
        return _line_through(points[sel])

    def joint(x: np.ndarray, points: np.ndarray, into: bool) -> tuple[np.ndarray, np.ndarray | None]:
        """The crossing point and the pinned tangent (None at a corner)."""
        line = approach(points, x)
        if line is None:
            return x, None
        p, direction = line
        # how far the approach leans off the perpendicular: twice that is the
        # turn at the reflected joint
        lean = math.degrees(math.asin(min(1.0, abs(float(direction @ d)))))
        if 2.0 * lean > params.corner_threshold:
            hit = _intersect(p, direction, c, d)
            if hit is not None and float(np.linalg.norm(hit - x)) <= 1.5:
                return hit, None
            return x, None
        return x, (nrm.copy() if into else -nrm)

    start, t_start = joint(x0, half_inner[: min(len(half_inner), 12)], True)
    end, t_end = joint(x1, half_inner[-min(len(half_inner), 12):][::-1], False)
    half = np.vstack([start, half_inner, end])
    corners = _open_corners(half, params.corner_threshold)
    sharp = {k: _sharp_corner(half, k) for k in corners}
    bounds = sorted({0, len(half) - 1, *corners})
    pieces: list[tuple[np.ndarray, int, int]] = []
    for k in range(len(bounds) - 1):
        lo, hi = bounds[k], bounds[k + 1]
        piece = _sharpen_piece(half, lo, hi, corners, (0.0, 0.0))
        if len(piece) < 2:
            continue
        if lo in sharp:
            piece[0] = sharp[lo]
        if hi in sharp:
            piece[-1] = sharp[hi]
        pieces.append((piece, lo, hi))
    corners_from_runs([piece for piece, _, _ in pieces], closed=False)
    # A corner on the axis is placed from the straight run that leads into
    # it, crossed with the axis: the few vertices next to an acute tip are
    # anti-aliasing mixtures pulled inward (a triangle's apex sat 0.9 px low
    # from a 3 px approach), a run of tens of pixels places it to hundredths.
    if pieces and t_start is None:
        _axis_corner_from_run(pieces[0][0], True, c, d)
    if pieces and t_end is None:
        _axis_corner_from_run(pieces[-1][0], False, c, d)
    segments: list[Segment] = []
    for piece, lo, hi in pieces:
        segments.extend(fit_stretch(piece, params.tol, t_start if lo == 0 else None, t_end if hi == len(half) - 1 else None))
    if not segments:
        return None
    segments = _snap_axis(segments, params.snap_axis_deg)
    mirrored = reverse_segments([_reflect_segment(seg, c, d) for seg in segments])
    full = merge_lines([*segments, *mirrored])
    # a smooth crossing between two lines is one line: the pair meeting at the
    # second crossing was folded above; the pair at the first meets at the wrap
    if len(full) > 1 and isinstance(full[0], Line) and isinstance(full[-1], Line):
        a, b = full[0].p1 - full[0].p0, full[-1].p1 - full[-1].p0
        la, lb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
        if la > 0 and lb > 0 and math.degrees(math.acos(min(1.0, max(-1.0, float(a @ b) / (la * lb))))) <= MERGE_DEG:
            full[-1] = Line(full[-1].p0.copy(), full[0].p1.copy())
            full = full[1:]
    return full


def _axis_corner_from_run(piece: np.ndarray, at_start: bool, c: np.ndarray, d: np.ndarray) -> None:
    """Move the piece's end on the axis to where its adjacent line run crosses
    the axis, when the run reaches within CORNER_REACH of that end and the
    crossing is within 2 px of it. In place."""
    runs = line_runs(piece)
    if not runs:
        return
    if at_start:
        i, _j, rc, rd = runs[0]
        reach = float(np.sum(np.linalg.norm(np.diff(piece[: i + 1], axis=0), axis=1))) if i > 0 else 0.0
        end = piece[0]
    else:
        _i, j, rc, rd = runs[-1]
        reach = float(np.sum(np.linalg.norm(np.diff(piece[j:], axis=0), axis=1))) if j < len(piece) - 1 else 0.0
        end = piece[-1]
    if reach > CORNER_REACH:
        return
    hit = _intersect(rc, rd, c, d)
    if hit is None or float(np.linalg.norm(hit - end)) > 2.0:
        return
    if at_start:
        piece[0] = hit
    else:
        piece[-1] = hit


def _reflect_segment(seg: Segment, c: np.ndarray, d: np.ndarray) -> Segment:
    if isinstance(seg, Line):
        return Line(reflect(seg.p0, c, d), reflect(seg.p1, c, d))
    if isinstance(seg, CircArc):
        return CircArc(reflect(seg.p0, c, d), reflect(seg.p1, c, d), seg.r, seg.large, not seg.sweep)
    return Cubic(reflect(seg.p0, c, d), reflect(seg.c1, c, d), reflect(seg.c2, c, d), reflect(seg.p1, c, d))


def _snap_axis(segments: list[Segment], snap_deg: float) -> list[Segment]:
    """Make a nearly horizontal or vertical line exactly so, as `curves.snap_axis_lines`
    does for a whole contour — but never moving the arc's own ends, which are
    nodes that the arcs on the other side have already been fitted to."""
    n = len(segments)
    for i, seg in enumerate(segments):
        if not isinstance(seg, Line):
            continue
        dx, dy = seg.p1 - seg.p0
        ang = np.degrees(np.arctan2(dy, dx)) % 180.0
        axis = 1 if min(ang, 180.0 - ang) <= snap_deg else (0 if abs(ang - 90.0) <= snap_deg else -1)
        if axis < 0:
            continue
        head, tail = i == 0, i == n - 1
        if head and tail:
            continue
        value = seg.p1[axis] if head else (seg.p0[axis] if tail else (seg.p0[axis] + seg.p1[axis]) / 2)
        if not head:
            seg.p0[axis] = value
            segments[i - 1].p1 = seg.p0.copy()
        if not tail:
            seg.p1[axis] = value
            segments[i + 1].p0 = seg.p1.copy()
    return segments


def _sharp_corner(pts: np.ndarray, k: int, reach: float = 3.1, trim: float = 0.8) -> np.ndarray:
    """Where an arc's interior corner really is: the crossing of lines fitted to
    the run-up on either side, skipping the half-pixel chamfer the lattice puts
    at the corner vertex itself. Falls back to the vertex when the lines do not
    cross within 1.5 px of it. This is what `curves.split_pieces` does for a
    closed contour; an arc's interior corners were left on the chamfer, and a
    line ending on a chamfer vertex leans half a pixel across a canvas border."""
    def near(q: np.ndarray) -> np.ndarray:
        d = np.linalg.norm(q - q[0], axis=1)
        sel = (d >= trim) & (d <= reach)
        if int(sel.sum()) < 2:
            sel = (d > 0) & (d <= 2.0 * reach)
        return q[sel]

    a, b = near(pts[:k + 1][::-1]), near(pts[k:])
    if len(a) >= 2 and len(b) >= 2:
        p, d = _line_through(a)
        q, e = _line_through(b)
        x = _intersect(p, d, q, e)
        if x is not None and float(np.linalg.norm(x - pts[k])) <= 1.5:
            return x
    return pts[k].copy()


def _sharpen_piece(pts: np.ndarray, lo: int, hi: int, corners: list[int], trims: tuple[float, float] = (NODE_TRIM, NODE_TRIM),
                   trim: float = 0.8, sliver: np.ndarray | None = None) -> np.ndarray:
    """One run between two breaks, with the vertices next to a break dropped where
    they cannot be trusted. The end points themselves stand: an arc end is a
    node every neighbouring arc has already agreed on.

    Next to an interior corner the marching-squares chamfer goes (`trim`). Next
    to a node the approach window goes (`trims`, at least NODE_TRIM, TIP_TRIM at
    a wedge tip, and wider where the node was moved): those vertices sit on
    pixels that mix three fills, and a fit that honours them bulges into the
    junction.
    """
    piece = pts[lo:hi + 1]
    if len(piece) < 4:
        return piece.copy()  # a copy: the caller moves its ends, and the arc's own vertices must stay
    inner = piece[1:-1]
    keep = np.ones(len(inner), bool)
    start_trim = trims[0] if lo == 0 else (trim if lo in corners else 0.0)
    end_trim = trims[1] if hi == len(pts) - 1 else (trim if hi in corners else 0.0)
    if start_trim > 0.0:
        keep &= np.linalg.norm(inner - piece[0], axis=1) >= start_trim
    if end_trim > 0.0:
        keep &= np.linalg.norm(inner - piece[-1], axis=1) >= end_trim
    if sliver is not None:
        keep &= ~sliver[lo + 1:hi]
    return np.vstack([piece[:1], inner[keep], piece[-1:]])
