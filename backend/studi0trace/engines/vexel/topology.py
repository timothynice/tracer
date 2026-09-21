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
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from studi0trace.engines.vexel.boundary import FillAt
from studi0trace.engines.vexel.curves import (
    CurveParams,
    Line,
    Segment,
    fit_contour_segments,
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
        return [(seq[0][0], seq[-1][1] < seq[0][1])]
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

    for v in sorted(nodes):
        for first in inc[v]:
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
    for key in pixels:
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
    t = np.full(len(p_in), 0.5)
    filled = np.zeros(len(p_in), bool)
    for k in range(3):
        lo, hi = level[:, k], level[:, k + 1]
        crosses = ~filled & (lo >= 0.5) & (hi < 0.5)
        if not crosses.any():
            continue
        span = np.where(crosses, lo - hi, 1.0)
        t = np.where(crosses, at[k] + (lo - 0.5) / np.maximum(span, 1e-9), t)
        filled |= crosses
    return np.clip(t, -REACH, 1.0 + REACH)


def _place(chains: list[dict], padded: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, fill_at: FillAt) -> list[np.ndarray]:
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


def _junctions(arcs: list[Arc], corner_threshold: float, reach: float = 4.0, trim: float = 0.8, limit: float = 2.0) -> None:
    """Place each node, and give arcs that run through it a shared tangent.

    Marching squares chamfers a junction the way it chamfers a corner, and each
    arc arrives at its own chamfered end. Fitting a line to each arc's approach
    and taking the point closest to all of them recovers the junction and hands
    every arc the same one.

    Then: where a third region merely ends against a boundary that carries on —
    the silhouette of a mark, with the colour changing along it — two of the arcs
    are one smooth curve. Pinning both to a single tangent keeps it smooth, so
    the outline does not hitch where the fill changes.
    """
    ends: dict[int, list[tuple[int, int]]] = {}
    for idx, arc in enumerate(arcs):
        if arc.closed or len(arc.pts) < 2:
            continue
        ends.setdefault(arc.n0, []).append((idx, 0))
        ends.setdefault(arc.n1, []).append((idx, -1))

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
            if abs(np.linalg.det(acc)) > 1e-9:
                guess = np.linalg.solve(acc, rhs)
                if np.linalg.norm(guess - mean) <= limit:
                    target = guess
        for i, k in incident:
            arcs[i].pts[k] = target

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

        best: tuple[float, tuple, tuple] | None = None
        keys = list(away)
        for x in range(len(keys)):
            for y in range(x + 1, len(keys)):
                turn = np.degrees(np.arccos(np.clip(-float(np.dot(away[keys[x]], away[keys[y]])), -1.0, 1.0)))
                if best is None or turn < best[0]:
                    best = (turn, keys[x], keys[y])
        if best is None or best[0] > corner_threshold:
            continue
        _turn, ka, kb = best
        shared = _normalize(away[ka] - away[kb])
        if not np.any(shared):
            continue
        for key, sign in ((ka, 1.0), (kb, -1.0)):
            i, k = key
            if k == 0:
                arcs[i].t0 = shared * sign
            else:
                arcs[i].t1 = shared * sign


def build(
    labels: np.ndarray,
    rgb: np.ndarray,
    alpha: np.ndarray,
    fill_at: FillAt,
    params: CurveParams,
    rank: dict[int, int] | None = None,
    bleed: float | None = None,
    taper: float | None = None,
) -> Boundary:
    """The whole boundary of the label map, placed sub-pixel and fitted once.

    `rank` is the paint order by label. Given it, each arc also gets a copy bled
    towards whichever side paints later, for the earlier side to use.
    """
    bleed = BLEED if bleed is None else bleed
    taper = TAPER if taper is None else taper
    padded = np.pad(labels.astype(np.int64), 1, constant_values=0)
    chains = _chains(padded)
    placed = _place(chains, padded, rgb, alpha, fill_at)

    arcs = [
        Arc(pair=ch["pair"], pts=pts, normal=normal, n0=ch["n0"], n1=ch["n1"])
        for ch, (pts, normal) in zip(chains, placed)
    ]
    _junctions(arcs, params.corner_threshold)
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
        segments.extend(fit_open(piece, params.tol, t_start=t_start, t_end=t_end))
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
