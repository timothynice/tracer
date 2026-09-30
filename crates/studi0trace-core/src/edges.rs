//! Canny edges, disk dilation and the scorecard's edge F1: `quality._edges`,
//! `quality._edge_f1` and the skimage 0.26 / scipy 1.18 code they call
//! (`skimage.feature.canny`, `skimage.morphology.dilation` with `disk`).
//!
//! The port is to the bit, not to a band. Every intermediate is stored in the
//! precision the Python stores it in and summed in the order scipy sums it, so
//! the edge maps are the same pixel set as skimage's on the golden fixtures:
//!
//! - `quality.luminance` is float32 (`0.299 r + 0.587 g + 0.114 b`, each Python
//!   float weakly cast to float32 under NEP 50), and `luminance(rgb) / 255.0` stays
//!   float32 for the same reason. skimage keeps a float32 image float32
//!   (`_supported_float_type`, and `gaussian` does not upcast), so the smoothing,
//!   the Sobel filters, the magnitude and the suppression are all float32 arrays.
//! - `scipy.ndimage.correlate1d` reads each line into a double buffer, sums in
//!   double and stores to the output's float32. For a symmetric kernel it pairs the
//!   taps, `w[c]·x[i] + Σ w[c−j]·(x[i−j] + x[i+j])` from the outermost pair in, and
//!   the arm64 build the fixtures come from contracts each step into one fused
//!   multiply-add (`f64::mul_add` here, which fuses on every target; an x86-64 scipy
//!   without FMA would round each step twice). An antisymmetric kernel
//!   (`[−1, 0, 1]`) pairs the taps as differences the same way.
//!   `vexel_rs::core::filters` sums the taps left to right without fusing, which can
//!   differ in the last bit of the double, so it is not reused here. (Stored to
//!   float32 the two agree on every fixture value; the order was measured on
//!   float64 lines, where it shows.)
//! - The Gaussian's taps are normalised by numpy's pairwise sum, not a running sum.
//! - The suppression's interpolation is skimage's Cython: `neigh_2 · w` in float32,
//!   plus `neigh_1 · (1.0 − w)` in double (the literal `1.0` is a C double), compared
//!   with the float32 magnitude in double.
//!
//! Hysteresis has no ties to break: an edge pixel is any pixel above the low
//! threshold 8-connected to one above the high threshold, which is the set
//! `ndi.label` plus the good-label lookup produces, whatever the label numbers.

use std::borrow::Cow;

/// skimage `canny`'s defaults for a float image (`low_threshold=None` → 0.1,
/// `high_threshold=None` → 0.2, not scaled by the dtype's range).
pub const LOW_THRESHOLD: f64 = 0.1;
pub const HIGH_THRESHOLD: f64 = 0.2;
/// `quality._edges`' `sigma`.
pub const SIGMA: f64 = 1.0;

/// `quality._edges`: Canny on the Rec. 601 luma of an RGB image, at sigma 1.
/// `rgb` is `h × w × 3` bytes, row-major; the result is one flag per pixel.
pub fn edges(rgb: &[u8], h: usize, w: usize) -> Vec<bool> {
    assert_eq!(rgb.len(), h * w * 3, "edges: {} bytes for a {h}x{w} RGB image", rgb.len());
    let gray: Vec<f32> = rgb.as_chunks::<3>().0.iter().map(|[r, g, b]| luminance(*r, *g, *b) / 255.0f32).collect();
    canny_f32(&gray, h, w, SIGMA)
}

/// `quality.luminance` for one pixel: float32 arithmetic, left to right.
#[inline]
fn luminance(r: u8, g: u8, b: u8) -> f32 {
    0.299f32 * r as f32 + 0.587f32 * g as f32 + 0.114f32 * b as f32
}

/// skimage `feature.canny(image, sigma)` with the default thresholds, no mask and
/// `mode='constant'`, for a **float32** image: every value is rounded to `f32` on
/// the way in, and everything after is the float32 computation skimage does for a
/// float32 array (the only kind `quality._edges` hands it). A side under 3 px has
/// no interior pixel, so the result is all `false`, as in skimage.
///
/// Panics if `gray.len() != h * w` or `sigma` is negative or not finite (skimage
/// raises on a negative sigma).
pub fn canny(gray: &[f64], h: usize, w: usize, sigma: f64) -> Vec<bool> {
    assert_eq!(gray.len(), h * w, "canny: {} values for a {h}x{w} image", gray.len());
    let g: Vec<f32> = gray.iter().map(|v| *v as f32).collect();
    canny_f32(&g, h, w, sigma)
}

fn canny_f32(image: &[f32], h: usize, w: usize, sigma: f64) -> Vec<bool> {
    let s = stages_f32(image, h, w, sigma);
    hysteresis(&s.suppressed, h, w)
}

/// canny's float32 intermediates, each the array skimage holds at that point.
#[doc(hidden)]
pub struct CannyStages {
    /// `_preprocess`'s smoothed image (already divided by the bleed-over).
    pub smoothed: Vec<f32>,
    /// `ndi.sobel(smoothed, axis=0)`.
    pub isobel: Vec<f32>,
    /// `ndi.sobel(smoothed, axis=1)`.
    pub jsobel: Vec<f32>,
    pub magnitude: Vec<f32>,
    /// `_nonmaximum_suppression_bilinear`'s output: the magnitude where kept, else 0.
    pub suppressed: Vec<f32>,
}

/// The intermediates of [`canny`], for the golden tests that pin each stage to the bit.
#[doc(hidden)]
pub fn canny_stages(gray: &[f64], h: usize, w: usize, sigma: f64) -> CannyStages {
    assert_eq!(gray.len(), h * w, "canny_stages: {} values for a {h}x{w} image", gray.len());
    let g: Vec<f32> = gray.iter().map(|v| *v as f32).collect();
    stages_f32(&g, h, w, sigma)
}

fn stages_f32(image: &[f32], h: usize, w: usize, sigma: f64) -> CannyStages {
    assert!(sigma >= 0.0 && sigma.is_finite(), "canny: sigma must be finite and non-negative, got {sigma}");
    if h == 0 || w == 0 {
        let none = Vec::new;
        return CannyStages { smoothed: none(), isobel: none(), jsobel: none(), magnitude: none(), suppressed: none() };
    }
    // _preprocess with mask=None and mode='constant': the image and an all-ones mask
    // are smoothed alike, and the image is divided by the mask's blur plus float32 eps
    // (the "bleed-over" that undoes the zeros the constant border mixes in).
    let bleed = gaussian_f32(&vec![1.0f32; h * w], h, w, sigma);
    let mut smoothed = gaussian_f32(image, h, w, sigma);
    for (s, b) in smoothed.iter_mut().zip(&bleed) {
        *s /= *b + f32::EPSILON;
    }

    // ndi.sobel(smoothed, axis=1), then axis=0; mode 'reflect' (sobel's own default).
    let jsobel = sobel_f32(&smoothed, h, w, 1);
    let isobel = sobel_f32(&smoothed, h, w, 0);
    // magnitude = isobel * isobel; magnitude += jsobel * jsobel; sqrt — float32 throughout.
    let magnitude: Vec<f32> = isobel.iter().zip(&jsobel).map(|(i, j)| (i * i + j * j).sqrt()).collect();

    let suppressed = suppress(&isobel, &jsobel, &magnitude, h, w);
    CannyStages { smoothed, isobel, jsobel, magnitude, suppressed }
}

/// `scipy.ndimage._gaussian_kernel1d(sigma, 0, radius)[::-1]` with
/// `radius = int(4.0 * sigma + 0.5)` — the taps `gaussian_filter1d` correlates with.
/// The kernel is symmetric to the bit, so the reversal changes nothing.
pub fn gaussian_kernel(sigma: f64) -> Vec<f64> {
    let radius = (4.0 * sigma + 0.5) as i64;
    let sigma2 = sigma * sigma;
    let phi: Vec<f64> = (-radius..=radius).map(|x| (-0.5 / sigma2 * (x * x) as f64).exp()).collect();
    let total = pairwise_sum(&phi);
    phi.iter().map(|v| v / total).collect()
}

/// numpy's pairwise summation (`DOUBLE_pairwise_sum`, block size 128), which is
/// what `ndarray.sum` does for a contiguous float64 array.
fn pairwise_sum(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        let mut res = 0.0;
        for v in a {
            res += v;
        }
        res
    } else if n <= 128 {
        let mut r = [0.0f64; 8];
        r.copy_from_slice(&a[..8]);
        let mut i = 8;
        while i < n - n % 8 {
            for j in 0..8 {
                r[j] += a[i + j];
            }
            i += 8;
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        while i < n {
            res += a[i];
            i += 1;
        }
        res
    } else {
        let mut n2 = n / 2;
        n2 -= n2 % 8;
        pairwise_sum(&a[..n2]) + pairwise_sum(&a[n2..])
    }
}

/// `scipy.ndimage.gaussian_filter` of a float32 image, `mode='constant', cval=0`:
/// axis 0 then axis 1, each pass stored as float32. scipy skips an axis whose
/// sigma is 1e-15 or less, which leaves the image as it is.
fn gaussian_f32(image: &[f32], h: usize, w: usize, sigma: f64) -> Vec<f32> {
    if sigma <= 1e-15 {
        return image.to_vec();
    }
    let taps = gaussian_kernel(sigma);
    let r = taps.len() / 2;
    let centre = taps[r];

    // Down the columns, a whole row of outputs at a time (the same arithmetic per
    // pixel as a column walk, without striding through memory).
    let mut down = vec![0.0f32; h * w];
    let mut acc = vec![0.0f64; w];
    for y in 0..h {
        let here = &image[y * w..(y + 1) * w];
        for (a, x) in acc.iter_mut().zip(here) {
            *a = *x as f64 * centre;
        }
        for j in (1..=r).rev() {
            // Rows outside the image are cval = 0.
            let above = y.checked_sub(j).map(|u| &image[u * w..(u + 1) * w]);
            let below = (y + j < h).then(|| &image[(y + j) * w..(y + j + 1) * w]);
            let k = taps[r - j];
            for c in 0..w {
                let s = above.map_or(0.0, |l| l[c] as f64) + below.map_or(0.0, |l| l[c] as f64);
                acc[c] = s.mul_add(k, acc[c]);
            }
        }
        for (o, a) in down[y * w..(y + 1) * w].iter_mut().zip(&acc) {
            *o = *a as f32;
        }
    }

    // Along the rows, on the float32 result of the first pass.
    let mut out = vec![0.0f32; h * w];
    let mut line = vec![0.0f64; w + 2 * r];
    for y in 0..h {
        for (l, v) in line[r..r + w].iter_mut().zip(&down[y * w..(y + 1) * w]) {
            *l = *v as f64;
        }
        for c in 0..w {
            let i = c + r;
            let mut a = line[i] * centre;
            for j in (1..=r).rev() {
                a = (line[i - j] + line[i + j]).mul_add(taps[r - j], a);
            }
            out[y * w + c] = a as f32;
        }
    }
    out
}

/// scipy's `reflect` for a one-pixel overhang: `x[-1] = x[0]`, `x[n] = x[n-1]`.
#[inline]
fn reflect(i: isize, n: usize) -> usize {
    if i < 0 {
        0
    } else if i as usize >= n {
        n - 1
    } else {
        i as usize
    }
}

/// `scipy.ndimage.sobel(image, axis)` on float32, `mode='reflect'`: the derivative
/// `[-1, 0, 1]` along `axis` (stored float32), then the smoothing `[1, 2, 1]` along
/// the other axis (stored float32). Axis 0 differentiates down the rows.
fn sobel_f32(image: &[f32], h: usize, w: usize, axis: usize) -> Vec<f32> {
    // correlate1d with an antisymmetric kernel: x0·0 + (x[-1] − x[+1])·(−1), fused.
    let derivative = |xm: f32, x0: f32, xp: f32| (xm as f64 - xp as f64).mul_add(-1.0, x0 as f64 * 0.0) as f32;
    // correlate1d with a symmetric kernel: x0·2 + (x[-1] + x[+1])·1, fused.
    let smooth = |xm: f32, x0: f32, xp: f32| (xm as f64 + xp as f64).mul_add(1.0, x0 as f64 * 2.0) as f32;
    let at = |g: &[f32], y: isize, x: isize| g[reflect(y, h) * w + reflect(x, w)];

    let mut first = vec![0.0f32; h * w];
    let mut out = vec![0.0f32; h * w];
    for y in 0..h as isize {
        for x in 0..w as isize {
            first[y as usize * w + x as usize] = if axis == 0 {
                derivative(at(image, y - 1, x), at(image, y, x), at(image, y + 1, x))
            } else {
                derivative(at(image, y, x - 1), at(image, y, x), at(image, y, x + 1))
            };
        }
    }
    for y in 0..h as isize {
        for x in 0..w as isize {
            out[y as usize * w + x as usize] = if axis == 0 {
                smooth(at(&first, y, x - 1), at(&first, y, x), at(&first, y, x + 1))
            } else {
                smooth(at(&first, y - 1, x), at(&first, y, x), at(&first, y + 1, x))
            };
        }
    }
    out
}

/// skimage `_nonmaximum_suppression_bilinear` (Cython, float32 specialisation),
/// with the low threshold applied: the magnitude where a pixel is a local maximum
/// along its gradient, interpolating between the two neighbours the gradient
/// passes between, and 0 elsewhere. The eroded mask of `_preprocess` (no mask
/// given) is every pixel off the frame, so the frame is never an edge and the
/// neighbours are always inside the image. Public only for the golden test that
/// feeds it gradients the smoothing would never produce.
#[doc(hidden)]
pub fn suppress(isobel: &[f32], jsobel: &[f32], magnitude: &[f32], h: usize, w: usize) -> Vec<f32> {
    assert!(isobel.len() == h * w && jsobel.len() == h * w && magnitude.len() == h * w, "suppress: arrays are not {h}x{w}");
    let mut out = vec![0.0f32; h * w];
    if h < 3 || w < 3 {
        return out;
    }
    // Comparing with 0.1 in float32 or in double selects the same float32 magnitudes
    // (float32(0.1) is the least float32 at or above 0.1), so the threshold's C type
    // does not matter.
    let low = LOW_THRESHOLD as f32;
    // neigh_2 · w is a float32 product; neigh_1 · (1.0 − w) and the sum are double.
    // (Only the compiled module is installed; this is the one of the candidate
    // roundings that agrees with it on the tie-heavy fixture, where all-float32 and
    // all-double each disagree on over a dozen pixels.)
    let lerp = |n1: f32, n2: f32, wt: f32| (n2 * wt) as f64 + n1 as f64 * (1.0 - wt as f64);
    for x in 1..h - 1 {
        for y in 1..w - 1 {
            let p = x * w + y;
            let m = magnitude[p];
            // `not (m >= low_threshold)`: a NaN magnitude is skipped too.
            if m < low || m.is_nan() {
                continue;
            }
            let (i, j) = (isobel[p], jsobel[p]);
            let (is_down, is_up, is_left, is_right) = (i <= 0.0, i >= 0.0, j <= 0.0, j >= 0.0);
            // Gradients of one sign, or of opposite signs (a zero component is both).
            let cond1 = (is_up && is_right) || (is_down && is_left);
            let cond2 = (is_down && is_right) || (is_up && is_left);
            if !cond1 && !cond2 {
                continue;
            }
            let (ai, aj) = (i.abs(), j.abs());
            // x is the row and y the column, as in the Cython.
            let at = |dx: isize, dy: isize| magnitude[(x as isize + dx) as usize * w + (y as isize + dy) as usize];
            let mf = m as f64;
            if cond1 {
                let (wt, n11, n12, n21, n22) = if ai > aj {
                    (aj / ai, at(1, 0), at(1, 1), at(-1, 0), at(-1, -1))
                } else {
                    (ai / aj, at(0, 1), at(1, 1), at(0, -1), at(-1, -1))
                };
                if lerp(n11, n12, wt) <= mf && lerp(n21, n22, wt) <= mf {
                    out[p] = m;
                    continue;
                }
            }
            if cond2 {
                let (wt, n11, n12, n21, n22) = if ai < aj {
                    (ai / aj, at(0, 1), at(-1, 1), at(0, -1), at(1, -1))
                } else {
                    (aj / ai, at(-1, 0), at(-1, 1), at(1, 0), at(1, -1))
                };
                if lerp(n11, n12, wt) <= mf && lerp(n21, n22, wt) <= mf {
                    out[p] = m;
                }
            }
        }
    }
    out
}

/// canny's double threshold: the 8-connected components of the suppressed
/// magnitude above zero that hold a pixel at or above the high threshold
/// (`low_masked >= high_threshold`, a float32 array against a Python float, so
/// compared in float32).
fn hysteresis(low: &[f32], h: usize, w: usize) -> Vec<bool> {
    let high = HIGH_THRESHOLD as f32;
    let mut out = vec![false; h * w];
    let mut stack: Vec<usize> = Vec::new();
    for (p, v) in low.iter().enumerate() {
        if *v > 0.0 && *v >= high && !out[p] {
            out[p] = true;
            stack.push(p);
            while let Some(q) = stack.pop() {
                let (y, x) = ((q / w) as isize, (q % w) as isize);
                for dy in -1..=1isize {
                    for dx in -1..=1isize {
                        let (ny, nx) = (y + dy, x + dx);
                        if ny < 0 || nx < 0 || ny >= h as isize || nx >= w as isize {
                            continue;
                        }
                        let n = ny as usize * w + nx as usize;
                        if low[n] > 0.0 && !out[n] {
                            out[n] = true;
                            stack.push(n);
                        }
                    }
                }
            }
        }
    }
    out
}

/// skimage `dilation(mask, disk(r))` for a boolean mask: a pixel is set when any
/// set pixel lies within `dx² + dy² <= r²` of it. skimage's default `mode='reflect'`
/// cannot add anything at the frame for a disk (the mirror image of a pixel past
/// the edge is no farther from the centre than the pixel was), so outside is unset.
pub fn dilate_disk(mask: &[bool], h: usize, w: usize, r: usize) -> Vec<bool> {
    assert_eq!(mask.len(), h * w, "dilate_disk: {} flags for a {h}x{w} mask", mask.len());
    let ri = r as isize;
    let offsets: Vec<(isize, isize)> = (-ri..=ri)
        .flat_map(|dy| (-ri..=ri).map(move |dx| (dy, dx)))
        .filter(|(dy, dx)| dy * dy + dx * dx <= ri * ri)
        .collect();
    let mut out = vec![false; h * w];
    for (p, _) in mask.iter().enumerate().filter(|(_, m)| **m) {
        let (y, x) = ((p / w) as isize, (p % w) as isize);
        for (dy, dx) in &offsets {
            let (ny, nx) = (y + dy, x + dx);
            if ny >= 0 && nx >= 0 && (ny as usize) < h && (nx as usize) < w {
                out[ny as usize * w + nx as usize] = true;
            }
        }
    }
    out
}

/// `quality._edge_f1`: the F1 of edge maps `ea` (the reference) and `eb` matched
/// within `tolerance_px`. `ea_wide` is `ea` already dilated by `disk(tolerance_px)`,
/// which the scorecard computes once per source; `None` dilates it here.
/// Both empty is a perfect match (1.0); one empty, or no pixel of either within
/// reach of the other, is 0.0.
pub fn f1(ea: &[bool], eb: &[bool], ea_wide: Option<&[bool]>, h: usize, w: usize, tolerance_px: usize) -> f64 {
    assert!(ea.len() == h * w && eb.len() == h * w, "f1: edge maps of {} and {} for {h}x{w}", ea.len(), eb.len());
    let count = |m: &[bool]| m.iter().filter(|v| **v).count();
    let (na, nb) = (count(ea), count(eb));
    if na == 0 && nb == 0 {
        return 1.0;
    }
    if na == 0 || nb == 0 {
        return 0.0;
    }
    let wide: Cow<[bool]> = match ea_wide {
        Some(m) => {
            assert_eq!(m.len(), h * w, "f1: ea_wide of {} for {h}x{w}", m.len());
            Cow::Borrowed(m)
        }
        None => Cow::Owned(dilate_disk(ea, h, w, tolerance_px)),
    };
    let eb_wide = dilate_disk(eb, h, w, tolerance_px);
    let both = |a: &[bool], b: &[bool]| a.iter().zip(b).filter(|(x, y)| **x && **y).count();
    let precision = both(eb, &wide) as f64 / nb as f64;
    let recall = both(ea, &eb_wide) as f64 / na as f64;
    if precision + recall == 0.0 {
        return 0.0;
    }
    2.0 * precision * recall / (precision + recall)
}
