"""The bench-only engines (bench.adapters), the held-out corpus and the head-to-head driver."""
from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from bench import adapters
from bench.adapters import autotrace as autotrace_adapter
from bench.adapters import imagetracer as imagetracer_adapter
from bench.corpus import load_corpus
from studi0trace.engines import registry
from studi0trace.imaging.intake import load_upload

from .conftest import black_square_on_transparent

HELDOUT = Path(__file__).resolve().parents[1] / "bench" / "heldout"
CORPUS = Path(__file__).resolve().parents[1] / "bench" / "corpus"


def _image(data: bytes):
    return load_upload(data, max_bytes=1 << 26, max_pixels=1 << 24)


def _rgba(svg: str, size: int) -> np.ndarray:
    from bench.raster import rasterize

    return rasterize(svg, size, size)


def test_the_app_never_registers_the_adapters():
    import subprocess
    import sys

    probe = ("import sys; from studi0trace.engines import registry; registry.load_builtin(); import studi0trace.main; "
             "print(sorted(registry.ids()), any(m.startswith('bench') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True,
                         cwd=Path(__file__).resolve().parents[1]).stdout.strip()
    assert out == "['potrace', 'vexel', 'vtracer'] False"
    src = (Path(__file__).resolve().parents[1] / "studi0trace").rglob("*.py")
    assert not any("bench" in line and "import" in line for p in src for line in p.read_text().splitlines()
                   if "bench.adapters" in line)


def test_a_missing_tool_is_skipped_not_failed(monkeypatch, tmp_path):
    from bench.adapters import autotrace
    from bench.runner import load_results, run
    from bench.synth import generate

    monkeypatch.setattr(autotrace, "BINARY", "no-such-autotrace-binary")
    missing = adapters.load()
    assert "autotrace" in missing and "no-such-autotrace-binary" in missing["autotrace"]
    with pytest.raises(registry.UnknownEngine):
        registry.get("autotrace")
    root = tmp_path / "c"
    generate(root, seed=3, sizes=(32,))
    results = load_results(run(load_corpus(root, ids=["logo/ring-32"]), ["potrace", "autotrace"],
                               out_dir=tmp_path / "r", media=False))
    assert set(results["engines"]) == {"potrace"}
    assert all(r["engine"] == "potrace" for r in results["items"])
    monkeypatch.undo()
    adapters.load()


@pytest.mark.skipif(autotrace_adapter.ENGINE.missing() is not None, reason="autotrace not installed")
def test_autotrace_traces_colour_and_flattens_transparency():
    engine = autotrace_adapter.ENGINE
    res = engine.trace(_image(black_square_on_transparent(64, 16)), engine.Params(color_count=4))
    assert 'viewBox="0 0 64 64"' in res.svg and "<path" in res.svg
    out = _rgba(res.svg, 64)
    # the transparent border must come back white (flattened), not as AutoTrace's black square
    assert out[2, 2, :3].min() > 200 and out[32, 32, :3].max() < 60


@pytest.mark.skipif(imagetracer_adapter.ENGINE.missing() is not None, reason="imagetracerjs not installed")
def test_imagetracer_presets_are_reproducible():
    engine = imagetracer_adapter.ENGINE
    img = _image(black_square_on_transparent(48, 12))
    a = engine.trace(img, engine.Params(preset="randomsampling1"))
    b = engine.trace(img, engine.Params(preset="randomsampling1"))
    assert a.svg == b.svg and "<path" in a.svg
    out = _rgba(engine.trace(img, engine.Params()).svg, 48)
    assert out[24, 24, 3] == 255 and out[24, 24, :3].max() < 60 and out[1, 1, 3] < 30


def test_vexel_auto_is_the_rule_over_the_candidates():
    from studi0trace import auto

    adapters.load()
    img = _image(black_square_on_transparent(64, 16))
    res = registry.get("vexel-auto").trace(img, registry.get("vexel-auto").Params())
    vexel = registry.get("vexel")
    ref = auto.reference(img)
    scored = []
    for p in auto.candidates("vexel"):
        s = auto.assess(vexel.trace(img, vexel.Params.model_validate(p.params)).svg, ref)
        scored.append(auto.Scored(p.id, s["delta_e_mean"], s["edge_f1"], s["artifact_index"], int(s["elements"])))
    pick, _ = auto.choose(scored)
    assert res.notes["pick"] == pick.id
    assert res.svg == vexel.trace(img, vexel.Params.model_validate(
        next(p for p in auto.candidates("vexel") if p.id == pick.id).params)).svg


def test_configs_tune_competitors_only():
    fam = adapters.FAMILIES
    assert fam["vexel"] == ["vexel"] and fam["vexel-auto"] == ["vexel-auto"] and fam["potrace"] == ["potrace"]
    assert {"vtracer", "vtracer-poster", "vtracer-photo"} == set(fam["vtracer"])
    assert len(fam["imagetracer"]) == len(adapters.IMAGETRACER_PRESETS)
    assert len(fam["autotrace"]) == 1 + len(adapters.AUTOTRACE_GRID)
    for name, (eid, params) in adapters.CONFIGS.items():
        adapters.load()
        if eid in adapters.missing():
            continue
        registry.get(eid).Params.model_validate(params)  # every documented preset is a valid parameter set


def test_heldout_corpus_is_complete_and_separate():
    items = load_corpus(HELDOUT)
    assert len(items) == 120
    assert {i.cls for i in items} == {"fluent-flat", "fluent-color", "noto"}
    assert all(i.png.exists() and i.truth_svg and i.truth_svg.exists() and i.width == i.height == 512 for i in items)
    assert sum(i.id.endswith("-ds") for i in items) == sum(i.id.endswith("-q75") for i in items) == 40
    sources = json.loads((HELDOUT / "sources.json").read_text())
    assert len(sources["files"]) == 40 and all(len(r["sha"]) == 40 for r in sources["repos"].values())
    import hashlib

    for f in sources["files"]:
        assert hashlib.sha256((HELDOUT / f["truth_svg"]).read_bytes()).hexdigest() == f["sha256"]
    dev = {p.name for p in CORPUS.rglob("*.svg")}
    assert not dev & {p.name for p in HELDOUT.rglob("*.svg")}


def test_heldout_rasters_are_built_like_the_corpus():
    from bench.synth import render_png

    item = load_corpus(HELDOUT, ids=["noto/u1f315-512"])[0]
    assert item.png.read_bytes() == render_png(item.truth_svg.read_text(encoding="utf-8"), 512)
    q75 = load_corpus(HELDOUT, ids=["noto/u1f315-512-q75"])[0]
    rgba = Image.open(item.png).convert("RGBA")
    flat = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba).convert("RGB")
    buf = io.BytesIO()
    flat.save(buf, "JPEG", quality=75)
    assert q75.png.read_bytes() == buf.getvalue()


def test_outline_truth_cache_changes_nothing():
    from bench import geometry

    truth = (HELDOUT / "noto" / "u1f315.svg").read_text(encoding="utf-8")
    other = (HELDOUT / "noto" / "u1f3f3-200d-26a7.svg").read_text(encoding="utf-8")
    geometry._TRUTH_CACHE.clear()
    a = geometry.outline_error(truth, other, 64, 64)
    b = geometry.outline_error(truth, other, 64, 64)  # served from the cache
    geometry._TRUTH_CACHE.clear()
    c = geometry.outline_error(truth, other, 64, 64)
    assert a == b == c


def test_headtohead_summary_picks_best_on_the_first_corpus(tmp_path):
    from bench.headtohead import best_configs, summarize_dirs

    def rec(cfg, eid, item, score, de):
        return {"config": cfg, "engine_id": eid, "engine": cfg, "id": item, "cls": "flat",
                "metrics": {"score": score, "delta_e_mean": de, "outline_px": de, "artifact_index": de,
                            "pinholes": 0, "slivers": 0, "degenerate": 0, "thin_strokes": 0, "wobble_deg_100px": 0,
                            "radius_inconsistent": 0, "rect_bowed": 0, "rect_skewed": 0}}

    held = [rec("vexel", "vexel", "a", 0.9, 0.1), rec("vtracer", "vtracer", "a", 0.8, 0.5),
            rec("vtracer-photo", "vtracer", "a", 0.85, 0.4)]
    dev = [rec("vexel", "vexel", "b", 0.9, 0.1), rec("vtracer", "vtracer", "b", 0.88, 0.2),
           rec("vtracer-photo", "vtracer", "b", 0.7, 0.9)]
    for name, recs in (("heldout", held), ("corpus", dev)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "records.jsonl").write_text("\n".join(json.dumps(r) for r in recs))
    by = {c: [r for r in held if r["config"] == c] for c in ("vexel", "vtracer", "vtracer-photo")}
    assert best_configs(by)["vtracer"]["best"] == "vtracer-photo"
    report = summarize_dirs([tmp_path / "heldout", tmp_path / "corpus"])
    assert report["choice"]["vtracer"]["best"] == "vtracer-photo"  # chosen on held-out, kept on the corpus
    assert report["corpora"]["corpus"]["best_on_this_corpus"]["vtracer"] == "vtracer"
    assert report["corpora"]["corpus"]["wins"]["vexel"]["delta_e_mean"]["wins"]["vexel"] == 1


def test_runner_workers_give_the_same_records(tmp_path):
    from bench.runner import load_results, run
    from bench.synth import generate

    root = tmp_path / "c"
    generate(root, seed=4, sizes=(32,))
    items = load_corpus(root, ids=["logo/ring-32", "flat/mosaic-32", "shadow/disc-32"])
    one = load_results(run(items, ["potrace", "vtracer"], out_dir=tmp_path / "a", media=False))
    two = load_results(run(items, ["potrace", "vtracer"], out_dir=tmp_path / "b", media=False, workers=2))
    strip = lambda rs: sorted(((r["id"], r["engine"], {k: v for k, v in r["metrics"].items() if k != "elapsed_ms"})
                               for r in rs["items"]), key=lambda t: t[:2])
    assert strip(one) == strip(two)
