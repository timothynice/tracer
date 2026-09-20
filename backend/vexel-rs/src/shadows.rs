//! Drop shadows, glows and inner shadows as SVG filters.
//!
//! A soft shadow is not a colour field to be approximated — it is a blurred,
//! offset, scaled copy of a shape's own alpha. Approximating it with regions
//! slices the falloff into bands whose boundaries are iso-contours of the blur,
//! and those trace as lumpy outlines against a perfectly smooth original.
//! Recognising it instead recovers the handful of numbers that made it:
//!
//! ```text
//! observed = B + o·g(x)·(C − B),      g = G_σ(shift_{dx,dy}(A))
//! ```
//!
//! with `A` the caster's silhouette, `B` the backdrop, `C` the shadow colour
//! and `o` its opacity. An inner shadow is the same with `g = A·(1 − G_σ(shift(A)))`.
//!
//! Only the product `o·(C − B)` is observable: every split renders identically.
//! The split chosen sends the colour to where the ray from `B` leaves the sRGB
//! cube, which recovers the authored values exactly for the usual black shadow.

use crate::core::edt::edt_to_true;
use crate::core::filters::{gaussian_filter, shift_bilinear, sobel, Mode};
use crate::core::grid::{Grid, Image, Mask};
use crate::core::labels::{self, LabelIndex, Labels};
use crate::core::morphology::{dilate_cross_n, erode_cross_n};
use crate::core::optimise::{nelder_mead, NmOptions};
use crate::fills::{fmt, hex, Fill};
use crate::prepare::Prepared;
use crate::timing::Timer;
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};

/// Fitting runs on a grid no larger than this on the long edge: σ scales with
/// the image, so the objective's shape does not need full resolution.
const FIT_EDGE: usize = 224;
const MIN_SIGMA: f64 = 0.6;
/// a blur wider than this much of the image is not a shadow
const MAX_SIGMA_FRAC: f64 = 0.35;
/// how far off the shadow ray a region's colour may sit (0–255)
const RAY_TOL: f64 = 14.0;
/// a darkening smaller than this is not worth a filter
const MIN_PEAK: f64 = 5.0;
/// The filter has to beat the bands *decisively*, not by a hair: the gate scores
/// a model computed here while the judge is a renderer, and the two agree only
/// to within a fraction of a colour level.
const WIN_MARGIN: f64 = 0.75;

#[derive(Clone)]
pub struct Shadow {
    pub caster: i32,
    pub dx: f64,
    pub dy: f64,
    pub sigma: f64,
    pub colour: [f64; 3],
    pub opacity: f64,
    pub inset: bool,
    /// filter region in user units (x, y, w, h)
    pub region: (f64, f64, f64, f64),
}

#[derive(Default)]
pub struct ShadowPlan {
    /// caster label -> its shadow
    pub shadows: HashMap<i32, Shadow>,
    /// regions the shadows explain
    pub absorbed: HashSet<i32>,
    /// backdrops to refit on `corrected`
    pub refit: HashSet<i32>,
    /// rgba255 with the accepted shadows removed
    pub corrected: Option<Vec<[f64; 4]>>,
}

// --- helpers ---------------------------------------------------------------

/// Largest `t` with `origin + t·direction` still inside [0, 255]³.
fn cube_exit(origin: &[f64; 3], direction: &[f64; 3]) -> f64 {
    let mut best = f64::INFINITY;
    let mut any = false;
    for i in 0..3 {
        let d = direction[i];
        if d.abs() <= 1e-9 {
            continue;
        }
        any = true;
        let t = ((if d > 0.0 { 255.0 } else { 0.0 }) - origin[i]) / d;
        if t < best {
            best = t;
        }
    }
    if any {
        best
    } else {
        0.0
    }
}

fn blur_shift(a: &Grid<f64>, dx: f64, dy: f64, sigma: f64) -> Grid<f64> {
    let shifted = if dx != 0.0 || dy != 0.0 { shift_bilinear(a, dy, dx, 0.0) } else { a.clone() };
    gaussian_filter(&shifted, sigma.max(1e-3), Mode::Constant, 0.0)
}

/// Least-squares `k` for `target ≈ k·model`, and the RMS that leaves.
fn solve_scale(model: &[f64], target: &[f64]) -> (f64, f64) {
    let denom: f64 = model.iter().map(|v| v * v).sum();
    if denom < 1e-9 {
        let rms = (target.iter().map(|v| v * v).sum::<f64>() / target.len() as f64).sqrt();
        return (0.0, rms);
    }
    let k = (model.iter().zip(target).map(|(m, t)| m * t).sum::<f64>() / denom).max(0.0);
    let rms = (target
        .iter()
        .zip(model)
        .map(|(t, m)| (t - k * m).powi(2))
        .sum::<f64>()
        / target.len() as f64)
        .sqrt();
    (k, rms)
}

/// How many grid pixels of blur the Nelder-Mead search is run at. A Gaussian of
/// σ is smooth on the scale of σ, so a grid that resolves it to about eight
/// pixels carries the whole shape of the objective; finer only costs kernel
/// taps. See `fit_blur`.
const NM_TARGET_SIGMA: f64 = 8.0;

/// Take every `d`-th pixel, the same nearest-sample reduction the caller used
/// to get from the image to the fit grid.
fn stride_grid(g: &Grid<f64>, d: usize) -> Grid<f64> {
    let (h, w) = (g.h.div_ceil(d), g.w.div_ceil(d));
    let mut out = Grid::new(h, w);
    for r in 0..h {
        for c in 0..w {
            out.data[r * w + c] = g.data[(r * d) * g.w + c * d];
        }
    }
    out
}

fn stride_mask(m: &Mask, d: usize) -> Mask {
    let (h, w) = (m.h.div_ceil(d), m.w.div_ceil(d));
    let mut out = Grid::filled(h, w, false);
    for r in 0..h {
        for c in 0..w {
            out.data[r * w + c] = m.data[(r * d) * m.w + c * d];
        }
    }
    out
}

/// One evaluation of `‖target − k·G_σ(shift(src))‖` over `domain`, as the
/// search sees it.
fn blur_cost(src: &Grid<f64>, target: &Grid<f64>, idx: &[usize], t: &[f64], inset: bool, v: &[f64], limit: f64) -> f64 {
    let sigma = v[2].exp();
    if !(MIN_SIGMA..=limit).contains(&sigma) {
        return 1e9;
    }
    let _ = target;
    let g = blur_shift(src, v[0], v[1], sigma);
    let model: Vec<f64> = if inset {
        idx.iter().map(|i| src.data[*i] * (1.0 - g.data[*i])).collect()
    } else {
        idx.iter().map(|i| g.data[*i]).collect()
    };
    solve_scale(&model, t).1
}

/// Fit (dx, dy, σ, k) of `k·G_σ(shift(src))` to `target` over `domain`, in grid units.
///
/// The search is the expensive part of the whole engine: every evaluation is a
/// full separable Gaussian, and Nelder-Mead runs hundreds of them per caster.
/// It is run on a grid coarse enough that the blur it is fitting spans about
/// `NM_TARGET_SIGMA` pixels — a σ of 40 on a 256-pixel grid is resolved twenty
/// times finer than anything it can distinguish, and the kernel is quadratic in
/// that waste. The accepted parameters are then scored once at the caller's own
/// resolution, so the `k` and residual that the gate sees are never the coarse
/// ones.
fn fit_blur(
    src: &Grid<f64>,
    target: &Grid<f64>,
    domain: &Mask,
    seed: (f64, f64),
    inset: bool,
) -> (f64, f64, f64, f64, f64) {
    let idx: Vec<usize> = (0..domain.len()).filter(|i| domain.data[*i]).collect();
    let t: Vec<f64> = idx.iter().map(|i| target.data[*i]).collect();
    let limit = MAX_SIGMA_FRAC * src.h.max(src.w) as f64;

    // A coarse octave sweep first: the objective is smooth in (dx, dy) but has a
    // long flat valley in σ, and Nelder-Mead from a bad σ just settles in it.
    let sigmas: Vec<f64> = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]
        .into_iter()
        .take_while(|s| *s <= limit)
        .collect();
    let seeds: Vec<(f64, Vec<f64>)> = sigmas
        .par_iter()
        .map(|s0| {
            let v0 = vec![seed.0, seed.1, s0.ln()];
            (blur_cost(src, target, &idx, &t, inset, &v0, limit), v0)
        })
        .collect();
    let Some((_, x0)) = seeds.into_iter().min_by(|a, b| a.0.total_cmp(&b.0)) else {
        return (0.0, 0.0, MIN_SIGMA, 0.0, f64::INFINITY);
    };

    let d = ((x0[2].exp() / NM_TARGET_SIGMA).floor() as usize).max(1);
    let (c_src, c_domain, c_target) = if d > 1 {
        (stride_grid(src, d), stride_mask(domain, d), stride_grid(target, d))
    } else {
        (src.clone(), domain.clone(), target.clone())
    };
    let c_idx: Vec<usize> = (0..c_domain.len()).filter(|i| c_domain.data[*i]).collect();
    let c_t: Vec<f64> = c_idx.iter().map(|i| c_target.data[*i]).collect();
    let c_limit = MAX_SIGMA_FRAC * c_src.h.max(c_src.w) as f64;
    let df = d as f64;

    // An explicit simplex: scipy's default step is a fraction of each parameter,
    // which collapses to 0.00025 for an offset seeded at zero — the search then
    // never moves dx or dy at all.
    let c_x0 = vec![x0[0] / df, x0[1] / df, x0[2] - df.ln()];
    let span = (0.04 * c_src.h.max(c_src.w) as f64).max(2.0);
    let simplex = vec![
        c_x0.clone(),
        vec![c_x0[0] + span, c_x0[1], c_x0[2]],
        vec![c_x0[0], c_x0[1] + span, c_x0[2]],
        vec![c_x0[0], c_x0[1], c_x0[2] + 0.4],
    ];
    let opts = NmOptions { xatol: 0.05, fatol: 1e-4, maxiter: 400, maxfev: usize::MAX };
    let cost = |v: &[f64]| blur_cost(&c_src, &c_target, &c_idx, &c_t, inset, v, c_limit);
    let best = nelder_mead(cost, simplex, &opts);

    let (dx, dy) = (best[0] * df, best[1] * df);
    let sigma = (best[2].exp() * df).clamp(MIN_SIGMA, limit);
    // score the answer at the caller's resolution, not the search's
    let g = blur_shift(src, dx, dy, sigma);
    let model: Vec<f64> = if inset {
        idx.iter().map(|i| src.data[*i] * (1.0 - g.data[*i])).collect()
    } else {
        idx.iter().map(|i| g.data[*i]).collect()
    };
    let (k, rms) = solve_scale(&model, &t);
    (dx, dy, sigma, k, rms)
}

/// The unshadowed colour: the mode of a coarsely quantised sample.
fn backdrop_colour(values: &[[f64; 3]]) -> [f64; 3] {
    let mut counts: HashMap<i64, usize> = HashMap::new();
    let keys: Vec<i64> = values
        .iter()
        .map(|v| {
            let q: Vec<i64> = v.iter().map(|c| (c / 6.0).floor() as i64).collect();
            (q[0] << 20) | (q[1] << 10) | q[2]
        })
        .collect();
    for k in &keys {
        *counts.entry(*k).or_insert(0) += 1;
    }
    // np.unique sorts, and argmax takes the first maximum among the sorted keys
    let mut uniq: Vec<(i64, usize)> = counts.into_iter().collect();
    uniq.sort_unstable();
    let pick = uniq.iter().max_by_key(|(_, c)| *c).map(|(k, _)| *k).unwrap_or(0);
    let pick = uniq
        .iter()
        .find(|(_, c)| *c == uniq.iter().map(|(_, c)| *c).max().unwrap())
        .map(|(k, _)| *k)
        .unwrap_or(pick);
    let mut sum = [0.0f64; 3];
    let mut n = 0usize;
    for (v, k) in values.iter().zip(keys.iter()) {
        if *k == pick {
            for c in 0..3 {
                sum[c] += v[c];
            }
            n += 1;
        }
    }
    if n == 0 {
        return [0.0; 3];
    }
    [sum[0] / n as f64, sum[1] / n as f64, sum[2] / n as f64]
}

/// The user-space box the blur needs.
///
/// The usual `x="-50%" width="200%"` is a percentage of the *bounding box*, so
/// a small shape with a wide blur has its shadow clipped — invisible in a model
/// computed in numpy, very visible in the render.
fn filter_region(sil: &Mask, dx: f64, dy: f64, sigma: f64) -> (f64, f64, f64, f64) {
    let Some((r0, r1, c0, c1)) = sil.bbox() else {
        return (0.0, 0.0, 1.0, 1.0);
    };
    let margin = 3.0 * sigma + dx.abs() + dy.abs() + 2.0;
    let x0 = c0 as f64 - margin;
    let x1 = (c1 - 1) as f64 + 1.0 + margin;
    let y0 = r0 as f64 - margin;
    let y1 = (r1 - 1) as f64 + 1.0 + margin;
    (x0, y0, x1 - x0, y1 - y0)
}

/// Does the caster have a hard edge?
///
/// A drop shadow hides behind an opaque shape, so the shape's own boundary is a
/// step. If the boundary is itself a ramp then the artwork is blurred, not
/// shadowed — a different effect, and out of scope: replacing it with a sharp
/// shape plus a filter renders a hard edge where the original is soft.
fn is_sharp(observed: &Image, sil: &Mask, grad: &Grid<f64>) -> bool {
    // Six morphology passes and two means, all confined to the caster's own
    // bounding box grown by the erosion depth: run frame-wide they cost O(K·N)
    // over the whole image for every candidate.
    let Some((br0, br1, bc0, bc1)) = sil.bbox() else { return false };
    let (r0, r1) = (br0.saturating_sub(4), (br1 + 4).min(sil.h));
    let (c0, c1) = (bc0.saturating_sub(4), (bc1 + 4).min(sil.w));
    let (h, w) = (r1 - r0, c1 - c0);
    let mut m = Grid::filled(h, w, false);
    for r in r0..r1 {
        m.data[(r - r0) * w..(r - r0 + 1) * w].copy_from_slice(&sil.data[r * sil.w + c0..r * sil.w + c1]);
    }

    let edge = dilate_cross_n(&m, 1).and_not(&erode_cross_n(&m, 1, false));
    if edge.count() < 8 {
        return false;
    }
    let inner = erode_cross_n(&m, 3, false);
    let outer = dilate_cross_n(&m, 3).and_not(&m);
    if inner.count() < 8 || outer.count() < 8 {
        return false;
    }
    let global = |i: usize| -> usize { (i / w + r0) * observed.w + (i % w + c0) };
    let mean = |mask: &Mask| -> [f64; 3] {
        let mut s = [0.0f64; 3];
        let mut n = 0usize;
        for i in 0..mask.len() {
            if mask.data[i] {
                let p = observed.px(global(i));
                for c in 0..3 {
                    s[c] += p[c];
                }
                n += 1;
            }
        }
        [s[0] / n as f64, s[1] / n as f64, s[2] / n as f64]
    };
    let (a, b) = (mean(&inner), mean(&outer));
    let step = ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt();
    if step < 8.0 {
        return false;
    }
    let mut vals: Vec<f64> = (0..edge.len()).filter(|i| edge.data[*i]).map(|i| grad.data[global(i)]).collect();
    vals.sort_by(|x, y| x.partial_cmp(y).unwrap());
    let pos = 0.90 * (vals.len() - 1) as f64;
    let lo = pos.floor() as usize;
    let hi = pos.ceil() as usize;
    let p90 = vals[lo] + (pos - lo as f64) * (vals[hi] - vals[lo]);
    p90 > 0.30 * step
}

/// The colour-gradient magnitude `_is_sharp` compares against, computed once for
/// the whole image instead of per caster: the Python recomputes six Sobel passes
/// for every candidate, and it is the same field every time.
fn sharpness_grad(observed: &Image) -> Grid<f64> {
    let (h, w) = (observed.h, observed.w);
    let mut acc = Grid::<f64>::new(h, w);
    for c in 0..3 {
        let ch = observed.channel(c);
        let g0 = sobel(&ch, 0);
        let g1 = sobel(&ch, 1);
        for i in 0..h * w {
            acc.data[i] += g0.data[i] * g0.data[i] + g1.data[i] * g1.data[i];
        }
    }
    Grid { h, w, data: acc.data.iter().map(|v| v.sqrt() / 4.0).collect() }
}

/// RMS colour error of the filter model and of the bands it would replace.
///
/// Full resolution and all three channels on purpose: measured on a reduced
/// grid along the shadow's own colour axis, a model can look better than the
/// bands while rendering visibly worse.
fn gate(
    observed: &Image,
    domain: &Mask,
    model_rgb: &[[f64; 3]],
    l: &Labels,
    fills: &HashMap<i32, Fill>,
    xs: &Grid<f64>,
    ys: &Grid<f64>,
) -> (f64, f64) {
    let idx: Vec<usize> = (0..domain.len()).filter(|i| domain.data[*i]).collect();
    let n = idx.len();
    // a hash probe per pixel over most of the frame is most of this function
    let max_lab = l.data.iter().copied().max().unwrap_or(0).max(0) as usize;
    let mut table: Vec<Option<&Fill>> = vec![None; max_lab + 1];
    for (lab, f) in fills {
        if *lab >= 0 && (*lab as usize) < table.len() {
            table[*lab as usize] = Some(f);
        }
    }
    let (filter_sq, band_sq) = idx
        .par_iter()
        .enumerate()
        .map(|(k, i)| {
            let p = observed.px(*i);
            let mut fs = 0.0;
            for c in 0..3 {
                fs += (p[c] - model_rgb[k][c]).powi(2);
            }
            let lab = l.data[*i];
            let band = match table.get(lab.max(0) as usize).copied().flatten() {
                Some(f) => {
                    let v = f.evaluate_one(xs.data[*i], ys.data[*i]);
                    [v[0], v[1], v[2]]
                }
                None => [p[0], p[1], p[2]],
            };
            let mut bs = 0.0;
            for c in 0..3 {
                bs += (p[c] - band[c]).powi(2);
            }
            (fs, bs)
        })
        .reduce(|| (0.0, 0.0), |a, b| (a.0 + b.0, a.1 + b.1));
    let m = (n * 3) as f64;
    ((filter_sq / m).sqrt(), (band_sq / m).sqrt())
}

// --- detection -------------------------------------------------------------

/// Find shadows, the regions they explain, and the backdrops to refit.
///
/// `silhouette(label)` gives the caster's painted footprint with enclosed
/// regions included — the white bar on a card is part of the card's
/// silhouette, so it is part of what the card casts.
#[allow(clippy::too_many_arguments)]
pub fn detect_shadows(
    l: &Labels,
    fills: &HashMap<i32, Fill>,
    visible: &HashMap<i32, bool>,
    silhouette: &(dyn Fn(i32) -> Mask + Sync),
    prep: &Prepared,
    xs: &Grid<f64>,
    ys: &Grid<f64>,
    min_region: usize,
    inset: bool,
) -> ShadowPlan {
    let mut t = Timer::new();
    let mut plan = ShadowPlan::default();
    let (h, w) = (l.h, l.w);
    let total = l.len() as f64;
    let ids = labels::unique_ids(l);
    if ids.len() < 3 {
        return plan;
    }
    let rgb = &prep.rgb;
    let index = LabelIndex::build(l);
    let areas: HashMap<i32, usize> = ids.iter().map(|i| (*i, index.area(*i))).collect();
    let vis: Vec<i32> = ids.iter().copied().filter(|i| visible.get(i).copied().unwrap_or(false)).collect();
    if vis.is_empty() {
        return plan;
    }
    let means: HashMap<i32, [f64; 3]> = vis
        .iter()
        .map(|lab| {
            let px = index.pixels(*lab);
            let mut s = [0.0f64; 3];
            for i in px {
                let p = rgb.px(*i as usize);
                for c in 0..3 {
                    s[c] += p[c];
                }
            }
            let n = px.len().max(1) as f64;
            (*lab, [s[0] / n, s[1] / n, s[2] / n])
        })
        .collect();

    let mut on_border: HashSet<i32> = HashSet::new();
    for c in 0..w {
        on_border.insert(l.data[c]);
        on_border.insert(l.data[(h - 1) * w + c]);
    }
    for r in 0..h {
        on_border.insert(l.data[r * w]);
        on_border.insert(l.data[r * w + w - 1]);
    }
    let Some(backdrop) = vis
        .iter()
        .filter(|lab| on_border.contains(lab))
        .max_by_key(|lab| areas[lab])
        .copied()
    else {
        return plan;
    };
    if (areas[&backdrop] as f64) < 0.05 * total {
        return plan;
    }

    let backdrop_pixels: Vec<[f64; 3]> = index
        .pixels(backdrop)
        .iter()
        .map(|i| {
            let p = rgb.px(*i as usize);
            [p[0], p[1], p[2]]
        })
        .collect();
    let b0 = backdrop_colour(&backdrop_pixels);

    // Which regions lie on a single ray away from the backdrop's own colour?
    // The best ray is the one the most regions share: a shadow arrives as a
    // ramp of several bands, a piece of artwork as one region of its own hue.
    let others: Vec<i32> = vis
        .iter()
        .copied()
        .filter(|lab| *lab != backdrop && (areas[lab] as f64) < 0.4 * total)
        .collect();
    let mut rays: Vec<(usize, usize, [f64; 3], HashSet<i32>)> = Vec::new();
    for cand in &others {
        let m = means[cand];
        let d = [m[0] - b0[0], m[1] - b0[1], m[2] - b0[2]];
        let n = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
        if n < MIN_PEAK {
            continue;
        }
        let u = [d[0] / n, d[1] / n, d[2] / n];
        let members: HashSet<i32> = others
            .iter()
            .copied()
            .filter(|lab| {
                let mm = means[lab];
                let dd = [mm[0] - b0[0], mm[1] - b0[1], mm[2] - b0[2]];
                let proj = dd[0] * u[0] + dd[1] * u[1] + dd[2] * u[2];
                if proj <= 0.0 {
                    return false;
                }
                let perp: f64 = (0..3).map(|c| (dd[c] - proj * u[c]).powi(2)).sum::<f64>().sqrt();
                perp <= RAY_TOL
            })
            .collect();
        let area: usize = members.iter().map(|m| areas[m]).sum();
        rays.push((members.len(), area, u, members));
    }
    if rays.is_empty() {
        return plan;
    }
    rays.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(&b.1)));

    let step = ((h.max(w) as f64 / FIT_EDGE as f64).round() as usize).max(1);
    let small = |g: &Grid<f64>| -> Grid<f64> {
        let (sh, sw) = (h.div_ceil(step), w.div_ceil(step));
        let mut out = Grid::<f64>::new(sh, sw);
        for r in 0..sh {
            for c in 0..sw {
                out.data[r * sw + c] = g.data[(r * step) * w + c * step];
            }
        }
        out
    };
    let small_mask = |m: &Mask| -> Mask {
        let (sh, sw) = (h.div_ceil(step), w.div_ceil(step));
        let mut out = Grid::filled(sh, sw, false);
        for r in 0..sh {
            for c in 0..sw {
                out.data[r * sw + c] = m.data[(r * step) * w + c * step];
            }
        }
        out
    };
    let small_rgb: Vec<Grid<f64>> = (0..3).map(|c| small(&rgb.channel(c))).collect();
    let sharp_grad = sharpness_grad(rgb);

    for (count, _area, u, members) in rays.iter().take(3) {
        if *count < 1 {
            continue;
        }
        if try_ray(
            &mut plan, l, &index, fills, xs, ys, prep, silhouette, &vis, &areas, backdrop, &b0, u,
            members, min_region, total, step, &small_rgb, &small_mask, &sharp_grad,
        ) {
            break;
        }
    }

    t.lap("    shadows/try_ray");
    if inset && plan.shadows.is_empty() {
        detect_inset(
            &mut plan, l, &index, fills, silhouette, prep, xs, ys, &areas, &means, &vis, total,
            step, &small_rgb, &small_mask, &sharp_grad,
        );
    }
    t.lap("    shadows/detect_inset");
    plan
}

#[allow(clippy::too_many_arguments)]
fn try_ray(
    plan: &mut ShadowPlan,
    l: &Labels,
    index: &LabelIndex,
    fills: &HashMap<i32, Fill>,
    xs: &Grid<f64>,
    ys: &Grid<f64>,
    prep: &Prepared,
    silhouette: &(dyn Fn(i32) -> Mask + Sync),
    vis: &[i32],
    areas: &HashMap<i32, usize>,
    backdrop: i32,
    b0: &[f64; 3],
    u: &[f64; 3],
    band_group: &HashSet<i32>,
    min_region: usize,
    total: f64,
    step: usize,
    small_rgb: &[Grid<f64>],
    small_mask: &(dyn Fn(&Mask) -> Mask + Sync),
    sharp_grad: &Grid<f64>,
) -> bool {
    let rgb = &prep.rgb;
    let (h, w) = (l.h, l.w);
    // Casters are the visible regions this ray does *not* explain: real ink.
    let floor = (4 * min_region).max((0.004 * total) as usize);
    let mut ink: Vec<i32> = vis
        .iter()
        .copied()
        .filter(|lab| *lab != backdrop && !band_group.contains(lab) && areas[lab] >= floor)
        .collect();
    let sils: HashMap<i32, Mask> = ink.iter().map(|lab| (*lab, silhouette(*lab))).collect();
    // An enclosed region rides on its container's silhouette, not its own.
    ink = ink
        .iter()
        .copied()
        .filter(|lab| {
            !ink.iter().any(|o| {
                if o == lab {
                    return false;
                }
                let (a, b) = (&sils[o], &sils[lab]);
                let n = b.count();
                if n == 0 {
                    return false;
                }
                let covered = (0..b.len()).filter(|i| b.data[*i] && a.data[*i]).count();
                covered as f64 / n as f64 > 0.9
            })
        })
        .collect();
    if ink.is_empty() {
        return false;
    }

    let mut painted = Grid::filled(h, w, false);
    for lab in &ink {
        painted.or_with(&sils[lab]);
    }
    // A page of cards has one shadow per card. Each caster owns the canvas
    // nearer to it than to any other, so the fits do not fight each other.
    let owner: Vec<usize> = if ink.len() > 1 {
        let dists: Vec<Grid<f64>> = ink.par_iter().map(|lab| edt_to_true(&sils[lab])).collect();
        (0..h * w)
            .map(|i| {
                let mut best = 0usize;
                for k in 1..dists.len() {
                    if dists[k].data[i] < dists[best].data[i] {
                        best = k;
                    }
                }
                best
            })
            .collect()
    } else {
        vec![0usize; h * w]
    };

    // Fit each caster on its own cell, then judge them together: shadows spill
    // across cell borders and compose source-over, so scoring one at a time
    // passes fits that render wrong once the others are drawn too.
    type Fitted = (i32, f64, f64, f64, f64, [f64; 3], HashSet<i32>);
    let fitted: Vec<Fitted> = ink
        .par_iter()
        .enumerate()
        .filter_map(|(i, caster)| {
            let sil = &sils[caster];
            if !is_sharp(rgb, sil, sharp_grad) {
                return None;
            }
            // A drop shadow lies *behind* its caster, so only regions outside
            // the silhouette can belong to it. Without this, a blurred shape's
            // own interior bands get absorbed and repainted flat.
            let cell = Grid {
                h,
                w,
                data: (0..h * w).map(|k| !painted.data[k] && owner[k] == i).collect(),
            };
            if cell.count() < 64 {
                return None;
            }
            let group: HashSet<i32> = band_group
                .iter()
                .copied()
                .filter(|lab| {
                    let px = index.pixels(*lab);
                    if px.is_empty() {
                        return false;
                    }
                    let inside = px.iter().filter(|k| cell.data[**k as usize]).count();
                    inside as f64 / px.len() as f64 > 0.9
                })
                .collect();
            if group.is_empty() {
                return None; // a filter that replaces nothing is not worth emitting
            }

            let cell_small = small_mask(&cell);
            let mut target = Grid::<f64>::new(cell_small.h, cell_small.w);
            for k in 0..cell_small.len() {
                if cell_small.data[k] {
                    target.data[k] = (0..3).map(|c| (small_rgb[c].data[k] - b0[c]) * u[c]).sum();
                }
            }
            let tmax = target.data.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            if tmax < MIN_PEAK {
                return None;
            }
            let src_full = Grid { h, w, data: sil.data.iter().map(|b| *b as u8 as f64).collect() };
            let src = small_mask(sil);
            let src = Grid { h: src.h, w: src.w, data: src.data.iter().map(|b| *b as u8 as f64).collect() };
            if src.data.iter().sum::<f64>() < 16.0 {
                return None;
            }
            let _ = &src_full;

            let (mut gx, mut gy, mut gn) = (0.0f64, 0.0f64, 0usize);
            let (mut sx, mut sy, mut sn) = (0.0f64, 0.0f64, 0usize);
            for k in 0..cell_small.len() {
                let (r, c) = (k / cell_small.w, k % cell_small.w);
                if cell_small.data[k] && target.data[k] > 0.35 * tmax {
                    gx += c as f64;
                    gy += r as f64;
                    gn += 1;
                }
                if src.data[k] > 0.5 {
                    sx += c as f64;
                    sy += r as f64;
                    sn += 1;
                }
            }
            let seed = if gn > 0 && sn > 0 {
                (gx / gn as f64 - sx / sn as f64, gy / gn as f64 - sy / sn as f64)
            } else {
                (0.0, 0.0)
            };

            let (dx, dy, sigma, k, _) = fit_blur(&src, &target, &cell_small, seed, false);
            if k <= 0.0 {
                return None;
            }
            let length = cube_exit(b0, u);
            if length <= 1e-6 {
                return None;
            }
            let mut opacity = k / length;
            let mut colour = [b0[0] + length * u[0], b0[1] + length * u[1], b0[2] + length * u[2]];
            if opacity > 1.0 {
                opacity = 1.0;
                colour = [b0[0] + k * u[0], b0[1] + k * u[1], b0[2] + k * u[2]];
            }
            if !(0.02..=1.0).contains(&opacity) {
                return None;
            }
            Some((
                *caster,
                dx * step as f64,
                dy * step as f64,
                sigma * step as f64,
                opacity,
                colour,
                group,
            ))
        })
        .collect();

    if fitted.is_empty() {
        return false;
    }

    // Compose them the way the renderer will, then gate once.
    let domain = painted.not();
    let idx: Vec<usize> = (0..h * w).filter(|i| domain.data[*i]).collect();
    let blurs: HashMap<i32, Grid<f64>> = fitted
        .par_iter()
        .map(|(caster, dx, dy, sigma, _, _, _)| {
            let a = Grid { h, w, data: sils[caster].data.iter().map(|b| *b as u8 as f64).collect() };
            (*caster, blur_shift(&a, *dx, *dy, *sigma))
        })
        .collect();

    let mut model: Vec<[f64; 3]> = vec![*b0; idx.len()];
    for (caster, _, _, _, opacity, colour, _) in &fitted {
        let g = &blurs[caster];
        let clipped = [colour[0].clamp(0.0, 255.0), colour[1].clamp(0.0, 255.0), colour[2].clamp(0.0, 255.0)];
        model.par_iter_mut().zip(idx.par_iter()).for_each(|(m, i)| {
            let a = opacity * g.data[*i];
            for c in 0..3 {
                m[c] = m[c] * (1.0 - a) + clipped[c] * a;
            }
        });
    }
    let (rms, band_rms) = gate(rgb, &domain, &model, l, fills, xs, ys);
    if rms >= WIN_MARGIN * band_rms {
        return false;
    }

    let mut corrected: Vec<[f64; 4]> = (0..h * w)
        .map(|i| {
            let p = rgb.px(i);
            [p[0], p[1], p[2], prep.alpha.data[i] * 255.0]
        })
        .collect();
    let mut composed: Vec<[f64; 3]> = vec![*b0; h * w];
    for (caster, dx, dy, sigma, opacity, colour, group) in &fitted {
        let g = &blurs[caster];
        let clipped = [colour[0].clamp(0.0, 255.0), colour[1].clamp(0.0, 255.0), colour[2].clamp(0.0, 255.0)];
        composed.par_iter_mut().enumerate().for_each(|(i, cc)| {
            let a = opacity * g.data[i];
            for c in 0..3 {
                cc[c] = cc[c] * (1.0 - a) + clipped[c] * a;
            }
        });
        plan.shadows.insert(
            *caster,
            Shadow {
                caster: *caster,
                dx: *dx,
                dy: *dy,
                sigma: *sigma,
                colour: clipped,
                opacity: *opacity,
                inset: false,
                region: filter_region(&sils[caster], *dx, *dy, *sigma),
            },
        );
        for m in group {
            plan.absorbed.insert(*m);
        }
    }
    plan.refit.insert(backdrop);
    for i in 0..h * w {
        for c in 0..3 {
            corrected[i][c] = (corrected[i][c] - (composed[i][c] - b0[c])).clamp(0.0, 255.0);
        }
        corrected[i][3] = corrected[i][3].clamp(0.0, 255.0);
    }
    plan.corrected = Some(corrected);
    true
}

/// An inner shadow darkens the inside of its own shape, against its own fill.
#[allow(clippy::too_many_arguments)]
fn detect_inset(
    plan: &mut ShadowPlan,
    l: &Labels,
    index: &LabelIndex,
    fills: &HashMap<i32, Fill>,
    silhouette: &(dyn Fn(i32) -> Mask + Sync),
    prep: &Prepared,
    xs: &Grid<f64>,
    ys: &Grid<f64>,
    areas: &HashMap<i32, usize>,
    means: &HashMap<i32, [f64; 3]>,
    vis: &[i32],
    total: f64,
    step: usize,
    small_rgb: &[Grid<f64>],
    small_mask: &(dyn Fn(&Mask) -> Mask + Sync),
    sharp_grad: &Grid<f64>,
) {
    let rgb = &prep.rgb;
    let (h, w) = (l.h, l.w);
    let mut order: Vec<i32> = vis.to_vec();
    order.sort_by_key(|lab| std::cmp::Reverse(areas[lab]));
    for caster in order {
        let a = areas[&caster] as f64;
        if a < 0.02 * total || a > 0.6 * total {
            continue;
        }
        let sil = silhouette(caster);
        if !is_sharp(rgb, &sil, sharp_grad) {
            continue;
        }
        let inside: Vec<i32> = vis
            .iter()
            .copied()
            .filter(|lab| {
                if *lab == caster || areas[lab] >= areas[&caster] {
                    return false;
                }
                let px = index.pixels(*lab);
                if px.is_empty() {
                    return false;
                }
                let within = px.iter().filter(|i| sil.data[**i as usize]).count();
                within as f64 / px.len() as f64 > 0.9
            })
            .collect();
        if inside.is_empty() {
            continue;
        }
        let caster_pixels: Vec<[f64; 3]> = index
            .pixels(caster)
            .iter()
            .map(|i| {
                let p = rgb.px(*i as usize);
                [p[0], p[1], p[2]]
            })
            .collect();
        let b0 = backdrop_colour(&caster_pixels);
        let deltas: Vec<[f64; 3]> = inside
            .iter()
            .map(|lab| {
                let m = means[lab];
                [m[0] - b0[0], m[1] - b0[1], m[2] - b0[2]]
            })
            .collect();
        let norms: Vec<f64> = deltas.iter().map(|d| (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt()).collect();
        let nmax = norms.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        if nmax < MIN_PEAK {
            continue;
        }
        let arg = norms.iter().position(|v| *v == nmax).unwrap();
        let u = [deltas[arg][0] / nmax, deltas[arg][1] / nmax, deltas[arg][2] / nmax];
        let group: HashSet<i32> = inside
            .iter()
            .zip(deltas.iter())
            .filter(|(_, d)| {
                let proj = d[0] * u[0] + d[1] * u[1] + d[2] * u[2];
                if proj <= 0.0 {
                    return false;
                }
                let perp: f64 = (0..3).map(|c| (d[c] - proj * u[c]).powi(2)).sum::<f64>().sqrt();
                perp <= RAY_TOL
            })
            .map(|(lab, _)| *lab)
            .collect();
        if group.is_empty() {
            continue;
        }
        let sil_small = small_mask(&sil);
        let mut target = Grid::<f64>::new(sil_small.h, sil_small.w);
        for k in 0..sil_small.len() {
            if sil_small.data[k] {
                target.data[k] = (0..3).map(|c| (small_rgb[c].data[k] - b0[c]) * u[c]).sum();
            }
        }
        if target.data.iter().copied().fold(f64::NEG_INFINITY, f64::max) < MIN_PEAK {
            continue;
        }
        let src = Grid { h: sil_small.h, w: sil_small.w, data: sil_small.data.iter().map(|b| *b as u8 as f64).collect() };
        let (dx, dy, sigma, k, _) = fit_blur(&src, &target, &sil_small, (0.0, 0.0), true);
        if k <= 0.0 {
            continue;
        }
        let a_full = Grid { h, w, data: sil.data.iter().map(|b| *b as u8 as f64).collect() };
        let blurred = blur_shift(&a_full, dx * step as f64, dy * step as f64, sigma * step as f64);
        let g_full: Vec<f64> = (0..h * w).map(|i| a_full.data[i] * (1.0 - blurred.data[i])).collect();
        let idx: Vec<usize> = (0..h * w).filter(|i| sil.data[*i]).collect();
        let model: Vec<[f64; 3]> = idx
            .iter()
            .map(|i| {
                let s = k * g_full[*i];
                [b0[0] + s * u[0], b0[1] + s * u[1], b0[2] + s * u[2]]
            })
            .collect();
        let (rms, band_rms) = gate(rgb, &sil, &model, l, fills, xs, ys);
        if rms >= WIN_MARGIN * band_rms {
            continue;
        }
        let length = cube_exit(&b0, &u);
        if length <= 1e-6 {
            continue;
        }
        let mut opacity = k / length;
        let mut colour = [b0[0] + length * u[0], b0[1] + length * u[1], b0[2] + length * u[2]];
        if opacity > 1.0 {
            opacity = 1.0;
            colour = [b0[0] + k * u[0], b0[1] + k * u[1], b0[2] + k * u[2]];
        }
        if !(0.02..=1.0).contains(&opacity) {
            continue;
        }
        let clipped = [colour[0].clamp(0.0, 255.0), colour[1].clamp(0.0, 255.0), colour[2].clamp(0.0, 255.0)];
        plan.shadows.insert(
            caster,
            Shadow {
                caster,
                dx: dx * step as f64,
                dy: dy * step as f64,
                sigma: sigma * step as f64,
                colour: clipped,
                opacity,
                inset: true,
                region: filter_region(&sil, dx * step as f64, dy * step as f64, sigma * step as f64),
            },
        );
        for m in group {
            plan.absorbed.insert(m);
        }
        plan.refit.insert(caster);
        let corrected: Vec<[f64; 4]> = (0..h * w)
            .map(|i| {
                let p = rgb.px(i);
                let s = k * g_full[i];
                [
                    (p[0] - s * u[0]).clamp(0.0, 255.0),
                    (p[1] - s * u[1]).clamp(0.0, 255.0),
                    (p[2] - s * u[2]).clamp(0.0, 255.0),
                    (prep.alpha.data[i] * 255.0).clamp(0.0, 255.0),
                ]
            })
            .collect();
        plan.corrected = Some(corrected);
        return;
    }
}

// --- emission --------------------------------------------------------------

/// The filter that regenerates the shadow.
///
/// `color-interpolation-filters="sRGB"` is not optional: the default of
/// linearRGB blurs in a different space and changes the falloff.
pub fn shadow_filter_svg(shadow: &Shadow, fid: &str, precision: usize) -> String {
    let p = precision;
    let (rx, ry, rw, rh) = shadow.region;
    let head = format!(
        "<filter id=\"{}\" filterUnits=\"userSpaceOnUse\" x=\"{}\" y=\"{}\" width=\"{}\" height=\"{}\" color-interpolation-filters=\"sRGB\">",
        fid,
        fmt(rx, 1),
        fmt(ry, 1),
        fmt(rw, 1),
        fmt(rh, 1)
    );
    let flood = format!(
        "<feFlood flood-color=\"{}\" flood-opacity=\"{}\"/>",
        hex(&shadow.colour),
        fmt(shadow.opacity, 3)
    );
    if shadow.inset {
        return format!(
            "{}<feOffset in=\"SourceAlpha\" dx=\"{}\" dy=\"{}\" result=\"o\"/><feGaussianBlur in=\"o\" stdDeviation=\"{}\" result=\"b\"/><feComposite in=\"SourceAlpha\" in2=\"b\" operator=\"out\" result=\"r\"/>{}<feComposite in2=\"r\" operator=\"in\" result=\"s\"/><feMerge><feMergeNode in=\"SourceGraphic\"/><feMergeNode in=\"s\"/></feMerge></filter>",
            head,
            fmt(shadow.dx, p),
            fmt(shadow.dy, p),
            fmt(shadow.sigma, p),
            flood
        );
    }
    format!(
        "{}<feGaussianBlur in=\"SourceAlpha\" stdDeviation=\"{}\"/><feOffset dx=\"{}\" dy=\"{}\" result=\"o\"/>{}<feComposite in2=\"o\" operator=\"in\" result=\"s\"/><feMerge><feMergeNode in=\"s\"/><feMergeNode in=\"SourceGraphic\"/></feMerge></filter>",
        head,
        fmt(shadow.sigma, p),
        fmt(shadow.dx, p),
        fmt(shadow.dy, p),
        flood
    )
}
