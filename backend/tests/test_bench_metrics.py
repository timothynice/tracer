import numpy as np
import pytest

from bench import metrics
from bench.config import DEFAULT_WEIGHTS
from bench.raster import rasterize, to_rgb_on_white

GRADIENT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '<defs><linearGradient id="g"><stop offset="0" stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient></defs>'
    '<rect width="100" height="100" fill="url(#g)"/></svg>'
)


def ramp(width: int = 256, height: int = 32) -> np.ndarray:
    """Opaque horizontal luminance ramp 0..255 as RGBA."""
    row = np.arange(width, dtype=np.uint8)
    rgb = np.repeat(row[None, :, None], 3, axis=2)
    rgb = np.repeat(rgb, height, axis=0)
    return np.dstack([rgb, np.full((height, width, 1), 255, np.uint8)])


def posterize(rgba: np.ndarray, levels: int) -> np.ndarray:
    step = 256 // levels
    out = rgba.copy()
    out[..., :3] = (rgba[..., :3] // step) * step
    return out


def solid(color, alpha=255, size=(32, 32)) -> np.ndarray:
    return np.dstack([np.full((*size, 3), color, np.uint8), np.full((*size, 1), alpha, np.uint8)])


def test_rasterize_gradient_left_red_right_blue():
    img = rasterize(GRADIENT_SVG, 200, 200)
    assert img.shape == (200, 200, 4)
    assert img[100, 0, 0] > 240 and img[100, 0, 2] < 15
    assert img[100, 199, 2] > 240 and img[100, 199, 0] < 15


def test_to_rgb_on_white_composites_alpha():
    half = solid((0, 0, 0), alpha=128)
    rgb = to_rgb_on_white(half)
    assert 125 <= rgb[0, 0, 0] <= 130


def test_identical_images_are_perfect():
    src = ramp()
    rgb = to_rgb_on_white(src)
    assert metrics.ssim(rgb, rgb) == pytest.approx(1.0)
    assert metrics.delta_e(rgb, rgb) == (0.0, 0.0)
    assert metrics.edge_f1(rgb, rgb) == 1.0
    assert metrics.alpha_mae(src, src) == 0.0
    assert metrics.banding_index(rgb, rgb)[0] == 0.0


def test_inverted_image_scores_badly():
    src = to_rgb_on_white(ramp())
    inv = 255 - src
    assert metrics.ssim(src, inv) < 0.5
    assert metrics.delta_e(src, inv)[0] > 30


def test_banding_detects_posterised_gradient():
    src = to_rgb_on_white(ramp())
    smooth_index, fraction = metrics.banding_index(src, src)
    banded_index, _ = metrics.banding_index(src, to_rgb_on_white(posterize(ramp(), 8)))
    assert fraction > 0.8, "a full-frame ramp should be almost entirely 'smooth'"
    assert smooth_index < 0.1
    assert banded_index > 1.0
    assert banded_index > 5 * max(smooth_index, 0.01)


def test_gentle_512px_ramp_is_still_smooth():
    # 0..102 over 512 px ≈ 0.2 luma/px, the slope of a full-frame brand gradient
    row = (np.arange(512) * 0.2).astype(np.uint8)
    rgb = np.repeat(np.repeat(row[None, :, None], 3, axis=2), 16, axis=0)
    _, fraction = metrics.banding_index(rgb, rgb)
    assert fraction > 0.8


def test_flat_image_has_no_smooth_region():
    rgb = to_rgb_on_white(solid((90, 90, 90)))
    index, fraction = metrics.banding_index(rgb, rgb)
    assert index == 0.0 and fraction == 0.0


def test_edge_f1_zero_when_edges_missing():
    with_edge = to_rgb_on_white(solid((255, 255, 255)))
    with_edge[:, 16:, :] = 0
    flat = to_rgb_on_white(solid((255, 255, 255)))
    assert metrics.edge_f1(with_edge, flat) == 0.0
    assert metrics.edge_f1(flat, flat) == 1.0
    shifted = to_rgb_on_white(solid((255, 255, 255)))
    shifted[:, 17:, :] = 0  # same edge, 1 px over: inside the 2 px tolerance
    assert metrics.edge_f1(with_edge, shifted) > 0.9
    far = to_rgb_on_white(solid((255, 255, 255)))
    far[:, 24:, :] = 0  # 8 px over: outside tolerance
    assert metrics.edge_f1(with_edge, far) < 0.1


def test_alpha_mae():
    assert metrics.alpha_mae(solid((0, 0, 0), 255), solid((0, 0, 0), 0)) == 1.0


def test_composite_is_bounded_and_monotone():
    good = {"ssim": 1.0, "delta_e_mean": 0.0, "edge_f1": 1.0, "banding_index": 0.0, "paths": 1}
    bad = {"ssim": 0.0, "delta_e_mean": 50.0, "edge_f1": 0.0, "banding_index": 50.0, "paths": 100000}
    g, b = metrics.composite(good), metrics.composite(bad)
    assert g["score"] == pytest.approx(1.0) and b["score"] == pytest.approx(0.0)
    for k in ("fidelity", "smoothness", "economy", "score"):
        assert 0.0 <= b[k] <= g[k] <= 1.0


def test_all_metrics_shape():
    src = ramp(64, 64)
    out = rasterize(GRADIENT_SVG, 64, 64)
    m = metrics.all_metrics(src, out, GRADIENT_SVG, elapsed_ms=3.2, truth_paths=2)
    for key in ("ssim", "delta_e_mean", "delta_e_p95", "edge_f1", "alpha_mae", "banding_index",
                "smooth_fraction", "paths", "nodes", "bytes", "gradients", "unique_fills",
                "path_ratio", "elapsed_ms", "fidelity", "smoothness", "economy", "score"):
        assert key in m, key
    assert m["gradients"] == 1
    assert m["path_ratio"] == 0.0  # rect, no <path>
    assert m["elapsed_ms"] == 3.2
    assert DEFAULT_WEIGHTS.as_dict()["w_ssim"] == 0.35
