mod common;
use serde_json::Value;
use studi0trace_core::drawing::{self, Drawing, DrawingError, Subpath};

/// A number from the fixture: JSON has no NaN or infinity, so the exporter writes them as strings.
fn num(v: &Value) -> f64 {
    match v.as_str() {
        Some("nan") => f64::NAN,
        Some("inf") => f64::INFINITY,
        Some("-inf") => f64::NEG_INFINITY,
        Some(s) => panic!("not a number: {s}"),
        None => v.as_f64().unwrap_or_else(|| panic!("not a number: {v}")),
    }
}

/// The largest differences seen from the Python, for the report: coordinates (absolute) and
/// coordinate sums (relative; numpy sums pairwise, this adds in order), and how many contours'
/// digests of every point differ.
#[derive(Default)]
struct Worst {
    point: f64,
    sum: f64,
    digests: usize,
    contours: usize,
}

impl Worst {
    fn report(&self, what: &str) {
        eprintln!(
            "{what}: worst coordinate difference {:e}, worst relative sum difference {:e}, {} of {} contour digests differ",
            self.point, self.sum, self.digests, self.contours
        );
        // The fixtures come from numpy with Accelerate and Apple's libm on arm64, and there this
        // port matches every point to the bit. Elsewhere a libm may round cos or sin an ulp
        // apart, which the 1e-9 comparisons allow.
        if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
            assert_eq!(self.digests, 0, "{what}: points that differ from the Python's in the last bit");
        }
    }
}

/// The interior points the exporter spread along a contour ("index x y" triples), at 1e-9,
/// and the digest of all of them: SHA-256 of float64 little-endian, NaN made canonical.
fn check_samples(what: &str, pts: &[[f64; 2]], want: &Value, worst: &mut Worst) {
    let samples: Vec<&str> = want["samples"].as_str().unwrap().split_whitespace().collect();
    for t in samples.chunks(3) {
        let i: usize = t[0].parse().unwrap();
        for j in 0..2 {
            let (got, w) = (pts[i][j], t[1 + j].parse::<f64>().unwrap());
            let d = same(got, w, 1e-9).unwrap_or_else(|| panic!("{what}: point {i}[{j}] {got} != {w}"));
            worst.point = worst.point.max(d);
        }
    }
    let bytes: Vec<u8> =
        pts.iter().flatten().flat_map(|&v| if v.is_nan() { f64::NAN } else { v }.to_le_bytes()).collect();
    worst.contours += 1;
    worst.digests += (common::sha256_hex(&bytes)[..16] != *want["digest"].as_str().unwrap()) as usize;
}

/// `got` equals `want` within `tol`, NaN equal to NaN and an infinity only to itself.
fn same(got: f64, want: f64, tol: f64) -> Option<f64> {
    if got.is_nan() && want.is_nan() || got == want {
        return Some(0.0);
    }
    let d = (got - want).abs();
    (got.is_finite() && want.is_finite() && d <= tol).then_some(d)
}

fn check_points(what: &str, pts: &[[f64; 2]], columns: usize, want: &Value, worst: &mut Worst) {
    let n = pts.len();
    assert_eq!(n as u64, want["n"].as_u64().unwrap(), "{what}: point count");
    assert_eq!(columns as u64, want["columns"].as_u64().unwrap(), "{what}: columns");
    for j in 0..columns {
        let got: f64 = pts.iter().map(|p| p[j]).sum();
        let w = num(&want["sum"][j]);
        let d = same(got, w, 1e-6 * (1.0 + w.abs())).unwrap_or_else(|| panic!("{what}: sum[{j}] {got} != {w}"));
        worst.sum = worst.sum.max(d / (1.0 + w.abs()));
    }
    if n == 0 {
        assert!(want["first"].is_null(), "{what}");
        return;
    }
    for (key, i) in [("first", 0), ("mid", n / 2), ("last", n - 1)] {
        for j in 0..columns {
            let (got, w) = (pts[i][j], num(&want[key][j]));
            let d = same(got, w, 1e-9).unwrap_or_else(|| panic!("{what}: {key}[{j}] {got} != {w}"));
            worst.point = worst.point.max(d);
        }
    }
}

/// `got` is what the Python made of the same SVG: its counters and every contour, or an
/// error of the kind the Python raised.
fn check_drawing(what: &str, got: Result<Drawing, DrawingError>, want: &Value, worst: &mut Worst) {
    if let Some(raised) = want.get("error").and_then(Value::as_str) {
        let e = got.expect_err(&format!("{what}: the Python raised {raised}"));
        let kind = match raised {
            "ParseError" => matches!(e, DrawingError::Xml(_)),
            "RecursionError" => matches!(e, DrawingError::TooDeep),
            "ValueError" | "IndexError" | "OverflowError" => matches!(e, DrawingError::Geometry(_)),
            other => panic!("{what}: no Rust error stands for {other}"),
        };
        assert!(kind, "{what}: the Python raised {raised}, the Rust {e:?}");
        return;
    }
    let d = got.unwrap_or_else(|e| panic!("{what}: {e}"));
    assert_eq!(d.elements as u64, want["elements"].as_u64().unwrap(), "{what}: elements");
    assert_eq!(d.segments as u64, want["segments"].as_u64().unwrap(), "{what}: segments");
    assert_eq!(d.strokes as u64, want["strokes"].as_u64().unwrap(), "{what}: strokes");
    let covers: Vec<bool> = want["covers"].as_array().unwrap().iter().map(|v| v.as_bool().unwrap()).collect();
    assert_eq!(d.covers, covers, "{what}: covers");
    let contours = want["contours"].as_array().unwrap();
    assert_eq!(d.contours.len(), contours.len(), "{what}: contours");
    for (i, (c, w)) in d.contours.iter().zip(contours).enumerate() {
        let what = format!("{what}, contour {i}");
        assert_eq!(c.element as u64, w["element"].as_u64().unwrap(), "{what}: element");
        assert_eq!(c.closed, w["closed"].as_bool().unwrap(), "{what}: closed");
        assert_eq!(c.paint, w["paint"].as_str().unwrap(), "{what}: paint");
        assert_eq!(c.fill_rule, w["fill_rule"].as_str().unwrap(), "{what}: fill_rule");
        match (c.stroke, w["stroke"].is_null()) {
            (None, true) => {}
            (Some(s), false) => {
                let want = num(&w["stroke"]);
                let d = same(s, want, 1e-12 * (1.0 + want.abs())).unwrap_or_else(|| panic!("{what}: stroke {s} != {want}"));
                worst.point = worst.point.max(d);
            }
            (got, _) => panic!("{what}: stroke {got:?}, the Python {}", w["stroke"]),
        }
        check_points(&what, &c.pts, 2, w, worst);
        check_samples(&what, &c.pts, w, worst);
    }
}

/// Runs `f` on a thread with the stack a document nested [`drawing::MAX_DEPTH`] deep needs in
/// an unoptimised build: roxmltree's parser recurses, about 17 KB a level there (0.9 KB
/// optimised), and a test thread has 2 MiB.
fn with_room<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> T {
    std::thread::Builder::new().stack_size(64 << 20).spawn(f).unwrap().join().unwrap_or_else(|e| std::panic::resume_unwind(e))
}

fn size(v: &Value) -> Option<(u32, u32)> {
    v.as_array().map(|s| (s[0].as_u64().unwrap() as u32, s[1].as_u64().unwrap() as u32))
}

#[test]
fn every_contour_is_sampled_as_the_python_samples_it() {
    let fixture = common::fixture_json("drawing.json");
    let mut worst = Worst::default();
    for case in fixture["files"].as_array().unwrap() {
        let file = case["file"].as_str().unwrap();
        let svg = std::fs::read_to_string(common::backend(&format!("../{file}"))).unwrap();
        let what = format!("{file} at {}", case["size"]);
        check_drawing(&what, drawing::parse(&svg, size(&case["size"])), &case["drawing"], &mut worst);
    }
    worst.report("traced output and vector truths");
}

#[test]
fn hand_written_svgs_take_every_branch_the_python_takes() {
    with_room(|| {
        let fixture = common::fixture_json("drawing.json");
        let mut worst = Worst::default();
        for case in fixture["cases"].as_array().unwrap() {
            let what = case["name"].as_str().unwrap();
            let got = drawing::parse(case["svg"].as_str().unwrap(), size(&case["size"]));
            check_drawing(what, got, &case["drawing"], &mut worst);
        }
        worst.report("hand-written SVGs");
    })
}

fn check_polylines(want: &Value, worst: &mut Worst) {
    let d = want["d"].as_str().unwrap();
    let got = drawing::path_polylines(d);
    if let Some(raised) = want.get("error").and_then(Value::as_str) {
        let e = got.expect_err(&format!("{d:?}: the Python raised {raised}"));
        assert!(matches!(e, DrawingError::Geometry(_)), "{d:?}: {e:?}");
        return;
    }
    let got: Vec<Subpath> = got.unwrap_or_else(|e| panic!("{d:?}: {e}"));
    let subpaths = want["subpaths"].as_array().unwrap();
    assert_eq!(got.len(), subpaths.len(), "{d:?}: subpaths");
    for (i, (s, w)) in got.iter().zip(subpaths).enumerate() {
        let what = format!("{d:?}, subpath {i}");
        assert_eq!(s.closed, w["closed"].as_bool().unwrap(), "{what}: closed");
        check_points(&what, &s.pts, s.columns, w, worst);
    }
}

#[test]
fn path_data_is_read_as_the_python_reads_it() {
    let fixture = common::fixture_json("drawing.json");
    let mut worst = Worst::default();
    for want in fixture["paths"].as_array().unwrap().iter().chain(fixture["prefixes"].as_array().unwrap()) {
        check_polylines(want, &mut worst);
    }
    worst.report("path data");
}

const HEAD: &str = r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">"#;

/// The Python has no limit here: it asks numpy for the points and waits, or runs out of
/// memory. The Rust refuses before it allocates.
#[test]
fn a_drawing_too_large_to_sample_is_refused_not_allocated() {
    let circle = format!(r#"{HEAD}<circle r="1e9"/></svg>"#);
    assert_eq!(drawing::parse(&circle, None), Err(DrawingError::TooLarge));
    let arcs: String = (0..10_000).map(|i| format!("A1e4 1e4 0 1 0 {} 0 ", i % 2)).collect();
    let path = format!(r#"{HEAD}<path d="M0 0 L1 0 {arcs}"/></svg>"#);
    assert_eq!(drawing::parse(&path, None), Err(DrawingError::TooLarge));
    assert_eq!(drawing::path_polylines(&format!("M0 0 L1 0 {arcs}")), Err(DrawingError::TooLarge));
    let uses: String = (0..20_000).map(|_| r##"<use href="#c"/>"##).collect();
    let many = format!(r#"{HEAD}<defs><circle id="c" r="1000"/></defs>{uses}</svg>"#);
    assert_eq!(drawing::parse(&many, None), Err(DrawingError::TooLarge));
    // well inside the limit, a large circle is sampled as the Python samples it
    let big = drawing::parse(&format!(r#"{HEAD}<circle r="1e5"/></svg>"#), None).unwrap();
    assert_eq!(big.contours[0].pts.len(), 2_513_276);
}

/// Groups nested deeper than the Python's recursion limit lets its walk go are refused, as the
/// Python refuses them (its RecursionError comes a few levels either side of MAX_DEPTH,
/// depending on the element and on how deep the caller already is).
#[test]
fn nesting_deeper_than_the_python_can_walk_is_refused() {
    let nested = |depth: usize| format!(r#"{HEAD}{}<path d="M0 0L1 1 1 0Z"/>{}</svg>"#, "<g>".repeat(depth), "</g>".repeat(depth));
    let ok = with_room(move || drawing::parse(&nested(drawing::MAX_DEPTH - 1), None)).unwrap();
    assert_eq!(ok.contours.len(), 1);
    assert_eq!(drawing::parse(&nested(drawing::MAX_DEPTH), None), Err(DrawingError::TooDeep));
    // refused before roxmltree, whose parser would overflow any stack first
    assert_eq!(drawing::parse(&nested(100_000), None), Err(DrawingError::TooDeep));
    let unwalked = format!(r#"{HEAD}<defs>{}</defs></svg>"#, "<g>".repeat(100_000) + &"</g>".repeat(100_000));
    assert_eq!(drawing::parse(&unwalked, None), Err(DrawingError::TooDeep));
    let entity = format!(
        r#"<!DOCTYPE svg [<!ENTITY deep "{}x{}">]><svg xmlns="http://www.w3.org/2000/svg">&deep;</svg>"#,
        "<g>".repeat(5_000),
        "</g>".repeat(5_000)
    );
    assert_eq!(drawing::parse(&entity, None), Err(DrawingError::TooDeep));
}

/// The parser reads the output of other engines: nothing it is given may panic. Every prefix
/// of an SVG that takes most branches, and byte-level mutations of it, parse or are refused.
#[test]
fn malformed_input_is_refused_without_a_panic() {
    let svg = concat!(
        r#"<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 64 64">"#,
        r##"<defs><path id="p" d="M0 0L4 0 4 4Z" transform="rotate(30 2 2)"/></defs><use xlink:href="#p" x="3" y="1e999"/>"##,
        r#"<g transform="matrix(1 0.2 0.3 1 5 6) scale(2)" style="fill:#f00;stroke-width:2" opacity="0.5">"#,
        r#"<path d="M10 20 L30-5.5e1 h4v-3 H12 V8 C1 2 3 4 5 6 s7 8 9 10 Q11 12 13 14 t15 16 A5 3 20 1 0 40 40 a2 2 0 0 1 -3 -3 Z m1 1 l2 2 z"/>"#,
        r##"<rect x="1" y="2" width="10" height="6" rx="2"/><circle cx="5" cy="6" r="3" stroke="#000"/><ellipse rx="5" ry="2"/>"##,
        r##"<polygon points="1,1 5,1 5,5"/><polyline points="1 1 5 1"/><line x1="1" y1="2" x2="3" y2="4" stroke="#000"/></g></svg>"##
    );
    let mut refused = 0;
    for k in 0..=svg.len() {
        refused += drawing::parse(&svg[..k], Some((64, 64))).is_err() as usize;
    }
    assert!(refused > 0 && drawing::parse(svg, Some((64, 64))).is_ok());
    let mut state = 0x9E37_79B9_7F4A_7C15u64;
    let mut next = move || {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        state
    };
    let alphabet = b" -+.,e0123456789MmLlHhVvCcSsQqTtAaZz()<>\"'#;:/=";
    for _ in 0..3000 {
        let mut bytes = svg.as_bytes().to_vec();
        for _ in 0..1 + next() % 4 {
            let at = (next() % bytes.len() as u64) as usize;
            bytes[at] = alphabet[(next() % alphabet.len() as u64) as usize];
        }
        let text = String::from_utf8(bytes).unwrap();
        let _ = drawing::parse(&text, Some((64, 64)));
    }
}
