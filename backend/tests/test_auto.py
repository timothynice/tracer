"""Auto's rule: the cleanest of the candidates as faithful as the best."""
from __future__ import annotations

from studi0trace.auto import DE_SLACK, Scored, choose, faithful, issues, summary


def S(id, de, f1=0.99, art=0.0, el=10):  # noqa: N802 - reads like a row of the table
    return Scored(id, de, f1, art, el)


def test_picks_the_lowest_artifact_index_within_the_fidelity_band():
    pick, why = choose([S("balanced", 0.40, art=12.0), S("logo", 0.50, art=3.0), S("dense", 0.52, art=1.0)])
    assert pick.id == "dense" and why == "the cleanest at the same fidelity"


def test_never_picks_outside_the_band_however_clean():
    # best 0.40: the band is 0.40 + max(0.15, 0.12) = 0.55
    pick, why = choose([S("balanced", 0.40, art=12.0), S("dense", 0.56, art=0.0)])
    assert pick.id == "balanced"
    assert why == "the only one this faithful to the image"


def test_the_band_widens_with_the_best_delta_e():
    # best 1.0: the band is 1.0 + max(0.15, 0.30) = 1.30
    assert choose([S("a", 1.0, art=9.0), S("b", 1.29, art=1.0)])[0].id == "b"
    assert choose([S("a", 1.0, art=9.0), S("b", 1.31, art=1.0)])[0].id == "a"
    assert DE_SLACK == 0.15


def test_edge_f1_must_be_within_two_hundredths_of_the_best_faithful_one():
    ok = faithful([S("a", 0.40, f1=0.99), S("b", 0.45, f1=0.96), S("c", 0.45, f1=0.975)])
    assert [s.id for s in ok] == ["a", "c"]


def test_a_tie_goes_to_fewer_shapes_then_to_the_earlier_preset():
    pick, why = choose([S("balanced", 0.40, art=2.0, el=20), S("logo", 0.42, art=2.04, el=12)])
    assert pick.id == "logo" and "fewer shapes" in why
    assert choose([S("balanced", 0.40, art=2.0, el=12), S("logo", 0.42, art=2.0, el=12)])[0].id == "balanced"


def test_reasons_read_as_the_end_of_one_sentence():
    assert choose([S("a", 0.40, art=0.0), S("b", 0.45, art=5.0)])[1] == "the most faithful, and the cleanest"
    # the most faithful wins because the cleaner one lost detail
    assert choose([S("a", 0.40, art=4.0), S("b", 0.45, art=5.0), S("c", 0.90, art=0.0)])[1] == \
        "the most faithful; the cleaner ones lose detail"
    assert choose([S("a", 0.40)])[1] == "the only candidate that traced"
    assert choose([]) == (None, "no candidate could be scored")


def test_issues_name_what_a_designer_would_circle():
    card = {"pinholes": 3, "slivers": 1, "degenerate": 0, "thin_strokes": 0, "wobble_deg_100px": 40.0,
            "radius_inconsistent": 1, "rect_bowed": 0, "rect_skewed": 0, "inflections": 0}
    assert issues(card) == ["3 pinholes", "1 sliver", "wobbly edges", "1 uneven rectangle"]
    clean = dict(card, pinholes=0, slivers=0, wobble_deg_100px=3.0, radius_inconsistent=0)
    assert issues(clean) == []
    s = summary({**clean, "delta_e_mean": 0.41234, "edge_f1": 0.98, "artifact_index": 0.75, "elements": 9})
    assert s["clean"] and s["shapes"] == 9 and s["delta_e"] == 0.4123
