//! Stage 10b: repeated shapes as `<use>`. Mirrors `reuse.py`.

use crate::curves::{fmt, shape_svg, Segment, Shape, P};
use crate::symmetry::Grid;

pub const USE_TOL: f64 = 0.10;
pub const USE_MIN_POINTS: usize = 8;

pub(crate) fn arc_centre(p0: P, p1: P, r: f64, large: bool, sweep: bool) -> P {
    let mid = [0.5 * (p0[0] + p1[0]), 0.5 * (p0[1] + p1[1])];
    let d = [p1[0] - p0[0], p1[1] - p0[1]];
    let half = 0.5 * (d[0] * d[0] + d[1] * d[1]).sqrt();
    if half < 1e-12 {
        return mid;
    }
    let r = r.max(half);
    let h = (r * r - half * half).max(0.0).sqrt();
    let n = [-d[1] / (2.0 * half), d[0] / (2.0 * half)];
    if sweep != large { [mid[0] + n[0] * h, mid[1] + n[1] * h] } else { [mid[0] - n[0] * h, mid[1] - n[1] * h] }
}

/// `n` points along an arc, ends included. Mirrors the Python `arc_points`.
pub fn arc_points(p0: P, p1: P, r: f64, large: bool, sweep: bool, n: usize) -> Vec<P> {
    let c = arc_centre(p0, p1, r, large, sweep);
    let a0 = (p0[1] - c[1]).atan2(p0[0] - c[0]);
    let a1 = (p1[1] - c[1]).atan2(p1[0] - c[0]);
    let tau = 2.0 * std::f64::consts::PI;
    let span = if sweep { (a1 - a0).rem_euclid(tau) } else { -((a0 - a1).rem_euclid(tau)) };
    let rr = ((p0[0] - c[0]).powi(2) + (p0[1] - c[1]).powi(2)).sqrt();
    (0..n)
        .map(|k| {
            let t = a0 + span * k as f64 / (n - 1) as f64;
            [c[0] + rr * t.cos(), c[1] + rr * t.sin()]
        })
        .collect()
}

fn sample(contours: &[Vec<Segment>]) -> Vec<P> {
    let per = 12usize;
    let mut out = Vec::new();
    for contour in contours {
        for seg in contour {
            match seg {
                Segment::Line { p0, p1 } => {
                    for k in 0..per {
                        let t = k as f64 / (per - 1) as f64;
                        out.push([p0[0] * (1.0 - t) + p1[0] * t, p0[1] * (1.0 - t) + p1[1] * t]);
                    }
                }
                Segment::Arc { p0, p1, r, large, sweep } => out.extend(arc_points(*p0, *p1, *r, *large, *sweep, per)),
                Segment::Cubic { p0, c1, c2, p1 } => {
                    for k in 0..per {
                        let t = k as f64 / (per - 1) as f64;
                        let mt = 1.0 - t;
                        let (a, b, c, d) = (mt * mt * mt, 3.0 * mt * mt * t, 3.0 * mt * t * t, t * t * t);
                        out.push([a * p0[0] + b * c1[0] + c * c2[0] + d * p1[0], a * p0[1] + b * c1[1] + c * c2[1] + d * p1[1]]);
                    }
                }
            }
        }
    }
    out
}

fn signature(shape: &Shape) -> String {
    match shape {
        Shape::Path { contours } => {
            let mut s = String::from("path:");
            for c in contours {
                for seg in c {
                    s.push(match seg {
                        Segment::Line { .. } => 'L',
                        Segment::Cubic { .. } => 'C',
                        Segment::Arc { .. } => 'A',
                    });
                }
                s.push('|');
            }
            s
        }
        Shape::Circle { .. } => "Circle".into(),
        Shape::Ellipse { .. } => "Ellipse".into(),
        Shape::Rect { .. } => "Rect".into(),
        Shape::RoundedRect { .. } => "RoundedRect".into(),
    }
}

fn bbox_min(pts: &[P]) -> P {
    let mut m = [f64::INFINITY, f64::INFINITY];
    for p in pts {
        m[0] = m[0].min(p[0]);
        m[1] = m[1].min(p[1]);
    }
    m
}

fn chamfer(from: &[P], to: &[P]) -> f64 {
    let grid = Grid::new(to);
    from.iter().map(|p| grid.nearest(*p).0).sum::<f64>() / from.len() as f64
}

/// The translation carrying `a` onto `b` when they are copies. See the Python `_same`.
fn same(a: &Shape, b: &Shape, pa: Option<&Vec<P>>, pb: Option<&Vec<P>>) -> Option<P> {
    if signature(a) != signature(b) {
        return None;
    }
    match (a, b) {
        (Shape::Circle { cx, cy, r }, Shape::Circle { cx: bx, cy: by, r: br }) => {
            if (r - br).abs() <= USE_TOL { Some([bx - cx, by - cy]) } else { None }
        }
        (Shape::Ellipse { cx, cy, rx, ry, angle_deg }, Shape::Ellipse { cx: bx, cy: by, rx: brx, ry: bry, angle_deg: bang }) => {
            if (rx - brx).abs() <= USE_TOL && (ry - bry).abs() <= USE_TOL && (angle_deg - bang).abs() <= 0.5 { Some([bx - cx, by - cy]) } else { None }
        }
        (Shape::Rect { x, y, w, h }, Shape::Rect { x: bx, y: by, w: bw, h: bh }) => {
            if (w - bw).abs() <= USE_TOL && (h - bh).abs() <= USE_TOL { Some([bx - x, by - y]) } else { None }
        }
        (Shape::RoundedRect { x, y, w, h, rx }, Shape::RoundedRect { x: bx, y: by, w: bw, h: bh, rx: brx }) => {
            if (w - bw).abs() <= USE_TOL && (h - bh).abs() <= USE_TOL && (rx - brx).abs() <= USE_TOL { Some([bx - x, by - y]) } else { None }
        }
        (Shape::Path { .. }, Shape::Path { .. }) => {
            let (pa, pb) = (pa?, pb?);
            if pa.len() < USE_MIN_POINTS || pb.len() != pa.len() {
                return None;
            }
            let (ma, mb) = (bbox_min(pa), bbox_min(pb));
            let shift = [mb[0] - ma[0], mb[1] - ma[1]];
            let moved: Vec<P> = pa.iter().map(|p| [p[0] + shift[0], p[1] + shift[1]]).collect();
            if chamfer(&moved, pb) > USE_TOL || chamfer(pb, &moved) > USE_TOL {
                return None;
            }
            Some(shift)
        }
        _ => None,
    }
}

/// `pending` is (shape, attrs) in paint order. Returns (defs, elements). See the Python `emit`.
pub fn emit(pending: &[(Shape, String)], precision: usize, first_id: usize) -> (Vec<String>, Vec<String>) {
    let samples: Vec<Option<Vec<P>>> = pending
        .iter()
        .map(|(s, _)| if let Shape::Path { contours } = s { Some(sample(contours)) } else { None })
        .collect();
    let n = pending.len();
    let mut group_of: Vec<Option<usize>> = vec![None; n];
    let mut shifts: Vec<Option<P>> = vec![None; n];
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for i in 0..n {
        if group_of[i].is_some() {
            continue;
        }
        let mut members = vec![i];
        for j in i + 1..n {
            if group_of[j].is_some() {
                continue;
            }
            if let Some(shift) = same(&pending[i].0, &pending[j].0, samples[i].as_ref(), samples[j].as_ref()) {
                group_of[j] = Some(groups.len());
                shifts[j] = Some(shift);
                members.push(j);
            }
        }
        if members.len() > 1 {
            group_of[i] = Some(groups.len());
            shifts[i] = Some([0.0, 0.0]);
            groups.push(members);
        }
    }
    let mut defs = Vec::new();
    let mut ids: Vec<String> = Vec::new();
    for (g, members) in groups.iter().enumerate() {
        let sid = format!("u{}", first_id + g);
        defs.push(shape_svg(&pending[members[0]].0, &format!("id=\"{}\"", sid), precision));
        ids.push(sid);
    }
    let mut elements = Vec::with_capacity(n);
    for (i, (shape, attrs)) in pending.iter().enumerate() {
        match group_of[i] {
            None => elements.push(shape_svg(shape, attrs, precision)),
            Some(g) => {
                let [dx, dy] = shifts[i].unwrap();
                let pos = if dx.abs() < 1e-9 && dy.abs() < 1e-9 {
                    String::new()
                } else {
                    format!(" x=\"{}\" y=\"{}\"", fmt(dx, precision), fmt(dy, precision))
                };
                elements.push(format!("<use href=\"#{}\"{} {}/>", ids[g], pos, attrs));
            }
        }
    }
    (defs, elements)
}
