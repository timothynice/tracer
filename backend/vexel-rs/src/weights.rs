//! Interior weights: how far a region pixel is from the region's boundary.
//!
//! Boundary pixels are anti-aliasing mixtures of two fills, so a region's own
//! colour has to be read from its interior. The weight is the distance into the
//! region, clamped and normalised, and it is *squared*: at the Python's linear
//! falloff a one-pixel rim still carries a quarter of its weight, and on a
//! compact shape there are enough rim pixels for that quarter to matter. An
//! opaque disc came out at `fill-opacity="0.998"` because its own
//! anti-aliasing dragged the fitted alpha below the threshold, and a
//! sub-pixel line inside a transparent field was partly absorbed into the
//! field's fitted colour, so the residual pass no longer saw it as an outlier
//! and stopped rescuing it.
//!
//! Sharpening the falloff fixes both. Measured over the corpus it is worth
//! about 0.05 ΔE on the logo class and never loses on the others; the numbers
//! are in the port's design note.

use crate::core::edt::edt;
use crate::core::grid::{Grid, Mask};

/// Distance at which a pixel counts fully as interior.
const REACH: f64 = 3.0;

#[inline]
fn weight_of(dist: f64) -> f64 {
    let t = dist.clamp(0.5, REACH) / REACH;
    t * t
}

/// Weights for `mask`'s true pixels in row-major order.
pub fn interior_weights(mask: &Mask) -> Vec<f64> {
    let Some((r0, r1, c0, c1)) = mask.bbox() else {
        return Vec::new();
    };
    let crop = mask.crop_pad(r0, r1, c0, c1, 1);
    let dist = edt(&crop);
    let mut out = Vec::new();
    for r in r0..r1 {
        for c in c0..c1 {
            if mask.data[r * mask.w + c] {
                out.push(weight_of(dist.data[(r - r0 + 1) * crop.w + (c - c0 + 1)]));
            }
        }
    }
    out
}

/// The same weights, for a region given as a pixel index list — the bounding box
/// comes from the list instead of a frame-sized mask scan.
pub fn interior_weights_at(l: &crate::core::labels::Labels, _label: i32, pixels: &[u32]) -> Vec<f64> {
    if pixels.is_empty() {
        return Vec::new();
    }
    let w = l.w;
    let (mut r0, mut r1, mut c0, mut c1) = (usize::MAX, 0usize, usize::MAX, 0usize);
    for i in pixels {
        let (r, c) = (*i as usize / w, *i as usize % w);
        r0 = r0.min(r);
        r1 = r1.max(r + 1);
        c0 = c0.min(c);
        c1 = c1.max(c + 1);
    }
    let (ch, cw) = (r1 - r0 + 2, c1 - c0 + 2);
    let mut crop = Grid::filled(ch, cw, false);
    for i in pixels {
        let (r, c) = (*i as usize / w, *i as usize % w);
        crop.data[(r - r0 + 1) * cw + (c - c0 + 1)] = true;
    }
    let dist = edt(&crop);
    pixels
        .iter()
        .map(|i| {
            let (r, c) = (*i as usize / w, *i as usize % w);
            weight_of(dist.data[(r - r0 + 1) * cw + (c - c0 + 1)])
        })
        .collect()
}
