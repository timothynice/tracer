//! Each helper of the card against the Python's (`tests/fixtures/geometry_helpers.json`, from
//! `tools/export_core_fixtures.py`): made outlines (circles, rectangles rounded, bowed, skewed
//! and chamfered, a bean, an S, a zig-zag, a sliver, a figure eight, a star, two- and one-point
//! ones) and three outlines of a trace, so that a difference names the helper that makes it.
//! Floats are compared to the bit, except a rectangle's bow and skew (see `rect_like`).
use super::accelerate::{convolve_same_ones, ddot, ddot_stride2};
use super::ids::{id_map, id_svg, lookup, visible_samples};
use super::outline::{cancelled, dilate, flips, inflections, resample, turns, wrap};
use super::rects::{area, corners, rect_like};
use super::{radians, INFLECT_HYST, MAX_SAMPLES};
use crate::drawing;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::sync::OnceLock;

fn fixture() -> &'static Value {
    static F: OnceLock<Value> = OnceLock::new();
    F.get_or_init(|| {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/geometry_helpers.json");
        let text = std::fs::read_to_string(path).unwrap_or_else(|e| panic!("{path}: {e}; run python -m tools.export_core_fixtures --only geometry"));
        serde_json::from_str(&text).unwrap()
    })
}

fn f64s(v: &Value) -> Vec<f64> {
    v.as_array().unwrap().iter().map(|x| x.as_f64().unwrap()).collect()
}

fn usizes(v: &Value) -> Vec<usize> {
    v.as_array().unwrap().iter().map(|x| x.as_u64().unwrap() as usize).collect()
}

fn points(v: &Value) -> Vec<[f64; 2]> {
    f64s(v).as_chunks::<2>().0.to_vec()
}

fn mask(v: &Value) -> Vec<bool> {
    v.as_str().unwrap().bytes().map(|b| b == b'1').collect()
}

fn bits(m: &[bool]) -> String {
    m.iter().map(|&b| if b { '1' } else { '0' }).collect()
}

fn sha256(a: &[f64]) -> String {
    let bytes: Vec<u8> = a.iter().flat_map(|x| x.to_le_bytes()).collect();
    Sha256::digest(&bytes).iter().map(|b| format!("{b:02x}")).collect()
}

/// `got` against an array the exporter wrote with `f64()`: whole when short, else every 16th
/// value and the SHA-256 of all of them. To the bit.
fn same_f64(what: &str, got: &[f64], want: &Value) {
    assert_eq!(got.len() as u64, want["n"].as_u64().unwrap(), "{what}: length");
    let (every, values) = match want.get("values") {
        Some(v) => (1, f64s(v)),
        None => (16, f64s(&want["sample"])),
    };
    for (i, w) in values.iter().enumerate() {
        let g = got[i * every];
        assert!(g.to_bits() == w.to_bits(), "{what}: [{}] is {g:e}, the Python's {w:e}", i * every);
    }
    assert_eq!(sha256(got), want["sha256"].as_str().unwrap(), "{what}: digest (every value)");
}

fn flat(q: &[[f64; 2]]) -> Vec<f64> {
    q.iter().flatten().copied().collect()
}

/// Each outline resampled, after `resample` has been checked against it.
fn outlines() -> Vec<(&'static Value, Vec<[f64; 2]>)> {
    fixture()["outlines"]
        .as_array()
        .unwrap()
        .iter()
        .map(|o| {
            let q = resample(&points(&o["pts"]), o["closed"].as_bool().unwrap(), &mut MAX_SAMPLES.clone()).unwrap();
            (o, q)
        })
        .collect()
}

#[test]
fn resample_is_the_pythons() {
    for (o, q) in outlines() {
        same_f64(&format!("{} resample", o["name"]), &flat(&q), &o["q"]);
    }
}

#[test]
fn resample_refuses_what_the_python_cannot_do() {
    let mut budget = MAX_SAMPLES;
    assert!(resample(&[], true, &mut budget).is_err());
    assert!(resample(&[], false, &mut budget).is_err());
    assert!(resample(&[[0.0, 0.0], [f64::MAX, 0.0], [-f64::MAX, 0.0]], true, &mut budget).is_err());
    let mut small = 100;
    assert_eq!(resample(&[[0.0, 0.0], [30.0, 0.0]], false, &mut small), Err(super::CardError::TooLarge));
    let mut budget = MAX_SAMPLES;
    resample(&[[0.0, 0.0], [10.0, 0.0]], false, &mut budget).unwrap();
    assert_eq!(budget, MAX_SAMPLES - 41, "41 samples of a 10 px line spent");
}

#[test]
fn turns_are_the_pythons() {
    for (o, q) in outlines() {
        let Some(want) = o.get("turns") else { continue };
        for k in [1, 2, 16, 24] {
            let closed = o["closed"].as_bool().unwrap();
            same_f64(&format!("{} turns k={k}", o["name"]), &turns(&q, closed, k), &want[k.to_string()]);
        }
    }
}

#[test]
fn cancelled_is_the_pythons() {
    for (o, q) in outlines() {
        if let Some(want) = o.get("cancelled") {
            same_f64(&format!("{} cancelled", o["name"]), &cancelled(&q, o["closed"].as_bool().unwrap(), 16), want);
        }
    }
}

#[test]
fn flips_are_the_pythons() {
    let mut seen = 0;
    for (o, q) in outlines() {
        let Some(want) = o.get("flips") else { continue };
        let closed = o["closed"].as_bool().unwrap();
        assert_eq!(flips(&turns(&q, closed, 24), closed, radians(INFLECT_HYST)), usizes(&want[0]), "{}", o["name"]);
        assert_eq!(flips(&turns(&q, closed, 1), closed, 0.02), usizes(&want[1]), "{} (fine)", o["name"]);
        seen += !usizes(&want[0]).is_empty() as usize;
    }
    assert!(seen >= 3);
}

/// Made turnings of dyadic steps, whose cumulative turning reaches the hysteresis exactly.
#[test]
fn flips_at_the_hysteresis_are_the_pythons() {
    for case in fixture()["flips"].as_array().unwrap() {
        let got = flips(&f64s(&case["turn"]), case["closed"].as_bool().unwrap(), case["hyst"].as_f64().unwrap());
        assert_eq!(got, usizes(&case["out"]), "{case}");
    }
}

#[test]
fn inflections_are_the_pythons() {
    let mut seen = 0;
    for (o, q) in outlines() {
        let Some(want) = o.get("inflections") else { continue };
        let got = inflections(&q, o["closed"].as_bool().unwrap(), 24).unwrap();
        assert_eq!(got, usizes(want), "{}", o["name"]);
        seen += !got.is_empty() as usize;
    }
    assert!(seen >= 3);
}

#[test]
fn area_is_the_pythons() {
    for (o, q) in outlines() {
        if let Some(want) = o.get("area") {
            let (g, w) = (area(&q), want.as_f64().unwrap());
            assert!(g.to_bits() == w.to_bits(), "{}: area {g:e}, the Python's {w:e}", o["name"]);
        }
    }
}

#[test]
fn corners_are_the_pythons() {
    let mut seen = 0;
    for (o, q) in outlines() {
        let Some(want) = o.get("corners") else { continue };
        let got = corners(&q, o["net_sign"].as_f64().unwrap()).unwrap();
        let want = want.as_array().unwrap();
        assert_eq!(got.len(), want.len(), "{}: corners", o["name"]);
        for (c, w) in got.iter().zip(want) {
            let at = f64s(&w["at"]);
            let same = |a: f64, b: &Value| a.to_bits() == b.as_f64().unwrap().to_bits();
            assert!(
                c.at[0].to_bits() == at[0].to_bits()
                    && c.at[1].to_bits() == at[1].to_bits()
                    && c.index as u64 == w["index"].as_u64().unwrap()
                    && c.lo as u64 == w["lo"].as_u64().unwrap()
                    && c.hi as u64 == w["hi"].as_u64().unwrap()
                    && same(c.turn_deg, &w["turn_deg"])
                    && same(c.radius, &w["radius"]),
                "{}: {c:?}, the Python's {w}",
                o["name"]
            );
        }
        seen += want.len();
    }
    assert!(seen >= 20);
}

/// The radii, the spread and `mixed` to the bit; the bow and the skew, which the Python reads
/// off LAPACK's SVD, to 1e-12 px and degrees.
#[test]
fn rect_like_is_the_pythons() {
    let (mut rects, mut worst) = (0, 0.0f64);
    for (o, q) in outlines() {
        let Some(want) = o.get("rect_like") else { continue };
        let found = corners(&q, o["net_sign"].as_f64().unwrap()).unwrap();
        let vis = mask(&o["rect_visible"]);
        for (got, want) in [rect_like(&q, &found, None), rect_like(&q, &found, Some(&vis))].iter().zip(want.as_array().unwrap()) {
            match (got, want) {
                (None, Value::Null) => {}
                (Some(r), Value::Object(w)) => {
                    let radii = f64s(&w["radii"]);
                    assert!(r.radii.iter().zip(&radii).all(|(a, b)| a.to_bits() == b.to_bits()), "{}: radii", o["name"]);
                    assert_eq!(r.spread.to_bits(), w["spread"].as_f64().unwrap().to_bits(), "{}: spread", o["name"]);
                    assert_eq!(r.mixed, w["mixed"].as_bool().unwrap(), "{}: mixed", o["name"]);
                    for (g, key) in [(r.bow, "bow"), (r.skew, "skew")] {
                        let d = (g - w[key].as_f64().unwrap()).abs();
                        worst = worst.max(d);
                        assert!(d <= 1e-12, "{}: {key} {g:e}, the Python's {}", o["name"], w[key]);
                    }
                    rects += 1;
                }
                (got, want) => panic!("{}: {got:?}, the Python's {want}", o["name"]),
            }
        }
    }
    println!("rect_like: bow and skew within {worst:e} of LAPACK's");
    assert!(rects >= 8);
}

#[test]
fn dilate_is_the_pythons() {
    for case in fixture()["dilate"].as_array().unwrap() {
        let got = dilate(&mask(&case["mask"]), case["r"].as_u64().unwrap() as usize, case["closed"].as_bool().unwrap());
        assert_eq!(bits(&got), case["out"].as_str().unwrap(), "{case}");
    }
}

#[test]
fn wrap_is_the_pythons() {
    let w = &fixture()["wrap"];
    for (a, want) in f64s(&w["in"]).into_iter().zip(f64s(&w["out"])) {
        assert_eq!(wrap(a).to_bits(), want.to_bits(), "wrap({a:e}) = {:e}, the Python's {want:e}", wrap(a));
    }
}

#[test]
fn the_id_map_and_what_is_visible_are_the_pythons() {
    let v = &fixture()["visibility"];
    let size = (v["size"][0].as_u64().unwrap() as u32, v["size"][1].as_u64().unwrap() as u32);
    let drawing = drawing::parse(v["svg"].as_str().unwrap(), Some(size)).unwrap();
    assert_eq!(id_svg(&drawing, size), v["id_svg"].as_str().unwrap());
    let pts = points(&Value::Array(v["lookup_pts"].as_array().unwrap().iter().flat_map(|p| p.as_array().unwrap().clone()).collect()));
    for s in v["scales"].as_array().unwrap() {
        let scale = s["scale"].as_u64().unwrap() as u32;
        let ids = id_map(&drawing, size, scale).unwrap();
        assert_eq!((ids.h as u64, ids.w as u64), (s["h"].as_u64().unwrap(), s["w"].as_u64().unwrap()));
        let mut counts = std::collections::BTreeMap::new();
        for &i in &ids.ids {
            *counts.entry(i).or_insert(0u64) += 1;
        }
        for (k, n) in s["ids_counts"].as_object().unwrap() {
            assert_eq!(counts.get(&k.parse::<i32>().unwrap()), Some(&n.as_u64().unwrap()), "scale {scale}: id {k}");
        }
        let bytes: Vec<u8> = ids.ids.iter().flat_map(|i| i.to_le_bytes()).collect();
        let digest: String = Sha256::digest(&bytes).iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(digest, s["ids_sha256"].as_str().unwrap(), "scale {scale}: the id map");
        let want: Vec<i32> = s["lookup"].as_array().unwrap().iter().map(|x| x.as_i64().unwrap() as i32).collect();
        assert_eq!(lookup(&ids, pts.iter().copied(), scale), want, "scale {scale}: lookup");
        let contours = s["contours"].as_array().unwrap();
        assert_eq!(contours.len(), drawing.contours.len());
        for (c, w) in drawing.contours.iter().zip(contours) {
            let q = resample(&c.pts, c.closed, &mut MAX_SAMPLES.clone()).unwrap();
            same_f64("visibility q", &flat(&q), &w["q"]);
            let vis = visible_samples(&q, c.closed, c.element, c.stroke, Some(&ids), scale);
            assert_eq!(bits(&vis), w["visible"].as_str().unwrap(), "scale {scale}: element {}", c.element);
        }
    }
    // no id map, or nothing to look at: everything is visible
    assert_eq!(visible_samples(&[[1.0, 1.0]; 3], true, 0, None, None, 2), vec![true; 3]);
}

#[test]
fn dot_is_accelerates() {
    for case in fixture()["dot"].as_array().unwrap() {
        let (x, y, q) = (f64s(&case["x"]), f64s(&case["y"]), points(&case["q"]));
        let n = x.len();
        let same = |g: f64, key: &str| assert_eq!(g.to_bits(), case[key].as_f64().unwrap().to_bits(), "n={n} {key}");
        same(ddot(&x, &y[..n], true), "aligned");
        same(ddot(&x, &y[1..], false), "shifted");
        same(ddot_stride2(n, |i| q[i][0], |i| q[(i + 1) % n][1]), "strided");
    }
}

#[test]
fn convolve_is_numpys() {
    for case in fixture()["convolve"].as_array().unwrap() {
        let got = convolve_same_ones(&f64s(&case["a"]), 32);
        let want = f64s(&case["out"]);
        assert_eq!(got.len(), want.len());
        for (i, (g, w)) in got.iter().zip(&want).enumerate() {
            assert_eq!(g.to_bits(), w.to_bits(), "n={} [{i}]: {g:e} vs {w:e}", want.len());
        }
    }
}
