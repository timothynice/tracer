//! End-to-end tests for the engine: known pictures in, recognisable SVG out.

use vexel_rs::engine::{trace_rgba, VexelParams};

/// An RGBA buffer with a filled axis-aligned rectangle of `rgb` on transparency.
fn rect(h: usize, w: usize, r0: usize, r1: usize, c0: usize, c1: usize, rgb: [u8; 3]) -> Vec<u8> {
    let mut buf = vec![0u8; h * w * 4];
    for r in r0..r1 {
        for c in c0..c1 {
            let i = (r * w + c) * 4;
            buf[i] = rgb[0];
            buf[i + 1] = rgb[1];
            buf[i + 2] = rgb[2];
            buf[i + 3] = 255;
        }
    }
    buf
}

fn disc(h: usize, w: usize, cy: f64, cx: f64, radius: f64, rgb: [u8; 3]) -> Vec<u8> {
    let mut buf = vec![0u8; h * w * 4];
    for r in 0..h {
        for c in 0..w {
            // 4x4 supersampled coverage, so the edge is anti-aliased like real art
            let mut hits = 0;
            for sy in 0..4 {
                for sx in 0..4 {
                    let y = r as f64 + (sy as f64 + 0.5) / 4.0;
                    let x = c as f64 + (sx as f64 + 0.5) / 4.0;
                    if (y - cy).hypot(x - cx) <= radius {
                        hits += 1;
                    }
                }
            }
            let i = (r * w + c) * 4;
            buf[i] = rgb[0];
            buf[i + 1] = rgb[1];
            buf[i + 2] = rgb[2];
            buf[i + 3] = (hits * 255 / 16) as u8;
        }
    }
    buf
}

#[test]
fn a_blank_image_traces_to_an_empty_svg() {
    let buf = vec![0u8; 32 * 32 * 4];
    let svg = trace_rgba(&buf, 32, 32, &VexelParams::default());
    assert!(svg.starts_with("<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 32 32\">"));
    assert!(svg.ends_with("</svg>"));
    assert!(!svg.contains("<path"), "nothing is visible, so nothing is painted: {svg}");
}

#[test]
fn a_rectangle_comes_out_as_a_rect_primitive() {
    let buf = rect(48, 48, 12, 36, 8, 40, [0x45, 0x7b, 0x9d]);
    let svg = trace_rgba(&buf, 48, 48, &VexelParams::default());
    assert!(svg.contains("<rect "), "expected a rect primitive, got: {svg}");
    assert!(svg.contains("#457b9d"), "the fill keeps the source colour: {svg}");
    // the traced edges sit on the pixel boundaries the rectangle was drawn at
    for needle in ["x=\"8\"", "y=\"12\"", "width=\"32\"", "height=\"24\""] {
        assert!(svg.contains(needle), "expected {needle} in {svg}");
    }
}

#[test]
fn a_disc_comes_out_as_a_circle_primitive() {
    let buf = disc(64, 64, 32.0, 32.0, 20.0, [0xe6, 0x3e, 0x62]);
    let svg = trace_rgba(&buf, 64, 64, &VexelParams::default());
    assert!(svg.contains("<circle "), "expected a circle primitive, got: {svg}");
    let r: f64 = svg
        .split("r=\"")
        .nth(1)
        .and_then(|s| s.split('"').next())
        .and_then(|s| s.parse().ok())
        .expect("a radius");
    assert!((r - 20.0).abs() < 0.6, "radius {r} should be within a pixel of 20");
}

#[test]
fn shape_fitting_off_falls_back_to_a_path() {
    let buf = disc(64, 64, 32.0, 32.0, 20.0, [0xe6, 0x3e, 0x62]);
    let p = VexelParams { shape_fitting: false, ..VexelParams::default() };
    let svg = trace_rgba(&buf, 64, 64, &p);
    assert!(!svg.contains("<circle"), "the parameter must be honoured: {svg}");
    assert!(svg.contains("<path "));
}

#[test]
fn a_linear_ramp_becomes_one_gradient_rather_than_bands() {
    let (h, w) = (64, 64);
    let mut buf = vec![0u8; h * w * 4];
    for r in 0..h {
        for c in 0..w {
            let i = (r * w + c) * 4;
            let t = c as f64 / (w - 1) as f64;
            buf[i] = (20.0 + 210.0 * t) as u8;
            buf[i + 1] = 40;
            buf[i + 2] = (230.0 - 200.0 * t) as u8;
            buf[i + 3] = 255;
        }
    }
    let svg = trace_rgba(&buf, w, h, &VexelParams::default());
    assert!(svg.contains("<linearGradient"), "a ramp is one gradient, not bands: {svg}");
    assert_eq!(svg.matches("<path").count() + svg.matches("<rect").count(), 1);
}

#[test]
fn gradients_off_posterises_the_same_ramp() {
    let (h, w) = (64, 64);
    let mut buf = vec![0u8; h * w * 4];
    for r in 0..h {
        for c in 0..w {
            let i = (r * w + c) * 4;
            let t = c as f64 / (w - 1) as f64;
            buf[i] = (20.0 + 210.0 * t) as u8;
            buf[i + 1] = 40;
            buf[i + 2] = (230.0 - 200.0 * t) as u8;
            buf[i + 3] = 255;
        }
    }
    let p = VexelParams { gradients: false, ..VexelParams::default() };
    let svg = trace_rgba(&buf, w, h, &p);
    assert!(!svg.contains("Gradient"), "gradients were turned off: {svg}");
    assert!(svg.matches("<path").count() + svg.matches("<rect").count() > 2, "expected bands");
}

#[test]
fn the_output_is_reproducible() {
    let buf = disc(64, 64, 30.0, 34.0, 18.0, [0x2a, 0x9d, 0x8f]);
    let p = VexelParams::default();
    let a = trace_rgba(&buf, 64, 64, &p);
    let b = trace_rgba(&buf, 64, 64, &p);
    assert_eq!(a, b, "the same picture must trace the same way twice");
}

#[test]
fn path_precision_controls_the_coordinate_decimals() {
    let buf = disc(64, 64, 32.0, 31.0, 19.0, [0x11, 0x22, 0x33]);
    for precision in [0usize, 3] {
        let p = VexelParams { path_precision: precision, shape_fitting: false, ..VexelParams::default() };
        let svg = trace_rgba(&buf, 64, 64, &p);
        let worst = svg
            .split(|ch: char| !(ch.is_ascii_digit() || ch == '.' || ch == '-'))
            .filter_map(|tok| tok.split_once('.'))
            .map(|(_, frac)| frac.len())
            .max()
            .unwrap_or(0);
        assert!(worst <= precision.max(3), "precision {precision} produced {worst} decimals");
    }
}

#[test]
fn a_hole_stays_a_hole() {
    // a ring: an opaque square with a transparent square cut out of it
    let (h, w) = (64, 64);
    let mut buf = rect(h, w, 8, 56, 8, 56, [0x22, 0x33, 0x44]);
    for r in 24..40 {
        for c in 24..40 {
            buf[(r * w + c) * 4 + 3] = 0;
        }
    }
    let svg = trace_rgba(&buf, w, h, &VexelParams::default());
    assert!(
        svg.contains("fill-rule=\"evenodd\""),
        "the cut-out must be a second contour on the same path: {svg}"
    );
}
