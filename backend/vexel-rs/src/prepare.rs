//! Stage 1: colour spaces and alpha inpainting.

use crate::core::colour::rgb2lab;
use crate::core::edt::edt_sq_indices;
use crate::core::grid::{Grid, Image};

pub const ALPHA_FEATURE_SCALE: f64 = 100.0;
/// 8-bit alpha below which a pixel's stored colour is not believed: straight
/// alpha quantises the colour to ±128/alpha levels, ±4 here (the Python's
/// `prepare.INPAINT_ALPHA`, chosen from a survey over the corpus and the
/// held-out set: the median colour error against the nearest solid pixel is
/// 68/44/30/11/4/3 levels for alpha 1–3/4–7/8–15/16–31/32–63/64–127).
pub const INPAINT_ALPHA: u8 = 32;

pub struct Prepared {
    /// (H, W, 3) 0..255, inpainted where alpha == 0
    pub rgb: Image,
    /// (H, W) 0..1
    pub alpha: Grid<f64>,
    /// (H, W, 4) = [L*, a*, b*, 100·alpha]
    pub features: Image,
}

/// px; the neighbourhood whose alpha-weighted mean colour a pixel below
/// INPAINT_ALPHA is read as (`settle_rim`; the Python's `prepare.RIM_REACH`).
pub const RIM_REACH: usize = 2;

/// The colour of every pixel below INPAINT_ALPHA as the alpha-weighted mean of
/// the (2·RIM_REACH+1)² neighbourhood round it — the premultiplied colour of
/// the neighbourhood over its alpha: beside an ink it is the ink, in a faint
/// field it is the field's own colour. Other pixels are returned as they are.
/// The sums run one offset at a time in raster order, as the Python adds them.
fn settle_rim(rgb: &Image, h: usize, w: usize, alpha8: &[u8]) -> Image {
    let mut out = Image::new(h, w, 3);
    out.data.copy_from_slice(&rgb.data);
    let r = RIM_REACH as isize;
    for y in 0..h {
        for x in 0..w {
            let i = y * w + x;
            if alpha8[i] == 0 || alpha8[i] >= INPAINT_ALPHA {
                continue;
            }
            let mut mass = 0.0f64;
            let mut premul = [0.0f64; 3];
            for dy in -r..=r {
                for dx in -r..=r {
                    let (yy, xx) = (y as isize + dy, x as isize + dx);
                    if yy < 0 || xx < 0 || yy >= h as isize || xx >= w as isize {
                        continue;
                    }
                    let j = yy as usize * w + xx as usize;
                    let a = alpha8[j] as f64;
                    mass += a;
                    for ch in 0..3 {
                        premul[ch] += a * rgb.data[j * 3 + ch];
                    }
                }
            }
            if mass > 0.0 {
                for ch in 0..3 {
                    out.data[i * 3 + ch] = premul[ch] / mass;
                }
            }
        }
    }
    out
}

/// Replace RGB under alpha == 0 with the nearest visible pixel's colour — that
/// pixel's settled colour (`settle_rim`) where its alpha is below
/// INPAINT_ALPHA. PNG encoders store arbitrary (often black) RGB under
/// transparent pixels, and a resampled edge's nearly transparent pixels carry
/// quantisation noise (G=255 at alpha 1 beside a green ink): inpainted from
/// those as they are, the transparent field carried seams the partition read
/// as edges. Visible pixels keep the colour they have. `round_f32` rounds the
/// inpainted colour to float32 as the Python's `prepare` keeps its rgb.
pub(crate) fn inpaint_transparent(rgb: &mut Image, h: usize, w: usize, alpha8: &[u8], round_f32: bool) {
    let n = h * w;
    let invisible = Grid { h, w, data: alpha8.iter().map(|a| *a == 0).collect() };
    let n_inv = invisible.count();
    if n_inv == 0 || n_inv == n {
        return;
    }
    let settled = settle_rim(rgb, h, w, alpha8);
    let (_, src_r, src_c) = edt_sq_indices(&invisible);
    for i in 0..n {
        if !invisible.data[i] {
            continue;
        }
        let s = src_r.data[i] as usize * w + src_c.data[i] as usize;
        for ch in 0..3 {
            let v = settled.data[s * 3 + ch];
            rgb.data[i * 3 + ch] = if round_f32 { (v as f32) as f64 } else { v };
        }
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
    let mut alpha8 = vec![0u8; n];
    for i in 0..n {
        rgb.data[i * 3] = rgba[i * 4] as f64;
        rgb.data[i * 3 + 1] = rgba[i * 4 + 1] as f64;
        rgb.data[i * 3 + 2] = rgba[i * 4 + 2] as f64;
        alpha8[i] = rgba[i * 4 + 3];
        alpha.data[i] = (rgba[i * 4 + 3] as f32 / 255.0) as f64;
    }
    inpaint_transparent(&mut rgb, h, w, &alpha8, true);

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
