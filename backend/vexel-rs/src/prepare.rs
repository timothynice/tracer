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
    /// (H, W, 3) 0..255, inpainted below INPAINT_ALPHA
    pub rgb: Image,
    /// (H, W) 0..1
    pub alpha: Grid<f64>,
    /// (H, W, 4) = [L*, a*, b*, 100·alpha]
    pub features: Image,
}

/// Replace the RGB of every pixel below INPAINT_ALPHA with the colour of the
/// nearest pixel at or above it. PNG encoders store arbitrary (often black)
/// RGB under transparent pixels, and a nearly transparent pixel's colour is
/// quantisation noise (G=255 at alpha 1 beside a green ink): inpainted from
/// those, the transparent field carried seams the partition read as edges.
/// An image with nothing at INPAINT_ALPHA is inpainted from whatever has
/// alpha at all, as before.
pub(crate) fn inpaint_transparent(rgb: &mut Image, h: usize, w: usize, alpha8: &[u8]) {
    let n = h * w;
    let mut sources = Grid { h, w, data: alpha8.iter().map(|a| *a >= INPAINT_ALPHA).collect() };
    if !sources.any() {
        sources = Grid { h, w, data: alpha8.iter().map(|a| *a > 0).collect() };
    }
    let n_src = sources.count();
    if n_src == 0 || n_src == n {
        return;
    }
    let targets = sources.not();
    let (_, src_r, src_c) = edt_sq_indices(&targets);
    let src: Vec<usize> = (0..n)
        .map(|i| src_r.data[i] as usize * w + src_c.data[i] as usize)
        .collect();
    let snapshot = rgb.data.clone();
    for i in 0..n {
        if !targets.data[i] {
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
    let mut alpha8 = vec![0u8; n];
    for i in 0..n {
        rgb.data[i * 3] = rgba[i * 4] as f64;
        rgb.data[i * 3 + 1] = rgba[i * 4 + 1] as f64;
        rgb.data[i * 3 + 2] = rgba[i * 4 + 2] as f64;
        alpha8[i] = rgba[i * 4 + 3];
        alpha.data[i] = (rgba[i * 4 + 3] as f32 / 255.0) as f64;
    }
    inpaint_transparent(&mut rgb, h, w, &alpha8);

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
