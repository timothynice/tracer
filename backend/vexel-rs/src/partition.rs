//! Stage 2: discontinuity map and the initial, edge-bounded partition.
//!
//! A discontinuity is a *ridge* of the colour gradient, not merely a large
//! gradient: a steep but smooth ramp has a large, flat gradient magnitude and
//! must stay one region, while a hard edge has a peak. Non-maximum suppression
//! along the local gradient orientation separates the two, as in Canny.

use crate::core::filters::{self, Mode};
use crate::core::grid::{Grid, Image, Mask};
use crate::core::labels::{self, Labels};
use crate::core::morphology::{dilate_cross, dilate_cross_n};
use rayon::prelude::*;
use crate::core::watershed::watershed;
use crate::timing::Timer;

/// Per-pixel colour change (ΔE-like units per pixel) across all feature channels.
pub fn discontinuity(features: &Image, sigma: f64) -> Grid<f64> {
    let (h, w) = (features.h, features.w);
    let mut g2 = Grid::<f64>::new(h, w);
    for c in 0..features.c {
        let g = filters::scharr(&features.channel(c));
        for i in 0..h * w {
            g2.data[i] = (g2.data[i] as f32 + (g.data[i] as f32) * (g.data[i] as f32)) as f64;
        }
    }
    let grad = Grid { h, w, data: g2.data.iter().map(|v| (*v as f32).sqrt() as f64).collect() };
    if sigma > 0.0 {
        filters::gaussian_filter_f32(&grad, sigma, Mode::Reflect, 0.0)
    } else {
        grad
    }
}

/// (ridge, valley) masks from non-maximum / non-minimum tests along the
/// structure-tensor gradient orientation.
pub fn ridges_and_valleys(features: &Image, grad: &Grid<f64>, g_low: f64) -> (Mask, Mask) {
    let mut t = Timer::new();
    let (h, w) = (grad.h, grad.w);
    let n = h * w;
    let mut jxx = Grid::<f64>::new(h, w);
    let mut jyy = Grid::<f64>::new(h, w);
    let mut jxy = Grid::<f64>::new(h, w);
    for c in 0..features.c {
        let ch = features.channel(c);
        let gy = filters::scharr_h(&ch);
        let gx = filters::scharr_v(&ch);
        for i in 0..n {
            jxx.data[i] = (jxx.data[i] as f32 + (gx.data[i] * gx.data[i]) as f32) as f64;
            jyy.data[i] = (jyy.data[i] as f32 + (gy.data[i] * gy.data[i]) as f32) as f64;
            jxy.data[i] = (jxy.data[i] as f32 + (gx.data[i] * gy.data[i]) as f32) as f64;
        }
    }
    t.lap("      ridges/scharr");
    let jxx = filters::gaussian_filter_f32(&jxx, 1.0, Mode::Reflect, 0.0);
    let jyy = filters::gaussian_filter_f32(&jyy, 1.0, Mode::Reflect, 0.0);
    let jxy = filters::gaussian_filter_f32(&jxy, 1.0, Mode::Reflect, 0.0);

    // One independent decision per pixel over four bilinear samples and a
    // trig call; at 768² that is the whole cost of the stage.
    let mut flags: Vec<u8> = vec![0; n];
    flags
        .par_chunks_mut(w)
        .enumerate()
        .for_each(|(r, row)| {
            for (c, slot) in row.iter_mut().enumerate() {
                let i = r * w + c;
                // The orientation and the sample offsets are computed in f32, as
                // the Python's float32 arrays are. At a plateau the near sample can
                // land within 1e-4 of the centre pixel, and `grad >= g_plus` then
                // turns on the last bit: carrying f64 here flips ridge pixels.
                let theta = 0.5f32
                    * (2.0f32 * jxy.data[i] as f32).atan2(jxx.data[i] as f32 - jyy.data[i] as f32);
                let (dx, dy) = (theta.cos(), theta.sin());
                let g = grad.data[i];
                let (rf, cf) = (r as f32, c as f32);
                // map_coordinates writes float32 for a float32 input, and the
                // comparisons below then happen in float32 too
                let at = |sy: f32, sx: f32| -> f32 {
                    filters::bilinear_nearest(grad, (rf + sy) as f64, (cf + sx) as f64) as f32
                };
                let gp = at(dy, dx);
                let gm = at(-dy, -dx);
                // Prominence is judged 1.5 px out: a real edge has fallen to ≈ 40 %
                // there, while the periodic bumps 8-bit quantisation puts on a steep
                // ramp are only a few percent high.
                let gpf = at(1.5f32 * dy, 1.5f32 * dx);
                let gmf = at(-1.5f32 * dy, -1.5f32 * dx);
                let g32 = g as f32;
                let prominent = g32 > 1.10f32 * gp.max(gm) || g32 > 1.10f32 * gpf.max(gmf);
                let is_ridge = g32 > g_low as f32 && g32 >= gp && g32 >= gm && prominent;
                let is_valley = g32 <= gp && g32 <= gm && !is_ridge;
                *slot = is_ridge as u8 | ((is_valley as u8) << 1);
            }
        });
    t.lap("      ridges/nms");
    let ridge = Grid { h, w, data: flags.iter().map(|f| f & 1 != 0).collect() };
    let valley = Grid { h, w, data: flags.iter().map(|f| f & 2 != 0).collect() };
    (ridge, valley)
}

/// Seeds: pixels clear of the ridge band, or valley pixels, that also have a
/// low gradient. NMS loses ridge pixels at T-junctions, and those gap pixels
/// still carry a high gradient, so the gradient test keeps neighbouring regions
/// from leaking into one seed.
pub fn seed_mask(features: &Image, grad: &Grid<f64>, g_low: f64, g_seed: f64) -> Mask {
    let (ridge, valley) = ridges_and_valleys(features, grad, g_low);
    let band = dilate_cross(&ridge);
    let near_band = dilate_cross_n(&band, 2);
    let mut out = Grid::filled(grad.h, grad.w, false);
    for i in 0..grad.len() {
        let leaky = near_band.data[i] && grad.data[i] >= g_seed;
        out.data[i] = (!band.data[i] && !leaky) || valley.data[i];
    }
    out
}

/// Flood regions below `min_region` pixels from their neighbours along low gradient.
fn absorb_small(labels: &Labels, grad: &Grid<f64>, min_region: usize, rounds: usize) -> Labels {
    let mut cur = labels.clone();
    for _ in 0..rounds {
        let sizes = labels::bincount(&cur);
        let small: Vec<bool> = sizes.iter().enumerate().map(|(i, s)| i != 0 && *s < min_region).collect();
        if !small.iter().any(|b| *b) {
            break;
        }
        let mut markers = cur.clone();
        let mut max = 0i32;
        for v in markers.data.iter_mut() {
            if *v >= 0 && small[*v as usize] {
                *v = 0;
            }
            if *v > max {
                max = *v;
            }
        }
        if max == 0 {
            return Grid::filled(cur.h, cur.w, 1);
        }
        cur = watershed(grad, &markers);
    }
    cur
}

/// Watershed on the discontinuity map, seeded by the connected smooth areas.
pub fn initial_labels(grad: &Grid<f64>, features: &Image, min_region: usize, g_low: f64) -> Labels {
    let mut t = Timer::new();
    let smooth = seed_mask(features, grad, g_low, 8.0);
    t.lap("    labels/seed_mask");
    let mut markers = labels::label_mask(&smooth, 1);
    t.lap("    labels/cc");
    let max = markers.data.iter().copied().max().unwrap_or(0);
    if max > 0 {
        let sizes = labels::bincount(&markers);
        let floor = min_region.max(2);
        let keep: Vec<bool> = sizes.iter().enumerate().map(|(i, s)| i != 0 && *s >= floor).collect();
        for v in markers.data.iter_mut() {
            if *v > 0 && !keep[*v as usize] {
                *v = 0;
            }
        }
    }
    if markers.data.iter().copied().max().unwrap_or(0) == 0 {
        // No smooth area at all: seed from the local minima of the gradient so
        // the watershed still has basins.
        let mn = filters::minimum_filter3(grad);
        let minima = Grid {
            h: grad.h,
            w: grad.w,
            data: (0..grad.len()).map(|i| mn.data[i] == grad.data[i]).collect(),
        };
        markers = labels::label_mask(&minima, 1);
        if markers.data.iter().copied().max().unwrap_or(0) == 0 {
            return Grid::filled(grad.h, grad.w, 1);
        }
    }
    let l = watershed(grad, &markers);
    t.lap("    labels/watershed");
    let l = absorb_small(&l, grad, min_region, 3);
    t.lap("    labels/absorb_small");
    labels::relabel_sequential(&l).0
}
