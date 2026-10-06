"""bench.gate: per-item regressions between two runs."""
import json

from bench import gate


def _run(**per_item):
    return {"items": [{"id": i, "cls": "logo", "engine": "vexel", "metrics": m} for i, m in per_item.items()]}


BASE = {"score": 0.95, "delta_e_mean": 0.30, "artifact_index": 1.0, "pinholes": 0, "slivers": 0,
        "thin_strokes": 0, "seam_ppm": 1000.0, "outline_px": 0.20, "wobble_deg_100px": 10.0}


def test_identical_runs_pass():
    regs, imps, missing = gate.gate(_run(a=BASE), _run(a=BASE))
    assert (regs, imps, missing) == ([], [], [])


def test_one_item_regression_is_caught_even_if_another_improves():
    worse = {**BASE, "score": 0.94}
    better = {**BASE, "score": 0.97}
    regs, imps, _ = gate.gate(_run(a=BASE, b=BASE), _run(a=worse, b=better))
    assert len(regs) == 1 and regs[0].startswith("a ") and "score" in regs[0]
    assert len(imps) == 1 and imps[0].startswith("b ")


def test_counts_regress_on_any_increase():
    regs, _, _ = gate.gate(_run(a=BASE), _run(a={**BASE, "slivers": 1}))
    assert any("slivers" in r for r in regs)


def test_missing_truth_metric_is_skipped_not_failed():
    no_truth = {**BASE, "outline_px": None}
    regs, _, _ = gate.gate(_run(a=no_truth), _run(a=no_truth))
    assert regs == []


def test_errored_item_is_missing(tmp_path):
    ref = _run(a=BASE)
    cand = {"items": [{"id": "a", "cls": "logo", "engine": "vexel", "error": "boom"}]}
    (tmp_path / "r.json").write_text(json.dumps(ref))
    (tmp_path / "c.json").write_text(json.dumps(cand))
    assert gate.main([str(tmp_path / "r.json"), str(tmp_path / "c.json")]) == 1
