"""A rescued band beside its parent's outline reaches the outline: the parent's
edge band between the two goes to whichever is nearer (`engine.reach_the_edge`).
Left with the parent, a two-pixel strip ran on between the band and the canvas
— the wrong colour along the outline, and on a diagonal a node at every row."""
from __future__ import annotations

import numpy as np

from studi0trace.engines.vexel.engine import reach_the_edge

GREY = np.array([200.0, 200.0, 200.0, 255.0])
INK = np.array([50.0, 50.0, 50.0, 255.0])


def _fill_at(lab, qx, qy):
    return np.tile(INK if lab == 3 else GREY, (len(qx), 1))


def _scene():
    h, w = 24, 24
    before = np.full((h, w), 2, np.int32)
    before[:8] = 1                                     # the canvas above the shape
    labels = before.copy()
    labels[10:16, 4:20] = 3                            # rescued from 2, two pixels short of the canvas
    ys, xs = np.mgrid[0:h, 0:w]
    xs, ys = xs.astype(np.float64) + 0.5, ys.astype(np.float64) + 0.5
    rgba = np.tile(GREY, (h, w, 1))
    rgba[8:16, 4:20] = INK                             # the band's ink runs to the outline
    return labels, before, xs, ys, rgba


def test_a_rescued_band_takes_its_parents_edge_band_up_to_the_outline():
    labels, before, xs, ys, rgba = _scene()
    out = reach_the_edge(labels, before, [3], xs, ys, rgba, _fill_at)
    assert (out[8:10, 6:18] == 3).all(), out[8:10]
    # the band now touches the canvas
    assert ((out[8] == 3) & (out[7] == 1)).any()
    # nothing else moved: the parent keeps its band elsewhere and its body
    assert (out[8:10, :3] == 2).all() and (out[8:10, 21:] == 2).all()
    assert (out[16:] == 2).all() and (out[:8] == 1).all()


def test_the_parent_keeps_a_band_pixel_nearer_to_it():
    """Two pixels from the rescued region but one from the parent's body: stays."""
    labels, before, xs, ys, rgba = _scene()
    out = reach_the_edge(labels, before, [3], xs, ys, rgba, _fill_at)
    assert out[8, 3] == 2 and out[8, 20] == 2, (out[8, 3], out[8, 20])


def test_only_the_band_within_two_pixels_of_the_rescued_region_is_asked():
    labels, before, xs, ys, rgba = _scene()
    labels[8:10, 4:20] = 3                             # already at the outline
    out = reach_the_edge(labels, before, [3], xs, ys, rgba, _fill_at)
    assert (out[8:10, :2] == 2).all() and (out[8:10, 22:] == 2).all()
    assert (out[10:] == labels[10:]).all() and (out[:8] == 1).all()


def test_a_small_neighbour_on_the_way_is_not_an_outline_to_reach():
    """Nine pixels of dot on a rescued line: the two pixels of band between
    them are the stroke stage's to bridge, and joined to the dot the ring of
    thin-mark-128 could no longer be stroked."""
    h, w = 24, 24
    before = np.full((h, w), 2, np.int32)
    before[10:13, 11:14] = 1                           # a dot, nine pixels
    labels = before.copy()
    labels[10:13, 0:9] = 3                             # a rescued line, 27 px, stopping two short of the dot
    ys, xs = np.mgrid[0:h, 0:w]
    xs, ys = xs.astype(np.float64) + 0.5, ys.astype(np.float64) + 0.5
    rgba = np.tile(GREY, (h, w, 1))
    rgba[10:13, 0:14] = INK
    out = reach_the_edge(labels, before, [3], xs, ys, rgba, _fill_at)
    assert (out == labels).all()
