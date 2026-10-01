//! `geometry::card` against `quality.geometry_card` (`geometry.json`): traces of the wordmark with
//! every fixed preset, vector truths, the synthetic corpus, corpus traces (an upsampled one and
//! one with a shadow filter) and made SVGs that set off each counter. The helpers are held to
//! the Python one by one in the module's own tests (`geometry_helpers.json`).
mod common;

use serde_json::{Map, Value};
use std::collections::BTreeMap;
use std::time::Instant;
use studi0trace_core::drawing::DrawingError;
use studi0trace_core::geometry::{self, CardError};

/// The SVG a case names: a fixture file, a file in the repo, the million-point square (built
/// here exactly as the exporter builds it) or the text itself.
fn svg_of(case: &Value) -> String {
    if let Some(file) = case["file"].as_str() {
        return String::from_utf8(common::fixture_bytes(file)).unwrap();
    }
    if let Some(rel) = case["repo"].as_str() {
        return std::fs::read_to_string(common::backend(rel)).unwrap();
    }
    if let Some(side) = case["square"].as_u64() {
        return square(side as usize);
    }
    case["svg"].as_str().unwrap().to_string()
}

/// A square of `4 * side` points 0.25 px apart, with a circle, on a 64 px canvas.
fn square(side: usize) -> String {
    let quarter = |v: usize| format!("{}.{:02}", v / 4, (v % 4) * 25);
    let ring = (0..side)
        .map(|i| (i, 0))
        .chain((0..side).map(|i| (side, i)))
        .chain((0..side).map(|i| (side - i, side)))
        .chain((0..side).map(|i| (0, side - i)));
    let d: Vec<String> = ring.map(|(x, y)| format!("{} {}", quarter(x), quarter(y))).collect();
    format!(
        r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M{} Z" fill="#c33"/><circle cx="32" cy="32" r="10" fill="#33c"/></svg>"##,
        d.join(" ")
    )
}

fn size_of(case: &Value) -> Option<(u32, u32)> {
    case["size"].as_array().map(|s| (s[0].as_u64().unwrap() as u32, s[1].as_u64().unwrap() as u32))
}

fn run(case: &Value) -> Result<Map<String, Value>, CardError> {
    let vis = case["visibility"].as_bool().unwrap();
    let scale = case["id_scale"].as_u64().unwrap() as u32;
    geometry::card(&svg_of(case), size_of(case), vis, scale)
}

/// Every number in `got` against `want`: integers (and booleans) exactly and of the same kind,
/// floats to `1e-9 * (1 + |want|)`, lists to the same length. A card's floats go through libm
/// (`atan2` in the turning, `cos` and `sin` in the drawing's arcs and ellipses), so they are
/// also held to the bit only where [`common::exact`]. The largest relative difference seen under
/// each top-level key goes into `worst`.
fn compare(path: &str, got: &Value, want: &Value, worst: &mut f64, errors: &mut Vec<String>) {
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
            if d > 1e-9 * (1.0 + b.abs()) || (common::exact() && a.to_bits() != b.to_bits()) {
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
                compare(&format!("{path}[{i}]"), a, b, worst, errors);
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
fn cards_match_the_python() {
    let cases = common::fixture_json("geometry.json");
    let mut worst: BTreeMap<String, f64> = BTreeMap::new();
    let mut errors = Vec::new();
    let mut compared = 0;
    for case in cases.as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        if !case["error"].is_null() {
            continue;
        }
        let got = match run(case) {
            Ok(got) => got,
            Err(e) => {
                errors.push(format!("{name}: refused ({e}) where the Python answers"));
                continue;
            }
        };
        let keys: Vec<&str> = case["keys"].as_array().unwrap().iter().map(|k| k.as_str().unwrap()).collect();
        let order: Vec<&str> = got.keys().map(String::as_str).collect();
        if order != keys {
            errors.push(format!("{name}: keys {order:?}, want {keys:?}"));
            continue;
        }
        for (key, want) in case["card"].as_object().unwrap() {
            let w = worst.entry(key.clone()).or_insert(0.0);
            compare(&format!("{name} {key}"), &got[key], want, w, &mut errors);
        }
        compared += 1;
    }
    for (key, w) in &worst {
        println!("{key:>22}: largest relative difference {w:e}");
    }
    assert!(compared >= 50, "only {compared} cards");
    assert!(errors.is_empty(), "{} differences:\n{}", errors.len(), errors[..errors.len().min(40)].join("\n"));
}

#[test]
fn what_the_python_raises_on_is_refused() {
    let cases = common::fixture_json("geometry.json");
    let mut seen = 0;
    for case in cases.as_array().unwrap() {
        let Some(error) = case["error"].as_str() else { continue };
        let name = case["name"].as_str().unwrap();
        match (error, run(case)) {
            ("ParseError", Err(CardError::Drawing(DrawingError::Xml(_)))) => {}
            // raised while sampling the drawing, or while measuring it
            ("IndexError" | "OverflowError" | "ValueError", Err(CardError::Geometry(_) | CardError::Drawing(DrawingError::Geometry(_)))) => {}
            (e, got) => panic!("{name}: the Python raises {e}, the port gives {got:?}"),
        }
        seen += 1;
    }
    assert_eq!(seen, 4);
}

fn zero_card(elements: u64, segments: u64, strokes: u64) -> Value {
    serde_json::json!({
        "elements": elements, "segments": segments, "strokes": strokes,
        "outline_len_px": 0.0, "hidden_len_px": 0.0, "slivers": 0, "sliver_area_px": 0.0,
        "degenerate": 0, "thin_strokes": 0, "wobble_deg_100px": 0.0, "inflections": 0,
        "rect_like": 0, "radius_inconsistent": 0, "rect_bowed": 0, "rect_skewed": 0,
        "segments_100px": 0.0, "_wobble_at": [], "_flips_at": [], "_slivers_at": [], "_radius_at": [],
    })
}

#[test]
fn a_drawing_with_nothing_to_measure_is_the_zero_card() {
    let ns = r#"xmlns="http://www.w3.org/2000/svg""#;
    for (svg, size) in [
        (format!("<svg {ns}/>"), Some((64, 64))),
        (format!("<svg {ns} viewBox=\"0 0 10 10\"><g><defs><rect width=\"5\" height=\"5\"/></defs></g></svg>"), Some((10, 10))),
        (format!("<svg {ns}/>"), None),
    ] {
        for vis in [true, false] {
            let got = geometry::card(&svg, size, vis, 2).unwrap();
            assert_eq!(Value::Object(got), zero_card(0, 0, 0), "{svg}");
        }
    }
    // a shape with nothing to paint is still an element, and its segments count
    let got = geometry::card(&format!("<svg {ns}><rect width=\"9\" height=\"9\" fill=\"none\"/></svg>"), Some((9, 9)), true, 2);
    assert_eq!(Value::Object(got.unwrap()), zero_card(1, 4, 0));
}

#[test]
fn hostile_drawings_are_answered_or_refused_without_a_panic() {
    let ns = r#"xmlns="http://www.w3.org/2000/svg""#;
    let made = |body: &str| format!("<svg {ns} viewBox=\"0 0 64 64\">{body}</svg>");
    let deep = format!("<svg {ns}>{}<rect width=\"4\" height=\"4\"/>{}</svg>", "<g>".repeat(5000), "</g>".repeat(5000));
    let cases = [
        made(r##"<path d="M5 5" fill="#000"/>"##),
        made(r##"<path d="M5 5 L6 6" fill="#000" stroke="#000" stroke-width="0.2"/>"##),
        made(r##"<polyline points="5,5" stroke="#000"/>"##),
        made(r##"<polyline points="" stroke="#000"/>"##),
        made(r##"<path d="M1e9 1e9 L1000000050 1e9 L1000000050 1000000050 Z" fill="#000"/>"##),
        made(r##"<path d="M-1e9 -1e9 L1e9 -1e9 L1e9 1e9 Z" stroke="#000" stroke-width="0.5" fill="none"/>"##),
        made(r##"<path d="M1e200 0 L-1e200 0 L0 1e200 Z" fill="#000"/>"##),
        made(r##"<circle cx="-500" cy="-500" r="40" fill="#000"/><circle cx="600" cy="30" r="40" fill="#000"/>"##),
        made(r##"<circle cx="0" cy="0" r="1e999" fill="#000"/>"##),
        made(r##"<ellipse cx="1e999" cy="0" rx="1" ry="1e999" fill="#000"/>"##),
        made(r##"<path d="M0 0 L1e999 0 L0 5 Z" fill="#000" stroke="#000" stroke-width="1e999"/>"##),
        made(r##"<g transform="scale(1e-300)"><rect width="10" height="10"/></g>"##),
        made(r##"<g transform="scale(0)"><rect width="10" height="10"/></g>"##),
        deep,
    ];
    for svg in &cases {
        for size in [Some((64, 64)), None, Some((1, 1))] {
            for scale in [2, 1, 0] {
                let _ = geometry::card(svg, size, true, scale);
            }
        }
    }
    assert!(matches!(geometry::card(&cases[cases.len() - 1], Some((8, 8)), true, 2), Err(CardError::Drawing(DrawingError::TooDeep))));
    // a stroke round a triangle 2e9 px a side: 2.7e10 samples, which the Python runs out of memory on
    assert_eq!(geometry::card(&cases[5], Some((64, 64)), true, 2), Err(CardError::TooLarge));
    // an outline far off the canvas looks the id map up outside it, and is measured
    let far = geometry::card(&cases[7], Some((64, 64)), true, 2).unwrap();
    assert!(far["outline_len_px"].as_f64().unwrap() > 400.0);
}

#[test]
fn the_wordmark_is_measured_in_milliseconds() {
    let svg = String::from_utf8(common::fixture_bytes("geometry_wordmark-balanced.svg")).unwrap();
    let t = Instant::now();
    let n = 5;
    for _ in 0..n {
        geometry::card(&svg, Some((512, 512)), true, 2).unwrap();
    }
    let per = t.elapsed() / n;
    println!("wordmark at 512 with its id map: {per:?} a card");
    let t = Instant::now();
    geometry::card(&square(250_000), Some((64, 64)), true, 2).unwrap();
    println!("a million-point square: {:?}", t.elapsed());
}
