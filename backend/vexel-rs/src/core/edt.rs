//! Exact Euclidean distance transform (Felzenszwalb & Huttenlocher's lower
//! envelope, run per axis), with the nearest-feature indices scipy returns for
//! `return_indices=True`.
//!
//! Distances are to the nearest **false** pixel, which is what
//! `scipy.ndimage.distance_transform_edt` measures.

use super::grid::{Grid, Mask};
use rayon::prelude::*;

const INF: f64 = 1e20;

/// 1-D squared-distance lower envelope of the parabolas `(q − x)² + f[q]`.
/// Fills `d` with the envelope and `arg` with the index of the winning parabola.
fn dt1d(f: &[f64], d: &mut [f64], arg: &mut [usize], v: &mut [usize], z: &mut [f64]) {
    let n = f.len();
    let mut k: isize = 0;
    v[0] = 0;
    z[0] = -INF;
    z[1] = INF;
    for q in 1..n {
        loop {
            let vk = v[k as usize];
            let s = ((f[q] + (q * q) as f64) - (f[vk] + (vk * vk) as f64)) / (2.0 * q as f64 - 2.0 * vk as f64);
            if s <= z[k as usize] && k > 0 {
                k -= 1;
                continue;
            }
            if s <= z[k as usize] {
                // k == 0: this parabola dominates everything seen so far
                v[0] = q;
                z[0] = -INF;
                z[1] = INF;
            } else {
                k += 1;
                v[k as usize] = q;
                z[k as usize] = s;
                z[k as usize + 1] = INF;
            }
            break;
        }
    }
    let mut k: usize = 0;
    for q in 0..n {
        while z[k + 1] < q as f64 {
            k += 1;
        }
        let dq = q as f64 - v[k] as f64;
        d[q] = dq * dq + f[v[k]];
        arg[q] = v[k];
    }
}

/// Squared distances plus, for every pixel, the (row, col) of the nearest false pixel.
pub fn edt_sq_indices(m: &Mask) -> (Grid<f64>, Grid<u32>, Grid<u32>) {
    let (h, w) = (m.h, m.w);

    // small regions are transformed many times over; the pool round trip
    // dominates below roughly a hundred thousand pixels
    let parallel = h * w >= (1 << 17);
    let col_job = |c: usize| -> (Vec<f64>, Vec<usize>) {
        {
            let f: Vec<f64> = (0..h).map(|r| if m.data[r * w + c] { INF } else { 0.0 }).collect();
            let (mut d, mut a) = (vec![0.0; h], vec![0usize; h]);
            let (mut v, mut z) = (vec![0usize; h], vec![0.0; h + 1]);
            dt1d(&f, &mut d, &mut a, &mut v, &mut z);
            (d, a)
        }
    };
    let cols: Vec<(Vec<f64>, Vec<usize>)> = if parallel {
        (0..w).into_par_iter().map(col_job).collect()
    } else {
        (0..w).map(col_job).collect()
    };

    let mut dy = Grid::<f64>::new(h, w);
    let mut src_r = Grid::<u32>::new(h, w);
    for (c, (d, a)) in cols.iter().enumerate() {
        for r in 0..h {
            dy.data[r * w + c] = d[r];
            src_r.data[r * w + c] = a[r] as u32;
        }
    }

    let row_job = |r: usize| -> (Vec<f64>, Vec<usize>) {
        {
            let f: Vec<f64> = dy.data[r * w..(r + 1) * w].to_vec();
            let (mut d, mut a) = (vec![0.0; w], vec![0usize; w]);
            let (mut v, mut z) = (vec![0usize; w], vec![0.0; w + 1]);
            dt1d(&f, &mut d, &mut a, &mut v, &mut z);
            (d, a)
        }
    };
    let rows: Vec<(Vec<f64>, Vec<usize>)> = if parallel {
        (0..h).into_par_iter().map(row_job).collect()
    } else {
        (0..h).map(row_job).collect()
    };

    let mut dist = Grid::<f64>::new(h, w);
    let mut out_r = Grid::<u32>::new(h, w);
    let mut out_c = Grid::<u32>::new(h, w);
    for (r, (d, a)) in rows.iter().enumerate() {
        for c in 0..w {
            dist.data[r * w + c] = d[c];
            let cc = a[c];
            out_c.data[r * w + c] = cc as u32;
            out_r.data[r * w + c] = src_r.data[r * w + cc];
        }
    }
    (dist, out_r, out_c)
}

/// Euclidean distance to the nearest false pixel.
pub fn edt(m: &Mask) -> Grid<f64> {
    let (sq, _, _) = edt_sq_indices(m);
    Grid { h: sq.h, w: sq.w, data: sq.data.iter().map(|v| v.sqrt()).collect() }
}

/// Distance from each pixel to the nearest **true** pixel of `m` — the Python's
/// `distance_transform_edt(~m)`.
pub fn edt_to_true(m: &Mask) -> Grid<f64> {
    edt(&m.not())
}

/// Maximum distance-to-boundary inside `mask`, computed on the region's own
/// bounding box with a one-pixel border, as `is_thin` and `interior_weights` do.
pub fn cropped_edt(mask: &Mask) -> Option<(Grid<f64>, usize, usize, usize, usize)> {
    let (r0, r1, c0, c1) = mask.bbox()?;
    let crop = mask.crop_pad(r0, r1, c0, c1, 1);
    let d = edt(&crop);
    Some((d, r0, r1, c0, c1))
}
