//! Stage 4: reconstruct the best solid / linear / radial fill for a region.
//!
//! Colours are fitted in sRGB (0–255) with alpha scaled to 0–255 as a fourth
//! channel, because SVG interpolates gradient stops in sRGB. Positions are
//! pixel-centre coordinates in SVG space.

use crate::core::linalg::{eigh2, lstsq, Mat};
use crate::core::optimise::{nelder_mead, NmOptions};
use crate::core::rng::{choice_without_replacement, Pcg64};

/// How many pixels a fill is fitted from.
///
/// The Python caps this at 2 500 for speed, which leaves real sampling noise in
/// the radial centre search: two draws of 2 500 from an 12 000-pixel backdrop
/// put the fitted centre half a pixel apart and move the gradient stops by ten
/// levels. Rust can afford an order of magnitude more, and the extra samples
/// buy a measurably better fit rather than a different one.
pub const MAX_FIT_SAMPLES: usize = 25_000;

/// Fewest core pixels a gradient is fitted from (`fit_fill`'s `core`): a few
/// pixels in the middle of a band-wide region leave the ramp's stops to be
/// extrapolated from a handful of values, which is ill-posed. Fewer, and the
/// region is its core's solid. `fills.CORE_GRADIENT_MIN` in the Python.
pub const CORE_GRADIENT_MIN: usize = 24;

/// Fewest pixels between two gradient stops (see `ramp_fit`).
pub const KNOT_GAP: f64 = 3.0;

#[derive(Clone, Debug)]
pub struct Stop {
    pub offset: f64,
    pub rgba: [f64; 4],
}

#[derive(Clone, Debug)]
pub enum Fill {
    Solid { rgba: [f64; 4] },
    Linear { x1: f64, y1: f64, x2: f64, y2: f64, stops: Vec<Stop> },
    Radial { cx: f64, cy: f64, r: f64, stops: Vec<Stop> },
}

#[derive(Clone, Copy)]
pub struct FitParams {
    pub gradients: bool,
    pub max_stops: usize,
    /// RMS in 0..255 units at which a model is "good enough".
    pub tol: f64,
}

pub fn interp_stops(t: f64, stops: &[Stop]) -> [f64; 4] {
    // `np.interp`: clamp outside the knot range, linear within
    let t = t.clamp(0.0, 1.0);
    if stops.is_empty() {
        return [0.0; 4];
    }
    if t <= stops[0].offset {
        return stops[0].rgba;
    }
    let last = stops.len() - 1;
    if t >= stops[last].offset {
        return stops[last].rgba;
    }
    let mut i = 0;
    while i + 1 < stops.len() && stops[i + 1].offset < t {
        i += 1;
    }
    let (a, b) = (&stops[i], &stops[i + 1]);
    let span = b.offset - a.offset;
    let f = if span > 0.0 { (t - a.offset) / span } else { 0.0 };
    let mut out = [0.0; 4];
    for c in 0..4 {
        out[c] = a.rgba[c] + f * (b.rgba[c] - a.rgba[c]);
    }
    out
}

impl Fill {
    pub fn kind(&self) -> &'static str {
        match self {
            Fill::Solid { .. } => "solid",
            Fill::Linear { .. } => "linear",
            Fill::Radial { .. } => "radial",
        }
    }

    /// The ramp position t at (x, y), unclamped: 0 at (x1, y1) and 1 at
    /// (x2, y2) for a linear ramp, distance from the centre over r for a radial.
    /// Zero for a solid.
    pub fn param(&self, x: f64, y: f64) -> f64 {
        match self {
            Fill::Solid { .. } => 0.0,
            Fill::Linear { x1, y1, x2, y2, .. } => {
                let (dx, dy) = (x2 - x1, y2 - y1);
                let denom = dx * dx + dy * dy;
                if denom > 0.0 { ((x - x1) * dx + (y - y1) * dy) / denom } else { 0.0 }
            }
            Fill::Radial { cx, cy, r, .. } => ((x - cx).hypot(y - cy)) / r.max(1e-9),
        }
    }

    pub fn evaluate_one(&self, x: f64, y: f64) -> [f64; 4] {
        match self {
            Fill::Solid { rgba } => *rgba,
            Fill::Linear { stops, .. } | Fill::Radial { stops, .. } => interp_stops(self.param(x, y), stops),
        }
    }

    pub fn evaluate(&self, xs: &[f64], ys: &[f64]) -> Vec<[f64; 4]> {
        xs.iter().zip(ys.iter()).map(|(x, y)| self.evaluate_one(*x, *y)).collect()
    }

    pub fn alpha_is_invisible(&self) -> bool {
        match self {
            Fill::Solid { rgba } => rgba[3] < 2.0,
            Fill::Linear { stops, .. } | Fill::Radial { stops, .. } => {
                if stops.is_empty() {
                    true
                } else {
                    stops.iter().all(|s| s.rgba[3] < 2.0)
                }
            }
        }
    }
}

fn rms(pred: &[[f64; 4]], target: &[[f64; 4]], w: &[f64]) -> f64 {
    let mut num = 0.0;
    let mut den = 0.0;
    for i in 0..target.len() {
        let mut e = 0.0;
        for c in 0..4 {
            let d = pred[i][c] - target[i][c];
            e += d * d;
        }
        num += w[i] * e;
        den += w[i];
    }
    (num / (den * 4.0).max(1e-12)).sqrt()
}

fn rms_flat(pred: &[f64], target: &[[f64; 4]], w: &[f64]) -> f64 {
    let mut num = 0.0;
    let mut den = 0.0;
    for i in 0..target.len() {
        let mut e = 0.0;
        for c in 0..4 {
            let d = pred[i * 4 + c] - target[i][c];
            e += d * d;
        }
        num += w[i] * e;
        den += w[i];
    }
    (num / (den * 4.0).max(1e-12)).sqrt()
}

/// A trial knot placement: (weighted SSE, the knot, its stops, its prediction).
type Candidate = (f64, f64, Vec<[f64; 4]>, Vec<f64>);

/// The pixels a ramp's "good enough" test is made over when they are not the
/// fitted ones: (t, colours, weights).
pub type Check<'a> = (&'a [f64], &'a [[f64; 4]], &'a [f64]);

/// Weighted piecewise-linear colour ramp over t ∈ [0, 1], adding knots where
/// the residual peaks.
///
/// `span` is the ramp's length in pixels: two stops closer than `KNOT_GAP` px
/// describe a step, which is an edge, not a gradient, and rest on a pixel or
/// two. `check` is what the "good enough" test is measured over when the ramp
/// is fitted to a region's core: the whole region. `fills.ramp_fit` in the
/// Python.
pub fn ramp_fit(
    t: &[f64],
    colours: &[[f64; 4]],
    w: &[f64],
    max_stops: usize,
    tol: f64,
    span: f64,
    check: Option<Check>,
) -> Vec<Stop> {
    let n = t.len();
    let gap = if span > 0.0 { (KNOT_GAP / span).max(0.04) } else { 0.04 };
    // A hat basis only overlaps its neighbours, so the weighted normal matrix
    // is symmetric tridiagonal: the whole fit is one pass to accumulate it and
    // a Thomas solve on a matrix of a handful of rows. Building the full n×k
    // design and running a QR over it — which is what the Python does through
    // `np.linalg.lstsq` — is the same answer for ten times the work, and this
    // runs up to forty-five times per region.
    let solve = |knots: &[f64]| -> (Vec<[f64; 4]>, Vec<f64>) {
        let k = knots.len();
        let mut diag = vec![0.0f64; k];
        let mut off = vec![0.0f64; k.saturating_sub(1)];
        let mut rhs = vec![[0.0f64; 4]; k];
        // (basis index, fraction) per sample, reused for the prediction
        let mut place: Vec<(usize, f64)> = Vec::with_capacity(n);
        for i in 0..n {
            // `np.searchsorted(knots, t, side="right") - 1`, clipped to a valid span
            let mut idx = knots.partition_point(|v| *v <= t[i]);
            idx = idx.saturating_sub(1).min(k - 2);
            let (left, right) = (knots[idx], knots[idx + 1]);
            let frac = if right > left { (t[i] - left) / (right - left).max(1e-12) } else { 0.0 };
            place.push((idx, frac));
            let (b0, b1) = (1.0 - frac, frac);
            let wi = w[i];
            diag[idx] += wi * b0 * b0;
            diag[idx + 1] += wi * b1 * b1;
            off[idx] += wi * b0 * b1;
            for c in 0..4 {
                rhs[idx][c] += wi * b0 * colours[i][c];
                rhs[idx + 1][c] += wi * b1 * colours[i][c];
            }
        }
        // a knot with no support would leave a zero pivot; the ridge is small
        // enough to leave a supported knot untouched
        let scale = diag.iter().copied().fold(0.0f64, f64::max).max(1e-30);
        for d in diag.iter_mut() {
            *d += 1e-12 * scale;
        }
        let coef = solve_tridiagonal(&diag, &off, &rhs);

        let stops: Vec<[f64; 4]> = coef
            .iter()
            .map(|c| [c[0].clamp(0.0, 255.0), c[1].clamp(0.0, 255.0), c[2].clamp(0.0, 255.0), c[3].clamp(0.0, 255.0)])
            .collect();
        // the residual is measured against the *unclipped* fit
        let mut pred = vec![0.0f64; n * 4];
        for (i, (idx, frac)) in place.iter().enumerate() {
            for c in 0..4 {
                pred[i * 4 + c] = (1.0 - frac) * coef[*idx][c] + frac * coef[idx + 1][c];
            }
        }
        (stops, pred)
    };

    let sse = |pred: &[f64]| -> f64 {
        let mut s = 0.0;
        for i in 0..n {
            let mut e = 0.0;
            for c in 0..4 {
                let d = pred[i * 4 + c] - colours[i][c];
                e += d * d;
            }
            s += w[i] * e;
        }
        s
    };

    let mut knots: Vec<f64> = vec![0.0, 1.0];
    let (mut coef, mut pred) = solve(&knots);
    let mut current = sse(&pred);
    let candidates: Vec<f64> = (1..16).map(|i| i as f64 / 16.0).collect();
    while knots.len() < max_stops {
        let good = match check {
            None => rms_flat(&pred, colours, w) <= tol,
            Some((tc, cc, wc)) => {
                let stops: Vec<Stop> =
                    knots.iter().zip(coef.iter()).map(|(k, c)| Stop { offset: *k, rgba: *c }).collect();
                let pc: Vec<[f64; 4]> = tc.iter().map(|v| interp_stops(*v, &stops)).collect();
                rms(&pc, cc, wc) <= tol
            }
        };
        if good {
            break;
        }
        let mut best: Option<Candidate> = None;
        for c in &candidates {
            if knots.iter().map(|k| (c - k).abs()).fold(f64::INFINITY, f64::min) < gap {
                continue;
            }
            let mut trial = knots.clone();
            trial.push(*c);
            trial.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let (tc, tp) = solve(&trial);
            let s = sse(&tp);
            if best.as_ref().is_none_or(|b| s < b.0) {
                best = Some((s, *c, tc, tp));
            }
        }
        match best {
            Some((s, knot, tc, tp)) if s <= current * 0.98 => {
                current = s;
                coef = tc;
                pred = tp;
                knots.push(knot);
                knots.sort_by(|a, b| a.partial_cmp(b).unwrap());
            }
            _ => break,
        }
    }
    knots.iter().zip(coef.iter()).map(|(k, c)| Stop { offset: *k, rgba: *c }).collect()
}

/// Which pixels of a region the fill is fitted from.
///
/// This is a reproducible draw from a PCG64 seeded with a constant, by Floyd's
/// algorithm — the same scheme `numpy.random.Generator.choice(replace=False)`
/// uses, and bit-identical to it whenever numpy takes that path. numpy switches
/// to another algorithm once the population is more than four times the sample,
/// and that branch is undocumented and free to change between releases, so it
/// is deliberately not emulated: pinning Vexel's output to a private detail of
/// one numpy version would mean an upgrade silently changing every trace.
/// Thomas algorithm for a symmetric tridiagonal system with several
/// right-hand sides.
fn solve_tridiagonal(diag: &[f64], off: &[f64], rhs: &[[f64; 4]]) -> Vec<[f64; 4]> {
    let n = diag.len();
    if n == 1 {
        let d = if diag[0].abs() > 1e-300 { diag[0] } else { 1.0 };
        return vec![[rhs[0][0] / d, rhs[0][1] / d, rhs[0][2] / d, rhs[0][3] / d]];
    }
    let mut c = vec![0.0f64; n];
    let mut d = vec![[0.0f64; 4]; n];
    let mut denom = diag[0];
    if denom.abs() < 1e-300 {
        denom = 1e-300;
    }
    c[0] = off[0] / denom;
    for ch in 0..4 {
        d[0][ch] = rhs[0][ch] / denom;
    }
    for i in 1..n {
        let a = off[i - 1];
        let mut m = diag[i] - a * c[i - 1];
        if m.abs() < 1e-300 {
            m = 1e-300;
        }
        if i + 1 < n {
            c[i] = off[i] / m;
        }
        for ch in 0..4 {
            d[i][ch] = (rhs[i][ch] - a * d[i - 1][ch]) / m;
        }
    }
    let mut x = vec![[0.0f64; 4]; n];
    x[n - 1] = d[n - 1];
    for i in (0..n - 1).rev() {
        for ch in 0..4 {
            x[i][ch] = d[i][ch] - c[i] * x[i + 1][ch];
        }
    }
    x
}

fn subsample(n: usize) -> Vec<usize> {
    let cap = MAX_FIT_SAMPLES;
    if n <= cap {
        return (0..n).collect();
    }
    let mut rng = Pcg64::seed_1234();
    choice_without_replacement(&mut rng, n as u64, cap)
        .into_iter()
        .map(|v| v as usize)
        .collect()
}

/// Best fill for the pixels `(xs, ys)` with colours `rgba255`.
///
/// `weights` lets the caller down-weight boundary pixels, which are
/// anti-aliasing mixtures of two fills and would otherwise bias the colour.
///
/// `core` marks the region's interior (`weights::fill_core`); the rest is its
/// edge band, whose colour is the edge's (anti-aliasing, a blur, a sharpening
/// halo). With a core the solid is the core's mean; "good enough" (the solid
/// early exit, the ramps' stopping test) is still judged over every pixel;
/// a gradient's direction, span and stops come from the core alone and it
/// must beat the solid there. See `fills.fit_fill` in the Python.
pub fn fit_fill(
    xs: &[f64],
    ys: &[f64],
    rgba255: &[[f64; 4]],
    params: &FitParams,
    weights: Option<&[f64]>,
    core: Option<&[bool]>,
) -> Fill {
    let n = xs.len();
    if n == 0 {
        return Fill::Solid { rgba: [0.0; 4] };
    }
    let mut w_all: Vec<f64> = rgba255.iter().map(|c| 0.3 + 0.7 * (c[3] / 255.0)).collect();
    if let Some(weights) = weights {
        for (a, b) in w_all.iter_mut().zip(weights.iter()) {
            *a *= *b;
        }
    }

    let sel = subsample(n);
    let mut x: Vec<f64> = sel.iter().map(|i| xs[*i]).collect();
    let mut y: Vec<f64> = sel.iter().map(|i| ys[*i]).collect();
    let mut c: Vec<[f64; 4]> = sel.iter().map(|i| rgba255[*i]).collect();
    let mut w: Vec<f64> = sel.iter().map(|i| w_all[*i]).collect();
    let k: Option<Vec<bool>> = core.map(|m| sel.iter().map(|i| m[*i]).collect::<Vec<bool>>()).filter(|k| k.iter().any(|v| *v) && !k.iter().all(|v| *v));
    let inner = |i: usize| k.as_ref().is_none_or(|k| k[i]);

    let mut wsum = 0.0;
    let mut acc = [0.0f64; 4];
    for i in 0..x.len() {
        if inner(i) {
            wsum += w[i];
            for ch in 0..4 {
                acc[ch] += c[i][ch] * w[i];
            }
        }
    }
    let mut mean = [0.0f64; 4];
    for ch in 0..4 {
        mean[ch] = acc[ch] / wsum;
    }
    let solid = Fill::Solid { rgba: mean };
    let mut rms_solid = rms(&vec![mean; x.len()], &c, &w);
    let too_few = match &k {
        None => x.len() < 8,
        Some(k) => k.iter().filter(|v| **v).count() < CORE_GRADIENT_MIN,
    };
    if !params.gradients || rms_solid <= params.tol || too_few {
        return solid;
    }
    // every pixel, for the ramps' "good enough" test
    let mut full: Option<(Vec<f64>, Vec<f64>, Vec<[f64; 4]>, Vec<f64>)> = None;
    if let Some(k) = &k {
        let pick = |v: &[f64]| -> Vec<f64> { v.iter().zip(k.iter()).filter(|(_, m)| **m).map(|(a, _)| *a).collect() };
        let (xk, yk, wk) = (pick(&x), pick(&y), pick(&w));
        let ck: Vec<[f64; 4]> = c.iter().zip(k.iter()).filter(|(_, m)| **m).map(|(a, _)| *a).collect();
        full = Some((std::mem::replace(&mut x, xk), std::mem::replace(&mut y, yk), std::mem::replace(&mut c, ck), std::mem::replace(&mut w, wk)));
        rms_solid = rms(&vec![mean; x.len()], &c, &w);
    }
    let ns = x.len();
    let wsum: f64 = w.iter().sum();

    let mut candidates: Vec<(f64, Fill)> = vec![(rms_solid, solid.clone())];

    // --- linear: direction from per-channel planar gradients ------------------
    let cx0: f64 = x.iter().zip(w.iter()).map(|(a, b)| a * b).sum::<f64>() / wsum;
    let cy0: f64 = y.iter().zip(w.iter()).map(|(a, b)| a * b).sum::<f64>() / wsum;
    let mut a_mat = Mat::zeros(ns, 3);
    let mut b_mat = Mat::zeros(ns, 4);
    for i in 0..ns {
        let sw = w[i].sqrt();
        a_mat.set(i, 0, sw);
        a_mat.set(i, 1, (x[i] - cx0) * sw);
        a_mat.set(i, 2, (y[i] - cy0) * sw);
        for ch in 0..4 {
            b_mat.set(i, ch, c[i][ch] * sw);
        }
    }
    let coef = lstsq(&a_mat, &b_mat); // (3, 4)
    // g is (4, 2): each channel's gradient vector
    let mut cov = [0.0f64; 3]; // xx, xy, yy
    for ch in 0..4 {
        let (gx, gy) = (coef.at(1, ch), coef.at(2, ch));
        cov[0] += gx * gx;
        cov[1] += gx * gy;
        cov[2] += gy * gy;
    }
    let (evals, evecs) = eigh2(cov[0], cov[1], cov[2]);
    let mut d = if evals[1] >= evals[0] { evecs[1] } else { evecs[0] };
    // the eigenvector sign is arbitrary: orient +x (or +y when vertical) so the
    // stop order is deterministic
    if d[0] < -1e-9 || (d[0].abs() <= 1e-9 && d[1] < 0.0) {
        d = [-d[0], -d[1]];
    }
    if (d[0] * d[0] + d[1] * d[1]).sqrt() > 0.0 && evals[0].max(evals[1]) > 1e-12 {
        let t_raw: Vec<f64> = (0..ns).map(|i| (x[i] - cx0) * d[0] + (y[i] - cy0) * d[1]).collect();
        // the ramp spans the whole region (a real gradient reaches the edge),
        // with the core's colours carried on over the band, not the band's
        let t_all: Option<Vec<f64>> = full
            .as_ref()
            .map(|(xf, yf, _, _)| (0..xf.len()).map(|i| (xf[i] - cx0) * d[0] + (yf[i] - cy0) * d[1]).collect());
        let t_span = t_all.as_deref().unwrap_or(&t_raw);
        let tmin = t_span.iter().copied().fold(f64::INFINITY, f64::min);
        let tmax = t_span.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        if tmax - tmin > 1e-6 {
            let t: Vec<f64> = t_raw.iter().map(|v| (v - tmin) / (tmax - tmin)).collect();
            let t_chk: Option<Vec<f64>> = t_all.as_ref().map(|ta| ta.iter().map(|v| (v - tmin) / (tmax - tmin)).collect());
            let chk: Option<Check> = match (&full, &t_chk) {
                (Some((_, _, cf, wf)), Some(tc)) => Some((tc.as_slice(), cf.as_slice(), wf.as_slice())),
                _ => None,
            };
            let stops = ramp_fit(&t, &c, &w, params.max_stops, params.tol, tmax - tmin, chk);
            let lin = Fill::Linear {
                x1: cx0 + tmin * d[0],
                y1: cy0 + tmin * d[1],
                x2: cx0 + tmax * d[0],
                y2: cy0 + tmax * d[1],
                stops,
            };
            let pred = lin.evaluate(&x, &y);
            candidates.push((rms(&pred, &c, &w), lin));
        }
    }

    // --- radial: centre from the quadratic fit, refined numerically -----------
    // The centre is geometry and is searched over every pixel: the band is
    // where a shadow or glow is strongest (it hugs its caster), and on the core
    // alone the search found no radial at all. Only the stops come from the
    // core. `fills.fit_fill` in the Python.
    let (xa, ya, ca, wa): (&[f64], &[f64], &[[f64; 4]], &[f64]) = match &full {
        Some((xf, yf, cf, wf)) => (xf, yf, cf, wf),
        None => (&x, &y, &c, &w),
    };
    let na = xa.len();
    if ns >= 24 {
        let wsa: f64 = wa.iter().sum();
        let gx0: f64 = xa.iter().zip(wa.iter()).map(|(a, b)| a * b).sum::<f64>() / wsa;
        let gy0: f64 = ya.iter().zip(wa.iter()).map(|(a, b)| a * b).sum::<f64>() / wsa;
        let xr: Vec<f64> = xa.iter().map(|v| v - gx0).collect();
        let yr: Vec<f64> = ya.iter().map(|v| v - gy0).collect();
        let mut b_all = Mat::zeros(na, 4);
        let mut q = Mat::zeros(na, 6);
        for i in 0..na {
            let sw = wa[i].sqrt();
            q.set(i, 0, sw);
            q.set(i, 1, xr[i] * sw);
            q.set(i, 2, yr[i] * sw);
            q.set(i, 3, xr[i] * xr[i] * sw);
            q.set(i, 4, xr[i] * yr[i] * sw);
            q.set(i, 5, yr[i] * yr[i] * sw);
            for ch in 0..4 {
                b_all.set(i, ch, ca[i][ch] * sw);
            }
        }
        let qc = lstsq(&q, &b_all); // (6, 4)
        let mut curv = [0.0f64; 4];
        for ch in 0..4 {
            curv[ch] = 0.5 * (qc.at(3, ch) + qc.at(5, ch));
        }
        let weight: Vec<f64> = curv.iter().map(|v| v.abs()).collect();
        let wsum_c: f64 = weight.iter().sum();
        if wsum_c > 1e-9 {
            let mut centre = [0.0f64; 2];
            for ch in 0..4 {
                let safe = if curv[ch].abs() > 1e-12 { curv[ch] } else { 1e-12 };
                centre[0] += (-qc.at(1, ch) / (2.0 * safe)) * weight[ch];
                centre[1] += (-qc.at(2, ch) / (2.0 * safe)) * weight[ch];
            }
            centre[0] /= wsum_c;
            centre[1] /= wsum_c;
            let (xmin, xmax) = minmax(&xr);
            let (ymin, ymax) = minmax(&yr);
            let (span_x, span_y) = (xmax - xmin, ymax - ymin);
            centre[0] = centre[0].clamp(xmin - 2.0 * span_x, xmax + 2.0 * span_x);
            centre[1] = centre[1].clamp(ymin - 2.0 * span_y, ymax + 2.0 * span_y);

            // a cheap, smooth surrogate for the centre search: colour as a cubic
            // polynomial in r
            let objective = |cen: &[f64]| -> f64 {
                let r: Vec<f64> = (0..na).map(|i| (xr[i] - cen[0]).hypot(yr[i] - cen[1])).collect();
                let rmax = r.iter().copied().fold(f64::NEG_INFINITY, f64::max).max(1e-9);
                let mut basis = Mat::zeros(na, 4);
                for i in 0..na {
                    let rn = r[i] / rmax;
                    let sw = wa[i].sqrt();
                    basis.set(i, 0, sw);
                    basis.set(i, 1, rn * sw);
                    basis.set(i, 2, rn * rn * sw);
                    basis.set(i, 3, rn * rn * rn * sw);
                }
                let cf = lstsq(&basis, &b_all);
                let mut pred = vec![0.0f64; na * 4];
                for i in 0..na {
                    let rn = r[i] / rmax;
                    let raw = [1.0, rn, rn * rn, rn * rn * rn];
                    for (j, bij) in raw.iter().enumerate() {
                        for ch in 0..4 {
                            pred[i * 4 + ch] += bij * cf.at(j, ch);
                        }
                    }
                }
                rms_flat(&pred, ca, wa)
            };

            let radial_for = |cen: &[f64]| -> (f64, Fill) {
                let r_all: Vec<f64> = (0..na).map(|i| (xr[i] - cen[0]).hypot(yr[i] - cen[1])).collect();
                let rmax = r_all.iter().copied().fold(f64::NEG_INFINITY, f64::max);
                if rmax < 1e-6 {
                    return (
                        f64::INFINITY,
                        Fill::Radial { cx: cen[0] + gx0, cy: cen[1] + gy0, r: 1.0, stops: Vec::new() },
                    );
                }
                let r: Vec<f64> = if full.is_some() {
                    (0..ns).map(|i| (x[i] - gx0 - cen[0]).hypot(y[i] - gy0 - cen[1])).collect()
                } else {
                    r_all.clone()
                };
                let tn: Vec<f64> = r.iter().map(|v| v / rmax).collect();
                let r_chk: Option<Vec<f64>> = full.as_ref().map(|_| r_all.iter().map(|v| v / rmax).collect());
                let chk: Option<Check> = r_chk.as_ref().map(|rc| (rc.as_slice(), ca, wa));
                let stops = ramp_fit(&tn, &c, &w, params.max_stops, params.tol, rmax, chk);
                let rad = Fill::Radial { cx: cen[0] + gx0, cy: cen[1] + gy0, r: rmax, stops };
                let pred = rad.evaluate(&x, &y);
                (rms(&pred, &c, &w), rad)
            };

            // Only search when the radial surrogate at the initial centre
            // already beats the best candidate so far by a margin; most regions
            // are not radial. Both are measured over every pixel, like the search.
            let best_so_far = if full.is_some() {
                candidates.iter().map(|(_, f)| rms(&f.evaluate(xa, ya), ca, wa)).fold(f64::INFINITY, f64::min)
            } else {
                candidates.iter().map(|(r, _)| *r).fold(f64::INFINITY, f64::min)
            };
            if objective(&centre) < best_so_far - 0.25 * params.tol {
                let opts = NmOptions { xatol: 0.05, fatol: 0.01, maxiter: 80, maxfev: usize::MAX };
                let start = crate::core::optimise::default_simplex(&centre);
                let best = nelder_mead(objective, start, &opts);
                let (r, rad) = radial_for(&best);
                candidates.push((r, rad));
            }
        }
    }

    // --- choose: a gradient must buy a real improvement over solid ------------
    let penalty = |kind: &str| match kind {
        "linear" => 0.35 * params.tol,
        "radial" => 0.5 * params.tol,
        _ => 0.0,
    };
    let mut best_i = 0usize;
    let mut best_score = f64::INFINITY;
    for (i, (r, f)) in candidates.iter().enumerate() {
        let s = r + penalty(f.kind());
        if s < best_score {
            best_score = s;
            best_i = i;
        }
    }
    let (best_rms, best) = candidates.swap_remove(best_i);
    if best.kind() != "solid" {
        if rms_solid - best_rms < 0.25 * params.tol {
            return solid;
        }
        // A ramp fitted to a core a few rows deep can follow the core's own
        // noise and carry it on across the band; a gradient whose range over
        // the core is under 2·tol is one the solid already holds within tol.
        // See `fills.fit_fill`.
        if full.is_some() {
            let pred = best.evaluate(&x, &y);
            let mut range = 0.0f64;
            for ch in 0..4 {
                let (lo, hi) = minmax(&pred.iter().map(|p| p[ch]).collect::<Vec<_>>());
                range = range.max(hi - lo);
            }
            if range < 2.0 * params.tol {
                return solid;
            }
        }
        // In a mostly transparent region, a model only somewhat better than
        // solid is fitting faint ink (a sub-pixel line in an empty field), not
        // a gradient, and would paint a haze. Leave it solid so the rescue pass
        // can promote the ink.
        let mostly_transparent = mean[3] < 0.2 * 255.0;
        if mostly_transparent && best_rms > params.tol && best_rms > 0.5 * rms_solid {
            return solid;
        }
    }
    best
}

fn minmax(v: &[f64]) -> (f64, f64) {
    let mut lo = f64::INFINITY;
    let mut hi = f64::NEG_INFINITY;
    for x in v {
        if *x < lo {
            lo = *x;
        }
        if *x > hi {
            hi = *x;
        }
    }
    (lo, hi)
}

// --- serialisation ---------------------------------------------------------

/// `fills._fmt`: fixed precision with trailing zeros stripped. Unlike
/// `curves._f` it does not fold "-0" to "0", so it is kept separate.
pub fn fmt(v: f64, precision: usize) -> String {
    let s = format!("{:.*}", precision, v);
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        s
    }
}

pub fn hex(rgb: &[f64]) -> String {
    let c = |v: f64| -> u8 { v.clamp(0.0, 255.0).round() as u8 };
    format!("#{:02x}{:02x}{:02x}", c(rgb[0]), c(rgb[1]), c(rgb[2]))
}

fn stops_svg(stops: &[Stop], _precision: usize) -> String {
    let mut out = String::new();
    for s in stops {
        let a = s.rgba[3].clamp(0.0, 255.0) / 255.0;
        let op = if a >= 0.999 { String::new() } else { format!(" stop-opacity=\"{}\"", fmt(a, 3)) };
        out.push_str(&format!(
            "<stop offset=\"{}\" stop-color=\"{}\"{}/>",
            fmt(s.offset, 3),
            hex(&s.rgba),
            op
        ));
    }
    out
}

impl Fill {
    /// (defs entry, fill attributes) for this fill under the id `gid`.
    pub fn svg(&self, gid: &str, precision: usize) -> (String, String) {
        let p = precision;
        match self {
            Fill::Solid { rgba } => {
                let a = rgba[3].clamp(0.0, 255.0) / 255.0;
                let mut attrs = format!("fill=\"{}\"", hex(rgba));
                if a < 0.999 {
                    attrs.push_str(&format!(" fill-opacity=\"{}\"", fmt(a, 3)));
                }
                (String::new(), attrs)
            }
            Fill::Linear { x1, y1, x2, y2, stops } => (
                format!(
                    "<linearGradient id=\"{}\" gradientUnits=\"userSpaceOnUse\" x1=\"{}\" y1=\"{}\" x2=\"{}\" y2=\"{}\">{}</linearGradient>",
                    gid,
                    fmt(*x1, p),
                    fmt(*y1, p),
                    fmt(*x2, p),
                    fmt(*y2, p),
                    stops_svg(stops, p)
                ),
                format!("fill=\"url(#{})\"", gid),
            ),
            Fill::Radial { cx, cy, r, stops } => (
                format!(
                    "<radialGradient id=\"{}\" gradientUnits=\"userSpaceOnUse\" cx=\"{}\" cy=\"{}\" r=\"{}\">{}</radialGradient>",
                    gid,
                    fmt(*cx, p),
                    fmt(*cy, p),
                    fmt(*r, p),
                    stops_svg(stops, p)
                ),
                format!("fill=\"url(#{})\"", gid),
            ),
        }
    }
}
