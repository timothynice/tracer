import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bench.corpus import load_corpus
from bench.runner import compare, load_results, run
from bench.synth import generate


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("corpus")
    generate(root, seed=5, sizes=(48,))
    return root


def test_run_writes_results_and_report(corpus: Path, tmp_path: Path):
    items = load_corpus(corpus, ids=["logo/ring-48", "gradient/linear-frame-48"])
    out = tmp_path / "r"
    results_path = run(items, ["potrace", "vtracer"], label="t", out_dir=out)
    assert results_path == out / "results.json"
    results = load_results(results_path)

    assert results["label"] == "t"
    assert set(results["engines"]) == {"potrace", "vtracer"}
    assert results["engines"]["potrace"]["params"]["threshold"] == 128
    assert len(results["items"]) == 4
    assert all("metrics" in r for r in results["items"]), [r.get("error") for r in results["items"]]
    assert results["weights"]["w_ssim"] == 0.35

    summary = results["summary"]
    assert set(summary["vtracer"]) == {"logo", "gradient"}
    assert summary["vtracer"]["gradient"]["items"] == 1 and summary["vtracer"]["gradient"]["errors"] == 0
    assert 0.0 <= summary["potrace"]["logo"]["score"] <= 1.0

    html = (out / "index.html").read_text()
    assert "logo/ring-48" in html and "data:image/png;base64," in html
    assert (out / "media.json").exists()


def test_params_are_applied_and_recorded(corpus: Path, tmp_path: Path):
    items = load_corpus(corpus, ids=["flat/mosaic-48"])
    results = load_results(run(items, ["vtracer"], params={"vtracer": {"color_precision": 3}}, out_dir=tmp_path / "p", media=False))
    assert results["engines"]["vtracer"]["params"]["color_precision"] == 3
    assert not (tmp_path / "p" / "media.json").exists()


def test_engine_error_is_recorded_not_raised(corpus: Path, tmp_path: Path):
    from pydantic import BaseModel

    from studi0trace.engines import registry

    class Broken:
        id, label, description = "broken", "Broken", ""

        class Params(BaseModel):
            pass

        def trace(self, image, params):
            raise RuntimeError("nope")

    registry.register(Broken())
    try:
        results = load_results(run(load_corpus(corpus, ids=["logo/venn-48"]), ["broken"], out_dir=tmp_path / "e", media=False))
    finally:
        registry.unregister("broken")
    assert results["items"][0]["error"].startswith("RuntimeError")
    assert results["summary"]["broken"]["logo"] == {"items": 1, "errors": 1}


def test_compare_detects_regression():
    base = {"summary": {"vtracer": {"logo": {"score": 0.80}, "flat": {"score": 0.70}}}}
    same = copy.deepcopy(base)
    lines, regressed = compare(base, same)
    assert not regressed and len(lines) == 2

    worse = copy.deepcopy(base)
    worse["summary"]["vtracer"]["logo"]["score"] = 0.75
    lines, regressed = compare(base, worse, tolerance=0.01)
    assert regressed and any("REGRESSION" in l for l in lines)

    better = copy.deepcopy(base)
    better["summary"]["vtracer"]["flat"]["score"] = 0.79
    better["summary"]["potrace"] = {"logo": {"score": 0.5}}
    lines, regressed = compare(base, better)
    assert not regressed
    assert any("improved" in l for l in lines) and any("no baseline" in l for l in lines)


def test_cli_smoke(corpus: Path, tmp_path: Path):
    out = tmp_path / "cli"
    cmd = [sys.executable, "-m", "bench", "run", "--engines", "potrace", "--classes", "logo", "--label", "smoke",
           "--corpus", str(corpus), "--out", str(out), "--no-media"]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert proc.returncode == 0, proc.stderr
    assert "potrace logo" in proc.stdout.replace("  ", " ")
    results = json.loads((out / "results.json").read_text())
    assert len(results["items"]) == len(load_corpus(corpus, classes=["logo"]))

    a, b = out / "results.json", out / "results.json"
    proc = subprocess.run([sys.executable, "-m", "bench", "compare", str(a), str(b)], capture_output=True, text=True,
                          cwd=Path(__file__).resolve().parents[1])
    assert proc.returncode == 0 and proc.stdout.strip().endswith("ok")
