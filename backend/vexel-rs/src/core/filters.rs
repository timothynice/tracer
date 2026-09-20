//! Separable convolution, the edge filters and the interpolators, with
//! `scipy.ndimage` / `skimage.filters` border semantics.
//!
//! Only the modes the pipeline actually asks for are implemented. scipy's
//! `reflect` is symmetric about the *edge between* pixels — `(d c b a | a b c d)`
//! — which is not numpy's `reflect`; getting that wrong shifts every gradient
//! by a pixel at the border and moves the outermost contour.

use super::grid::Grid;
use rayon::prelude::*;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Mode {
    /// (d c b a | a b c d | d c b a)
    Reflect,
    /// (a a a a | a b c d | d d d d)
    Nearest,
    /// (k k k k | a b c d | k k k k)
    Constant,
}

#[inline]
fn sample(line: &[f64], i: isize, mode: Mode, cval: f64) -> f64 {
    let n = line.len() as isize;
    if i >= 0 && i < n {
        return line[i as usize];
    }
    match mode {
        Mode::Constant => cval,
        Mode::Nearest => line[i.clamp(0, n - 1) as usize],
        Mode::Reflect => {
            // fold repeatedly so a kernel wider than the line still resolves
            let mut j = i;
            loop {
                if j < 0 {
                    j = -j - 1;
                } else if j >= n {
                    j = 2 * n - j - 1;
                } else {
                    return line[j as usize];
                }
            }
        }
    }
}

/// How the tap products are summed.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Accum {
    /// One accumulator, left to right — bit-for-bit what scipy's `correlate1d`
    /// does. The partition's ridge test compares a pixel's gradient with an
    /// interpolated neighbour's, so its filters need the same last bit.
    Exact,
    /// Four independent accumulators. The FMA latency chain is what limits the
    /// exact loop, and breaking it is roughly four times faster; the reassociation
    /// moves the result by an ulp or two, which is why it is confined to the
    /// shadow fit — a numerical search whose own tolerance is 1e-4.
    Fast,
}

/// `scipy.ndimage.correlate1d`: out[i] = Σ_k w[k] · in[i + k − origin_centre].
///
/// Two things keep this off the naive path. The interior — where every tap
/// lands inside the line — is a tight loop with no border logic, so only the
/// two ends pay for the mode. And under `Constant` a tap outside the line
/// contributes exactly zero, so the kernel is clipped to the line rather than
/// multiplying hundreds of zeros: the shadow fit blurs a 256-pixel grid with a
/// σ of 40, where four fifths of a `truncate = 4` kernel hangs over the edge.
fn correlate1d_line(line: &[f64], w: &[f64], mode: Mode, cval: f64, acc_mode: Accum, out: &mut [f64]) {
    let half = (w.len() / 2) as isize;
    let n = line.len() as isize;
    let k = w.len() as isize;
    let lo = half.min(n);
    let hi = (n - (k - 1 - half)).max(lo);
    for i in 0..lo {
        out[i as usize] = border_point(line, w, i, half, mode, cval);
    }
    match acc_mode {
        Accum::Exact => {
            for i in lo..hi {
                let base = (i - half) as usize;
                let src = &line[base..base + w.len()];
                let mut acc = 0.0;
                for (wk, sv) in w.iter().zip(src.iter()) {
                    acc += wk * sv;
                }
                out[i as usize] = acc;
            }
        }
        Accum::Fast => {
            for i in lo..hi {
                let base = (i - half) as usize;
                let src = &line[base..base + w.len()];
                out[i as usize] = dot4(w, src);
            }
        }
    }
    for i in hi..n {
        out[i as usize] = border_point(line, w, i, half, mode, cval);
    }
}

/// Dot product with eight independent accumulators.
///
/// The limit on a single-accumulator loop is the FMA latency chain, not the
/// multiplier; the array form also lets the back end fold pairs into SIMD lanes.
#[inline]
fn dot4(a: &[f64], b: &[f64]) -> f64 {
    const LANES: usize = 8;
    let mut s = [0.0f64; LANES];
    let chunks = a.len() / LANES;
    for c in 0..chunks {
        let av = &a[c * LANES..c * LANES + LANES];
        let bv = &b[c * LANES..c * LANES + LANES];
        for j in 0..LANES {
            s[j] += av[j] * bv[j];
        }
    }
    let mut tail = 0.0;
    for i in chunks * LANES..a.len() {
        tail += a[i] * b[i];
    }
    let mut acc = tail;
    for v in s {
        acc += v;
    }
    acc
}

/// Below this many tap-multiplications, handing the work to the thread pool
/// costs more than doing it.
///
/// The shadow fit runs its search on a grid of a few thousand pixels, and every
/// `par_iter` from outside a worker is a park/unpark round trip.
const PAR_MIN_WORK: usize = 1 << 17;

#[inline]
fn border_point(line: &[f64], w: &[f64], i: isize, half: isize, mode: Mode, cval: f64) -> f64 {
    let n = line.len() as isize;
    // Under Constant every tap outside the line contributes zero, so clip the
    // window to the overlap and then index straight in — no bounds test, no
    // mode dispatch. This is the whole of the blur when the kernel is wider
    // than the grid, which is where the shadow fit spends its time.
    if mode == Mode::Constant && cval == 0.0 {
        let k0 = (half - i).max(0);
        let k1 = (half - i + n).min(w.len() as isize);
        if k1 <= k0 {
            return 0.0;
        }
        let base = (i - half + k0) as usize;
        let wk = &w[k0 as usize..k1 as usize];
        let sv = &line[base..base + wk.len()];
        return dot4(wk, sv);
    }
    let mut acc = 0.0;
    for (k, wk) in w.iter().enumerate() {
        if *wk == 0.0 {
            continue;
        }
        acc += wk * sample(line, i + k as isize - half, mode, cval);
    }
    acc
}

/// Correlate along rows (axis 1).
pub fn correlate1d_x(g: &Grid<f64>, w: &[f64], mode: Mode, cval: f64, acc: Accum) -> Grid<f64> {
    let mut out = Grid::new(g.h, g.w);
    let wdt = g.w;
    if g.len() * w.len() < PAR_MIN_WORK {
        for (o, row) in out.data.chunks_mut(wdt).zip(g.data.chunks(wdt)) {
            correlate1d_line(row, w, mode, cval, acc, o);
        }
        return out;
    }
    out.data
        .par_chunks_mut(wdt)
        .zip(g.data.par_chunks(wdt))
        .for_each(|(o, row)| correlate1d_line(row, w, mode, cval, acc, o));
    out
}

/// Correlate along columns (axis 0). Transposing once and filtering rows beats
/// striding down each column: the column walk misses the cache on every tap.
pub fn correlate1d_y(g: &Grid<f64>, w: &[f64], mode: Mode, cval: f64, acc: Accum) -> Grid<f64> {
    let t = transpose(g);
    let f = correlate1d_x(&t, w, mode, cval, acc);
    transpose(&f)
}

fn transpose(g: &Grid<f64>) -> Grid<f64> {
    const BLOCK: usize = 32;
    let (h, w) = (g.h, g.w);
    let mut out = Grid::new(w, h);
    for r0 in (0..h).step_by(BLOCK) {
        let r1 = (r0 + BLOCK).min(h);
        for c0 in (0..w).step_by(BLOCK) {
            let c1 = (c0 + BLOCK).min(w);
            for r in r0..r1 {
                for c in c0..c1 {
                    out.data[c * h + r] = g.data[r * w + c];
                }
            }
        }
    }
    out
}

/// `scipy.ndimage._gaussian_kernel1d(sigma, 0, radius)` with the default
/// `truncate = 4.0`.
pub fn gaussian_kernel1d(sigma: f64) -> Vec<f64> {
    let radius = (4.0 * sigma + 0.5) as isize;
    let sigma2 = sigma * sigma;
    let mut w: Vec<f64> = (-radius..=radius).map(|x| (-0.5 / sigma2 * (x * x) as f64).exp()).collect();
    let s: f64 = w.iter().sum();
    for v in w.iter_mut() {
        *v /= s;
    }
    w
}

/// `scipy.ndimage.gaussian_filter` on a **float32** array: two separable passes,
/// each accumulating in double and storing float32, exactly as scipy does. The
/// intermediate rounding is not pedantry — the partition compares a pixel's
/// gradient against an interpolated neighbour, and on a plateau those are equal
/// only if both sides round the same way.
pub fn gaussian_filter_f32(g: &Grid<f64>, sigma: f64, mode: Mode, cval: f64) -> Grid<f64> {
    if sigma <= 0.0 {
        return to_f32(g);
    }
    let w = gaussian_kernel1d(sigma);
    let a = to_f32(&correlate1d_y(g, &w, mode, cval, Accum::Exact));
    to_f32(&correlate1d_x(&a, &w, mode, cval, Accum::Exact))
}

/// The same filter in full double precision, for the float64 arrays (the shadow
/// fit's blurs) where the Python has no float32 to match.
pub fn gaussian_filter(g: &Grid<f64>, sigma: f64, mode: Mode, cval: f64) -> Grid<f64> {
    if sigma <= 0.0 {
        return g.clone();
    }
    let w = gaussian_kernel1d(sigma);
    let a = correlate1d_y(g, &w, mode, cval, Accum::Fast);
    correlate1d_x(&a, &w, mode, cval, Accum::Fast)
}

/// Round every value through f32, matching a numpy float32 store.
pub fn to_f32(g: &Grid<f64>) -> Grid<f64> {
    Grid { h: g.h, w: g.w, data: g.data.iter().map(|v| *v as f32 as f64).collect() }
}

const SCHARR_SMOOTH: [f64; 3] = [3.0 / 16.0, 10.0 / 16.0, 3.0 / 16.0];
const SCHARR_EDGE: [f64; 3] = [1.0, 0.0, -1.0];

/// `scipy.ndimage.convolve` with a 3×3 kernel: the kernel is flipped, the sum
/// accumulates in double and the result is stored as float32. skimage builds the
/// Scharr kernels as one 2-D array and convolves once, so splitting this into two
/// separable passes (and rounding between them) would not give the same bits.
fn convolve3x3_f32(g: &Grid<f64>, k: &[[f64; 3]; 3], mode: Mode) -> Grid<f64> {
    let (h, w) = (g.h, g.w);
    let mut out = Grid::new(h, w);
    out.data
        .par_chunks_mut(w)
        .enumerate()
        .for_each(|(r, row)| {
            for (c, o) in row.iter_mut().enumerate() {
                let mut acc = 0.0f64;
                for i in 0..3 {
                    for j in 0..3 {
                        let kv = k[i][j];
                        if kv == 0.0 {
                            continue;
                        }
                        let rr = r as isize + 1 - i as isize;
                        let cc = c as isize + 1 - j as isize;
                        acc += kv * sample2d(g, rr, cc, mode);
                    }
                }
                *o = acc as f32 as f64;
            }
        });
    out
}

#[inline]
fn sample2d(g: &Grid<f64>, r: isize, c: isize, mode: Mode) -> f64 {
    let rr = fold(r, g.h as isize, mode);
    let cc = fold(c, g.w as isize, mode);
    match (rr, cc) {
        (Some(a), Some(b)) => g.data[a * g.w + b],
        _ => 0.0,
    }
}

#[inline]
fn fold(i: isize, n: isize, mode: Mode) -> Option<usize> {
    if i >= 0 && i < n {
        return Some(i as usize);
    }
    match mode {
        Mode::Constant => None,
        Mode::Nearest => Some(i.clamp(0, n - 1) as usize),
        Mode::Reflect => {
            let mut j = i;
            loop {
                if j < 0 {
                    j = -j - 1;
                } else if j >= n {
                    j = 2 * n - j - 1;
                } else {
                    return Some(j as usize);
                }
            }
        }
    }
}

fn hscharr() -> [[f64; 3]; 3] {
    let mut k = [[0.0; 3]; 3];
    for (i, row) in k.iter_mut().enumerate() {
        for (j, v) in row.iter_mut().enumerate() {
            *v = SCHARR_EDGE[i] * SCHARR_SMOOTH[j];
        }
    }
    k
}

fn vscharr() -> [[f64; 3]; 3] {
    let mut k = [[0.0; 3]; 3];
    for (i, row) in k.iter_mut().enumerate() {
        for (j, v) in row.iter_mut().enumerate() {
            *v = SCHARR_SMOOTH[i] * SCHARR_EDGE[j];
        }
    }
    k
}

/// `skimage.filters.scharr_h`: derivative down the rows (y).
pub fn scharr_h(g: &Grid<f64>) -> Grid<f64> {
    convolve3x3_f32(g, &hscharr(), Mode::Reflect)
}

/// `skimage.filters.scharr_v`: derivative across the columns (x).
pub fn scharr_v(g: &Grid<f64>) -> Grid<f64> {
    convolve3x3_f32(g, &vscharr(), Mode::Reflect)
}

/// `skimage.filters.scharr`: √(h² + v²) / √2, accumulated in float32 the way
/// `_generic_edge_filter` does.
pub fn scharr(g: &Grid<f64>) -> Grid<f64> {
    let hh = scharr_h(g);
    let vv = scharr_v(g);
    let root2 = (2.0f32).sqrt();
    Grid {
        h: g.h,
        w: g.w,
        data: hh
            .data
            .iter()
            .zip(vv.data.iter())
            .map(|(a, b)| {
                let (a, b) = (*a as f32, *b as f32);
                let s = a * a + b * b;
                (s.sqrt() / root2) as f64
            })
            .collect(),
    }
}

/// `scipy.ndimage.sobel(axis)` — axis 0 differentiates down the rows.
pub fn sobel(g: &Grid<f64>, axis: usize) -> Grid<f64> {
    let d: [f64; 3] = [-1.0, 0.0, 1.0];
    let s: [f64; 3] = [1.0, 2.0, 1.0];
    if axis == 0 {
        let a = correlate1d_y(g, &d, Mode::Reflect, 0.0, Accum::Exact);
        correlate1d_x(&a, &s, Mode::Reflect, 0.0, Accum::Exact)
    } else {
        let a = correlate1d_x(g, &d, Mode::Reflect, 0.0, Accum::Exact);
        correlate1d_y(&a, &s, Mode::Reflect, 0.0, Accum::Exact)
    }
}

/// `scipy.ndimage.minimum_filter(size=3, mode="reflect")`, separable.
pub fn minimum_filter3(g: &Grid<f64>) -> Grid<f64> {
    let mut tmp = Grid::new(g.h, g.w);
    for r in 0..g.h {
        for c in 0..g.w {
            let mut m = f64::INFINITY;
            for dc in -1isize..=1 {
                let v = sample(g.row(r), c as isize + dc, Mode::Reflect, 0.0);
                if v < m {
                    m = v;
                }
            }
            tmp.data[r * g.w + c] = m;
        }
    }
    let mut out = Grid::new(g.h, g.w);
    for c in 0..g.w {
        let col: Vec<f64> = (0..g.h).map(|r| tmp.data[r * g.w + c]).collect();
        for r in 0..g.h {
            let mut m = f64::INFINITY;
            for dr in -1isize..=1 {
                let v = sample(&col, r as isize + dr, Mode::Reflect, 0.0);
                if v < m {
                    m = v;
                }
            }
            out.data[r * g.w + c] = m;
        }
    }
    out
}

/// Bilinear sample with clamped coordinates — `map_coordinates(order=1, mode="nearest")`.
pub fn bilinear_nearest(g: &Grid<f64>, y: f64, x: f64) -> f64 {
    let yy = y.clamp(0.0, (g.h - 1) as f64);
    let xx = x.clamp(0.0, (g.w - 1) as f64);
    let r0 = yy.floor() as usize;
    let c0 = xx.floor() as usize;
    let r1 = (r0 + 1).min(g.h - 1);
    let c1 = (c0 + 1).min(g.w - 1);
    let fr = yy - r0 as f64;
    let fc = xx - c0 as f64;
    let v00 = g.data[r0 * g.w + c0];
    let v01 = g.data[r0 * g.w + c1];
    let v10 = g.data[r1 * g.w + c0];
    let v11 = g.data[r1 * g.w + c1];
    (v00 * (1.0 - fc) + v01 * fc) * (1.0 - fr) + (v10 * (1.0 - fc) + v11 * fc) * fr
}

/// `scipy.ndimage.shift(a, (dy, dx), order=1, mode="constant", cval=0)`.
/// Output pixel (r, c) samples the input at (r − dy, c − dx); anything whose
/// source falls outside `[0, n − 1]` takes `cval`.
pub fn shift_bilinear(g: &Grid<f64>, dy: f64, dx: f64, cval: f64) -> Grid<f64> {
    let (h, w) = (g.h, g.w);
    let mut out = Grid::new(h, w);
    let body = |r: usize, row: &mut [f64]| {
            let sy = r as f64 - dy;
            if sy < 0.0 || sy > (h - 1) as f64 {
                for v in row.iter_mut() {
                    *v = cval;
                }
                return;
            }
            let r0 = sy.floor() as usize;
            let r1 = (r0 + 1).min(h - 1);
            let fr = sy - r0 as f64;
            for (c, v) in row.iter_mut().enumerate() {
                let sx = c as f64 - dx;
                if sx < 0.0 || sx > (w - 1) as f64 {
                    *v = cval;
                    continue;
                }
                let c0 = sx.floor() as usize;
                let c1 = (c0 + 1).min(w - 1);
                let fc = sx - c0 as f64;
                let v00 = g.data[r0 * w + c0];
                let v01 = g.data[r0 * w + c1];
                let v10 = g.data[r1 * w + c0];
                let v11 = g.data[r1 * w + c1];
                *v = (v00 * (1.0 - fc) + v01 * fc) * (1.0 - fr) + (v10 * (1.0 - fc) + v11 * fc) * fr;
            }
    };
    if h * w < PAR_MIN_WORK {
        for (r, row) in out.data.chunks_mut(w).enumerate() {
            body(r, row);
        }
    } else {
        out.data.par_chunks_mut(w).enumerate().for_each(|(r, row)| body(r, row));
    }
    out
}
