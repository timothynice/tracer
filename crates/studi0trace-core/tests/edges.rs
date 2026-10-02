mod common;
use serde_json::Value;
use studi0trace_core::{color, edges, intake};

fn indices(v: &Value) -> Vec<usize> {
    v.as_array().unwrap().iter().map(|i| i.as_u64().unwrap() as usize).collect()
}

fn on(mask: &[bool]) -> Vec<usize> {
    mask.iter().enumerate().filter(|(_, e)| **e).map(|(i, _)| i).collect()
}

/// A float the exporter sent as its IEEE 754 bits (big-endian hex), so it arrives exact.
fn bits(v: &Value) -> f64 {
    f64::from_bits(u64::from_str_radix(v.as_str().unwrap(), 16).unwrap())
}

fn mask(v: &Value, len: usize) -> Vec<bool> {
    let mut m = vec![false; len];
    for i in indices(v) {
        m[i] = true;
    }
    m
}

/// Little-endian float32 bytes, as numpy's `tobytes()` writes them on this machine.
fn f32_hex(v: &Value) -> Vec<f64> {
    let s = v.as_str().unwrap();
    let bytes: Vec<u8> = (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect();
    bytes.chunks(4).map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]) as f64).collect()
}

/// Every pixel the two edge maps disagree on, named, so a failure says how far off it is.
fn assert_same_edges(got: &[usize], want: &[usize], what: &str) {
    let missing: Vec<_> = want.iter().filter(|i| got.binary_search(i).is_err()).collect();
    let extra: Vec<_> = got.iter().filter(|i| want.binary_search(i).is_err()).collect();
    assert!(
        missing.is_empty() && extra.is_empty(),
        "{what}: {} edge pixels, {} missing from ours {:?}, {} extra {:?}",
        want.len(),
        missing.len(),
        &missing[..missing.len().min(10)],
        extra.len(),
        &extra[..extra.len().min(10)]
    );
}

fn roll(rgb: &[u8], h: usize, w: usize, by: usize) -> Vec<u8> {
    // np.roll(rgb, by, axis=1): the last `by` columns come round to the front.
    (0..h)
        .flat_map(|r| {
            let row = &rgb[r * w * 3..(r + 1) * w * 3];
            let mut out = row[(w - by) * 3..].to_vec();
            out.extend_from_slice(&row[..(w - by) * 3]);
            out
        })
        .collect()
}

#[test]
fn canny_matches_skimage_and_f1_matches_the_python() {
    let g = common::fixture_json("edges.json");
    let cases = g["corpus"].as_array().unwrap();
    assert_eq!(cases.len(), 8);
    for case in cases {
        let item = case["item"].as_str().unwrap();
        let bytes = std::fs::read(common::backend(&format!("bench/corpus/{item}"))).unwrap();
        let img = intake::load(&bytes, Default::default()).unwrap();
        let (h, w) = (img.height as usize, img.width as usize);
        assert_eq!((h as u64, w as u64), (case["h"].as_u64().unwrap(), case["w"].as_u64().unwrap()), "{item}");
        let rgb = color::rgb_on_white(&img.rgba);
        let e = edges::edges(&rgb, h, w);
        assert_eq!(e.len(), h * w);
        assert_same_edges(&on(&e), &indices(&case["edges"]), item);

        let f1 = edges::f1(&e, &edges::edges(&roll(&rgb, h, w, 1), h, w), None, h, w, 2);
        assert_eq!(f1, bits(&case["f1_shifted"]), "{item} f1 at 1 px");
        let e3 = edges::edges(&roll(&rgb, h, w, 3), h, w);
        assert_eq!(edges::f1(&e, &e3, None, h, w, 2), bits(&case["f1_shifted3"]), "{item} f1 at 3 px");
        let wide = edges::dilate_disk(&e, h, w, 2);
        assert_eq!(edges::f1(&e, &e3, Some(&wide), h, w, 2), bits(&case["f1_shifted3_wide"]), "{item} wide");
    }
}

#[test]
fn canny_on_float32_images_matches_skimage() {
    let g = common::fixture_json("edges.json");
    for case in g["grey"].as_array().unwrap() {
        let (h, w) = (case["h"].as_u64().unwrap() as usize, case["w"].as_u64().unwrap() as usize);
        let sigma = bits(&case["sigma"]);
        // The tiny shapes carry their pixels; the bigger images are sent once, by name.
        let gray = f32_hex(if case["gray"].is_null() { &g["images"][case["name"].as_str().unwrap()] } else { &case["gray"] });
        assert_eq!(gray.len(), h * w);
        let got = edges::canny(&gray, h, w, sigma);
        assert_eq!(got.len(), h * w);
        assert_same_edges(&on(&got), &indices(&case["edges"]), &format!("{} {h}x{w} sigma {sigma}", case["name"]));
    }
}

/// The edge maps alone would not notice a last-bit slip in the smoothing or the Sobel
/// (it rarely flips a pixel), so every float32 stage is held to skimage's bits.
#[test]
fn canny_stages_match_skimage_to_the_bit() {
    let g = common::fixture_json("edges.json");
    let cases = g["stages"].as_array().unwrap();
    assert_eq!(cases.len(), 2);
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let (h, w) = (case["h"].as_u64().unwrap() as usize, case["w"].as_u64().unwrap() as usize);
        let s = edges::canny_stages(&f32_hex(&case["gray"]), h, w, bits(&case["sigma"]));
        for (stage, got) in [("smoothed", &s.smoothed), ("isobel", &s.isobel), ("jsobel", &s.jsobel),
                             ("magnitude", &s.magnitude), ("suppressed", &s.suppressed)] {
            let want = f32_hex(&case[stage]);
            assert_eq!(got.len(), want.len(), "{name} {stage}");
            let differ: Vec<usize> = (0..want.len()).filter(|i| (got[*i] as f64).to_bits() != want[*i].to_bits()).collect();
            assert!(differ.is_empty(), "{name} {stage}: {} of {} values differ, first at {:?}", differ.len(), want.len(),
                    differ.first().map(|i| (i, got[*i], want[*i])));
        }
        let e = edges::canny(&f32_hex(&case["gray"]), h, w, bits(&case["sigma"]));
        assert_same_edges(&on(&e), &indices(&case["edges"]), name);
    }
}

/// Interpolations that tie in exact arithmetic, decided by the precision of each step.
#[test]
fn suppression_rounds_as_the_cython_does() {
    let g = common::fixture_json("edges.json");
    let s = &g["suppress"];
    let (h, w) = (s["h"].as_u64().unwrap() as usize, s["w"].as_u64().unwrap() as usize);
    let ints = |v: &Value| -> Vec<f32> { v.as_str().unwrap().split(' ').map(|t| t.parse::<i32>().unwrap() as f32).collect() };
    let palette: Vec<f32> = s["palette"].as_array().unwrap().iter().map(|v| bits(v) as f32).collect();
    let magnitude: Vec<f32> = s["magnitude"].as_str().unwrap().bytes().map(|b| palette[(b - b'0') as usize]).collect();
    let (isobel, jsobel) = (ints(&s["isobel"]), ints(&s["jsobel"]));
    assert_eq!((isobel.len(), jsobel.len(), magnitude.len()), (h * w, h * w, h * w));
    let got = edges::suppress(&isobel, &jsobel, &magnitude, h, w);
    let kept: Vec<usize> = (0..h * w).filter(|p| got[*p] > 0.0).collect();
    assert_same_edges(&kept, &indices(&s["kept"]), "suppressed");
    for p in kept {
        assert_eq!(got[p], magnitude[p], "a kept pixel carries its magnitude");
    }
}

#[test]
fn canny_of_an_empty_image_is_empty() {
    assert!(edges::canny(&[], 0, 0, 1.0).is_empty());
    assert!(edges::canny(&[], 0, 5, 1.0).is_empty());
    assert!(edges::edges(&[], 0, 0).is_empty());
}

#[test]
fn gaussian_taps_match_scipy_to_the_bit() {
    let g = common::fixture_json("edges.json");
    for k in g["kernels"].as_array().unwrap() {
        let sigma = bits(&k["sigma"]);
        let want: Vec<f64> = k["taps"].as_array().unwrap().iter().map(bits).collect();
        assert_eq!(edges::gaussian_kernel(sigma), want, "sigma {sigma}");
    }
}

#[test]
fn dilate_disk_matches_skimage() {
    let g = common::fixture_json("edges.json");
    let cases = g["dilate"].as_array().unwrap();
    assert_eq!(cases.len(), 12);
    for case in cases {
        let (h, w, r) = (case["h"].as_u64().unwrap() as usize, case["w"].as_u64().unwrap() as usize, case["r"].as_u64().unwrap() as usize);
        let got = edges::dilate_disk(&mask(&case["mask"], h * w), h, w, r);
        assert_eq!(on(&got), indices(&case["out"]), "{h}x{w} r {r}");
    }
}

#[test]
fn f1_edge_cases_match_the_python() {
    let g = common::fixture_json("edges.json");
    for case in g["f1"].as_array().unwrap() {
        let (h, w) = (case["h"].as_u64().unwrap() as usize, case["w"].as_u64().unwrap() as usize);
        let (a, b) = (mask(&case["a"], h * w), mask(&case["b"], h * w));
        let tol = case["tol"].as_u64().unwrap() as usize;
        let want = bits(&case["f1"]);
        assert_eq!(edges::f1(&a, &b, None, h, w, tol), want, "{}", case["name"]);
        let wide = edges::dilate_disk(&a, h, w, tol);
        assert_eq!(edges::f1(&a, &b, Some(&wide), h, w, tol), want, "{} with ea_wide", case["name"]);
    }
}
