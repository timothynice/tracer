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

from dataclasses import dataclass, field, replace

import numpy as np

from studi0trace.engines.vexel.boundary import FillAt
from studi0trace.engines.vexel.curves import (
    CurveParams,
    Line,
    Segment,
    _end_tangent,
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
# How far past the two pixels either side of a label edge the half-coverage
# search may reach, in pixels.
REACH = 0.75


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
    chains: list[dict], padded: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, fill_at: FillAt
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Sub-pixel position for every lattice edge of every arc."""
    pad_rgba = np.concatenate(
        [np.pad(rgb, ((1, 1), (1, 1), (0, 0))), (np.pad(alpha, 1) * 255.0)[..., None]], axis=-1
    )
    out: list[np.ndarray] = []
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
        out.append((c_in + t[:, None] * step, step))
    return out


def _approach(pts: np.ndarray, from_start: bool, reach: float, trim: float) -> tuple[np.ndarray, np.ndarray] | None:
    """Total-least-squares line through an arc's run-up to one end, skipping the
    half-pixel marching-squares chamfer at the end itself."""
    q = pts if from_start else pts[::-1]
    d = np.linalg.norm(q - q[0], axis=1)
    sel = (d >= trim) & (d <= reach)
    if int(sel.sum()) < 2:
        sel = (d > 0) & (d <= 2.0 * reach)
    if int(sel.sum()) < 2:
        return None
    return _line_through(q[sel])



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
        return padded
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
    return out


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
    reach: float = 4.0,
    trim: float = 0.8,
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
    for idx, arc in enumerate(arcs):
        if arc.closed or len(arc.pts) < 2:
            continue
        ends.setdefault(arc.n0, []).append((idx, 0))
        ends.setdefault(arc.n1, []).append((idx, -1))

    moves: list[tuple[list[tuple[int, int]], np.ndarray, dict, dict]] = []
    for incident in ends.values():
        lines = {(i, k): _approach(arcs[i].pts, k == 0, reach, trim) for i, k in incident}
        mean = np.mean([arcs[i].pts[k] for i, k in incident], axis=0)

        target = mean
        usable = [v for v in lines.values() if v is not None]
        if len(usable) >= 2:
            acc = np.zeros((2, 2))
            rhs = np.zeros(2)
            for point, direction in usable:
                normal = np.eye(2) - np.outer(direction, direction)
                acc += normal
                rhs += normal @ point
            # `acc` is a sum of projectors onto unit normals, so its smaller
            # eigenvalue says how well the incident lines actually pin a point
            # down: about one when they cross squarely, and towards zero when
            # they are nearly parallel and meet nowhere in particular.
            #
            # The estimate is faded in across that range rather than switched on
            # at a threshold, and the move it asks for is clamped rather than
            # refused. Every step from the arcs to the node is then continuous in
            # the fills underneath, which matters because the two implementations
            # of those fills agree only to about a colour level: a threshold here
            # let a single junction land two pixels apart between them, and the
            # arcs are shared, so that is two shapes moving.
            half = (acc[0, 0] + acc[1, 1]) / 2.0
            spread = np.hypot((acc[0, 0] - acc[1, 1]) / 2.0, acc[0, 1])
            trust = float(np.clip((half - spread - 0.15) / 0.2, 0.0, 1.0))
            if trust > 0.0 and abs(np.linalg.det(acc)) > 1e-12:
                guess = np.linalg.solve(acc, rhs)
                away_by = float(np.linalg.norm(guess - mean))
                if away_by > 1e-12:
                    target = mean + (guess - mean) * (trust * min(1.0, limit / away_by))
        # Which two arcs, if any, are one curve passing through?
        away: dict[tuple[int, int], np.ndarray] = {}
        for i, k in incident:
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
            tip = _wedge(padded, target, [arcs[i].pair for i, _ in incident], [away[k] for k in keys])
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
                tangents[key] = axis if k == through else -axis
        best: tuple[float, tuple, tuple] | None = None
        for x in range(len(keys)):
            for y in range(x + 1, len(keys)):
                turn = np.degrees(np.arccos(np.clip(-float(np.dot(away[keys[x]], away[keys[y]])), -1.0, 1.0)))
                if best is None or turn < best[0]:
                    best = (turn, keys[x], keys[y])
        if not tangents and best is not None and best[0] <= corner_threshold:
            _turn, ka, kb = best
            shared = _normalize(away[ka] - away[kb])
            if np.any(shared):
                tangents[ka] = shared
                tangents[kb] = -shared
        moves.append((incident, target, tangents, {}))

    for incident, target, tangents, _ in moves:
        for i, k in incident:
            arcs[i].pts[k] = target.copy()
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
    if extend:
        padded = _extend_wedges(padded, rgb, alpha, fill_at, params)
    chains = _chains(padded)
    placed = _place(chains, padded, rgb, alpha, fill_at)

    arcs = [
        Arc(pair=ch["pair"], pts=pts, normal=normal, n0=ch["n0"], n1=ch["n1"])
        for ch, (pts, normal) in zip(chains, placed)
    ]
    _junctions(arcs, padded, params.corner_threshold)
    for arc in arcs:
        arc.segments = _fit_arc(arc, params)

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
            arc.under = _fit_arc(_bled(arc, bleed if b_later else -bleed, taper), loose)

    edge_arc: dict[int, tuple[int, int]] = {}
    for idx, ch in enumerate(chains):
        for pos, key in enumerate(ch["edges"]):
            edge_arc[key] = (idx, pos)
    return Boundary(arcs=arcs, padded=padded, edge_arc=edge_arc, _later_is_b=later_is_b)


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
    return Arc(pair=arc.pair, pts=moved, normal=arc.normal, n0=arc.n0, n1=arc.n1, t0=arc.t0, t1=arc.t1)


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
        # A region wholly inside one neighbour: no node anywhere on it, so this
        # is an ordinary closed contour and the closed fit is the right one. It
        # keeps the curve G1 across the seam and looks for corners around the
        # wrap, neither of which an open fit can do.
        return [] if len(pts) < 3 else fit_contour_segments(pts, params)[0]
    if len(pts) < 2:
        return []
    corners = _open_corners(pts, params.corner_threshold)
    bounds = [0, *corners, len(pts) - 1]
    segments: list[Segment] = []
    for k in range(len(bounds) - 1):
        lo, hi = bounds[k], bounds[k + 1]
        piece = _sharpen_piece(pts, lo, hi, corners)
        if len(piece) < 2:
            continue
        t_start = arc.t0 if lo == 0 else None
        t_end = arc.t1 if hi == len(pts) - 1 else None
        segments.extend(_fit_piece(piece, params.tol, t_start, t_end))
    return _snap_axis(segments, params.snap_axis_deg)


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


def _fit_piece(points: np.ndarray, tol: float, t_start: np.ndarray | None, t_end: np.ndarray | None) -> list[Segment]:
    """`curves.fit_open`, except that a pinned tangent is not thrown away when the
    run happens to be nearly straight.

    A straight line is the cheapest possible fit and `fit_open` reaches for it
    first, which is right everywhere else — but a tangent is only ever pinned to
    make a join smooth, and a line leaves that join at whatever angle its two
    ends happen to sit at. That is how a wedge closing to a cusp came out as a
    94 degree corner instead.
    """
    if len(points) < 2:
        return []
    if t_start is None and t_end is None:
        return fit_open(points, tol)
    chord = points[-1] - points[0]
    span = float(np.linalg.norm(chord))
    if span > 1e-9:
        along = chord / span
        drift = 0.0
        for pinned, want in ((t_start, along), (t_end, -along)):
            if pinned is not None:
                drift = max(drift, float(np.degrees(np.arccos(np.clip(np.dot(pinned, want), -1.0, 1.0)))))
        if drift <= 2.0:
            return fit_open(points, tol, t_start=t_start, t_end=t_end)
    t1 = t_start if t_start is not None else _end_tangent(points, True)
    t2 = t_end if t_end is not None else _end_tangent(points, False)
    return list(fit_cubics(points, t1, t2, tol))


def _sharpen_piece(pts: np.ndarray, lo: int, hi: int, corners: list[int], trim: float = 0.8) -> np.ndarray:
    """One run between two breaks, with the chamfer dropped at any end that is a
    corner. The end points themselves stand: an arc end is a node every
    neighbouring arc has already agreed on."""
    piece = pts[lo : hi + 1]
    if len(piece) < 4:
        return piece
    inner = piece[1:-1]
    keep = np.ones(len(inner), bool)
    if lo in corners:
        keep &= np.linalg.norm(inner - piece[0], axis=1) >= trim
    if hi in corners:
        keep &= np.linalg.norm(inner - piece[-1], axis=1) >= trim
    return np.vstack([piece[:1], inner[keep], piece[-1:]])
