"""bench.degraded: the degraded bench set (degradations, the wave-lockup stand-in, generate)."""
import random

import numpy as np
import pytest

from bench import degraded as dg

SQUARE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
          '<rect x="0" y="0" width="40" height="64" fill="#3366cc"/>'
          '<circle cx="48" cy="32" r="10" fill="#cc3333" fill-opacity="0.5"/></svg>')
WIDE = ('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="10" viewBox="0 0 100 50">'
        '<rect x="10" y="10" width="80" height="30" fill="#222"/></svg>')


def _ramp(h: int, w: int) -> np.ndarray:
    a = np.zeros((h, w, 4), np.uint8)
    a[..., 0] = (np.arange(h)[:, None] * 7 + np.arange(w)[None, :] * 3) % 256
    a[..., 1] = (np.arange(w)[None, :] * 11) % 256
    a[..., 2] = 40
    a[..., 3] = 255
    return a


def test_nn2x_phase_zero_doubles_every_pixel():
    n = _ramp(5, 4)
    out = dg.nn2x(n, 0)
    assert out.shape == (10, 8, 4)
    assert np.array_equal(out[0::2], out[1::2])
    assert np.array_equal(out[:, 0::2], out[:, 1::2])
    assert np.array_equal(out[::2, ::2], n)


def test_nn2x_phase_one_pairs_rows_from_the_second():
    n = _ramp(6, 4)  # one extra native row, as render(..., row_phase=1) gives
    out = dg.nn2x(n, 1)
    assert out.shape == (10, 8, 4)
    assert np.array_equal(out[1:9:2], out[2:10:2])  # rows (2k+1, 2k+2)
    assert not np.array_equal(out[0], out[1])
    assert np.array_equal(out[0], np.repeat(n[0], 2, axis=0))
    assert np.array_equal(out[9], np.repeat(n[5], 2, axis=0))
    assert np.array_equal(out[:, 0::2], out[:, 1::2])


def test_render_is_exact_and_phase_one_keeps_the_geometry():
    assert dg.fit(WIDE, 176) == (176, 88)
    img = dg.render(WIDE, 176, 88)
    assert img.shape == (88, 176, 4)  # the root's width/height are ignored
    half = dg.render(SQUARE, 32, 32, row_phase=1)
    assert half.shape == (33, 32, 4)
    full = dg.render(SQUARE, 64, 64)
    doubled = dg.nn2x(half, 1)
    # the circle's alpha centroid lands where the full-size render has it
    def centroid(a):
        w = a[..., 3].astype(float) * (a[..., 0] > a[..., 2])
        ys, xs = np.mgrid[:a.shape[0], :a.shape[1]]
        return (w * ys).sum() / w.sum(), (w * xs).sum() / w.sum()
    (y1, x1), (y2, x2) = centroid(doubled), centroid(full)
    assert abs(y1 - y2) < 0.1 and abs(x1 - x2) < 0.1


def test_unsharp_rims_dark_inside_and_halos_light_outside():
    a = np.full((32, 32, 4), 220, np.uint8)
    a[..., 3] = 255
    a[8:24, 8:24, :3] = 40
    a[0, 0, 3] = 7
    out = dg.unsharp(a)
    assert out[16, 8, 0] < 40 and out[16, 7, 0] > 220
    assert out[16, 16, 0] == 40 and out[16, 30, 0] == 220
    assert np.array_equal(out[..., 3], a[..., 3])


def test_small_has_the_long_side_176():
    out = dg.degrade("small", WIDE, 176, 0, random.Random(0))
    assert out.shape == (88, 176, 4)


def test_combo_is_doubled_at_phase_one_and_noisy_only_where_opaque():
    out = dg.degrade("combo", SQUARE, 128, 1, random.Random("t"))
    assert out.shape == (128, 128, 4)
    assert np.array_equal(out[1:127:2], out[2:128:2])
    assert np.array_equal(out[:, 0::2], out[:, 1::2])
    clean = dg.nn2x(dg.unsharp(dg.render(SQUARE, 64, 64, row_phase=1)), 1)
    assert np.array_equal(out[..., 3], clean[..., 3])
    diff = out[..., :3].astype(int) - clean[..., :3]
    opaque = clean[..., 3] == 255
    assert np.abs(diff).max() <= 1
    assert (diff[~opaque] == 0).all() and (diff[opaque] != 0).any()


def test_ground_noise_is_seeded():
    a = _ramp(40, 40)
    one = dg.ground_noise(a, random.Random("s"), 20.0)
    assert np.array_equal(one, dg.ground_noise(a, random.Random("s"), 20.0))
    assert not np.array_equal(one, a)


def test_unknown_class_is_refused():
    with pytest.raises(ValueError):
        dg.degrade("blur", SQUARE, 64, 0, random.Random(0))


def test_wave_lockup_stand_in_parses_and_renders():
    import xml.etree.ElementTree as ET

    svg = dg.wave_lockup_svg()
    root = ET.fromstring(svg)
    assert root.get("viewBox") == "0 0 1208 308"
    tags = [el.tag.split("}")[-1] for el in root.iter()]
    assert "text" not in tags and "image" not in tags  # shapes only
    assert tags.count("linearGradient") == 2 and tags.count("stop") == 8
    img = dg.render(svg, 1208, 308)
    assert img.shape == (308, 1208, 4)
    assert tuple(img[2, 2]) == (254, 254, 254, 255)
    rgb = img[..., :3].astype(int)

    def mask(colour):
        return np.abs(rgb - colour).sum(-1) <= 6

    # The ten small shapes sit in a row at the top left, and only there.
    small = mask((0xE0, 0x70, 0x3A))
    assert small[36:64, :720].sum() > 900
    assert small.sum() == small[36:64, :720].sum()
    # The thin shapes (6 px strokes) and the heavy ones (16 px stems) are below them, on the left.
    ink = mask((0x2B, 0x2B, 0x2B))
    assert ink[95:185, :720].sum() > 3000 and ink[200:290, :720].sum() > 7000
    assert ink[:, 720:].sum() == 0 and ink[:95].sum() == 0 and ink[185:200].sum() == 0
    # The ribbons are on the right, clear of the shapes.
    assert (rgb[:, 740:] < 250).any(-1).sum() > 19000
    assert (rgb[:, 725:760] >= 250).all()
    # the 7 px channel: a run of background between the two ribbons at x 996
    col = rgb[:, 996]
    y = 20
    while (col[y] >= 250).all():  # background above the light ribbon
        y += 1
    while not (col[y] >= 250).all():  # the light ribbon (#e8c65c-ish here) and its rim
        assert col[y, 1] > 170, "the light ribbon crosses x 996"
        y += 1
    run = 0
    while (col[y + run] >= 250).all():
        run += 1
    assert 6 <= run <= 8
    assert col[y + run + 2, 1] < 170 and col[y + run + 2, 0] > 150  # then the dark ribbon


def test_wave_lockup_ribbons_keep_a_channel_and_sharp_tips():
    img = dg.render(dg.wave_lockup_svg(), 1208, 308)
    ink = (img[..., :3].astype(int) < 250).any(-1)

    def runs(x):
        ys = np.flatnonzero(ink[:, x])
        return np.split(ys, np.flatnonzero(np.diff(ys) > 1) + 1) if len(ys) else []

    # Between the tips (x 872-1084, where both ribbons are present) the background gap is 6-7 px.
    for x in range(872, 1085):
        r = runs(x)
        assert len(r) == 2, x
        assert 6 <= r[1][0] - r[0][-1] - 1 <= 7, x
    # The four tips (light 788 / 1086, dark 869 / 1159) are 2 px or less across at their outermost three columns.
    for xs, pick in (((788, 789, 790), 0), ((1083, 1084, 1085), 0), ((869, 870, 871), -1), ((1156, 1157, 1158), -1)):
        for x in xs:
            assert len(runs(x)[pick]) <= 2, x


def test_generate_writes_52_items_byte_identically(tmp_path):
    from bench.corpus import load_corpus

    items = dg.generate(tmp_path / "a", seed=1234)
    assert len(items) == len(dg.SOURCES) * len(dg.CLASSES) == 52
    dg.generate(tmp_path / "b", seed=1234)
    files_a = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(tmp_path / "b") for p in (tmp_path / "b").rglob("*") if p.is_file())
    assert files_a == files_b and len(files_a) == 52 + 52 + 1 + 1 + 2  # PNGs, truths, manifest, SOURCES.md, licences
    for rel in files_a:
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), rel

    loaded = load_corpus(tmp_path / "a")
    assert len(loaded) == 52
    assert all(i.truth_svg is not None and i.truth_svg.exists() and i.png.exists() for i in loaded)
    assert {i.cls for i in loaded} == set(dg.CLASSES)
    by_id = {i.id: i for i in loaded}
    combo = by_id["combo/wave-lockup-1208"]
    assert (combo.width, combo.height) == (1208, 308)
    assert combo.tags == ["degraded", "degraded:combo", "source:synthetic", "size:1208"]
    assert (by_id["small/venn-176"].width, by_id["small/venn-176"].height) == (176, 176)
    assert "source:heldout" in by_id["sharpen/u2049-512"].tags
    assert (by_id["nn2x/logomark-512"].width, by_id["nn2x/logomark-512"].height) == (512, 484)


def test_nn2x_row_phase_follows_the_source_index(tmp_path):
    from bench.raster import load_png

    dg.generate(tmp_path, seed=1234)

    def row_phase(a):
        for p in (0, 1):
            m = (a.shape[0] - p) // 2
            if np.array_equal(a[p:p + 2 * m:2], a[p + 1:p + 2 * m:2]):
                return p
        return None

    for index, src in enumerate(dg.SOURCES):
        nn = load_png(tmp_path / "nn2x" / f"{src.name}-{src.long_side}.png")
        combo = load_png(tmp_path / "combo" / f"{src.name}-{src.long_side}.png")
        assert row_phase(nn) == index % 2, src.name
        assert row_phase(combo) == 1, src.name
        assert row_phase(nn.transpose(1, 0, 2)) == 0 and row_phase(combo.transpose(1, 0, 2)) == 0


def test_every_sentinel_names_an_item():
    from bench.corpus import load_corpus

    lines = [ln for ln in (dg.HERE / "sentinels.txt").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert any(ln.split()[0] == "degraded" for ln in lines)
    known: dict[str, set[str]] = {}
    for ln in lines:
        name, item = ln.split()[:2]
        if name not in known:
            known[name] = {i.id for i in load_corpus(dg.HERE / name)}
        assert item in known[name], ln


def test_the_committed_set_is_current(tmp_path):
    """Everything but the PNGs (resvg's output may drift between versions) is what `generate` writes now."""
    dg.generate(tmp_path, seed=1234)
    fresh = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file() and p.suffix != ".png")
    committed = sorted(p.relative_to(dg.DEFAULT_OUT) for p in dg.DEFAULT_OUT.rglob("*")
                       if p.is_file() and p.suffix != ".png")
    assert fresh == committed
    assert {"manifest.yaml", "SOURCES.md"} <= {p.as_posix() for p in fresh}
    assert any(p.parts[0] == "LICENSES" for p in fresh)
    assert sum(p.suffix == ".svg" for p in fresh) == 52
    for rel in fresh:
        assert (tmp_path / rel).read_bytes() == (dg.DEFAULT_OUT / rel).read_bytes(), (
            f"{rel} is stale: run `python -m bench.degraded generate` and commit the result")
