import numpy as np

from studi0trace.engines.vexel.rescue import boundary_band, edge_mix, rescue_features


def test_boundary_band_marks_pixels_next_to_label_changes():
    labels = np.ones((6, 8), np.int32)
    labels[:, 4:] = 2
    band = boundary_band(labels)
    assert band[:, 2:6].all() and not band[:, :2].any() and not band[:, 6:].any()


def test_high_residual_interior_component_becomes_a_region():
    labels = np.ones((40, 40), np.int32)
    labels[:, 30:] = 2
    residual = np.zeros((40, 40), np.float32)
    residual[10:12, 5:25] = 3.0  # a 2 px thick stroke swallowed by region 1
    residual[0:3, 29:31] = 5.0  # noise on the boundary band: ignored
    out, rescued = rescue_features(labels, residual, threshold=1.0, min_region=6)
    assert len(rescued) == 1
    new = rescued[0]
    assert (out[10:12, 5:25] == new).all()
    assert out[20, 20] == out[0, 0] and out[0, 0] != new
    assert out[1, 30] != new


def test_small_components_are_not_rescued():
    labels = np.ones((20, 20), np.int32)
    residual = np.zeros((20, 20), np.float32)
    residual[10, 10] = 9.0
    out, rescued = rescue_features(labels, residual, threshold=1.0, min_region=6)
    assert rescued == [] and (out == labels).all()


def _edge_scene(band_colours: dict):
    """A mid-grey region (label 1, columns < 30) against white (label 2), with
    whole columns of region 1 repainted: {column: rgb}."""
    h, w = 24, 40
    labels = np.ones((h, w), np.int32)
    labels[:, 30:] = 2
    host, other = np.array([60.0, 60.0, 70.0, 255.0]), np.array([250.0, 250.0, 250.0, 255.0])
    pred = np.where((labels == 1)[..., None], host, other)
    colour = pred.copy()
    for col, rgb in band_colours.items():
        colour[:, col, :3] = rgb
    return labels, colour, pred, np.ones((h, w))


HOST, OTHER = np.array([60.0, 60.0, 70.0]), np.array([250.0, 250.0, 250.0])


def test_edge_mix_explains_an_edge_ringing_both_ways_and_nothing_else():
    labels, colour, pred, alpha = _edge_scene({
        27: HOST + 0.12 * (OTHER - HOST),  # the light ring a sharpened edge leaves inside
        28: HOST - 0.10 * (OTHER - HOST),  # and its overshoot the other way
        25: HOST + 0.60 * (OTHER - HOST),  # more than half way across: not this edge's rendering
        26: (200.0, 40.0, 40.0),  # red: nowhere near the line between the two fills
        20: HOST + 0.12 * (OTHER - HOST),  # the ring's colour, but out of the edge's reach
    })
    ex = edge_mix(labels, colour, pred, alpha, np.ones(labels.shape, bool))
    assert ex[:, 27].all() and ex[:, 28].all()
    assert not ex[:, 25].any() and not ex[:, 26].any() and not ex[:, 20].any()


def test_only_the_nearest_edge_explains_a_pixel():
    """A pale red pixel two columns from the white region and three from a red
    line the partition kept: the edge it sits on is the one with white, and
    against white it is no mix at all. The red line three pixels off must not
    explain it away (that is how a partly swallowed outline lost its rescue)."""
    red = np.array([220.0, 40.0, 40.0])
    labels, colour, pred, alpha = _edge_scene({28: HOST + 0.4 * (red - HOST)})
    labels[:, 25] = 3
    pred[:, 25, :3] = red
    colour[:, 25, :3] = red
    ex = edge_mix(labels, colour, pred, alpha, np.ones(labels.shape, bool))
    assert not ex[:, 28].any()


def test_a_component_only_half_explained_is_still_rescued():
    labels = np.ones((20, 40), np.int32)
    labels[:, 30:] = 2
    residual = np.zeros((20, 40), np.float32)
    residual[8:12, 10] = 3.0  # a 4 px feature, well inside region 1
    explained = np.zeros((20, 40), bool)
    explained[8:10, 10] = True  # half of it looks like an edge's rendering
    _, rescued = rescue_features(labels, residual, threshold=1.0, min_region=3, explained=explained)
    assert len(rescued) == 1
    explained[8:11, 10] = True  # most of it does, and the rest is under the floor
    _, rescued = rescue_features(labels, residual, threshold=1.0, min_region=3, explained=explained)
    assert rescued == []


def test_a_ringing_band_is_not_rescued_but_a_line_beside_it_is():
    labels, colour, pred, alpha = _edge_scene({
        27: HOST + 0.15 * (OTHER - HOST),  # ringing, 3 px inside the edge
        10: (200.0, 40.0, 40.0),  # a red hairline the partition lost
    })
    residual = (np.linalg.norm(colour - pred, axis=-1) / 20.0).astype(np.float32)
    ex = edge_mix(labels, colour, pred, alpha, residual > 1.0)
    out, rescued = rescue_features(labels, residual, threshold=1.0, min_region=3, explained=ex)
    assert len(rescued) == 1
    assert (out[2:-2, 10] == rescued[0]).all() and not (out[:, 27] == rescued[0]).any()
    # without the edge test the ring is promoted as well: the sliver this guards against
    _, before = rescue_features(labels, residual, threshold=1.0, min_region=3)
    assert len(before) == 2


def _core_map(labels):
    from studi0trace.engines.vexel.weights import interior

    core = np.zeros(labels.shape, bool)
    for lab in np.unique(labels):
        m = labels == lab
        core[m] = interior(m)[1]
    return core


def test_sharpening_halo_along_an_edge_is_not_a_feature():
    # A glyph (label 2) on white (label 1), edge between columns 9 and 10. A
    # sharpened source rings inside the edge: the rebound ring three pixels in
    # disagrees with the glyph's fill, but it is the edge's, which the fill is
    # not fitted from (weights.fill_core), so it must not become a region: that
    # promoted a 1 px sliver down the whole "l" of the Vexel wordmark.
    labels = np.ones((40, 40), np.int32)
    labels[:, 10:] = 2
    core = _core_map(labels)
    residual = np.zeros((40, 40), np.float32)
    residual[:, 12] = 1.7  # depth 3 into the glyph
    out, rescued = rescue_features(labels, residual, threshold=1.0, min_region=3, core=core)
    assert rescued == [] and (out == labels).all()
    residual[:, 12] = 0.0
    residual[5:35, 14] = 1.7  # depth 5: a swallowed line, not the edge's ring
    out, rescued = rescue_features(labels, residual, threshold=1.0, min_region=3, core=core)
    assert len(rescued) == 1 and (out[5:35, 14] == rescued[0]).all()


def test_a_feature_that_reaches_the_core_keeps_its_edge_band_pixels():
    # The darkest band of a drop shadow runs right up to its caster: the band
    # pixels next to the edge join the feature as long as it reaches the core.
    labels = np.ones((40, 40), np.int32)
    labels[:, 10:] = 2
    core = _core_map(labels)
    residual = np.zeros((40, 40), np.float32)
    residual[5:35, 12:20] = 2.0  # from depth 3 to depth 10
    out, rescued = rescue_features(labels, residual, threshold=1.0, min_region=3, core=core)
    assert len(rescued) == 1 and (out[5:35, 12:20] == rescued[0]).all()
