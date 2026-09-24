import numpy as np

from studi0trace.engines.vexel import stats as st
from studi0trace.engines.vexel.merge import MergeParams, adjacency, merge_regions
from studi0trace.engines.vexel.partition import discontinuity, initial_labels
from studi0trace.engines.vexel.prepare import prepare


def features_from(img_rgba):
    p = prepare(img_rgba)
    return p.features


def flat_halves(delta_l=6, size=64):
    """Two flat halves whose L* differ by ~delta_l (grey levels chosen in Lab)."""
    from skimage.color import lab2rgb

    l0 = 50.0
    rgb_a = (lab2rgb(np.array([[[l0, 0, 0]]]))[0, 0] * 255).round()
    rgb_b = (lab2rgb(np.array([[[l0 + delta_l, 0, 0]]]))[0, 0] * 255).round()
    img = np.zeros((size, size, 4), np.uint8)
    img[..., 3] = 255
    img[:, : size // 2, :3] = rgb_a
    img[:, size // 2 :, :3] = rgb_b
    return img


def test_planar_image_has_near_zero_planar_sse_but_large_solid_sse():
    h, w = 40, 40
    xn, yn, _, _ = st.normalised_coords(h, w)
    colours = np.stack([50 + 30 * xn + 10 * yn, 20 * xn, np.zeros_like(xn), np.zeros_like(xn)], axis=-1)
    labels = np.ones((h, w), np.int32)
    stats = st.accumulate(labels, xn, yn, colours)
    sse = st.model_sse(stats[1], 4)
    assert sse[0] > 1000
    assert sse[1] < 1e-3 and sse[2] < 1e-3


def test_union_stats_equal_stats_of_union():
    h, w = 20, 30
    xn, yn, _, _ = st.normalised_coords(h, w)
    colours = np.random.default_rng(0).normal(size=(h, w, 4)) * 10 + 50
    labels = np.ones((h, w), np.int32)
    labels[:, 15:] = 2
    stats = st.accumulate(labels, xn, yn, colours)
    whole = st.accumulate(np.ones((h, w), np.int32), xn, yn, colours)
    assert np.allclose(stats[1] + stats[2], whole[1])


def test_merge_distance_is_mean_colour_difference_for_flat_regions():
    h, w = 32, 32
    xn, yn, _, _ = st.normalised_coords(h, w)
    colours = np.zeros((h, w, 4))
    colours[:, 16:, 0] = 6.0
    labels = np.ones((h, w), np.int32)
    labels[:, 16:] = 2
    stats = st.accumulate(labels, xn, yn, colours)
    cost, best = st.region_cost(stats, 4, mu=4.0)
    union_cost, _ = st.region_cost(stats[1] + stats[2], 4, mu=4.0)
    d = st.merge_distance(float(union_cost), float(cost[1]), float(cost[2]), stats[1, 0], stats[2, 0])
    assert 5.5 < d < 6.5
    assert best[1] == 0 and best[2] == 0  # flat regions choose the solid model


def test_flat_neighbours_merge_only_below_detail():
    img = flat_halves(6)
    f = features_from(img)
    grad = discontinuity(f)
    labels0 = initial_labels(grad, f)
    assert len(np.unique(labels0)) == 2
    assert len(np.unique(merge_regions(labels0, f, MergeParams(detail=4)))) == 2
    assert len(np.unique(merge_regions(labels0, f, MergeParams(detail=12)))) == 1


def test_gradient_disc_over_flat_background_is_two_regions():
    size = 96
    img = np.zeros((size, size, 4), np.uint8)
    img[..., :3] = (240, 240, 240)
    img[..., 3] = 255
    yy, xx = np.mgrid[0:size, 0:size]
    disc = (xx - 48) ** 2 + (yy - 48) ** 2 < 36**2
    t = (xx - 12) / 72.0
    ramp_r = (255 * (1 - t)).clip(0, 255)
    ramp_b = (255 * t).clip(0, 255)
    img[disc, 0] = ramp_r[disc]
    img[disc, 1] = 40
    img[disc, 2] = ramp_b[disc]
    f = features_from(img)
    grad = discontinuity(f)
    labels0 = initial_labels(grad, f)
    merged = merge_regions(labels0, f, MergeParams(detail=8), grad)
    ids = np.unique(merged)
    assert len(ids) == 2, f"expected background + one gradient region, got {len(ids)}"
    assert len(np.unique(merged[disc & ((xx - 48) ** 2 + (yy - 48) ** 2 < 30**2)])) == 1
    # with gradients disabled the ramp is still one region here: it is its
    # fitted fill that is cut into bands (tests/test_vexel_posterize.py)


def test_adjacency_counts_boundary_pixels():
    labels = np.ones((4, 6), np.int32)
    labels[:, 3:] = 2
    edges = adjacency(labels)
    assert edges == {(1, 2): [4.0, 0.0]}
