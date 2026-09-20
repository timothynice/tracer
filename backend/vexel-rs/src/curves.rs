//! Stage 7: corners, whole-shape fitting, and piecewise cubic Bézier fitting.
//!
//! Input polylines come from marching squares (a vertex every ≤ 1 px). Corners
//! are turning angles that persist across two chord scales; between corners,
//! Schneider's least-squares cubic fitting with recursive splitting produces
//! G1-continuous curves within `tol`. Closed contours with no corners are tried
//! as circles / ellipses first, four axis-aligned corners as rectangles.

use crate::core::linalg::{eigh2, lstsq, solve, Mat};

pub type P = [f64; 2];

#[derive(Clone, Debug)]
pub enum Segment {
    Line { p0: P, p1: P },
    Cubic { p0: P, c1: P, c2: P, p1: P },
}

impl Segment {
    pub fn start(&self) -> P {
        match self {
            Segment::Line { p0, .. } | Segment::Cubic { p0, .. } => *p0,
        }
    }

    pub fn end(&self) -> P {
        match self {
            Segment::Line { p1, .. } | Segment::Cubic { p1, .. } => *p1,
        }
    }

    fn set_start(&mut self, v: P) {
        match self {
            Segment::Line { p0, .. } | Segment::Cubic { p0, .. } => *p0 = v,
        }
    }

    fn set_end(&mut self, v: P) {
        match self {
            Segment::Line { p1, .. } | Segment::Cubic { p1, .. } => *p1 = v,
        }
    }
}

#[derive(Clone, Debug)]
pub enum Shape {
    Circle { cx: f64, cy: f64, r: f64 },
    Ellipse { cx: f64, cy: f64, rx: f64, ry: f64, angle_deg: f64 },
    Rect { x: f64, y: f64, w: f64, h: f64 },
    Path { contours: Vec<Vec<Segment>> },
}

#[derive(Clone, Copy)]
pub struct CurveParams {
    pub corner_threshold: f64,
    pub tol: f64,
    pub shape_fitting: bool,
    pub snap_axis_deg: f64,
}

// --- helpers ---------------------------------------------------------------

#[inline]
fn sub(a: P, b: P) -> P {
    [a[0] - b[0], a[1] - b[1]]
}

#[inline]
fn norm(a: P) -> f64 {
    (a[0] * a[0] + a[1] * a[1]).sqrt()
}

#[inline]
fn normalize(v: P) -> P {
    let n = norm(v);
    if n > 1e-12 {
        [v[0] / n, v[1] / n]
    } else {
        v
    }
}

fn arc_lengths(poly: &[P], closed: bool) -> Vec<f64> {
    let mut cum = vec![0.0];
    let n = poly.len();
    let last = if closed { n } else { n - 1 };
    for i in 0..last {
        let j = (i + 1) % n;
        cum.push(cum[i] + norm(sub(poly[j], poly[i])));
    }
    cum
}

fn turning_angle(pb: P, p: P, pf: P) -> f64 {
    let v1 = sub(p, pb);
    let v2 = sub(pf, p);
    let (n1, n2) = (norm(v1), norm(v2));
    if n1 < 1e-9 || n2 < 1e-9 {
        return 0.0;
    }
    let c = ((v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)).clamp(-1.0, 1.0);
    c.acos().to_degrees()
}

/// `np.interp` along a closed polyline's arc length.
fn points_at_arcs(poly: &[P], cum: &[f64], s: f64) -> P {
    let total = cum[cum.len() - 1];
    let mut ss = s % total;
    if ss < 0.0 {
        ss += total;
    }
    // np.interp clamps outside the sample range
    if ss <= cum[0] {
        return poly[0];
    }
    let n = poly.len();
    if ss >= total {
        return poly[0];
    }
    let mut i = 0usize;
    while i + 1 < cum.len() && cum[i + 1] < ss {
        i += 1;
    }
    let span = cum[i + 1] - cum[i];
    let f = if span > 0.0 { (ss - cum[i]) / span } else { 0.0 };
    let a = poly[i % n];
    let b = poly[(i + 1) % n];
    [a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])]
}

/// Indices of vertices where the contour turns by more than `threshold_deg` at
/// every chord scale (closed polyline).
pub fn find_corners(poly: &[P], threshold_deg: f64) -> Vec<usize> {
    const SCALES: [f64; 2] = [2.0, 4.0];
    let n = poly.len();
    if n < 4 {
        return Vec::new();
    }
    let cum = arc_lengths(poly, true);
    let total = cum[cum.len() - 1];
    if total < 2.0 * SCALES[1] {
        return Vec::new();
    }
    let here = &cum[..n];
    let mut angles = vec![f64::INFINITY; n];
    for s in SCALES {
        for i in 0..n {
            let pb = points_at_arcs(poly, &cum, here[i] - s);
            let pf = points_at_arcs(poly, &cum, here[i] + s);
            let v1 = sub(poly[i], pb);
            let v2 = sub(pf, poly[i]);
            let (n1, n2) = (norm(v1), norm(v2));
            let cosang = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2).max(1e-12);
            let mut ang = cosang.clamp(-1.0, 1.0).acos().to_degrees();
            if n1 < 1e-9 || n2 < 1e-9 {
                ang = 0.0;
            }
            if ang < angles[i] {
                angles[i] = ang;
            }
        }
    }
    let cand: Vec<usize> = (0..n).filter(|i| angles[*i] > threshold_deg).collect();
    if cand.is_empty() {
        return Vec::new();
    }
    // non-maximum suppression within the smallest scale along the arc
    let window = SCALES[0];
    let mut out = Vec::new();
    for (ii, i) in cand.iter().enumerate() {
        let mut beaten = false;
        for (jj, j) in cand.iter().enumerate() {
            if ii == jj {
                continue;
            }
            let mut d = (here[*i] - here[*j]).abs();
            d = d.min(total - d);
            if d <= window && (angles[*j] > angles[*i] || (angles[*j] == angles[*i] && *j < *i)) {
                beaten = true;
                break;
            }
        }
        if !beaten {
            out.push(*i);
        }
    }
    out
}

// --- whole shapes ----------------------------------------------------------

fn percentile95(v: &mut [f64]) -> f64 {
    // numpy's linear interpolation between order statistics
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = v.len();
    if n == 0 {
        return f64::INFINITY;
    }
    let pos = 0.95 * (n - 1) as f64;
    let lo = pos.floor() as usize;
    let hi = pos.ceil() as usize;
    if lo == hi {
        v[lo]
    } else {
        v[lo] + (pos - lo as f64) * (v[hi] - v[lo])
    }
}

/// Kåsa algebraic circle fit. Returns (circle, 95th-percentile radial deviation).
pub fn fit_circle(poly: &[P]) -> (Shape, f64) {
    let n = poly.len();
    let mut a = Mat::zeros(n, 3);
    let mut b = Mat::zeros(n, 1);
    for (i, p) in poly.iter().enumerate() {
        a.set(i, 0, p[0]);
        a.set(i, 1, p[1]);
        a.set(i, 2, 1.0);
        b.set(i, 0, -(p[0] * p[0] + p[1] * p[1]));
    }
    let s = lstsq(&a, &b);
    let (cx, cy, c) = (-s.at(0, 0) / 2.0, -s.at(1, 0) / 2.0, s.at(2, 0));
    let r2 = cx * cx + cy * cy - c;
    if r2 <= 0.0 {
        return (Shape::Circle { cx, cy, r: 0.0 }, f64::INFINITY);
    }
    let r = r2.sqrt();
    let mut dev: Vec<f64> = poly.iter().map(|p| ((p[0] - cx).hypot(p[1] - cy) - r).abs()).collect();
    (Shape::Circle { cx, cy, r }, percentile95(&mut dev))
}

/// Direct least-squares conic fit (Halir & Flusser), converted to centre /
/// semi-axes / angle. `None` when the conic is not an ellipse.
pub fn fit_ellipse(poly: &[P]) -> (Option<Shape>, f64) {
    let n = poly.len();
    if n < 5 {
        return (None, f64::INFINITY);
    }
    let mx = poly.iter().map(|p| p[0]).sum::<f64>() / n as f64;
    let my = poly.iter().map(|p| p[1]).sum::<f64>() / n as f64;
    let mut s1 = [[0.0f64; 3]; 3];
    let mut s2 = [[0.0f64; 3]; 3];
    let mut s3 = [[0.0f64; 3]; 3];
    for p in poly {
        let (x, y) = (p[0] - mx, p[1] - my);
        let d1 = [x * x, x * y, y * y];
        let d2 = [x, y, 1.0];
        for i in 0..3 {
            for j in 0..3 {
                s1[i][j] += d1[i] * d1[j];
                s2[i][j] += d1[i] * d2[j];
                s3[i][j] += d2[i] * d2[j];
            }
        }
    }
    // t = -solve(s3, s2.T)
    let mut s3m = Mat::zeros(3, 3);
    let mut s2t = Mat::zeros(3, 3);
    for i in 0..3 {
        for j in 0..3 {
            s3m.set(i, j, s3[i][j]);
            s2t.set(i, j, s2[j][i]);
        }
    }
    let Some(tsol) = solve(&s3m, &s2t) else {
        return (None, f64::INFINITY);
    };
    let mut t = [[0.0f64; 3]; 3];
    for i in 0..3 {
        for j in 0..3 {
            t[i][j] = -tsol.at(i, j);
        }
    }
    // m = s1 + s2 @ t, then the reduced scatter matrix
    let mut m = [[0.0f64; 3]; 3];
    for i in 0..3 {
        for j in 0..3 {
            let mut acc = s1[i][j];
            for k in 0..3 {
                acc += s2[i][k] * t[k][j];
            }
            m[i][j] = acc;
        }
    }
    let mm = [
        [m[2][0] / 2.0, m[2][1] / 2.0, m[2][2] / 2.0],
        [-m[1][0], -m[1][1], -m[1][2]],
        [m[0][0] / 2.0, m[0][1] / 2.0, m[0][2] / 2.0],
    ];
    let Some((a1, _)) = ellipse_eigenvector(&mm) else {
        return (None, f64::INFINITY);
    };
    let (a, b, c) = (a1[0], a1[1], a1[2]);
    let mut dv = [0.0f64; 3];
    for i in 0..3 {
        dv[i] = t[i][0] * a1[0] + t[i][1] * a1[1] + t[i][2] * a1[2];
    }
    let (d, e, f) = (dv[0], dv[1], dv[2]);

    // (p − c)ᵀ Q (p − c) = k
    let mut q = Mat::zeros(2, 2);
    q.set(0, 0, a);
    q.set(0, 1, b / 2.0);
    q.set(1, 0, b / 2.0);
    q.set(1, 1, c);
    let mut rhs = Mat::zeros(2, 1);
    rhs.set(0, 0, d);
    rhs.set(1, 0, e);
    let Some(sol) = solve(&q, &rhs) else {
        return (None, f64::INFINITY);
    };
    let centre = [-0.5 * sol.at(0, 0), -0.5 * sol.at(1, 0)];
    let mut k = -(f + 0.5 * (d * centre[0] + e * centre[1]));
    let (mut lam, vec) = eigh2(a, b / 2.0, c);
    if k <= 0.0 || lam[0] <= 0.0 || lam[1] <= 0.0 {
        if k < 0.0 && lam[0] < 0.0 && lam[1] < 0.0 {
            k = -k;
            lam = [-lam[0], -lam[1]];
        } else {
            return (None, f64::INFINITY);
        }
    }
    let axes = [(k / lam[0]).sqrt(), (k / lam[1]).sqrt()];
    let (major_i, minor_i) = if axes[0] >= axes[1] { (0, 1) } else { (1, 0) };
    let (rx, ry) = (axes[major_i], axes[minor_i]);
    let major = vec[major_i];
    let angle = major[1].atan2(major[0]).to_degrees();
    let (cx, cy) = (centre[0] + mx, centre[1] + my);
    if !(cx.is_finite() && cy.is_finite() && rx.is_finite() && ry.is_finite() && angle.is_finite()) || rx <= 0.0 || ry <= 0.0 {
        return (None, f64::INFINITY);
    }
    let th = angle.to_radians();
    let mut dev: Vec<f64> = poly
        .iter()
        .map(|p| {
            let (dx, dy) = (p[0] - cx, p[1] - cy);
            let u = dx * th.cos() + dy * th.sin();
            let v = -dx * th.sin() + dy * th.cos();
            let rho = ((u / rx).powi(2) + (v / ry).powi(2)).sqrt();
            (u.hypot(v) * (1.0 - 1.0 / rho.max(1e-9))).abs()
        })
        .collect();
    (Some(Shape::Ellipse { cx, cy, rx, ry, angle_deg: angle }), percentile95(&mut dev))
}

/// The eigenvector of the 3×3 reduced scatter matrix that satisfies the
/// ellipse condition `4·a₀·a₂ − a₁² > 0`. The matrix is not symmetric, so this
/// is an unsymmetric eigenproblem solved by finding the roots of the cubic
/// characteristic polynomial and back-substituting.
fn ellipse_eigenvector(m: &[[f64; 3]; 3]) -> Option<([f64; 3], f64)> {
    let roots = cubic_eigenvalues(m);
    for lam in roots {
        let Some(v) = null_vector(m, lam) else { continue };
        if 4.0 * v[0] * v[2] - v[1] * v[1] > 0.0 {
            return Some((v, lam));
        }
    }
    None
}

/// Real roots of `det(M − λI) = 0` for a 3×3, by the trigonometric solution of
/// the depressed cubic.
fn cubic_eigenvalues(m: &[[f64; 3]; 3]) -> Vec<f64> {
    let tr = m[0][0] + m[1][1] + m[2][2];
    let c2 = -tr;
    let c1 = m[0][0] * m[1][1] - m[0][1] * m[1][0] + m[0][0] * m[2][2] - m[0][2] * m[2][0]
        + m[1][1] * m[2][2]
        - m[1][2] * m[2][1];
    let det = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    let c0 = -det;
    // depressed cubic t³ + pt + q with λ = t − c2/3
    let shift = c2 / 3.0;
    let p = c1 - c2 * c2 / 3.0;
    let q = 2.0 * c2 * c2 * c2 / 27.0 - c2 * c1 / 3.0 + c0;
    let mut out = Vec::new();
    if p.abs() < 1e-300 && q.abs() < 1e-300 {
        out.push(-shift);
        return out;
    }
    let disc = q * q / 4.0 + p * p * p / 27.0;
    if disc > 0.0 {
        let s = disc.sqrt();
        let u = (-q / 2.0 + s).cbrt();
        let v = (-q / 2.0 - s).cbrt();
        out.push(u + v - shift);
    } else {
        let r = (-p / 3.0).max(0.0).sqrt();
        let cos_arg = if r > 1e-300 { (-q / (2.0 * r * r * r)).clamp(-1.0, 1.0) } else { 0.0 };
        let phi = cos_arg.acos();
        for k in 0..3 {
            out.push(2.0 * r * ((phi + 2.0 * std::f64::consts::PI * k as f64) / 3.0).cos() - shift);
        }
    }
    out
}

/// A unit null vector of `M − λI`, by Gaussian elimination with the free
/// variable set to 1.
fn null_vector(m: &[[f64; 3]; 3], lam: f64) -> Option<[f64; 3]> {
    let mut a = [[0.0f64; 3]; 3];
    for i in 0..3 {
        for j in 0..3 {
            a[i][j] = m[i][j] - if i == j { lam } else { 0.0 };
        }
    }
    // row reduce
    let mut pivots: Vec<usize> = Vec::new();
    let mut row = 0usize;
    for col in 0..3 {
        let mut best = row;
        for r in row..3 {
            if a[r][col].abs() > a[best][col].abs() {
                best = r;
            }
        }
        if row >= 3 || a[best][col].abs() < 1e-12 {
            continue;
        }
        a.swap(row, best);
        let d = a[row][col];
        for j in 0..3 {
            a[row][j] /= d;
        }
        for r in 0..3 {
            if r != row && a[r][col].abs() > 0.0 {
                let f = a[r][col];
                for j in 0..3 {
                    a[r][j] -= f * a[row][j];
                }
            }
        }
        pivots.push(col);
        row += 1;
        if row == 3 {
            break;
        }
    }
    let free = (0..3).find(|c| !pivots.contains(c))?;
    let mut v = [0.0f64; 3];
    v[free] = 1.0;
    for (r, col) in pivots.iter().enumerate() {
        v[*col] = -a[r][free];
    }
    let n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
    if n < 1e-300 {
        return None;
    }
    Some([v[0] / n, v[1] / n, v[2] / n])
}

fn chord_deviation(points: &[P]) -> f64 {
    let a = points[0];
    let b = points[points.len() - 1];
    let d = sub(b, a);
    let n = norm(d);
    if n < 1e-9 {
        return points.iter().map(|p| norm(sub(*p, a))).fold(0.0, f64::max);
    }
    points
        .iter()
        .map(|p| ((p[0] - a[0]) * d[1] - (p[1] - a[1]) * d[0]).abs() / n)
        .fold(0.0, f64::max)
}

fn angle_deg(p0: P, p1: P) -> f64 {
    (p1[1] - p0[1]).atan2(p1[0] - p0[0]).to_degrees()
}

pub fn try_rect(poly: &[P], corners: &[usize], params: &CurveParams) -> Option<Shape> {
    if corners.len() != 4 {
        return None;
    }
    let pieces = split_pieces(poly, corners);
    let pts: Vec<P> = pieces.iter().map(|p| p[0]).collect();
    for (k, side) in pieces.iter().enumerate() {
        if chord_deviation(side) > params.tol {
            return None;
        }
        let ang = angle_deg(pts[k], pts[(k + 1) % 4]).rem_euclid(180.0);
        if ang.min((ang - 90.0).abs()).min((ang - 180.0).abs()) > params.snap_axis_deg {
            return None;
        }
    }
    let x0 = pts.iter().map(|p| p[0]).fold(f64::INFINITY, f64::min);
    let y0 = pts.iter().map(|p| p[1]).fold(f64::INFINITY, f64::min);
    let x1 = pts.iter().map(|p| p[0]).fold(f64::NEG_INFINITY, f64::max);
    let y1 = pts.iter().map(|p| p[1]).fold(f64::NEG_INFINITY, f64::max);
    Some(Shape::Rect { x: x0, y: y0, w: x1 - x0, h: y1 - y0 })
}

// --- Schneider cubic fitting -----------------------------------------------

fn bezier(p0: P, c1: P, c2: P, p1: P, t: f64) -> P {
    let mt = 1.0 - t;
    let (a, b, c, d) = (mt * mt * mt, 3.0 * mt * mt * t, 3.0 * mt * t * t, t * t * t);
    [
        a * p0[0] + b * c1[0] + c * c2[0] + d * p1[0],
        a * p0[1] + b * c1[1] + c * c2[1] + d * p1[1],
    ]
}

fn bezier_d1(p0: P, c1: P, c2: P, p1: P, t: f64) -> P {
    let mt = 1.0 - t;
    [
        3.0 * (mt * mt * (c1[0] - p0[0]) + 2.0 * mt * t * (c2[0] - c1[0]) + t * t * (p1[0] - c2[0])),
        3.0 * (mt * mt * (c1[1] - p0[1]) + 2.0 * mt * t * (c2[1] - c1[1]) + t * t * (p1[1] - c2[1])),
    ]
}

fn bezier_d2(p0: P, c1: P, c2: P, p1: P, t: f64) -> P {
    let mt = 1.0 - t;
    [
        6.0 * (mt * (c2[0] - 2.0 * c1[0] + p0[0]) + t * (p1[0] - 2.0 * c2[0] + c1[0])),
        6.0 * (mt * (c2[1] - 2.0 * c1[1] + p0[1]) + t * (p1[1] - 2.0 * c2[1] + c1[1])),
    ]
}

fn chord_params(points: &[P]) -> Vec<f64> {
    let mut u = vec![0.0];
    for i in 1..points.len() {
        u.push(u[i - 1] + norm(sub(points[i], points[i - 1])));
    }
    let total = u[u.len() - 1];
    if total > 0.0 {
        u.iter().map(|v| v / total).collect()
    } else {
        let n = points.len();
        (0..n).map(|i| i as f64 / (n - 1).max(1) as f64).collect()
    }
}

fn generate_bezier(points: &[P], u: &[f64], t1: P, t2: P) -> (P, P, P, P) {
    let p0 = points[0];
    let p3 = points[points.len() - 1];
    let (mut c11, mut c12, mut c22, mut x1, mut x2) = (0.0, 0.0, 0.0, 0.0, 0.0);
    for (i, ui) in u.iter().enumerate() {
        let mt = 1.0 - ui;
        let b1 = 3.0 * mt * mt * ui;
        let b2 = 3.0 * mt * ui * ui;
        let a1 = [t1[0] * b1, t1[1] * b1];
        let a2 = [t2[0] * b2, t2[1] * b2];
        c11 += a1[0] * a1[0] + a1[1] * a1[1];
        c12 += a1[0] * a2[0] + a1[1] * a2[1];
        c22 += a2[0] * a2[0] + a2[1] * a2[1];
        let m3 = mt * mt * mt;
        let u3 = ui * ui * ui;
        let tmp = [
            points[i][0] - (m3 * p0[0] + u3 * p3[0]) - b1 * p0[0] - b2 * p3[0],
            points[i][1] - (m3 * p0[1] + u3 * p3[1]) - b1 * p0[1] - b2 * p3[1],
        ];
        x1 += a1[0] * tmp[0] + a1[1] * tmp[1];
        x2 += a2[0] * tmp[0] + a2[1] * tmp[1];
    }
    let det = c11 * c22 - c12 * c12;
    let seg_len = norm(sub(p3, p0));
    let (mut alpha1, mut alpha2) = if det.abs() > 1e-12 {
        ((x1 * c22 - x2 * c12) / det, (c11 * x2 - c12 * x1) / det)
    } else {
        (seg_len / 3.0, seg_len / 3.0)
    };
    let eps = 1e-6 * seg_len.max(1.0);
    if alpha1 < eps || alpha2 < eps || alpha1 > 3.0 * seg_len || alpha2 > 3.0 * seg_len {
        alpha1 = seg_len / 3.0;
        alpha2 = seg_len / 3.0;
    }
    (
        p0,
        [p0[0] + t1[0] * alpha1, p0[1] + t1[1] * alpha1],
        [p3[0] + t2[0] * alpha2, p3[1] + t2[1] * alpha2],
        p3,
    )
}

fn max_error(points: &[P], c: &(P, P, P, P), u: &[f64]) -> (f64, usize) {
    let d: Vec<f64> = u
        .iter()
        .enumerate()
        .map(|(i, t)| norm(sub(bezier(c.0, c.1, c.2, c.3, *t), points[i])))
        .collect();
    let split = if d.len() > 2 {
        let mut best = 1usize;
        for i in 1..d.len() - 1 {
            if d[i] > d[best] {
                best = i;
            }
        }
        best
    } else {
        d.len() / 2
    };
    (d.iter().copied().fold(0.0, f64::max), split)
}

fn reparametrize(points: &[P], c: &(P, P, P, P), u: &[f64]) -> Vec<f64> {
    u.iter()
        .enumerate()
        .map(|(i, t)| {
            let q = sub(bezier(c.0, c.1, c.2, c.3, *t), points[i]);
            let d1 = bezier_d1(c.0, c.1, c.2, c.3, *t);
            let d2 = bezier_d2(c.0, c.1, c.2, c.3, *t);
            let num = q[0] * d1[0] + q[1] * d1[1];
            let den = d1[0] * d1[0] + d1[1] * d1[1] + q[0] * d2[0] + q[1] * d2[1];
            let step = if den.abs() > 1e-12 { num / den } else { 0.0 };
            (t - step).clamp(0.0, 1.0)
        })
        .collect()
}

/// Schneider: fit one cubic to `points` with end tangents, split at the worst
/// point and recurse when needed.
pub fn fit_cubics(points: &[P], t1: P, t2: P, tol: f64, depth: usize) -> Vec<Segment> {
    if points.len() == 2 {
        let d = norm(sub(points[1], points[0])) / 3.0;
        return vec![Segment::Cubic {
            p0: points[0],
            c1: [points[0][0] + t1[0] * d, points[0][1] + t1[1] * d],
            c2: [points[1][0] + t2[0] * d, points[1][1] + t2[1] * d],
            p1: points[1],
        }];
    }
    let mut u = chord_params(points);
    let mut c = generate_bezier(points, &u, t1, t2);
    let (mut err, mut split) = max_error(points, &c, &u);
    if err < tol {
        return vec![Segment::Cubic { p0: c.0, c1: c.1, c2: c.2, p1: c.3 }];
    }
    if err < tol * tol * 4.0 + tol {
        for _ in 0..4 {
            u = reparametrize(points, &c, &u);
            c = generate_bezier(points, &u, t1, t2);
            let (e, s) = max_error(points, &c, &u);
            err = e;
            split = s;
            if err < tol {
                return vec![Segment::Cubic { p0: c.0, c1: c.1, c2: c.2, p1: c.3 }];
            }
        }
    }
    if depth > 24 || points.len() < 4 {
        return vec![Segment::Cubic { p0: c.0, c1: c.1, c2: c.2, p1: c.3 }];
    }
    let split = split.clamp(1, points.len() - 2);
    let centre_t = normalize(sub(points[split - 1], points[split + 1]));
    let mut left = fit_cubics(&points[..split + 1], t1, centre_t, tol, depth + 1);
    let right = fit_cubics(&points[split..], [-centre_t[0], -centre_t[1]], t2, tol, depth + 1);
    left.extend(right);
    left
}

fn end_tangent(points: &[P], at_start: bool) -> P {
    let k = 3.min(points.len() - 1);
    if at_start {
        normalize(sub(points[k], points[0]))
    } else {
        normalize(sub(points[points.len() - 1 - k], points[points.len() - 1]))
    }
}

/// Fit an open polyline as a line or a chain of cubics.
pub fn fit_open(points: &[P], tol: f64, t_start: Option<P>, t_end: Option<P>) -> Vec<Segment> {
    if points.len() < 2 {
        return Vec::new();
    }
    if chord_deviation(points) <= tol {
        return vec![Segment::Line { p0: points[0], p1: points[points.len() - 1] }];
    }
    let t1 = t_start.unwrap_or_else(|| end_tangent(points, true));
    let t2 = t_end.unwrap_or_else(|| end_tangent(points, false));
    fit_cubics(points, t1, t2, tol, 0)
}

/// Closed contour without corners: start at the vertex of least curvature, G1
/// at the seam.
pub fn fit_closed_smooth(poly: &[P], tol: f64) -> Vec<Segment> {
    let n = poly.len();
    if n < 3 {
        return Vec::new();
    }
    let mut start = 0usize;
    let mut best = f64::INFINITY;
    for i in 0..n {
        let prev = poly[(i + n - 1) % n];
        let nxt = poly[(i + 1) % n];
        let t = turning_angle(prev, poly[i], nxt);
        if t < best {
            best = t;
            start = i;
        }
    }
    let mut pts: Vec<P> = Vec::with_capacity(n + 1);
    pts.extend_from_slice(&poly[start..]);
    pts.extend_from_slice(&poly[..start]);
    pts.push(poly[start]);
    let t = normalize(sub(pts[1], pts[pts.len() - 2]));
    fit_open(&pts, tol, Some(t), Some([-t[0], -t[1]]))
}

/// Make nearly horizontal / vertical lines exactly so, moving shared endpoints
/// with them.
pub fn snap_axis_lines(mut segments: Vec<Segment>, snap_deg: f64) -> Vec<Segment> {
    let n = segments.len();
    if n == 0 {
        return segments;
    }
    for i in 0..n {
        let (p0, p1) = match &segments[i] {
            Segment::Line { p0, p1 } => (*p0, *p1),
            _ => continue,
        };
        let ang = angle_deg(p0, p1).rem_euclid(180.0);
        let (new0, new1) = if ang.min(180.0 - ang) <= snap_deg {
            let y = (p0[1] + p1[1]) / 2.0;
            ([p0[0], y], [p1[0], y])
        } else if (ang - 90.0).abs() <= snap_deg {
            let x = (p0[0] + p1[0]) / 2.0;
            ([x, p0[1]], [x, p1[1]])
        } else {
            continue;
        };
        segments[i] = Segment::Line { p0: new0, p1: new1 };
        let prev = (i + n - 1) % n;
        let next = (i + 1) % n;
        segments[prev].set_end(new0);
        segments[next].set_start(new1);
    }
    segments
}

// --- corner sharpening -----------------------------------------------------

fn line_through(points: &[P]) -> (P, P) {
    let n = points.len();
    let cx = points.iter().map(|p| p[0]).sum::<f64>() / n as f64;
    let cy = points.iter().map(|p| p[1]).sum::<f64>() / n as f64;
    if n < 2 {
        return ([cx, cy], [1.0, 0.0]);
    }
    let centred: Vec<[f64; 2]> = points.iter().map(|p| [p[0] - cx, p[1] - cy]).collect();
    let (mut sxx, mut sxy, mut syy) = (0.0, 0.0, 0.0);
    for p in &centred {
        sxx += p[0] * p[0];
        sxy += p[0] * p[1];
        syy += p[1] * p[1];
    }
    let (vals, vecs) = eigh2(sxx, sxy, syy);
    let d = if vals[1] >= vals[0] { vecs[1] } else { vecs[0] };
    ([cx, cy], d)
}

fn intersect(p: P, d: P, q: P, e: P) -> Option<P> {
    let den = d[0] * e[1] - d[1] * e[0];
    if den.abs() < 1e-9 {
        return None;
    }
    let t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den;
    Some([p[0] + t * d[0], p[1] + t * d[1]])
}

/// Open polylines between consecutive corners, with each corner *sharpened*:
/// marching squares chamfers a hard corner by half a pixel, so the true corner
/// is recovered as the intersection of lines fitted to the two adjacent sides.
pub fn split_pieces(poly: &[P], corners: &[usize]) -> Vec<Vec<P>> {
    const REACH: f64 = 3.0;
    const TRIM: f64 = 0.8;
    let mut corners: Vec<usize> = corners.to_vec();
    corners.sort_unstable();
    let k = corners.len();
    let n = poly.len();

    let piece_between = |i: usize, j: usize| -> Vec<P> {
        if j > i {
            poly[i..=j].to_vec()
        } else {
            let mut v = poly[i..].to_vec();
            v.extend_from_slice(&poly[..=j]);
            v
        }
    };
    let raw: Vec<Vec<P>> = (0..k).map(|i| piece_between(corners[i], corners[(i + 1) % k])).collect();

    let near = |points: &[P], from_start: bool| -> Vec<P> {
        let anchor = if from_start { points[0] } else { points[points.len() - 1] };
        let d: Vec<f64> = points.iter().map(|p| norm(sub(*p, anchor))).collect();
        let sel: Vec<P> = points
            .iter()
            .zip(d.iter())
            .filter(|(_, dd)| **dd >= TRIM && **dd <= REACH)
            .map(|(p, _)| *p)
            .collect();
        if sel.len() < 2 {
            points
                .iter()
                .zip(d.iter())
                .filter(|(_, dd)| **dd > 0.0 && **dd <= REACH * 2.0)
                .map(|(p, _)| *p)
                .collect()
        } else {
            sel
        }
    };

    let mut sharp: Vec<P> = Vec::with_capacity(k);
    for i in 0..k {
        let incoming = &raw[(i + k - 1) % k];
        let outgoing = &raw[i];
        let corner = poly[corners[i] % n];
        let a_pts = near(incoming, false);
        let b_pts = near(outgoing, true);
        if a_pts.len() >= 2 && b_pts.len() >= 2 {
            let (p, d) = line_through(&a_pts);
            let (q, e) = line_through(&b_pts);
            if let Some(x) = intersect(p, d, q, e) {
                if norm(sub(x, corner)) <= 1.5 {
                    sharp.push(x);
                    continue;
                }
            }
        }
        sharp.push(corner);
    }

    let mut pieces = Vec::with_capacity(k);
    for (i, pts) in raw.iter().enumerate() {
        let start = sharp[i];
        let end = sharp[(i + 1) % k];
        let mut out = vec![start];
        if pts.len() > 2 {
            for p in &pts[1..pts.len() - 1] {
                if norm(sub(*p, pts[0])) >= TRIM && norm(sub(*p, pts[pts.len() - 1])) >= TRIM {
                    out.push(*p);
                }
            }
        }
        out.push(end);
        pieces.push(out);
    }
    pieces
}

pub fn fit_contour_segments(poly: &[P], params: &CurveParams) -> Vec<Segment> {
    let corners = find_corners(poly, params.corner_threshold);
    if corners.is_empty() {
        return fit_closed_smooth(poly, params.tol);
    }
    let mut segments = Vec::new();
    for piece in split_pieces(poly, &corners) {
        if piece.len() < 2 {
            continue;
        }
        segments.extend(fit_open(&piece, params.tol, None, None));
    }
    snap_axis_lines(segments, params.snap_axis_deg)
}

/// Fit a region's contours (outer first). Whole-shape primitives only for
/// single contours.
pub fn fit_shape(contours: &[Vec<P>], params: &CurveParams) -> Shape {
    if contours.len() == 1 && params.shape_fitting {
        let poly = &contours[0];
        let corners = find_corners(poly, params.corner_threshold);
        if corners.is_empty() && poly.len() >= 8 {
            let (circle, dev) = fit_circle(poly);
            if dev <= params.tol {
                if let Shape::Circle { r, .. } = circle {
                    if r > 1.0 {
                        return circle;
                    }
                }
            }
            let (ellipse, dev) = fit_ellipse(poly);
            if let Some(e) = ellipse {
                if dev <= params.tol {
                    return e;
                }
            }
        }
        if let Some(rect) = try_rect(poly, &corners, params) {
            return rect;
        }
    }
    Shape::Path {
        contours: contours.iter().map(|p| fit_contour_segments(p, params)).collect(),
    }
}

// --- serialisation ---------------------------------------------------------

pub fn fmt(v: f64, precision: usize) -> String {
    let s = format!("{:.*}", precision, v);
    let s = if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        s
    };
    if s == "-0" || s.is_empty() {
        "0".to_string()
    } else {
        s
    }
}

pub fn path_d(contours: &[Vec<Segment>], precision: usize) -> String {
    let mut parts = String::new();
    for segs in contours {
        if segs.is_empty() {
            continue;
        }
        let p = segs[0].start();
        parts.push_str(&format!("M{} {}", fmt(p[0], precision), fmt(p[1], precision)));
        for s in segs {
            match s {
                Segment::Line { p1, .. } => {
                    parts.push_str(&format!("L{} {}", fmt(p1[0], precision), fmt(p1[1], precision)));
                }
                Segment::Cubic { c1, c2, p1, .. } => {
                    parts.push_str(&format!(
                        "C{} {} {} {} {} {}",
                        fmt(c1[0], precision),
                        fmt(c1[1], precision),
                        fmt(c2[0], precision),
                        fmt(c2[1], precision),
                        fmt(p1[0], precision),
                        fmt(p1[1], precision)
                    ));
                }
            }
        }
        parts.push('Z');
    }
    parts
}

pub fn shape_svg(shape: &Shape, attrs: &str, precision: usize) -> String {
    let p = precision;
    match shape {
        Shape::Circle { cx, cy, r } => format!(
            "<circle cx=\"{}\" cy=\"{}\" r=\"{}\" {}/>",
            fmt(*cx, p),
            fmt(*cy, p),
            fmt(*r, p),
            attrs
        ),
        Shape::Ellipse { cx, cy, rx, ry, angle_deg } => {
            let rot = if angle_deg.abs() > 0.05 {
                format!(
                    " transform=\"rotate({} {} {})\"",
                    fmt(*angle_deg, 2),
                    fmt(*cx, p),
                    fmt(*cy, p)
                )
            } else {
                String::new()
            };
            format!(
                "<ellipse cx=\"{}\" cy=\"{}\" rx=\"{}\" ry=\"{}\" {}{}/>",
                fmt(*cx, p),
                fmt(*cy, p),
                fmt(*rx, p),
                fmt(*ry, p),
                attrs,
                rot
            )
        }
        Shape::Rect { x, y, w, h } => format!(
            "<rect x=\"{}\" y=\"{}\" width=\"{}\" height=\"{}\" {}/>",
            fmt(*x, p),
            fmt(*y, p),
            fmt(*w, p),
            fmt(*h, p),
            attrs
        ),
        Shape::Path { contours } => {
            let rule = if contours.len() > 1 { " fill-rule=\"evenodd\"" } else { "" };
            format!("<path d=\"{}\" {}{}/>", path_d(contours, p), attrs, rule)
        }
    }
}
