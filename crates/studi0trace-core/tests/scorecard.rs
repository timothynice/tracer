//! `scorecard` against `quality.Reference`, `assess`, `scorecard`, `artifact_index` and
//! `is_clean` (`scorecard.json`): the wordmark traced with each Auto candidate (Task 13 scores
//! the same SVGs), held-out items, a source with transparency, a non-square one, a source above
//! 640 x 640 that is scored at 2x, made SVGs that set off each counter, the options of
//! `scorecard`, the refusals, and cards made by hand for the weights and the thresholds.
mod common;

use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::time::Instant;
use studi0trace_core::drawing::{self, DrawingError};
use studi0trace_core::geometry::CardError;
use studi0trace_core::intake::{self, Image};
use studi0trace_core::scorecard::{self, Reference, ScoreError, ScoreOptions};

/// The source a case names: a file of the repo, or a PNG beside the fixtures.
fn source_of(case: &Value) -> Image {
    let bytes = match (case["source_repo"].as_str(), case["source_file"].as_str()) {
        (Some(rel), None) => std::fs::read(common::backend(rel)).unwrap(),
        (None, Some(name)) => common::fixture_bytes(name),
        other => panic!("{}: a source is a file of the repo or a fixture, not {other:?}", case["name"]),
    };
    let img = intake::load(&bytes, intake::Limits::default()).unwrap_or_else(|e| panic!("{}: {e}", case["name"]));
    let name = case["name"].as_str().unwrap();
    assert_eq!((img.width as u64, img.height as u64), (case["width"].as_u64().unwrap(), case["height"].as_u64().unwrap()), "{name}");
    assert_eq!(common::sha256_hex(&img.rgba), case["rgba_sha256"].as_str().unwrap(), "{name}: the intake decodes other pixels than Pillow");
    img
}

/// The SVG a case names: a fixture file or the text itself.
fn svg_of(case: &Value) -> String {
    match case["svg_file"].as_str() {
        Some(file) => String::from_utf8(common::fixture_bytes(file)).unwrap(),
        None => case["svg"].as_str().unwrap().to_string(),
    }
}

fn reference_of(img: &Image) -> Reference {
    Reference::new(&img.rgba, img.height as usize, img.width as usize).unwrap()
}

/// What the case asks of the Rust side: `assess`, or `scorecard` with the options it names.
fn run(case: &Value, img: &Image, r: &Reference, svg: &str) -> Result<Map<String, Value>, ScoreError> {
    let o = &case["options"];
    let hole_scale = o["hole_scale"].as_u64().map(|s| s as u32);
    match case["kind"].as_str().unwrap() {
        "assess" => scorecard::assess(svg, r, hole_scale),
        "scorecard" => {
            let opts = ScoreOptions {
                detail: o["detail"].as_bool().unwrap(),
                visibility: o["visibility"].as_bool().unwrap(),
                hole_scale: hole_scale.unwrap(),
                id_scale: o["id_scale"].as_u64().unwrap() as u32,
                opaque: o["opaque"].as_bool().unwrap_or(false).then_some(&r.opaque[..]),
            };
            scorecard::scorecard(svg, &img.rgba, img.height as usize, img.width as usize, &opts)
        }
        kind => panic!("unknown kind {kind}"),
    }
}

/// Every number in `got` against `want`: integers (and booleans) exactly and of the same kind,
/// floats to `1e-9 * (1 + |want|)`, and to the bit where [`common::exact`] and `bitwise`: the
/// scorecard's floats go through libm (`atan2`, `cos`, `sin`) and so are held to the bit only on
/// the platform the fixtures were exported on. The CIEDE2000 of two Lab images is not: the Lab
/// of a colour is `rgb2lab`'s through a BLAS product that fuses, and differs by 1e-13.
/// The largest relative difference seen goes into `worst`.
fn compare(path: &str, got: &Value, want: &Value, bitwise: bool, worst: &mut f64, errors: &mut Vec<String>) {
    match (got, want) {
        (Value::Number(g), Value::Number(w)) if w.is_f64() => {
            if !g.is_f64() {
                errors.push(format!("{path}: {g} is not a float (want {w})"));
                return;
            }
            let (a, b) = (g.as_f64().unwrap(), w.as_f64().unwrap());
            let d = (a - b).abs();
            if d > 0.0 {
                *worst = worst.max(d / b.abs().max(f64::MIN_POSITIVE));
            }
            if d > 1e-9 * (1.0 + b.abs()) || (bitwise && common::exact() && a.to_bits() != b.to_bits()) {
                errors.push(format!("{path}: {a:e} vs {b:e}"));
            }
        }
        (Value::Number(g), Value::Number(w)) => {
            if g != w {
                errors.push(format!("{path}: {g} vs {w}"));
            }
        }
        (Value::Array(g), Value::Array(w)) => {
            if g.len() != w.len() {
                errors.push(format!("{path}: {} entries vs {}", g.len(), w.len()));
                return;
            }
            for (i, (a, b)) in g.iter().zip(w).enumerate() {
                compare(&format!("{path}[{i}]"), a, b, bitwise, worst, errors);
            }
        }
        (Value::Object(g), Value::Object(w)) => {
            // the fixture's objects are written with their keys sorted; a card's order is its `keys`
            let (mut gk, mut wk): (Vec<_>, Vec<_>) = (g.keys().collect(), w.keys().collect());
            gk.sort();
            wk.sort();
            if gk != wk {
                errors.push(format!("{path}: keys {gk:?} vs {wk:?}"));
                return;
            }
            for (k, v) in w {
                compare(&format!("{path}.{k}"), &g[k], v, bitwise, worst, errors);
            }
        }
        (g, w) => {
            if g != w {
                errors.push(format!("{path}: {g} vs {w}"));
            }
        }
    }
}

#[test]
fn assessments_match_the_python() {
    let data = common::fixture_json("scorecard.json");
    let mut worst: BTreeMap<String, f64> = BTreeMap::new();
    let mut errors = Vec::new();
    let mut compared = 0;
    for case in data["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        if !case["error"].is_null() {
            continue;
        }
        let (img, svg) = (source_of(case), svg_of(case));
        let r = reference_of(&img);
        let t = Instant::now();
        let got = match run(case, &img, &r, &svg) {
            Ok(got) => got,
            Err(e) => {
                errors.push(format!("{name}: refused ({e}) where the Python answers"));
                continue;
            }
        };
        eprintln!("{name:<44} {:>4}x{:<4} {:>9.1?}", img.width, img.height, t.elapsed());
        let keys: Vec<&str> = case["keys"].as_array().unwrap().iter().map(|k| k.as_str().unwrap()).collect();
        let order: Vec<&str> = got.keys().map(String::as_str).collect();
        if order != keys {
            errors.push(format!("{name}: keys {order:?}, want {keys:?}"));
            continue;
        }
        for (key, want) in case["card"].as_object().unwrap() {
            let w = worst.entry(key.clone()).or_insert(0.0);
            compare(&format!("{name} {key}"), &got[key], want, !key.starts_with("delta_e"), w, &mut errors);
        }
        if scorecard::is_clean(&got) != case["clean"].as_bool().unwrap() {
            errors.push(format!("{name}: is_clean {} vs {}", scorecard::is_clean(&got), case["clean"]));
        }
        // the index is the card's own, and `artifact_index` reproduces it from the rest
        if scorecard::artifact_index(&got).to_bits() != got["artifact_index"].as_f64().unwrap().to_bits() {
            errors.push(format!("{name}: artifact_index() gives {} for a card that says {}", scorecard::artifact_index(&got), got["artifact_index"]));
        }
        compared += 1;
    }
    for (key, w) in &worst {
        println!("{key:>22}: largest relative difference {w:e}");
    }
    assert!(compared >= 30, "only {compared} cases");
    assert!(errors.is_empty(), "{} differences:\n{}", errors.len(), errors[..errors.len().min(40)].join("\n"));
}

#[test]
fn what_the_python_raises_on_is_refused() {
    let data = common::fixture_json("scorecard.json");
    let mut seen = 0;
    for case in data["cases"].as_array().unwrap() {
        let Some(error) = case["error"].as_str() else { continue };
        let name = case["name"].as_str().unwrap();
        let (img, svg) = (source_of(case), svg_of(case));
        let r = reference_of(&img);
        match (error, run(case, &img, &r, &svg)) {
            ("ParseError", Err(ScoreError::Card(CardError::Drawing(DrawingError::Xml(_))))) => {}
            // resvg refuses the markup, or a render of no width
            ("ValueError", Err(ScoreError::Render(_) | ScoreError::Holes(_))) => {}
            (e, got) => panic!("{name}: the Python raises {e}, the port gives {got:?}"),
        }
        seen += 1;
    }
    assert_eq!(seen, 3);
}

#[test]
fn a_reference_caches_what_the_python_caches() {
    let data = common::fixture_json("scorecard.json");
    let mut seen = std::collections::BTreeSet::new();
    for case in data["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let img = source_of(case);
        let r = reference_of(&img);
        let want = &case["reference"];
        let bytes = |m: &[bool]| m.iter().map(|&b| b as u8).collect::<Vec<u8>>();
        assert_eq!((r.width, r.height), (img.width as usize, img.height as usize), "{name}");
        assert_eq!(r.rgba, img.rgba, "{name}");
        assert_eq!(r.rgb.len(), r.width * r.height * 3, "{name}");
        assert_eq!(r.lab.len(), r.width * r.height, "{name}");
        assert_eq!(common::sha256_hex(&bytes(&r.edges)), want["edges_sha256"].as_str().unwrap(), "{name}: edges");
        assert_eq!(common::sha256_hex(&bytes(&r.edges_wide)), want["edges_wide_sha256"].as_str().unwrap(), "{name}: edges dilated by disk(2)");
        assert_eq!(common::sha256_hex(&bytes(&r.opaque)), want["opaque_sha256"].as_str().unwrap(), "{name}: opaque interior");
        assert_eq!(r.edges.iter().filter(|e| **e).count() as u64, want["edges"].as_u64().unwrap(), "{name}");
        assert_eq!(r.opaque.iter().filter(|e| **e).count() as u64, want["opaque"].as_u64().unwrap(), "{name}");
        seen.insert(want["edges_wide_sha256"].as_str().unwrap().to_string());
    }
    assert!(seen.len() >= 10, "{} distinct references", seen.len());
}

#[test]
fn the_hand_made_cards_are_indexed_and_judged_as_the_python_does() {
    let data = common::fixture_json("scorecard.json");
    let keys: Vec<&str> = data["keys"].as_array().unwrap().iter().map(|k| k.as_str().unwrap()).collect();
    assert_eq!(scorecard::ARTIFACT_KEYS.to_vec(), keys, "ARTIFACT_KEYS");
    assert_eq!((data["hole_scale"]["hole_scale"].as_u64(), data["hole_scale"]["id_scale"].as_u64()), (Some(4), Some(2)));
    let mut seen = 0;
    for hand in data["hand"].as_array().unwrap() {
        let name = hand["name"].as_str().unwrap();
        let card = hand["card"].as_object().unwrap();
        let (got, want) = (scorecard::artifact_index(card), hand["index"].as_f64().unwrap());
        // only additions and multiplications by decimals: the same on every platform
        assert_eq!(got.to_bits(), want.to_bits(), "{name}: artifact_index {got} vs {want}");
        assert_eq!(scorecard::is_clean(card), hand["clean"].as_bool().unwrap(), "{name}: is_clean");
        seen += 1;
    }
    assert!(seen >= 25, "{seen} hand-made cards");
    // an older card with no rect_skewed: it counts as none
    for case in data["missing_rect_skewed"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        assert_eq!(scorecard::is_clean(case["card"].as_object().unwrap()), case["clean"].as_bool().unwrap(), "{name}");
    }
}

fn card(v: Value) -> Map<String, Value> {
    v.as_object().unwrap().clone()
}

fn zero() -> Value {
    json!({"hole_subpx": 0, "hole_px": 0.0, "hole_clusters": 0, "pinholes": 0, "elements": 3, "segments": 20, "strokes": 0,
           "outline_len_px": 100.0, "hidden_len_px": 0.0, "slivers": 0, "sliver_area_px": 0.0, "degenerate": 0, "thin_strokes": 0,
           "wobble_deg_100px": 0.0, "inflections": 0, "rect_like": 2, "radius_inconsistent": 0, "rect_bowed": 0, "rect_skewed": 0,
           "segments_100px": 20.0})
}

#[test]
fn each_term_of_the_index_has_its_weight() {
    // one defect of each kind, alone: what it costs, from `artifact_index`'s docstring
    let weights = [
        ("pinholes", 1.0), ("slivers", 1.0), ("degenerate", 1.0), ("thin_strokes", 1.0), ("radius_inconsistent", 0.5),
        ("rect_bowed", 0.5), ("rect_skewed", 0.5), ("inflections", 1.0), ("wobble_deg_100px", 0.25), ("hole_clusters", 0.1),
    ];
    for (key, w) in weights {
        let mut c = zero();
        c[key] = json!(4);
        assert_eq!(scorecard::artifact_index(&card(c)), 4.0 * w, "{key}");
    }
    // a pinhole is a cluster too and is not charged twice
    let mut c = zero();
    c["pinholes"] = json!(2);
    c["hole_clusters"] = json!(2);
    assert_eq!(scorecard::artifact_index(&card(c)), 2.0);
    // fewer clusters than pinholes (not a card the trace makes) costs the pinholes alone
    let mut c = zero();
    c["pinholes"] = json!(3);
    c["hole_clusters"] = json!(1);
    assert_eq!(scorecard::artifact_index(&card(c)), 3.0);
    // what the index does not count
    let mut c = zero();
    c["rect_like"] = json!(500);
    c["elements"] = json!(500);
    c["segments"] = json!(5000);
    c["hole_subpx"] = json!(50);
    c["hole_px"] = json!(5.0);
    c["outline_len_px"] = json!(1e6);
    assert_eq!(scorecard::artifact_index(&card(c)), 0.0);
}

#[test]
fn a_card_that_lacks_a_term_has_no_index_and_is_not_clean() {
    // the Python raises KeyError; the port answers NaN, and is_clean answers no, rather than panic
    let index_terms = [
        "pinholes", "hole_clusters", "slivers", "degenerate", "thin_strokes", "radius_inconsistent", "rect_bowed", "rect_skewed",
        "wobble_deg_100px", "inflections",
    ];
    for key in index_terms {
        let mut c = zero();
        c.as_object_mut().unwrap().remove(key);
        assert!(scorecard::artifact_index(&card(c.clone())).is_nan(), "{key}");
        // is_clean reads these, but not hole_clusters or inflections, and rect_skewed with a default of 0
        let reads = !["hole_clusters", "inflections", "rect_skewed"].contains(&key);
        assert_eq!(scorecard::is_clean(&card(c)), !reads, "{key}");
    }
    assert!(scorecard::artifact_index(&Map::new()).is_nan() && !scorecard::is_clean(&Map::new()));
    // a word where a number belongs is no number
    let mut c = zero();
    c["pinholes"] = json!("1");
    assert!(scorecard::artifact_index(&card(c.clone())).is_nan() && !scorecard::is_clean(&card(c)));
}

#[test]
fn the_cleanliness_thresholds_are_the_pythons() {
    let with = |key: &str, v: Value| {
        let mut c = zero();
        c[key] = v;
        scorecard::is_clean(&card(c))
    };
    assert!(scorecard::is_clean(&card(zero())));
    assert!(with("wobble_deg_100px", json!(24.99)) && !with("wobble_deg_100px", json!(25.0)));
    assert!(with("wobble_deg_100px", json!(24.999999999999996)) && !with("wobble_deg_100px", json!(25.000000000000004)));
    assert!(!with("wobble_deg_100px", json!(f64::MAX)));
    for key in ["pinholes", "slivers", "degenerate", "thin_strokes", "radius_inconsistent", "rect_bowed", "rect_skewed"] {
        assert!(!with(key, json!(1)), "{key}");
    }
    // only these: inflections and the clusters that are not pinholes do not make a trace unclean
    assert!(with("inflections", json!(40)) && with("hole_clusters", json!(9)) && with("rect_like", json!(9)));
}

#[test]
fn the_default_options_are_the_pythons() {
    let o = ScoreOptions::default();
    assert!(!o.detail && o.visibility && o.opaque.is_none());
    assert_eq!((o.hole_scale, o.id_scale), (4, 2));
}

#[test]
fn the_hole_scale_drops_to_2x_above_640_by_640_pixels() {
    assert_eq!(scorecard::default_hole_scale(512, 512), 4);
    assert_eq!(scorecard::default_hole_scale(640, 640), 4);
    assert_eq!(scorecard::default_hole_scale(641, 640), 2);
    assert_eq!(scorecard::default_hole_scale(640, 641), 2);
    // the product counts, not either side
    assert_eq!(scorecard::default_hole_scale(800, 512), 4);
    assert_eq!(scorecard::default_hole_scale(512, 800), 4);
    assert_eq!(scorecard::default_hole_scale(409_601, 1), 2);
    assert_eq!(scorecard::default_hole_scale(1, 409_600), 4);
    assert_eq!(scorecard::default_hole_scale(768, 768), 2);
    assert_eq!(scorecard::default_hole_scale(1, 1), 4);
    assert_eq!(scorecard::default_hole_scale(usize::MAX, usize::MAX), 2);
}

#[test]
fn a_reference_refuses_what_numpy_does_not_hold() {
    for (len, h, w) in [(0, 0, 0), (0, 0, 5), (0, 5, 0), (15, 2, 2), (17, 2, 2), (4, 2, 2), (64, 1, 1)] {
        let bytes = vec![255u8; len];
        match Reference::new(&bytes, h, w) {
            Err(ScoreError::Source(_)) => {}
            other => panic!("{len} bytes as {h}x{w}: {other:?}"),
        }
    }
    assert!(matches!(Reference::new(&[], usize::MAX, usize::MAX), Err(ScoreError::Source(_))));
    // a side no render could have, whatever the bytes say
    if let Ok(long) = usize::try_from(1u64 << 32) {
        assert!(matches!(Reference::new(&[], long, 1), Err(ScoreError::Source(_))));
        assert!(matches!(Reference::new(&[], 1, long), Err(ScoreError::Source(_))));
    }
    assert!(Reference::new(&[0, 0, 0, 0], 1, 1).is_ok());
    let r = Reference::new(&[255u8; 3 * 3 * 4], 3, 3).unwrap();
    assert!(matches!(r.fidelity(&[0u8; 8]), Err(ScoreError::Source(_))));
    assert!(matches!(r.fidelity(&[]), Err(ScoreError::Source(_))));
    assert!(matches!(r.fidelity(&[0u8; 3 * 3 * 4 + 4]), Err(ScoreError::Source(_))));
}

#[test]
fn the_opaque_mask_of_the_options_replaces_the_one_computed_from_the_source() {
    // The cases of the fixture hand `scorecard` the Reference's own mask, which is what it computes
    // anyway, so they cannot tell a mask that is used from one that is ignored. Here a source that is
    // opaque everywhere is traced with a window cut out of the middle, and the holes are counted under
    // three masks: none (computed from the source), one that leaves the window out, one of the window.
    let (h, w) = (12usize, 12usize);
    let src = vec![255u8; h * w * 4];
    let svg = r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12"><path fill-rule="evenodd" fill="#fff" d="M0 0h12v12h-12zM4 4h4v4h-4z"/></svg>"##;
    let hole_subpx = |mask: Option<&[bool]>| {
        let card = scorecard::scorecard(svg, &src, h, w, &ScoreOptions { opaque: mask, ..ScoreOptions::default() }).unwrap();
        card["hole_subpx"].as_u64().unwrap()
    };
    let window = |x: usize, y: usize| (4..8).contains(&x) && (4..8).contains(&y);
    let computed = hole_subpx(None);
    assert_eq!(computed, 4 * 4 * 16, "the window is 4 x 4 pixels at 4x");
    let beside: Vec<bool> = (0..h * w).map(|i| !window(i % w, i / w) && i % w < 3).collect();
    assert_eq!(hole_subpx(Some(&beside)), 0, "a mask that does not cover the window sees no hole");
    let only: Vec<bool> = (0..h * w).map(|i| window(i % w, i / w)).collect();
    assert_eq!(hole_subpx(Some(&only)), computed, "a mask of the window sees all of it");
    let half: Vec<bool> = (0..h * w).map(|i| window(i % w, i / w) && i % w < 6).collect();
    assert_eq!(hole_subpx(Some(&half)), 2 * 4 * 16, "and half a mask, half of it");
    // `assess` forwards the Reference's mask the same way
    let r = Reference::new(&src, h, w).unwrap();
    assert_eq!(scorecard::assess(svg, &r, Some(4)).unwrap()["hole_subpx"].as_u64().unwrap(), computed);
}

#[test]
fn a_size_whose_bytes_overflow_is_refused_by_scorecard_and_holes_not_wrapped_into_a_panic() {
    // 2^31 x 2^31 pixels is 2^64 bytes: `pixels * 4` wrapped to the 0 bytes of the slice that was given
    if usize::BITS == 64 {
        let side = 1usize << 31;
        assert!(matches!(scorecard::scorecard("<svg/>", &[], side, side, &ScoreOptions::default()), Err(ScoreError::Source(_))));
        assert!(matches!(scorecard::scorecard("<svg/>", &[], side, 1usize << 33, &ScoreOptions::default()), Err(ScoreError::Source(_))));
        let e = studi0trace_core::holes::holes("<svg/>", &[], side, side, 2, None).unwrap_err();
        assert!(e.contains("too large"), "{e}");
    }
    // a source of no pixels is still the Python's: zeros, whatever the SVG
    let card = scorecard::scorecard("<svg/>", &[], 0, 0, &ScoreOptions::default());
    assert!(!matches!(card, Err(ScoreError::Source(ref m)) if m.contains("too large")), "{card:?}");
}

#[test]
fn a_trace_that_is_the_source_is_perfect() {
    let data = common::fixture_json("scorecard.json");
    let case = data["cases"].as_array().unwrap().iter().find(|c| c["name"] == "auto_balanced").unwrap();
    let img = source_of(case);
    let r = reference_of(&img);
    let got = r.fidelity(&img.rgba).unwrap();
    assert_eq!(got.keys().collect::<Vec<_>>(), ["delta_e_mean", "delta_e_p95", "edge_f1"]);
    assert_eq!(Value::Object(got), json!({"delta_e_mean": 0.0, "delta_e_p95": 0.0, "edge_f1": 1.0}));
    // against white, a source with edges and colour is far from it
    let white = vec![255u8; img.rgba.len()];
    let got = r.fidelity(&white).unwrap();
    assert!(got["delta_e_mean"].as_f64().unwrap() > 1.0);
    assert_eq!(got["edge_f1"].as_f64().unwrap(), 0.0);
}

/// `n` groups round a shape: the stack a parser would need is the SVG's own depth.
fn nested(n: usize) -> String {
    format!(r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8">{}<rect width="4" height="4"/>{}</svg>"#, "<g>".repeat(n), "</g>".repeat(n))
}

#[test]
fn an_svg_nested_beyond_reach_is_refused_before_anything_renders_it() {
    // 100 000 groups deep: resvg's parser would overflow this thread's stack on the first render
    // (and `holes`' is the first), as roxmltree's does; the pre-scan of the text refuses it first
    let deep = nested(100_000);
    let img = Image { rgba: vec![255u8; 8 * 8 * 4], width: 8, height: 8, format: "PNG".into() };
    let r = reference_of(&img);
    let refused = |got: Result<Map<String, Value>, ScoreError>| matches!(got, Err(ScoreError::Card(CardError::Drawing(DrawingError::TooDeep))));
    let t = Instant::now();
    assert!(refused(scorecard::assess(&deep, &r, None)));
    assert!(refused(scorecard::scorecard(&deep, &img.rgba, 8, 8, &ScoreOptions::default())));
    // also over a source that has nothing opaque, which `holes` does not render for
    let clear = Image { rgba: vec![0u8; 8 * 8 * 4], ..img.clone() };
    assert!(refused(scorecard::scorecard(&deep, &clear.rgba, 8, 8, &ScoreOptions::default())));
    // and with the opaque mask given, and with any option
    let opts = ScoreOptions { detail: true, visibility: false, hole_scale: 1, id_scale: 1, opaque: Some(&r.opaque) };
    assert!(refused(scorecard::scorecard(&deep, &img.rgba, 8, 8, &opts)));
    // a deep one inside a text with a DOCTYPE is deep all the same
    let doctype = format!(r#"<!DOCTYPE svg [<!ENTITY a "{}">]>{}"#, "<g>".repeat(2000), nested(10));
    assert!(refused(scorecard::assess(&doctype, &r, None)));
    println!("100 000 groups deep refused four times in {:?}", t.elapsed());
    assert!(t.elapsed().as_secs() < 5);
    // the limit is `drawing`'s own
    assert_eq!(drawing::check_nesting(&nested(drawing::MAX_DEPTH - 1)), Ok(()));
    assert_eq!(drawing::check_nesting(&nested(drawing::MAX_DEPTH)), Err(DrawingError::TooDeep));
    assert_eq!(drawing::check_nesting(&deep), Err(DrawingError::TooDeep));
    assert_eq!(drawing::check_nesting("<svg"), Ok(()), "what does not parse is the parser's to refuse");
}

#[test]
fn a_deep_but_allowed_svg_is_assessed_where_a_thread_has_the_room() {
    // 900 groups deep is under the limit: it is assessed (on a thread with room for the recursion
    // of three parsers) and the shape inside is measured
    let svg = nested(900);
    let img = Image { rgba: vec![255u8; 8 * 8 * 4], width: 8, height: 8, format: "PNG".into() };
    let got = std::thread::Builder::new()
        .stack_size(128 << 20)
        .spawn(move || {
            let r = Reference::new(&img.rgba, 8, 8).unwrap();
            scorecard::assess(&svg, &r, None)
        })
        .unwrap()
        .join()
        .unwrap();
    let got = got.unwrap();
    assert_eq!(got["elements"], json!(1));
}

#[test]
fn the_assessment_of_a_wordmark_takes_milliseconds() {
    let data = common::fixture_json("scorecard.json");
    for name in ["auto_balanced", "large_silverpeak_768"] {
        let case = data["cases"].as_array().unwrap().iter().find(|c| c["name"] == name).unwrap();
        let (img, svg) = (source_of(case), svg_of(case));
        let t = Instant::now();
        let r = reference_of(&img);
        let built = t.elapsed();
        let n = 3;
        let t = Instant::now();
        for _ in 0..n {
            scorecard::assess(&svg, &r, None).unwrap();
        }
        println!("{name}: Reference::new {built:?}, assess {:?}", t.elapsed() / n);
    }
}
