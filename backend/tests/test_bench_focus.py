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


def _disc_asset(tmp_path):
    from PIL import Image

    asset = tmp_path / "disc"
    asset.mkdir()
    rgb = _disc(20.0)
    Image.fromarray(np.dstack([rgb, np.full(rgb.shape[:2], 255, np.uint8)]), "RGBA").save(asset / "source.png")
    (asset / "focus.yaml").write_text("regions:\n  left: [0, 0, 32, 64]\npreset: balanced\nparams: {}\n")
    return asset


def test_run_writes_a_complete_run_dir(tmp_path):
    asset = _disc_asset(tmp_path)
    run_dir = focus.run(asset, backend="rust", label="t")
    for name in ("trace.svg", "focus.json", "sheet.png", "flip.html"):
        assert (run_dir / name).exists(), name
    import json
    data = json.loads((run_dir / "focus.json").read_text())
    assert set(data["regions"]) == {"left"} and data["whole"]["delta_e_mean"] < 2.0
    assert {"edge_off_frac", "edge_p99_px", "fill_de_mean", "fill_de_p95"} <= set(data["regions"]["left"])
    assert "misses" in data
    focus.pin(asset, run_dir)
    assert (asset / "baseline.json").exists()


def test_run_auto_keeps_autos_pick(tmp_path):
    import json

    asset = _disc_asset(tmp_path)
    run_dir = focus.run(asset, backend="rust", label="a", auto=True)
    assert (run_dir / "trace.svg").exists() and (run_dir / "focus.json").exists()
    assert json.loads((run_dir / "focus.json").read_text())["what"].startswith("auto→")


def _square(col=(30, 40, 80), w=64, lo=16, hi=48) -> np.ndarray:
    a = np.full((w, w, 3), 255, np.uint8)
    a[lo:hi, lo:hi] = col
    return a


BOX = (0, 0, 64, 64)


def test_halo_and_sharpening_rim_are_forgiven():
    src = _square()
    src[15, 15:49] = src[48, 15:49] = 200          # light halo just outside
    src[15:49, 15] = src[15:49, 48] = 200
    src[16, 16:48] = src[47, 16:48] = 5            # dark rim just inside
    src[16:48, 16] = src[16:48, 47] = 5
    out = _square()
    es, eo = focus.shape_edges(src), focus.shape_edges(out)
    assert focus.edge_fit(es, eo, BOX)["edge_off_frac"] == 0.0
    assert focus.fill_de(src, out, es, eo, BOX)["fill_de_mean"] < 0.5


def test_cut_corner_is_an_edge_miss_where_it_is():
    out = _square().copy()
    for i in range(6):
        out[16 + i, 16:22 - i] = 255
    es, eo = focus.shape_edges(_square()), focus.shape_edges(out)
    assert focus.edge_fit(es, eo, BOX)["edge_off_frac"] > 0.02
    x, y, n = focus.edge_misses(es, eo)[0]
    assert abs(x - 18) < 3 and abs(y - 18) < 3 and n > 0


def test_wrong_fill_is_a_fill_error_not_an_edge_error():
    src, out = _square(col=(30, 40, 80)), _square(col=(45, 55, 105))
    es, eo = focus.shape_edges(src), focus.shape_edges(out)
    assert focus.edge_fit(es, eo, BOX)["edge_off_frac"] == 0.0
    assert focus.fill_de(src, out, es, eo, BOX)["fill_de_p95"] > 3.0
