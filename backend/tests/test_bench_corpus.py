from pathlib import Path

import numpy as np

from bench.corpus import Item, load_corpus, write_manifest
from bench.raster import load_png
from bench.synth import TEMPLATES, generate


def test_generate_is_complete_and_deterministic(tmp_path: Path):
    items = generate(tmp_path, seed=7, sizes=(64, 32))
    n_templates = sum(len(v) for v in TEMPLATES.values())
    assert len(items) == n_templates * 2 == 48
    assert all(i.png.exists() and i.truth_svg.exists() for i in items)

    loaded = load_corpus(tmp_path)
    assert len(loaded) == 48
    assert {i.cls for i in loaded} == {"logo", "flat", "gradient", "shadow"}
    assert sum(1 for i in loaded if i.cls == "logo") == 12

    first = {i.id: i.png.read_bytes() for i in items}
    generate(tmp_path, seed=7, sizes=(64, 32))
    assert {i.id: i.png.read_bytes() for i in load_corpus(tmp_path)} == first


def test_filters(tmp_path: Path):
    generate(tmp_path, seed=1, sizes=(32,))
    assert len(load_corpus(tmp_path, classes=["logo"])) == 6
    assert [i.id for i in load_corpus(tmp_path, ids=["flat/mosaic-32"])] == ["flat/mosaic-32"]


def test_items_have_truth_and_expected_pixels(tmp_path: Path):
    items = {i.id: i for i in generate(tmp_path, seed=3, sizes=(64,))}
    ring = items["logo/ring-64"]
    assert ring.truth_paths == 0  # circles, not paths - fine, path_ratio is None then
    px = load_png(ring.png)
    assert px.shape == (64, 64, 4)
    assert px[0, 0, 3] == 0, "logo backgrounds are transparent"
    assert px[32, 32, 3] == 255

    grad = load_png(items["gradient/linear-frame-64"].png)
    assert not np.array_equal(grad[32, 0, :3], grad[32, 63, :3]), "gradient should vary across the frame"

    shadow = load_png(items["shadow/disc-64"].png)
    assert shadow[..., 3].min() == 255, "shadow items on white are opaque"


def test_real_entries_are_preserved(tmp_path: Path):
    real = tmp_path / "real" / "logo"
    real.mkdir(parents=True)
    (real / "acme.png").write_bytes(b"")
    write_manifest(tmp_path, [Item(id="logo/acme", cls="logo", png=real / "acme.png", width=10, height=10, tags=["real"])])
    generate(tmp_path, seed=1, sizes=(32,))
    ids = {i.id for i in load_corpus(tmp_path)}
    assert "logo/acme" in ids and len(ids) == 25
