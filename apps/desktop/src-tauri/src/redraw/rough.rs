//! The inspector's quiet hint: an image *looks rough* when its longest side is under [`ROUGH_SIDE`], or when it is
//! an exact 2x nearest-neighbour upscale on both axes: every complete row pair and column pair byte-identical at
//! some phase, and the native image (one pixel of each pair) not itself doubled, since a replication at half size
//! too is crisp art aligned to the pixel grid, not an upscale. The rule held on all 224 corpus and held-out items
//! in the wave-lockup work (`backend/bench/reports/keep-2026-10-05-wave-lockup/survey/d0_undouble.py`).

use super::ROUGH_SIDE;
use serde::Serialize;

/// An axis shorter than this is never called doubled.
pub const MIN_DOUBLED_SIDE: usize = 8;

/// Why an image looks rough.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Reason {
    Small,
    Doubled,
}

/// `image_roughness`'s answer: `{"rough": true, "reason": "small"}`, or `{"rough": false, "reason": null}`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Roughness {
    pub rough: bool,
    pub reason: Option<Reason>,
}

/// The phase p ∈ {0, 1} at which every complete pair (p + 2k, p + 2k + 1) of rows (`rows`) or of columns is
/// byte-identical in all four channels, phase 0 tried first; None if neither holds or the axis is shorter than
/// [`MIN_DOUBLED_SIDE`].
pub fn doubling_phase(rgba: &[u8], w: usize, h: usize, rows: bool) -> Option<usize> {
    let n = if rows { h } else { w };
    if n < MIN_DOUBLED_SIDE {
        return None;
    }
    let same = |a: usize, b: usize| -> bool {
        if rows {
            rgba[a * w * 4..(a + 1) * w * 4] == rgba[b * w * 4..(b + 1) * w * 4]
        } else {
            (0..h).all(|y| rgba[(y * w + a) * 4..(y * w + a) * 4 + 4] == rgba[(y * w + b) * 4..(y * w + b) * 4 + 4])
        }
    };
    (0..2usize).find(|&p| (0..(n - p) / 2).all(|k| same(p + 2 * k, p + 2 * k + 1)))
}

/// One pixel of each pair: pixel (2i, 2j) of the image with its phase's first row and column repeated at the top
/// left (`pr`, `pc`), as `(pixels, width, height)`.
fn native(rgba: &[u8], w: usize, h: usize, pr: usize, pc: usize) -> (Vec<u8>, usize, usize) {
    let (nh, nw) = ((h + pr).div_ceil(2), (w + pc).div_ceil(2));
    let mut out = vec![0u8; nh * nw * 4];
    for i in 0..nh {
        let y = (2 * i).saturating_sub(pr).min(h - 1);
        for j in 0..nw {
            let x = (2 * j).saturating_sub(pc).min(w - 1);
            out[(i * nw + j) * 4..(i * nw + j) * 4 + 4].copy_from_slice(&rgba[(y * w + x) * 4..(y * w + x) * 4 + 4]);
        }
    }
    (out, nw, nh)
}

/// An exact 2x nearest-neighbour upscale, doubled once: both axes doubled, each at its own phase, and the native
/// image not doubled on both again.
pub fn is_doubled(rgba: &[u8], w: u32, h: u32) -> bool {
    let (w, h) = (w as usize, h as usize);
    let (Some(pr), Some(pc)) = (doubling_phase(rgba, w, h, true), doubling_phase(rgba, w, h, false)) else {
        return false;
    };
    let (half, nw, nh) = native(rgba, w, h, pr, pc);
    !(doubling_phase(&half, nw, nh, true).is_some() && doubling_phase(&half, nw, nh, false).is_some())
}

/// Whether a `w` x `h` RGBA image looks rough, and why (small first).
pub fn assess(rgba: &[u8], w: u32, h: u32) -> Roughness {
    if w.max(h) < ROUGH_SIDE {
        return Roughness { rough: true, reason: Some(Reason::Small) };
    }
    if is_doubled(rgba, w, h) {
        return Roughness { rough: true, reason: Some(Reason::Doubled) };
    }
    Roughness { rough: false, reason: None }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn noise(w: usize, h: usize, seed: u64) -> Vec<u8> {
        let mut s = seed | 1;
        (0..w * h)
            .flat_map(|_| -> [u8; 4] {
                s ^= s << 13;
                s ^= s >> 7;
                s ^= s << 17;
                [s as u8, (s >> 8) as u8, (s >> 16) as u8, 255]
            })
            .collect()
    }

    fn upscale(src: &[u8], w: usize, h: usize, k: usize) -> Vec<u8> {
        let mut out = Vec::with_capacity(src.len() * k * k);
        for y in 0..h * k {
            for x in 0..w * k {
                let i = ((y / k) * w + x / k) * 4;
                out.extend_from_slice(&src[i..i + 4]);
            }
        }
        out
    }

    fn stretch_columns(src: &[u8], w: usize, h: usize) -> Vec<u8> {
        let mut out = Vec::with_capacity(src.len() * 2);
        for y in 0..h {
            for x in 0..w * 2 {
                let i = (y * w + x / 2) * 4;
                out.extend_from_slice(&src[i..i + 4]);
            }
        }
        out
    }

    fn drop_first_row_and_column(rgba: &[u8], w: usize, h: usize) -> Vec<u8> {
        (1..h).flat_map(|y| rgba[(y * w + 1) * 4..(y + 1) * w * 4].to_vec()).collect()
    }

    /// Three vertical bands, cut at `cuts`.
    fn bands(w: usize, h: usize, cuts: [usize; 2]) -> Vec<u8> {
        let colours: [[u8; 4]; 3] = [[200, 30, 30, 255], [30, 160, 60, 255], [20, 40, 220, 255]];
        (0..w * h).flat_map(|i| colours[usize::from(i % w >= cuts[0]) + usize::from(i % w >= cuts[1])]).collect()
    }

    #[test]
    fn a_small_image_is_rough_whatever_it_holds() {
        assert_eq!(assess(&noise(599, 300, 1), 599, 300), Roughness { rough: true, reason: Some(Reason::Small) });
        assert_eq!(assess(&noise(300, 599, 2), 300, 599).reason, Some(Reason::Small));
        assert_eq!(assess(&noise(600, 300, 3), 600, 300), Roughness { rough: false, reason: None });
        assert!(!assess(&noise(300, 600, 4), 300, 600).rough);
        // small is said first: a small doubled image is small
        assert_eq!(assess(&upscale(&noise(100, 100, 5), 100, 100, 2), 200, 200).reason, Some(Reason::Small));
    }

    #[test]
    fn an_exact_2x_upscale_is_rough_at_either_phase() {
        let doubled = upscale(&noise(400, 350, 6), 400, 350, 2);
        assert_eq!(doubling_phase(&doubled, 800, 700, true), Some(0));
        assert_eq!(doubling_phase(&doubled, 800, 700, false), Some(0));
        assert_eq!(assess(&doubled, 800, 700), Roughness { rough: true, reason: Some(Reason::Doubled) });
        let shifted = drop_first_row_and_column(&doubled, 800, 700);
        assert_eq!(doubling_phase(&shifted, 799, 699, true), Some(1));
        assert_eq!(doubling_phase(&shifted, 799, 699, false), Some(1));
        assert_eq!(assess(&shifted, 799, 699).reason, Some(Reason::Doubled));
    }

    #[test]
    fn doubled_twice_is_grid_art_not_an_upscale() {
        let quadrupled = upscale(&noise(200, 175, 7), 200, 175, 4);
        assert!(!is_doubled(&quadrupled, 800, 700));
        assert_eq!(assess(&quadrupled, 800, 700), Roughness { rough: false, reason: None });
    }

    #[test]
    fn crisp_three_colour_art_is_not_rough() {
        let art = bands(700, 700, [233, 466]);
        assert_eq!(doubling_phase(&art, 700, 700, true), Some(0)); // every row is the same
        assert_eq!(doubling_phase(&art, 700, 700, false), None); // a cut inside a pair at either phase
        assert_eq!(assess(&art, 700, 700), Roughness { rough: false, reason: None });
    }

    #[test]
    fn one_doubled_axis_is_a_stretch_not_an_upscale() {
        assert!(!is_doubled(&stretch_columns(&noise(400, 700, 8), 400, 700), 800, 700));
    }

    #[test]
    fn the_answer_is_the_commands_json() {
        assert_eq!(serde_json::to_value(Roughness { rough: true, reason: Some(Reason::Doubled) }).unwrap(), serde_json::json!({"rough": true, "reason": "doubled"}));
        assert_eq!(serde_json::to_value(Roughness { rough: true, reason: Some(Reason::Small) }).unwrap(), serde_json::json!({"rough": true, "reason": "small"}));
        assert_eq!(serde_json::to_value(Roughness { rough: false, reason: None }).unwrap(), serde_json::json!({"rough": false, "reason": null}));
    }
}
