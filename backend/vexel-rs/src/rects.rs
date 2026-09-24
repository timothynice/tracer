//! Stage 7a: rounded rectangles, drawn the way a designer draws them.
//!
//! Mirrors `engines/vexel/rects.py` function for function: a model of an
//! axis-aligned rectangle with a radius per corner, fitted to a ring's placed
//! vertices; the model's outline as lines and quarter circles, walked from any
//! point on it to any other; the 1-D clustering that decides which radii, sizes
//! and edges are one; fillet geometry for a rounded corner between two lines;
//! and the blur read across the model's sides (`edge_sigma`), taken out of each
//! radius (`deblur`). The graph side is `topology::rectify`.
//!
//! The arithmetic follows the Python's to the bit where a decision hangs off
//! it: CPython's built-in `sum()` over floats is Neumaier's compensated sum
//! (`py_sum`), numpy's `sum`/`mean` over an array is its pairwise sum
//! (`np_sum`), `np.percentile` interpolates the way `np_percentile` does, and
//! the edge profile is divided in float32, as numpy divides the float32 image.
//! A `@` between two float64 vectors is BLAS's dot, whose summation order is
//! the library's; those are plain sums here and may differ in the last bit.

use crate::core::grid::Image;
use crate::curves::{line_runs, Segment, P};

pub const CORNERS: [&str; 4] = ["LT", "RT", "RB", "LB"]; // clockwise on screen, from the top left
pub const RECT_MIN_SIDE: f64 = 4.0;
pub const RECT_RMS: f64 = 0.15;
pub const SHARP_R: f64 = 0.6;
/// What a sharp corner can read as: the lattice's half-pixel chamfer. A fillet
/// that reads no more than this is left as it was fitted. See the Python.
pub const CHAMFER_R: f64 = 1.5;
/// A blurred corner reads √(r² + (1.86σ)² + READ_C). See the Python.
pub const BLUR_K: f64 = 1.86 * 1.86;
pub const READ_C: f64 = 0.58;
pub const SHARP_SHAPE_R: f64 = 1.1;
pub const MERGE_LEVEL: f64 = 0.3;
pub const CORNER_READ: i64 = 2;
pub const RADIUS_ITER: usize = 60;
pub const MOVE_SHARE: f64 = 0.5;
pub const RADIUS_GAIN: f64 = std::f64::consts::SQRT_2 - 1.0;
pub const RADIUS_NOISE: f64 = 0.4;
pub const NODE_ON: f64 = 0.2;
pub const EDGE_REACH: f64 = 2.0;
pub const EDGE_PLATEAU: (f64, f64) = (2.0, 3.5);
pub const EDGE_END: f64 = 2.0;
pub const EDGE_SIGMA_MAX: f64 = 2.0;

// --- the Python's arithmetic -----------------------------------------------------

/// CPython 3.12's built-in `sum()` over floats: Neumaier's compensated sum.
pub fn py_sum<I: IntoIterator<Item = f64>>(items: I) -> f64 {
    let mut f = 0.0f64;
    let mut c = 0.0f64;
    for x in items {
        let t = f + x;
        if f.abs() >= x.abs() {
            c += (f - t) + x;
        } else {
            c += (x - t) + f;
        }
        f = t;
    }
    if c != 0.0 && c.is_finite() {
        f += c;
    }
    f
}

/// numpy's pairwise sum of a contiguous float64 array (`np.sum`, `np.mean`).
pub fn np_sum(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        let mut res = 0.0;
        for x in a {
            res += *x;
        }
        res
    } else if n <= 128 {
        let mut r = [0.0f64; 8];
        r.copy_from_slice(&a[..8]);
        let mut i = 8;
        while i < n - (n % 8) {
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
        np_sum(&a[..n2]) + np_sum(&a[n2..])
    }
}

/// `np.percentile(v, q)`, the default linear method.
pub fn np_percentile(v: &[f64], q: f64) -> f64 {
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.total_cmp(b));
    let n = s.len();
    let pos = (n as f64 - 1.0) * (q / 100.0);
    if pos >= n as f64 - 1.0 {
        // numpy interpolates the last value with itself: an infinity comes back NaN
        let a = s[n - 1];
        return a + (a - a);
    }
    let lo = pos.floor();
    let (a, b) = (s[lo as usize], s[lo as usize + 1]);
    let g = pos - lo;
    let diff = b - a;
    if g >= 0.5 {
        b - diff * (1.0 - g)
    } else {
        a + diff * g
    }
}

/// `np.median(v)`.
pub fn np_median(v: &[f64]) -> f64 {
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.total_cmp(b));
    let n = s.len();
    if n % 2 == 1 {
        s[n / 2]
    } else {
        (s[n / 2 - 1] + s[n / 2]) / 2.0
    }
}

/// Python's float `%`: the sign of the divisor, and +0.0 for an exact zero.
pub fn py_mod(x: f64, y: f64) -> f64 {
    let mut m = x % y;
    if m != 0.0 {
        if (y < 0.0) != (m < 0.0) {
            m += y;
        }
    } else {
        m = 0.0f64.copysign(y);
    }
    m
}

/// `np.linspace(0, 1, k)`: k·(1/(k−1)), the last one exactly 1.
pub fn linspace01(k: usize) -> Vec<f64> {
    if k == 1 {
        return vec![0.0];
    }
    let step = 1.0 / (k - 1) as f64;
    let mut t: Vec<f64> = (0..k).map(|i| i as f64 * step + 0.0).collect();
    t[k - 1] = 1.0;
    t
}

#[inline]
fn norm(v: P) -> f64 {
    (v[0] * v[0] + v[1] * v[1]).sqrt()
}

#[inline]
fn dot(a: P, b: P) -> f64 {
    a[0] * b[0] + a[1] * b[1]
}

/// Golden-section minimisation over [lo, hi], RADIUS_ITER steps, as the Python.
fn golden(mut lo: f64, mut hi: f64, mut cost: impl FnMut(f64) -> f64) -> f64 {
    let phi = (5.0f64.sqrt() - 1.0) / 2.0;
    let mut a = hi - phi * (hi - lo);
    let mut b = lo + phi * (hi - lo);
    let mut fa = cost(a);
    let mut fb = cost(b);
    for _ in 0..RADIUS_ITER {
        if fa <= fb {
            hi = b;
            b = a;
            fb = fa;
            a = hi - phi * (hi - lo);
            fa = cost(a);
        } else {
            lo = a;
            a = b;
            fa = fb;
            b = lo + phi * (hi - lo);
            fb = cost(b);
        }
    }
    0.5 * (lo + hi)
}

// --- the model -------------------------------------------------------------------

/// The radius a corner read as `r` has under blur `sigma` (see BLUR_K).
pub fn deblur(r: f64, sigma: f64) -> f64 {
    let x = r * r - BLUR_K * sigma * sigma - READ_C;
    (if x > 0.0 { x } else { 0.0 }).sqrt()
}

/// The radius the placement reads for a corner of radius `r` under blur `sigma`.
pub fn reblur(r: f64, sigma: f64) -> f64 {
    (r * r + BLUR_K * sigma * sigma + READ_C).sqrt()
}

/// The model as the placement would read it: each rounded corner reblurred.
pub fn apparent(m: &Model, sigma: f64) -> Model {
    let mut out = m.clone();
    let cap = 0.5 * out.w().min(out.h());
    for k in 0..4 {
        let r = if m.r[k] > 0.0 { reblur(m.r[k], sigma) } else { 0.0 };
        out.r[k] = if cap < r { cap } else { r };
    }
    out
}

/// How far a corner's radius may move to join its group.
pub fn radius_move(tol: f64) -> f64 {
    let a = MOVE_SHARE * tol / RADIUS_GAIN;
    if RADIUS_NOISE > a {
        RADIUS_NOISE
    } else {
        a
    }
}

#[derive(Clone, Debug)]
pub struct Model {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
    /// Per corner, in CORNERS order.
    pub r: [f64; 4],
    /// Trusted vertices behind each corner's radius (0 = not read).
    pub votes: [f64; 4],
    /// Per corner, the ring's vertex indices it is read from.
    pub gaps: [Vec<usize>; 4],
}

impl Model {
    pub fn new(x0: f64, y0: f64, x1: f64, y1: f64, r: [f64; 4]) -> Model {
        Model { x0, y0, x1, y1, r, votes: [0.0; 4], gaps: Default::default() }
    }

    pub fn w(&self) -> f64 {
        self.x1 - self.x0
    }

    pub fn h(&self) -> f64 {
        self.y1 - self.y0
    }
}

/// Distance from a point at inside distances (u, v) from a corner's two sides
/// to the corner rounded with radius r. See the Python `_corner_dist`.
#[inline]
pub fn corner_dist(u: f64, v: f64, r: f64) -> f64 {
    if u < r && v < r {
        ((u - r).hypot(v - r) - r).abs()
    } else {
        let a = if u >= r { v.abs() } else { f64::INFINITY };
        let b = if v >= r { u.abs() } else { f64::INFINITY };
        if b < a {
            b
        } else {
            a
        }
    }
}

/// The least-squares corner radius in [0, r_max], by golden section.
fn fit_radius(u: &[f64], v: &[f64], r_max: f64) -> f64 {
    golden(0.0, r_max, |r| {
        let mut s = 0.0;
        for k in 0..u.len() {
            let e = corner_dist(u[k], v[k], r);
            s += e * e;
        }
        s
    })
}

/// (corner x, corner y, inward x sign, inward y sign) of corner k.
pub fn corner_frame(m: &Model, k: usize) -> (f64, f64, f64, f64) {
    let name = CORNERS[k].as_bytes();
    let (cx, sx) = if name[0] == b'L' { (m.x0, 1.0) } else { (m.x1, -1.0) };
    let (cy, sy) = if name[1] == b'T' { (m.y0, 1.0) } else { (m.y1, -1.0) };
    (cx, cy, sx, sy)
}

/// Distance from each point to the model's outline.
pub fn outline_distance(pts: &[P], m: &Model) -> Vec<f64> {
    let mut best = vec![f64::INFINITY; pts.len()];
    let (half_w, half_h) = (0.5 * m.w(), 0.5 * m.h());
    for k in 0..4 {
        let (cx, cy, sx, sy) = corner_frame(m, k);
        for (b, p) in best.iter_mut().zip(pts) {
            let u = sx * (p[0] - cx);
            let v = sy * (p[1] - cy);
            if u <= half_w + 1e-9 && v <= half_h + 1e-9 {
                let d = corner_dist(u, v, m.r[k]);
                if d < *b {
                    *b = d;
                }
            }
        }
    }
    best
}

/// The four sides of the axis-aligned rectangle through a closed ring, or
/// None. See the Python `fit_sides`.
pub fn fit_sides(poly: &[P], snap_axis_deg: f64) -> Option<Model> {
    let n = poly.len();
    if n < 16 {
        return None;
    }
    // start at the vertex farthest from the centroid (numpy's mean over axis 0
    // is a running sum)
    let (mut sx, mut sy) = (0.0f64, 0.0f64);
    for p in poly {
        sx += p[0];
        sy += p[1];
    }
    let mean = [sx / n as f64, sy / n as f64];
    let mut start = 0usize;
    let mut far = f64::NEG_INFINITY;
    for (k, p) in poly.iter().enumerate() {
        let d = norm([p[0] - mean[0], p[1] - mean[1]]);
        if d > far {
            far = d;
            start = k;
        }
    }
    let order: Vec<usize> = (start..n).chain(0..start).collect();
    let mut rolled: Vec<P> = order.iter().map(|&k| poly[k]).collect();
    rolled.push(rolled[0]);
    let runs = line_runs(&rolled);
    // (i, j, axis, level): axis 0 = horizontal
    let mut sides: Vec<(i64, i64, usize, f64)> = Vec::new();
    for run in &runs {
        let ang = py_mod(run.d[1].atan2(run.d[0]).to_degrees(), 180.0);
        let (axis, level) = if ang.min(180.0 - ang) <= snap_axis_deg {
            (0usize, run.c[1])
        } else if (ang - 90.0).abs() <= snap_axis_deg {
            (1usize, run.c[0])
        } else {
            return None;
        };
        let (i, j) = (run.i as i64, run.j as i64);
        if let Some(last) = sides.last_mut() {
            if last.2 == axis && (last.3 - level).abs() <= MERGE_LEVEL {
                // a node on a side can split its run in two
                let (pi, pj, _pa, plevel) = *last;
                let (wa, wb) = (pj - pi, j - i);
                *last = (pi, j, axis, (plevel * wa as f64 + level * wb as f64) / (wa + wb).max(1) as f64);
                continue;
            }
        }
        sides.push((i, j, axis, level));
    }
    if sides.len() == 5 && sides[0].2 == sides[4].2 && (sides[0].3 - sides[4].3).abs() <= MERGE_LEVEL {
        // a side cut by the loop's start: its two runs are one
        let (i, _j, axis, level) = sides.pop().unwrap();
        sides[0] = (i - n as i64, sides[0].1, axis, 0.5 * (level + sides[0].3));
    }
    if sides.len() != 4 || (0..4).any(|k| sides[k].2 == sides[(k + 1) % 4].2) {
        return None;
    }
    let mut xs: Vec<f64> = sides.iter().filter(|s| s.2 == 1).map(|s| s.3).collect();
    let mut ys: Vec<f64> = sides.iter().filter(|s| s.2 == 0).map(|s| s.3).collect();
    xs.sort_by(|a, b| a.total_cmp(b));
    ys.sort_by(|a, b| a.total_cmp(b));
    let mut m = Model::new(xs[0], ys[0], xs[1], ys[1], [0.0; 4]);
    if m.w() < RECT_MIN_SIDE || m.h() < RECT_MIN_SIDE {
        return None;
    }
    let nn = n as i64;
    for k in 0..4 {
        let (a, b) = (sides[k], sides[(k + 1) % 4]);
        let lo = a.1 - CORNER_READ;
        let mut hi = b.0 + CORNER_READ;
        if hi < lo {
            hi += nn;
        }
        let x_side = if a.2 == 1 { a.3 } else { b.3 };
        let y_side = if a.2 == 0 { a.3 } else { b.3 };
        let left = (x_side - m.x0).abs() <= (x_side - m.x1).abs();
        let top = (y_side - m.y0).abs() <= (y_side - m.y1).abs();
        let c = match (left, top) {
            (true, true) => 0,
            (false, true) => 1,
            (false, false) => 2,
            (true, false) => 3,
        };
        m.gaps[c] = (lo..=hi).map(|t| order[t.rem_euclid(nn) as usize]).collect();
    }
    Some(m)
}

/// Each corner's radius: the least-squares radius of the circle tangent to
/// both sides through the corner's trusted vertices. See the Python.
pub fn fit_radii(m: &mut Model, poly: &[P], trusted: &[bool]) {
    let r_max = 0.5 * m.w().min(m.h());
    for c in 0..4 {
        m.r[c] = 0.0;
        m.votes[c] = 0.0;
        let gap: Vec<P> = m.gaps[c].iter().filter(|&&k| trusted[k]).map(|&k| poly[k]).collect();
        if gap.is_empty() {
            continue;
        }
        let (cx, cy, sx, sy) = corner_frame(m, c);
        let u: Vec<f64> = gap.iter().map(|p| sx * (p[0] - cx)).collect();
        let v: Vec<f64> = gap.iter().map(|p| sy * (p[1] - cy)).collect();
        let r = fit_radius(&u, &v, r_max);
        m.r[c] = if r < SHARP_R { 0.0 } else { r };
        m.votes[c] = u.iter().zip(&v).filter(|(a, b)| **a < r && **b < r).count() as f64;
    }
}

/// The trusted vertices must lie on the outline: the 95th percentile within
/// `tol`, the worst within `worst` (2·tol by default), the RMS within RECT_RMS.
pub fn holds(m: &Model, poly: &[P], trusted: &[bool], tol: f64, worst: Option<f64>) -> bool {
    let pts: Vec<P> = poly.iter().zip(trusted).filter(|(_, t)| **t).map(|(p, _)| *p).collect();
    let dist = outline_distance(&pts, m);
    if dist.is_empty() {
        return false;
    }
    let worst = worst.unwrap_or(2.0 * tol);
    let max = dist.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let sq: Vec<f64> = dist.iter().map(|d| d * d).collect();
    np_percentile(&dist, 95.0) <= tol && max <= worst && (np_sum(&sq) / sq.len() as f64).sqrt() <= RECT_RMS
}

/// `fit_sides`, then `fit_radii`, kept when it `holds` the trusted vertices.
pub fn fit_model(poly: &[P], trusted: &[bool], snap_axis_deg: f64, tol: f64) -> Option<Model> {
    let mut m = fit_sides(poly, snap_axis_deg)?;
    fit_radii(&mut m, poly, trusted);
    if holds(&m, poly, trusted, tol, None) {
        Some(m)
    } else {
        None
    }
}

// --- the outline as segments -------------------------------------------------------

#[derive(Clone, Copy, Debug)]
struct Piece {
    arc: bool,
    a: P,
    b: P,
    c: P,
    r: f64,
}

/// The outline clockwise on screen from the top side's left end; empty pieces
/// are left out. See the Python `_pieces`.
fn pieces(m: &Model) -> Vec<Piece> {
    let [rlt, rrt, rrb, rlb] = m.r;
    let (x0, y0, x1, y1) = (m.x0, m.y0, m.x1, m.y1);
    let pts: [P; 8] = [
        [x0 + rlt, y0],
        [x1 - rrt, y0],
        [x1, y0 + rrt],
        [x1, y1 - rrb],
        [x1 - rrb, y1],
        [x0 + rlb, y1],
        [x0, y1 - rlb],
        [x0, y0 + rlt],
    ];
    let centres: [P; 4] = [[x1 - rrt, y0 + rrt], [x1 - rrb, y1 - rrb], [x0 + rlb, y1 - rlb], [x0 + rlt, y0 + rlt]];
    let radii = [rrt, rrb, rlb, rlt];
    let mut out = Vec::new();
    for k in 0..4 {
        let (a, b) = (pts[2 * k], pts[2 * k + 1]);
        if norm([b[0] - a[0], b[1] - a[1]]) > 1e-9 {
            out.push(Piece { arc: false, a, b, c: [0.0, 0.0], r: 0.0 });
        }
        let (c, d) = (pts[2 * k + 1], pts[(2 * k + 2) % 8]);
        if radii[k] > 0.0 {
            out.push(Piece { arc: true, a: c, b: d, c: centres[k], r: radii[k] });
        }
    }
    out
}

fn piece_length(p: &Piece) -> f64 {
    if p.arc {
        0.5 * std::f64::consts::PI * p.r
    } else {
        norm([p.b[0] - p.a[0], p.b[1] - p.a[1]])
    }
}

fn piece_point(p: &Piece, t: f64) -> P {
    if !p.arc {
        return [p.a[0] + (p.b[0] - p.a[0]) * t, p.a[1] + (p.b[1] - p.a[1]) * t];
    }
    let a0 = (p.a[1] - p.c[1]).atan2(p.a[0] - p.c[0]);
    let ang = a0 + 0.5 * std::f64::consts::PI * t; // clockwise on screen: the angle grows
    [p.c[0] + p.r * ang.cos(), p.c[1] + p.r * ang.sin()]
}

/// (t in [0, 1], distance) of the nearest point of the piece to q.
fn piece_project(p: &Piece, q: P) -> (f64, f64) {
    if !p.arc {
        let d = [p.b[0] - p.a[0], p.b[1] - p.a[1]];
        let dd = dot(d, d);
        let t = (dot([q[0] - p.a[0], q[1] - p.a[1]], d) / if dd > 1e-18 { dd } else { 1e-18 }).clamp(0.0, 1.0);
        return (t, norm([p.a[0] + d[0] * t - q[0], p.a[1] + d[1] * t - q[1]]));
    }
    let a0 = (p.a[1] - p.c[1]).atan2(p.a[0] - p.c[0]);
    let ang = (q[1] - p.c[1]).atan2(q[0] - p.c[0]);
    let mut t = py_mod(ang - a0, 2.0 * std::f64::consts::PI) / (0.5 * std::f64::consts::PI);
    if t > 1.0 {
        // outside the quarter: the nearer end
        t = if t < 2.5 { 1.0 } else { 0.0 };
    }
    let r = piece_point(p, t);
    (t, norm([r[0] - q[0], r[1] - q[1]]))
}

pub fn perimeter(m: &Model) -> f64 {
    py_sum(pieces(m).iter().map(piece_length))
}

/// (arc-length position clockwise from the top side's left end, distance) of
/// the nearest outline point to p. Ties go to the earlier piece.
pub fn project(m: &Model, p: P) -> (f64, f64) {
    let mut s = 0.0;
    let mut best: Option<(f64, f64)> = None;
    for piece in pieces(m) {
        let length = piece_length(&piece);
        let (t, d) = piece_project(&piece, p);
        if best.is_none_or(|b| d < b.1 - 1e-12) {
            best = Some((s + t * length, d));
        }
        s += length;
    }
    best.expect("a model has an outline")
}

pub fn point_at(m: &Model, s: f64) -> P {
    let ps = pieces(m);
    let total = py_sum(ps.iter().map(piece_length));
    let mut s = py_mod(s, total);
    for piece in &ps {
        let length = piece_length(piece);
        if s <= length + 1e-12 {
            let t = s / if length > 1e-18 { length } else { 1e-18 };
            return piece_point(piece, if t < 1.0 { t } else { 1.0 });
        }
        s -= length;
    }
    piece_point(ps.last().unwrap(), 1.0)
}

/// The outline clockwise from position s0 to s1 (the whole loop when `whole`),
/// as lines and quarter-circle (or shorter) arcs.
pub fn subpath(m: &Model, s0: f64, s1: f64, whole: bool) -> Vec<Segment> {
    let ps = pieces(m);
    let total = py_sum(ps.iter().map(piece_length));
    let s0 = py_mod(s0, total);
    let span = if whole { total } else { py_mod(s1 - s0, total) };
    let mut out = Vec::new();
    let mut s = 0.0;
    // walk the pieces twice round, so a span that wraps is one pass
    for _lap in 0..2 {
        for piece in &ps {
            let length = piece_length(piece);
            let (a, b) = (s, s + length);
            s = b;
            let lo = if s0 > a { s0 } else { a };
            let hi = if s0 + span < b { s0 + span } else { b };
            if hi - lo <= 1e-9 {
                continue;
            }
            let p = piece_point(piece, (lo - a) / length);
            let q = piece_point(piece, (hi - a) / length);
            if piece.arc {
                out.push(Segment::Arc { p0: p, p1: q, r: piece.r, large: false, sweep: true });
            } else {
                out.push(Segment::Line { p0: p, p1: q });
            }
        }
    }
    out
}

// --- clustering ------------------------------------------------------------------

/// Groups of values that are one value, and that value. See the Python
/// `cluster_1d`: ties in the sort are broken by index, and a group is split
/// at its widest gap (the first of equal ones).
pub fn cluster_1d(values: &[f64], weights: &[f64], mv: f64) -> Vec<(f64, Vec<usize>)> {
    let mut order: Vec<usize> = (0..values.len()).collect();
    order.sort_by(|&a, &b| values[a].partial_cmp(&values[b]).unwrap().then(a.cmp(&b)));
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for i in order {
        if let Some(g) = groups.last_mut() {
            if values[i] - values[*g.last().unwrap()] <= 2.0 * mv {
                g.push(i);
                continue;
            }
        }
        groups.push(vec![i]);
    }
    fn settle(members: &[usize], values: &[f64], weights: &[f64], mv: f64, out: &mut Vec<(f64, Vec<usize>)>) {
        let w = py_sum(members.iter().map(|&i| weights[i]));
        let mean = if w > 0.0 {
            py_sum(members.iter().map(|&i| values[i] * weights[i])) / w
        } else {
            py_sum(members.iter().map(|&i| values[i])) / members.len() as f64
        };
        if members.len() == 1 || members.iter().all(|&i| (values[i] - mean).abs() <= mv) {
            out.push((mean, members.to_vec()));
            return;
        }
        let mut cut = 0usize;
        for k in 1..members.len() - 1 {
            if values[members[k + 1]] - values[members[k]] > values[members[cut + 1]] - values[members[cut]] {
                cut = k;
            }
        }
        settle(&members[..cut + 1], values, weights, mv, out);
        settle(&members[cut + 1..], values, weights, mv, out);
    }
    let mut out = Vec::new();
    for g in &groups {
        settle(g, values, weights, mv, &mut out);
    }
    out
}

// --- fillets: a rounded corner between two lines, anywhere in the graph --------

/// (half the interior angle, unit bisector into the corner) of the corner
/// between a line arriving along `da` and one leaving along `db`.
pub fn fillet_frame(da: P, db: P) -> (f64, P) {
    let turn = dot(da, db).clamp(-1.0, 1.0).acos();
    let bis = [db[0] - da[0], db[1] - da[1]];
    let n = norm(bis);
    let n = if n > 1e-12 { n } else { 1e-12 };
    (0.5 * (std::f64::consts::PI - turn), [bis[0] / n, bis[1] / n])
}

/// Distance from points to the corner at x rounded with radius r.
pub fn fillet_dist(pts: &[P], x: P, da: P, db: P, r: f64) -> Vec<f64> {
    let (half, bis) = fillet_frame(da, db);
    let reach = r / half.tan();
    let c = [x[0] + bis[0] * (r / half.sin()), x[1] + bis[1] * (r / half.sin())];
    pts.iter()
        .map(|p| {
            let rel = [p[0] - x[0], p[1] - x[1]];
            let sa = dot(rel, da);
            let sb = dot(rel, db);
            let na = dot(rel, [-da[1], da[0]]).abs();
            let nb = dot(rel, [-db[1], db[0]]).abs();
            let on_a = sa <= -reach;
            let on_b = sb >= reach;
            let arc = ((p[0] - c[0]).hypot(p[1] - c[1]) - r).abs();
            let mut d = if on_a || on_b { f64::INFINITY } else { arc };
            if on_a && na < d {
                d = na;
            }
            if on_b && nb < d {
                d = nb;
            }
            d
        })
        .collect()
}

/// The least-squares fillet radius in [0, r_max], by golden section.
pub fn fit_fillet(pts: &[P], x: P, da: P, db: P, r_max: f64) -> f64 {
    golden(0.0, r_max, |r| {
        let mut s = 0.0;
        for e in fillet_dist(pts, x, da, db, r) {
            s += e * e;
        }
        s
    })
}

/// The fillet's two tangent points, and its SVG sweep flag.
pub fn fillet_points(x: P, da: P, db: P, r: f64) -> (P, P, bool) {
    let (half, _bis) = fillet_frame(da, db);
    let reach = r / half.tan();
    let sweep = da[0] * db[1] - da[1] * db[0] > 0.0; // turning clockwise on screen
    ([x[0] - da[0] * reach, x[1] - da[1] * reach], [x[0] + db[0] * reach, x[1] + db[1] * reach], sweep)
}

// --- how soft the image is -------------------------------------------------------

/// The standard normal CDF, by Abramowitz & Stegun 7.1.26 for erf.
pub fn ndtr(z: f64) -> f64 {
    let x = z.abs() / std::f64::consts::SQRT_2;
    let t = 1.0 / (1.0 + 0.3275911 * x);
    let poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))));
    let erf = 1.0 - poly * (-x * x).exp();
    let sign = if z > 0.0 {
        1.0
    } else if z < 0.0 {
        -1.0
    } else {
        0.0
    };
    0.5 * (1.0 + sign * erf)
}

/// How much of a pixel whose centre lies d px outside a blurred straight edge
/// is inside it. See the Python `_inside`.
pub fn inside(d: f64, sigma: f64) -> f64 {
    if sigma < 1e-3 {
        return (0.5 - d).clamp(0.0, 1.0);
    }
    let root = (2.0 * std::f64::consts::PI).sqrt();
    let g = |x: f64| -> f64 {
        let z = x / sigma;
        x * ndtr(z) + sigma * (-0.5 * z * z).exp() / root
    };
    g(0.5 - d) - g(-0.5 - d)
}

/// The blur of the image along the model's four sides, and the number of
/// pixels it was read from. See the Python `edge_sigma`. `rgb` holds the
/// image's integer levels; the profile is divided in float32, as numpy does
/// for the float32 image the Python is handed.
pub fn edge_sigma(rgb: &Image, m: &Model) -> Option<(f64, usize)> {
    let (h, w) = (rgb.h as i64, rgb.w as i64);
    let mut ds: Vec<f64> = Vec::new();
    let mut qs: Vec<f64> = Vec::new();
    let rmax = m.r.iter().cloned().fold(f64::NEG_INFINITY, |a, b| if b > a { b } else { a });
    for side in 0..4 {
        let axis = if side < 2 { 0 } else { 1 }; // the coordinate the side fixes (0: x)
        let level = [m.x0, m.x1, m.y0, m.y1][side];
        let out = if side == 0 || side == 2 { -1.0 } else { 1.0 };
        let (lo, hi) = if axis == 0 { (m.y0, m.y1) } else { (m.x0, m.x1) };
        let r0 = (lo + rmax + EDGE_END - 0.5).ceil() as i64;
        let r1 = (hi - rmax - EDGE_END - 0.5).floor() as i64;
        if r1 + 1 - r0 < 3 {
            continue;
        }
        let rows: Vec<i64> = (r0..=r1).collect();
        let c0 = (level - EDGE_PLATEAU.1 - 0.5).floor() as i64;
        let c1 = (level + EDGE_PLATEAU.1 - 0.5).ceil() as i64;
        let cols: Vec<i64> = (c0..=c1).collect();
        let (last_row, last_col) = (*rows.last().unwrap(), *cols.last().unwrap());
        if rows[0] < 0
            || cols.is_empty()
            || cols[0] < 0
            || (if axis == 0 { last_row >= h } else { last_row >= w })
            || (if axis == 0 { last_col >= w } else { last_col >= h })
        {
            continue;
        }
        let px = |i: usize, j: usize| -> &[f64] {
            if axis == 0 {
                rgb.at(rows[i] as usize, cols[j] as usize)
            } else {
                rgb.at(cols[j] as usize, rows[i] as usize)
            }
        };
        let d: Vec<f64> = cols.iter().map(|&c| out * ((c as f64 + 0.5) - level)).collect();
        let inner: Vec<bool> = d.iter().map(|&x| x <= -EDGE_PLATEAU.0 && x >= -EDGE_PLATEAU.1).collect();
        let outer: Vec<bool> = d.iter().map(|&x| x >= EDGE_PLATEAU.0 && x <= EDGE_PLATEAU.1).collect();
        if !inner.iter().any(|b| *b) || !outer.iter().any(|b| *b) {
            continue;
        }
        let median_of = |mask: &[bool]| -> [f64; 3] {
            let mut out = [0.0; 3];
            for ch in 0..3 {
                let mut vals: Vec<f64> = Vec::new();
                for i in 0..rows.len() {
                    for j in 0..cols.len() {
                        if mask[j] {
                            vals.push(px(i, j)[ch]);
                        }
                    }
                }
                // numpy takes the median of the float32 image in float32
                out[ch] = np_median_f32(&vals);
            }
            out
        };
        let c_in = median_of(&inner);
        let c_out = median_of(&outer);
        let axis_c = [c_in[0] - c_out[0], c_in[1] - c_out[1], c_in[2] - c_out[2]];
        let n2 = axis_c[0] * axis_c[0] + axis_c[1] * axis_c[1] + axis_c[2] * axis_c[2];
        if n2 < 100.0 {
            // under 10 levels of contrast: nothing to read
            continue;
        }
        for i in 0..rows.len() {
            for j in 0..cols.len() {
                if d[j].abs() > EDGE_REACH {
                    continue;
                }
                let p = px(i, j);
                let proj = (p[0] - c_out[0]) * axis_c[0] + (p[1] - c_out[1]) * axis_c[1] + (p[2] - c_out[2]) * axis_c[2];
                ds.push(d[j]);
                qs.push(((proj as f32) / (n2 as f32)) as f64);
            }
        }
    }
    if ds.is_empty() {
        return None;
    }
    let sigma = golden(0.0, EDGE_SIGMA_MAX, |sig| {
        let mut s = 0.0;
        for k in 0..ds.len() {
            let e = inside(ds[k], sig) - qs[k];
            s += e * e;
        }
        s
    });
    Some((sigma, ds.len()))
}

/// `np.median` of float32 values (integer levels): the mean of the two middle
/// ones is taken in float32.
fn np_median_f32(v: &[f64]) -> f64 {
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.total_cmp(b));
    let n = s.len();
    if n % 2 == 1 {
        s[n / 2]
    } else {
        ((s[n / 2 - 1] as f32 + s[n / 2] as f32) / 2.0f32) as f64
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rounded_rect_poly(x: f64, y: f64, w: f64, h: f64, r: f64, step: f64) -> Vec<P> {
        let m = Model::new(x, y, x + w, y + h, [r; 4]);
        let total = perimeter(&m);
        let k = (total / step).ceil() as usize;
        (0..k).map(|i| point_at(&m, total * i as f64 / k as f64)).collect()
    }

    #[test]
    fn a_small_corner_is_read_from_the_few_vertices_it_has() {
        for r in [2.0, 3.0, 4.5] {
            let poly = rounded_rect_poly(10.3, 20.6, 21.0, 21.0, r, 1.0);
            let m = fit_model(&poly, &vec![true; poly.len()], 1.5, 0.4).expect("a model");
            assert!((m.x0 - 10.3).abs() < 0.05 && (m.y1 - 41.6).abs() < 0.05, "{m:?}");
            assert!(m.r.iter().all(|v| (v - r).abs() < 0.15), "{r} {:?}", m.r);
        }
    }

    #[test]
    fn the_outline_is_walked_from_any_point_to_any_other() {
        let m = Model::new(0.0, 0.0, 20.0, 10.0, [3.0; 4]);
        let total = perimeter(&m);
        assert!((total - (2.0 * (14.0 + 4.0) + 2.0 * std::f64::consts::PI * 3.0)).abs() < 1e-9);
        for k in 0..36 {
            let s = total * k as f64 / 36.0;
            let p = point_at(&m, s);
            let (s2, d) = project(&m, p);
            assert!(d < 1e-9 && (py_mod(s2 - s + total / 2.0, total) - total / 2.0).abs() < 1e-6);
        }
        let segs = subpath(&m, 1.0, total - 1.0, false);
        for w in segs.windows(2) {
            assert!(norm([w[0].end()[0] - w[1].start()[0], w[0].end()[1] - w[1].start()[1]]) < 1e-9);
        }
    }

    #[test]
    fn cluster_1d_splits_what_one_value_cannot_hold() {
        let groups = cluster_1d(&[1.9, 2.6, 2.95, 3.2], &[1.0; 4], 0.48);
        let mut sizes: Vec<usize> = groups.iter().map(|g| g.1.len()).collect();
        sizes.sort();
        assert_eq!(sizes, vec![1, 3]);
    }

    #[test]
    fn python_arithmetic() {
        assert_eq!(py_sum(vec![0.1; 10]), 1.0);
        assert_eq!(py_mod(-0.0, 180.0).to_bits(), 0.0f64.to_bits());
        assert_eq!(np_percentile(&[1.0, 2.0, 3.0, 4.0], 95.0), 3.8499999999999996); // as numpy
        assert_eq!(linspace01(3), vec![0.0, 0.5, 1.0]);
    }
}
