//! An outline as the card walks it: resampled at [`STEP`], its turning at a chord of `k`
//! samples, the turning that cancels inside a window, and the sign changes of its curvature
//! (`quality._resample`, `_wrap`, `_turns`, `_cancelled`, `_flips`, `_dilate`, `_inflections`).
use super::{py_round, radians, CardError, CORNER_GUARD, CORNER_SNAP, INFLECT_HYST, STEP};
use std::f64::consts::PI;

fn geometry(e: &str) -> CardError {
    CardError::Geometry(e.into())
}

/// `np.linalg.norm(b - a)` of one row: `sqrt(dx·dx + dy·dy)`, unfused.
fn dist(a: [f64; 2], b: [f64; 2]) -> f64 {
    let (dx, dy) = (b[0] - a[0], b[1] - a[1]);
    (dx * dx + dy * dy).sqrt()
}

/// `np.allclose(a, b)`: `|a - b| <= 1e-8 + 1e-5·|b|` where `b` is finite, or `a == b`.
fn allclose(a: [f64; 2], b: [f64; 2]) -> bool {
    (0..2).all(|i| ((a[i] - b[i]).abs() <= 1e-08 + 1e-05 * b[i].abs() && b[i].is_finite()) || a[i] == b[i])
}

/// numpy's `binary_search_with_guess` (`compiled_base.c`): the last `j` with `arr[j] <= key`,
/// -1 below the first, `len` above the last; the guess decides which of equal keys is found.
fn search(key: f64, arr: &[f64], guess: isize) -> isize {
    const LIKELY_IN_CACHE_SIZE: isize = 8;
    let len = arr.len() as isize;
    let at = |i: isize| arr[i as usize];
    if key > at(len - 1) {
        return len;
    } else if key < at(0) {
        return -1;
    }
    if len <= 4 {
        let mut i = 1;
        while i < len && key >= at(i) {
            i += 1;
        }
        return i - 1;
    }
    let guess = guess.min(len - 3).max(1);
    let (mut imin, mut imax) = (0, len);
    if key < at(guess) {
        if key < at(guess - 1) {
            imax = guess - 1;
            if guess > LIKELY_IN_CACHE_SIZE && key >= at(guess - LIKELY_IN_CACHE_SIZE) {
                imin = guess - LIKELY_IN_CACHE_SIZE;
            }
        } else {
            return guess - 1;
        }
    } else if key < at(guess + 1) {
        return guess;
    } else if key < at(guess + 2) {
        return guess + 1;
    } else {
        imin = guess + 2;
        if guess < len - LIKELY_IN_CACHE_SIZE - 1 && key < at(guess + LIKELY_IN_CACHE_SIZE) {
            imax = guess + LIKELY_IN_CACHE_SIZE;
        }
    }
    while imin < imax {
        let imid = imin + ((imax - imin) >> 1);
        if key >= at(imid) {
            imin = imid + 1;
        } else {
            imax = imid;
        }
    }
    imin - 1
}

/// `np.interp(x, xp, fp)` (`arr_interp`), whose `slope * (x - xp[j]) + fp[j]` the compiler
/// fuses (measured: 300 511 interpolations, every one the fused value).
fn interp(x: &[f64], xp: &[f64], fp: &[f64]) -> Vec<f64> {
    let len = xp.len() as isize;
    let (left, right) = (fp[0], fp[fp.len() - 1]);
    let mut j = 0isize;
    x.iter()
        .map(|&v| {
            if v.is_nan() {
                return v;
            }
            j = search(v, xp, j);
            if j == -1 {
                left
            } else if j == len {
                right
            } else {
                let u = j as usize;
                if j == len - 1 || xp[u] == v {
                    fp[u]
                } else {
                    let slope = (fp[u + 1] - fp[u]) / (xp[u + 1] - xp[u]);
                    let mut r = slope.mul_add(v - xp[u], fp[u]);
                    if r.is_nan() {
                        r = slope.mul_add(v - xp[u + 1], fp[u + 1]);
                        if r.is_nan() && fp[u] == fp[u + 1] {
                            r = fp[u];
                        }
                    }
                    r
                }
            }
        })
        .collect()
}

/// `_resample`: the outline at `STEP` px intervals of its length (a closed one closed first,
/// points within 1e-9 px of the one before dropped), or its points as they are when it is
/// shorter than a step. `budget` is what is left of [`super::MAX_SAMPLES`].
pub(super) fn resample(pts: &[[f64; 2]], closed: bool, budget: &mut usize) -> Result<Vec<[f64; 2]>, CardError> {
    if pts.is_empty() {
        // pts[0], or a boolean index of one against no rows
        return Err(geometry(if closed {
            "index 0 is out of bounds for axis 0 with size 0"
        } else {
            "boolean index did not match indexed array along axis 0; size of axis is 0 but size of corresponding boolean axis is 1"
        }));
    }
    let mut p = pts.to_vec();
    if closed && !allclose(p[0], p[p.len() - 1]) {
        p.push(p[0]);
    }
    let mut kept = Vec::with_capacity(p.len());
    kept.push(p[0]);
    for w in p.windows(2) {
        if dist(w[0], w[1]) > 1e-9 {
            kept.push(w[1]);
        }
    }
    let mut s = Vec::with_capacity(kept.len());
    s.push(0.0);
    let mut run = 0.0;
    for (i, w) in kept.windows(2).enumerate() {
        let d = dist(w[0], w[1]);
        run = if i == 0 { d } else { run + d };
        s.push(run);
    }
    let total = s[s.len() - 1];
    if total < STEP {
        return Ok(kept);
    }
    let count = (total / STEP).round_ties_even();
    let n = if count.is_nan() || count.is_infinite() {
        py_round(total / STEP)? as usize // raises as round() does
    } else if count >= *budget as f64 {
        return Err(CardError::TooLarge);
    } else {
        (count as usize).max(2)
    };
    let samples = if closed { n } else { n + 1 };
    if samples > *budget {
        return Err(CardError::TooLarge);
    }
    *budget -= samples;
    // np.linspace(0, total, n + 1): i · (total / n), the last one `total` itself
    let step = total / n as f64;
    let mut t: Vec<f64> = (0..samples).map(|i| i as f64 * step + 0.0).collect();
    if !closed {
        t[n] = total;
    }
    let xs: Vec<f64> = kept.iter().map(|v| v[0]).collect();
    let ys: Vec<f64> = kept.iter().map(|v| v[1]).collect();
    let (x, y) = (interp(&t, &s, &xs), interp(&t, &s, &ys));
    Ok(x.into_iter().zip(y).map(|(a, b)| [a, b]).collect())
}

/// `_wrap`: an angle into [-π, π), as `(a + π) % 2π - π` with numpy's (Python's) remainder,
/// which takes the sign of the divisor.
pub(super) fn wrap(a: f64) -> f64 {
    let tau = 2.0 * PI;
    let mut r = (a + PI) % tau;
    if r != 0.0 {
        if r < 0.0 {
            r += tau;
        }
    } else {
        r = 0.0;
    }
    r - PI
}

/// `_turns`: the turning between successive chord directions of span `k` samples. Closed, one
/// a sample, chord `i` running from sample `i` to `i + k` round the ring; open, `n - k - 1` of
/// them (none when that is not positive).
pub(super) fn turns(q: &[[f64; 2]], closed: bool, k: usize) -> Vec<f64> {
    let n = q.len();
    let angle = |a: [f64; 2], b: [f64; 2]| (b[1] - a[1]).atan2(b[0] - a[0]);
    if closed {
        if n == 0 {
            return Vec::new();
        }
        let ang: Vec<f64> = (0..n).map(|i| angle(q[i], q[(i + k) % n])).collect();
        return (0..n).map(|i| wrap(ang[(i + 1) % n] - ang[i])).collect();
    }
    if n <= k + 1 {
        return Vec::new();
    }
    let ang: Vec<f64> = (0..n - k).map(|i| angle(q[i], q[i + k])).collect();
    ang.windows(2).map(|w| wrap(w[1] - w[0])).collect()
}

/// `_cancelled`: the turning cancelled inside a `k`-sample window, per position: the fine
/// turning |θ| spread over the `k` positions a chord of span `k` sees it from, less the
/// coarse turning |Θ| of the chord centred there. It sums to the wobble. Closed, one a sample;
/// open, `n + 2·(k / 2)` positions, so that nothing is lost off the ends.
pub(super) fn cancelled(q: &[[f64; 2]], closed: bool, k: usize) -> Vec<f64> {
    let n = q.len();
    let fine: Vec<f64> = turns(q, closed, 1).into_iter().map(f64::abs).collect();
    let coarse: Vec<f64> = turns(q, closed, k).into_iter().map(f64::abs).collect();
    let h = k / 2;
    let kf = k as f64;
    if closed {
        // c[p] = coarse[p - h] (np.roll by h); ext = fine[(-h .. n + k - h) mod n]
        let back = |i: usize| (i as isize - h as isize).rem_euclid(n as isize) as usize;
        let cs = cumsum((0..n + k).map(|i| fine[back(i)]));
        return (0..n).map(|p| (cs[k + p] - cs[p]) / kf - coarse[back(p)]).collect();
    }
    let size = n + 2 * h;
    let mut f = vec![0.0; size];
    f[h + 1..h + 1 + fine.len()].copy_from_slice(&fine);
    let mut c = vec![0.0; size];
    c[2 * h..2 * h + coarse.len()].copy_from_slice(&coarse);
    let padded = std::iter::repeat_n(0.0, h.saturating_sub(1)).chain(f.iter().copied()).chain(std::iter::repeat_n(0.0, k + 1));
    let cs = cumsum(padded);
    (0..size).map(|p| (cs[k + p] - cs[p]) / kf - c[p]).collect()
}

/// `np.concatenate([[0.0], np.cumsum(v)])`: a running sum, left to right.
fn cumsum(v: impl Iterator<Item = f64>) -> Vec<f64> {
    let mut out = vec![0.0];
    let mut run = 0.0;
    for (i, x) in v.enumerate() {
        run = if i == 0 { x } else { run + x };
        out.push(run);
    }
    out
}

/// `_flips`: the curvature's sign changes, a zig-zag over the cumulative turning with `hyst`
/// radians of threshold. A closed curve is scanned twice and only the second lap counts.
/// Returns the sample indices.
pub(super) fn flips(turn: &[f64], closed: bool, hyst: f64) -> Vec<usize> {
    let n = turn.len();
    if n == 0 {
        return Vec::new();
    }
    let laps = if closed { 2 } else { 1 };
    let mut direction = 0i8;
    let (mut hi, mut lo) = (0.0f64, 0.0f64);
    let mut theta = 0.0;
    let mut out = Vec::new();
    for i in 0..n * laps {
        theta = if i == 0 { turn[0] } else { theta + turn[i % n] };
        let t = theta;
        if direction >= 0 && t > hi {
            hi = t; // Python's max(hi, t)
        }
        if direction <= 0 && t < lo {
            lo = t;
        }
        if direction >= 0 && hi - t >= hyst {
            if direction == 1 && (!closed || i >= n) {
                out.push(i % n);
            }
            direction = -1;
            lo = t;
        } else if direction <= 0 && t - lo >= hyst {
            if direction == -1 && (!closed || i >= n) {
                out.push(i % n);
            }
            direction = 1;
            hi = t;
        }
    }
    out
}

/// `_dilate`: `mask` grown by `r` samples either way, round the ring when `closed` (an int32
/// convolution with a box of `2r + 1`, `valid` over a wrapped copy or `same`). `r` is at most
/// the mask's length, as wherever the card calls it.
pub(super) fn dilate(mask: &[bool], r: usize, closed: bool) -> Vec<bool> {
    let n = mask.len();
    if !mask.iter().any(|&m| m) || r == 0 {
        return mask.to_vec();
    }
    let at = |i: isize| -> bool {
        if closed {
            mask[i.rem_euclid(n as isize) as usize]
        } else {
            i >= 0 && (i as usize) < n && mask[i as usize]
        }
    };
    let r = r as isize;
    (0..n as isize).map(|i| (i - r..=i + r).any(at)).collect()
}

/// `_inflections`: curvature sign changes inside the smooth runs of an outline, with every
/// corner ([`CORNER_SNAP`] degrees within 2 px) and [`CORNER_GUARD`] px either side of it cut
/// out first. Returns sample indices.
pub(super) fn inflections(q: &[[f64; 2]], closed: bool, k: usize) -> Result<Vec<usize>, CardError> {
    let n = q.len();
    let d = turns(q, closed, 1);
    if d.len() < 2 * k {
        return Ok(Vec::new());
    }
    let hyst = radians(INFLECT_HYST);
    let m = py_round(2.0 / STEP)? as usize;
    let nd = d.len();
    let ext: Vec<f64> = if closed {
        d[nd - m / 2..].iter().chain(&d).chain(&d[..m - m / 2]).copied().collect()
    } else {
        std::iter::repeat_n(0.0, m / 2).chain(d.iter().copied()).chain(std::iter::repeat_n(0.0, m - m / 2)).collect()
    };
    let cs = cumsum(ext.into_iter());
    let snap = radians(CORNER_SNAP);
    let near: Vec<bool> = (0..nd).map(|i| (cs[m + i] - cs[i]).abs() >= snap).collect();
    let corner = dilate(&near, py_round(CORNER_GUARD / STEP)? as usize, closed);
    let t = turns(q, closed, k);
    if !corner.iter().any(|&c| c) {
        let at = flips(&t, closed, hyst);
        return Ok(if closed { at } else { at.into_iter().map(|j| j + k / 2).collect() });
    }
    // t[i] is inside a run when no sample from i to i + k + 1 is at a corner
    let cm: Vec<bool> = if closed {
        corner.iter().chain(&corner[..(k + 2).min(nd)]).copied().collect()
    } else {
        corner.iter().copied().chain(std::iter::repeat_n(true, k + 2)).collect()
    };
    let mut ccs = vec![0i64];
    for &c in &cm {
        ccs.push(ccs[ccs.len() - 1] + c as i64);
    }
    let nt = t.len();
    let bad: Vec<bool> = (0..nt).map(|i| ccs[i + k + 2] - ccs[i] > 0).collect();
    let s0 = if closed {
        // rotate so the scan starts inside a corner: no run straddles the wrap
        bad.iter().position(|&b| b).ok_or_else(|| geometry("index 0 is out of bounds for axis 0 with size 0"))?
    } else {
        0
    };
    let order: Vec<usize> = (0..nt).map(|i| (i + s0) % nt).collect();
    let mut out = Vec::new();
    let mut a = 0;
    while a < nt {
        if bad[order[a]] {
            a += 1;
            continue;
        }
        let mut b = a;
        while b < nt && !bad[order[b]] {
            b += 1;
        }
        if (b - a) as f64 * STEP >= 4.0 {
            let run: Vec<f64> = order[a..b].iter().map(|&i| t[i]).collect();
            out.extend(flips(&run, false, hyst).into_iter().map(|j| (order[a + j] + k / 2) % n));
        }
        a = b;
    }
    Ok(out)
}
