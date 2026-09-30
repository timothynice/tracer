mod common;
use studi0trace_core::{intake, render};

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
}

/// Worst channel difference and how many channels differ at all.
fn diff(got: &[u8], want: &[u8]) -> (i32, usize) {
    assert_eq!(got.len(), want.len());
    got.iter().zip(want).fold((0, 0), |(worst, n), (a, b)| {
        let d = (*a as i32 - *b as i32).abs();
        (worst.max(d), n + (d != 0) as usize)
    })
}

const SQUARE: &str = r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#c33"/></svg>"##;

#[test]
fn renders_match_resvg_py_pixel_for_pixel() {
    for case in common::fixture_json("render.json").as_array().unwrap() {
        let stem = case["stem"].as_str().unwrap();
        let (w, h) = (case["width"].as_u64().unwrap() as u32, case["height"].as_u64().unwrap() as u32);
        let svg = String::from_utf8(common::fixture_bytes(&format!("render_{stem}.svg"))).unwrap();
        for (crisp, tag) in [(false, "aa"), (true, "crisp")] {
            let got = render::render(&svg, w, h, crisp).unwrap();
            let want = intake::load(&common::fixture_bytes(&format!("render_{stem}_{tag}.png")), Default::default()).unwrap();
            assert_eq!((want.width, want.height), (w, h));
            let (worst, n) = diff(&got, &want.rgba);
            eprintln!("{stem} {tag}: worst channel difference {worst}, {n} of {} channels differ", got.len());
            assert!(worst <= 1, "{stem} {tag}: worst channel difference {worst}");
        }
    }
}

/// The behaviours `quality.render` leans on, each against the PNG resvg-py produced for it:
/// the size it fits the SVG to, the units it refuses, shape-rendering as a default, text
/// without fonts, and what it will not render at all.
#[test]
fn cases_match_resvg_py() {
    let (mut worst_all, mut channels, mut differing, mut renders, mut refusals) = (0, 0, 0, 0, 0);
    for case in common::fixture_json("render_cases.json").as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let svg = case["svg"].as_str().unwrap();
        let (w, h) = (case["width"].as_u64().unwrap() as u32, case["height"].as_u64().unwrap() as u32);
        for (crisp, tag) in [(false, "aa"), (true, "crisp")] {
            let want = &case[tag];
            let what = format!("{name} ({tag})");
            if want.get("error").is_some() {
                let e = render::render_fit(svg, w, h, crisp).expect_err(&what);
                assert!(!e.is_empty(), "{what}: an empty error");
                assert!(render::render(svg, w, h, crisp).is_err(), "{what}");
                refusals += 1;
                continue;
            }
            let shape = intake::load(&unhex(want["png"].as_str().unwrap()), Default::default()).unwrap();
            let got = render::render_fit(svg, w, h, crisp).unwrap_or_else(|e| panic!("{what}: {e}"));
            assert_eq!((got.width, got.height), (shape.width, shape.height), "{what}: size");
            let (worst, n) = diff(&got.rgba, &shape.rgba);
            assert!(worst <= 1, "{what}: worst channel difference {worst}");
            (worst_all, channels, differing, renders) = (worst_all.max(worst), channels + got.rgba.len(), differing + n, renders + 1);

            // `render` is that render when it is the size asked for, and says so when it is not:
            // resvg-py's wrapper would have resized it with Pillow, and this crate does not.
            if want.get("forced").is_some() {
                let e = render::render(svg, w, h, crisp).expect_err(&what);
                assert!(e.contains("resize"), "{what}: {e}");
            } else {
                assert!(render::render(svg, w, h, crisp).unwrap() == got.rgba, "{what}");
            }
        }
    }
    eprintln!("{renders} renders: worst channel difference {worst_all}, {differing} of {channels} channels differ; {refusals} refusals");
    assert_eq!((renders, refusals), (58, 22), "the fixture has changed");
}

#[test]
fn the_sizes_and_units_behave_as_resvg_py_does() {
    // Spot checks of what the fixture cases above pin, in words.
    let rect = |attrs: &str| format!(r##"<svg xmlns="http://www.w3.org/2000/svg" {attrs}><rect width="5" height="5" fill="#c33"/></svg>"##);
    let size = |svg: &str, w, h| render::render_fit(svg, w, h, false).map(|r| (r.width, r.height));
    // The SVG is scaled to fit inside the box, keeping its aspect, and its size is rounded
    // to whole pixels first.
    assert_eq!(size(SQUARE, 40, 20), Ok((20, 20)));
    assert_eq!(size(SQUARE, 20, 40), Ok((20, 20)));
    assert_eq!(size(&rect(r#"viewBox="0 0 10 3""#), 30, 10), Ok((30, 9)));
    assert_eq!(size(&rect(r#"width="7.4" height="3.6""#), 37, 18), Ok((32, 18)));
    assert_eq!(size(&rect(""), 10, 10), Ok((10, 10)), "no size is 100 x 100");
    // Absolute units are zero lengths, as they are under resvg-py (which leaves dpi at 0).
    for unit in ["10pt", "1in", "10mm", "1cm", "1pc"] {
        let e = size(&rect(&format!(r#"width="{unit}" height="{unit}""#)), 10, 10).unwrap_err();
        assert!(e.contains("invalid size"), "{unit}: {e}");
    }
    assert_eq!(size(&rect(r#"width="2em" height="2em""#), 32, 32), Ok((32, 32)));
}

#[test]
fn a_straight_alpha_pixel_is_not_premultiplied() {
    let svg = r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 1"><rect width="1" height="1" fill="#ff0000" fill-opacity="0.5"/></svg>"##;
    let got = render::render(svg, 2, 1, false).unwrap();
    assert_eq!(got, [255, 0, 0, 128, 0, 0, 0, 0]);
}

#[test]
fn text_is_absent_without_fonts_and_nothing_panics() {
    let with = |body: &str| format!(r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20"><rect width="40" height="20" fill="#eef"/>{body}</svg>"##);
    let none = render::render(&with(""), 80, 40, false).unwrap();
    for text in [
        r##"<text x="2" y="15" font-family="sans-serif" font-size="14" fill="#000">Hi</text>"##,
        r##"<text x="2" y="15" font-family="Arial" font-size="14" fill="#000" stroke="red">Hi</text><text><tspan>x</tspan></text>"##,
        r#"<text>&#x1F600;</text>"#,
    ] {
        assert!(render::render(&with(text), 80, 40, false).unwrap() == none, "{text}");
        assert!(render::render(&with(text), 80, 40, true).unwrap() == render::render(&with(""), 80, 40, true).unwrap(), "{text} (crisp)");
    }
}

#[test]
fn an_embedded_image_is_left_out_and_is_not_an_error() {
    // The core builds resvg without `raster-images`: a traced SVG carries none, and the
    // decoders would go into the WebAssembly build for nothing. resvg-py, which has them,
    // would draw this one.
    let png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
    let with = |body: &str| {
        format!(r##"<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 40 20"><rect width="40" height="20" fill="#eef"/>{body}</svg>"##)
    };
    let image = format!(r#"<image x="5" y="5" width="10" height="10" xlink:href="data:image/png;base64,{png}"/>"#);
    assert!(render::render(&with(&image), 40, 20, false).unwrap() == render::render(&with(""), 40, 20, false).unwrap(), "a raster image was drawn");
}

#[test]
fn an_image_path_is_not_opened() {
    // usvg's default resolver opens whatever file an <image> names and, for an SVG, renders it
    // whether or not resvg has raster decoders. The core must not read the disk.
    let path = common::fixture_path("render_glow-128.svg");
    assert!(path.is_file());
    let with = |body: &str| format!(r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20"><rect width="40" height="20" fill="#eef"/>{body}</svg>"##);
    let image = with(&format!(r#"<image x="2" y="2" width="16" height="16" href="{}"/>"#, path.display()));
    assert!(render::render(&image, 40, 20, false).unwrap() == render::render(&with(""), 40, 20, false).unwrap(), "an image path was opened");
    // ... while an SVG that is inside the document as a data: URL is bytes already in hand.
    let inline = with(r#"<image x="2" y="2" width="16" height="16" href="data:image/svg+xml;utf8,&lt;svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'&gt;&lt;rect width='1' height='1' fill='red'/&gt;&lt;/svg&gt;"/>"#);
    assert!(render::render(&inline, 40, 20, false).unwrap() != render::render(&with(""), 40, 20, false).unwrap(), "an inline SVG image was dropped");
}

#[test]
fn a_size_that_is_not_the_svgs_aspect_is_unsupported() {
    // `quality.render` forces the size with Pillow (Lanczos, or nearest for crisp). The scorecard
    // always renders a candidate at a whole multiple of the size it was normalised to, so that
    // resize never runs; it is not written here, and the mismatch is an error rather than a guess.
    let e = render::render(SQUARE, 40, 20, false).unwrap_err();
    assert!(e.contains("20x20") && e.contains("40x20") && e.contains("resize"), "{e}");
    let got = render::render_fit(SQUARE, 40, 20, true).unwrap();
    assert_eq!((got.width, got.height, got.rgba.len()), (20, 20, 20 * 20 * 4));
}

#[test]
fn a_size_that_cannot_be_allocated_is_an_error_not_an_allocation() {
    // Each of these is a square box round a square SVG, so the fit is the box: none of them
    // may get as far as a pixmap (resvg-py would try a 40 GB one for the second).
    for (w, h) in [(0, 0), (0, 5), (5, 0), (100_000, 100_000), (u32::MAX, u32::MAX), (16_385, 16_385)] {
        assert!(!render::render(SQUARE, w, h, false).unwrap_err().is_empty(), "{w}x{h}");
        assert!(render::render_fit(SQUARE, w, h, true).is_err(), "{w}x{h}");
    }
    assert!(render::render(SQUARE, 1 << 28, 2, false).is_err());
    // A box this large that the SVG does not fill is a small render: it is the fit, not
    // the box, that has to be allocated.
    let got = render::render_fit(SQUARE, u32::MAX, 3, false).unwrap();
    assert_eq!((got.width, got.height), (3, 3));
}

#[test]
fn refusals_carry_the_renderers_words() {
    assert!(render::render("not svg", 10, 10, false).unwrap_err().contains("unknown token"));
    assert!(render::render("", 10, 10, false).is_err());
    assert!(render::render("<svg", 10, 10, true).is_err());
    assert!(render::render(SQUARE, 0, 10, false).unwrap_err().contains("width"));
    assert!(render::render(SQUARE, 10, 0, false).unwrap_err().contains("height"));
}
