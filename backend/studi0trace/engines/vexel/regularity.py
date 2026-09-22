"""Stage 7b: regularity across the boundary graph.

Artwork is full of lines that are meant to be parallel, perpendicular or on an
axis, and a trace that has them a fraction of a degree apart reads as sloppy
even when every line is within tolerance of its own edge. This stage looks at
every straight segment the fit produced, clusters their directions, and snaps
each cluster that carries enough length to one direction: the length-weighted
mean, made exactly horizontal or vertical when it is within the axis snap, and
made exactly perpendicular to another cluster when it is within a degree of it.

A snapped line turns about a fixed point — the node at its end if it has one,
its midpoint otherwise — and the joints it shares with neighbouring segments are
re-made: two lines meet at their new crossing, a line and a curve meet where
the old joint projects onto the new line. A line with a node at both ends
cannot turn without moving a node the other arcs were fitted to, and is left
alone. Vectorizer.AI lists exactly this under "symmetry modelling"; PolyFit
calls it regularity.
"""
from __future__ import annotations

import math

import numpy as np

from studi0trace.engines.vexel.curves import Cubic, Line, Segment, _intersect

CLUSTER_DEG = 0.5        # directions within this of each other are one cluster
CLUSTER_MIN_PX = 40.0    # a cluster snaps only when it carries this much line
PERP_DEG = 0.5           # a cluster this close to perpendicular to a bigger one is made exactly so
# px. A snap may move a line's end this far and no further: the placement knows
# an edge to a tenth of a pixel, so a line that would have to move more than
# that to join its cluster is not really parallel to it, and is left where its
# pixels are. A joint moves to the crossing with its neighbour only when that
# crossing is within the same distance of the plain projection.
END_MOVE_MAX = 0.15


def _angle(seg: Line) -> float:
    d = seg.p1 - seg.p0
    return math.degrees(math.atan2(d[1], d[0])) % 180.0


def _length(seg: Segment) -> float:
    return float(np.linalg.norm(seg.p1 - seg.p0))


def _circular_mean(angles: list[float], weights: list[float]) -> float:
    """Length-weighted mean of directions modulo 180 degrees."""
    z = sum(w * complex(math.cos(math.radians(2 * a)), math.sin(math.radians(2 * a))) for a, w in zip(angles, weights))
    if abs(z) < 1e-12:
        return angles[0]
    return (math.degrees(math.atan2(z.imag, z.real)) / 2.0) % 180.0


def _diff(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def cluster_directions(lines: list[tuple[float, float]], snap_axis_deg: float) -> dict[int, float]:
    """`lines` are (angle, length). Returns, per input index, the target angle of
    its cluster, for lines whose cluster carries at least CLUSTER_MIN_PX."""
    if not lines:
        return {}
    order = sorted(range(len(lines)), key=lambda i: lines[i][0])
    clusters: list[list[int]] = [[order[0]]]
    for i in order[1:]:
        if _diff(lines[i][0], lines[clusters[-1][-1]][0]) <= CLUSTER_DEG:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    # the wrap at 180: first and last clusters may be one
    if len(clusters) > 1 and _diff(lines[clusters[0][0]][0], lines[clusters[-1][-1]][0]) <= CLUSTER_DEG:
        clusters[0].extend(clusters.pop())
    targets: list[tuple[float, float, list[int]]] = []  # (angle, weight, members)
    for members in clusters:
        weight = sum(lines[i][1] for i in members)
        if weight < CLUSTER_MIN_PX:
            continue
        angle = _circular_mean([lines[i][0] for i in members], [lines[i][1] for i in members])
        if min(angle, 180.0 - angle) <= snap_axis_deg:
            angle = 0.0
        elif abs(angle - 90.0) <= snap_axis_deg:
            angle = 90.0
        targets.append((angle, weight, members))
    # perpendicular pairs: the lighter cluster takes the heavier one's angle + 90
    targets.sort(key=lambda t: -t[1])
    fixed: list[tuple[float, float, list[int]]] = []
    for angle, weight, members in targets:
        for other, _w, _m in fixed:
            if _diff(angle, (other + 90.0) % 180.0) <= PERP_DEG:
                angle = (other + 90.0) % 180.0
                break
        fixed.append((angle, weight, members))
    out: dict[int, float] = {}
    for angle, _weight, members in fixed:
        for i in members:
            out[i] = angle
    return out


def _unit(angle: float) -> np.ndarray:
    return np.array([math.cos(math.radians(angle)), math.sin(math.radians(angle))])


def _line_of(seg: Segment) -> tuple[np.ndarray, np.ndarray] | None:
    if not isinstance(seg, Line):
        return None
    d = seg.p1 - seg.p0
    n = float(np.linalg.norm(d))
    return (seg.p0, d / n) if n > 1e-9 else None


def _rejoin(line: Line, end: str, neighbour: Segment | None, new_dir: np.ndarray, anchor: np.ndarray) -> None:
    """Move `line`'s `end` onto the snapped line, and the neighbour's touching end with it."""
    old = line.p1 if end == "p1" else line.p0
    corner = anchor + new_dir * float((old - anchor) @ new_dir)
    other = _line_of(neighbour) if neighbour is not None else None
    if other is not None:
        x = _intersect(anchor, new_dir, other[0], other[1])
        if x is not None and float(np.linalg.norm(x - corner)) <= END_MOVE_MAX:
            corner = x
    delta = corner - old
    if end == "p1":
        line.p1 = corner.copy()
    else:
        line.p0 = corner.copy()
    if neighbour is not None:
        # The neighbour's touching end comes along; a curve's arm at that end
        # comes too, so the tangent it was fitted with is kept.
        if end == "p1":
            neighbour.p0 = corner.copy()
            if isinstance(neighbour, Cubic):
                neighbour.c1 = neighbour.c1 + delta
        else:
            neighbour.p1 = corner.copy()
            if isinstance(neighbour, Cubic):
                neighbour.c2 = neighbour.c2 + delta


def regularize(segment_lists: list[tuple[list[Segment], bool]], snap_axis_deg: float) -> int:
    """Snap the straight segments of every list to their cluster direction.

    Each entry is (segments, closed): an arc's fitted segments, closed when the
    arc is a loop with no node. In an open arc the first segment's start and the
    last segment's end are nodes and never move. Returns how many lines moved.
    """
    entries: list[tuple[int, int]] = []
    lines: list[tuple[float, float]] = []
    for li, (segs, _closed) in enumerate(segment_lists):
        for si, seg in enumerate(segs):
            if isinstance(seg, Line) and _length(seg) > 1e-6:
                entries.append((li, si))
                lines.append((_angle(seg), _length(seg)))
    targets = cluster_directions(lines, snap_axis_deg)
    moved = 0
    for k, (li, si) in enumerate(entries):
        if k not in targets:
            continue
        segs, closed = segment_lists[li]
        line = segs[si]
        assert isinstance(line, Line)
        if _diff(_angle(line), targets[k]) < 1e-9:
            continue
        n = len(segs)
        start_is_node = not closed and si == 0
        end_is_node = not closed and si == n - 1
        if start_is_node and end_is_node:
            continue
        prev_seg = segs[(si - 1) % n] if (closed or si > 0) else None
        next_seg = segs[(si + 1) % n] if (closed or si < n - 1) else None
        if n == 1:
            prev_seg = next_seg = None
        new_dir = _unit(targets[k])
        if float(new_dir @ (line.p1 - line.p0)) < 0.0:
            new_dir = -new_dir
        if start_is_node:
            anchor = line.p0.copy()
        elif end_is_node:
            anchor = line.p1.copy()
        else:
            anchor = 0.5 * (line.p0 + line.p1)
        # the turn must stay inside what the placement can tell: an end that
        # would have to move further than END_MOVE_MAX says this line is not
        # really in the cluster
        far = max(
            float(np.linalg.norm(anchor + new_dir * float((q - anchor) @ new_dir) - q)) for q in (line.p0, line.p1)
        )
        if far > END_MOVE_MAX:
            continue
        if not start_is_node:
            _rejoin(line, "p0", prev_seg, new_dir, anchor)
        if not end_is_node:
            _rejoin(line, "p1", next_seg, new_dir, anchor)
        moved += 1
    return moved
