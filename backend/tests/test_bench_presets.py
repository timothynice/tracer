"""The preset list's detail lines come from measured records, with Auto picked by the shipped rule."""
from __future__ import annotations

import json

from bench.presets_report import auto_records, detail_lines, write_details
from studi0trace.engines.presets import all_presets, auto_candidates

CLEAN = {"pinholes": 0, "hole_clusters": 0, "slivers": 0, "degenerate": 0, "thin_strokes": 0, "wobble_deg_100px": 5.0,
         "inflections": 0, "radius_inconsistent": 0, "rect_bowed": 0, "rect_skewed": 0}


def rec(config, item, de, art, elements=10, f1=0.99, **dirty):
    m = {**CLEAN, **dirty, "delta_e_mean": de, "edge_f1": f1, "artifact_index": art, "elements": elements}
    return {"config": config, "id": item, "cls": "logo", "metrics": m}


def corpus() -> list[dict]:
    out = []
    for item, (b, lo, de, d) in {"a": (0.40, 0.45, 0.39, 0.50), "b": (0.30, 0.70, 0.35, 0.80)}.items():
        out += [rec("balanced", item, b, 8.0, pinholes=2), rec("logo", item, lo, 1.0, elements=6),
                rec("detailed", item, de, 9.0, pinholes=3), rec("dense", item, d, 0.5, elements=5)]
        out += [rec("flat", item, 1.2, 50.0, pinholes=9), rec("cutfile", item, 0.6, 20.0, slivers=4)]
    return out


def test_auto_records_are_the_candidate_the_rule_picks():
    picks = {r["id"]: r["pick"] for r in auto_records(corpus())}
    # item a: every candidate within 0.39 + 0.15; dense is the cleanest. item b: only balanced
    # and detailed are within 0.30 + 0.15, and balanced is the cleaner of the two
    assert picks == {"a": "dense", "b": "balanced"}
    partial = [r for r in corpus() if not (r["id"] == "a" and r["config"] == "logo")]
    assert {r["id"] for r in auto_records(partial)} == {"b"}, "an item missing a candidate gets no Auto record"


def test_detail_lines_quote_mean_delta_e_median_shapes_and_the_clean_share():
    records = corpus()
    records += auto_records(records)
    lines, numbers = detail_lines(records)
    assert set(lines) == {p.id for p in all_presets()}
    assert lines["logo"] == "ΔE 0.57 · 6 shapes · clean on 100% of 2 test images"
    assert lines["balanced"] == "ΔE 0.35 · 10 shapes · clean on 0% of 2 test images"
    assert numbers["auto"]["picks"] == {"dense": 50.0, "balanced": 50.0}
    assert {p.id for p in auto_candidates()} == {"balanced", "logo", "detailed", "dense"}


def test_write_details_is_what_the_preset_list_reads(tmp_path):
    records = corpus()
    records += auto_records(records)
    path = write_details(records, 2, tmp_path / "details.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["corpus_items"] == 2 and doc["lines"]["dense"].startswith("ΔE ")
