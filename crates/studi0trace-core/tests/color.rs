mod common;
use studi0trace_core::color;

const TOL: f64 = 1e-9;

fn flat(v: &serde_json::Value) -> Vec<u8> {
    v.as_array().unwrap().iter().flat_map(|p| p.as_array().unwrap().iter().map(|c| c.as_u64().unwrap() as u8)).collect()
}

fn hex(v: &serde_json::Value) -> Vec<u8> {
    let s = v.as_str().unwrap();
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
}

fn floats(v: &serde_json::Value) -> Vec<f64> {
    v.as_array().unwrap().iter().map(|x| x.as_f64().unwrap()).collect()
}

fn triples(v: &serde_json::Value) -> Vec<[f64; 3]> {
    v.as_array().unwrap().iter().map(|p| { let p = floats(p); [p[0], p[1], p[2]] }).collect()
}

#[test]
fn lab_and_ciede2000_match_skimage() {
    let g = common::fixture_json("color.json");
    let (a, b) = (flat(&g["rgb"]), flat(&g["rgb2"]));
    let (la, lb) = (color::lab(&a), color::lab(&b));
    let (want_a, want_b) = (triples(&g["lab"]), triples(&g["lab2"]));
    let want_de = floats(&g["de"]);
    assert_eq!((la.len(), lb.len(), want_de.len()), (64, 64, 64));
    for i in 0..la.len() {
        for c in 0..3 {
            assert!((la[i][c] - want_a[i][c]).abs() < TOL, "lab {i}.{c}: {} vs {}", la[i][c], want_a[i][c]);
            assert!((lb[i][c] - want_b[i][c]).abs() < TOL, "lab2 {i}.{c}");
        }
        assert!((color::ciede2000(la[i], lb[i]) - want_de[i]).abs() < TOL, "de {i}");
    }
    let map = color::delta_e_map(&a, &b);
    assert_eq!(map.len(), 64);
    for (i, (got, want)) in map.iter().zip(&want_de).enumerate() {
        assert!((got - want).abs() < TOL, "delta_e_map {i}");
    }
    let (mean, p95) = color::delta_e(&a, &b);
    assert!((mean - g["mean"].as_f64().unwrap()).abs() < TOL, "mean {mean}");
    assert!((p95 - g["p95"].as_f64().unwrap()).abs() < TOL, "p95 {p95}");
}

/// Black has chroma exactly 0, the greys have chroma in the rounding noise, identical pairs
/// have a difference of 0 and the reds and blues sit on both sides of the 0 / 2*pi hue wrap:
/// every branch of CIEDE2000, against skimage's number for each of the 27 x 27 pairs.
#[test]
fn ciede2000_branches_match_skimage() {
    let g = common::fixture_json("color.json");
    let palette = flat(&g["palette"]);
    let labs = color::lab(&palette);
    let want_lab = triples(&g["palette_lab"]);
    let n = labs.len();
    assert_eq!(n, 27);
    for (i, (got, want)) in labs.iter().zip(&want_lab).enumerate() {
        for c in 0..3 {
            assert!((got[c] - want[c]).abs() < TOL, "palette lab {i}.{c}");
        }
    }
    let want: Vec<f64> = g["palette_de"].as_array().unwrap().iter().flat_map(floats).collect();
    assert_eq!(want.len(), n * n);
    let (mut a, mut b) = (Vec::new(), Vec::new());
    for i in 0..n {
        for j in 0..n {
            assert!((color::ciede2000(labs[i], labs[j]) - want[i * n + j]).abs() < TOL, "pair {i},{j}");
            a.extend_from_slice(&palette[i * 3..i * 3 + 3]);
            b.extend_from_slice(&palette[j * 3..j * 3 + 3]);
        }
    }
    let map = color::delta_e_map(&a, &b);
    for (k, (got, w)) in map.iter().zip(&want).enumerate() {
        assert!((got - w).abs() < TOL, "delta_e_map pair {k}");
    }
    for (i, l) in labs.iter().enumerate() {
        assert_eq!(color::ciede2000(*l, *l), 0.0, "identical colour {i}");
    }
}

#[test]
fn mean_and_percentile_over_a_larger_image() {
    let g = common::fixture_json("color.json");
    let (a, b) = (hex(&g["big_a"]), hex(&g["big_b"]));
    assert_eq!(a.len(), 64 * 64 * 3);
    let (mean, p95) = color::delta_e(&a, &b);
    let (want_mean, want_p95) = (g["big_mean"].as_f64().unwrap(), g["big_p95"].as_f64().unwrap());
    assert!((mean - want_mean).abs() < TOL, "mean {mean} vs {want_mean}");
    assert!((p95 - want_p95).abs() < TOL, "p95 {p95} vs {want_p95}");
}

/// `Reference.fidelity` keeps the source's Lab and converts only the candidate: the same
/// numbers as converting both every time.
#[test]
fn delta_e_lab_is_delta_e_from_cached_lab() {
    let g = common::fixture_json("color.json");
    let (a, b) = (hex(&g["big_a"]), hex(&g["big_b"]));
    let cached = color::lab(&a);
    assert_eq!(color::delta_e_lab(&cached, &color::lab(&b)), color::delta_e(&a, &b));
    assert_eq!(color::delta_e_lab(&cached, &cached).0, 0.0);
}

#[test]
fn compositing_on_white_matches() {
    let g = common::fixture_json("color.json");
    assert_eq!(color::rgb_on_white(&flat(&g["rgba"])), flat(&g["on_white"]));
}

/// 6 alphas x 6^3 channel triples, every one of the values either side of a rounding boundary
/// in every channel: the float32 product, the sum and the +0.5 truncation must agree to the byte.
#[test]
fn compositing_on_white_agrees_at_the_rounding_boundaries() {
    let g = common::fixture_json("color.json");
    let (rgba, want) = (hex(&g["grid_rgba"]), hex(&g["grid_on_white"]));
    assert_eq!((rgba.len(), want.len()), (6 * 216 * 4, 6 * 216 * 3));
    let got = color::rgb_on_white(&rgba);
    let bad: Vec<String> = got.chunks(3).zip(want.chunks(3)).zip(rgba.chunks(4))
        .filter(|((g, w), _)| g != w).map(|((g, w), s)| format!("{s:?} -> {g:?}, want {w:?}")).take(5).collect();
    assert!(bad.is_empty(), "{bad:?}");
}

/// The float32 arithmetic is the exact rounding, to the nearest integer, of
/// `(v * a + 255 * (255 - a)) / 255` for every one of the 65 536 (alpha, value) pairs: no pair is
/// an exact tie and float32's error never crosses a rounding boundary. The exporter asserts the
/// same identity of the Python, so this is the whole domain and not a sample of it.
#[test]
fn compositing_on_white_is_exact_rounding_for_every_alpha_and_value() {
    let mut rgba = Vec::with_capacity(256 * 256 * 4);
    for a in 0..=255u8 {
        for v in 0..=255u8 {
            rgba.extend_from_slice(&[v, v, v, a]);
        }
    }
    for (i, px) in color::rgb_on_white(&rgba).chunks(3).enumerate() {
        let (a, v) = ((i / 256) as u32, (i % 256) as u32);
        let want = ((2 * (v * a + 255 * (255 - a)) + 255) / 510) as u8;
        assert_eq!(px, [want; 3], "alpha {a} value {v}");
    }
}

#[test]
fn compositing_on_white_basics() {
    assert_eq!(color::rgb_on_white(&[10, 20, 30, 255]), [10, 20, 30]);
    assert_eq!(color::rgb_on_white(&[10, 20, 30, 0]), [255, 255, 255]);
    assert_eq!(color::rgb_on_white(&[]), Vec::<u8>::new());
    // A trailing partial pixel is not a pixel.
    assert_eq!(color::rgb_on_white(&[0, 0, 0, 255, 1, 2, 3]), [0, 0, 0]);
}

#[test]
fn partial_trailing_pixels_are_ignored() {
    assert_eq!(color::lab(&[255, 255, 255, 9, 9]).len(), 1);
    assert_eq!(color::lab(&[1, 2]).len(), 0);
}

#[test]
fn percentile_is_numpys_linear_method() {
    let g = common::fixture_json("color.json");
    let cases = g["percentiles"].as_array().unwrap();
    assert_eq!(cases.len(), 9);
    for case in cases {
        let values = floats(&case["values"]);
        for pair in case["expect"].as_array().unwrap() {
            let (q, want) = (pair[0].as_f64().unwrap(), pair[1].as_f64().unwrap());
            let got = color::percentile(&values, q);
            // Not `==`: serde_json's default float parsing is up to an ulp off for some decimals.
            assert!((got - want).abs() <= 1e-12 * want.abs().max(1.0), "n={} q={q}: {got} vs {want}", values.len());
        }
    }
}

#[test]
fn percentile_edges() {
    assert!(color::percentile(&[], 95.0).is_nan());
    assert!(color::percentile(&[1.0, f64::NAN, 3.0], 50.0).is_nan());
    assert_eq!(color::percentile(&[7.0], 95.0), 7.0);
    assert_eq!(color::percentile(&[3.0, 1.0, 2.0], 0.0), 1.0);
    assert_eq!(color::percentile(&[3.0, 1.0, 2.0], 100.0), 3.0);
    assert_eq!(color::percentile(&[3.0, 1.0, 2.0], 50.0), 2.0);
}

#[test]
fn empty_images_have_no_statistics() {
    let (mean, p95) = color::delta_e(&[], &[]);
    assert!(mean.is_nan() && p95.is_nan());
    assert!(color::delta_e_map(&[], &[]).is_empty());
}

#[test]
#[should_panic(expected = "same size")]
fn images_of_different_sizes_are_refused() {
    color::delta_e_map(&[0, 0, 0], &[0, 0, 0, 1, 1, 1]);
}
