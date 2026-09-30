//! Stage 1: colour spaces and alpha inpainting.

use crate::core::colour::rgb2lab;
use crate::core::edt::edt_sq_indices;
use crate::core::grid::{Grid, Image};

pub const ALPHA_FEATURE_SCALE: f64 = 100.0;
/// The alpha (0..255) under which a pixel's colour may be unpremultiply noise:
/// under alpha a an unpremultiplied file holds a colour quantised to steps of
/// 255/a per channel, which over the corpus differs from the neighbours' by
/// about 45/a ΔE, and below 8 a halo of it is all ridge and no seed
/// (`prepare.COLOUR_ALPHA_FLOOR`).
pub const COLOUR_ALPHA_FLOOR: u8 = 8;
/// Such noise is known by its grid: every channel is round(k·255/a) within a
/// level either way; a colour off the grid is a straight-alpha file's own
/// (`prepare.GRID_TOL`).
pub const GRID_TOL: f64 = 1.0;
/// The colour under such a pixel is the alpha-weighted mean of the noise
/// pixels within this radius, its own neighbourhood's samples and never a
/// shape's colour (`prepare.NOISE_RADIUS`).
pub const NOISE_RADIUS: usize = 3;

pub struct Prepared {
    /// (H, W, 3) 0..255, smoothed under unpremultiply noise, inpainted where alpha == 0
    pub rgb: Image,
    /// (H, W) 0..1
    pub alpha: Grid<f64>,
    /// (H, W, 4) = [L*, a*, b*, 100·alpha]
    pub features: Image,
}

/// A pixel under `COLOUR_ALPHA_FLOOR` alpha whose colour lies on the 255/a
/// grid, as the Python's `unpremultiply_noise` decides it (alpha 0 is not this).
#[inline]
fn is_unpremultiply_noise(rgb: &[f64], a: u8) -> bool {
    if a == 0 || a >= COLOUR_ALPHA_FLOOR {
        return false;
    }
    let step = 255.0 / a as f64;
    rgb.iter().all(|c| {
        let k = (c / step).round();
        (c - k * step).abs() <= GRID_TOL
    })
}

/// Sum over the (2r+1)² window, zero beyond the frame: rows then columns,
/// each a run of adds in the order the Python's `_box_sum` adds them.
fn box_sum(a: &[f64], h: usize, w: usize, ch: usize, radius: usize) -> Vec<f64> {
    let r = radius as i64;
    let mut rows = vec![0.0; h * w * ch];
    for y in 0..h {
        for x in 0..w {
            for c in 0..ch {
                let mut acc = 0.0;
                for k in -r..=r {
                    let xx = x as i64 + k;
                    if xx >= 0 && (xx as usize) < w {
                        acc += a[(y * w + xx as usize) * ch + c];
                    }
                }
                rows[(y * w + x) * ch + c] = acc;
            }
        }
    }
    let mut out = vec![0.0; h * w * ch];
    for y in 0..h {
        for x in 0..w {
            for c in 0..ch {
                let mut acc = 0.0;
                for k in -r..=r {
                    let yy = y as i64 + k;
                    if yy >= 0 && (yy as usize) < h {
                        acc += rows[(yy as usize * w + x) * ch + c];
                    }
                }
                out[(y * w + x) * ch + c] = acc;
            }
        }
    }
    out
}

/// Replace the colour under unpremultiply noise with the alpha-weighted mean
/// colour of the noise pixels within `NOISE_RADIUS` (`prepare.smooth_faint_noise`).
fn smooth_faint_noise(rgb: &mut Image, alpha255: &[u8], h: usize, w: usize) {
    let n = h * w;
    let noise: Vec<bool> = (0..n).map(|i| is_unpremultiply_noise(&rgb.data[i * 3..i * 3 + 3], alpha255[i])).collect();
    if !noise.iter().any(|b| *b) {
        return;
    }
    let wgt: Vec<f64> = (0..n).map(|i| if noise[i] { alpha255[i] as f64 } else { 0.0 }).collect();
    let prem: Vec<f64> = (0..n * 3).map(|j| rgb.data[j] * wgt[j / 3]).collect();
    let num = box_sum(&prem, h, w, 3, NOISE_RADIUS);
    let den = box_sum(&wgt, h, w, 1, NOISE_RADIUS);
    for i in 0..n {
        if noise[i] {
            for c in 0..3 {
                // the Python holds the colour in float32
                rgb.data[i * 3 + c] = (num[i * 3 + c] / den[i]) as f32 as f64;
            }
        }
    }
}

/// Replace RGB under alpha == 0 with the nearest visible pixel's colour. PNG
/// encoders store arbitrary (often black) RGB there, and letting that into the
/// gradient fits or the edge detection would be wrong. `alpha255` is the
/// file's alpha, 0..255.
fn inpaint_transparent(rgb: &mut Image, alpha255: &[u8], h: usize, w: usize) {
    let n = h * w;
    let invisible = Grid { h, w, data: alpha255.iter().map(|a| *a == 0).collect() };
    let n_inv = invisible.count();
    if n_inv == 0 || n_inv == n {
        return;
    }
    let (_, src_r, src_c) = edt_sq_indices(&invisible);
    let src: Vec<usize> = (0..n)
        .map(|i| src_r.data[i] as usize * w + src_c.data[i] as usize)
        .collect();
    let snapshot = rgb.data.clone();
    for i in 0..n {
        if !invisible.data[i] {
            continue;
        }
        let s = src[i];
        rgb.data[i * 3..i * 3 + 3].copy_from_slice(&snapshot[s * 3..s * 3 + 3]);
    }
}

/// `alpha · 255` as the Python computes it: its alpha is float32 and so is
/// `prep.alpha * 255.0`, which rounds back to the integer alpha exactly
/// (f32(242/255)·255 is 242). Widened to f64 first, it is 242.00000077, and on
/// a translucent low-contrast edge that is 1.5e-6 px of placed vertex.
#[inline]
pub fn alpha255(alpha: f64) -> f64 {
    (alpha as f32 * 255.0f32) as f64
}

pub fn prepare(rgba: &[u8], h: usize, w: usize) -> Prepared {
    let n = h * w;
    let mut rgb = Image::new(h, w, 3);
    let mut alpha = Grid::<f64>::new(h, w);
    let mut alpha255 = vec![0u8; n];
    for i in 0..n {
        rgb.data[i * 3] = rgba[i * 4] as f64;
        rgb.data[i * 3 + 1] = rgba[i * 4 + 1] as f64;
        rgb.data[i * 3 + 2] = rgba[i * 4 + 2] as f64;
        alpha255[i] = rgba[i * 4 + 3];
        alpha.data[i] = (rgba[i * 4 + 3] as f32 / 255.0) as f64;
    }
    smooth_faint_noise(&mut rgb, &alpha255, h, w);
    inpaint_transparent(&mut rgb, &alpha255, h, w);

    let mut features = Image::new(h, w, 4);
    for i in 0..n {
        let lab = rgb2lab(rgb.data[i * 3], rgb.data[i * 3 + 1], rgb.data[i * 3 + 2]);
        // the Python keeps features in float32; the partition thresholds read
        // them back, so round here rather than carrying spurious precision
        features.data[i * 4] = lab[0] as f32 as f64;
        features.data[i * 4 + 1] = lab[1] as f32 as f64;
        features.data[i * 4 + 2] = lab[2] as f32 as f64;
        features.data[i * 4 + 3] = (alpha.data[i] * ALPHA_FEATURE_SCALE) as f32 as f64;
    }
    Prepared { rgb, alpha, features }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn alpha255_is_the_integer_alpha_the_python_gets() {
        // the prepared alpha is float32's a/255; scaled back in float32, as
        // numpy scales the Python's, it is `a` exactly
        for a in 0..=255u8 {
            let alpha = (a as f32 / 255.0) as f64;
            assert_eq!(alpha255(alpha), a as f64);
        }
        // widened first, it is not
        assert_ne!((242.0f32 / 255.0) as f64 * 255.0, 242.0);
    }
}
