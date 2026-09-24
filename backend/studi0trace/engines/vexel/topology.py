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
from scipy import ndimage

from studi0trace.engines.vexel.boundary import FillAt
from studi0trace.engines.vexel.regularity import regularize
from studi0trace.engines.vexel import rects
from studi0trace.engines.vexel.symmetry import reflect, ring_symmetries
from studi0trace.engines.vexel.curves import (
    CORNER_REACH,
    MERGE_DEG,
    CircArc,
    Cubic,
    CurveParams,
    Line,
    Rect,
    RoundedRect,
    Segment,
    arc_points,
    corners_from_runs,
    fit_stretch,
    line_runs,
    merge_lines,
    _intersect,
    _bezier,
    _bezier_d1,
    arc_centre,
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
# The bled copy's fitting tolerance, as a fraction of the bleed. It has to stay
# below it: an error larger than the offset would let the copy wander back over
# the edge it exists to cover.
UNDER_TOL = 0.6
# The bled copy is read off the *visible* curve, sampled this far apart: an
# offset of what is drawn, not of the vertices the fit was free to leave.
UNDER_STEP = 1.0
UNDER_SUB = 4
UNDER_TURN = 5.0  # degrees
# ...and a sample that comes back nearer the visible curve than this share of
# the bleed is dropped: the offset of an inside corner, or of a bend tighter than
# the bleed, crosses itself there, and the loop would reach back over the edge.
UNDER_CLEAR = 0.9
# The fitted copy has to keep within this share of the bleed of the offset
# samples everywhere; a fit that does not is tried again as curves only, then at
# half the tolerance, this many times, before the samples are used as they stand.
UNDER_DEV = 0.3
UNDER_TRIES = 3
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
TIP_AHEAD = 1.5  # px a held tip may sit beyond the end of its sliver (see `_junctions`)
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
# The sub-pixel placement reads each side's colour as the fitted fill plus the
# fill's residual, averaged over the region's pure pixels with a Gaussian of
# LOCAL_SIGMA px (cut at LOCAL_TRUNCATE sigma). LOCAL_SUPPORT is the Gaussian
# mass of pure pixels at which the correction counts half: it fades out where
# a region has no pure pixels near the edge. See `_local_fills`.
LOCAL_SIGMA = 2.0
LOCAL_TRUNCATE = 4.0  # scipy's default, which `core::filters::gaussian_filter` twins
LOCAL_SUPPORT = 0.05
# A region whose fitted alpha is under this is a transparent field: its colour
# is inpainting and gets no correction (see `_local_fills`).
LOCAL_OPAQUE_ALPHA = 128.0
# The corrected fills are believed in full where they keep at least LOCAL_KEEP1
# of the fitted fills' contrast across the edge, not at all below LOCAL_KEEP0,
# linearly between (see `_coverage`).
LOCAL_KEEP0 = 0.25
LOCAL_KEEP1 = 0.5


@dataclass
class Arc:
    """One run of boundary between two nodes, parting one pair of labels."""

    pair: tuple[int, int]
    pts: np.ndarray  # (N, 2) sub-pixel xy in SVG space
    n0: int | None  # flat lattice index of each end; None on a closed loop
    n1: int | None
    normal: np.ndarray | None = None  # unit vector per vertex, from pair[0] towards pair[1]
    segments: list[Segment] = field(default_factory=list)
    under: list[Segment] = field(default_factory=list)  # the same curve, bled under the side painted later
    under_into: int | None = None  # the label `under` reaches into
    under_jog: tuple[bool, bool] = (False, False)  # `under` starts / ends with a jog from / to its node
    t0: np.ndarray | None = None  # tangent pinned at each end, pointing into the arc
    t1: np.ndarray | None = None
    tip0: bool = False  # this end is the tip of a wedge closing to a point
    tip1: bool = False
    trim0: float = NODE_TRIM  # vertices within this of each end are not believed (widened by the node's move)
    trim1: float = NODE_TRIM
    sliver: np.ndarray | None = None  # per vertex: placed on a pixel handed back to a cut-off wedge (three-fill mixture)
    mirror: tuple[np.ndarray, np.ndarray] | None = None  # a closed arc's mirror axis (point, unit direction), when it has one
    rect: Rect | RoundedRect | None = None  # a closed arc that is one (rounded) rectangle, as the primitive

    @property
    def closed(self) -> bool:
        return self.n0 is None


@dataclass
class Boundary:
    arcs: list[Arc]
    padded: np.ndarray  # the label map with a one-pixel border of 0
    edge_arc: dict[int, tuple[int, int]]  # undirected edge key -> (arc, position)
    _later_is_b: list[bool] = field(default_factory=list)  # which side of each arc paints later
    rank: dict[int, int] | None = None  # paint order by label, when the shapes are stacked

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

    def primitive(self, ring: list[tuple[int, bool]]) -> Rect | RoundedRect | None:
        """The whole-shape primitive a ring already is, when `_rectify` made it one."""
        if len(ring) == 1:
            return self.arcs[ring[0][0]].rect
        return None

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
        pieces: list[list] = []  # [segments, (jog in, jog out), start node, end node]
        own = min(self.rank.get(m, -1) for m in member) if member and self.rank is not None else None
        for idx, reverse in ring:
            arc = self.arcs[idx]
            segs = arc.segments
            jog = (False, False)
            if member is not None and arc.under and arc.under_into not in member:
                # the far side is painted after this shape: reach under it
                if own is None or self.rank.get(arc.under_into, -1) > own:
                    segs, jog = arc.under, arc.under_jog
            if reverse:
                pieces.append([reverse_segments(segs), (jog[1], jog[0]), arc.n1, arc.n0])
            else:
                pieces.append([list(segs), jog, arc.n0, arc.n1])
        # Two bled copies meeting at a node where everything else is painted
        # later are joined directly, not each pinned back to the node: pinned,
        # the three shapes there each anti-alias their own corner of one point,
        # and a third over a third over a third leaves a dot of backdrop.
        # Joined, this shape runs a pixel past the node under the later ones.
        if member is not None and self.rank is not None and len(pieces) > 1:
            for k in range(len(pieces)):
                p, q = pieces[k], pieces[(k + 1) % len(pieces)]
                if not (p[1][1] and q[1][0]) or p[3] is None or p[3] != q[2] or not self._all_later(p[3], member, own):
                    continue
                p[0] = p[0][:-1] + [Line(p[0][-1].p0.copy(), q[0][0].p1.copy())]
                q[0] = q[0][1:]
                p[1] = (p[1][0], False)
                q[1] = (False, q[1][1])
        # An arc too short to carry both of its nodes (one lattice edge between
        # two nodes placed apart: the end cap of a stem narrower than two
        # pixels) has no segments, and the ring would jump the gap; written as
        # a path, the next segment then starts from the wrong point and a
        # straight stem comes out as a wedge. The gap is a line.
        out: list[Segment] = []
        for piece in pieces:
            for seg in piece[0]:
                if out and float(np.linalg.norm(seg.p0 - out[-1].p1)) > 1e-6:
                    out.append(Line(out[-1].p1.copy(), seg.p0.copy()))
                out.append(seg)
        return out

    def _all_later(self, node: int, member: frozenset[int], own: int) -> bool:
        """Every label at a lattice node is this shape's own or painted after it."""
        lat_cols = self.padded.shape[1] + 1
        i, j = divmod(node, lat_cols)
        around = self.padded[max(i - 1, 0):i + 1, max(j - 1, 0):j + 1]
        return all(int(v) in member or (int(v) != 0 and self.rank.get(int(v), -1) > own) for v in np.unique(around))


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


def _local_fills(labels: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, fill_at: FillAt,
                 sigma: float = LOCAL_SIGMA, support: float = LOCAL_SUPPORT) -> FillAt:
    """Each region's fill as it actually is near a point: the fitted model plus
    the model's own residual there, smoothed over the region's pure pixels.

    A region's fill is one gradient fitted to all of it, and on shaded artwork
    that model can be a dozen levels off near one of the region's edges — the
    fold of a ribbon lighter than the ramp through the whole ribbon. The
    sub-pixel placement projects an edge pixel onto the segment between the two
    fills, so an error of that size puts the pure pixels of a region at a
    coverage near a half, the crossing wanders from pixel to pixel along the
    edge, and the fit follows it: a straight edge between two shaded regions
    comes out wavy, and a tip placed from that edge's direction lands pixels
    away. The residual, read from pixels whose neighbours are all the region's
    (so no anti-aliasing mixture enters it) and averaged with a Gaussian of
    `sigma` px, is what the model misses locally; adding it back makes the
    projection's reference colours the colours two pixels either side of the
    edge. Where a region has no pure pixels nearby (a hairline, the far side of
    a tip) the correction fades out with the support and the model stands.

    Colour only: alpha is the coverage itself. The middle of a stroke four
    pixels wide against transparency is not opaque, and taking its alpha as the
    stroke's own moved the stroke's edges (three new pinholes on
    studi0mail-logo-dark, a worse sawtooth on thin-mark-512-ds).
    """
    h, w = labels.shape
    radius = int(math.floor(LOCAL_TRUNCATE * sigma + 0.5))
    corr: dict[int, tuple[int, int, np.ndarray]] = {}
    boxes = ndimage.find_objects(np.maximum(labels, 0))
    for index, box in enumerate(boxes):
        if box is None:
            continue
        lab = index + 1
        r0, r1 = max(0, box[0].start - radius), min(h, box[0].stop + radius)
        c0, c1 = max(0, box[1].start - radius), min(w, box[1].stop + radius)
        m = labels[r0:r1, c0:c1] == lab
        # pure: all eight neighbours are the region's own (the canvas frame counts as own)
        pure = ndimage.binary_erosion(m, np.ones((3, 3), bool), border_value=1)
        if not pure.any():
            continue
        yy, xx = np.nonzero(pure)
        model = fill_at(lab, xx + c0 + 0.5, yy + r0 + 0.5)
        if float(np.mean(model[:, 3])) < LOCAL_OPAQUE_ALPHA:
            # A transparent field's colour is inpainting, not ink: near an edge
            # it is the ink's own colour copied outward, and reading it as the
            # field's makes the projection alpha-only. Kept to the fitted
            # model, so a silhouette against transparency is placed as before
            # (corrected, studi0trace-mark-512 lost 0.9 % of its ink to seams).
            continue
        res = np.zeros((r1 - r0, c1 - c0, 3))
        res[yy, xx] = rgb[yy + r0, xx + c0] - model[:, :3]
        den = ndimage.gaussian_filter(pure.astype(float), sigma, mode="constant", truncate=LOCAL_TRUNCATE)
        num = np.stack([ndimage.gaussian_filter(res[..., k], sigma, mode="constant", truncate=LOCAL_TRUNCATE)
                        for k in range(3)], axis=-1)
        corr[lab] = (r0, c0, num / (den[..., None] + support))

    def local(lab: int, qx: np.ndarray, qy: np.ndarray) -> np.ndarray:
        base = fill_at(lab, qx, qy)
        found = corr.get(int(lab))
        if found is None:
            return base
        r0, c0, img = found
        col = np.floor(qx).astype(int) - c0
        row = np.floor(qy).astype(int) - r0
        inside = (row >= 0) & (row < img.shape[0]) & (col >= 0) & (col < img.shape[1])
        out = base.copy()
        out[inside, :3] += img[row[inside], col[inside]]
        return out

    return local


def _coverage(pad_rgba: np.ndarray, pix: np.ndarray, lab: int, other: int, fill_at: FillAt,
              local: FillAt | None = None) -> np.ndarray:
    """How much of each pixel is `lab` rather than `other`, read from its colour.

    Given `local` (`_local_fills`), the reference colours are the fills as they
    are beside the edge — where those still differ by LOCAL_KEEP1 of what the
    fitted fills differ by. Where the two sides' local colours meet, there is no
    edge in the colour to place: a boundary the partition drew through one
    continuous gradient (radial-focal-128's corner) has its two local colours at
    a third of the fitted contrast, a colour level of ripple in the correction is
    a third of a pixel of edge, and the one clean arc came out a wobble. There
    the fitted fills, smooth by construction, place it as before.
    """
    qx = pix[:, 1] - 0.5
    qy = pix[:, 0] - 0.5
    colour = pad_rgba[pix[:, 0], pix[:, 1]]
    f_lab, f_other = fill_at(lab, qx, qy), fill_at(other, qx, qy)
    if local is not None:
        l_lab, l_other = local(lab, qx, qy), local(other, qx, qy)
        kept = np.linalg.norm(l_lab - l_other, axis=1) / np.maximum(np.linalg.norm(f_lab - f_other, axis=1), 1e-9)
        w = np.clip((kept - LOCAL_KEEP0) / (LOCAL_KEEP1 - LOCAL_KEEP0), 0.0, 1.0)[:, None]
        f_lab = f_lab + w * (l_lab - f_lab)
        f_other = f_other + w * (l_other - f_other)
    diff = f_lab - f_other
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
    with_found: bool = False,
    local: FillAt | None = None,
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
        cov = _coverage(pad_rgba, clipped, a, b, fill_at, local)
        usable = (padded[clipped[:, 0], clipped[:, 1]] == want) & np.all(clipped == pix, axis=1) & np.isfinite(cov)
        return cov if fallback is None else np.where(usable, cov, fallback)

    here = _coverage(pad_rgba, p_in, a, b, fill_at, local)
    there = _coverage(pad_rgba, p_out, a, b, fill_at, local)
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
    placed = np.clip(over + (t - over) * trust, -REACH, 1.0 + REACH)
    if with_found:
        # Where no crossing was found, which way the samples say the edge lies:
        # -1 all four read as `b`, so it is beyond the `a` pixel; +1 all read as
        # `a`, beyond the `b` pixel; 0 found, or the samples disagree.
        side = np.zeros(len(p_in), dtype=np.int8)
        none = ~np.isfinite(best)
        side[none & np.all(level < 0.5, axis=1)] = -1
        side[none & np.all(level >= 0.5, axis=1)] = 1
        return placed, side
    return placed


def _place(
    chains: list[dict],
    padded: np.ndarray,
    rgb: np.ndarray,
    alpha: np.ndarray,
    fill_at: FillAt,
    handed_back: set[tuple[int, int]] | None = None,
    local: FillAt | None = None,
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
        side = np.zeros(len(ch["edges"]), dtype=np.int8)
        if a != 0 and b != 0:
            t, side = _crossing(pad_rgba, padded, p_in, p_out, a, b, fill_at, with_found=True, local=local)
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
        pts = c_in + t[:, None] * step
        if len(pts) >= 3:
            # A vertex whose four samples all sit on one side of a half has no
            # crossing within reach along its own step — on a steep staircase the
            # step at an outer corner meets the edge at a glancing angle, and the
            # edge is a pixel beyond it. Left at the label edge it stands out
            # of line with both neighbours, which found the edge beyond the
            # label edge on that same side: a spike. Such a vertex takes the
            # midpoint of its neighbours. Where the neighbours sit inside their
            # own two pixels — a shape's corner pixel, too mixed to cross a half
            # on either axis — the label edge is the corner, and it stays.
            lone = np.zeros(len(pts), dtype=bool)
            beyond_a = (t < 0.0) & (side == 0)
            beyond_b = (t > 1.0) & (side == 0)
            lone[1:-1] = (((side[1:-1] == -1) & beyond_a[:-2] & beyond_a[2:])
                          | ((side[1:-1] == 1) & beyond_b[:-2] & beyond_b[2:]))
            if lone.any():
                mid = np.zeros_like(pts)
                mid[1:-1] = (pts[:-2] + pts[2:]) / 2.0
                pts = np.where(lone[:, None], mid, pts)
        pts = _unfold(pts)
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

    moves: list[tuple[list[tuple[int, int]], np.ndarray, dict, set, np.ndarray]] = []
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
        plain = target
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
            plain = target
            target = _node_estimate([lines[key] for key in tips], mean, TIP_LIMIT)
            if any(_sliver_at(arcs[i], k) for i, k in tips):
                # The handed-back sliver is the colour's own evidence of how far
                # the wedge reaches, and the arcs end where it ends. Where the
                # sides' lines cross short of that — two lines meeting at 15-25
                # degrees place their crossing to a few pixels at best — the tip
                # stepped back inside its own sliver: the sliver's vertices were
                # left beyond the node and the fit turned back from them onto
                # it, a hook (the wordmark's cyan tip; wedge-fan's). The tip may
                # move across the wedge and further out, never back into it.
                inward = sum(away[key] for key in tips)
                length = float(np.hypot(*inward))
                if length > 1e-9:
                    inward = inward / length
                    along = float((target - mean) @ inward)
                    # Nor may it run far out past the sliver: past the last
                    # pixel the colour hands back, a wedge narrower than a
                    # third of a pixel reaches on for about a pixel at 18
                    # degrees. Two sides that close at 15-25 degrees cross
                    # wherever a tenth of a pixel of placement puts them — on
                    # wedge-fan, 4 px down the bar's edge, clamped by TIP_LIMIT.
                    target = target - (along - float(np.clip(along, -TIP_AHEAD, 0.0))) * inward
                # ...and it sits on the boundary that carries on through it, so
                # that boundary stays one line: the tip is where the wedge ends
                # against it, not a notch in it.
                carry = lines.get(keys[through])
                if carry is not None:
                    c, dvec = carry[0], carry[1]
                    target = c + dvec * float((target - c) @ dvec)
                # Placed from the sliver rather than from the lines, the tip no
                # longer needs the lines' directions, and on a curved side they
                # are wrong for a tangent: grown out along the side while it
                # stays straight to APPROACH_RMS, the line reads the curve's
                # direction several pixels back (35 degrees against 45 on the
                # wordmark's ribbon). Pinned there, the curve into the tip took
                # three cubics, lines-first lost to the plain fit and the
                # ribbon's straight top came out an S. The sides leave the tip
                # along their own vertices; a straight side is a line either way.
                for key in tips:
                    tangents.pop(key, None)
            target = _on_border(target, [(arcs[i], k) for i, k in incident])
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
        moves.append((incident, target, tangents, tips, plain))

    # A tip is placed from its two sides alone and may travel up to TIP_LIMIT,
    # which on a short arc is further than the arc is long: the tip then lands
    # beyond the node at the arc's other end, the arc between them runs
    # backwards, and its fit is a hairpin that crosses both neighbours — a
    # spike at the tip, and a patch that no shape paints. A node that would turn
    # an arc round goes back to where all its arcs together place it.
    at: dict[tuple[int, int], int] = {}
    for m, (incident, _t, _g, _tips, _p) in enumerate(moves):
        for key in incident:
            at[key] = m
    targets = [mv[1] for mv in moves]
    reverted: set[int] = set()
    for _ in range(len(moves)):
        undo: set[int] = set()
        for idx, arc in enumerate(arcs):
            if arc.closed or idx in short or (idx, 0) not in at or (idx, -1) not in at:
                continue
            m0, m1 = at[(idx, 0)], at[(idx, -1)]
            if m0 == m1:
                continue
            chord = arc.pts[-1] - arc.pts[0]
            if float(np.dot(targets[m1] - targets[m0], chord)) > 0.0:
                continue
            for m in (m0, m1):
                if moves[m][3] and m not in reverted:
                    undo.add(m)
        if not undo:
            break
        for m in sorted(undo):
            targets[m] = moves[m][4]
            reverted.add(m)
    moves = [(incident, targets[m], tangents, tips) for m, (incident, _t, tangents, tips, _p) in enumerate(moves)]

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
    extend: bool = True,
    see_through: set[int] | None = None,
    painted_by: dict[int, int] | None = None,
) -> Boundary:
    """The whole boundary of the label map, placed sub-pixel and fitted once.

    `rank` is the paint order by label. Given it, each arc also gets a copy bled
    towards whichever side paints later, for the earlier side to use — unless
    the later side is in `see_through`: paint that does not hide what lies
    beneath it (a translucent fill, a line drawn along a region's middle), under
    which a bleed would show as a band of the wrong colour. `painted_by` names,
    for a label no fill of its own paints (a stroked region), the earlier shape
    that fills it underneath; that shape's copy then reaches under the label's
    other neighbours.
    """
    bleed = BLEED if bleed is None else bleed
    padded = np.pad(labels.astype(np.int64), 1, constant_values=0)
    handed_back: set[tuple[int, int]] = set()
    if extend:
        padded, handed_back = _extend_wedges(padded, rgb, alpha, fill_at, params)
    chains = _chains(padded)
    # The placement reads colour against the fills as they are beside each edge.
    placed = _place(chains, padded, rgb, alpha, fill_at, handed_back, local=_local_fills(labels, rgb, alpha, fill_at))

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
    # Rounded rectangles drawn as a designer draws them: one radius per shape
    # and per size of shape, edges on shared guides. See `rects.py`.
    whole = Boundary(arcs=arcs, padded=padded, edge_arc=edge_arc, _later_is_b=[])
    rect_arcs, radii = _rectify(whole, params, rgb)
    # and a rounded corner between two lines anywhere else is one circle, of
    # the radius the rest of the mark's corners share where they agree
    _fillets(whole, params, rect_arcs, radii)
    # Across the graph: lines meant to be parallel, perpendicular or on an axis
    # are made exactly so. Nodes never move, so the ring still closes.
    regularize([(arc.segments, arc.closed) for arc in arcs], params.snap_axis_deg)

    later_is_b: list[bool] = []
    by_label: dict[int, list[int]] = {}
    for idx, arc in enumerate(arcs):
        for lab in sorted(set(arc.pair)):
            by_label.setdefault(lab, []).append(idx)
    drawn: dict[int, np.ndarray] = {}
    see_through = see_through or set()
    # The earliest paint on each label: its own shape's, or that of the shape
    # filling it underneath.
    floor = dict(rank or {})
    for lab, owner in (painted_by or {}).items():
        if rank is not None and lab in rank and owner in rank:
            floor[lab] = min(rank[lab], rank[owner])

    def walls(into: int, painter: int, idx: int) -> list[np.ndarray]:
        # The far side of the shape bled under, where crossing it would put
        # the painter's colour over something painted before it. Crossing into
        # a shape painted after it is as hidden as the bleed itself.
        out = []
        for j in by_label.get(into, []):
            other = arcs[j].pair[0] if arcs[j].pair[1] == into else arcs[j].pair[1]
            if j == idx or not arcs[j].segments or other == 0 or rank.get(other, -1) >= painter:
                continue
            if j not in drawn:
                drawn[j] = _sample(arcs[j].segments)[0]
            out.append(drawn[j])
        return out

    for idx, arc in enumerate(arcs):
        a, b = arc.pair
        b_later = rank is not None and rank.get(b, -1) > rank.get(a, -1)
        later_is_b.append(b_later)
        if rank is None or bleed <= 0.0 or a == 0 or b == 0:
            continue
        # Into the side first painted later, from the other: its own shape, or
        # the shape filling it underneath. (Only one side can need a copy: every
        # paint on the one comes before the first on the other.)
        into, side = (b, a) if floor.get(a, -1) < floor.get(b, -1) else (a, b)
        if into in see_through or floor.get(side, -1) >= rank.get(into, -1):
            continue
        # the latest shape to use the copy: the side's own, when it is earlier
        painter = rank.get(side, -1) if rank.get(side, -1) < rank.get(into, -1) else floor.get(side, -1)
        # The bled copy is never seen — the shape that causes it covers it —
        # so it is fitted loosely. Holding it to the visible tolerance would
        # spend nodes describing a curve nobody looks at.
        # Fitted no looser than the bleed can absorb: an error larger than
        # the offset would let the copy wander back across the very edge it
        # is there to cover.
        # The copy is weighed at its own tolerance alone: nobody sees it.
        loose = replace(params, tol=min(2.0 * params.tol, UNDER_TOL * bleed), kind_tol=math.inf)
        arc.under, arc.under_jog = _under(arc, bleed if into == b else -bleed, loose, walls(into, painter, idx))
        arc.under_into = into

    return Boundary(arcs=arcs, padded=padded, edge_arc=edge_arc, _later_is_b=later_is_b, rank=rank)


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


GUIDE_MIN = 12.0     # px; an axis-aligned line this long elsewhere in the graph is a guide other edges snap to
FINAL_SLACK = 1.5    # the snapped model still holds every trusted vertex within this many tolerances
CUSP_ALONG_DEG = 10.0  # an arc leaving a node within this of a side's direction carries that side on
CUSP_REACH = 4.0      # px past a rounded corner where the arc outside it must run along a side line


@dataclass
class _NodeCorner:
    """A junction node in one of the model's corners: the corner is not read
    from its vertices (the node's approach window hides them) and is decided
    once the model is regular (`_decide_corner`)."""

    point: np.ndarray
    corner: int
    sides: list[int]  # the sides (0 x0, 1 x1, 2 y0, 3 y1) whose line the node is on
    before: int       # ring position of the arc arriving at the node
    after: int        # ring position of the arc leaving it


@dataclass
class _RectCandidate:
    ring: list[tuple[int, bool]]
    poly: np.ndarray
    trusted: np.ndarray
    model: rects.Model
    clockwise: bool
    # per side (x0, x1, y0, y1): the coordinates of the nodes on it, which it
    # may not leave by more than NODE_ON (empty: a free side)
    pinned: list[list[float]]
    node_corners: list[_NodeCorner]
    sigma: float = 0.0  # the blur read across the shape's sides (`rects.edge_sigma`)


def _ring_vertices(bnd: Boundary, ring: list[tuple[int, bool]]) -> tuple[np.ndarray, np.ndarray]:
    """The ring's vertices, and which of them the fit believes: not those inside
    a node's approach window (`trim0`/`trim1`), which `_fit_arc` leaves out too,
    and not the node itself, which `_nodes` judges on its own: where a square
    meets a bar the node sits in the square's corner, and says nothing about
    the corner's radius."""
    parts, ok = [], []
    for idx, rev in ring:
        arc = bnd.arcs[idx]
        pts = arc.pts
        keep = np.ones(len(pts), dtype=bool)
        if not arc.closed:
            cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
            keep = ~((cum < arc.trim0) | (cum[-1] - cum < arc.trim1))
        parts.append(pts[::-1] if rev else pts)
        ok.append(keep[::-1] if rev else keep)
    return np.vstack(parts), np.concatenate(ok)


_SIDE_AXIS = (0, 0, 1, 1)  # the coordinate each side (x0, x1, y0, y1) fixes


def _side_level(m: rects.Model, side: int) -> float:
    return (m.x0, m.x1, m.y0, m.y1)[side]


def _side_corners(side: int) -> tuple[int, int]:
    """The corners (CORNERS indices) at a side's low and high end."""
    return ((0, 3), (1, 2), (0, 1), (3, 2))[side]


def _nodes(ring: list[tuple[int, bool]], arcs: list[Arc], m: rects.Model) -> tuple[list[list[float]], list[_NodeCorner]] | None:
    """Where the ring's nodes sit on the model: the sides they pin, and the
    nodes that sit in a corner. None when a node is off the model and in no
    corner: the model cannot be written into arcs that end there.

    A node on a side pins that side at its own level: the side may not move
    further than the node may (NODE_ON, onto the side), because every other
    arc there was fitted to the node. A node within a corner's reach of the
    corner point is that corner's: the T where a square meets a bar, or the
    tip of the notch between two rounded corners."""
    if len(ring) == 1 and arcs[ring[0][0]].closed:
        return [[], [], [], []], []
    free = [m.r[k] for k in range(4) if m.votes[k] > 0 and m.r[k] > 0.0]
    reach = (max(free) if free else 0.0) + rects.NODE_ON
    pinned: list[list[float]] = [[], [], [], []]
    corners: list[_NodeCorner] = []
    for k, (idx, rev) in enumerate(ring):
        arc = arcs[idx]
        p = arc.pts[0] if rev else arc.pts[-1]  # the node this arc arrives at
        on: list[int] = []
        near: list[tuple[float, int]] = []
        for side in range(4):
            axis = _SIDE_AXIS[side]
            if abs(float(p[axis]) - _side_level(m, side)) > rects.NODE_ON:
                continue
            lo, hi = (m.y0, m.y1) if axis == 0 else (m.x0, m.x1)
            along = float(p[1 - axis])
            if along < lo - rects.NODE_ON or along > hi + rects.NODE_ON:
                continue
            on.append(side)
            c_lo, c_hi = _side_corners(side)
            near.append((along - lo, c_lo))
            near.append((hi - along, c_hi))
        for side in on:
            pinned[side].append(float(p[_SIDE_AXIS[side]]))
        in_corner = sorted((d, c) for d, c in near if d <= reach)
        if in_corner:
            c = in_corner[0][1]
            corners.append(_NodeCorner(p.copy(), c, [s for s in on if c in _side_corners(s)], k, (k + 1) % len(ring)))
            continue
        if not on and rects.project(m, p)[1] > rects.NODE_ON:
            return None
    return pinned, corners


def _arcs_at(arcs: list[Arc], point: np.ndarray) -> list[tuple[int, int]]:
    """Every (arc, end) placed on this node."""
    out = []
    for idx, arc in enumerate(arcs):
        if arc.closed or len(arc.pts) < 2:
            continue
        for k in (0, -1):
            if arc.pts[k][0] == point[0] and arc.pts[k][1] == point[1]:
                out.append((idx, k))
    return out


def _runs_along(pts: np.ndarray, corner: np.ndarray, r: float, m: rects.Model, side: int, tol: float) -> bool:
    """Whether an arc's vertices a little past a rounded corner (r + 1 to
    r + CUSP_REACH px from the corner point) lie on the line of one of its
    sides, within `tol`: a neighbour's edge that carries that line on, or
    rounds its own corner into it."""
    d = np.hypot(pts[:, 0] - corner[0], pts[:, 1] - corner[1])
    far = pts[(d >= r + 1.0) & (d <= r + CUSP_REACH)]
    if len(far) < 2:
        return False
    return bool(np.all(np.abs(far[:, _SIDE_AXIS[side]] - _side_level(m, side)) <= tol))


def _decide_corner(arcs: list[Arc], cand: _RectCandidate, nc: _NodeCorner, m: rects.Model, r_shape: float,
                   tol: float) -> tuple[str, tuple | None]:
    """Is the corner at this node rounded like the shape's free corners, and
    if so how is it made so? Returns ("sharp", None), ("cusp", plan) with plan
    (side, S, C, (O, end), carries), or ("unresolved", None).

    The label map cannot hold the sliver between a rounded corner and the
    straight edge it meets: the notch runs out to a point. So the node sits
    short of that point, where the pixels ran out, and the corner comes out
    sharp, or bulges into a foot. The vertices near the node still say which
    it is: they are asked whether the shape's radius or a sharp corner holds
    them better. A rounded one is a cusp: the node slides along a side to the
    corner's tangent point (`_apply_cusp`), which is right only where the
    outside arc O runs along one of the corner's two side lines past the
    corner: straight on along the side the node slides on (`carries`: a bar
    the square sits flush against), or round a corner of its own into the
    other (the bar's rounded corner beside the square's). An O that leaves at
    an angle (a circle crossing the corner) is neither, and neither is a node
    the graph does not make a three-way junction: "unresolved", and the shape
    is not rewritten."""
    if r_shape <= 0.0:
        return "sharp", None
    cx, cy, sx, sy = rects._corner_frame(m, nc.corner)
    u = sx * (cand.poly[:, 0] - cx)
    v = sy * (cand.poly[:, 1] - cy)
    zone = (u < r_shape + 1.0) & (v < r_shape + 1.0) & (u > -1.0) & (v > -1.0)
    zone &= np.hypot(cand.poly[:, 0] - nc.point[0], cand.poly[:, 1] - nc.point[1]) > 1e-9
    if int(zone.sum()) < 2:
        return "unresolved", None
    round_cost = float(np.sum(rects._corner_dist(u[zone], v[zone], r_shape) ** 2))
    sharp_cost = float(np.sum(rects._corner_dist(u[zone], v[zone], 0.0) ** 2))
    if round_cost >= sharp_cost:
        return "sharp", None
    here = _arcs_at(arcs, nc.point)
    ring_arcs = {cand.ring[nc.before][0], cand.ring[nc.after][0]}
    outside = [(i, k) for i, k in here if i not in ring_arcs]
    if len(here) != 3 or len(outside) != 1:
        return "unresolved", None
    o_idx, o_end = outside[0]
    o_pts = arcs[o_idx].pts if o_end == 0 else arcs[o_idx].pts[::-1]
    o_segs = arcs[o_idx].segments if o_end == 0 else reverse_segments(arcs[o_idx].segments)
    if not o_segs:
        # a collapsed arc (both ends on one node) has no first piece to leave along
        return "unresolved", None
    head = o_segs[0]
    point = np.array([cx, cy])
    corner_sides = [k for k in range(4) if nc.corner in _side_corners(k)]

    def straight_along(s_: int) -> bool:
        direction = np.array([0.0, 1.0]) if _SIDE_AXIS[s_] == 0 else np.array([1.0, 0.0])
        return (isinstance(head, Line) and float(np.linalg.norm(head.p1 - head.p0)) > 1e-9
                and abs(float(_normalize(head.p1 - head.p0) @ direction)) >= math.cos(math.radians(CUSP_ALONG_DEG)))

    # which side the node slides along: the one whose line it is on; at the
    # corner point itself, where it is on both, the one O carries straight
    # on, else the other (O rounds its own corner into the one it runs along)
    options = nc.sides if len(nc.sides) == 1 else corner_sides
    side = None
    carries = False
    for s_ in options:
        other = next(k for k in corner_sides if k != s_)
        if _runs_along(o_pts, point, r_shape, m, s_, tol) and straight_along(s_):
            side, carries = s_, True
            break
        if _runs_along(o_pts, point, r_shape, m, other, tol):
            side, carries = s_, False
            break
    if side is None:
        return "unresolved", None
    # of the two ring arcs at the node, the one that runs along that side
    axis = _SIDE_AXIS[side]
    level = _side_level(m, side)
    along_side = []
    for pos in (nc.before, nc.after):
        idx, _rev = cand.ring[pos]
        q = arcs[idx].pts
        along_side.append(float(np.median(np.abs(q[:, axis] - level))))
    s_pos = nc.before if along_side[0] <= along_side[1] else nc.after
    if min(along_side) > rects.NODE_ON:
        return "unresolved", None
    c_pos = nc.after if s_pos == nc.before else nc.before
    s_idx, c_idx = cand.ring[s_pos][0], cand.ring[c_pos][0]
    if set(arcs[o_idx].pair) != set(arcs[s_idx].pair) ^ set(arcs[c_idx].pair):
        return "unresolved", None
    return "cusp", (side, s_idx, c_idx, (o_idx, o_end), carries)


def _tangent_point(m: rects.Model, corner: int, side: int) -> np.ndarray:
    """Where the corner's arc leaves the side."""
    cx, cy, sx, sy = rects._corner_frame(m, corner)
    r = m.r[corner]
    if _SIDE_AXIS[side] == 0:   # a vertical side: the point is r along y
        return np.array([_side_level(m, side), cy + sy * r])
    return np.array([cx + sx * r, _side_level(m, side)])


def _placed_normals(arc: Arc) -> np.ndarray:
    """Normals for an arc whose vertices were spliced: the direction from its
    own tangent, the side (pair[0] towards pair[1]) from the placed normal
    in its middle, which the splice did not touch."""
    pts = arc.pts
    ahead = np.vstack([pts[1:], pts[-1:]])
    behind = np.vstack([pts[:1], pts[:-1]])
    t = ahead - behind
    t = t / np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)
    normal = np.column_stack([-t[:, 1], t[:, 0]])
    if arc.normal is not None and len(arc.normal):
        ref = arc.normal[len(arc.normal) // 2]
        if float(ref @ normal[len(normal) // 2]) < 0.0:
            normal = -normal
    return normal


def _apply_cusp(arcs: list[Arc], m: rects.Model, corner: int, plan: tuple, node: np.ndarray, params: CurveParams) -> np.ndarray:
    """Slide the node along the side to the corner's tangent point T.

    The side arc S (the ring's, along that side) loses its vertices between T
    and the node, and the corner arc C starts at T. The outside arc O starts
    at T too, leaving it along the side, as all three arcs are tangent there.
    Where O carried the side on past the node (a square flush against a bar
    whose edge runs on), the vertices S lost part O's two regions along the
    same straight edge and join it. Where O left the side at the node (the
    bar's own rounded corner), O is that corner: `_node_fillet`, or refitted
    from T."""
    side, s_idx, c_idx, (o_idx, o_end), carries_on = plan
    t = _tangent_point(m, corner, side)
    along = 1 - _SIDE_AXIS[side]
    step = float(np.sign(float(node[along]) - float(t[along])))  # from T towards the node
    S, C, O = arcs[s_idx], arcs[c_idx], arcs[o_idx]
    s_end = 0 if S.pts[0][0] == node[0] and S.pts[0][1] == node[1] else -1
    s_pts = S.pts if s_end == -1 else S.pts[::-1]         # walking towards the node
    s_nrm = None if S.normal is None else (S.normal if s_end == -1 else S.normal[::-1])
    beyond = step * (s_pts[:, along] - float(t[along])) > 0.0
    beyond[-1] = True
    cut = max(1, int(np.argmax(beyond)))                   # the first vertex past T
    # the vertices between T and the node: they lay along the side and now
    # part the outside arc's regions along the same straight edge, so they go
    # onto it (the label map bent them into the notch it could not hold)
    moved = s_pts[cut:-1].copy()
    moved[:, _SIDE_AXIS[side]] = _side_level(m, side)
    s_new = np.vstack([s_pts[:cut], t[None, :]])
    S.pts = s_new if s_end == -1 else s_new[::-1].copy()
    if s_nrm is not None:
        n_new = np.vstack([s_nrm[:cut], s_nrm[cut - 1:cut]])
        S.normal = n_new if s_end == -1 else n_new[::-1].copy()
    o_pts = O.pts if o_end == 0 else O.pts[::-1]           # walking away from the node
    o_segs = O.segments if o_end == 0 else reverse_segments(O.segments)
    direction = np.zeros(2)
    direction[along] = step                                # along the side, from T past the node
    through = node.copy()
    through[_SIDE_AXIS[side]] = _side_level(m, side)
    o_trim = O.trim0 if o_end == 0 else O.trim1
    o_cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(o_pts, axis=0), axis=1))])
    first = max(1, int(np.searchsorted(o_cum, o_trim)))
    if first >= len(o_pts) - 1:
        first = 1
    head = o_segs[0]
    if carries_on:
        # the outside arc carries the side on past the node: the vertices in
        # the old node's approach window were the junction's chamfer, which is
        # gone, and its first line now starts at T
        o_new = np.vstack([t[None, :], moved, through[None, :], o_pts[first:]])
        kept = first
        segs = [Line(t.copy(), head.p1.copy()), *o_segs[1:]]
    else:
        # the outside arc leaves the side at the node: it is the neighbour's
        # own rounded corner, and it meets this one at T, the pixels between
        # the two being the third region's. It starts at T along the side; the
        # vertices between T and the old node lay on the side, which is no
        # longer an edge there, and are dropped, and so is the old node
        o_new = np.vstack([t[None, :], o_pts[1:]])
        kept = 1
        end = head.p1
        nxt = o_segs[1] if len(o_segs) > 1 else None
        segs = _node_fillet(t, direction, o_segs, m.r[corner], o_pts[first:], params.tol)
        if segs is None:
            reach = int(np.argmin(np.linalg.norm(o_pts - end, axis=1)))
            inner = o_pts[first:reach] if reach > first else o_pts[1:reach]
            piece = np.vstack([t[None, :], inner, end[None, :]])
            if isinstance(nxt, Cubic) and float(np.linalg.norm(nxt.c1 - nxt.p0)) > 1e-9:
                d_next = _normalize(nxt.c1 - nxt.p0)
            elif isinstance(nxt, Line) and float(np.linalg.norm(nxt.p1 - nxt.p0)) > 1e-9:
                d_next = _normalize(nxt.p1 - nxt.p0)
            else:
                d_next = _normalize(end - piece[-2])
            segs = [*fit_cubics(piece, direction, -d_next, params.tol), *o_segs[1:]]
    O.pts = o_new if o_end == 0 else o_new[::-1].copy()
    # the vertices spliced in along the side take the side's normal, turned
    # the way the outside arc's own placed normals point; the rest keep theirs
    # (a normal re-read from the tangent is wrong at a wedge tip, and the bled
    # copy made from it tore a pinhole there)
    o_nrm = None if O.normal is None else (O.normal if o_end == 0 else O.normal[::-1])
    side_n = np.zeros(2)
    side_n[_SIDE_AXIS[side]] = 1.0
    if o_nrm is not None and len(o_nrm) == len(o_pts):
        vote = float(np.sum(o_nrm[kept:kept + 4] @ side_n))
        head_n = np.tile(side_n if vote >= 0.0 else -side_n, (len(o_new) - (len(o_pts) - kept), 1))
        n_new = np.vstack([head_n, o_nrm[kept:]])
        O.normal = n_new if o_end == 0 else n_new[::-1].copy()
    else:
        O.normal = _placed_normals(O)
    O.sliver = None
    if o_end == 0:
        O.t0, O.trim0 = direction, NODE_TRIM
    else:
        O.t1, O.trim1 = direction, NODE_TRIM
    c_end = 0 if C.pts[0][0] == node[0] and C.pts[0][1] == node[1] else -1
    C.pts = C.pts.copy()
    C.pts[c_end] = t
    O.segments = segs if o_end == 0 else reverse_segments(segs)
    return t


def _node_fillet(t: np.ndarray, direction: np.ndarray, o_segs: list[Segment], r: float, pts: np.ndarray, tol: float) -> list[Segment] | None:
    """The outside arc's corner at a cusp node as a designer draws it: leaving
    T along the side, a quarter circle of the shape's radius r into the first
    line of the arc that turns from the side as a corner does (`o_segs`,
    walking away from the node; what comes before it, at most two pieces, was
    the corner as fitted), so the two rounded corners that meet at T mirror
    each other. The radius is held to the room between T and the lines'
    crossing (a tangent point past T would be past the node), within NODE_ON;
    the placed vertices (`pts`, the arc's own, outside the node's window) must
    hold it as a snapped rectangle is held. Returns the arc's segments from T,
    or None when no such line follows or the corner does not hold."""
    if r <= 0.0:
        return None
    for j, nxt in enumerate(o_segs[:3]):
        if not isinstance(nxt, Line):
            continue
        lb = float(np.linalg.norm(nxt.p1 - nxt.p0))
        if lb < 1e-9:
            continue
        db = (nxt.p1 - nxt.p0) / lb
        turn = math.degrees(math.acos(max(-1.0, min(1.0, float(direction @ db)))))
        if not FILLET_TURN[0] <= turn <= FILLET_TURN[1]:
            continue
        x = _intersect(t, direction, nxt.p0, db)
        if x is None:
            return None
        half, _bis = rects.fillet_frame(direction, db)
        ra, rb = float((x - t) @ direction), float((nxt.p1 - x) @ db)
        r_max = ra * math.tan(half)
        if ra <= 0.0 or r - r_max > rects.NODE_ON:
            return None
        r = min(r, r_max)
        reach = r / math.tan(half)
        if rb - reach < FILLET_LINE_KEEP:
            return None
        near = pts[np.linalg.norm(pts - x, axis=1) <= reach + 1.0]
        if len(near) < 3 or not _fillet_holds(near, x, direction, db, r, FINAL_SLACK * tol, 2.0 * tol):
            return None
        t1, t2, sweep = rects.fillet_points(x, direction, db, r)
        out: list[Segment] = []
        if float((t1 - t) @ direction) > 1e-6:
            out.append(Line(t.copy(), t1.copy()))
        else:
            t1 = t.copy()
        out.append(CircArc(t1.copy(), t2.copy(), float(r), False, sweep))
        out.append(Line(t2.copy(), nxt.p1.copy()))
        return out + list(o_segs[j + 1:])
    return None


def _guides(bnd: Boundary, used: set[int], snap_axis_deg: float) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Axis-aligned lines elsewhere in the graph, as (level, length) for x and y."""
    gx: list[tuple[float, float]] = []
    gy: list[tuple[float, float]] = []
    for idx, arc in enumerate(bnd.arcs):
        if idx in used or 0 in arc.pair:
            continue
        for seg in arc.segments:
            if not isinstance(seg, Line):
                continue
            d = seg.p1 - seg.p0
            length = float(np.linalg.norm(d))
            if length < GUIDE_MIN:
                continue
            ang = math.degrees(math.atan2(d[1], d[0])) % 180.0
            if min(ang, 180.0 - ang) <= snap_axis_deg:
                gy.append((0.5 * float(seg.p0[1] + seg.p1[1]), length))
            elif abs(ang - 90.0) <= snap_axis_deg:
                gx.append((0.5 * float(seg.p0[0] + seg.p1[0]), length))
    return gx, gy


def _snap_levels(cands: list[_RectCandidate], models: list[rects.Model], guides: list[tuple[float, float]],
                 axis: int, move: float, sized: set[tuple[int, int]]) -> dict[int, tuple[bool, bool]]:
    """Edges on one guide: the side levels on `axis` (0 = x) clustered across
    shapes; a group goes to the graph's nearest long line on that axis when
    one is within `move` of it, else to its length-weighted mean. A side takes
    its group's level only within `move` of it, and a side with nodes on it only where every one of
    them is within NODE_ON (they go onto the side). A shape whose size was
    made one with other shapes' (`sized`) moves whole when one side snaps,
    unless its other side has nodes on it; any other shape moves that side
    alone. With both sides snapped, it takes both. Returns, per shape, which
    of its two sides on this axis snapped."""
    values: list[float] = []
    weights: list[float] = []
    owner: list[tuple[int, int]] = []  # (shape, 0 = low side / 1 = high side)
    for k, m in enumerate(models):
        lo, hi = (m.x0, m.x1) if axis == 0 else (m.y0, m.y1)
        span = m.h if axis == 0 else m.w
        for end, level in enumerate((lo, hi)):
            values.append(level)
            weights.append(span)
            owner.append((k, end))
    levels = sorted(g for g, _length in guides)
    target: dict[tuple[int, int], float] = {}
    for mean, members in rects.cluster_1d(values, weights, move):
        # a guide within reach of the group is where the group goes: it is a
        # long line elsewhere and does not move; the nearest one, since two
        # guides a third of a pixel apart are two lines (a round letter's
        # overshoot below the baseline is not the baseline)
        level = mean if len(members) > 1 else None
        if levels:
            j = int(np.searchsorted(levels, mean))
            near = [levels[i] for i in (j - 1, j) if 0 <= i < len(levels)]
            best = min(near, key=lambda g: (abs(g - mean), g))
            if abs(best - mean) <= move:
                level = best
        if level is None:
            continue
        for i in members:
            if abs(values[i] - level) > move:
                continue
            k, end = owner[i]
            if all(abs(v - level) <= rects.NODE_ON for v in cands[k].pinned[2 * axis + end]):
                target[owner[i]] = level
    snapped: dict[int, tuple[bool, bool]] = {}
    for k, m in enumerate(models):
        lo, hi = (m.x0, m.x1) if axis == 0 else (m.y0, m.y1)
        t_lo, t_hi = target.get((k, 0)), target.get((k, 1))
        snapped[k] = (t_lo is not None, t_hi is not None)
        whole = (k, axis) in sized
        held_lo = bool(cands[k].pinned[2 * axis]) or not whole
        held_hi = bool(cands[k].pinned[2 * axis + 1]) or not whole
        if t_lo is not None and t_hi is not None:
            lo, hi = t_lo, t_hi
        elif t_lo is not None:
            lo, hi = t_lo, (hi if held_hi else hi + (t_lo - lo))
        elif t_hi is not None:
            lo, hi = (lo if held_lo else lo + (t_hi - hi)), t_hi
        if axis == 0:
            m.x0, m.x1 = lo, hi
        else:
            m.y0, m.y1 = lo, hi
    return snapped


def _set_length(m: rects.Model, axis: int, length: float, keep: int | None) -> None:
    """Give the model this length on `axis`, keeping side `keep` (0 low,
    1 high) where it is, or its centre when None."""
    lo, hi = (m.x0, m.x1) if axis == 0 else (m.y0, m.y1)
    if keep == 0:
        hi = lo + length
    elif keep == 1:
        lo = hi - length
    else:
        c = 0.5 * (lo + hi)
        lo, hi = c - 0.5 * length, c + 0.5 * length
    if axis == 0:
        m.x0, m.x1 = lo, hi
    else:
        m.y0, m.y1 = lo, hi


def _regular_models(cands: list[_RectCandidate], guides_x, guides_y, tol: float
                    ) -> tuple[list[rects.Model], list[float], list[rects.Model], list[float]]:
    """The candidates' models made regular together: one radius per shape,
    one radius per group of shapes whose radii agree, one size per group of
    sides whose lengths agree (a square is square), edges on shared guides.
    Every snap is held to MOVE_SHARE of the tolerance. Returns the models and
    each shape's radius (0 where its corners do not agree on one), and the
    same for each shape made regular on its own (one radius round it, its
    own size and place), to fall back on where the snaps together move it
    further than its vertices allow."""
    move = rects.MOVE_SHARE * tol
    r_move = rects.radius_move(tol)
    models = [c.model.copy() for c in cands]
    # one radius round each shape. The radii are the blur-corrected ones
    # (`rects.deblur`); a sharp corner still reads a little, from the lattice's
    # half-pixel chamfer, so a shape whose free corners read no more than
    # SHARP_SHAPE_R on average is sharp. Otherwise every free corner takes the
    # mean of the clearly rounded ones when that one radius holds the placed
    # vertices: one corner's reading moves by ±0.5 px with its phase on the
    # pixel grid, so readings that far apart are still one radius, and the
    # vertices are what says whether they are. Failing that, the rounded
    # corners are one radius where they agree, and a corner that read low
    # takes it too if it is that close, else stays sharp.
    shape_r: list[float] = []
    shape_w: list[float] = []
    for c, m in zip(cands, models):
        at_node = {nc.corner for nc in c.node_corners}
        free = [k for k in range(4) if k not in at_node]
        readings = [m.r[k] for k in free]
        round_ = [k for k in free if m.r[k] > rects.SHARP_SHAPE_R]
        if not free or sum(readings) / len(readings) <= rects.SHARP_SHAPE_R or not round_:
            for k in free:
                m.r[k] = 0.0
            shape_r.append(0.0)
            shape_w.append(0.0)
            continue
        w = sum(max(m.votes[k], 1.0) for k in round_)
        mean = sum(m.r[k] * max(m.votes[k], 1.0) for k in round_) / w
        one = m.copy()
        for k in free:
            one.r[k] = mean
        if rects.holds(rects.apparent(one, c.sigma), c.poly, c.trusted, tol):
            m.r = one.r
            shape_r.append(mean)
            shape_w.append(w)
            continue
        if not all(abs(m.r[k] - mean) <= r_move for k in round_):
            for k in free:
                if m.r[k] <= rects.SHARP_SHAPE_R:
                    m.r[k] = 0.0
            shape_r.append(0.0)
            shape_w.append(0.0)
            continue
        for k in free:
            m.r[k] = mean if (k in round_ or abs(m.r[k] - mean) <= r_move) else 0.0
        shape_r.append(mean)
        shape_w.append(w)
    own = [m.copy() for m in models]
    own_r = list(shape_r)
    # one radius across shapes whose radii agree
    idx = [k for k, r in enumerate(shape_r) if r > 0.0]
    for mean, members in rects.cluster_1d([shape_r[k] for k in idx], [shape_w[k] for k in idx], r_move):
        for i in members:
            k = idx[i]
            m = models[k]
            for c in range(4):
                if m.r[c] > 0.0 and abs(m.r[c] - shape_r[k]) <= 1e-12:
                    m.r[c] = mean
            shape_r[k] = mean
    # one size across sides whose lengths agree, about each shape's centre;
    # a side a node pins does not move, so its shape's size stays too
    sizes: list[float] = []
    owners: list[tuple[int, int]] = []
    for k, (c, m) in enumerate(zip(cands, models)):
        for axis, length in ((0, m.w), (1, m.h)):
            if not c.pinned[2 * axis] and not c.pinned[2 * axis + 1]:
                sizes.append(length)
                owners.append((k, axis))
    sized: set[tuple[int, int]] = set()
    size_groups: list[list[tuple[int, int]]] = []
    for mean, members in rects.cluster_1d(sizes, [1.0] * len(sizes), move):
        if len(members) < 2:
            continue
        size_groups.append([owners[i] for i in members])
        for i in members:
            k, axis = owners[i]
            sized.add((k, axis))
            _set_length(models[k], axis, mean, None)
    snapped = (_snap_levels(cands, models, guides_x, 0, move, sized),
               _snap_levels(cands, models, guides_y, 1, move, sized))
    # a length whose two sides both went onto guides is what the guides say;
    # the rest of its size group follows it, from the side of theirs that
    # snapped (a square whose top and bottom are its neighbours' stays square)
    for group in size_groups:
        fixed = [models[k].w if axis == 0 else models[k].h for k, axis in group if all(snapped[axis][k])]
        if not fixed:
            continue
        length = sum(fixed) / len(fixed)
        for k, axis in group:
            lo_s, hi_s = snapped[axis][k]
            current = models[k].w if axis == 0 else models[k].h
            if lo_s and hi_s or abs(current - length) > move:
                continue
            _set_length(models[k], axis, length, 0 if lo_s else (1 if hi_s else None))
    for k, m in enumerate(models):
        cap = 0.5 * min(m.w, m.h)
        m.r = [min(r, cap) for r in m.r]
        shape_r[k] = min(shape_r[k], cap)
    return models, shape_r, own, own_r


def _write_rect(bnd: Boundary, cand: _RectCandidate, m: rects.Model) -> None:
    """Write the model into the ring's arcs. A ring that is one closed arc
    takes the whole outline and, when its four radii are one, the primitive;
    otherwise each arc takes the stretch of outline between its two nodes,
    which stay exactly where they are (they were moved onto the outline)."""
    if len(cand.ring) == 1 and bnd.arcs[cand.ring[0][0]].closed:
        arc = bnd.arcs[cand.ring[0][0]]
        segs = rects.subpath(m, 0.0, 0.0, whole=True)
        x, y = arc.pts[:, 0], arc.pts[:, 1]
        clockwise = float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) > 0.0
        arc.segments = segs if clockwise else reverse_segments(segs)
        arc.mirror = None
        if max(m.r) - min(m.r) <= 1e-9:
            arc.rect = (RoundedRect(m.x0, m.y0, m.w, m.h, m.r[0]) if m.r[0] > 0.0
                        else Rect(m.x0, m.y0, m.w, m.h))
        _resample(arc)
        return
    for idx, rev in cand.ring:
        arc = bnd.arcs[idx]
        start, end = (arc.pts[-1], arc.pts[0]) if rev else (arc.pts[0], arc.pts[-1])
        s0, s1 = rects.project(m, start)[0], rects.project(m, end)[0]
        segs = rects.subpath(m, s0, s1) if cand.clockwise else reverse_segments(rects.subpath(m, s1, s0))
        if not segs:
            continue
        segs[0].p0 = start.copy()
        segs[-1].p1 = end.copy()
        arc.segments = reverse_segments(segs) if rev else segs
        _resample(arc)


RESAMPLE_STEP = 1.0  # px between the vertices put back on a written arc


def _resample(arc: Arc) -> None:
    """The arc's vertices and normals, taken again from the curve it was
    written with. The bled copy under a shape is made from them (`_bled`), and
    the placed vertices it was made from carry each pixel's own noise: a
    one-pixel notch in the label map is three vertices whose placed normals
    lie along the edge, and a copy bled along those went the wrong way. The
    normals here are the curve's, turned to the side the placed normals point
    to by one vote over the whole arc."""
    pts: list[np.ndarray] = []
    for seg in arc.segments:
        if isinstance(seg, CircArc):
            dense = arc_points(seg, 33)
            length = float(np.sum(np.linalg.norm(np.diff(dense, axis=0), axis=1)))
            k = max(2, int(math.ceil(length / RESAMPLE_STEP)) + 1)
            q = arc_points(seg, k)
        else:
            length = float(np.linalg.norm(seg.p1 - seg.p0))
            k = max(2, int(math.ceil(length / RESAMPLE_STEP)) + 1)
            t = np.linspace(0.0, 1.0, k)[:, None]
            q = seg.p0 + (seg.p1 - seg.p0) * t
        pts.extend(q if not pts else q[1:])
    new = np.array(pts)
    if len(new) < 2:
        return
    if arc.closed and float(np.linalg.norm(new[-1] - new[0])) < 1e-9:
        new = new[:-1]
    ahead = np.roll(new, -1, axis=0) if arc.closed else np.vstack([new[1:], new[-1:]])
    behind = np.roll(new, 1, axis=0) if arc.closed else np.vstack([new[:1], new[:-1]])
    t = ahead - behind
    t = t / np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    normal = np.column_stack([t[:, 1], -t[:, 0]])
    if arc.normal is not None and len(arc.normal) == len(arc.pts):
        near = np.argmin(np.linalg.norm(arc.pts[:, None, :] - new[None, :, :], axis=2), axis=1)
        if float(np.sum(arc.normal * normal[near])) < 0.0:
            normal = -normal
    if not arc.closed:
        new[0], new[-1] = arc.pts[0], arc.pts[-1]
    arc.pts = new
    arc.normal = normal
    arc.sliver = None


def _rectify(bnd: Boundary, params: CurveParams, rgb: np.ndarray, log: list | None = None) -> tuple[set[int], list[float]]:
    """Every ring that is an axis-aligned rounded rectangle, made regular and
    written back into its arcs. Returns the arcs of every ring that reads as
    one, written or not, and the radii the rounded shapes took, for `_fillets`.

    The rings are each label's, deduplicated (a square's ring is also a hole in
    its backdrop). A ring on the canvas frame is left alone. Two candidates
    that share an arc cannot both be written: a rounded one is taken before a
    sharp one (a bar a rounded square sits against is only lines already, and
    the square's corners at the bar need the bar's arcs), and otherwise the
    first found.

    `log`, when given, receives one row of numbers per candidate and per
    settled shape, for `tools/diffcheck.py`'s `rects` stage
    (`topology/rectify.rs` writes the same rows)."""
    h, w = bnd.padded.shape[0] - 2, bnd.padded.shape[1] - 2
    seen: set[frozenset[int]] = set()
    found: list[_RectCandidate] = []
    for lab in np.unique(bnd.padded):
        if lab == 0:
            continue
        for ring in bnd.rings(frozenset([int(lab)])):
            key = frozenset(i for i, _ in ring)
            if key in seen:
                continue
            seen.add(key)
            if any(0 in bnd.arcs[i].pair for i, _ in ring):
                continue
            poly, trusted = _ring_vertices(bnd, ring)
            if (poly[:, 0] <= 0.0).any() or (poly[:, 1] <= 0.0).any() or (poly[:, 0] >= w).any() or (poly[:, 1] >= h).any():
                continue
            m = rects.fit_sides(poly, params.snap_axis_deg)
            if m is None:
                continue
            rects.fit_radii(m, poly, trusted)
            placed = _nodes(ring, bnd.arcs, m)
            if placed is None:
                continue
            pinned, node_corners = placed
            # a corner with a node in it does not vote: its vertices are the
            # node's approach, and the node sits where the pixels ran out
            for nc in node_corners:
                m.votes[nc.corner] = 0.0
            if not rects.holds(m, poly, trusted, params.tol):
                continue
            # the radii as drawn: the blur read across the shape's own sides
            # taken out of what the placement read (`rects.deblur`)
            read = rects.edge_sigma(rgb, m)
            sigma = read[0] if read is not None else 0.0
            raw = list(m.r)
            m.r = [rects.deblur(r, sigma) if r > 0.0 else 0.0 for r in m.r]
            x, y = poly[:, 0], poly[:, 1]
            clockwise = float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) > 0.0
            if log is not None:
                log.append([1.0, float(len(ring)), *[float(v) for i, rev in ring for v in (i, rev)],
                            m.x0, m.y0, m.x1, m.y1, *raw, *m.votes, sigma, float(read[1]) if read is not None else -1.0,
                            *m.r, float(clockwise), *[float(len(p)) for p in pinned], float(len(node_corners)),
                            *[float(v) for nc in node_corners for v in (nc.corner, nc.before, nc.after, len(nc.sides), *nc.sides)]])
            found.append(_RectCandidate(ring, poly, trusted, m, clockwise, pinned, node_corners, sigma))
    rounded = [any(c.model.r[k] > rects.SHARP_SHAPE_R and c.model.votes[k] > 0 for k in range(4)) for c in found]
    cands: list[_RectCandidate] = []
    claimed: dict[int, int] = {}
    for k in sorted(range(len(found)), key=lambda k: (not rounded[k], k)):
        c = found[k]
        if any(i in claimed for i, _ in c.ring):
            continue
        for i, _ in c.ring:
            claimed[i] = len(cands)
        cands.append(c)
    if not cands:
        return set(), []
    gx, gy = _guides(bnd, set(claimed), params.snap_axis_deg)
    models, shape_r, own, own_r = _regular_models(cands, gx, gy, params.tol)
    radii: set[float] = set()
    for k, cand in enumerate(cands):
        # the model made regular with the others, else on its own
        settled, which = _settle_corners(bnd.arcs, cand, models[k], shape_r[k], claimed, params), 1.0
        if settled is None:
            settled, which = _settle_corners(bnd.arcs, cand, own[k], own_r[k], claimed, params), 2.0
        if settled is None:
            if log is not None:
                log.append([2.0, float(k), 0.0, shape_r[k], own_r[k]])
            continue
        m, plans = settled
        if log is not None:
            log.append([2.0, float(k), which, shape_r[k], own_r[k], m.x0, m.y0, m.x1, m.y1, *m.r, float(len(plans)),
                        *[float(v) for nc, plan in plans
                          for v in (nc.corner, plan[0], plan[1], plan[2], plan[3][0], plan[3][1] == -1, plan[4])]])
        moved = set()
        for nc, plan in plans:
            t = _apply_cusp(bnd.arcs, m, nc.corner, plan, nc.point, params)
            moved.add((float(t[0]), float(t[1])))
        # every other node of the ring goes onto the outline (it is within
        # NODE_ON of it), and the arcs outside the ring that end there with it
        ring_arcs = {i for i, _ in cand.ring}
        for idx, rev in cand.ring:
            p = bnd.arcs[idx].pts[0] if rev else bnd.arcs[idx].pts[-1]
            if (float(p[0]), float(p[1])) in moved:
                continue
            q = rects.point_at(m, rects.project(m, p)[0])
            _move_node(bnd.arcs, p.copy(), q, ring_arcs)
        _write_rect(bnd, cand, m)
        radii.update(r for r in m.r if r > 0.0)
    # every ring that reads as a rectangle is this stage's, written or not:
    # `_fillets` rounding some of its corners and not others is the
    # inconsistency this stage exists to remove
    return {i for c in found for i, _ in c.ring}, sorted(radii)


def _settle_corners(arcs: list[Arc], cand: _RectCandidate, m: rects.Model, shape_r: float,
                    claimed: dict[int, int], params: CurveParams) -> tuple[rects.Model, list] | None:
    """The corners of a regular model that have a node in them, decided, and
    the model checked against the ring's vertices; None when a corner cannot
    be decided or the model does not hold them. A corner with a node in it
    takes the shape's radius, or where the shape's corners did not agree on
    one, their mean. Returns the model, with those corners' radii, and the
    cusps to apply."""
    m = m.copy()
    free = [(m.r[c], m.votes[c]) for c in range(4) if m.votes[c] > 0 and m.r[c] > 0.0]
    r_node = shape_r if shape_r > 0.0 else (
        sum(r * v for r, v in free) / sum(v for _r, v in free) if free else 0.0)
    r_node = min(r_node, 0.5 * min(m.w, m.h))
    plans = []
    for nc in cand.node_corners:
        m.r[nc.corner] = r_node
        if r_node > 0.0 and rects.project(rects.apparent(m, cand.sigma), nc.point)[1] <= rects.NODE_ON:
            # the node is on the rounded corner itself (an edge that crosses
            # the corner there): it goes onto the outline as any other node
            # does, and the corner keeps the shape's radius
            continue
        kind, plan = _decide_corner(arcs, cand, nc, m, r_node, params.tol)
        if kind == "cusp" and plan[3][0] in claimed:
            kind = "unresolved"  # the outside arc is another rectangle's
        if kind == "unresolved":
            return None
        if kind == "sharp":
            m.r[nc.corner] = 0.0
        else:
            plans.append((nc, plan))
    # the snaps are bounded one by one; the sum is checked here, against the
    # model as the placement would read it (`rects.apparent`), since the
    # placed vertices are the blurred corner's
    if not rects.holds(rects.apparent(m, cand.sigma), cand.poly, cand.trusted, FINAL_SLACK * params.tol, 2.0 * params.tol):
        return None
    return m, plans

FILLET_SPAN = 12.0       # px; a curve between two lines longer than this is a curve, not a rounded corner
FILLET_TURN = (30.0, 150.0)  # degrees two lines must turn by to make a corner worth rounding
FILLET_LINE_KEEP = 1.0   # px of each line that must be left once the fillet has taken its share


def _fillets(bnd: Boundary, params: CurveParams, skip: set[int], anchors: list[float], log: list | None = None) -> int:
    """Rounded corners between two lines, anywhere in the graph, as a designer
    draws them: a circular arc tangent to both lines, of a radius shared with
    the other rounded corners of the mark where they agree.

    The fit leaves such a corner as a cubic between two lines, a little
    squarer or a little rounder each time: the placement gives it four to six
    vertices. Here each Line-curve-Line inside an arc is asked whether one
    circle tangent to both lines holds the placed vertices between them; its
    radius is read by least squares, pooled with the rectangles' radii
    (`anchors`) and the other fillets', and the corner is rewritten as the two
    lines, shortened to the tangent points, and the arc. Nodes do not move and
    the arc is one shared curve, so both sides get the same corner. Returns the
    number of corners rewritten."""
    tol = params.tol
    r_move = rects.radius_move(tol)
    found: list[tuple[int, int, int, np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, float]] = []
    for idx, arc in enumerate(bnd.arcs):
        if idx in skip or arc.rect is not None or len(arc.segments) < 3:
            continue
        segs = arc.segments
        n = len(segs)
        pts = arc.pts
        keep = np.ones(len(pts), dtype=bool)
        if not arc.closed:
            cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
            keep = ~((cum < arc.trim0) | (cum[-1] - cum < arc.trim1))
        for i in range(n - 2):
            a = segs[i]
            if not isinstance(a, Line):
                continue
            for gap in (1, 2):
                j = i + gap + 1
                if j >= n:
                    break
                b = segs[j]
                mids = segs[i + 1:j]
                if not isinstance(b, Line) or any(isinstance(s, Line) for s in mids):
                    continue
                la, lb = float(np.linalg.norm(a.p1 - a.p0)), float(np.linalg.norm(b.p1 - b.p0))
                if la < 1e-6 or lb < 1e-6 or float(np.linalg.norm(b.p0 - a.p1)) > FILLET_SPAN:
                    continue
                da, db = (a.p1 - a.p0) / la, (b.p1 - b.p0) / lb
                turn = math.degrees(math.acos(max(-1.0, min(1.0, float(da @ db)))))
                if not FILLET_TURN[0] <= turn <= FILLET_TURN[1]:
                    continue
                x = _intersect(a.p0, da, b.p0, db)
                if x is None:
                    continue
                half, _bis = rects.fillet_frame(da, db)
                # each line may give a fillet half its length: the other half
                # may be another corner's
                room = 0.5 * min(float((x - a.p0) @ da), float((b.p1 - x) @ db)) - FILLET_LINE_KEEP
                if room <= 0.0:
                    continue
                r_max = room * math.tan(half)
                # the placed vertices of this stretch of the arc, from the
                # first line's start to the second line's end
                lo = int(np.argmin(np.linalg.norm(pts - a.p0, axis=1)))
                hi = int(np.argmin(np.linalg.norm(pts - b.p1, axis=1)))
                if hi > lo:
                    span = np.arange(lo, hi + 1)
                elif arc.closed:
                    span = np.concatenate([np.arange(lo, len(pts)), np.arange(0, hi + 1)])
                else:
                    continue
                span = span[keep[span]]
                near = pts[span][np.linalg.norm(pts[span] - x, axis=1) <= room + 1.0]
                if len(near) < 3:
                    continue
                r = rects.fit_fillet(near, x, da, db, r_max)
                if r <= rects.CHAMFER_R:
                    continue
                if not _fillet_holds(near, x, da, db, r, tol, tol):
                    continue
                found.append((idx, i, j, x, da, db, near, r, r_max))
                break
    if not found:
        return 0
    # one radius where they agree: a rectangle's radius first, else the group's
    radii = [f[7] for f in found]
    target = list(radii)
    for mean, members in rects.cluster_1d(radii, [1.0] * len(radii), r_move):
        near_anchor = min(anchors, key=lambda v: (abs(v - mean), v)) if anchors else None
        value = near_anchor if near_anchor is not None and abs(near_anchor - mean) <= r_move else mean
        for k in members:
            if abs(radii[k] - value) <= r_move:
                target[k] = value
    changed = 0
    # rewrite from the back of each arc's list, so the indices still hold
    for (idx, i, j, x, da, db, near, r, r_max), want in sorted(zip(found, target), key=lambda t: (t[0][0], -t[0][1])):
        if want != r and (want > r_max or not _fillet_holds(near, x, da, db, want, FINAL_SLACK * tol, 2.0 * tol)):
            want = r
        if log is not None:
            log.append([3.0, float(idx), float(i), float(j), r, r_max, want])
        segs = bnd.arcs[idx].segments
        t1, t2, sweep = rects.fillet_points(x, da, db, want)
        a, b = segs[i], segs[j]
        a.p1 = t1.copy()
        b.p0 = t2.copy()
        segs[i + 1:j] = [CircArc(t1.copy(), t2.copy(), float(want), False, sweep)]
        _resample_corner(bnd.arcs[idx], x, da, db, t1, t2, segs[i + 1])
        changed += 1
    return changed


def _resample_corner(arc: Arc, x: np.ndarray, da: np.ndarray, db: np.ndarray, t1: np.ndarray, t2: np.ndarray, fillet: CircArc) -> None:
    """The placed vertices round a rewritten corner, taken again from the
    lines and the arc, with the curve's normals turned the way the placed ones
    pointed (one vote over the corner): the bled copy is made from them, and a
    lattice step in a corner is two vertices a hundredth of a pixel apart whose
    placed normals lie along the edge, which bled the copy the wrong way and
    left a pinhole. Only the corner's own vertices are touched."""
    pts = arc.pts
    n = len(pts)
    reach = float(np.linalg.norm(t1 - x)) + 1.5
    mid = arc_points(fillet, 3)[1]
    k0 = int(np.argmin(np.linalg.norm(pts - mid, axis=1)))
    lo, hi = k0, k0
    first, last = (0, n - 1) if arc.closed else (1, n - 2)
    while lo - 1 >= first and float(np.linalg.norm(pts[lo - 1] - x)) <= reach and hi - lo < n - 2:
        lo -= 1
    while hi + 1 <= last and float(np.linalg.norm(pts[hi + 1] - x)) <= reach and hi - lo < n - 2:
        hi += 1
    if hi - lo < 2 or arc.normal is None or len(arc.normal) != n:
        return
    start = x + da * float((pts[lo] - x) @ da)
    end = x + db * float((pts[hi] - x) @ db)
    parts = []
    for p, q in ((start, t1), (t2, end)):
        k = max(2, int(math.ceil(float(np.linalg.norm(q - p)))) + 1)
        parts.append(p + (q - p) * np.linspace(0.0, 1.0, k)[:, None])
    dense = arc_points(fillet, 33)
    k = max(2, int(math.ceil(float(np.sum(np.linalg.norm(np.diff(dense, axis=0), axis=1))))) + 1)
    new = np.vstack([parts[0][:-1], arc_points(fillet, k)[:-1], parts[1]])
    ahead = np.vstack([new[1:], new[-1:]])
    behind = np.vstack([new[:1], new[:-1]])
    t = ahead - behind
    t = t / np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    normal = np.column_stack([t[:, 1], -t[:, 0]])
    old = arc.normal[lo:hi + 1]
    near = np.argmin(np.linalg.norm(pts[lo:hi + 1, None, :] - new[None, :, :], axis=2), axis=1)
    if float(np.sum(old * normal[near])) < 0.0:
        normal = -normal
    arc.pts = np.vstack([pts[:lo], new, pts[hi + 1:]])
    arc.normal = np.vstack([arc.normal[:lo], normal, arc.normal[hi + 1:]])
    if arc.sliver is not None:
        arc.sliver = np.concatenate([arc.sliver[:lo], np.zeros(len(new), dtype=bool), arc.sliver[hi + 1:]])


def _fillet_holds(pts: np.ndarray, x: np.ndarray, da: np.ndarray, db: np.ndarray, r: float, p95: float, worst: float) -> bool:
    d = rects.fillet_dist(pts, x, da, db, r)
    return float(np.percentile(d, 95)) <= p95 and float(d.max()) <= worst


def _move_node(arcs: list[Arc], p: np.ndarray, q: np.ndarray, keep: set[int]) -> None:
    """Move the node at p to q, a fraction of a pixel: every arc there ends at
    q, and the fitted segments of the arcs not in `keep` (which are about to
    be written) move their end with it, a cubic its arm too, so the tangent
    it was fitted with is kept."""
    if float(np.linalg.norm(q - p)) <= 1e-12:
        return
    delta = q - p
    for idx, end in _arcs_at(arcs, p):
        arc = arcs[idx]
        arc.pts = arc.pts.copy()
        arc.pts[end] = q
        if idx in keep or not arc.segments:
            continue
        seg = arc.segments[0] if end == 0 else arc.segments[-1]
        if end == 0:
            seg.p0 = q.copy()
            if isinstance(seg, Cubic):
                seg.c1 = seg.c1 + delta
        else:
            seg.p1 = q.copy()
            if isinstance(seg, Cubic):
                seg.c2 = seg.c2 + delta


def _side(arc: Arc) -> float:
    """+1 when pair[1] lies on the left of the arc as its vertices run, else -1.

    One sign for the whole arc, voted by every vertex. An arc parts the same two
    regions all the way along, with the same one on its left, so the side never
    changes. Asked vertex by vertex, the vote fails at a stair step: its lattice
    step is square to the curve, the dot product is a rounding error, and its
    sign pushed that one vertex a pixel *out* of the later shape — a hairpin in
    the earlier shape's outline, and a pinhole where neither shape paints.
    """
    pts = arc.pts
    ahead = np.roll(pts, -1, axis=0) if arc.closed else np.vstack([pts[1:], pts[-1:]])
    behind = np.roll(pts, 1, axis=0) if arc.closed else np.vstack([pts[:1], pts[:-1]])
    tangent = ahead - behind
    left = np.column_stack([-tangent[:, 1], tangent[:, 0]])
    return -1.0 if float(np.sum(left * arc.normal)) < 0.0 else 1.0


def _sample(segments: list[Segment], step: float = UNDER_STEP) -> tuple[np.ndarray, np.ndarray]:
    """Points along a fitted curve no more than `step` apart, with the unit
    tangent at each. Every segment is sampled end to end, so a join is sampled
    once from each side and a corner carries both of its tangents."""
    points: list[np.ndarray] = []
    tangents: list[np.ndarray] = []
    for seg in segments:
        if isinstance(seg, Line):
            d = seg.p1 - seg.p0
            m = max(1, math.ceil(float(np.linalg.norm(d)) / step))
            t = np.linspace(0.0, 1.0, m + 1)
            pts = seg.p0 + t[:, None] * d
            tan = np.repeat(d[None, :], m + 1, axis=0)
        elif isinstance(seg, Cubic):
            length = 0.5 * (float(np.linalg.norm(seg.p1 - seg.p0)) + float(
                np.linalg.norm(seg.c1 - seg.p0) + np.linalg.norm(seg.c2 - seg.c1) + np.linalg.norm(seg.p1 - seg.c2)))
            m = max(2, math.ceil(length / step))
            t = np.linspace(0.0, 1.0, m + 1)
            pts = _bezier(seg, t)
            tan = _bezier_d1(seg, t)
        else:
            c = arc_centre(seg)
            r = max(float(np.linalg.norm(seg.p0 - c)), 1e-9)
            a0 = math.atan2(seg.p0[1] - c[1], seg.p0[0] - c[0])
            a1 = math.atan2(seg.p1[1] - c[1], seg.p1[0] - c[0])
            span = (a1 - a0) % (2.0 * math.pi) if seg.sweep else -((a0 - a1) % (2.0 * math.pi))
            m = max(2, math.ceil(abs(span) * r / step))
            a = a0 + span * np.linspace(0.0, 1.0, m + 1)
            pts = np.column_stack([c[0] + r * np.cos(a), c[1] + r * np.sin(a)])
            pts[0], pts[-1] = seg.p0, seg.p1
            tan = np.column_stack([-np.sin(a), np.cos(a)]) * (1.0 if span >= 0.0 else -1.0)
        norm = np.linalg.norm(tan, axis=1)
        flat = norm < 1e-9
        if flat.any():  # a control point on its end: the chord to the next sample
            chord = np.vstack([pts[1:] - pts[:-1], pts[-1:] - pts[-2:-1]])
            tan[flat] = chord[flat]
            norm = np.linalg.norm(tan, axis=1)
        points.append(pts)
        tangents.append(tan / np.maximum(norm, 1e-12)[:, None])
    if not points:
        return np.zeros((0, 2)), np.zeros((0, 2))
    return np.vstack(points), np.vstack(tangents)


def _clearance(q: np.ndarray, poly: np.ndarray, within: float = math.inf) -> np.ndarray:
    """Distance from each point of `q` to the polyline `poly`; a distance over
    `within` may be reported as infinity (only nearer segments are searched)."""
    out = np.full(len(q), np.inf)
    if len(poly) < 2 or len(q) == 0:
        if len(poly) == 1:
            out = np.linalg.norm(q - poly[0], axis=1)
        return out
    a, b = poly[:-1], poly[1:]
    ab = b - a
    den = np.maximum(np.sum(ab * ab, axis=1), 1e-18)
    seg_lo, seg_hi = np.minimum(a, b), np.maximum(a, b)
    for lo in range(0, len(q), 256):
        qq = q[lo:lo + 256]
        if math.isfinite(within):
            box_lo, box_hi = qq.min(axis=0) - within, qq.max(axis=0) + within
            sel = np.nonzero(np.all(seg_hi >= box_lo, axis=1) & np.all(seg_lo <= box_hi, axis=1))[0]
            if len(sel) == 0:
                continue
        else:
            sel = slice(None)
        sa, sab, sden = a[sel], ab[sel], den[sel]
        t = np.clip(np.einsum("qmk,mk->qm", qq[:, None, :] - sa[None, :, :], sab) / sden, 0.0, 1.0)
        near = sa[None, :, :] + t[..., None] * sab[None, :, :]
        out[lo:lo + 256] = np.sqrt(np.min(np.sum((qq[:, None, :] - near) ** 2, axis=2), axis=1))
    return out


def _ray_gap(pts: np.ndarray, normal: np.ndarray, walls: list[np.ndarray], far: float) -> np.ndarray:
    """How far each ray pts[i] + t·normal[i] (0 <= t <= far) runs before it meets
    one of the polylines in `walls`; infinity where it meets none."""
    gap = np.full(len(pts), np.inf)
    lo, hi = pts.min(axis=0) - far, pts.max(axis=0) + far
    for wall in walls:
        if len(wall) < 2 or (wall.max(axis=0) < lo).any() or (wall.min(axis=0) > hi).any():
            continue
        a, e = wall[:-1], wall[1:] - wall[:-1]
        ap = a[None, :, :] - pts[:, None, :]
        den = normal[:, None, 0] * e[None, :, 1] - normal[:, None, 1] * e[None, :, 0]
        safe = np.where(np.abs(den) > 1e-12, den, 1.0)
        t = (ap[..., 0] * e[None, :, 1] - ap[..., 1] * e[None, :, 0]) / safe
        u = (ap[..., 0] * normal[:, None, 1] - ap[..., 1] * normal[:, None, 0]) / safe
        hit = (np.abs(den) > 1e-12) & (u >= 0.0) & (u <= 1.0) & (t >= 0.0) & (t <= far)
        gap = np.minimum(gap, np.where(hit, t, np.inf).min(axis=1))
    return gap


def _under(arc: Arc, amount: float, params: CurveParams, walls: list[np.ndarray] | None = None) -> list[Segment]:
    """The arc's visible curve pushed `amount` towards one side (towards pair[1]
    when positive), for the side painted earlier to use.

    It is an offset of the curve that is actually drawn, not of the placed
    vertices: the fit is free to leave those by its tolerance, and further
    inside a node's approach window, where they are not believed at all. A copy
    bled from the vertices crossed back over the drawn edge wherever the fit had
    left them by more than the bleed (at a wedge tip, along a loosely fitted
    curve), and neither shape painted what lay between.

    The bleed is hidden only while it stays inside the later shape, so it never
    reaches more than halfway to that shape's far side (`walls`, its other
    arcs, met along the normal): towards a wedge's tip it eases to nothing
    instead of poking out through the other side of the wedge. Samples of the
    offset that come back within UNDER_CLEAR of their own bleed of the drawn
    curve are dropped, which trims the loop an inside corner puts in an offset.
    The rest is fitted loosely and checked: a fit that strays more than UNDER_DEV
    of the bleed from the offset (a corner extrapolated to where two lines
    cross, which lands back on a rounded one) is fitted again as curves only,
    then tighter, and in the end the samples themselves are used. An open arc's
    copy is pinned to its two nodes by a jog at each end, so the ring closes.
    """
    if not arc.segments:
        return [], (False, False)
    bleed = abs(amount)
    # Densely, so that the offset polyline is the offset curve to well inside
    # what the check below allows; every UNDER_SUB-th sample is what is fitted.
    pts, tangent = _sample(arc.segments, UNDER_STEP / UNDER_SUB)
    normal = (math.copysign(1.0, amount) * _side(arc)) * np.column_stack([-tangent[:, 1], tangent[:, 0]])
    reach = np.full(len(pts), bleed)
    if walls:
        reach = np.minimum(reach, 0.5 * _ray_gap(pts, normal, walls, 2.0 * bleed))
        # A far side met *behind* the edge: the shape's two edges have crossed
        # there (a sliver fitted thinner than nothing), and it has no inside to
        # reach into.
        reach[_ray_gap(pts, -normal, walls, bleed) < bleed] = 0.0
    moved = pts + reach[:, None] * normal
    moved = moved[_clearance(moved, pts, bleed) >= UNDER_CLEAR * reach - 1e-9]
    if len(moved) < 2 or (arc.closed and len(moved) < 4):
        return list(arc.segments), (False, False)
    dense = np.vstack([moved, moved[:1]]) if arc.closed else moved
    # Every UNDER_SUB-th sample, and every one where the offset turns: a bleed
    # easing off towards a tip, or a corner, is a feature finer than the step.
    step = np.diff(dense, axis=0)
    length = np.maximum(np.linalg.norm(step, axis=1), 1e-12)
    cos_turn = np.sum(step[1:] * step[:-1], axis=1) / (length[1:] * length[:-1])
    turning = np.nonzero(cos_turn < math.cos(math.radians(UNDER_TURN)))[0] + 1
    pick = np.unique(np.concatenate([np.arange(0, len(dense), UNDER_SUB), turning, [len(dense) - 1]]))
    run = dense[pick]
    fitted: list[Segment] | None = None
    # Lines first, as the visible curve was fitted; then curves only, whose
    # corners are not extrapolated to where two lines cross; then tighter.
    for k in range(UNDER_TRIES + 1):
        segs = fit_stretch(run, params.tol, kind_tol=params.kind_tol) if k == 0 else fit_open(run, params.tol * 0.5 ** (k - 1))
        probe, _t = _sample(segs, UNDER_STEP / UNDER_SUB)
        if len(probe) and float(_clearance(probe, dense, 2.0 * bleed).max()) <= UNDER_DEV * bleed:
            fitted = segs
            break
    if fitted is None:
        fitted = [Line(run[k].copy(), run[k + 1].copy()) for k in range(len(run) - 1)
                  if float(np.linalg.norm(run[k + 1] - run[k])) > 1e-9]
    if arc.closed:
        return fitted, (False, False)
    head, tail = arc.segments[0].p0.copy(), arc.segments[-1].p1.copy()
    jog = (float(np.linalg.norm(moved[0] - head)) > 1e-9, float(np.linalg.norm(tail - moved[-1])) > 1e-9)
    out: list[Segment] = []
    if jog[0]:
        out.append(Line(head, moved[0].copy()))
    out.extend(fitted)
    if jog[1]:
        out.append(Line(moved[-1].copy(), tail))
    return out, jog


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


def _sliver_at(arc: Arc, k: int, reach: int = 3) -> bool:
    """Does this end of the arc run along a handed-back sliver?"""
    if arc.sliver is None:
        return False
    s = arc.sliver[:reach] if k == 0 else arc.sliver[-reach:]
    return bool(s.any())


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
    # A corner inside a node's approach window is believed no more than the
    # other vertices there. Where the node was moved back up its approach (a
    # wedge tip whose sides ran on past the crossing of their approach lines)
    # the chain overshoots the node and doubles back, and that fold read as a
    # corner kept the overshoot as a break: the arc hooked past the node and
    # back, crossed its neighbour, and left a patch no shape painted.
    corners = [k for k in _open_corners(pts, params.corner_threshold)
               if (float(np.linalg.norm(pts[k] - pts[0])) >= arc.trim0
               and float(np.linalg.norm(pts[k] - pts[-1])) >= arc.trim1)]
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
        segments.extend(fit_stretch(piece, params.tol, t_start, t_end, params.kind_tol))
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
        segments.extend(fit_stretch(piece, params.tol, t_start if lo == 0 else None, t_end if hi == len(half) - 1 else None, params.kind_tol))
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
