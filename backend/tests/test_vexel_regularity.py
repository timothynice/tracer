"""Task 8: parallel, perpendicular and axis regularity across the boundary graph."""
from __future__ import annotations

import math
import re

import numpy as np

from studi0trace.engines.vexel.curves import Cubic, Line
from studi0trace.engines.vexel.regularity import cluster_directions, regularize
from tests.test_vexel_topology import jpeg, tilted_square_png, trace

# The SVG is written to two decimals, so a 120 px edge's direction is known to
# 0.01/120 rad. Anything under one such step is exact as far as the file can say.
ONE_STEP_DEG = math.degrees(0.01 / 120.0)


def line_directions(svg: str) -> list[float]:
    """Angle mod 180 of every straight segment in every path (absolute M/L/Z)."""
    out = []
    for d in re.findall(r'\sd="([^"]*)"', svg):
        pos = start = None
        for cmd, body in re.findall(r"([MLCAZ])([^MLCAZ]*)", d):
            v = [float(x) for x in re.findall(r"-?\d*\.?\d+", body)]
            if cmd == "M":
                pos = start = np.array(v[:2])
            elif cmd == "A":
                pos = np.array(v[5:7])
            elif cmd == "L":
                nxt = np.array(v[:2])
                out.append(math.degrees(math.atan2(*(nxt - pos)[::-1])) % 180.0)
                pos = nxt
            elif cmd == "C":
                pos = np.array(v[4:6])
            elif cmd == "Z" and pos is not None and start is not None and np.linalg.norm(pos - start) > 1e-6:
                out.append(math.degrees(math.atan2(*(start - pos)[::-1])) % 180.0)
                pos = start
    return out


def _apart(a: float, b: float) -> float:
    return abs(((a - b) + 90.0) % 180.0 - 90.0)


def test_parallel_and_perpendicular_edges_of_one_logo_come_out_exactly_so():
    """A JPEG'd tilted square fits as four lines whose directions disagree by a
    few hundredths of a degree (0.10° on the 5° square). Across the graph they
    are one pair of directions, exactly a right angle apart. Read at four
    decimals: at the default two, the endpoints' rounding alone moves a 120 px
    line's direction by up to 1.4 steps, and the test was measuring where the
    corners happened to fall against the rounding grid."""
    for ang in (5, 38, 50):
        svg = trace(jpeg(tilted_square_png(ang), quality=75), path_precision=4)
        dirs = [d for d in line_directions(svg) if _apart(d, 0.0) > 0.01 and _apart(d, 90.0) > 0.01]
        assert len(dirs) == 4, (ang, dirs)
        for a in dirs:
            for b in dirs:
                if a is b:
                    continue
                off = _apart(a, b) if _apart(a, b) < 45.0 else _apart(a, (b + 90.0) % 180.0)
                assert off <= ONE_STEP_DEG, f"{ang}°: edges {a:.4f} and {b:.4f} are off by {off:.4f}°"


def test_clusters_snap_to_the_axis_and_to_perpendicular():
    lines = [(0.4, 60.0), (179.95, 30.0), (89.6, 50.0), (45.3, 20.0)]
    targets = cluster_directions(lines, snap_axis_deg=1.5)
    assert targets[0] == 0.0 and targets[1] == 0.0
    assert targets[2] == 90.0
    assert 3 not in targets, "a 20 px cluster is too little to snap"
    tilted = [(30.3, 100.0), (29.9, 90.0), (120.5, 45.0)]
    t = cluster_directions(tilted, snap_axis_deg=1.5)
    assert abs(t[0] - t[1]) < 1e-9 and 29.9 < t[0] < 30.3
    assert abs(t[2] - (t[0] + 90.0)) < 1e-9, "the lighter cluster is made exactly perpendicular to the heavier"


def test_a_line_between_two_nodes_never_turns_and_a_free_line_turns_about_its_node():
    a = Line(np.array([0.0, 0.0]), np.array([50.0, 0.4]))          # nodes at both ends, alone in its arc
    b = [Line(np.array([0.0, 10.0]), np.array([50.0, 10.0])),
         Cubic(np.array([50.0, 10.0]), np.array([55.0, 12.0]), np.array([58.0, 18.0]), np.array([60.0, 25.0]))]
    before = a.p1.copy()
    moved = regularize([([a], False), (b, False)], snap_axis_deg=1.5)
    assert np.array_equal(a.p1, before), "an arc that is one line between two nodes stays put"
    assert moved == 0
    c = [Line(np.array([0.0, 20.0]), np.array([50.0, 20.1])),
         Cubic(np.array([50.0, 20.1]), np.array([55.0, 22.0]), np.array([58.0, 28.0]), np.array([60.0, 35.0]))]
    moved = regularize([(c, False), (b, False)], snap_axis_deg=1.5)
    assert moved == 1
    assert c[0].p0[1] == 20.0 and abs(c[0].p1[1] - 20.0) < 1e-9, "the line turned about its node end"
    assert np.allclose(c[1].p0, c[0].p1) and np.allclose(c[1].c1, [55.0, 21.9]), "the curve's end and its arm moved together"


def test_a_line_that_would_have_to_move_further_than_the_placement_knows_stays_put():
    """0.4 px over 50 px is half a degree: inside the cluster, but the end would
    move 0.4 px, four times what the placement can vouch for. Not parallel."""
    c = [Line(np.array([0.0, 20.0]), np.array([50.0, 20.4])),
         Cubic(np.array([50.0, 20.4]), np.array([55.0, 22.0]), np.array([58.0, 28.0]), np.array([60.0, 35.0]))]
    b = [Line(np.array([0.0, 10.0]), np.array([50.0, 10.0])),
         Cubic(np.array([50.0, 10.0]), np.array([55.0, 12.0]), np.array([58.0, 18.0]), np.array([60.0, 25.0]))]
    assert regularize([(c, False), (b, False)], snap_axis_deg=1.5) == 0
    assert c[0].p1[1] == 20.4
