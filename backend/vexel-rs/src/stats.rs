//! Region sufficient statistics and closed-form colour-model fits.
//!
//! A region is summarised by position moments up to 4th order and
//! colour-weighted moments up to 2nd order. Adding two regions' vectors gives
//! the union's statistics, and the least-squares error of a solid / planar /
//! quadratic colour model follows from the normal equations — so region merging
//! never revisits pixels.

use crate::core::grid::{Grid, Image};
use crate::core::labels::Labels;
use crate::core::linalg::{solve, Mat};

pub const MOMENT_ORDER: [(u32, u32); 15] = [
    (0, 0), (1, 0), (0, 1), (2, 0), (1, 1), (0, 2),
    (3, 0), (2, 1), (1, 2), (0, 3),
    (4, 0), (3, 1), (2, 2), (1, 3), (0, 4),
];
pub const N_MOMENTS: usize = 15;
pub const MODEL_K: [f64; 3] = [0.0, 2.0, 5.0];

fn moment_index(a: u32, b: u32) -> usize {
    MOMENT_ORDER.iter().position(|m| *m == (a, b)).expect("moment in basis")
}

/// numpy's `npy_pow` for the small integer exponents the moment basis uses:
/// it special-cases 0, 1 and 2 and calls `pow` for the rest, so a cube is
/// `pow(x, 3.0)` and not `x·x·x`. The two differ in the last bit, and the
/// merge threshold is a comparison.
#[inline]
fn npy_pow(x: f64, e: u32) -> f64 {
    match e {
        0 => 1.0,
        1 => x,
        2 => x * x,
        _ => x.powf(e as f64),
    }
}

/// `A[i][j] = Σ φ_i φ_j` as an index into a moment row.
fn normal_idx() -> [[usize; 6]; 6] {
    let mut out = [[0usize; 6]; 6];
    for i in 0..6 {
        for j in 0..6 {
            let (ai, bi) = MOMENT_ORDER[i];
            let (aj, bj) = MOMENT_ORDER[j];
            out[i][j] = moment_index(ai + aj, bi + bj);
        }
    }
    out
}

/// Pixel-centre coordinates mapped to [-1, 1].
pub fn normalised_coords(height: usize, width: usize) -> (Grid<f64>, Grid<f64>) {
    let cx = width as f64 / 2.0;
    let cy = height as f64 / 2.0;
    let scale = width.max(height) as f64 / 2.0;
    let mut xn = Grid::<f64>::new(height, width);
    let mut yn = Grid::<f64>::new(height, width);
    for r in 0..height {
        for c in 0..width {
            xn.data[r * width + c] = ((c as f64 + 0.5) - cx) / scale;
            yn.data[r * width + c] = ((r as f64 + 0.5) - cy) / scale;
        }
    }
    (xn, yn)
}

/// One row per label 0..=K: 15 moments, then `C × 6` colour-weighted moments
/// (channel-major), then `C` colour squares.
pub struct Stats {
    pub n_ch: usize,
    pub row_len: usize,
    pub rows: Vec<f64>,
}

impl Stats {
    pub fn k(&self) -> usize {
        self.rows.len() / self.row_len
    }

    pub fn row(&self, i: usize) -> &[f64] {
        &self.rows[i * self.row_len..(i + 1) * self.row_len]
    }

    pub fn add_into(&mut self, dst: usize, src: usize) {
        for i in 0..self.row_len {
            self.rows[dst * self.row_len + i] += self.rows[src * self.row_len + i];
        }
    }

    pub fn union(&self, a: usize, b: usize) -> Vec<f64> {
        (0..self.row_len).map(|i| self.row(a)[i] + self.row(b)[i]).collect()
    }
}

pub fn accumulate(labels: &Labels, xn: &Grid<f64>, yn: &Grid<f64>, colours: &Image) -> Stats {
    let n_ch = colours.c;
    let row_len = N_MOMENTS + 6 * n_ch + n_ch;
    let k = labels.data.iter().copied().max().unwrap_or(0).max(0) as usize + 1;
    let mut rows = vec![0.0f64; k * row_len];
    for i in 0..labels.len() {
        let lab = labels.data[i];
        if lab < 0 {
            continue;
        }
        let base = lab as usize * row_len;
        let (x, y) = (xn.data[i], yn.data[i]);
        let mut moments = [0.0f64; N_MOMENTS];
        for (m, (a, b)) in MOMENT_ORDER.iter().enumerate() {
            moments[m] = npy_pow(x, *a) * npy_pow(y, *b);
        }
        for m in 0..N_MOMENTS {
            rows[base + m] += moments[m];
        }
        let col = colours.px(i);
        for (ch, cv) in col.iter().enumerate() {
            let off = base + N_MOMENTS + ch * 6;
            for p in 0..6 {
                rows[off + p] += cv * moments[p];
            }
            rows[base + N_MOMENTS + 6 * n_ch + ch] += cv * cv;
        }
    }
    Stats { n_ch, row_len, rows }
}

fn cached_normal_idx() -> &'static [[usize; 6]; 6] {
    static IDX: std::sync::OnceLock<[[usize; 6]; 6]> = std::sync::OnceLock::new();
    IDX.get_or_init(normal_idx)
}

fn sse_for_order(row: &[f64], n_ch: usize, order: usize, nidx: &[[usize; 6]; 6]) -> f64 {
    let n = row[0];
    let cross = |ch: usize, p: usize| row[N_MOMENTS + ch * 6 + p];
    let sq = |ch: usize| row[N_MOMENTS + 6 * n_ch + ch];
    if order == 1 {
        let nn = n.max(1e-12);
        let mut s = 0.0;
        for ch in 0..n_ch {
            let explained = if n > 0.0 { cross(ch, 0) * cross(ch, 0) / nn } else { 0.0 };
            s += sq(ch) - explained;
        }
        return s.max(0.0);
    }
    let mut a = Mat::zeros(order, order);
    for i in 0..order {
        for j in 0..order {
            a.set(i, j, row[nidx[i][j]]);
        }
    }
    for i in 0..order {
        let v = a.at(i, i) + 1e-9;
        a.set(i, i, v);
    }
    let mut b = Mat::zeros(order, n_ch);
    for p in 0..order {
        for ch in 0..n_ch {
            b.set(p, ch, cross(ch, p));
        }
    }
    let coef = match solve(&a, &b) {
        Some(c) => c,
        None => return (0..n_ch).map(sq).sum::<f64>().max(0.0),
    };
    let mut explained = 0.0;
    for p in 0..order {
        for ch in 0..n_ch {
            explained += coef.at(p, ch) * b.at(p, ch);
        }
    }
    ((0..n_ch).map(sq).sum::<f64>() - explained).max(0.0)
}

/// SSE for the solid, planar and quadratic models. `+inf` where the region is
/// too small to support the model.
pub fn model_sse(row: &[f64], n_ch: usize) -> [f64; 3] {
    let nidx = cached_normal_idx();
    let n = row[0];
    [
        sse_for_order(row, n_ch, 1, nidx),
        if n >= 4.0 { sse_for_order(row, n_ch, 3, nidx) } else { f64::INFINITY },
        if n >= 10.0 { sse_for_order(row, n_ch, 6, nidx) } else { f64::INFINITY },
    ]
}

/// `min` over models of `SSE + k·mu·n`. Returns (cost, model index).
pub fn region_cost(row: &[f64], n_ch: usize, mu: f64, allow_gradients: bool) -> (f64, usize) {
    let sse = model_sse(row, n_ch);
    let n = row[0];
    let mut best = (f64::INFINITY, 0usize);
    for m in 0..3 {
        if m > 0 && !allow_gradients {
            continue;
        }
        let p = sse[m] + MODEL_K[m] * mu * n;
        if p < best.0 {
            best = (p, m);
        }
    }
    best
}

/// The effective colour distance implied by merging: the mean colour
/// difference for two flat regions, ≈ 0 for two pieces of one gradient.
pub fn merge_distance(cost_union: f64, cost_a: f64, cost_b: f64, n_a: f64, n_b: f64) -> f64 {
    let increase = (cost_union - cost_a - cost_b).max(0.0);
    (increase * (n_a + n_b) / (n_a * n_b).max(1e-12)).sqrt()
}
