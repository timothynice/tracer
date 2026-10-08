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


def test_no_items_for_engine_returns_2(tmp_path):
    ref = _run(a=BASE)
    cand = _run(a=BASE)
    (tmp_path / "r.json").write_text(json.dumps(ref))
    (tmp_path / "c.json").write_text(json.dumps(cand))
    assert gate.main([str(tmp_path / "r.json"), str(tmp_path / "c.json"), "--engine", "vexl"]) == 2


def test_candidate_metric_none_is_regression():
    cand_no_outline = {**BASE, "outline_px": None}
    regs, _, _ = gate.gate(_run(a=BASE), _run(a=cand_no_outline))
    assert any("outline_px" in r and "0.2000 →" in r for r in regs)


def test_candidate_metric_nan_is_regression():
    cand_nan = {**BASE, "outline_px": float("nan")}
    regs, _, _ = gate.gate(_run(a=BASE), _run(a=cand_nan))
    assert any("outline_px" in r and "0.2000 →" in r for r in regs)


def test_subset_run_only_checks_its_own_items():
    regs, _, missing = gate.gate(_run(a=BASE, b=BASE), _run(a=BASE))
    assert regs == [] and missing == []


def _items(**per_item):
    return {"items": [{"id": i, "cls": i.split("/")[0], "engine": "vexel", "metrics": m} for i, m in per_item.items()]}


def test_by_class_counts_items_not_metrics():
    ref = _items(**{"nn2x/a": BASE, "nn2x/b": BASE, "nn2x/c": BASE, "small/d": BASE, "small/e": BASE})
    cand = _items(**{
        "nn2x/a": {**BASE, "score": 0.97, "artifact_index": 0.5},   # two metrics better: one improved item
        "nn2x/b": {**BASE, "score": 0.97, "slivers": 1},            # better and worse: regressed
        "nn2x/c": BASE,
        "small/d": {**BASE, "outline_px": 0.30},
        "small/e": BASE,
    })
    assert gate.by_class(ref, cand) == {
        "nn2x": {"improved": 1, "regressed": 1, "same": 1},
        "small": {"improved": 0, "regressed": 1, "same": 1},
    }


def test_by_class_counts_an_errored_item_as_regressed():
    ref = _items(**{"combo/a": BASE})
    cand = {"items": [{"id": "combo/a", "cls": "combo", "engine": "vexel", "error": "boom"}]}
    assert gate.by_class(ref, cand) == {"combo": {"improved": 0, "regressed": 1, "same": 0}}


def test_by_class_flag_prints_the_table(tmp_path, capsys):
    ref = _items(**{"nn2x/a": BASE})
    cand = _items(**{"nn2x/a": {**BASE, "score": 0.97}})
    (tmp_path / "r.json").write_text(json.dumps(ref))
    (tmp_path / "c.json").write_text(json.dumps(cand))
    assert gate.main([str(tmp_path / "r.json"), str(tmp_path / "c.json"), "--by-class"]) == 0
    out = capsys.readouterr().out
    assert "class" in out and "nn2x" in out and "        1         0     0" in out


def _files(tmp_path):
    ref = _items(**{"nn2x/a": BASE, "nn2x/b": BASE})
    cand = _items(**{
        "nn2x/a": {**BASE, "score": 0.97, "slivers": 1},   # better score, worse slivers
        "nn2x/b": {**BASE, "seam_ppm": 5000.0},            # worse seam only
    })
    (tmp_path / "r.json").write_text(json.dumps(ref))
    (tmp_path / "c.json").write_text(json.dumps(cand))
    return [str(tmp_path / "r.json"), str(tmp_path / "c.json")]


def test_metrics_restricts_what_is_judged():
    ref = _items(**{"a": BASE})
    cand = _items(**{"a": {**BASE, "slivers": 1, "score": 0.97}})
    assert gate.gate(ref, cand, metrics=["score"]) == ([], [f"a score: 0.9500 → 0.9700"], [])
    regs, imps, _ = gate.gate(ref, cand, metrics=["slivers", "outline_px"])
    assert len(regs) == 1 and "slivers" in regs[0] and imps == []


def test_metrics_changes_the_by_class_counts():
    ref = _items(**{"nn2x/a": BASE, "nn2x/b": BASE})
    cand = _items(**{"nn2x/a": {**BASE, "score": 0.97, "slivers": 1}, "nn2x/b": {**BASE, "seam_ppm": 5000.0}})
    assert gate.by_class(ref, cand) == {"nn2x": {"improved": 0, "regressed": 2, "same": 0}}
    assert gate.by_class(ref, cand, metrics=["score", "delta_e_mean"]) == {"nn2x": {"improved": 1, "regressed": 0, "same": 1}}


def test_metrics_flag_with_and_without_by_class(tmp_path, capsys):
    files = _files(tmp_path)
    assert gate.main(files) == 1
    assert gate.main([*files, "--metrics", "score,delta_e_mean"]) == 0
    capsys.readouterr()
    assert gate.main([*files, "--metrics", "score, slivers", "--by-class"]) == 1
    out = capsys.readouterr().out
    assert "slivers" in out and "seam_ppm" not in out and "nn2x" in out
    assert gate.main([*files, "--metrics", "score,delta_e_mean", "--by-class"]) == 0
    assert "        1         0     1" in capsys.readouterr().out


def test_metrics_flag_rejects_unknown_names(tmp_path, capsys):
    files = _files(tmp_path)
    assert gate.main([*files, "--metrics", "score,bogus"]) == 2
    err = capsys.readouterr().err
    assert "bogus" in err and "score," in err
    assert gate.main([*files, "--metrics", "bogus", "--by-class"]) == 2
