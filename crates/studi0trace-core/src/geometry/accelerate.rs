//! `np.dot` of two float64 vectors, as numpy hands it to Accelerate's `cblas_ddot` on arm64,
//! and `np.convolve(a, ones(l), "same")`, which is a run of those dots.
//!
//! Accelerate does not add left to right. Its order was read off numpy 2.5.3 by experiment: a
//! dot of zeros with one `2^54`, one `-2^54` and one `1` says whether the `1` is added before
//! the two big terms meet, and those answers for every triple give the tree of additions. Then
//! random vectors decided where the multiply-adds are fused. The models below reproduce every
//! one of 9 240 random dots (lengths 1 to 896, both alignments) and 64 733 convolution outputs
//! to the bit; `geometry_helpers.json` holds some of them. Above 896 elements the contiguous
//! kernel changes and this model does not follow it; the scorecard's dots there are the strided
//! ones, which hold to at least 100 000.
//!
//! Nothing here calls libm: `f64::mul_add` is IEEE 754's fusedMultiplyAdd, correctly rounded
//! wherever it runs (an `fmadd` on arm64, the FMA instruction or compiler-builtins' exact
//! software `fma` elsewhere, wasm included), so these sums are the same on every platform; only
//! whether they are Accelerate's is a fact about macOS on arm64.
//!
//! - **x at a stride of two** (`q[:, 0]`, a column of the outline): four running sums, the
//!   `i`-th product fused into sum `i % 4`, then `(s0 + s2) + (s1 + s3)`.
//! - **both contiguous**: under four elements, a plain sequential sum of unfused products.
//!   From four up, pairs of lanes. A first vector is taken apart (two elements; one, into the
//!   first lane, when `y` sits 8 bytes off a 16-byte boundary, as a slice at an odd offset into
//!   a freshly allocated array does); the rest is cut into pairs, the last of which starts a
//!   second accumulator (an odd element left over is its second lane, alone). The pairs between
//!   go four at a time into four accumulators, fused, the first vector's being the third of
//!   them; what is left over goes, fused, alternately into the sums of accumulators 3 + 1 and
//!   2 + 4. Those two, then the two lanes, are added.

/// `np.dot(q[:, 0], y)` where `x(i)` is a column of a C-ordered `(n, 2)` array and `y(i)` is
/// contiguous: Accelerate's strided `ddot`.
pub(super) fn ddot_stride2(n: usize, x: impl Fn(usize) -> f64, y: impl Fn(usize) -> f64) -> f64 {
    let mut acc = [0.0f64; 4];
    for i in 0..n {
        acc[i % 4] = x(i).mul_add(y(i), acc[i % 4]);
    }
    (acc[0] + acc[2]) + (acc[1] + acc[3])
}

type Lanes = [f64; 2];

fn fused(x: Lanes, y: Lanes, acc: Lanes) -> Lanes {
    [x[0].mul_add(y[0], acc[0]), x[1].mul_add(y[1], acc[1])]
}

fn add(a: Lanes, b: Lanes) -> Lanes {
    [a[0] + b[0], a[1] + b[1]]
}

/// `np.dot(x, y)` of two contiguous float64 vectors of equal length (at most 896: see the
/// module docs). `y_aligned`: whether `y` starts on a 16-byte boundary.
pub(super) fn ddot(x: &[f64], y: &[f64], y_aligned: bool) -> f64 {
    let n = x.len().min(y.len());
    if n < 4 {
        let mut s = 0.0;
        for i in 0..n {
            s += x[i] * y[i];
        }
        return s;
    }
    let (mut acc_c, start) = if y_aligned || n == 4 { ([x[0] * y[0], x[1] * y[1]], 2) } else { ([x[0] * y[0], 0.0], 1) };
    let pair = |k: usize| -> (Lanes, Lanes) { ([x[start + 2 * k], x[start + 2 * k + 1]], [y[start + 2 * k], y[start + 2 * k + 1]]) };
    let rest = n - start;
    let pairs = rest / 2;
    let (mut acc_d, middle) = if rest.is_multiple_of(2) {
        let (a, b) = pair(pairs - 1);
        ([a[0] * b[0], a[1] * b[1]], pairs - 1)
    } else {
        ([0.0, x[n - 1] * y[n - 1]], pairs)
    };
    let (mut acc_a, mut acc_b) = ([0.0; 2], [0.0; 2]);
    let blocks = middle / 4;
    for i in 0..blocks {
        let j = 4 * i;
        let (a, b) = pair(j);
        acc_a = fused(a, b, acc_a);
        let (a, b) = pair(j + 1);
        acc_b = fused(a, b, acc_b);
        let (a, b) = pair(j + 2);
        acc_c = fused(a, b, acc_c);
        let (a, b) = pair(j + 3);
        acc_d = fused(a, b, acc_d);
    }
    let (mut g1, mut g2) = (add(acc_c, acc_a), add(acc_b, acc_d));
    for (r, j) in (4 * blocks..middle).enumerate() {
        let (a, b) = pair(j);
        if r % 2 == 0 {
            g1 = fused(a, b, g1);
        } else {
            g2 = fused(a, b, g2);
        }
    }
    let g = add(g1, g2);
    g[0] + g[1]
}

/// `np.convolve(a, np.ones(l), "same")` for `a` at least `l` long: numpy's correlation loop,
/// one `ddot` an output, against a copy of the kernel that starts 16-byte aligned. The first
/// `l / 2` outputs dot the start of `a` with the kernel from an offset that falls by one each
/// time; the last `l - l / 2 - 1` dot the end of `a` with a kernel that shortens.
pub(super) fn convolve_same_ones(a: &[f64], l: usize) -> Vec<f64> {
    let n = a.len();
    debug_assert!(l >= 1 && n >= l);
    let kernel = vec![1.0; l];
    let left = l / 2;
    let right = l - left - 1;
    let mut out = Vec::with_capacity(n);
    for i in 0..left {
        let len = l - left + i;
        let off = left - i;
        out.push(ddot(&a[..len], &kernel[off..], off.is_multiple_of(2)));
    }
    for t in 0..=(n - l) {
        out.push(ddot(&a[t..t + l], &kernel, true));
    }
    for r in 0..right {
        let from = n - l + 1 + r;
        out.push(ddot(&a[from..], &kernel[..l - 1 - r], true));
    }
    out
}
