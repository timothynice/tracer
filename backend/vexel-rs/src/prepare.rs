//! Stage 1: colour spaces and alpha inpainting.

use crate::core::colour::rgb2lab;
use crate::core::edt::edt_sq_indices;
use crate::core::grid::{Grid, Image};

pub const ALPHA_FEATURE_SCALE: f64 = 100.0;

pub struct Prepared {
    /// (H, W, 3) 0..255, inpainted where alpha == 0
    pub rgb: Image,
    /// (H, W) 0..1
    pub alpha: Grid<f64>,
    /// (H, W, 4) = [L*, a*, b*, 100·alpha]
    pub features: Image,
}

/// Replace RGB under alpha == 0 with the nearest visible pixel's colour. PNG
/// encoders store arbitrary (often black) RGB there, and letting that into the
/// gradient fits or the edge detection would be wrong.
fn inpaint_transparent(rgb: &mut Image, alpha: &Grid<f64>) {
    let n = alpha.len();
    let invisible = Grid { h: alpha.h, w: alpha.w, data: alpha.data.iter().map(|a| *a <= 0.0).collect() };
    let n_inv = invisible.count();
    if n_inv == 0 || n_inv == n {
        return;
    }
    let (_, src_r, src_c) = edt_sq_indices(&invisible);
    let w = alpha.w;
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
    for i in 0..n {
        rgb.data[i * 3] = rgba[i * 4] as f64;
        rgb.data[i * 3 + 1] = rgba[i * 4 + 1] as f64;
        rgb.data[i * 3 + 2] = rgba[i * 4 + 2] as f64;
        alpha.data[i] = (rgba[i * 4 + 3] as f32 / 255.0) as f64;
    }
    inpaint_transparent(&mut rgb, &alpha);

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
