//! Stage 0b: trace a small input at twice its size when it has thin features.
//! Mirrors `upsample.py`, literal for literal, so the two upsamples agree to the byte.

use crate::core::labels::Labels;

pub const UPSAMPLE_MAX_SIDE: usize = 192;
pub const THIN_WIDTH: f64 = 2.2;
pub const MIN_AREA: usize = 6;

const W_EVEN: [f64; 6] = [-0.06850269446295365, 0.273025020812246, 0.8994068413745139, -0.13426528114738912, 0.030336113423582882, 0.0];
const W_ODD: [f64; 6] = [0.030112285361897653, -0.1332746355359615, 0.8927707740853273, 0.2710105682570789, -0.06799726302855182, 0.0073782708602093345];

/// Double the row count of an (n, m) f64 image with `c` channels: out[(2i+parity), j, ch].
fn pass_rows(a: &[f64], n: usize, m: usize, c: usize) -> Vec<f64> {
    let mut out = vec![0.0f64; 2 * n * m * c];
    for (parity, weights) in [(0usize, &W_EVEN), (1usize, &W_ODD)] {
        for i in 0..n {
            for j in 0..m {
                for ch in 0..c {
                    let mut acc = 0.0f64;
                    for (t, w) in weights.iter().enumerate() {
                        let k = i as i64 + t as i64 - 2;
                        let src = k.clamp(0, n as i64 - 1) as usize;
                        acc += w * a[(src * m + j) * c + ch];
                    }
                    out[((2 * i + parity) * m + j) * c + ch] = acc;
                }
            }
        }
    }
    out
}

fn transpose(a: &[f64], n: usize, m: usize, c: usize) -> Vec<f64> {
    let mut out = vec![0.0f64; n * m * c];
    for i in 0..n {
        for j in 0..m {
            for ch in 0..c {
                out[(j * n + i) * c + ch] = a[(i * m + j) * c + ch];
            }
        }
    }
    out
}

/// (H, W, 4) u8 → (2H, 2W, 4) u8, Lanczos-3, channels straight. See the Python `upsample2x`.
pub fn upsample2x(rgba: &[u8], h: usize, w: usize) -> Vec<u8> {
    let a: Vec<f64> = rgba.iter().map(|v| *v as f64).collect();
    let rows = pass_rows(&a, h, w, 4); // (2h, w)
    let t = transpose(&rows, 2 * h, w, 4); // (w, 2h)
    let cols = pass_rows(&t, w, 2 * h, 4); // (2w, 2h)
    let back = transpose(&cols, 2 * w, 2 * h, 4); // (2h, 2w)
    back.iter().map(|v| (v + 0.5).floor().clamp(0.0, 255.0) as u8).collect()
}

/// The smallest 2·area/perimeter over regions of at least MIN_AREA pixels; see the Python.
pub fn thinnest_region(l: &Labels) -> f64 {
    let (h, w) = (l.h, l.w);
    let mut ids: Vec<i32> = l.data.iter().copied().filter(|v| *v != 0).collect();
    ids.sort_unstable();
    ids.dedup();
    let mut best = f64::INFINITY;
    for lab in ids {
        let mut area = 0usize;
        let mut per = 0usize;
        for y in 0..h {
            for x in 0..w {
                let here = l.data[y * w + x] == lab;
                if here {
                    area += 1;
                }
                if x + 1 < w && here != (l.data[y * w + x + 1] == lab) {
                    per += 1;
                }
                if y + 1 < h && here != (l.data[(y + 1) * w + x] == lab) {
                    per += 1;
                }
            }
        }
        if area < MIN_AREA {
            continue;
        }
        best = best.min(2.0 * area as f64 / per.max(1) as f64);
    }
    best
}

pub fn wants_upsample(l: &Labels, height: usize, width: usize) -> bool {
    height.max(width) <= UPSAMPLE_MAX_SIDE && thinnest_region(l) < THIN_WIDTH
}

/// The SVG of the 2× trace drawn at the original size. See the Python `halve`.
pub fn halve(svg: &str, width: usize, height: usize) -> String {
    let Some(root_end) = svg.find('>') else { return svg.to_string() };
    let root = &svg[..=root_end];
    let rest = &svg[root_end + 1..];
    let Some(body) = rest.strip_suffix("</svg>") else { return svg.to_string() };
    let root = match (root.find("viewBox=\""), root.find("viewBox=\"").and_then(|s| root[s + 9..].find('"').map(|e| s + 9 + e))) {
        (Some(s), Some(e)) => format!("{}viewBox=\"0 0 {} {}\"{}", &root[..s], width, height, &root[e + 1..]),
        _ => root.to_string(),
    };
    let (defs, elements) = if body.starts_with("<defs>") {
        match body.find("</defs>") {
            Some(e) => (&body[..e + 7], &body[e + 7..]),
            None => ("", body),
        }
    } else {
        ("", body)
    };
    format!("{}{}<g transform=\"scale(0.5)\">{}</g></svg>", root, defs, elements)
}
