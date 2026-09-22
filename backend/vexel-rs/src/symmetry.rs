//! Stage 6b: symmetry of the placed boundary. Mirrors `symmetry.py`.

use crate::curves::P;

pub const SYM_MEAN: f64 = 0.10;
pub const SYM_MAX: f64 = 0.30;
pub const SYM_CAP: f64 = 1.0;
pub const MIN_VERTICES: usize = 24;
pub const ORDERS: [usize; 7] = [8, 7, 6, 5, 4, 3, 2];
pub const AXIS_SNAP_DEG: f64 = 1.5;

fn sub(a: P, b: P) -> P {
    [a[0] - b[0], a[1] - b[1]]
}

fn norm(a: P) -> f64 {
    (a[0] * a[0] + a[1] * a[1]).sqrt()
}

fn centroid(poly: &[P]) -> P {
    let n = poly.len() as f64;
    [poly.iter().map(|p| p[0]).sum::<f64>() / n, poly.iter().map(|p| p[1]).sum::<f64>() / n]
}

/// numpy's default (linear) percentile.
fn percentile(sorted: &[f64], q: f64) -> f64 {
    let n = sorted.len();
    if n == 0 {
        return 0.0;
    }
    let pos = (n as f64 - 1.0) * q / 100.0;
    let lo = pos.floor() as usize;
    let hi = pos.ceil() as usize;
    if lo == hi {
        sorted[lo]
    } else {
        sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo as f64)
    }
}

/// Exact nearest-vertex lookup through a uniform grid: the search widens ring
/// by ring until no unsearched cell can hold a closer vertex.
pub(crate) struct Grid {
    cell: f64,
    x0: f64,
    y0: f64,
    nx: i64,
    ny: i64,
    bins: Vec<Vec<usize>>,
    pts: Vec<P>,
}

impl Grid {
    pub(crate) fn new(pts: &[P]) -> Grid {
        let cell = 2.0;
        let (mut x0, mut y0, mut x1, mut y1) = (f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY);
        for p in pts {
            x0 = x0.min(p[0]);
            y0 = y0.min(p[1]);
            x1 = x1.max(p[0]);
            y1 = y1.max(p[1]);
        }
        let nx = (((x1 - x0) / cell).floor() as i64 + 1).max(1);
        let ny = (((y1 - y0) / cell).floor() as i64 + 1).max(1);
        let mut bins = vec![Vec::new(); (nx * ny) as usize];
        for (i, p) in pts.iter().enumerate() {
            let cx = (((p[0] - x0) / cell).floor() as i64).clamp(0, nx - 1);
            let cy = (((p[1] - y0) / cell).floor() as i64).clamp(0, ny - 1);
            bins[(cy * nx + cx) as usize].push(i);
        }
        Grid { cell, x0, y0, nx, ny, bins, pts: pts.to_vec() }
    }

    pub(crate) fn nearest(&self, q: P) -> (f64, usize) {
        let cx = (((q[0] - self.x0) / self.cell).floor() as i64).clamp(0, self.nx - 1);
        let cy = (((q[1] - self.y0) / self.cell).floor() as i64).clamp(0, self.ny - 1);
        let mut best = (f64::INFINITY, 0usize);
        let max_ring = self.nx.max(self.ny);
        for ring in 0..=max_ring {
            // a vertex in a cell `ring` away is at least (ring - 1) cells off
            if ring > 0 && ((ring - 1) as f64) * self.cell > best.0 {
                break;
            }
            for dy in -ring..=ring {
                for dx in -ring..=ring {
                    if dx.abs() != ring && dy.abs() != ring {
                        continue;
                    }
                    let (gx, gy) = (cx + dx, cy + dy);
                    if gx < 0 || gy < 0 || gx >= self.nx || gy >= self.ny {
                        continue;
                    }
                    for &i in &self.bins[(gy * self.nx + gx) as usize] {
                        let d = norm(sub(self.pts[i], q));
                        if d < best.0 {
                            best = (d, i);
                        }
                    }
                }
            }
        }
        best
    }
}

/// (mean, worst, index of nearest vertex) for every image; see the Python `_match`.
fn matching(grid: &Grid, images: &[P]) -> (f64, f64, Vec<usize>) {
    let mut dist = Vec::with_capacity(images.len());
    let mut idx = Vec::with_capacity(images.len());
    for q in images {
        let (d, i) = grid.nearest(*q);
        dist.push(d);
        idx.push(i);
    }
    let mean = dist.iter().sum::<f64>() / dist.len() as f64;
    let max = dist.iter().cloned().fold(0.0f64, f64::max);
    let mut sorted = dist.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let p99 = percentile(&sorted, 99.0);
    let worst = if max <= SYM_CAP { p99 } else { max };
    (mean, worst, idx)
}

fn fits(mean: f64, worst: f64) -> bool {
    mean <= SYM_MEAN && worst <= SYM_MAX
}

pub fn reflect(p: P, c: P, d: P) -> P {
    // r = 2 d dᵀ − I
    let q = sub(p, c);
    let rx = (2.0 * d[0] * d[0] - 1.0) * q[0] + (2.0 * d[0] * d[1]) * q[1];
    let ry = (2.0 * d[0] * d[1]) * q[0] + (2.0 * d[1] * d[1] - 1.0) * q[1];
    [c[0] + rx, c[1] + ry]
}

pub fn rotate(p: P, c: P, angle: f64) -> P {
    let (ca, sa) = (angle.cos(), angle.sin());
    let q = sub(p, c);
    [c[0] + ca * q[0] - sa * q[1], c[1] + sa * q[0] + ca * q[1]]
}

/// Candidate mirror axes as (point on axis, unit direction). See the Python.
pub fn mirror_axes(poly: &[P]) -> Vec<(P, P)> {
    let c = centroid(poly);
    let n = poly.len().max(1) as f64;
    let (mut a, mut b, mut cc) = (0.0, 0.0, 0.0);
    for p in poly {
        let q = sub(*p, c);
        a += q[0] * q[0];
        b += q[0] * q[1];
        cc += q[1] * q[1];
    }
    a /= n;
    b /= n;
    cc /= n;
    let theta = 0.5 * (2.0 * b).atan2(a - cc);
    let d1 = [theta.cos(), theta.sin()];
    let d2 = [-theta.sin(), theta.cos()];
    let mut dirs: Vec<P> = vec![d1, d2, [d1[0] + d2[0], d1[1] + d2[1]], [d1[0] - d2[0], d1[1] - d2[1]]];
    for k in 0..12 {
        let ang = (15.0 * k as f64).to_radians();
        dirs.push([ang.cos(), ang.sin()]);
    }
    let mut out: Vec<(P, P)> = Vec::new();
    let cos2 = 2.0f64.to_radians().cos();
    for d in dirs {
        let len = norm(d);
        if len < 1e-12 {
            continue;
        }
        let mut d = [d[0] / len, d[1] / len];
        if d[0] < 0.0 || (d[0] == 0.0 && d[1] < 0.0) {
            d = [-d[0], -d[1]];
        }
        let ang = d[1].atan2(d[0]).to_degrees();
        if ang.abs().min((180.0 - ang).abs()) <= AXIS_SNAP_DEG {
            d = [1.0, 0.0];
        } else if (ang - 90.0).abs() <= AXIS_SNAP_DEG {
            d = [0.0, 1.0];
        }
        if out.iter().all(|(_c, e)| (d[0] * e[0] + d[1] * e[1]).abs() < cos2) {
            out.push((c, d));
        }
    }
    out
}

/// The ring made exactly mirror-symmetric about `axis`, or None when it is not.
pub fn symmetrize(poly: &[P], axis: (P, P)) -> Option<Vec<P>> {
    let (c, d) = axis;
    let grid = Grid::new(poly);
    let images: Vec<P> = poly.iter().map(|p| reflect(*p, c, d)).collect();
    let (mean, worst, idx) = matching(&grid, &images);
    if !fits(mean, worst) {
        return None;
    }
    Some(
        poly.iter()
            .zip(idx)
            .map(|(p, i)| {
                let m = reflect(poly[i], c, d);
                [0.5 * (p[0] + m[0]), 0.5 * (p[1] + m[1])]
            })
            .collect(),
    )
}

pub fn rotational_order(poly: &[P]) -> usize {
    let c = centroid(poly);
    let grid = Grid::new(poly);
    for &k in ORDERS.iter() {
        let angle = 2.0 * std::f64::consts::PI / k as f64;
        let images: Vec<P> = poly.iter().map(|p| rotate(*p, c, angle)).collect();
        let (mean, worst, _idx) = matching(&grid, &images);
        if fits(mean, worst) {
            return k;
        }
    }
    1
}

pub fn symmetrize_rotational(poly: &[P], k: usize) -> Vec<P> {
    let c = centroid(poly);
    let grid = Grid::new(poly);
    let mut acc: Vec<P> = poly.to_vec();
    for j in 1..k {
        let angle = 2.0 * std::f64::consts::PI * j as f64 / k as f64;
        let images: Vec<P> = poly.iter().map(|p| rotate(*p, c, angle)).collect();
        let (_m, _w, idx) = matching(&grid, &images);
        for (a, &i) in acc.iter_mut().zip(idx.iter()) {
            let back = rotate(poly[i], c, -angle);
            a[0] += back[0];
            a[1] += back[1];
        }
    }
    let kf = k as f64;
    acc.iter().map(|a| [a[0] / kf, a[1] / kf]).collect()
}

/// (the symmetrised ring or None, the mirror axes that fit, best first). See the Python.
pub fn ring_symmetries(poly: &[P]) -> (Option<Vec<P>>, Vec<(P, P)>) {
    if poly.len() < MIN_VERTICES {
        return (None, Vec::new());
    }
    let mut out: Vec<P> = poly.to_vec();
    let mut changed = false;
    let k = rotational_order(&out);
    if k > 1 {
        out = symmetrize_rotational(&out, k);
        changed = true;
    }
    let mut axes: Vec<(f64, (P, P))> = Vec::new();
    for axis in mirror_axes(&out) {
        if let Some(sym) = symmetrize(&out, axis) {
            let grid = Grid::new(&out);
            let images: Vec<P> = out.iter().map(|p| reflect(*p, axis.0, axis.1)).collect();
            let (mean, _w, _i) = matching(&grid, &images);
            axes.push((mean, axis));
            out = sym;
            changed = true;
        }
    }
    // best first by a rule both implementations agree on to the last bit
    let rank = |d: P| -> (u8, f64) {
        let exact = if (d[0] == 1.0 && d[1] == 0.0) || (d[0] == 0.0 && d[1] == 1.0) { 0 } else { 1 };
        (exact, d[1].atan2(d[0]).to_degrees().rem_euclid(180.0))
    };
    axes.sort_by(|a, b| {
        let (ra, rb) = (rank(a.1 .1), rank(b.1 .1));
        ra.0.cmp(&rb.0).then(ra.1.partial_cmp(&rb.1).unwrap())
    });
    (if changed { Some(out) } else { None }, axes.into_iter().map(|(_m, a)| a).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn regular(n_sides: usize, r: f64, step: f64) -> Vec<P> {
        let mut pts = Vec::new();
        let mut seed: u64 = 7;
        let mut noise = || {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            ((seed >> 33) as f64 / (1u64 << 31) as f64 - 0.5) * 0.08
        };
        for k in 0..n_sides {
            let a0 = 2.0 * std::f64::consts::PI * k as f64 / n_sides as f64;
            let a1 = 2.0 * std::f64::consts::PI * (k + 1) as f64 / n_sides as f64;
            let p0 = [128.0 + r * a0.cos(), 128.0 + r * a0.sin()];
            let p1 = [128.0 + r * a1.cos(), 128.0 + r * a1.sin()];
            let m = (norm(sub(p1, p0)) / step) as usize;
            for i in 0..m {
                let f = i as f64 / m as f64;
                pts.push([p0[0] + f * (p1[0] - p0[0]) + noise(), p0[1] + f * (p1[1] - p0[1]) + noise()]);
            }
        }
        pts
    }

    #[test]
    fn a_hexagon_has_order_six_and_a_square_four_mirror_axes() {
        let hexagon = regular(6, 80.0, 0.7);
        assert_eq!(rotational_order(&hexagon), 6);
        let out = symmetrize_rotational(&hexagon, 6);
        let c = centroid(&out);
        let grid = Grid::new(&out);
        let turned: Vec<P> = out.iter().map(|p| rotate(*p, c, std::f64::consts::PI / 3.0)).collect();
        let (_m, worst, _i) = matching(&grid, &turned);
        assert!(worst < 0.05, "{worst}");
        let square = regular(4, 80.0, 0.7);
        let hits = mirror_axes(&square).into_iter().filter(|ax| symmetrize(&square, *ax).is_some()).count();
        assert!(hits >= 4, "{hits}");
        assert_eq!(rotational_order(&regular(5, 80.0, 0.7)), 5);
    }

    #[test]
    fn the_grid_finds_the_true_nearest_vertex() {
        let pts = regular(3, 60.0, 0.9);
        let grid = Grid::new(&pts);
        for q in [[100.0, 100.0], [128.0, 68.0], [190.0, 160.0], [0.0, 0.0]] {
            let (d, i) = grid.nearest(q);
            let brute = pts.iter().map(|p| norm(sub(*p, q))).fold(f64::INFINITY, f64::min);
            assert!((d - brute).abs() < 1e-12 && (norm(sub(pts[i], q)) - brute).abs() < 1e-12);
        }
    }
}
