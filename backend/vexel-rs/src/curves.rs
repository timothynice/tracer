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
    /// A circular arc — SVG `A r r 0 large sweep x y`. See the Python `CircArc`.
    Arc { p0: P, p1: P, r: f64, large: bool, sweep: bool },
}

impl Segment {
    pub fn start(&self) -> P {
        match self {
            Segment::Line { p0, .. } | Segment::Cubic { p0, .. } | Segment::Arc { p0, .. } => *p0,
        }
    }

    pub fn end(&self) -> P {
        match self {
            Segment::Line { p1, .. } | Segment::Cubic { p1, .. } | Segment::Arc { p1, .. } => *p1,
        }
    }

    pub fn set_start_pub(&mut self, v: P) {
        self.set_start(v)
    }

    pub fn set_end_pub(&mut self, v: P) {
        self.set_end(v)
    }

    fn set_start(&mut self, v: P) {
        match self {
            Segment::Line { p0, .. } | Segment::Cubic { p0, .. } | Segment::Arc { p0, .. } => *p0 = v,
        }
    }

    fn set_end(&mut self, v: P) {
        match self {
            Segment::Line { p1, .. } | Segment::Cubic { p1, .. } | Segment::Arc { p1, .. } => *p1 = v,
        }
    }
}

/// The same curve walked the other way — what the region on the far side of a
/// shared boundary needs, so that both describe one geometry.
pub fn reverse_segments(segments: &[Segment]) -> Vec<Segment> {
    segments
        .iter()
        .rev()
        .map(|s| match s {
            Segment::Line { p0, p1 } => Segment::Line { p0: *p1, p1: *p0 },
            Segment::Cubic { p0, c1, c2, p1 } => Segment::Cubic { p0: *p1, c1: *c2, c2: *c1, p1: *p0 },
            Segment::Arc { p0, p1, r, large, sweep } => Segment::Arc { p0: *p1, p1: *p0, r: *r, large: *large, sweep: !*sweep },
        })
        .collect()
}

#[derive(Clone, Debug)]
pub enum Shape {
    Circle { cx: f64, cy: f64, r: f64 },
    Ellipse { cx: f64, cy: f64, rx: f64, ry: f64, angle_deg: f64 },
    Rect { x: f64, y: f64, w: f64, h: f64 },
    RoundedRect { x: f64, y: f64, w: f64, h: f64, rx: f64 },
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
pub fn normalize(v: P) -> P {
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

// --- lines first ---------------------------------------------------------------------
//
// Port of the Python `line_runs` / `lines_first` / `fit_stretch`. Same arithmetic,
// same order, so both implementations land on the same bits.
pub const LINE_RMS: f64 = 0.10;
pub const LINE_P98: f64 = 0.30;
pub const LINE_MIN: f64 = 8.1;
pub const LINE_SAG: f64 = 0.10;
pub const LINE_END: f64 = 0.15;
pub const MERGE_DEG: f64 = 2.0;
pub const GAP_MIN: f64 = 1.6;
pub const CORNER_GAP: f64 = 2.1;
pub const CORNER_REACH: f64 = 3.1;
pub const SNAP_END: f64 = 0.5;
pub const CHORD_TURN: f64 = 20.0;
pub const CHORD_GAP: f64 = 12.1;
pub const CHORD_END: f64 = 2.0 * LINE_MIN;
pub const LINE_COST: f64 = 0.5;

struct Prefix {
    sx: Vec<f64>,
    sy: Vec<f64>,
    sxx: Vec<f64>,
    sxy: Vec<f64>,
    syy: Vec<f64>,
}

fn prefix(pts: &[P]) -> Prefix {
    let n = pts.len();
    let mut p = Prefix { sx: vec![0.0; n + 1], sy: vec![0.0; n + 1], sxx: vec![0.0; n + 1], sxy: vec![0.0; n + 1], syy: vec![0.0; n + 1] };
    for (k, q) in pts.iter().enumerate() {
        p.sx[k + 1] = p.sx[k] + q[0];
        p.sy[k + 1] = p.sy[k] + q[1];
        p.sxx[k + 1] = p.sxx[k] + q[0] * q[0];
        p.sxy[k + 1] = p.sxy[k] + q[0] * q[1];
        p.syy[k + 1] = p.syy[k] + q[1] * q[1];
    }
    p
}

/// Centre, unit direction and RMS residual of the TLS line through pts[i..=j].
fn tls_from_prefix(p: &Prefix, i: usize, j: usize) -> (P, P, f64) {
    let n = (j + 1 - i) as f64;
    let sx = p.sx[j + 1] - p.sx[i];
    let sy = p.sy[j + 1] - p.sy[i];
    let sxx = p.sxx[j + 1] - p.sxx[i];
    let sxy = p.sxy[j + 1] - p.sxy[i];
    let syy = p.syy[j + 1] - p.syy[i];
    let (mx, my) = (sx / n, sy / n);
    let (cxx, cxy, cyy) = (sxx / n - mx * mx, sxy / n - mx * my, syy / n - my * my);
    let half = (cxx + cyy) / 2.0;
    let spread = ((cxx - cyy) / 2.0).hypot(cxy);
    let lam_min = (half - spread).max(0.0);
    let lam_max = half + spread;
    let mut d: P = if cxy.abs() > 1e-15 {
        [lam_max - cyy, cxy]
    } else if cxx >= cyy {
        [1.0, 0.0]
    } else {
        [0.0, 1.0]
    };
    let norm = d[0].hypot(d[1]);
    if norm > 0.0 {
        d = [d[0] / norm, d[1] / norm];
    } else {
        d = [1.0, 0.0];
    }
    ([mx, my], d, lam_min.sqrt())
}

/// numpy's default (linear) 98th percentile.
fn percentile98(values: &mut Vec<f64>) -> f64 {
    values.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = values.len();
    if n == 1 {
        return values[0];
    }
    let pos = 0.98 * (n - 1) as f64;
    let lo = pos.floor() as usize;
    let frac = pos - lo as f64;
    let hi = (lo + 1).min(n - 1);
    values[lo] + (values[hi] - values[lo]) * frac
}

/// How much the run bows: the quadratic term of a parabola fitted to the
/// residuals along the run, as its rise over the run's half-length. Same
/// arithmetic as the Python `_sag`.
fn sag(pts: &[P], c: P, d: P) -> f64 {
    let n = pts.len() as f64;
    let along: Vec<f64> = pts.iter().map(|p| (p[0] - c[0]) * d[0] + (p[1] - c[1]) * d[1]).collect();
    let off: Vec<f64> = pts.iter().map(|p| (p[0] - c[0]) * (-d[1]) + (p[1] - c[1]) * d[0]).collect();
    let mean = along.iter().sum::<f64>() / n;
    let s: Vec<f64> = along.iter().map(|a| a - mean).collect();
    let (mut s2sum, mut s3sum, mut s4sum, mut o, mut o1, mut o2) = (0.0f64, 0.0f64, 0.0f64, 0.0f64, 0.0f64, 0.0f64);
    for (k, sk) in s.iter().enumerate() {
        let s2 = sk * sk;
        s2sum += s2;
        s3sum += s2 * sk;
        s4sum += s2 * s2;
        o += off[k];
        o1 += off[k] * sk;
        o2 += off[k] * s2;
    }
    let det = s4sum * (s2sum * n) - s3sum * (s3sum * n) + s2sum * (0.0 - s2sum * s2sum);
    if det.abs() < 1e-18 {
        return 0.0;
    }
    let a = (o2 * (s2sum * n) - s3sum * (o1 * n) + s2sum * (0.0 - o * s2sum)) / det;
    let lo = along.iter().cloned().fold(f64::INFINITY, f64::min);
    let hi = along.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let half = 0.5 * (hi - lo);
    a.abs() * half * half
}

pub struct Run {
    pub i: usize,
    pub j: usize,
    pub c: P,
    pub d: P,
}

/// Maximal straight runs of an open polyline. See the Python `line_runs`.
pub fn line_runs(pts: &[P]) -> Vec<Run> {
    let n = pts.len();
    if n < 3 {
        return Vec::new();
    }
    let mut cum = vec![0.0f64; n];
    for k in 1..n {
        cum[k] = cum[k - 1] + ((pts[k][0] - pts[k - 1][0]).powi(2) + (pts[k][1] - pts[k - 1][1]).powi(2)).sqrt();
    }
    let p = prefix(pts);
    let mut runs = Vec::new();
    let mut i = 0usize;
    while i + 2 < n {
        let mut best: Option<(usize, P, P)> = None;
        let mut j = i + 2;
        while j < n {
            let (c, d, rms) = tls_from_prefix(&p, i, j);
            if rms > LINE_RMS {
                break;
            }
            best = Some((j, c, d));
            j += 1;
        }
        if let Some((mut j, mut c, mut d)) = best {
            while j > i + 1 {
                let mut off: Vec<f64> = (i..=j).map(|k| ((pts[k][0] - c[0]) * (-d[1]) + (pts[k][1] - c[1]) * d[0]).abs()).collect();
                if percentile98(&mut off) <= LINE_P98 {
                    break;
                }
                j -= 1;
                let (c2, d2, _) = tls_from_prefix(&p, i, j);
                c = c2;
                d = d2;
            }
            // A run that grew into the start of a bend bows; shed the far end
            // until what is left is straight. See the Python.
            while j > i + 1 && sag(&pts[i..=j], c, d) > LINE_SAG {
                j -= 1;
                let (c2, d2, _) = tls_from_prefix(&p, i, j);
                c = c2;
                d = d2;
            }
            // A run may also begin inside a bend. Shed end vertices off the line. See the Python.
            let mut a = i;
            while j > a + 1 {
                let head = ((pts[a][0] - c[0]) * (-d[1]) + (pts[a][1] - c[1]) * d[0]).abs();
                let tail = ((pts[j][0] - c[0]) * (-d[1]) + (pts[j][1] - c[1]) * d[0]).abs();
                if head <= LINE_END && tail <= LINE_END {
                    break;
                }
                if head > tail {
                    a += 1;
                } else {
                    j -= 1;
                }
                let (c2, d2, _) = tls_from_prefix(&p, a, j);
                c = c2;
                d = d2;
            }
            if j > a + 1 && cum[j] - cum[a] >= LINE_MIN {
                // point the direction along the run: see the Python
                if (pts[j][0] - pts[a][0]) * d[0] + (pts[j][1] - pts[a][1]) * d[1] < 0.0 {
                    d = [-d[0], -d[1]];
                }
                runs.push(Run { i: a, j, c, d });
                i = j;
                continue;
            }
        }
        i += 1;
    }
    runs
}

fn project(c: P, d: P, q: P) -> P {
    let t = (q[0] - c[0]) * d[0] + (q[1] - c[1]) * d[1];
    [c[0] + d[0] * t, c[1] + d[1] * t]
}

fn turn_deg(a: P, b: P) -> f64 {
    (a[0] * b[0] + a[1] * b[1]).clamp(-1.0, 1.0).acos().to_degrees()
}

pub fn dist(a: P, b: P) -> f64 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt()
}

fn seg_start(s: &Segment) -> P {
    match s {
        Segment::Line { p0, .. } => *p0,
        Segment::Cubic { p0, .. } => *p0,
        Segment::Arc { p0, .. } => *p0,
    }
}

fn seg_end(s: &Segment) -> P {
    match s {
        Segment::Line { p1, .. } => *p1,
        Segment::Cubic { p1, .. } => *p1,
        Segment::Arc { p1, .. } => *p1,
    }
}

fn set_start(s: &mut Segment, q: P) {
    match s {
        Segment::Line { p0, .. } => *p0 = q,
        Segment::Cubic { p0, .. } => *p0 = q,
        Segment::Arc { p0, .. } => *p0 = q,
    }
}

fn set_end(s: &mut Segment, q: P) {
    match s {
        Segment::Line { p1, .. } => *p1 = q,
        Segment::Cubic { p1, .. } => *p1 = q,
        Segment::Arc { p1, .. } => *p1 = q,
    }
}

/// The stretch as its straight runs, each one Line, with the gaps fitted as
/// cubics. See the Python `lines_first`. None where no run was found.
pub fn lines_first(pts: &[P], tol: f64, t_start: Option<P>, t_end: Option<P>) -> Option<Vec<Segment>> {
    let runs = line_runs(pts);
    if runs.is_empty() {
        return None;
    }
    let n = pts.len();
    let p = prefix(pts);
    let mut cum = vec![0.0f64; n];
    for k in 1..n {
        cum[k] = cum[k - 1] + ((pts[k][0] - pts[k - 1][0]).powi(2) + (pts[k][1] - pts[k - 1][1]).powi(2)).sqrt();
    }
    // merge touching, nearly collinear runs
    let mut merged: Vec<Run> = Vec::new();
    for run in runs {
        if let Some(last) = merged.last() {
            if turn_deg(last.d, run.d) <= MERGE_DEG && cum[run.i] - cum[last.j] <= CHORD_GAP {
                let (c, d, rms) = tls_from_prefix(&p, last.i, run.j);
                if rms <= LINE_RMS {
                    let i0 = last.i;
                    merged.pop();
                    merged.push(Run { i: i0, j: run.j, c, d });
                    continue;
                }
            }
        }
        merged.push(run);
    }
    let mut runs = merged;
    // chords of a curve go back to being curve: see the Python.
    if runs.len() > 1 {
        let small: Vec<bool> = (0..runs.len() - 1)
            .map(|k| cum[runs[k + 1].i] - cum[runs[k].j] <= CHORD_GAP && turn_deg(runs[k].d, runs[k + 1].d) <= CHORD_TURN)
            .collect();
        let mut keep = Vec::new();
        for (k, run) in runs.into_iter().enumerate() {
            let before = k > 0 && small[k - 1];
            let after = k < small.len() && small[k];
            let length = dist(pts[run.j], pts[run.i]);
            if before && after {
                continue;
            }
            if (before || after) && length < CHORD_END {
                continue;
            }
            keep.push(run);
        }
        runs = keep;
        if runs.is_empty() {
            return None;
        }
    }

    let on_line = |a: usize, b: usize, c: P, d: P| -> bool {
        (a..=b).all(|k| ((pts[k][0] - c[0]) * (-d[1]) + (pts[k][1] - c[1]) * d[0]).abs() <= LINE_P98)
    };
    let snaps = |end: P, a: usize, b: usize, c: P, d: P| -> bool {
        let off = ((end[0] - c[0]) * (-d[1]) + (end[1] - c[1]) * d[0]).abs();
        cum[b] - cum[a] < CORNER_REACH && off <= SNAP_END
    };
    // Cubics through pts[a..=b]; None means "a corner": the two lines meet.
    let gap = |a: usize, b: usize, ta: Option<P>, tb: Option<P>| -> Option<Vec<Segment>> {
        let run = &pts[a..=b];
        if run.len() < 2 || dist(run[run.len() - 1], run[0]) < 1e-9 {
            return Some(Vec::new());
        }
        if dist(run[run.len() - 1], run[0]) < CORNER_GAP {
            if ta.is_some() && tb.is_some() {
                return None;
            }
            return Some(vec![Segment::Line { p0: run[0], p1: run[run.len() - 1] }]);
        }
        if run.len() == 2 {
            return Some(vec![Segment::Line { p0: run[0], p1: run[1] }]);
        }
        let t1 = ta.unwrap_or_else(|| end_tangent(run, true));
        let t2 = tb.unwrap_or_else(|| end_tangent(run, false));
        if let Some(arcs) = fit_arc_run(run, tol, ta, tb) {
            return Some(arcs);
        }
        Some(fit_cubics(run, t1, t2, tol, 0))
    };

    let mut segs: Vec<Segment> = Vec::new();
    let mut prev: Option<(P, P, P)> = None; // end point, direction, centre of the last line
    let mut cursor = 0usize;
    for run in &runs {
        let (i, j, c) = (run.i, run.j, run.c);
        let mut d = run.d;
        if (pts[j][0] - pts[i][0]) * d[0] + (pts[j][1] - pts[i][1]) * d[1] < 0.0 {
            d = [-d[0], -d[1]];
        }
        let mut start = if i == 0 { pts[0] } else { project(c, d, pts[i]) };
        let end = if j == n - 1 { pts[n - 1] } else { project(c, d, pts[j]) };
        if i > cursor && cursor == 0 {
            if on_line(0, i, c, d) || snaps(pts[0], 0, i, c, d) {
                start = pts[0];
            } else {
                match gap(0, i, t_start, Some([-d[0], -d[1]])) {
                    Some(mut before) if !before.is_empty() => {
                        let k = before.len() - 1;
                        set_end(&mut before[k], start);
                        segs.extend(before);
                    }
                    _ => segs.push(Segment::Line { p0: pts[0], p1: start }),
                }
            }
        } else if i > cursor {
            let absorbed = prev.is_some_and(|(_, pd, pc)| on_line(cursor, i, pc, pd)) || on_line(cursor, i, c, d);
            let before = if absorbed && prev.is_some() {
                None
            } else {
                gap(cursor, i, if cursor == 0 { t_start } else { prev.map(|v| v.1) }, Some([-d[0], -d[1]]))
            };
            match before {
                None => {
                    let (pe, pd, _) = prev.unwrap();
                    match intersect(pe, pd, c, d) {
                        Some(x) if dist(x, pts[i]) <= 3.0 => {
                            let last = segs.len() - 1;
                            set_end(&mut segs[last], x);
                            start = x;
                        }
                        _ => {
                            let from = seg_end(segs.last().unwrap());
                            segs.push(Segment::Line { p0: from, p1: start });
                        }
                    }
                }
                Some(mut before) => {
                    if !before.is_empty() {
                        if let Some(last) = segs.last() {
                            let from = seg_end(last);
                            set_start(&mut before[0], from);
                        }
                        let k = before.len() - 1;
                        set_end(&mut before[k], start);
                    }
                    segs.extend(before);
                }
            }
        } else if let Some(last) = segs.last() {
            start = seg_end(last);
        }
        segs.push(Segment::Line { p0: start, p1: end });
        prev = Some((end, d, c));
        cursor = j;
    }
    if cursor < n - 1 {
        let (pe, pd, pc) = prev.unwrap();
        if on_line(cursor, n - 1, pc, pd) || snaps(pts[n - 1], cursor, n - 1, pc, pd) {
            let last = segs.len() - 1;
            if matches!(segs[last], Segment::Line { .. }) {
                set_end(&mut segs[last], pts[n - 1]);
            } else {
                segs.push(Segment::Line { p0: pe, p1: pts[n - 1] });
            }
        } else {
            match gap(cursor, n - 1, Some(pd), t_end) {
                Some(mut after) if !after.is_empty() => {
                    set_start(&mut after[0], pe);
                    let k = after.len() - 1;
                    set_end(&mut after[k], pts[n - 1]);
                    segs.extend(after);
                }
                _ => segs.push(Segment::Line { p0: pe, p1: pts[n - 1] }),
            }
        }
    }
    let first = seg_start(&segs[0]);
    if dist(first, pts[0]) > 1e-9 {
        if matches!(segs[0], Segment::Line { .. }) && dist(first, pts[0]) <= 3.0 {
            set_start(&mut segs[0], pts[0]);
        } else {
            segs.insert(0, Segment::Line { p0: pts[0], p1: first });
        }
    }
    Some(merge_lines(segs))
}

/// Fold a stub shorter than GAP_MIN, or a line within MERGE_DEG of the line
/// before it, into that line. See the Python.
pub fn merge_lines(mut segs: Vec<Segment>) -> Vec<Segment> {
    // See the Python `merge_lines`.
    let mut out: Vec<Segment> = Vec::new();
    let mut k = 0usize;
    while k < segs.len() {
        let seg = segs[k].clone();
        if let (Some(Segment::Line { p0: q0, p1: q1 }), Segment::Line { p0, p1 }) = (out.last().cloned(), &seg) {
            let a = [q1[0] - q0[0], q1[1] - q0[1]];
            let b = [p1[0] - p0[0], p1[1] - p0[1]];
            let la = a[0].hypot(a[1]);
            let lb = b[0].hypot(b[1]);
            if la > 0.0 && lb > 0.0 {
                let da = [a[0] / la, a[1] / la];
                let db = [b[0] / lb, b[1] / lb];
                let off_end = ((p1[0] - q0[0]) * (-da[1]) + (p1[1] - q0[1]) * da[0]).abs();
                let joined = [p1[0] - q0[0], p1[1] - q0[1]];
                let lj = joined[0].hypot(joined[1]);
                let joint_off = if lj > 0.0 { ((p0[0] - q0[0]) * (-joined[1]) + (p0[1] - q0[1]) * joined[0]).abs() / lj } else { 0.0 };
                if (turn_deg(da, db) <= MERGE_DEG && joint_off <= LINE_END) || (lb < GAP_MIN && off_end <= LINE_END) {
                    let last = out.len() - 1;
                    out[last] = Segment::Line { p0: q0, p1: *p1 };
                    k += 1;
                    continue;
                }
                if lb < GAP_MIN && k + 1 < segs.len() {
                    if let Segment::Line { p0: n0, p1: n1 } = segs[k + 1].clone() {
                        let c = [n1[0] - n0[0], n1[1] - n0[1]];
                        let lc = c[0].hypot(c[1]);
                        if lc > 0.0 {
                            if let Some(x) = intersect(q0, da, n0, [c[0] / lc, c[1] / lc]) {
                                if dist(x, *p0) <= 3.0 {
                                    let last = out.len() - 1;
                                    out[last] = Segment::Line { p0: q0, p1: x };
                                    segs[k + 1] = Segment::Line { p0: x, p1: n1 };
                                    k += 1;
                                    continue;
                                }
                            }
                        }
                    }
                }
            }
        }
        out.push(seg);
        k += 1;
    }
    out
}

fn cost(segs: &[Segment]) -> f64 {
    segs.iter().map(|s| if matches!(s, Segment::Line { .. }) { LINE_COST } else { 1.0 }).sum()
}

/// Move each corner shared by two pieces to where their adjacent line runs
/// cross. See the Python `corners_from_runs`.
pub fn corners_from_runs(pieces: &mut [Vec<P>], closed: bool) {
    let n = pieces.len();
    if n < 2 {
        return;
    }
    let runs: Vec<Vec<Run>> = pieces.iter().map(|p| line_runs(p)).collect();
    let range: Vec<usize> = if closed { (0..n).collect() } else { (1..n).collect() };
    for idx in range {
        let prev_idx = (idx + n - 1) % n;
        let (rp, rn) = (&runs[prev_idx], &runs[idx]);
        let prev = &pieces[prev_idx];
        let nxt = &pieces[idx];
        let corner = prev[prev.len() - 1];
        let mut line_prev: Option<(P, P)> = None;
        let mut line_next: Option<(P, P)> = None;
        let (circ_prev, circ_next) = (whole_circle(prev), whole_circle(nxt));
        if let (Some(last), None) = (rp.last(), circ_prev) {
            let tail: f64 = prev[last.j..].windows(2).map(|w| dist(w[0], w[1])).sum();
            if tail <= CORNER_REACH {
                line_prev = Some((last.c, last.d));
            }
        }
        if let (Some(first), None) = (rn.first(), circ_next) {
            let head: f64 = nxt[..=first.i].windows(2).map(|w| dist(w[0], w[1])).sum();
            if head <= CORNER_REACH {
                line_next = Some((first.c, first.d));
            }
        }
        let x = match (line_prev, line_next) {
            (Some((c0, d0)), Some((c1, d1))) => intersect(c0, d0, c1, d1),
            (Some((c0, d0)), None) => circ_next.or_else(|| circle_near(nxt, true)).and_then(|(c, r)| line_circle(c0, d0, c, r, corner)),
            (None, Some((c1, d1))) => circ_prev.or_else(|| circle_near(prev, false)).and_then(|(c, r)| line_circle(c1, d1, c, r, corner)),
            (None, None) => continue,
        };
        let Some(x) = x else { continue };
        if dist(x, corner) > 1.5 {
            continue;
        }
        let lp = pieces[prev_idx].len() - 1;
        pieces[prev_idx][lp] = x;
        pieces[idx][0] = x;
    }
}

pub const CORNER_CIRCLE_DEV: f64 = 0.15;
pub const CORNER_CIRCLE_MIN_DEG: f64 = 10.0;
pub const CORNER_CIRCLE_REACH: f64 = 30.0;

/// The circle a whole piece runs on, when it is one. See the Python `_whole_circle`.
fn whole_circle(piece: &[P]) -> Option<(P, f64)> {
    if piece.len() < 6 {
        return None;
    }
    let length: f64 = piece.windows(2).map(|w| dist(w[0], w[1])).sum();
    if length < ARC_MIN_CHORD {
        return None;
    }
    let (circle, dev) = fit_circle(piece);
    let Shape::Circle { cx, cy, r } = circle else { return None };
    if !dev.is_finite() || dev > CORNER_CIRCLE_DEV || r <= 2.0 {
        return None;
    }
    let c = [cx, cy];
    let n = piece.len();
    let a0 = (piece[0][1] - c[1]).atan2(piece[0][0] - c[0]);
    let a1 = (piece[n - 1][1] - c[1]).atan2(piece[n - 1][0] - c[0]);
    let tau = 2.0 * std::f64::consts::PI;
    let span = ((a1 - a0 + std::f64::consts::PI).rem_euclid(tau) - std::f64::consts::PI).abs();
    if span.to_degrees() < CORNER_CIRCLE_MIN_DEG {
        return None;
    }
    Some((c, r))
}

/// The circle a piece runs on at one end. See the Python `_circle_near`.
fn circle_near(piece: &[P], from_start: bool) -> Option<(P, f64)> {
    let pts: Vec<P> = if from_start { piece.to_vec() } else { piece.iter().rev().cloned().collect() };
    let mut cum = vec![0.0f64];
    for w in pts.windows(2) {
        cum.push(cum[cum.len() - 1] + dist(w[0], w[1]));
    }
    let reach_m = cum.iter().position(|&v| v > CORNER_CIRCLE_REACH).unwrap_or(cum.len());
    for m in [pts.len(), reach_m] {
        if m < 6 || cum[m.min(cum.len()) - 1] < ARC_MIN_CHORD {
            continue;
        }
        let seg = &pts[..m];
        let (circle, dev) = fit_circle(seg);
        let Shape::Circle { cx, cy, r } = circle else { continue };
        if !dev.is_finite() || dev > CORNER_CIRCLE_DEV || r <= 2.0 {
            continue;
        }
        let c = [cx, cy];
        let a0 = (seg[0][1] - c[1]).atan2(seg[0][0] - c[0]);
        let a1 = (seg[m - 1][1] - c[1]).atan2(seg[m - 1][0] - c[0]);
        let tau = 2.0 * std::f64::consts::PI;
        let span = ((a1 - a0 + std::f64::consts::PI).rem_euclid(tau) - std::f64::consts::PI).abs();
        if span.to_degrees() < CORNER_CIRCLE_MIN_DEG {
            continue;
        }
        return Some((c, r));
    }
    None
}

/// Where the line through p along unit d cuts the circle (c, r): the crossing nearest `near`.
fn line_circle(p: P, d: P, c: P, r: f64, near: P) -> Option<P> {
    let q = sub(p, c);
    let b = 2.0 * (q[0] * d[0] + q[1] * d[1]);
    let cc = q[0] * q[0] + q[1] * q[1] - r * r;
    let disc = b * b - 4.0 * cc;
    if disc < 0.0 {
        return None;
    }
    let root = disc.sqrt();
    let mut best: Option<(f64, P)> = None;
    for t in [(-b - root) / 2.0, (-b + root) / 2.0] {
        let x = [p[0] + d[0] * t, p[1] + d[1] * t];
        let dd = dist(x, near);
        if best.map_or(true, |(bd, _)| dd < bd) {
            best = Some((dd, x));
        }
    }
    best.map(|(_, x)| x)
}

/// One run between two breaks, as lines first or as a curve. See the Python `fit_stretch`.
pub const ARC_MIN_CHORD: f64 = 6.0;
pub const ARC_MIN_DEG: f64 = 10.0;
pub const ARC_TANGENT_DEG: f64 = 3.0;
/// See the Python: arcs stay under this so the implied centre is well conditioned.
pub const ARC_MAX_DEG: f64 = 150.0;

/// The arc of circle (c, r) from p0 to p1 in the sweep direction, as arcs of at most ARC_MAX_DEG.
pub fn split_arc(c: P, r: f64, p0: P, p1: P, sweep: bool) -> Vec<Segment> {
    let tau = 2.0 * std::f64::consts::PI;
    let a0 = (p0[1] - c[1]).atan2(p0[0] - c[0]);
    let a1 = (p1[1] - c[1]).atan2(p1[0] - c[0]);
    let mut span = if sweep { (a1 - a0).rem_euclid(tau) } else { -((a0 - a1).rem_euclid(tau)) };
    if span.abs() < 1e-12 {
        span = if sweep { tau } else { -tau };
    }
    let pieces = ((span.abs().to_degrees() / ARC_MAX_DEG - 1e-9).ceil() as usize).max(1);
    let mut out = Vec::with_capacity(pieces);
    let mut start = p0;
    for k in 1..=pieces {
        let end = if k == pieces {
            p1
        } else {
            let a = a0 + span * k as f64 / pieces as f64;
            [c[0] + r * a.cos(), c[1] + r * a.sin()]
        };
        out.push(Segment::Arc { p0: start, p1: end, r, large: span.abs() / pieces as f64 > std::f64::consts::PI, sweep });
        start = end;
    }
    out
}

fn arc_tangent(c: P, p: P, sweep: bool) -> P {
    let u = sub(p, c);
    normalize(if sweep { [-u[1], u[0]] } else { [u[1], -u[0]] })
}

pub const CIRCLE_SEARCH_ITER: usize = 60;

/// The circle through p0 and p1 closest to `pts`. See the Python `circle_through`.
pub fn circle_through(p0: P, p1: P, pts: &[P], guess: P) -> (P, f64, f64, f64) {
    let mid = [0.5 * (p0[0] + p1[0]), 0.5 * (p0[1] + p1[1])];
    let d = sub(p1, p0);
    let half = 0.5 * norm(d);
    let nrm = [-d[1] / (2.0 * half), d[0] / (2.0 * half)];
    let cost = |t: f64| -> f64 {
        let c = [mid[0] + nrm[0] * t, mid[1] + nrm[1] * t];
        let r = (c[0] - p0[0]).hypot(c[1] - p0[1]);
        pts.iter()
            .map(|p| {
                let e = (p[0] - c[0]).hypot(p[1] - c[1]) - r;
                e * e
            })
            .sum::<f64>()
    };
    let g = sub(guess, mid);
    let t0 = g[0] * nrm[0] + g[1] * nrm[1];
    let span = t0.abs().max(half) + half;
    let (mut lo, mut hi) = (t0 - span, t0 + span);
    let phi = (5.0f64.sqrt() - 1.0) / 2.0;
    let (mut a, mut b) = (hi - phi * (hi - lo), lo + phi * (hi - lo));
    let (mut fa, mut fb) = (cost(a), cost(b));
    for _ in 0..CIRCLE_SEARCH_ITER {
        if fa < fb {
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
    let t = 0.5 * (lo + hi);
    let c = [mid[0] + nrm[0] * t, mid[1] + nrm[1] * t];
    let r = (c[0] - p0[0]).hypot(c[1] - p0[1]);
    let mut dev: Vec<f64> = pts.iter().map(|p| ((p[0] - c[0]).hypot(p[1] - c[1]) - r).abs()).collect();
    let worst = dev.iter().cloned().fold(0.0f64, f64::max);
    (c, r, percentile95(&mut dev), worst)
}

/// One circular arc through a run, when the run is one. See the Python `fit_arc_run`.
pub fn fit_arc_run(pts: &[P], tol: f64, t_start: Option<P>, t_end: Option<P>) -> Option<Vec<Segment>> {
    let n = pts.len();
    if n < 5 {
        return None;
    }
    let (p0, p1) = (pts[0], pts[n - 1]);
    let chord = norm(sub(p1, p0));
    if chord < ARC_MIN_CHORD {
        return None;
    }
    let (free, dev) = fit_circle(pts);
    let Shape::Circle { cx, cy, r: free_r } = free else { return None };
    if !dev.is_finite() || dev > tol || free_r <= 1.0 {
        return None;
    }
    let (c, r, dev, worst) = circle_through(p0, p1, pts, [cx, cy]);
    if dev > tol || worst > 2.0 * tol || r <= 1.0 {
        return None;
    }
    let m = pts[n / 2];
    let sweep = (p0[0] - c[0]) * (m[1] - c[1]) - (p0[1] - c[1]) * (m[0] - c[0]) > 0.0;
    let a0 = (p0[1] - c[1]).atan2(p0[0] - c[0]);
    let a1 = (p1[1] - c[1]).atan2(p1[0] - c[0]);
    let tau = 2.0 * std::f64::consts::PI;
    let span = if sweep { (a1 - a0).rem_euclid(tau) } else { (a0 - a1).rem_euclid(tau) };
    if span.to_degrees() < ARC_MIN_DEG || span > tau - 1e-6 {
        return None;
    }
    if let Some(t) = t_start {
        if turn_deg(normalize(t), arc_tangent(c, p0, sweep)) > ARC_TANGENT_DEG {
            return None;
        }
    }
    if let Some(t) = t_end {
        let e = arc_tangent(c, p1, sweep);
        if turn_deg(normalize(t), [-e[0], -e[1]]) > ARC_TANGENT_DEG {
            return None;
        }
    }
    Some(split_arc(c, r, p0, p1, sweep))
}

pub fn fit_stretch(pts: &[P], tol: f64, t_start: Option<P>, t_end: Option<P>) -> Vec<Segment> {
    if pts.len() < 2 {
        return Vec::new();
    }
    let curve = if t_start.is_none() && t_end.is_none() {
        fit_open(pts, tol, None, None)
    } else {
        let t1 = t_start.unwrap_or_else(|| end_tangent(pts, true));
        let t2 = t_end.unwrap_or_else(|| end_tangent(pts, false));
        fit_cubics(pts, t1, t2, tol, 0)
    };
    let best = match lines_first(pts, tol, t_start, t_end) {
        Some(lines) if cost(&lines) <= cost(&curve) => lines,
        _ => curve,
    };
    if let Some(arcs) = fit_arc_run(pts, tol, t_start, t_end) {
        if cost(&arcs) <= cost(&best) {
            return arcs;
        }
    }
    best
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

/// Corner radii of one rounded rect agree to this share of the radius (or 2·tol).
pub const ROUND_RADIUS_TOL: f64 = 0.05;

fn median(v: &mut Vec<f64>) -> f64 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = v.len();
    if n % 2 == 1 {
        v[n / 2]
    } else {
        0.5 * (v[n / 2 - 1] + v[n / 2])
    }
}

/// Four axis-aligned straight runs joined by four tangent quarter circles of
/// one radius: an SVG `<rect rx>`. See the Python `try_rounded_rect`.
pub fn try_rounded_rect(poly: &[P], params: &CurveParams) -> Option<Shape> {
    let n = poly.len();
    if n < 16 {
        return None;
    }
    let mean = [poly.iter().map(|p| p[0]).sum::<f64>() / n as f64, poly.iter().map(|p| p[1]).sum::<f64>() / n as f64];
    let mut start = 0;
    let mut best = f64::NEG_INFINITY;
    for (k, p) in poly.iter().enumerate() {
        let d = norm(sub(*p, mean));
        if d > best {
            best = d;
            start = k;
        }
    }
    let mut rolled: Vec<P> = Vec::with_capacity(n + 1);
    rolled.extend_from_slice(&poly[start..]);
    rolled.extend_from_slice(&poly[..start]);
    let mut closed = rolled.clone();
    closed.push(rolled[0]);
    let runs = line_runs(&closed);
    if runs.len() != 4 {
        return None;
    }
    let mut axes: Vec<usize> = Vec::with_capacity(4);
    let mut levels: Vec<f64> = Vec::with_capacity(4);
    for run in &runs {
        let ang = run.d[1].atan2(run.d[0]).to_degrees().rem_euclid(180.0);
        if ang.min(180.0 - ang) <= params.snap_axis_deg {
            axes.push(0);
            levels.push(run.c[1]);
        } else if (ang - 90.0).abs() <= params.snap_axis_deg {
            axes.push(1);
            levels.push(run.c[0]);
        } else {
            return None;
        }
    }
    if axes[0] == axes[1] || axes[1] == axes[2] || axes[2] == axes[3] || axes[3] == axes[0] {
        return None;
    }
    let mut ys: Vec<f64> = (0..4).filter(|&k| axes[k] == 0).map(|k| levels[k]).collect();
    let mut xs: Vec<f64> = (0..4).filter(|&k| axes[k] == 1).map(|k| levels[k]).collect();
    if xs.len() != 2 || ys.len() != 2 {
        return None;
    }
    xs.sort_by(|a, b| a.partial_cmp(b).unwrap());
    ys.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let (x0, x1) = (xs[0], xs[1]);
    let (y0, y1) = (ys[0], ys[1]);
    if x1 - x0 < 2.0 || y1 - y0 < 2.0 {
        return None;
    }
    let mut radii: Vec<f64> = Vec::with_capacity(4);
    for k in 0..4 {
        let j = runs[k].j;
        let i_next = runs[(k + 1) % 4].i;
        let gap: Vec<P> = if k < 3 {
            rolled[j..=i_next].to_vec()
        } else {
            let mut g = rolled[j..].to_vec();
            g.extend_from_slice(&rolled[..=runs[0].i]);
            g
        };
        let (lv_a, lv_b) = (levels[k], levels[(k + 1) % 4]);
        let x_side = if axes[k] == 1 { lv_a } else { lv_b };
        let y_side = if axes[k] == 0 { lv_a } else { lv_b };
        let sx = if (x_side - x0).abs() <= (x_side - x1).abs() { 1.0 } else { -1.0 };
        let sy = if (y_side - y0).abs() <= (y_side - y1).abs() { 1.0 } else { -1.0 };
        let floor = params.tol.max(0.5);
        let mut rs: Vec<f64> = Vec::new();
        let mut on_arc: Vec<P> = Vec::new();
        for p in &gap {
            let u = (sx * (p[0] - x_side)).max(0.0);
            let v = (sy * (p[1] - y_side)).max(0.0);
            if u.min(v) > floor {
                rs.push((u + v) + (2.0 * u * v).sqrt());
                on_arc.push(*p);
            }
        }
        if on_arc.len() < 3 {
            return None;
        }
        let r = median(&mut rs);
        if r <= 1.0 {
            return None;
        }
        let centre = [x_side + sx * r, y_side + sy * r];
        let mut dev: Vec<f64> = on_arc.iter().map(|p| (norm(sub(*p, centre)) - r).abs()).collect();
        if percentile95(&mut dev) > params.tol {
            return None;
        }
        radii.push(r);
    }
    let rx = radii.iter().sum::<f64>() / 4.0;
    let (rmax, rmin) = (radii.iter().cloned().fold(f64::NEG_INFINITY, f64::max), radii.iter().cloned().fold(f64::INFINITY, f64::min));
    if rmax - rmin > (2.0 * params.tol).max(ROUND_RADIUS_TOL * rx) {
        return None;
    }
    let rx = rx.min((x1 - x0) / 2.0).min((y1 - y0) / 2.0);
    Some(Shape::RoundedRect { x: x0, y: y0, w: x1 - x0, h: y1 - y0, rx })
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

/// px: interior errors this close to the worst count as tied. See the Python `_max_error`.
pub const SPLIT_TIE: f64 = 1e-9;

/// The worst distance from the curve, and the interior vertex to split at:
/// among the vertices tied for worst, the one nearest the middle of the run
/// (the lower index if two are equally near). See the Python `_max_error`.
fn max_error(points: &[P], c: &(P, P, P, P), u: &[f64]) -> (f64, usize) {
    let d: Vec<f64> = u
        .iter()
        .enumerate()
        .map(|(i, t)| norm(sub(bezier(c.0, c.1, c.2, c.3, *t), points[i])))
        .collect();
    let split = if d.len() > 2 {
        let top = d[1..d.len() - 1].iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let mid = (d.len() - 1) as f64 / 2.0;
        let mut best = 1usize;
        let mut best_key = f64::INFINITY;
        for i in 1..d.len() - 1 {
            if d[i] >= top - SPLIT_TIE {
                let key = (i as f64 - mid).abs();
                if key < best_key {
                    best_key = key;
                    best = i;
                }
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

pub const SPLINE_MIN: usize = 16;
pub const SPLINE_SPANS: usize = 24;
pub const SPLINE_ROUNDS: usize = 5;
pub const SPLINE_CROWD: f64 = 0.2;
/// See the Python: knots move toward the error a few times; a bumpy span gets
/// one more knot or the spline is declined.
pub const EQUALISE_ROUNDS: usize = 4;
pub const BUMP_RATIO: f64 = 0.85;

/// Clamped cubic knot vector over [0, 1].
fn knot_vector(interior: &[f64]) -> Vec<f64> {
    let mut kv = vec![0.0; 4];
    kv.extend_from_slice(interior);
    kv.extend_from_slice(&[1.0; 4]);
    kv
}

/// Cox-de Boor, one row per sample and one column per control point.
fn bspline_basis(u: &[f64], knots: &[f64], n_ctrl: usize) -> Vec<Vec<f64>> {
    let m = u.len();
    let mut cur = vec![vec![0.0f64; knots.len() - 1]; m];
    for (i, &uu) in u.iter().enumerate() {
        for j in 0..knots.len() - 1 {
            if knots[j + 1] > knots[j] && uu >= knots[j] && uu < knots[j + 1] {
                cur[i][j] = 1.0;
            }
        }
    }
    let last = (0..knots.len() - 1).rfind(|&j| knots[j + 1] > knots[j]).unwrap();
    let end = knots[knots.len() - 1] - 1e-12;
    for (i, &uu) in u.iter().enumerate() {
        if uu >= end {
            cur[i][last] = 1.0;
        }
    }
    for degree in 1..4 {
        let cols = cur[0].len() - 1;
        let mut nxt = vec![vec![0.0f64; cols]; m];
        for j in 0..cols {
            let lo = knots[j + degree] - knots[j];
            let hi = knots[j + degree + 1] - knots[j + 1];
            for i in 0..m {
                if lo > 0.0 {
                    nxt[i][j] += (u[i] - knots[j]) / lo * cur[i][j];
                }
                if hi > 0.0 {
                    nxt[i][j] += (knots[j + degree + 1] - u[i]) / hi * cur[i][j + 1];
                }
            }
        }
        cur = nxt;
    }
    for row in cur.iter_mut() {
        row.truncate(n_ctrl);
    }
    cur
}

/// Gauss-Jordan with partial pivoting on the augmented matrix, written the long
/// way round so this and the Python side do the same arithmetic.
fn gauss_solve(mut a: Vec<Vec<f64>>) -> Option<Vec<f64>> {
    let n = a.len();
    for col in 0..n {
        let mut pivot = col;
        for row in col + 1..n {
            if a[row][col].abs() > a[pivot][col].abs() {
                pivot = row;
            }
        }
        if a[pivot][col].abs() < 1e-12 {
            return None;
        }
        a.swap(col, pivot);
        let lead = a[col][col];
        for v in a[col].iter_mut() {
            *v /= lead;
        }
        for row in 0..n {
            if row != col && a[row][col] != 0.0 {
                let f = a[row][col];
                for k in 0..=n {
                    a[row][k] -= f * a[col][k];
                }
            }
        }
    }
    Some((0..n).map(|i| a[i][n]).collect())
}

/// Least-squares control points, with both ends and both end tangents pinned.
///
/// The first and last control points are the run's own ends; the second and the
/// second to last are free only along the pinned tangents, which is what keeps
/// the join to the neighbouring arc smooth. Everything between is free.
fn spline_controls(
    points: &[P],
    u: &[f64],
    knots: &[f64],
    n_ctrl: usize,
    t1: P,
    t2: P,
) -> Option<Vec<P>> {
    let basis = bspline_basis(u, knots, n_ctrl);
    let head = points[0];
    let tail = points[points.len() - 1];
    let free = n_ctrl - 4;
    let nx = 2 + 2 * free;
    let m = u.len();
    let mut design = vec![vec![0.0f64; nx]; 2 * m];
    let mut want = vec![0.0f64; 2 * m];
    for i in 0..m {
        let b = &basis[i];
        design[2 * i][0] = b[1] * t1[0];
        design[2 * i + 1][0] = b[1] * t1[1];
        design[2 * i][1] = b[n_ctrl - 2] * t2[0];
        design[2 * i + 1][1] = b[n_ctrl - 2] * t2[1];
        for k in 0..free {
            design[2 * i][2 + 2 * k] = b[2 + k];
            design[2 * i + 1][3 + 2 * k] = b[2 + k];
        }
        let at_head = b[0] + b[1];
        let at_tail = b[n_ctrl - 2] + b[n_ctrl - 1];
        want[2 * i] = points[i][0] - (at_head * head[0] + at_tail * tail[0]);
        want[2 * i + 1] = points[i][1] - (at_head * head[1] + at_tail * tail[1]);
    }
    // The normal equations. Each row of the design matrix has at most a handful
    // of non-zero columns - a cubic basis function covers four spans and no more
    // - so only those pairs are accumulated. Skipping a zero term adds nothing
    // and changes nothing; for a row of a hundred columns it is the difference
    // between a second and no time at all.
    let mut aug = vec![vec![0.0f64; nx + 1]; nx];
    for (i, row) in design.iter().enumerate() {
        let live: Vec<usize> = (0..nx).filter(|&c| row[c] != 0.0).collect();
        for &r in live.iter() {
            for &c in live.iter() {
                aug[r][c] += row[r] * row[c];
            }
            aug[r][nx] += row[r] * want[i];
        }
    }
    let x = gauss_solve(aug)?;
    if x.iter().any(|v| !v.is_finite()) {
        return None;
    }
    let mut ctrl = vec![[0.0f64; 2]; n_ctrl];
    ctrl[0] = head;
    ctrl[n_ctrl - 1] = tail;
    ctrl[1] = [head[0] + t1[0] * x[0], head[1] + t1[1] * x[0]];
    ctrl[n_ctrl - 2] = [tail[0] + t2[0] * x[1], tail[1] + t2[1] * x[1]];
    for k in 0..free {
        ctrl[2 + k] = [x[2 + 2 * k], x[3 + 2 * k]];
    }
    Some(ctrl)
}

/// Boehm's knot insertion, once, leaving the curve exactly where it was.
fn insert_knot(ctrl: &[P], knots: &[f64], at: f64) -> (Vec<P>, Vec<f64>) {
    let k = knots.partition_point(|&v| v <= at) - 1;
    let mut out: Vec<P> = ctrl[..k - 2].to_vec();
    for i in k - 2..=k {
        let span = knots[i + 3] - knots[i];
        let w = if span <= 0.0 { 0.0 } else { (at - knots[i]) / span };
        out.push([
            (1.0 - w) * ctrl[i - 1][0] + w * ctrl[i][0],
            (1.0 - w) * ctrl[i - 1][1] + w * ctrl[i][1],
        ]);
    }
    out.extend_from_slice(&ctrl[k..]);
    let mut kv = knots.to_vec();
    kv.insert(k + 1, at);
    (out, kv)
}

/// The spline's spans as Bezier segments: insert each interior knot until it is
/// threefold, and every four control points are then one cubic.
fn spline_cubics(ctrl: &[P], knots: &[f64]) -> Vec<Segment> {
    let mut ctrl = ctrl.to_vec();
    let mut knots = knots.to_vec();
    let mut interior: Vec<f64> = knots[4..knots.len() - 4].to_vec();
    interior.sort_by(|a, b| a.partial_cmp(b).unwrap());
    interior.dedup();
    for at in interior {
        loop {
            let seen = knots.iter().filter(|&&k| (k - at).abs() <= 1e-8 + 1e-5 * at.abs()).count();
            if seen >= 3 {
                break;
            }
            let (c, k) = insert_knot(&ctrl, &knots, at);
            ctrl = c;
            knots = k;
        }
    }
    (0..ctrl.len() / 3)
        .map(|i| Segment::Cubic {
            p0: ctrl[3 * i],
            c1: ctrl[3 * i + 1],
            c2: ctrl[3 * i + 2],
            p1: ctrl[3 * i + 3],
        })
        .collect()
}

fn spline_at(ctrl: &[P], knots: &[f64], u: &[f64]) -> Vec<P> {
    let basis = bspline_basis(u, knots, ctrl.len());
    basis
        .iter()
        .map(|b| {
            let mut q = [0.0f64; 2];
            for (j, c) in ctrl.iter().enumerate() {
                q[0] += b[j] * c[0];
                q[1] += b[j] * c[1];
            }
            q
        })
        .collect()
}

fn reparametrize_spline(points: &[P], ctrl: &[P], knots: &[f64], u: &[f64]) -> Vec<f64> {
    let step = 1e-4;
    let up: Vec<f64> = u.iter().map(|&v| (v + step).clamp(0.0, 1.0)).collect();
    let um: Vec<f64> = u.iter().map(|&v| (v - step).clamp(0.0, 1.0)).collect();
    let here = spline_at(ctrl, knots, u);
    let ahead = spline_at(ctrl, knots, &up);
    let behind = spline_at(ctrl, knots, &um);
    (0..u.len())
        .map(|i| {
            let q = [here[i][0] - points[i][0], here[i][1] - points[i][1]];
            let gap = up[i] - um[i];
            let d1 = [(ahead[i][0] - behind[i][0]) / gap, (ahead[i][1] - behind[i][1]) / gap];
            let d2 = [
                (ahead[i][0] - 2.0 * here[i][0] + behind[i][0]) / (step * step),
                (ahead[i][1] - 2.0 * here[i][1] + behind[i][1]) / (step * step),
            ];
            let num = q[0] * d1[0] + q[1] * d1[1];
            let den = d1[0] * d1[0] + d1[1] * d1[1] + q[0] * d2[0] + q[1] * d2[1];
            let move_by = if den.abs() > 1e-12 { num / den } else { 0.0 };
            (u[i] - move_by).clamp(0.0, 1.0)
        })
        .collect()
}

/// Fit a run as one clamped cubic B-spline, returned as its Bezier spans.
///
/// A chain built by splitting and recursing is only G1 where it joins: the two
/// halves are handed the same tangent direction, but nothing ties their
/// curvature, so the curve can bend one way and then abruptly the other at a
/// point that is not a corner. That is the hitch. A cubic B-spline is C2
/// everywhere by construction, so between one corner and the next it cannot
/// kink at all, however many spans it takes. Returns None where no spline
/// inside `tol` was found, and the split-and-recurse fit answers instead.
pub fn fit_c2(points: &[P], t1: P, t2: P, tol: f64) -> Option<Vec<Segment>> {
    let mut walk = vec![0.0f64; points.len()];
    for i in 1..points.len() {
        walk[i] = walk[i - 1] + norm(sub(points[i], points[i - 1]));
    }
    let total = walk[points.len() - 1];
    let u: Vec<f64> = if total > 0.0 {
        walk.iter().map(|v| v / total).collect()
    } else {
        (0..points.len()).map(|i| i as f64 / (points.len() - 1) as f64).collect()
    };
    let mut interior: Vec<f64> = Vec::new();
    for _ in 0..SPLINE_SPANS {
        let n_ctrl = interior.len() + 4;
        if points.len() < n_ctrl + 1 {
            return None;
        }
        let knots = knot_vector(&interior);
        let mut moved = u.clone();
        let mut worst = 0usize;
        for _ in 0..SPLINE_ROUNDS {
            let ctrl = spline_controls(points, &moved, &knots, n_ctrl, t1, t2)?;
            let drawn = spline_at(&ctrl, &knots, &moved);
            let mut far = -1.0;
            for i in 0..points.len() {
                let d = norm(sub(drawn[i], points[i]));
                if d > far {
                    far = d;
                    worst = i;
                }
            }
            if far < tol {
                return finish_spline(points, &u, interior.clone(), ctrl, knots, moved, t1, t2, tol);
            }
            moved = reparametrize_spline(points, &ctrl, &knots, &moved);
        }
        // One more span, cut where the fit is furthest out - the same place the
        // split-and-recurse fit would have cut, except that the spline stays one
        // curve across it. Crowding a knot against its neighbour buys no freedom
        // and makes the solve ill-conditioned, so a cut landing near one halves
        // the span instead.
        let mut cut = u[worst].clamp(0.0, 1.0);
        let lo = interior.iter().filter(|&&k| k < cut).fold(0.0f64, |a, &b| a.max(b));
        let hi = interior.iter().filter(|&&k| k > cut).fold(1.0f64, |a, &b| a.min(b));
        if hi - lo < 1e-6 {
            return None;
        }
        if cut - lo < SPLINE_CROWD * (hi - lo) || hi - cut < SPLINE_CROWD * (hi - lo) {
            cut = 0.5 * (lo + hi);
        }
        if interior.iter().any(|&k| (cut - k).abs() < 1e-9) {
            return None;
        }
        interior.push(cut);
        interior.sort_by(|a, b| a.partial_cmp(b).unwrap());
    }
    None
}

/// The largest point error in each span, spans bounded by the interior knots.
fn span_errors(points: &[P], moved: &[f64], knots: &[f64], ctrl: &[P], interior: &[f64]) -> Vec<f64> {
    let drawn = spline_at(ctrl, knots, moved);
    let mut edges = vec![0.0];
    edges.extend_from_slice(interior);
    edges.push(1.0);
    let mut out = vec![0.0f64; edges.len() - 1];
    for k in 0..edges.len() - 1 {
        for i in 0..points.len() {
            if moved[i] >= edges[k] && moved[i] <= edges[k + 1] {
                let d = norm(sub(drawn[i], points[i]));
                if d > out[k] {
                    out[k] = d;
                }
            }
        }
    }
    out
}

/// Fit the spline with these knots; Some((ctrl, knots, moved)) when inside `tol`.
fn solve_spans(points: &[P], u: &[f64], interior: &[f64], t1: P, t2: P, tol: f64) -> Option<(Vec<P>, Vec<f64>, Vec<f64>)> {
    let n_ctrl = interior.len() + 4;
    let knots = knot_vector(interior);
    let mut moved = u.to_vec();
    for _ in 0..SPLINE_ROUNDS {
        let ctrl = spline_controls(points, &moved, &knots, n_ctrl, t1, t2)?;
        let drawn = spline_at(&ctrl, &knots, &moved);
        let far = (0..points.len()).map(|i| norm(sub(drawn[i], points[i]))).fold(0.0f64, f64::max);
        if far < tol {
            return Some((ctrl, knots, moved));
        }
        moved = reparametrize_spline(points, &ctrl, &knots, &moved);
    }
    None
}

fn bumpy(cubics: &[Segment]) -> Option<usize> {
    for (k, c) in cubics.iter().enumerate() {
        if let Segment::Cubic { p0, c1, c2, p1 } = c {
            let chord = norm(sub(*p1, *p0));
            if chord > 1e-9 && (norm(sub(*c1, *p0)) > BUMP_RATIO * chord || norm(sub(*c2, *p1)) > BUMP_RATIO * chord) {
                return Some(k);
            }
        }
    }
    None
}

/// Even out the per-span error, then refuse a bumpy span. See the Python `_finish_spline`.
fn finish_spline(points: &[P], u: &[f64], mut interior: Vec<f64>, mut ctrl: Vec<P>, mut knots: Vec<f64>, mut moved: Vec<f64>, t1: P, t2: P, tol: f64) -> Option<Vec<Segment>> {
    if !interior.is_empty() {
        let mut errs = span_errors(points, &moved, &knots, &ctrl, &interior);
        for _ in 0..EQUALISE_ROUNDS {
            let mut edges = vec![0.0];
            edges.extend_from_slice(&interior);
            edges.push(1.0);
            let mut trial: Vec<f64> = Vec::with_capacity(interior.len());
            for (k, knot) in interior.iter().enumerate() {
                let (left, right) = (errs[k], errs[k + 1]);
                if left + right <= 1e-12 {
                    trial.push(*knot);
                    continue;
                }
                let width = (knot - edges[k]).min(edges[k + 2] - knot);
                trial.push(knot + 0.25 * (right - left) / (right + left) * width);
            }
            let Some((c2, k2, m2)) = solve_spans(points, u, &trial, t1, t2, tol) else { break };
            let e2 = span_errors(points, &m2, &k2, &c2, &trial);
            let (max2, min2) = (e2.iter().cloned().fold(f64::NEG_INFINITY, f64::max), e2.iter().cloned().fold(f64::INFINITY, f64::min));
            let (max1, min1) = (errs.iter().cloned().fold(f64::NEG_INFINITY, f64::max), errs.iter().cloned().fold(f64::INFINITY, f64::min));
            if max2 < tol && (max2 - min2) < (max1 - min1) {
                ctrl = c2;
                knots = k2;
                moved = m2;
                interior = trial;
                errs = e2;
            } else {
                break;
            }
        }
    }
    let _ = &moved;
    let mut cubics = spline_cubics(&ctrl, &knots);
    if let Some(bump) = bumpy(&cubics) {
        let mut edges = vec![0.0];
        edges.extend_from_slice(&interior);
        edges.push(1.0);
        let mid = 0.5 * (edges[bump] + edges[bump + 1]);
        if interior.iter().any(|k| (mid - k).abs() < 1e-9) {
            return None;
        }
        let mut more = interior.clone();
        more.push(mid);
        more.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let (c3, k3, _m3) = solve_spans(points, u, &more, t1, t2, tol)?;
        cubics = spline_cubics(&c3, &k3);
        if bumpy(&cubics).is_some() {
            return None;
        }
    }
    Some(cubics)
}

pub const SPLIT_REACH: usize = 2;
pub const TANGENT_SCATTER: f64 = 0.10;

/// Which way the curve is going at `points[at]`, from a least-squares line in
/// arc length through the few vertices either side of it.
///
/// The obvious answer - the chord from one neighbour to the other - is a chord
/// two pixels long, and two pixels of a sub-pixel outline is mostly noise. The
/// join then leaves a few degrees off the direction the curve is really
/// travelling, and that is the hitch you see when you zoom in. A longer chord
/// is steadier, but it leans towards its own two ends where the curve bends,
/// and the vertices are not evenly spaced, so it leans unevenly. Fitting a line
/// against arc length uses all five and weights them by where they actually
/// fell. Fitting a quadratic instead was tried: with five vertices it spends
/// its freedom following the noise rather than smoothing it.
fn local_tangent(points: &[P], at: usize, reach: usize) -> P {
    let chord = normalize(sub(points[at + 1], points[at - 1]));
    let r = reach.min(at).min(points.len() - 1 - at);
    if r < 2 {
        return chord;
    }
    let w = &points[at - r..=at + r];
    let n = w.len();
    let mut s = vec![0.0f64; n];
    for i in 1..n {
        s[i] = s[i - 1] + norm(sub(w[i], w[i - 1]));
    }
    if s[n - 1] <= 1e-9 {
        return chord;
    }
    // Written as plain sums, in this order, so this and the Python side do the
    // same arithmetic and land on the same bits.
    let mut total = 0.0;
    for &v in &s {
        total += v;
    }
    let mean = total / n as f64;
    for v in s.iter_mut() {
        *v -= mean;
    }
    let mut got = [0.0f64; 2];
    for (axis, out) in got.iter_mut().enumerate() {
        let mut total = 0.0;
        for p in w.iter() {
            total += p[axis];
        }
        let mean = total / n as f64;
        let mut acc = 0.0;
        for (i, p) in w.iter().enumerate() {
            acc += s[i] * (p[axis] - mean);
        }
        *out = acc;
    }
    if got[0] * got[0] + got[1] * got[1] <= 1e-18 {
        return chord;
    }
    let got = normalize(got);
    // Only where the five really do lie on a line. Where the placed vertices
    // scramble - a small circle on a small canvas is the usual case - a line
    // through them is a line through noise, and has come out pointing back the
    // way the curve came. Real curvature over four pixels stays well inside
    // this: a circle of radius ten sags a twentieth of a pixel.
    let mut centre = [0.0f64; 2];
    for (axis, out) in centre.iter_mut().enumerate() {
        let mut total = 0.0;
        for p in w.iter() {
            total += p[axis];
        }
        *out = total / n as f64;
    }
    let mut acc = 0.0;
    for p in w.iter() {
        let d = [p[0] - centre[0], p[1] - centre[1]];
        let along = d[0] * got[0] + d[1] * got[1];
        let off = [d[0] - along * got[0], d[1] - along * got[1]];
        acc += off[0] * off[0];
        acc += off[1] * off[1];
    }
    if (acc / n as f64).sqrt() <= TANGENT_SCATTER {
        got
    } else {
        chord
    }
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
    if depth == 0 && points.len() >= SPLINE_MIN {
        if let Some(spline) = fit_c2(points, t1, t2, tol) {
            return spline;
        }
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
    let t = local_tangent(points, split, SPLIT_REACH);
    let centre_t = [-t[0], -t[1]];
    let mut left = fit_cubics(&points[..split + 1], t1, centre_t, tol, depth + 1);
    let right = fit_cubics(&points[split..], [-centre_t[0], -centre_t[1]], t2, tol, depth + 1);
    left.extend(right);
    left
}

pub fn end_tangent(points: &[P], at_start: bool) -> P {
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

pub fn line_through(points: &[P]) -> (P, P) {
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

pub fn intersect(p: P, d: P, q: P, e: P) -> Option<P> {
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
        return fit_closed(poly, params.tol);
    }
    let mut pieces: Vec<Vec<P>> = split_pieces(poly, &corners).into_iter().filter(|p| p.len() >= 2).collect();
    corners_from_runs(&mut pieces, true);
    let mut segments = Vec::new();
    for piece in &pieces {
        segments.extend(fit_stretch(piece, params.tol, None, None));
    }
    snap_axis_lines(segments, params.snap_axis_deg)
}

/// Closed contour without corners: lines first where it has straight runs,
/// else the smooth closed fit. See the Python `fit_closed`.
pub fn fit_closed(poly: &[P], tol: f64) -> Vec<Segment> {
    let n = poly.len();
    if n >= 8 {
        // a loop that is one circle: two half arcs (see the Python)
        let (circle, dev) = fit_circle(poly);
        if let Shape::Circle { cx, cy, r } = circle {
            if dev.is_finite() && dev <= tol && r > 1.0 {
                let c = [cx, cy];
                let worst = poly.iter().map(|p| (norm(sub(*p, c)) - r).abs()).fold(0.0f64, f64::max);
                if worst <= 2.0 * tol {
                    let mut area2 = 0.0;
                    for k in 0..n {
                        let (a, b) = (poly[k], poly[(k + 1) % n]);
                        area2 += a[0] * b[1] - a[1] * b[0];
                    }
                    let sweep = area2 > 0.0;
                    let u = sub(poly[0], c);
                    let k = r / norm(u).max(1e-12);
                    let p0 = [c[0] + u[0] * k, c[1] + u[1] * k];
                    return split_arc(c, r, p0, p0, sweep);
                }
            }
        }
    }
    let smooth = fit_closed_smooth(poly, tol);
    if n < 4 {
        return smooth;
    }
    let mut closed: Vec<P> = poly.to_vec();
    closed.push(poly[0]);
    let runs = line_runs(&closed);
    if runs.is_empty() {
        return smooth;
    }
    // open the loop in the middle of the longest run: see the Python
    // the first of the longest runs, as Python's max() picks on a tie
    let mut longest = &runs[0];
    for r in &runs {
        if r.j - r.i > longest.j - longest.i {
            longest = r;
        }
    }
    let start = ((longest.i + longest.j) / 2) % n;
    let mut rolled: Vec<P> = poly[start..].to_vec();
    rolled.extend_from_slice(&poly[..start]);
    rolled.push(poly[start]);
    let Some(mut lines) = lines_first(&rolled, tol, None, None) else {
        return smooth;
    };
    if lines.len() > 1 {
        let last = lines.len() - 1;
        if let (Segment::Line { p0: a0, p1: a1 }, Segment::Line { p0: b0, p1: b1 }) = (lines[0].clone(), lines[last].clone()) {
            let a = [a1[0] - a0[0], a1[1] - a0[1]];
            let b = [b1[0] - b0[0], b1[1] - b0[1]];
            let la = a[0].hypot(a[1]);
            let lb = b[0].hypot(b[1]);
            if la > 0.0 && lb > 0.0 && turn_deg([a[0] / la, a[1] / la], [b[0] / lb, b[1] / lb]) <= MERGE_DEG {
                lines[last] = Segment::Line { p0: b0, p1: a1 };
                lines.remove(0);
            }
        }
    }
    if cost(&lines) <= cost(&smooth) {
        lines
    } else {
        smooth
    }
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
        if corners.is_empty() {
            if let Some(rounded) = try_rounded_rect(poly, params) {
                return rounded;
            }
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
                Segment::Arc { p1, r, large, sweep, .. } => {
                    let rs = fmt(*r, precision);
                    parts.push_str(&format!(
                        "A{} {} 0 {} {} {} {}",
                        rs,
                        rs,
                        *large as u8,
                        *sweep as u8,
                        fmt(p1[0], precision),
                        fmt(p1[1], precision)
                    ));
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
        Shape::RoundedRect { x, y, w, h, rx } => format!(
            "<rect x=\"{}\" y=\"{}\" width=\"{}\" height=\"{}\" rx=\"{}\" {}/>",
            fmt(*x, p),
            fmt(*y, p),
            fmt(*w, p),
            fmt(*h, p),
            fmt(*rx, p),
            attrs
        ),
        Shape::Path { contours } => {
            let rule = if contours.len() > 1 { " fill-rule=\"evenodd\"" } else { "" };
            format!("<path d=\"{}\" {}{}/>", path_d(contours, p), attrs, rule)
        }
    }
}

#[cfg(test)]
mod line_tests {
    use super::*;

    fn kinds(segs: &[Segment]) -> String {
        segs.iter().map(|s| if matches!(s, Segment::Line { .. }) { 'L' } else { 'C' }).collect()
    }

    #[test]
    fn a_noisy_edge_with_bad_ends_is_one_line() {
        // deterministic "noise": a small sawtooth, ends pushed off the line
        let mut pts: Vec<P> = (0..143).map(|k| { let t = k as f64 / 142.0; [100.0 * t, 3.0 * t + 0.05 * ((k % 5) as f64 - 2.0) / 2.0] }).collect();
        pts[0][1] += 0.35;
        pts[142][1] -= 0.3;
        let runs = line_runs(&pts);
        assert_eq!(runs.len(), 1, "{:?}", runs.iter().map(|r| (r.i, r.j)).collect::<Vec<_>>());
        let segs = fit_stretch(&pts, 0.4, None, None);
        assert_eq!(kinds(&segs), "L");
        assert_eq!(segs[0].start(), pts[0]);
    }

    #[test]
    fn a_quarter_circle_stays_a_curve() {
        for r in [30.0f64, 120.0, 300.0] {
            let n = (2.0 * r) as usize;
            let pts: Vec<P> = (0..n).map(|k| { let a = std::f64::consts::FRAC_PI_2 * k as f64 / (n - 1) as f64; [r * a.cos(), r * a.sin()] }).collect();
            assert!(!kinds(&fit_stretch(&pts, 0.4, None, None)).contains('L'), "r={r}");
        }
    }

    #[test]
    fn a_rounded_corner_is_line_curve_line() {
        let mut pts: Vec<P> = (0..60).map(|k| [40.0 * k as f64 / 59.0, 0.0]).collect();
        let r = 10.0f64;
        for k in 1..25 {
            let a = -std::f64::consts::FRAC_PI_2 + std::f64::consts::FRAC_PI_2 * k as f64 / 24.0;
            pts.push([40.0 + r * a.cos(), r + r * a.sin()]);
        }
        for k in 1..60 {
            pts.push([40.0 + r, r + 40.0 * k as f64 / 59.0]);
        }
        let k = kinds(&fit_stretch(&pts, 0.4, None, None));
        assert!(k.starts_with('L') && k.ends_with('L') && k.contains('C') && k.matches('L').count() == 2, "{k}");
    }
}
