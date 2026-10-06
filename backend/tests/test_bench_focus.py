"""bench.focus: what the inner loop measures."""
import numpy as np

from bench import focus


def _disc(r: float, w: int = 64) -> np.ndarray:
    yy, xx = np.mgrid[0:w, 0:w] + 0.5
    cover = np.clip(r - np.hypot(xx - w / 2, yy - w / 2) + 0.5, 0, 1)
    rgb = 255 - (cover[..., None] * np.array([235, 155, 55])).astype(np.uint8)
    return rgb.astype(np.uint8)


def test_subpixel_edge_shift_is_not_visible():
    de = focus.view_de(_disc(20.0), _disc(20.15))
    assert focus.region_stats(de, (0, 0, 64, 64))["visible_frac"] < 0.005


def test_half_pixel_edge_shift_is_visible():
    de = focus.view_de(_disc(20.0), _disc(20.5))
    assert focus.region_stats(de, (0, 0, 64, 64))["visible_frac"] > 0.03


def test_missing_shape_is_visible_only_in_its_region():
    src = _disc(20.0)
    out = np.full_like(src, 255)
    out[:, 32:] = src[:, 32:]          # left half of the disc gone
    de = focus.view_de(src, out)
    left = focus.region_stats(de, (0, 0, 32, 64))
    right = focus.region_stats(de, (34, 0, 64, 64))
    assert left["visible_frac"] > 0.2
    assert right["visible_frac"] == 0.0


def test_region_defects_count_only_inside_the_box():
    card = {
        "_clusters": [{"x": 5, "y": 5, "pinhole": True}, {"x": 50, "y": 5, "pinhole": True},
                      {"x": 6, "y": 6, "pinhole": False}],
        "_slivers_at": [(10, 10, 3.0, 0.4)],
        "_flips_at": [(70, 70)],
        "_wobble_at": [(4, 4, 2.5), (5, 5, 1.0)],
    }
    got = focus.region_defects(card, (0, 0, 32, 32))
    assert got == {"pinholes": 1, "slivers": 1, "inflections": 0, "wobble": 3.5}
