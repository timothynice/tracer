mod common;
use studi0trace_core::render::RenderError;
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
                assert!(!e.to_string().is_empty(), "{what}: an empty error");
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

            // `render` is that render when it is the size asked for; when it is not, it is the
            // size asked for anyway (what it holds is `render_resize.json`'s business).
            if want.get("forced").is_some() {
                assert_eq!(render::render(svg, w, h, crisp).unwrap().len(), (w * h * 4) as usize, "{what}");
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
        assert!(matches!(&e, RenderError::Svg(m) if m.contains("invalid size")), "{unit}: {e}");
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

/// A size that is not the fit: `quality.render` resizes with Pillow and so does `render`.
/// Every expected image here is Pillow's, from `render_resize.json`; `resample.rs` holds the
/// resampler itself to Pillow over hundreds of shapes.
#[test]
fn a_render_that_misses_its_size_is_resized_as_quality_render_does() {
    let fixture = common::fixture_json("render_resize.json");
    let (mut worst_all, mut differing, mut n) = (0, 0, 0);
    for case in fixture["small"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let svg = case["svg"].as_str().unwrap();
        let (w, h) = (case["width"].as_u64().unwrap() as u32, case["height"].as_u64().unwrap() as u32);
        for (crisp, tag) in [(false, "aa"), (true, "crisp")] {
            let what = format!("{name} ({tag})");
            // a fixture, not an upload: the renders run past the intake's 2048 px a side
            let unlimited = intake::Limits { max_side: None, ..Default::default() };
            let want = intake::load(&unhex(case[tag]["png"].as_str().unwrap()), unlimited).unwrap();
            assert_eq!((want.width, want.height), (w, h), "{what}: the fixture");

            // The render resvg makes is another size than the box ...
            let fit = render::render_fit(svg, w, h, crisp).unwrap();
            let fit_size: Vec<u64> = case[tag]["fit"].as_array().unwrap().iter().map(|v| v.as_u64().unwrap()).collect();
            assert_eq!([fit.width as u64, fit.height as u64], fit_size[..], "{what}: the fit");
            assert_ne!((fit.width, fit.height), (w, h), "{what}: this case no longer needs the resize");

            // ... and `render` is Pillow's resize of it.
            let got = render::render(svg, w, h, crisp).unwrap_or_else(|e| panic!("{what}: {e}"));
            let (worst, differ) = diff(&got, &want.rgba);
            assert!(worst <= 1, "{what}: worst channel difference {worst}");
            (worst_all, differing, n) = (worst_all.max(worst), differing + differ, n + 1);
        }
    }
    eprintln!("{n} resized renders: worst channel difference {worst_all}, {differing} channels differ");
    assert_eq!(n, 20);
}

/// The sizes a scorecard run reaches: a viewBox the size of the upload, drawn at its own size,
/// which resvg fits a row too tall (f32 rounding in `IntSize::scale_to`) and Pillow then
/// trims. The images are 16.8 MP, so only their SHA-256 is kept.
#[test]
fn a_viewbox_drawn_at_its_own_size_comes_out_that_size_when_resvg_fits_it_a_row_too_tall() {
    for case in common::fixture_json("render_resize.json")["large"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let svg = case["svg"].as_str().unwrap();
        let (w, h) = (case["width"].as_u64().unwrap() as u32, case["height"].as_u64().unwrap() as u32);
        for (crisp, tag) in [(false, "aa"), (true, "crisp")] {
            let fit = render::render_fit(svg, w, h, crisp).unwrap();
            let want: Vec<u64> = case[tag]["fit"].as_array().unwrap().iter().map(|v| v.as_u64().unwrap()).collect();
            assert_eq!([fit.width as u64, fit.height as u64], want[..], "{name} ({tag}): the fit");
            assert_ne!((fit.width, fit.height), (w, h), "{name} ({tag}): this case no longer needs the resize");
            drop(fit);

            let got = render::render(svg, w, h, crisp).unwrap_or_else(|e| panic!("{name} ({tag}): {e}"));
            assert_eq!(got.len(), (w * h * 4) as usize);
            assert!(common::sha256_hex(&got) == case[tag]["sha256"].as_str().unwrap(), "{name} ({tag}): not quality.render's bytes");
        }
    }
}

#[test]
fn an_exact_fit_is_not_resized() {
    let svg = r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480"><rect width="640" height="480" fill="#c33"/></svg>"##;
    let fit = render::render_fit(svg, 1280, 960, false).unwrap();
    assert_eq!((fit.width, fit.height), (1280, 960));
    assert!(render::render(svg, 1280, 960, false).unwrap() == fit.rgba);
}

#[test]
fn a_size_that_cannot_be_allocated_is_an_error_not_an_allocation() {
    // The cap is on what is asked for as well as on what resvg draws, and it comes before
    // anything is allocated. Each of these boxes is over it, for a square SVG whose fit is
    // the box or far smaller than it.
    let cap = |e: RenderError| assert!(matches!(e, RenderError::TooLarge { .. }) && e.to_string().contains("pixels allowed"), "{e}");
    for (w, h) in [(100_000, 100_000), (u32::MAX, u32::MAX), (16_385, 16_385), (1 << 28, 2), (2, 1 << 28), (u32::MAX, 3)] {
        cap(render::render(SQUARE, w, h, false).unwrap_err());
        cap(render::render(SQUARE, w, h, true).unwrap_err());
    }
    // `render_fit` has only the fit to allocate: a square SVG in a box that is over the cap
    // on one side only is small, and one whose fit is over it is refused.
    for (w, h) in [(16_385, 16_385), (100_000, 100_000), (u32::MAX, u32::MAX)] {
        cap(render::render_fit(SQUARE, w, h, true).unwrap_err());
    }
    let got = render::render_fit(SQUARE, u32::MAX, 3, false).unwrap();
    assert_eq!((got.width, got.height), (3, 3));
    // Inside the cap, a box much wider than the fit is a resize, and it is allowed.
    assert_eq!(render::render(SQUARE, 1 << 14, 1, false).map(|v| v.len()), Ok((1 << 14) * 4));
    for (w, h) in [(0, 0), (0, 5), (5, 0)] {
        assert!(matches!(render::render(SQUARE, w, h, false).unwrap_err(), RenderError::ZeroSide(_)), "{w}x{h}");
        assert!(render::render_fit(SQUARE, w, h, true).is_err(), "{w}x{h}");
    }
}

#[test]
fn refusals_carry_the_renderers_words() {
    assert!(matches!(render::render("not svg", 10, 10, false).unwrap_err(), RenderError::Svg(m) if m.contains("unknown token")));
    assert!(render::render("", 10, 10, false).is_err());
    assert!(render::render("<svg", 10, 10, true).is_err());
    assert_eq!(render::render(SQUARE, 0, 10, false).unwrap_err(), RenderError::ZeroSide("width"));
    assert_eq!(render::render(SQUARE, 10, 0, false).unwrap_err(), RenderError::ZeroSide("height"));
    // resvg-py's words
    assert_eq!(RenderError::ZeroSide("width").to_string(), "The value of 'width' must be a positive integer");
}

/// `levels` nested groups around one square.
fn nested(levels: usize) -> String {
    format!(r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">{}<rect width="10" height="10" fill="#c33"/>{}</svg>"##, "<g>".repeat(levels), "</g>".repeat(levels))
}

#[test]
fn an_svg_nested_beyond_reach_is_refused_by_every_way_into_the_renderer_before_it_parses() {
    // 100 000 nested groups overflow the stack of any thread in resvg's recursive parser; a refusal
    // from the text alone is quick and cannot overflow, here on a thread with a small stack on purpose
    let deep = nested(100_000);
    let answer = std::thread::Builder::new()
        .stack_size(512 * 1024)
        .spawn(move || {
            let started = std::time::Instant::now();
            let a = render::render(&deep, 16, 16, false);
            let b = render::render(&deep, 16, 16, true);
            let c = render::render_fit(&deep, 16, 16, false);
            (a.map(|v| v.len()), b.map(|v| v.len()), c.map(|r| r.rgba.len()), started.elapsed())
        })
        .unwrap()
        .join()
        .expect("a refusal, not an overflow");
    let (a, b, c, took) = answer;
    for e in [a.unwrap_err(), b.unwrap_err(), c.unwrap_err()] {
        assert!(e == RenderError::TooDeep && e.to_string().contains("nested more than 988"), "{e}");
    }
    assert!(took.as_secs() < 5, "{took:?}");
    // the limit is the drawing's: 987 levels draw, 988 are refused, as the Python's recursion would
    // (resvg wants about 3.5 MiB of stack to render one that deep, so it is rendered on a thread that has it)
    let (ok, over) = std::thread::Builder::new()
        .stack_size(64 * 1024 * 1024)
        .spawn(|| (render::render(&nested(987), 16, 16, false).is_ok(), render::render(&nested(988), 16, 16, false).is_err()))
        .unwrap()
        .join()
        .unwrap();
    assert!(ok, "987 levels render");
    assert!(over, "988 levels are refused");
}
