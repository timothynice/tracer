//! `auto` against `studi0trace/auto.py` and the route that runs it (`api/routes._run_auto`):
//! the seven tests of `backend/tests/test_auto.py` first, then the same rule over 300 random
//! sets of candidates the Python answered (`auto.json`), Python's `round` over a thousand
//! numbers, `issues` and `summary` over their thresholds, and the whole of `auto=true` on real
//! images: the SVG of every candidate to the byte, its scores, the pick and the reason, each as
//! the route sends them. The SVG bytes and the counts read off them are compared where
//! `common::exact` says the engine's floats are the fixtures' (macOS arm64) and not elsewhere,
//! or under `STUDI0TRACE_FORCE_TOLERANT`; the pick, the reason and the scores always are. The
//! failure paths run through [`auto::run_with`], whose tracer a test can replace.
mod common;

use serde_json::{json, Map, Value};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};
use studi0trace_core::auto::{self, AutoError, AutoOutcome, Scored, DE_SHARE, DE_SLACK, EDGE_SLACK};
use studi0trace_core::intake::{self, Image};
use studi0trace_core::presets::{self, Preset};
use vexel_rs::engine::VexelParams;

// ---------------------------------------------------------------- the unit tests of test_auto.py

/// `S(id, de)` of the Python: `f1=0.99, art=0.0, el=10`; the rest by struct update.
#[allow(non_snake_case)]
fn S(id: &str, de: f64) -> Scored {
    Scored { id: id.into(), delta_e: de, edge_f1: 0.99, artifact_index: 0.0, elements: 10 }
}

fn id_of(scored: &[Scored], pick: Option<usize>) -> &str {
    &scored[pick.expect("a pick")].id
}

/// `choose(...)` as the Python tests read it: the pick's id and the reason.
fn choose_id(scored: &[Scored]) -> (&str, &'static str) {
    let (pick, why) = auto::choose(scored);
    (id_of(scored, pick), why)
}

#[test]
fn picks_the_lowest_artifact_index_within_the_fidelity_band() {
    let scored = [
        Scored { artifact_index: 12.0, ..S("balanced", 0.40) },
        Scored { artifact_index: 3.0, ..S("logo", 0.50) },
        Scored { artifact_index: 1.0, ..S("dense", 0.52) },
    ];
    assert_eq!(choose_id(&scored), ("dense", "the cleanest at the same fidelity"));
}

#[test]
fn never_picks_outside_the_band_however_clean() {
    // best 0.40: the band is 0.40 + max(0.15, 0.12) = 0.55
    let scored = [Scored { artifact_index: 12.0, ..S("balanced", 0.40) }, Scored { artifact_index: 0.0, ..S("dense", 0.56) }];
    assert_eq!(choose_id(&scored), ("balanced", "the only one this faithful to the image"));
}

#[test]
fn the_band_widens_with_the_best_delta_e() {
    // best 1.0: the band is 1.0 + max(0.15, 0.30) = 1.30
    let pair = |b: f64| [Scored { artifact_index: 9.0, ..S("a", 1.0) }, Scored { artifact_index: 1.0, ..S("b", b) }];
    assert_eq!(choose_id(&pair(1.29)).0, "b");
    assert_eq!(choose_id(&pair(1.31)).0, "a");
    assert_eq!(DE_SLACK, 0.15);
}

#[test]
fn edge_f1_must_be_within_two_hundredths_of_the_best_faithful_one() {
    let scored = [Scored { edge_f1: 0.99, ..S("a", 0.40) }, Scored { edge_f1: 0.96, ..S("b", 0.45) }, Scored { edge_f1: 0.975, ..S("c", 0.45) }];
    let ok: Vec<&str> = auto::faithful(&scored).into_iter().map(|i| scored[i].id.as_str()).collect();
    assert_eq!(ok, ["a", "c"]);
}

#[test]
fn a_tie_goes_to_fewer_shapes_then_to_the_earlier_preset() {
    let scored = [Scored { artifact_index: 2.0, elements: 20, ..S("balanced", 0.40) }, Scored { artifact_index: 2.04, elements: 12, ..S("logo", 0.42) }];
    let (id, why) = choose_id(&scored);
    assert!(id == "logo" && why.contains("fewer shapes"), "{id}: {why}");
    let scored = [Scored { artifact_index: 2.0, elements: 12, ..S("balanced", 0.40) }, Scored { artifact_index: 2.0, elements: 12, ..S("logo", 0.42) }];
    assert_eq!(choose_id(&scored).0, "balanced");
}

#[test]
fn reasons_read_as_the_end_of_one_sentence() {
    let scored = [S("a", 0.40), Scored { artifact_index: 5.0, ..S("b", 0.45) }];
    assert_eq!(choose_id(&scored).1, "the most faithful, and the cleanest");
    // the most faithful wins because the cleaner one lost detail
    let scored = [
        Scored { artifact_index: 4.0, ..S("a", 0.40) },
        Scored { artifact_index: 5.0, ..S("b", 0.45) },
        Scored { artifact_index: 0.0, ..S("c", 0.90) },
    ];
    assert_eq!(choose_id(&scored).1, "the most faithful; the cleaner ones lose detail");
    assert_eq!(choose_id(&[S("a", 0.40)]).1, "the only candidate that traced");
    assert_eq!(auto::choose(&[]), (None, "no candidate could be scored"));
}

fn card(v: Value) -> Map<String, Value> {
    v.as_object().unwrap().clone()
}

#[test]
fn issues_name_what_a_designer_would_circle() {
    let base = json!({"pinholes": 3, "slivers": 1, "degenerate": 0, "thin_strokes": 0, "wobble_deg_100px": 40.0,
                      "radius_inconsistent": 1, "rect_bowed": 0, "rect_skewed": 0, "inflections": 0});
    assert_eq!(auto::issues(&card(base.clone())), ["3 pinholes", "1 sliver", "wobbly edges", "1 uneven rectangle"]);
    let mut clean = base.clone();
    for (k, v) in [("pinholes", json!(0)), ("slivers", json!(0)), ("wobble_deg_100px", json!(3.0)), ("radius_inconsistent", json!(0))] {
        clean[k] = v;
    }
    assert!(auto::issues(&card(clean.clone())).is_empty());
    for (k, v) in [("delta_e_mean", json!(0.41234)), ("edge_f1", json!(0.98)), ("artifact_index", json!(0.75)), ("elements", json!(9))] {
        clean[k] = v;
    }
    let s = auto::summary(&card(clean));
    assert!(s["clean"] == json!(true) && s["shapes"] == json!(9) && s["delta_e"] == json!(0.4123), "{s}");
}

// ---------------------------------------------------------------- the rule, held to the Python

fn fl(v: &Value) -> f64 {
    match v {
        Value::Number(n) => n.as_f64().unwrap(),
        Value::String(s) => match s.as_str() {
            "nan" => f64::NAN,
            "inf" => f64::INFINITY,
            "-inf" => f64::NEG_INFINITY,
            other => panic!("{other}"),
        },
        other => panic!("{other}"),
    }
}

fn from_bits(v: &Value) -> f64 {
    f64::from_bits(u64::from_str_radix(v.as_str().unwrap().trim_start_matches("0x"), 16).unwrap())
}

fn scored_of(case: &Value) -> Vec<Scored> {
    case["scored"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| Scored { id: r[0].as_str().unwrap().into(), delta_e: fl(&r[1]), edge_f1: fl(&r[2]), artifact_index: fl(&r[3]), elements: r[4].as_u64().unwrap() })
        .collect()
}

#[test]
fn the_constants_are_the_pythons() {
    let want = &common::fixture_json("auto.json")["constants"];
    assert_eq!((DE_SLACK, DE_SHARE, EDGE_SLACK), (want["DE_SLACK"].as_f64().unwrap(), want["DE_SHARE"].as_f64().unwrap(), want["EDGE_SLACK"].as_f64().unwrap()));
}

/// Python's `round(x, n)` is the exact binary value rounded correctly, ties to even. The plan
/// said `0.35 -> 0.4`; the Python says `0.3` (the double nearest 0.35 is 0.34999999999999997).
#[test]
fn round_is_pythons_over_decimals_ties_near_ties_signs_and_the_edges_of_the_range() {
    let golden = common::fixture_json("auto.json");
    let digits: Vec<u32> = golden["round"]["digits"].as_array().unwrap().iter().map(|d| d.as_u64().unwrap() as u32).collect();
    assert_eq!(digits, [0, 1, 2, 4]);
    let same = |got: f64, want: f64| got.to_bits() == want.to_bits() || (got.is_nan() && want.is_nan());
    let mut checked = 0;
    for row in golden["round"]["cases"].as_array().unwrap() {
        let x = from_bits(&row[0]);
        for (i, n) in digits.iter().enumerate() {
            let (got, want) = (auto::py_round(x, *n), from_bits(&row[i + 1]));
            assert!(same(got, want), "round({x:e}, {n}) = {got:e}, the Python says {want:e}");
            checked += 1;
        }
    }
    assert!(checked > 3200, "{checked} roundings");
    for (name, case) in golden["round"]["named"].as_object().unwrap() {
        let (x, n, want) = (from_bits(&case[0]), case[1].as_u64().unwrap() as u32, from_bits(&case[2]));
        assert!(same(auto::py_round(x, n), want), "{name}: round({x:e}, {n})");
    }
    // sign and range, by what the Python does with them
    assert!(auto::py_round(-0.04, 1).is_sign_negative() && auto::py_round(-0.04, 1) == 0.0);
    assert!(auto::py_round(f64::NAN, 1).is_nan());
    assert_eq!(auto::py_round(f64::INFINITY, 2), f64::INFINITY);
    assert_eq!(auto::py_round(f64::NEG_INFINITY, 2), f64::NEG_INFINITY);
}

#[test]
fn choose_and_faithful_answer_as_the_python_does_on_every_random_set() {
    let golden = common::fixture_json("auto.json");
    let cases = golden["choose"].as_array().unwrap();
    assert!(cases.len() >= 300, "{} sets", cases.len());
    let mut picks = std::collections::BTreeMap::<String, usize>::new();
    for (n, case) in cases.iter().enumerate() {
        let scored = scored_of(case);
        let (pick, why) = auto::choose(&scored);
        let faithful = auto::faithful(&scored);
        let note = format!("set {n}: {:?}", scored);
        match case["faithful"].as_str() {
            // the Python's `max()` of nothing; the port answers with no one
            Some("raises") => assert!(faithful.is_empty(), "{note}: faithful {faithful:?}"),
            _ => {
                let want: Vec<usize> = case["faithful"].as_array().unwrap().iter().map(|i| i.as_u64().unwrap() as usize).collect();
                assert_eq!(faithful, want, "{note}");
            }
        }
        if case["raises"].is_null() {
            assert_eq!(pick, case["pick"].as_u64().map(|i| i as usize), "{note}");
            assert_eq!(why, case["reason"].as_str().unwrap(), "{note}");
            *picks.entry(why.to_string()).or_default() += 1;
        } else {
            // a NaN that leaves nobody faithful: the Python raises `ValueError`, the port has no pick
            assert_eq!((pick, why), (None, "no candidate could be scored"), "{note}");
        }
    }
    assert_eq!(picks.len(), 8, "{picks:?}");
}

#[test]
fn issues_and_summary_are_the_pythons_down_to_the_kind_of_every_number() {
    let golden = common::fixture_json("auto.json");
    let order: Vec<&str> = golden["key_order"]["CandidateScores"].as_array().unwrap().iter().map(|k| k.as_str().unwrap()).collect();
    let cases = golden["issues"].as_array().unwrap();
    assert!(cases.len() >= 300);
    let mut kinds = std::collections::BTreeSet::new();
    for (n, case) in cases.iter().enumerate() {
        let c = case["card"].as_object().unwrap();
        let want: Vec<&str> = case["issues"].as_array().unwrap().iter().map(|k| k.as_str().unwrap()).collect();
        assert_eq!(auto::issues(c), want, "card {n}: {c:?}");
        let got = auto::summary(c);
        // `Value`'s equality tells 3 from 3.0 and ignores the order, so the order is read apart
        assert_eq!(got, case["summary"], "card {n}: {c:?}");
        let keys: Vec<&str> = got.as_object().unwrap().keys().map(String::as_str).collect();
        assert_eq!(keys, order, "card {n}");
        for k in want {
            kinds.insert(k.trim_start_matches(|ch: char| ch.is_ascii_digit() || ch == ' ').trim_end_matches('s').to_string());
        }
    }
    assert_eq!(kinds.into_iter().collect::<Vec<_>>(), ["pinhole", "sliver", "uneven rectangle", "wavy curve", "wobbly edge"]);
}

#[test]
fn a_card_the_scorecard_would_not_write_is_summarised_without_a_panic() {
    // the Python raises `KeyError`; a card is whole when it comes from `assess`, and the port reads
    // what is missing as nothing (NaN for the three floats, which JSON holds as null)
    let s = auto::summary(&Map::new());
    assert_eq!(s["shapes"], json!(0));
    assert_eq!(s["issues"], json!([]));
    assert!(s["delta_e"].is_null() && s["artifact_index"].is_null());
    assert_eq!(auto::issues(&Map::new()), Vec::<String>::new());
}

// ---------------------------------------------------------------- run, with a tracer of the test's

/// 16 x 16, a red half and a blue half.
fn tiny() -> Image {
    let mut rgba = Vec::new();
    for _y in 0..16 {
        for x in 0..16 {
            rgba.extend_from_slice(if x < 8 { &[200, 50, 50, 255] } else { &[50, 50, 200, 255] });
        }
    }
    Image { rgba, width: 16, height: 16, format: "PNG".into() }
}

const GOOD: &str = r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect width="8" height="16" fill="#c83232"/><rect x="8" width="8" height="16" fill="#3232c8"/></svg>"##;
const WRONG: &str = r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect width="16" height="16" fill="#00ff00"/></svg>"##;

fn candidates() -> Vec<Preset> {
    presets::auto_candidates()
}

fn find<'a>(o: &'a AutoOutcome, id: &str) -> &'a auto::Candidate {
    o.candidates.iter().find(|c| c.preset == id).unwrap()
}

#[test]
fn a_candidate_that_panics_is_reported_and_the_others_still_score() {
    let tracer = |p: &Preset, _: &Image, _: &VexelParams| -> String {
        match p.id.as_str() {
            "balanced" => GOOD.to_string(),
            "logo" => panic!("kaboom"),
            "detailed" => "<svg".to_string(), // traced, but nothing renders
            _ => WRONG.to_string(),
        }
    };
    let o = auto::run_with(&tiny(), &candidates(), &tracer).unwrap();
    let ids: Vec<&str> = o.candidates.iter().map(|c| c.preset.as_str()).collect();
    assert_eq!(ids, ["balanced", "logo", "detailed", "dense"]);

    // a crash: as the route reports an exception that is not an `EngineError`
    let logo = find(&o, "logo");
    let e = logo.error.as_ref().expect("the panic is the candidate's error");
    assert_eq!(e.code, "engine_crashed");
    assert!(e.message.contains("kaboom"), "{}", e.message);
    assert!(logo.svg.is_none() && logo.stats.is_none() && logo.elapsed_ms.is_none() && logo.scores.is_none());
    // the parameters were validated before the trace ran, and stay
    assert_eq!(logo.parameters.as_ref().unwrap()["detail"], json!(10.0));

    // a trace nothing can render: kept, without scores and without an error
    let detailed = find(&o, "detailed");
    assert_eq!(detailed.svg.as_deref(), Some("<svg"));
    assert!(detailed.error.is_none() && detailed.scores.is_none());
    assert!(detailed.elapsed_ms.is_some() && detailed.stats.is_some() && detailed.parameters.is_some());

    // the other two are scored, and the pick is made among them
    let (balanced, dense) = (find(&o, "balanced"), find(&o, "dense"));
    assert!(balanced.scores.is_some() && dense.scores.is_some() && balanced.error.is_none());
    assert_eq!(balanced.scores.as_ref().unwrap()["delta_e"].as_f64().unwrap(), 0.0);
    assert!(dense.scores.as_ref().unwrap()["delta_e"].as_f64().unwrap() > 10.0);
    assert_eq!((o.pick.as_deref(), o.reason.as_str()), (Some("balanced"), "the only one this faithful to the image"));
    assert_eq!(o.chosen().unwrap().preset, "balanced");
    assert!(o.result_error().is_none());
}

#[test]
fn the_pick_is_never_a_candidate_that_failed_however_clean_it_would_have_been() {
    // the dense candidate is the cleanest (by far) when it is the only one scored; then only it can be picked
    let tracer = |p: &Preset, _: &Image, _: &VexelParams| -> String {
        if p.id == "dense" {
            GOOD.to_string()
        } else if p.id == "balanced" {
            panic!("balanced fails")
        } else {
            "<svg".to_string()
        }
    };
    let o = auto::run_with(&tiny(), &candidates(), &tracer).unwrap();
    assert_eq!((o.pick.as_deref(), o.reason.as_str()), (Some("dense"), "the only candidate that traced"));
}

#[test]
fn every_candidate_failing_leaves_no_pick_and_the_first_error() {
    let tracer = |p: &Preset, _: &Image, _: &VexelParams| -> String { panic!("{} fails", p.id) };
    let o = auto::run_with(&tiny(), &candidates(), &tracer).unwrap();
    assert_eq!((o.pick.as_deref(), o.reason.as_str()), (None, "every candidate failed"));
    assert!(o.candidates.iter().all(|c| c.svg.is_none() && c.scores.is_none() && c.error.is_some()));
    assert!(o.chosen().is_none());
    // `results[engine].error`: the first candidate's, in preference order
    let e = o.result_error().unwrap();
    assert_eq!(e.code, "engine_crashed");
    assert!(e.message.contains("balanced fails"), "{}", e.message);
    // and, when no candidate even says why, the route's own words
    let mut quiet = o.clone();
    for c in &mut quiet.candidates {
        c.error = None;
    }
    let e = quiet.result_error().unwrap();
    assert_eq!((e.code.as_str(), e.message.as_str()), ("engine_failed", "no Auto candidate traced"));
}

#[test]
fn when_nothing_can_be_scored_the_first_preset_that_traced_is_the_pick() {
    // the route: "scoring was unavailable, so the first preset that traced", in preference order
    let tracer = |p: &Preset, _: &Image, _: &VexelParams| -> String {
        if p.id == "balanced" {
            panic!("no trace")
        } else {
            "<svg".to_string()
        }
    };
    let o = auto::run_with(&tiny(), &candidates(), &tracer).unwrap();
    assert_eq!((o.pick.as_deref(), o.reason.as_str()), (Some("logo"), "scoring was unavailable, so the first preset that traced"));
    assert!(o.candidates.iter().all(|c| c.scores.is_none()));
    assert_eq!(o.chosen().unwrap().preset, "logo");
    assert!(o.result_error().is_none());

    // a source that cannot be a reference (no pixels) loses the scoring of every candidate, not the traces
    let empty = Image { rgba: vec![], width: 0, height: 0, format: "PNG".into() };
    let o = auto::run_with(&empty, &candidates(), &|_: &Preset, _: &Image, _: &VexelParams| GOOD.to_string()).unwrap();
    assert_eq!((o.pick.as_deref(), o.reason.as_str()), (Some("balanced"), "scoring was unavailable, so the first preset that traced"));
    assert!(o.candidates.iter().all(|c| c.svg.is_some() && c.scores.is_none() && c.error.is_none()));
}

#[test]
fn parameters_that_do_not_validate_are_the_candidates_error_and_it_never_traces() {
    let mut bad = candidates();
    bad[1].params.insert("detail".into(), json!(-5));
    let traced = AtomicUsize::new(0);
    let tracer = |_: &Preset, _: &Image, _: &VexelParams| -> String {
        traced.fetch_add(1, Ordering::SeqCst);
        GOOD.to_string()
    };
    let o = auto::run_with(&tiny(), &bad, &tracer).unwrap();
    assert_eq!(traced.load(Ordering::SeqCst), 3);
    let logo = &o.candidates[1];
    let e = logo.error.as_ref().unwrap();
    assert_eq!(e.code, "engine_crashed");
    assert!(e.message.starts_with("ValidationError: ") && e.message.contains("detail"), "{}", e.message);
    // `cand.parameters` is set after the validation, so a candidate that failed it has none
    assert!(logo.parameters.is_none() && logo.svg.is_none());
    assert!(o.candidates[0].parameters.is_some());
}

#[test]
fn there_is_no_auto_without_candidates() {
    let e = auto::run_with(&tiny(), &[], &|_: &Preset, _: &Image, _: &VexelParams| GOOD.to_string()).unwrap_err();
    // the route's 400
    assert_eq!((e.code, e.message.as_str()), ("auto_unavailable", "Auto has no candidates for the selected engines"));
    // an image whose buffer is not its size cannot be traced at all
    let broken = Image { rgba: vec![0; 10], width: 4, height: 4, format: "PNG".into() };
    let e: AutoError = auto::run(&broken).unwrap_err();
    assert_eq!(e.code, "invalid_image");
    let none = Image { rgba: vec![], width: 0, height: 0, format: "PNG".into() };
    assert_eq!(auto::run(&none).unwrap_err().code, "invalid_image");
}

#[test]
fn candidates_come_back_in_the_order_of_preference_whichever_finishes_first() {
    let tracer = |p: &Preset, _: &Image, _: &VexelParams| -> String {
        if p.id == "balanced" {
            std::thread::sleep(Duration::from_millis(400));
        }
        GOOD.to_string()
    };
    let o = auto::run_with(&tiny(), &candidates(), &tracer).unwrap();
    let ids: Vec<&str> = o.candidates.iter().map(|c| c.preset.as_str()).collect();
    assert_eq!(ids, ["balanced", "logo", "detailed", "dense"]);
    let ms: Vec<f64> = o.candidates.iter().map(|c| c.elapsed_ms.unwrap()).collect();
    assert!(ms[0] >= 400.0, "{ms:?}");
    // the others were not made to wait for it, and each carries its own time
    assert!(ms[1..].iter().all(|m| *m < 300.0), "{ms:?}");
    // equal scores: the earlier preset wins the tie, which is the first in the list however the threads ran
    assert_eq!(o.pick.as_deref(), Some("balanced"));
    // and a time is a time, not a time rounded to a tenth
    assert!(ms.iter().any(|m| auto::py_round(*m, 1) != *m), "{ms:?}");
}

#[test]
fn the_svg_is_given_the_sources_view_box_whatever_the_engine_wrote() {
    // `finish`: no width or height on the root, a viewBox of the source's size; the stats are of that SVG
    let tracer = |_: &Preset, _: &Image, _: &VexelParams| -> String {
        GOOD.replacen(r#"viewBox="0 0 16 16""#, r#"width="32pt" height="32pt" viewBox="0 0 32 32""#, 1)
    };
    let o = auto::run_with(&tiny(), &candidates(), &tracer).unwrap();
    for c in &o.candidates {
        let svg = c.svg.as_deref().unwrap();
        assert!(svg.starts_with(r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">"#), "{svg}");
        assert_eq!(c.stats.unwrap().bytes as usize, svg.len());
        assert!(c.scores.is_some());
    }
}

#[test]
fn a_panic_in_the_engines_own_parallel_work_is_the_candidates_error_and_the_pool_carries_on() {
    use rayon::prelude::*;
    let tracer = |p: &Preset, _: &Image, _: &VexelParams| -> String {
        if p.id == "logo" {
            (0..400).into_par_iter().for_each(|i| {
                if i == 313 {
                    panic!("deep in a par_iter")
                }
            });
        }
        GOOD.to_string()
    };
    for _ in 0..2 {
        let o = auto::run_with(&tiny(), &candidates(), &tracer).unwrap();
        let e = o.candidates[1].error.as_ref().expect("the panic of a worker is the candidate's error");
        assert!(e.code == "engine_crashed" && e.message.contains("deep in a par_iter"), "{e:?}");
        assert!(o.candidates.iter().filter(|c| c.scores.is_some()).count() == 3);
        assert_eq!(o.pick.as_deref(), Some("balanced"));
    }
}

#[test]
fn the_order_holds_when_the_slowest_to_score_is_the_first_too() {
    // balanced traces at once and scores last (a heavy SVG), the others the other way round
    let heavy = format!(
        r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect width="8" height="16" fill="#c83232"/><rect x="8" width="8" height="16" fill="#3232c8"/>{}</svg>"##,
        (0..4000).map(|i| format!(r##"<path d="M{} {}h0.5v0.5h-0.5z" fill="#c83232"/>"##, i % 16, (i / 16) % 16)).collect::<String>()
    );
    let tracer = |p: &Preset, _: &Image, _: &VexelParams| -> String { if p.id == "balanced" { heavy.clone() } else { GOOD.to_string() } };
    let o = auto::run_with(&tiny(), &candidates(), &tracer).unwrap();
    let ids: Vec<&str> = o.candidates.iter().map(|c| c.preset.as_str()).collect();
    assert_eq!(ids, ["balanced", "logo", "detailed", "dense"]);
    assert!(o.candidates.iter().all(|c| c.scores.is_some()));
}

#[test]
fn the_candidates_trace_at_once() {
    let cores = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
    let arrived = AtomicUsize::new(0);
    let met = AtomicUsize::new(0);
    let tracer = |_: &Preset, _: &Image, _: &VexelParams| -> String {
        arrived.fetch_add(1, Ordering::SeqCst);
        let until = Instant::now() + Duration::from_secs(5);
        while arrived.load(Ordering::SeqCst) < 4 && Instant::now() < until {
            std::thread::yield_now();
        }
        if arrived.load(Ordering::SeqCst) == 4 {
            met.fetch_add(1, Ordering::SeqCst);
        }
        GOOD.to_string()
    };
    let started = Instant::now();
    auto::run_with(&tiny(), &candidates(), &tracer).unwrap();
    if cores >= 4 {
        assert_eq!(met.load(Ordering::SeqCst), 4, "the four candidates did not overlap");
        assert!(started.elapsed() < Duration::from_secs(4));
    }
}

/// About `frames * 48 KiB` of stack, held while it recurses.
fn eat(frames: usize) -> usize {
    let mut buf = [0u8; 48 * 1024];
    buf[0] = frames as u8;
    let below = if frames == 0 { 0 } else { eat(frames - 1) };
    std::hint::black_box(&mut buf);
    below + buf[0] as usize + buf[buf.len() - 1] as usize
}

#[test]
fn the_candidates_run_on_threads_of_their_own_with_room_for_a_deep_svg() {
    // 64 frames of 48 KiB is 3 MiB: more than the 2 MiB of one of rayon's own workers (that
    // overflows, and takes the test process with it), less than the 8 MiB of Auto's
    let names = std::sync::Mutex::new(Vec::new());
    let tracer = |_: &Preset, _: &Image, _: &VexelParams| -> String {
        names.lock().unwrap().push(std::thread::current().name().unwrap_or("").to_string());
        std::hint::black_box(eat(64));
        GOOD.to_string()
    };
    let o = auto::run_with(&tiny(), &candidates(), &tracer).unwrap();
    let names = names.into_inner().unwrap();
    assert_eq!(names.len(), 4);
    assert!(names.iter().all(|n| n.starts_with("studi0trace-auto-")), "{names:?}");
    assert!(o.candidates.iter().all(|c| c.error.is_none()));
}

#[test]
fn the_time_is_the_traces_and_not_the_scoring() {
    // the route's `elapsed_ms` is taken inside `Engine.trace`: the trace, the dimensions and the stats
    let tracer = |_: &Preset, _: &Image, _: &VexelParams| -> String {
        std::thread::sleep(Duration::from_millis(120));
        GOOD.to_string()
    };
    let o = auto::run_with(&tiny(), &candidates(), &tracer).unwrap();
    for c in &o.candidates {
        let ms = c.elapsed_ms.unwrap();
        assert!((120.0..2000.0).contains(&ms), "{}: {ms}", c.preset);
    }
}

#[test]
fn the_result_is_the_apis_auto_result_with_every_field_present() {
    let tracer = |p: &Preset, _: &Image, _: &VexelParams| -> String {
        if p.id == "logo" {
            panic!("kaboom")
        }
        GOOD.to_string()
    };
    let o = auto::run_with(&tiny(), &candidates(), &tracer).unwrap();
    let v = serde_json::to_value(&o).unwrap();
    let keys = |v: &Value| -> Vec<String> { v.as_object().unwrap().keys().cloned().collect() };
    let order = &common::fixture_json("auto.json")["key_order"];
    let want = |name: &str| -> Vec<String> { order[name].as_array().unwrap().iter().map(|k| k.as_str().unwrap().to_string()).collect() };
    assert_eq!(keys(&v), want("AutoResult"));
    assert_eq!(v["engine"], "vexel");
    for c in v["candidates"].as_array().unwrap() {
        assert_eq!(keys(c), want("AutoCandidate"));
    }
    // Pydantic writes what is unset as null; the app reads `null`, not a missing key
    let logo = &v["candidates"][1];
    for k in ["svg", "elapsed_ms", "stats", "scores"] {
        assert!(logo[k].is_null(), "{k}");
    }
    assert_eq!(keys(&logo["error"]), want("ErrorBody"));
    assert_eq!(keys(&v["candidates"][0]["scores"]), want("CandidateScores"));
    assert!(v["candidates"][0]["error"].is_null());
    let stats = &v["candidates"][0]["stats"];
    assert_eq!(keys(stats), ["paths", "nodes", "bytes", "gradients", "unique_fills"]);
    // the full card is the port's own, not the API's
    assert!(v["candidates"][0].get("card").is_none());
    assert!(o.candidates[0].card.is_some());
}

// ---------------------------------------------------------------- the real thing, against the Python's route

fn image_of(entry: &Value) -> Image {
    let name = entry["name"].as_str().unwrap();
    let bytes = std::fs::read(common::backend(entry["source_repo"].as_str().unwrap())).unwrap();
    let img = intake::load(&bytes, intake::Limits::default()).unwrap_or_else(|e| panic!("{name}: {e}"));
    assert_eq!((img.width as u64, img.height as u64), (entry["width"].as_u64().unwrap(), entry["height"].as_u64().unwrap()), "{name}");
    assert_eq!(common::sha256_hex(&img.rgba), entry["rgba_sha256"].as_str().unwrap(), "{name}: the intake decodes other pixels than Pillow");
    img
}

/// A card against the Python's: integers exactly, floats to 1e-9 (relative), and to the bit
/// where [`common::exact`] except the CIEDE2000s (see `tests/scorecard.rs`, which holds the same).
fn same_card(name: &str, got: &Map<String, Value>, want: &Value) {
    for (key, w) in want.as_object().unwrap() {
        let g = got.get(key).unwrap_or_else(|| panic!("{name}: no {key}"));
        match w {
            Value::Number(w) if w.is_f64() => {
                let (a, b) = (g.as_f64().filter(|_| g.is_f64()).unwrap_or_else(|| panic!("{name} {key}: {g} is not a float")), w.as_f64().unwrap());
                assert!((a - b).abs() <= 1e-9 * (1.0 + b.abs()), "{name} {key}: {a:e} vs {b:e}");
                if !key.starts_with("delta_e") && common::exact() {
                    assert_eq!(a.to_bits(), b.to_bits(), "{name} {key}: {a:e} vs {b:e}");
                }
            }
            w => assert_eq!(g, w, "{name} {key}"),
        }
    }
}

fn check_image(entry: &Value) {
    let name = entry["name"].as_str().unwrap();
    let exact = common::exact();
    let img = image_of(entry);
    let started = Instant::now();
    let o = auto::run(&img).unwrap_or_else(|e| panic!("{name}: {e}"));
    eprintln!("{name:<22} {:>4}x{:<4} auto::run {:>9.1?}  -> {} ({})", img.width, img.height, started.elapsed(), o.pick.as_deref().unwrap_or("-"), o.reason);

    assert_eq!((o.pick.as_deref(), o.reason.as_str()), (entry["pick"].as_str(), entry["reason"].as_str().unwrap()), "{name}: the pick");
    assert_eq!(o.candidates.len(), 4, "{name}");
    let v = serde_json::to_value(&o).unwrap();
    assert_eq!(v["engine"], entry["engine"], "{name}");
    for (c, want) in o.candidates.iter().zip(entry["candidates"].as_array().unwrap()) {
        let id = c.preset.as_str();
        let at = format!("{name} {id}");
        assert_eq!((id, c.label.as_str()), (want["preset"].as_str().unwrap(), want["label"].as_str().unwrap()), "{name}");
        // the SVG the Python route sends, to the byte, and the counts read off it: the engine's
        // floats decide both, so off the fixtures' platform (`common::exact`) they are not compared
        // (`tests/api.rs` blanks the same two); the scores and the pick below are, on every platform
        let svg_file = want["svg_file"].as_str().unwrap();
        if exact {
            let stored = String::from_utf8(common::fixture_bytes(svg_file)).unwrap();
            if c.svg.as_deref() != Some(stored.as_str()) {
                let got = c.svg.as_deref().unwrap_or("");
                let at_byte = got.bytes().zip(stored.bytes()).position(|(a, b)| a != b).unwrap_or(got.len().min(stored.len()));
                panic!("{at}: the SVG differs from {svg_file} ({} bytes vs {}) from byte {at_byte}; {}", got.len(), stored.len(), common::REEXPORT);
            }
        } else {
            assert!(c.svg.as_deref().is_some_and(|s| s.starts_with("<svg")), "{at}: no SVG");
        }
        assert!(c.error.is_none() && c.elapsed_ms.unwrap() > 0.0, "{at}");
        // everything else of the candidate, as the API writes it
        if exact {
            assert_eq!(serde_json::to_value(c.stats.unwrap()).unwrap(), want["stats"], "{at}: stats");
        }
        assert_eq!(Value::Object(c.parameters.clone().unwrap()), want["parameters"], "{at}: parameters");
        assert_eq!(c.scores.as_ref().unwrap(), &want["scores"], "{at}: scores");
        let python = &entry["scored"][id];
        assert_eq!(c.scores.as_ref().unwrap(), &python["summary"], "{at}: the Python's summary of its own card");
        same_card(&at, c.card.as_ref().unwrap(), &python["card"]);
        // `summary` of the Python's own card is the Python's summary
        let from_python = auto::summary(python["card"].as_object().unwrap());
        assert_eq!(from_python, python["summary"], "{at}: summary of the stored card");
    }
    // the whole response, but for the SVGs and the times (checked above), and off the fixtures'
    // platform the counts read off the SVG
    let mut shaped = v.clone();
    for c in shaped["candidates"].as_array_mut().unwrap() {
        let c = c.as_object_mut().unwrap();
        c.remove("svg");
        c.remove("elapsed_ms");
        if !exact {
            c.remove("stats");
        }
    }
    let order = &common::fixture_json("auto.json")["key_order"];
    let keys = |v: &Value| -> Vec<String> { v.as_object().unwrap().keys().cloned().collect() };
    let want_keys = |n: &str| -> Vec<String> { order[n].as_array().unwrap().iter().map(|k| k.as_str().unwrap().to_string()).collect() };
    assert_eq!(keys(&v), want_keys("AutoResult"), "{name}");
    assert_eq!(keys(&v["candidates"][0]), want_keys("AutoCandidate"), "{name}");
    assert_eq!(keys(&v["candidates"][0]["scores"]), want_keys("CandidateScores"), "{name}");
    let mut want = json!({"engine": entry["engine"], "pick": entry["pick"], "reason": entry["reason"]});
    want["candidates"] = Value::Array(
        entry["candidates"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| {
                let mut c = c.as_object().unwrap().clone();
                c.remove("svg_file");
                if !exact {
                    c.remove("stats");
                }
                Value::Object(c)
            })
            .collect(),
    );
    assert_eq!(shaped, want, "{name}");
    // the pick is also the engine's own result
    let chosen = o.chosen().unwrap();
    if exact {
        assert_eq!(chosen.svg.as_deref(), Some(String::from_utf8(common::fixture_bytes(entry["chosen_svg_file"].as_str().unwrap())).unwrap().as_str()), "{name}");
    }
    assert_eq!(Value::Object(chosen.parameters.clone().unwrap()), entry["parameters_used"], "{name}");
}

#[test]
fn the_wordmark_is_traced_scored_and_picked_as_the_python_route_does() {
    let golden = common::fixture_json("auto.json");
    let entry = golden["images"].as_array().unwrap().iter().find(|e| e["name"] == "wordmark").unwrap();
    assert_eq!(entry["pick"], "balanced");
    check_image(entry);
}

#[test]
fn six_small_images_pick_every_preset_for_every_reason_the_python_does() {
    let golden = common::fixture_json("auto.json");
    let mut seen = std::collections::BTreeSet::new();
    let mut picks = std::collections::BTreeSet::new();
    for entry in golden["images"].as_array().unwrap() {
        if entry["name"] == "wordmark" {
            continue;
        }
        check_image(entry);
        seen.insert(entry["reason"].as_str().unwrap().to_string());
        picks.insert(entry["pick"].as_str().unwrap().to_string());
    }
    // balanced is not the answer to everything, and the rule is not one reason
    assert_eq!(picks.len(), 4, "{picks:?}");
    assert!(seen.len() >= 5, "{seen:?}");
}
