mod common;
use serde_json::Value;
use studi0trace_core::holes::{self, Holes};
use studi0trace_core::intake;
use vexel_rs::core::{grid::Grid, labels::label_mask};

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
}

/// A mask the exporter wrote as a string of `0` and `1`, row by row.
fn unpack(s: &str) -> Vec<bool> {
    s.bytes().map(|b| b == b'1').collect()
}

fn dim(v: &Value, key: &str) -> usize {
    v[key].as_u64().unwrap() as usize
}

/// The largest relative difference between a float of ours and the Python's, over a whole run.
/// The port adds and divides in the Python's order (and sums pairwise where numpy does), so the
/// floats are the Python's to the bit; the difference is kept to say so, and asserted to be zero.
#[derive(Default)]
struct Worst(f64);

impl Worst {
    fn float(&mut self, got: f64, want: f64, what: &str) {
        let d = (got - want).abs() / (1.0 + want.abs());
        self.0 = self.0.max(d);
        assert!(got == want, "{what}: {got:?} vs {want:?} (relative difference {d:e})");
    }

    /// All of a card's numbers: the four counts and every field of every cluster, in the Python's order.
    fn card(&mut self, got: &Holes, want: &Value, what: &str) {
        for (k, g) in [("hole_subpx", got.hole_subpx), ("hole_clusters", got.hole_clusters), ("pinholes", got.pinholes)] {
            assert_eq!(g as u64, want[k].as_u64().unwrap(), "{what}: {k}");
        }
        self.float(got.hole_px, want["hole_px"].as_f64().unwrap(), &format!("{what}: hole_px"));
        let clusters = want["_clusters"].as_array().unwrap();
        assert_eq!(got.clusters.len(), clusters.len(), "{what}: the number of clusters");
        for (i, (g, w)) in got.clusters.iter().zip(clusters).enumerate() {
            let at = format!("{what}: cluster {i}");
            self.float(g.x, w["x"].as_f64().unwrap(), &format!("{at} x"));
            self.float(g.y, w["y"].as_f64().unwrap(), &format!("{at} y"));
            assert_eq!(g.subpx as u64, w["subpx"].as_u64().unwrap(), "{at} subpx");
            self.float(g.min_cover, w["min_cover"].as_f64().unwrap(), &format!("{at} min_cover"));
            self.float(g.deficit_px, w["deficit_px"].as_f64().unwrap(), &format!("{at} deficit_px"));
            assert_eq!(g.pinhole, w["pinhole"].as_bool().unwrap(), "{at} pinhole");
        }
    }
}

#[test]
fn opaque_is_the_eroded_solid_interior() {
    let data = common::fixture_json("holes.json");
    // Random sources over the alphas around the threshold, and a ramp of every alpha.
    for (i, case) in data["erosion"].as_array().unwrap().iter().enumerate() {
        let (h, w) = (dim(case, "h"), dim(case, "w"));
        let got = holes::opaque(&unhex(case["rgba"].as_str().unwrap()), h, w);
        assert_eq!(got, unpack(case["opaque"].as_str().unwrap()), "erosion case {i} ({h}x{w})");
    }
    // The made sources, which include the 252/253 bands and sources too small to have an interior.
    for (name, s) in data["sources"].as_object().unwrap() {
        let (h, w) = (dim(s, "h"), dim(s, "w"));
        let got = holes::opaque(&unhex(s["rgba"].as_str().unwrap()), h, w);
        assert_eq!(got, unpack(s["opaque"].as_str().unwrap()), "source {name}");
    }
}

/// The hole count leans on `label_mask(_, 2)` numbering 8-connected components the way
/// `ndimage.label(mask, np.ones((3, 3)))` does, so the clusters come back in the Python's order.
#[test]
fn label_numbering_is_ndimages() {
    for (i, case) in common::fixture_json("holes.json")["labels"].as_array().unwrap().iter().enumerate() {
        let (h, w) = (dim(case, "h"), dim(case, "w"));
        let mask = Grid::from_vec(h, w, unpack(case["mask"].as_str().unwrap()));
        let got = label_mask(&mask, 2);
        let want: Vec<i32> = case["labels"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap() as i32).collect();
        assert_eq!(got.data, want, "label case {i} ({h}x{w}) {}", case["name"]);
        assert_eq!(*got.data.iter().max().unwrap_or(&0) as u64, case["n"].as_u64().unwrap());
    }
}

#[test]
fn traced_items_and_their_damaged_copies_match_the_python() {
    let mut worst = Worst::default();
    let mut clusters = 0;
    for case in common::fixture_json("holes.json")["traced"].as_array().unwrap() {
        let src = intake::load(&std::fs::read(common::backend(case["source"].as_str().unwrap())).unwrap(), Default::default()).unwrap();
        let (h, w) = (src.height as usize, src.width as usize);
        let scale = case["scale"].as_u64().unwrap() as u32;
        let what = format!("{} {} x{scale}", case["stem"], case["variant"]);

        let opaque = holes::opaque(&src.rgba, h, w);
        assert_eq!(opaque.iter().filter(|b| **b).count() as u64, case["opaque_count"].as_u64().unwrap(), "{what}: opaque count");
        let bytes: Vec<u8> = opaque.iter().map(|b| *b as u8).collect();
        assert_eq!(common::sha256_hex(&bytes), case["opaque_sha256"].as_str().unwrap(), "{what}: opaque mask");

        let svg = case["svg"].as_str().unwrap();
        let got = holes::holes(svg, &src.rgba, h, w, scale, None).unwrap();
        worst.card(&got, &case["card"], &what);
        // The same again with the mask a `Reference` would have cached.
        let cached = holes::holes(svg, &src.rgba, h, w, scale, Some(&opaque)).unwrap();
        assert_eq!(cached, got, "{what}: a cached mask changes the answer");
        clusters += got.clusters.len();
    }
    eprintln!("traced: {clusters} clusters, worst relative difference {:e}", worst.0);
    assert!(clusters > 100, "the traced cases carry holes: {clusters}");
}

#[test]
fn made_holes_match_the_python() {
    let data = common::fixture_json("holes.json");
    let sources = data["sources"].as_object().unwrap();
    let mut worst = Worst::default();
    let (mut clusters, mut pinholes, mut refused) = (0, 0, 0);
    for case in data["synthetic"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let s = &sources[case["source"].as_str().unwrap()];
        let (h, w) = (dim(s, "h"), dim(s, "w"));
        let rgba = unhex(s["rgba"].as_str().unwrap());
        let scale = case["scale"].as_u64().unwrap() as u32;
        let svg = case["svg"].as_str().unwrap();
        let mask = case["opaque"].as_str().map(unpack);

        let got = holes::holes(svg, &rgba, h, w, scale, mask.as_deref());
        if case["error"].is_string() {
            // the Python raised; so does this, with the renderer's words
            assert!(got.is_err(), "{name}: expected a refusal, got {got:?}");
            refused += 1;
            continue;
        }
        let got = got.unwrap_or_else(|e| panic!("{name}: {e}"));
        worst.card(&got, &case["card"], name);
        clusters += got.clusters.len();
        pinholes += got.pinholes;

        // The mask a Reference would cache gives the same answer as computing it.
        if mask.is_none() {
            let cached = holes::holes(svg, &rgba, h, w, scale, Some(&holes::opaque(&rgba, h, w))).unwrap();
            assert_eq!(cached, got, "{name}: a cached mask changes the answer");
        }
    }
    eprintln!("made: {clusters} clusters, {pinholes} pinholes, {refused} refusals, worst relative difference {:e}", worst.0);
    assert!(clusters > 100 && pinholes > 20 && refused == 2, "the made cases carry holes: {clusters} {pinholes} {refused}");
}

/// The dictionary the Python returns: the same keys in the same order, `_clusters` last, each
/// cluster's fields in order, and (for this case) the same numbers.
#[test]
fn the_map_is_the_dictionary_the_python_returns() {
    let data = common::fixture_json("holes.json");
    let case = data["synthetic"].as_array().unwrap().iter().find(|c| c["name"] == "solid: pin-holes x4").unwrap();
    let s = &data["sources"][case["source"].as_str().unwrap()];
    let (h, w) = (dim(s, "h"), dim(s, "w"));
    let got = holes::holes(case["svg"].as_str().unwrap(), &unhex(s["rgba"].as_str().unwrap()), h, w, 4, None).unwrap();
    assert!(got.clusters.len() > 3);

    let map = got.to_map();
    let keys: Vec<&str> = map.keys().map(String::as_str).collect();
    assert_eq!(keys, ["hole_subpx", "hole_px", "hole_clusters", "pinholes", "_clusters"]);
    let listed = map["_clusters"].as_array().unwrap();
    assert_eq!(listed.len(), got.clusters.len());
    for c in listed {
        let keys: Vec<&str> = c.as_object().unwrap().keys().map(String::as_str).collect();
        assert_eq!(keys, ["x", "y", "subpx", "min_cover", "deficit_px", "pinhole"]);
    }
    assert!(map["hole_subpx"].is_u64() && map["hole_px"].is_f64() && map["hole_clusters"].is_u64() && map["pinholes"].is_u64());
    assert_eq!(Value::Object(map), case["card"], "the numbers");
}

#[test]
fn nothing_to_look_at_is_nothing() {
    // No pixel of the source is opaque inside: the answer comes before the SVG is looked at.
    let svg = "not an svg at all";
    for (h, w) in [(0, 5), (4, 0), (0, 0), (1, 1), (2, 2)] {
        let rgba = vec![255u8; h * w * 4];
        let got = holes::holes(svg, &rgba, h, w, 4, None).unwrap();
        assert_eq!(got, Holes::default(), "{h}x{w}");
        assert!(got.to_map()["_clusters"].as_array().unwrap().is_empty());
    }
    let clear = vec![0u8; 16 * 4];
    assert_eq!(holes::holes(svg, &clear, 4, 4, 0, None).unwrap(), Holes::default(), "scale 0 is not looked at either");
    let shut = vec![false; 16];
    assert_eq!(holes::holes(svg, &clear, 4, 4, 3, Some(&shut)).unwrap(), Holes::default(), "a mask with nothing set");
}

#[test]
fn what_cannot_be_looked_at_is_refused() {
    let solid = vec![255u8; 8 * 8 * 4];
    let ok = r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"/>"#;
    assert!(holes::holes(ok, &solid, 8, 8, 0, None).unwrap_err().contains("width"));
    assert!(holes::holes("<svg", &solid, 8, 8, 2, None).is_err());
    assert!(holes::holes(ok, &solid[1..], 8, 8, 2, None).unwrap_err().contains("255"), "bytes for another size");
    assert!(holes::holes(ok, &[], 8, 8, 2, Some(&vec![true; 63])).unwrap_err().contains("63"), "a mask of another size");
    // with a mask in hand the source itself is not read
    assert_eq!(holes::holes(ok, &[], 8, 8, 2, Some(&vec![true; 64])).unwrap().hole_clusters, 1);
}

/// The opaque alpha threshold is `float32(alpha) / 255 >= 0.99`, which is 253 and up; and a
/// sub-pixel is short when its alpha is under 242.25, which is 242 and down. Every alpha.
#[test]
fn both_alpha_thresholds_are_where_the_python_has_them() {
    // a 3 x 3 block of one alpha inside opaque pixels keeps its centre iff the alpha is opaque
    for a in 0..=255u8 {
        let mut rgba = vec![255u8; 5 * 5 * 4];
        for r in 1..4 {
            for c in 1..4 {
                rgba[(r * 5 + c) * 4 + 3] = a;
            }
        }
        assert_eq!(holes::opaque(&rgba, 5, 5)[2 * 5 + 2], a >= 253, "alpha {a}");
    }
    // a 3 x 3 source that is all opaque, traced to one uniform alpha: a hole iff alpha is under 243
    let solid = vec![255u8; 3 * 3 * 4];
    for a in 236..=250u32 {
        let svg = format!(r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3 3"><rect width="3" height="3" fill="#000" fill-opacity="{}"/></svg>"##, a as f64 / 255.0);
        let got = holes::holes(&svg, &solid, 3, 3, 1, None).unwrap();
        assert_eq!(got.hole_subpx, (a < 243) as usize, "a render of alpha {a}");
    }
}

/// The worst case the scorecard meets: 640 x 640 at 4x with every sub-pixel a hole, one cluster of
/// 6.5 million. It holds real assertions (the cluster, its centre, a hundred small holes beside
/// it) and costs 0.25 s in a release build and about 3 s in a debug one, so it always runs.
#[test]
fn a_640_square_at_4x_with_nothing_drawn() {
    let (h, w, scale) = (640usize, 640usize, 4u32);
    let rgba = vec![255u8; h * w * 4];
    let svg = r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"/>"#;
    let t = std::time::Instant::now();
    let got = holes::holes(svg, &rgba, h, w, scale, None).unwrap();
    eprintln!("640x640 at 4x, one cluster of {} sub-pixels: {:?}", got.hole_subpx, t.elapsed());
    let inner = 638usize;
    assert_eq!((got.hole_subpx, got.hole_clusters, got.pinholes), (inner * inner * 16, 1, 1));
    assert_eq!(got.hole_px, (inner * inner) as f64);
    // sub-pixels 4 .. 2555 on each axis: their mean is 1279.5, which is 319.875 px
    assert_eq!((got.clusters[0].x, got.clusters[0].y), (319.875, 319.875));

    // and the usual case: a hundred small holes in a big drawing
    let mut pits = String::new();
    for i in 0..100 {
        let (x, y) = (7 + (i % 10) * 61, 9 + (i / 10) * 59);
        pits.push_str(&format!("M{x} {y}h3v2h-3z"));
    }
    let svg = format!(r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><path fill-rule="evenodd" fill="#123" d="M0 0h640v640h-640z{pits}"/></svg>"##);
    let t = std::time::Instant::now();
    let got = holes::holes(&svg, &rgba, h, w, scale, None).unwrap();
    eprintln!("640x640 at 4x, {} clusters: {:?}", got.hole_clusters, t.elapsed());
    assert_eq!(got.hole_clusters, 100);
    assert_eq!(got.hole_subpx, 100 * 6 * 16);
}
