import numpy as np

from studi0trace.engines.vexel.rescue import boundary_band, rescue_features


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
