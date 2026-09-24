//! Stage 7a, the graph side: every ring that is an axis-aligned rounded
//! rectangle made regular and written back into the shared arcs, and a rounded
//! corner between two lines anywhere else made one circle.
//!
//! Mirrors the Python `topology._rectify`, `_fillets` and their helpers
//! (`_ring_vertices`, `_nodes`, `_runs_along`, `_decide_corner`,
//! `_apply_cusp`, `_node_fillet`, `_guides`, `_snap_levels`, `_set_length`,
//! `_regular_models`, `_settle_corners`, `_write_rect`, `_resample`,
//! `_resample_corner`, `_fillet_holds`, `_move_node`) step for step, with every
//! tie broken as the Python breaks it. The geometry is `crate::rects`.
//!
//! `log`, when given, receives one row of numbers per decision, in the same
//! layout the Python writes, for `tools/diffcheck.py`'s `rects` stage.

use std::collections::{HashMap, HashSet};

use super::{Arc, Boundary, NODE_TRIM};
use crate::core::grid::Image;
use crate::curves::{fit_cubics, intersect, normalize, reverse_segments, CurveParams, Segment, Shape, P};
use crate::rects::{self, linspace01, np_median, np_percentile, np_sum, py_mod, py_sum, Model, NODE_ON};

pub const GUIDE_MIN: f64 = 12.0;
pub const FINAL_SLACK: f64 = 1.5;
pub const CUSP_ALONG_DEG: f64 = 10.0;
pub const CUSP_REACH: f64 = 4.0;
pub const RESAMPLE_STEP: f64 = 1.0;
pub const FILLET_SPAN: f64 = 12.0;
pub const FILLET_TURN: (f64, f64) = (30.0, 150.0);
pub const FILLET_LINE_KEEP: f64 = 1.0;

/// The coordinate each side (x0, x1, y0, y1) fixes.
const SIDE_AXIS: [usize; 4] = [0, 0, 1, 1];

pub type Log = Vec<Vec<f64>>;

#[inline]
fn norm(v: P) -> f64 {
    (v[0] * v[0] + v[1] * v[1]).sqrt()
}

#[inline]
fn sub(a: P, b: P) -> P {
    [a[0] - b[0], a[1] - b[1]]
}

#[inline]
fn dot(a: P, b: P) -> f64 {
    a[0] * b[0] + a[1] * b[1]
}

#[inline]
fn same(a: P, b: P) -> bool {
    a[0] == b[0] && a[1] == b[1]
}

/// `np.argmin` of the distances from `q` to `pts`: the first nearest.
fn nearest(pts: &[P], q: P) -> usize {
    let mut best = 0usize;
    let mut bd = f64::INFINITY;
    for (k, p) in pts.iter().enumerate() {
        let d = norm(sub(*p, q));
        if d < bd {
            bd = d;
            best = k;
        }
    }
    best
}

/// `np.sign`.
fn sign(x: f64) -> f64 {
    if x > 0.0 {
        1.0
    } else if x < 0.0 {
        -1.0
    } else {
        0.0
    }
}

/// `arc_points` as the Python computes it (angles on `np.linspace`).
fn arc_points(p0: P, p1: P, r: f64, large: bool, sweep: bool, n: usize) -> Vec<P> {
    let c = crate::reuse::arc_centre(p0, p1, r, large, sweep);
    let a0 = (p0[1] - c[1]).atan2(p0[0] - c[0]);
    let a1 = (p1[1] - c[1]).atan2(p1[0] - c[0]);
    let tau = 2.0 * std::f64::consts::PI;
    let span = if sweep { py_mod(a1 - a0, tau) } else { -py_mod(a0 - a1, tau) };
    let rr = norm(sub(p0, c));
    linspace01(n)
        .into_iter()
        .map(|t| {
            let a = a0 + span * t;
            [c[0] + rr * a.cos(), c[1] + rr * a.sin()]
        })
        .collect()
}

fn seg_arc_points(seg: &Segment, n: usize) -> Vec<P> {
    match seg {
        Segment::Arc { p0, p1, r, large, sweep } => arc_points(*p0, *p1, *r, *large, *sweep, n),
        _ => unreachable!("an arc"),
    }
}

/// Length of a polyline as numpy sums it (pairwise).
fn np_length(pts: &[P]) -> f64 {
    let d: Vec<f64> = pts.windows(2).map(|w| norm(sub(w[1], w[0]))).collect();
    np_sum(&d)
}

/// `np.concatenate([[0], np.cumsum(|diff|)])`.
fn cumulative(pts: &[P]) -> Vec<f64> {
    let mut cum = vec![0.0f64; pts.len()];
    for k in 1..pts.len() {
        cum[k] = cum[k - 1] + norm(sub(pts[k], pts[k - 1]));
    }
    cum
}

/// Vertices within an arc's trims of its ends are not believed.
fn believed(arc: &Arc) -> Vec<bool> {
    if arc.closed() {
        return vec![true; arc.pts.len()];
    }
    let cum = cumulative(&arc.pts);
    let last = *cum.last().unwrap_or(&0.0);
    cum.iter().map(|c| !(*c < arc.trim0 || last - *c < arc.trim1)).collect()
}

#[derive(Clone, Debug)]
struct NodeCorner {
    point: P,
    corner: usize,
    /// The sides (0 x0, 1 x1, 2 y0, 3 y1) whose line the node is on.
    sides: Vec<usize>,
    /// Ring positions of the arcs arriving at and leaving the node.
    before: usize,
    after: usize,
}

#[derive(Clone, Debug)]
struct RectCandidate {
    ring: Vec<(usize, bool)>,
    poly: Vec<P>,
    trusted: Vec<bool>,
    model: Model,
    clockwise: bool,
    /// Per side, the coordinates of the nodes on it.
    pinned: [Vec<f64>; 4],
    node_corners: Vec<NodeCorner>,
    sigma: f64,
}

/// A cusp: the node slides along `side` to the corner's tangent point.
#[derive(Clone, Copy, Debug)]
struct Plan {
    side: usize,
    s_idx: usize,
    c_idx: usize,
    o_idx: usize,
    /// The outside arc meets the node with its last vertex.
    o_last: bool,
    carries: bool,
}

enum Corner {
    Sharp,
    Cusp(Plan),
    Unresolved,
}

fn ring_vertices(bnd: &Boundary, ring: &[(usize, bool)]) -> (Vec<P>, Vec<bool>) {
    let mut poly = Vec::new();
    let mut ok = Vec::new();
    for (idx, rev) in ring {
        let arc = &bnd.arcs[*idx];
        let keep = believed(arc);
        if *rev {
            poly.extend(arc.pts.iter().rev().copied());
            ok.extend(keep.iter().rev().copied());
        } else {
            poly.extend(arc.pts.iter().copied());
            ok.extend(keep.iter().copied());
        }
    }
    (poly, ok)
}

fn side_level(m: &Model, side: usize) -> f64 {
    [m.x0, m.x1, m.y0, m.y1][side]
}

/// The corners (CORNERS indices) at a side's low and high end.
fn side_corners(side: usize) -> (usize, usize) {
    [(0, 3), (1, 2), (0, 1), (3, 2)][side]
}

fn has_corner(side: usize, c: usize) -> bool {
    let (a, b) = side_corners(side);
    a == c || b == c
}

type Placed = ([Vec<f64>; 4], Vec<NodeCorner>);

/// Where the ring's nodes sit on the model. See the Python `_nodes`.
fn nodes(ring: &[(usize, bool)], arcs: &[Arc], m: &Model) -> Option<Placed> {
    let mut pinned: [Vec<f64>; 4] = Default::default();
    if ring.len() == 1 && arcs[ring[0].0].closed() {
        return Some((pinned, Vec::new()));
    }
    let free: Vec<f64> = (0..4).filter(|&k| m.votes[k] > 0.0 && m.r[k] > 0.0).map(|k| m.r[k]).collect();
    let reach = free.iter().cloned().fold(None, |a: Option<f64>, b| Some(a.map_or(b, |a| if b > a { b } else { a }))).unwrap_or(0.0) + NODE_ON;
    let mut corners = Vec::new();
    for (k, (idx, rev)) in ring.iter().enumerate() {
        let arc = &arcs[*idx];
        let p = if *rev { arc.pts[0] } else { *arc.pts.last().unwrap() }; // the node this arc arrives at
        let mut on: Vec<usize> = Vec::new();
        let mut near: Vec<(f64, usize)> = Vec::new();
        for side in 0..4 {
            let axis = SIDE_AXIS[side];
            if (p[axis] - side_level(m, side)).abs() > NODE_ON {
                continue;
            }
            let (lo, hi) = if axis == 0 { (m.y0, m.y1) } else { (m.x0, m.x1) };
            let along = p[1 - axis];
            if along < lo - NODE_ON || along > hi + NODE_ON {
                continue;
            }
            on.push(side);
            let (c_lo, c_hi) = side_corners(side);
            near.push((along - lo, c_lo));
            near.push((hi - along, c_hi));
        }
        for &side in &on {
            pinned[side].push(p[SIDE_AXIS[side]]);
        }
        let mut best: Option<(f64, usize)> = None;
        for &(d, c) in &near {
            if d <= reach && best.is_none_or(|(bd, bc)| d < bd || (d == bd && c < bc)) {
                best = Some((d, c));
            }
        }
        if let Some((_, c)) = best {
            let sides: Vec<usize> = on.iter().copied().filter(|&s| has_corner(s, c)).collect();
            corners.push(NodeCorner { point: p, corner: c, sides, before: k, after: (k + 1) % ring.len() });
            continue;
        }
        if on.is_empty() && rects::project(m, p).1 > NODE_ON {
            return None;
        }
    }
    Some((pinned, corners))
}

/// Every (arc, end) placed on this node; the end is true for the last vertex.
fn arcs_at(arcs: &[Arc], point: P) -> Vec<(usize, bool)> {
    let mut out = Vec::new();
    for (idx, arc) in arcs.iter().enumerate() {
        if arc.closed() || arc.pts.len() < 2 {
            continue;
        }
        if same(arc.pts[0], point) {
            out.push((idx, false));
        }
        if same(*arc.pts.last().unwrap(), point) {
            out.push((idx, true));
        }
    }
    out
}

/// Whether an arc's vertices a little past a rounded corner lie on the line of
/// one of its sides. See the Python `_runs_along`.
fn runs_along(pts: &[P], corner: P, r: f64, m: &Model, side: usize, tol: f64) -> bool {
    let far: Vec<P> = pts
        .iter()
        .copied()
        .filter(|p| {
            let d = (p[0] - corner[0]).hypot(p[1] - corner[1]);
            d >= r + 1.0 && d <= r + CUSP_REACH
        })
        .collect();
    if far.len() < 2 {
        return false;
    }
    far.iter().all(|p| (p[SIDE_AXIS[side]] - side_level(m, side)).abs() <= tol)
}

fn pair_set(pair: (i32, i32)) -> Vec<i32> {
    let mut v = vec![pair.0, pair.1];
    v.sort();
    v.dedup();
    v
}

/// Is the corner at this node rounded like the shape's free corners, and how
/// is it made so? See the Python `_decide_corner`.
fn decide_corner(arcs: &[Arc], cand: &RectCandidate, nc: &NodeCorner, m: &Model, r_shape: f64, tol: f64) -> Corner {
    if r_shape <= 0.0 {
        return Corner::Sharp;
    }
    let (cx, cy, sx, sy) = rects::corner_frame(m, nc.corner);
    let mut round_cost = Vec::new();
    let mut sharp_cost = Vec::new();
    for p in &cand.poly {
        let u = sx * (p[0] - cx);
        let v = sy * (p[1] - cy);
        let zone = u < r_shape + 1.0 && v < r_shape + 1.0 && u > -1.0 && v > -1.0 && (p[0] - nc.point[0]).hypot(p[1] - nc.point[1]) > 1e-9;
        if zone {
            let a = rects::corner_dist(u, v, r_shape);
            let b = rects::corner_dist(u, v, 0.0);
            round_cost.push(a * a);
            sharp_cost.push(b * b);
        }
    }
    if round_cost.len() < 2 {
        return Corner::Unresolved;
    }
    if np_sum(&round_cost) >= np_sum(&sharp_cost) {
        return Corner::Sharp;
    }
    let here = arcs_at(arcs, nc.point);
    let ring_arcs = [cand.ring[nc.before].0, cand.ring[nc.after].0];
    let outside: Vec<(usize, bool)> = here.iter().copied().filter(|(i, _)| !ring_arcs.contains(i)).collect();
    if here.len() != 3 || outside.len() != 1 {
        return Corner::Unresolved;
    }
    let (o_idx, o_last) = outside[0];
    let o_arc = &arcs[o_idx];
    let o_pts: Vec<P> = if o_last { o_arc.pts.iter().rev().copied().collect() } else { o_arc.pts.clone() };
    let o_segs: Vec<Segment> = if o_last { reverse_segments(&o_arc.segments) } else { o_arc.segments.clone() };
    if o_segs.is_empty() {
        // (the Python guards this too: a collapsed arc has no first piece)
        return Corner::Unresolved;
    }
    let head = &o_segs[0];
    let point = [cx, cy];
    let corner_sides: Vec<usize> = (0..4).filter(|&k| has_corner(k, nc.corner)).collect();
    let straight_along = |s: usize| -> bool {
        let direction: P = if SIDE_AXIS[s] == 0 { [0.0, 1.0] } else { [1.0, 0.0] };
        match head {
            Segment::Line { p0, p1 } => {
                norm(sub(*p1, *p0)) > 1e-9 && dot(normalize(sub(*p1, *p0)), direction).abs() >= CUSP_ALONG_DEG.to_radians().cos()
            }
            _ => false,
        }
    };
    let options: Vec<usize> = if nc.sides.len() == 1 { nc.sides.clone() } else { corner_sides.clone() };
    let mut chosen: Option<(usize, bool)> = None;
    for &s in &options {
        let other = *corner_sides.iter().find(|&&k| k != s).unwrap();
        if runs_along(&o_pts, point, r_shape, m, s, tol) && straight_along(s) {
            chosen = Some((s, true));
            break;
        }
        if runs_along(&o_pts, point, r_shape, m, other, tol) {
            chosen = Some((s, false));
            break;
        }
    }
    let Some((side, carries)) = chosen else {
        return Corner::Unresolved;
    };
    // of the two ring arcs at the node, the one that runs along that side
    let axis = SIDE_AXIS[side];
    let level = side_level(m, side);
    let along_side: Vec<f64> = [nc.before, nc.after]
        .iter()
        .map(|&pos| {
            let q = &arcs[cand.ring[pos].0].pts;
            let off: Vec<f64> = q.iter().map(|p| (p[axis] - level).abs()).collect();
            np_median(&off)
        })
        .collect();
    let s_pos = if along_side[0] <= along_side[1] { nc.before } else { nc.after };
    let lowest = if along_side[1] < along_side[0] { along_side[1] } else { along_side[0] };
    if lowest > NODE_ON {
        return Corner::Unresolved;
    }
    let c_pos = if s_pos == nc.before { nc.after } else { nc.before };
    let (s_idx, c_idx) = (cand.ring[s_pos].0, cand.ring[c_pos].0);
    let s_set = pair_set(arcs[s_idx].pair);
    let c_set = pair_set(arcs[c_idx].pair);
    let mut sym: Vec<i32> = s_set.iter().copied().filter(|v| !c_set.contains(v)).chain(c_set.iter().copied().filter(|v| !s_set.contains(v))).collect();
    sym.sort();
    if pair_set(arcs[o_idx].pair) != sym {
        return Corner::Unresolved;
    }
    Corner::Cusp(Plan { side, s_idx, c_idx, o_idx, o_last, carries })
}

/// Where the corner's arc leaves the side.
fn tangent_point(m: &Model, corner: usize, side: usize) -> P {
    let (cx, cy, sx, sy) = rects::corner_frame(m, corner);
    let r = m.r[corner];
    if SIDE_AXIS[side] == 0 {
        // a vertical side: the point is r along y
        return [side_level(m, side), cy + sy * r];
    }
    [cx + sx * r, side_level(m, side)]
}

/// Normals for an arc whose vertices were spliced. See the Python `_placed_normals`.
fn placed_normals(arc: &Arc) -> Vec<P> {
    let pts = &arc.pts;
    let n = pts.len();
    let mut normal: Vec<P> = (0..n)
        .map(|k| {
            let ahead = pts[(k + 1).min(n - 1)];
            let behind = pts[k.saturating_sub(1)];
            let t = sub(ahead, behind);
            let l = norm(t);
            let l = if l > 1e-9 { l } else { 1e-9 };
            let t = [t[0] / l, t[1] / l];
            [-t[1], t[0]]
        })
        .collect();
    if !arc.normal.is_empty() {
        let r = arc.normal[arc.normal.len() / 2];
        if dot(r, normal[n / 2]) < 0.0 {
            for v in normal.iter_mut() {
                *v = [-v[0], -v[1]];
            }
        }
    }
    normal
}

/// Slide the node along the side to the corner's tangent point T. See the
/// Python `_apply_cusp`.
fn apply_cusp(arcs: &mut [Arc], m: &Model, corner: usize, plan: &Plan, node: P, params: &CurveParams) -> P {
    let Plan { side, s_idx, c_idx, o_idx, o_last, carries } = *plan;
    let t = tangent_point(m, corner, side);
    let along = 1 - SIDE_AXIS[side];
    let level = side_level(m, side);
    let step = sign(node[along] - t[along]); // from T towards the node
    // S: the ring's arc along the side, walked towards the node
    let (moved, _) = {
        let s = &mut arcs[s_idx];
        let s_first = same(s.pts[0], node);
        let s_pts: Vec<P> = if s_first { s.pts.iter().rev().copied().collect() } else { s.pts.clone() };
        let s_nrm: Vec<P> = if s_first { s.normal.iter().rev().copied().collect() } else { s.normal.clone() };
        let mut beyond: Vec<bool> = s_pts.iter().map(|p| step * (p[along] - t[along]) > 0.0).collect();
        let last = beyond.len() - 1;
        beyond[last] = true;
        let cut = beyond.iter().position(|b| *b).unwrap().max(1); // the first vertex past T
        let mut moved: Vec<P> = s_pts[cut..s_pts.len() - 1].to_vec();
        for q in moved.iter_mut() {
            q[SIDE_AXIS[side]] = level;
        }
        let mut s_new: Vec<P> = s_pts[..cut].to_vec();
        s_new.push(t);
        if s_first {
            s_new.reverse();
        }
        s.pts = s_new;
        if !s_nrm.is_empty() {
            let mut n_new: Vec<P> = s_nrm[..cut.min(s_nrm.len())].to_vec();
            n_new.push(s_nrm[(cut - 1).min(s_nrm.len() - 1)]);
            if s_first {
                n_new.reverse();
            }
            s.normal = n_new;
        }
        (moved, cut)
    };
    let o = &arcs[o_idx];
    let o_pts: Vec<P> = if o_last { o.pts.iter().rev().copied().collect() } else { o.pts.clone() }; // walking away from the node
    let o_segs: Vec<Segment> = if o_last { reverse_segments(&o.segments) } else { o.segments.clone() };
    let mut direction: P = [0.0, 0.0];
    direction[along] = step; // along the side, from T past the node
    let mut through = node;
    through[SIDE_AXIS[side]] = level;
    let o_trim = if o_last { o.trim1 } else { o.trim0 };
    let o_cum = cumulative(&o_pts);
    let mut first = o_cum.iter().position(|c| *c >= o_trim).unwrap_or(o_cum.len()).max(1);
    if first >= o_pts.len() - 1 {
        first = 1;
    }
    let head_end = o_segs[0].end();
    let (o_new, kept, segs): (Vec<P>, usize, Vec<Segment>) = if carries {
        // the outside arc carries the side on past the node: its first line now starts at T
        let mut o_new = vec![t];
        o_new.extend(moved.iter().copied());
        o_new.push(through);
        o_new.extend(o_pts[first..].iter().copied());
        let mut segs = vec![Segment::Line { p0: t, p1: head_end }];
        segs.extend(o_segs[1..].iter().cloned());
        (o_new, first, segs)
    } else {
        // the outside arc leaves the side at the node: the neighbour's own
        // rounded corner, which meets this one at T
        let mut o_new = vec![t];
        o_new.extend(o_pts[1..].iter().copied());
        let end = head_end;
        let nxt = o_segs.get(1);
        let segs = match node_fillet(t, direction, &o_segs, m.r[corner], &o_pts[first..], params.tol) {
            Some(s) => s,
            None => {
                let reach = nearest(&o_pts, end);
                let inner: &[P] = if reach > first { &o_pts[first..reach] } else if reach > 1 { &o_pts[1..reach] } else { &[] };
                let mut piece = vec![t];
                piece.extend(inner.iter().copied());
                piece.push(end);
                let d_next = match nxt {
                    Some(Segment::Cubic { p0, c1, .. }) if norm(sub(*c1, *p0)) > 1e-9 => normalize(sub(*c1, *p0)),
                    Some(Segment::Line { p0, p1 }) if norm(sub(*p1, *p0)) > 1e-9 => normalize(sub(*p1, *p0)),
                    _ => normalize(sub(end, piece[piece.len() - 2])),
                };
                let mut segs = fit_cubics(&piece, direction, [-d_next[0], -d_next[1]], params.tol, 0);
                segs.extend(o_segs[1..].iter().cloned());
                segs
            }
        };
        (o_new, 1, segs)
    };
    {
        let o = &mut arcs[o_idx];
        let o_nrm: Vec<P> = if o_last { o.normal.iter().rev().copied().collect() } else { o.normal.clone() };
        o.pts = if o_last { o_new.iter().rev().copied().collect() } else { o_new.clone() };
        // the vertices spliced in along the side take the side's normal, turned
        // the way the outside arc's own placed normals point; the rest keep theirs
        let mut side_n: P = [0.0, 0.0];
        side_n[SIDE_AXIS[side]] = 1.0;
        if o_nrm.len() == o_pts.len() {
            let votes: Vec<f64> = o_nrm[kept..(kept + 4).min(o_nrm.len())].iter().map(|v| dot(*v, side_n)).collect();
            let vote = np_sum(&votes);
            let hn = if vote >= 0.0 { side_n } else { [-side_n[0], -side_n[1]] };
            let mut n_new: Vec<P> = vec![hn; o_new.len() - (o_pts.len() - kept)];
            n_new.extend(o_nrm[kept..].iter().copied());
            if o_last {
                n_new.reverse();
            }
            o.normal = n_new;
        } else {
            o.normal = placed_normals(o);
        }
        o.sliver = None;
        if o_last {
            o.t1 = Some(direction);
            o.trim1 = NODE_TRIM;
        } else {
            o.t0 = Some(direction);
            o.trim0 = NODE_TRIM;
        }
        o.segments = if o_last { reverse_segments(&segs) } else { segs };
    }
    {
        let c = &mut arcs[c_idx];
        let first_end = same(c.pts[0], node);
        let k = if first_end { 0 } else { c.pts.len() - 1 };
        c.pts[k] = t;
    }
    t
}

/// The outside arc's corner at a cusp node as a designer draws it: a quarter
/// circle of the shape's radius into the first line that turns like a corner.
/// See the Python `_node_fillet`.
fn node_fillet(t: P, direction: P, o_segs: &[Segment], r: f64, pts: &[P], tol: f64) -> Option<Vec<Segment>> {
    if r <= 0.0 {
        return None;
    }
    let mut r = r;
    for (j, nxt) in o_segs.iter().take(3).enumerate() {
        let Segment::Line { p0, p1 } = nxt else {
            continue;
        };
        let (p0, p1) = (*p0, *p1);
        let lb = norm(sub(p1, p0));
        if lb < 1e-9 {
            continue;
        }
        let db = [(p1[0] - p0[0]) / lb, (p1[1] - p0[1]) / lb];
        let turn = dot(direction, db).clamp(-1.0, 1.0).acos().to_degrees();
        if !(FILLET_TURN.0 <= turn && turn <= FILLET_TURN.1) {
            continue;
        }
        let x = intersect(t, direction, p0, db)?;
        let (half, _bis) = rects::fillet_frame(direction, db);
        let ra = dot(sub(x, t), direction);
        let rb = dot(sub(p1, x), db);
        let r_max = ra * half.tan();
        if ra <= 0.0 || r - r_max > NODE_ON {
            return None;
        }
        if r_max < r {
            r = r_max;
        }
        let reach = r / half.tan();
        if rb - reach < FILLET_LINE_KEEP {
            return None;
        }
        let near: Vec<P> = pts.iter().copied().filter(|p| norm(sub(*p, x)) <= reach + 1.0).collect();
        if near.len() < 3 || !fillet_holds(&near, x, direction, db, r, FINAL_SLACK * tol, 2.0 * tol) {
            return None;
        }
        let (mut t1, t2, sweep) = rects::fillet_points(x, direction, db, r);
        let mut out = Vec::new();
        if dot(sub(t1, t), direction) > 1e-6 {
            out.push(Segment::Line { p0: t, p1: t1 });
        } else {
            t1 = t;
        }
        out.push(Segment::Arc { p0: t1, p1: t2, r, large: false, sweep });
        out.push(Segment::Line { p0: t2, p1 });
        out.extend(o_segs[j + 1..].iter().cloned());
        return Some(out);
    }
    None
}

type Guides = Vec<(f64, f64)>;

/// Axis-aligned lines elsewhere in the graph, as (level, length) for x and y.
fn guides(bnd: &Boundary, used: &HashSet<usize>, snap_axis_deg: f64) -> (Guides, Guides) {
    let mut gx = Vec::new();
    let mut gy = Vec::new();
    for (idx, arc) in bnd.arcs.iter().enumerate() {
        if used.contains(&idx) || arc.pair.0 == 0 || arc.pair.1 == 0 {
            continue;
        }
        for seg in &arc.segments {
            let Segment::Line { p0, p1 } = seg else {
                continue;
            };
            let d = sub(*p1, *p0);
            let length = norm(d);
            if length < GUIDE_MIN {
                continue;
            }
            let ang = py_mod(d[1].atan2(d[0]).to_degrees(), 180.0);
            if ang.min(180.0 - ang) <= snap_axis_deg {
                gy.push((0.5 * (p0[1] + p1[1]), length));
            } else if (ang - 90.0).abs() <= snap_axis_deg {
                gx.push((0.5 * (p0[0] + p1[0]), length));
            }
        }
    }
    (gx, gy)
}

/// Edges on one guide: the side levels on `axis` clustered across shapes. See
/// the Python `_snap_levels`. Returns, per shape, which of its two sides on
/// this axis snapped.
fn snap_levels(cands: &[RectCandidate], models: &mut [Model], guides: &Guides, axis: usize, mv: f64, sized: &HashSet<(usize, usize)>) -> Vec<(bool, bool)> {
    let mut values = Vec::new();
    let mut weights = Vec::new();
    let mut owner: Vec<(usize, usize)> = Vec::new();
    for (k, m) in models.iter().enumerate() {
        let (lo, hi) = if axis == 0 { (m.x0, m.x1) } else { (m.y0, m.y1) };
        let span = if axis == 0 { m.h() } else { m.w() };
        for (end, level) in [lo, hi].into_iter().enumerate() {
            values.push(level);
            weights.push(span);
            owner.push((k, end));
        }
    }
    let mut levels: Vec<f64> = guides.iter().map(|g| g.0).collect();
    levels.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let mut target: HashMap<(usize, usize), f64> = HashMap::new();
    for (mean, members) in rects::cluster_1d(&values, &weights, mv) {
        let mut level = if members.len() > 1 { Some(mean) } else { None };
        if !levels.is_empty() {
            let j = levels.partition_point(|g| *g < mean);
            let mut best: Option<f64> = None;
            for i in [j as i64 - 1, j as i64] {
                if i < 0 || i as usize >= levels.len() {
                    continue;
                }
                let g = levels[i as usize];
                if best.is_none_or(|b| (g - mean).abs() < (b - mean).abs() || ((g - mean).abs() == (b - mean).abs() && g < b)) {
                    best = Some(g);
                }
            }
            let best = best.unwrap();
            if (best - mean).abs() <= mv {
                level = Some(best);
            }
        }
        let Some(level) = level else {
            continue;
        };
        for &i in &members {
            if (values[i] - level).abs() > mv {
                continue;
            }
            let (k, end) = owner[i];
            if cands[k].pinned[2 * axis + end].iter().all(|v| (v - level).abs() <= NODE_ON) {
                target.insert(owner[i], level);
            }
        }
    }
    let mut snapped = Vec::with_capacity(models.len());
    for (k, m) in models.iter_mut().enumerate() {
        let (mut lo, mut hi) = if axis == 0 { (m.x0, m.x1) } else { (m.y0, m.y1) };
        let (t_lo, t_hi) = (target.get(&(k, 0)).copied(), target.get(&(k, 1)).copied());
        snapped.push((t_lo.is_some(), t_hi.is_some()));
        let whole = sized.contains(&(k, axis));
        let held_lo = !cands[k].pinned[2 * axis].is_empty() || !whole;
        let held_hi = !cands[k].pinned[2 * axis + 1].is_empty() || !whole;
        match (t_lo, t_hi) {
            (Some(a), Some(b)) => {
                lo = a;
                hi = b;
            }
            (Some(a), None) => {
                let nhi = if held_hi { hi } else { hi + (a - lo) };
                lo = a;
                hi = nhi;
            }
            (None, Some(b)) => {
                let nlo = if held_lo { lo } else { lo + (b - hi) };
                lo = nlo;
                hi = b;
            }
            (None, None) => {}
        }
        if axis == 0 {
            m.x0 = lo;
            m.x1 = hi;
        } else {
            m.y0 = lo;
            m.y1 = hi;
        }
    }
    snapped
}

/// Give the model this length on `axis`, keeping side `keep` (0 low, 1 high)
/// where it is, or its centre when None.
fn set_length(m: &mut Model, axis: usize, length: f64, keep: Option<usize>) {
    let (mut lo, mut hi) = if axis == 0 { (m.x0, m.x1) } else { (m.y0, m.y1) };
    match keep {
        Some(0) => hi = lo + length,
        Some(_) => lo = hi - length,
        None => {
            let c = 0.5 * (lo + hi);
            lo = c - 0.5 * length;
            hi = c + 0.5 * length;
        }
    }
    if axis == 0 {
        m.x0 = lo;
        m.x1 = hi;
    } else {
        m.y0 = lo;
        m.y1 = hi;
    }
}

type Regular = (Vec<Model>, Vec<f64>, Vec<Model>, Vec<f64>);

/// The candidates' models made regular together, and each made regular on its
/// own. See the Python `_regular_models`.
fn regular_models(cands: &[RectCandidate], gx: &Guides, gy: &Guides, tol: f64) -> Regular {
    let mv = rects::MOVE_SHARE * tol;
    let r_move = rects::radius_move(tol);
    let mut models: Vec<Model> = cands.iter().map(|c| c.model.clone()).collect();
    let mut shape_r: Vec<f64> = Vec::new();
    let mut shape_w: Vec<f64> = Vec::new();
    for (c, m) in cands.iter().zip(models.iter_mut()) {
        let at_node: Vec<usize> = c.node_corners.iter().map(|nc| nc.corner).collect();
        let free: Vec<usize> = (0..4).filter(|k| !at_node.contains(k)).collect();
        let readings: Vec<f64> = free.iter().map(|&k| m.r[k]).collect();
        let round_: Vec<usize> = free.iter().copied().filter(|&k| m.r[k] > rects::SHARP_SHAPE_R).collect();
        if free.is_empty() || py_sum(readings.iter().copied()) / readings.len() as f64 <= rects::SHARP_SHAPE_R || round_.is_empty() {
            for &k in &free {
                m.r[k] = 0.0;
            }
            shape_r.push(0.0);
            shape_w.push(0.0);
            continue;
        }
        let wt = |k: usize| -> f64 {
            if 1.0 > m.votes[k] {
                1.0
            } else {
                m.votes[k]
            }
        };
        let w = py_sum(round_.iter().map(|&k| wt(k)));
        let mean = py_sum(round_.iter().map(|&k| m.r[k] * wt(k))) / w;
        let mut one = m.clone();
        for &k in &free {
            one.r[k] = mean;
        }
        if rects::holds(&rects::apparent(&one, c.sigma), &c.poly, &c.trusted, tol, None) {
            m.r = one.r;
            shape_r.push(mean);
            shape_w.push(w);
            continue;
        }
        if !round_.iter().all(|&k| (m.r[k] - mean).abs() <= r_move) {
            for &k in &free {
                if m.r[k] <= rects::SHARP_SHAPE_R {
                    m.r[k] = 0.0;
                }
            }
            shape_r.push(0.0);
            shape_w.push(0.0);
            continue;
        }
        for &k in &free {
            m.r[k] = if round_.contains(&k) || (m.r[k] - mean).abs() <= r_move { mean } else { 0.0 };
        }
        shape_r.push(mean);
        shape_w.push(w);
    }
    let own: Vec<Model> = models.clone();
    let own_r = shape_r.clone();
    // one radius across shapes whose radii agree
    let idx: Vec<usize> = (0..shape_r.len()).filter(|&k| shape_r[k] > 0.0).collect();
    let vals: Vec<f64> = idx.iter().map(|&k| shape_r[k]).collect();
    let wts: Vec<f64> = idx.iter().map(|&k| shape_w[k]).collect();
    for (mean, members) in rects::cluster_1d(&vals, &wts, r_move) {
        for i in members {
            let k = idx[i];
            let m = &mut models[k];
            for c in 0..4 {
                if m.r[c] > 0.0 && (m.r[c] - shape_r[k]).abs() <= 1e-12 {
                    m.r[c] = mean;
                }
            }
            shape_r[k] = mean;
        }
    }
    // one size across sides whose lengths agree, about each shape's centre
    let mut sizes = Vec::new();
    let mut owners: Vec<(usize, usize)> = Vec::new();
    for (k, (c, m)) in cands.iter().zip(models.iter()).enumerate() {
        for (axis, length) in [(0usize, m.w()), (1usize, m.h())] {
            if c.pinned[2 * axis].is_empty() && c.pinned[2 * axis + 1].is_empty() {
                sizes.push(length);
                owners.push((k, axis));
            }
        }
    }
    let mut sized: HashSet<(usize, usize)> = HashSet::new();
    let mut size_groups: Vec<Vec<(usize, usize)>> = Vec::new();
    let ones = vec![1.0; sizes.len()];
    for (mean, members) in rects::cluster_1d(&sizes, &ones, mv) {
        if members.len() < 2 {
            continue;
        }
        size_groups.push(members.iter().map(|&i| owners[i]).collect());
        for &i in &members {
            let (k, axis) = owners[i];
            sized.insert((k, axis));
            set_length(&mut models[k], axis, mean, None);
        }
    }
    let snapped = [snap_levels(cands, &mut models, gx, 0, mv, &sized), snap_levels(cands, &mut models, gy, 1, mv, &sized)];
    // a length whose two sides both went onto guides is what the guides say
    for group in &size_groups {
        let fixed: Vec<f64> = group
            .iter()
            .filter(|(k, axis)| snapped[*axis][*k].0 && snapped[*axis][*k].1)
            .map(|(k, axis)| if *axis == 0 { models[*k].w() } else { models[*k].h() })
            .collect();
        if fixed.is_empty() {
            continue;
        }
        let length = py_sum(fixed.iter().copied()) / fixed.len() as f64;
        for &(k, axis) in group {
            let (lo_s, hi_s) = snapped[axis][k];
            let current = if axis == 0 { models[k].w() } else { models[k].h() };
            if (lo_s && hi_s) || (current - length).abs() > mv {
                continue;
            }
            set_length(&mut models[k], axis, length, if lo_s { Some(0) } else if hi_s { Some(1) } else { None });
        }
    }
    for (k, m) in models.iter_mut().enumerate() {
        let cap = 0.5 * m.w().min(m.h());
        for r in m.r.iter_mut() {
            if cap < *r {
                *r = cap;
            }
        }
        if cap < shape_r[k] {
            shape_r[k] = cap;
        }
    }
    (models, shape_r, own, own_r)
}

/// Write the model into the ring's arcs. See the Python `_write_rect`.
fn write_rect(bnd: &mut Boundary, cand: &RectCandidate, m: &Model) {
    if cand.ring.len() == 1 && bnd.arcs[cand.ring[0].0].closed() {
        let arc = &mut bnd.arcs[cand.ring[0].0];
        let segs = rects::subpath(m, 0.0, 0.0, true);
        let clockwise = shoelace(&arc.pts) > 0.0;
        arc.segments = if clockwise { segs } else { reverse_segments(&segs) };
        arc.mirror = None;
        let (lo, hi) = m.r.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), r| (lo.min(*r), hi.max(*r)));
        if hi - lo <= 1e-9 {
            arc.rect = Some(if m.r[0] > 0.0 {
                Shape::RoundedRect { x: m.x0, y: m.y0, w: m.w(), h: m.h(), rx: m.r[0] }
            } else {
                Shape::Rect { x: m.x0, y: m.y0, w: m.w(), h: m.h() }
            });
        }
        resample(arc);
        return;
    }
    for &(idx, rev) in &cand.ring {
        let arc = &mut bnd.arcs[idx];
        let (start, end) = if rev { (*arc.pts.last().unwrap(), arc.pts[0]) } else { (arc.pts[0], *arc.pts.last().unwrap()) };
        let (s0, s1) = (rects::project(m, start).0, rects::project(m, end).0);
        let mut segs = if cand.clockwise { rects::subpath(m, s0, s1, false) } else { reverse_segments(&rects::subpath(m, s1, s0, false)) };
        if segs.is_empty() {
            continue;
        }
        segs[0].set_start_pub(start);
        let last = segs.len() - 1;
        segs[last].set_end_pub(end);
        arc.segments = if rev { reverse_segments(&segs) } else { segs };
        resample(arc);
    }
}

/// `np.dot(x, roll(y, -1)) - np.dot(y, roll(x, -1))`.
fn shoelace(pts: &[P]) -> f64 {
    let n = pts.len();
    let (mut a, mut b) = (0.0, 0.0);
    for k in 0..n {
        let q = pts[(k + 1) % n];
        a += pts[k][0] * q[1];
        b += pts[k][1] * q[0];
    }
    a - b
}

/// Points `k` apart along a straight piece, as `p0 + (p1 - p0) * linspace`.
fn line_points(p: P, q: P, k: usize) -> Vec<P> {
    linspace01(k).into_iter().map(|t| [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t]).collect()
}

/// Curve normals `(t_y, -t_x)` of a resampled run, turned the way `old` points
/// by one vote over the vertices it replaces.
fn curve_normals(new: &[P], closed: bool) -> Vec<P> {
    let n = new.len();
    (0..n)
        .map(|k| {
            let (ahead, behind) = if closed {
                (new[(k + 1) % n], new[(k + n - 1) % n])
            } else {
                (new[(k + 1).min(n - 1)], new[k.saturating_sub(1)])
            };
            let t = sub(ahead, behind);
            let l = norm(t);
            let l = if l > 1e-12 { l } else { 1e-12 };
            [t[1] / l, -t[0] / l]
        })
        .collect()
}

/// The vote `np.sum(old * normal[near])`: the flattened products, summed pairwise.
fn vote(old_pts: &[P], old_normal: &[P], new: &[P], normal: &[P]) -> f64 {
    let mut prod = Vec::with_capacity(2 * old_pts.len());
    for (p, o) in old_pts.iter().zip(old_normal) {
        let v = normal[nearest(new, *p)];
        prod.push(o[0] * v[0]);
        prod.push(o[1] * v[1]);
    }
    np_sum(&prod)
}

/// The arc's vertices and normals, taken again from the curve it was written
/// with. See the Python `_resample`.
fn resample(arc: &mut Arc) {
    let mut pts: Vec<P> = Vec::new();
    for seg in &arc.segments {
        let q = match seg {
            Segment::Arc { .. } => {
                let length = np_length(&seg_arc_points(seg, 33));
                let k = 2usize.max((length / RESAMPLE_STEP).ceil() as usize + 1);
                seg_arc_points(seg, k)
            }
            _ => {
                let (p0, p1) = (seg.start(), seg.end());
                let length = norm(sub(p1, p0));
                let k = 2usize.max((length / RESAMPLE_STEP).ceil() as usize + 1);
                line_points(p0, p1, k)
            }
        };
        if pts.is_empty() {
            pts.extend(q);
        } else {
            pts.extend(q.into_iter().skip(1));
        }
    }
    let mut new = pts;
    if new.len() < 2 {
        return;
    }
    let closed = arc.closed();
    if closed && norm(sub(new[new.len() - 1], new[0])) < 1e-9 {
        new.pop();
    }
    let mut normal = curve_normals(&new, closed);
    if arc.normal.len() == arc.pts.len() && vote(&arc.pts, &arc.normal, &new, &normal) < 0.0 {
        for v in normal.iter_mut() {
            *v = [-v[0], -v[1]];
        }
    }
    if !closed {
        let last = new.len() - 1;
        new[0] = arc.pts[0];
        new[last] = *arc.pts.last().unwrap();
    }
    arc.pts = new;
    arc.normal = normal;
    arc.sliver = None;
}

/// Every ring that is an axis-aligned rounded rectangle, made regular and
/// written back into its arcs. Returns the arcs of every ring that reads as
/// one, written or not, and the radii the rounded shapes took, for `fillets`.
/// See the Python `_rectify`.
pub fn rectify(bnd: &mut Boundary, params: &CurveParams, rgb: &Image, mut log: Option<&mut Log>) -> (HashSet<usize>, Vec<f64>) {
    let (h, w) = ((bnd.padded.h - 2) as f64, (bnd.padded.w - 2) as f64);
    let mut labels: Vec<i32> = bnd.padded.data.clone();
    labels.sort_unstable();
    labels.dedup();
    let mut seen: HashSet<Vec<usize>> = HashSet::new();
    let mut found: Vec<RectCandidate> = Vec::new();
    for lab in labels {
        if lab == 0 {
            continue;
        }
        let one: HashSet<i32> = [lab].into_iter().collect();
        for ring in bnd.rings(&one) {
            let mut key: Vec<usize> = ring.iter().map(|(i, _)| *i).collect();
            key.sort_unstable();
            key.dedup();
            if !seen.insert(key) {
                continue;
            }
            if ring.iter().any(|(i, _)| bnd.arcs[*i].pair.0 == 0 || bnd.arcs[*i].pair.1 == 0) {
                continue;
            }
            let (poly, trusted) = ring_vertices(bnd, &ring);
            if poly.iter().any(|p| p[0] <= 0.0 || p[1] <= 0.0 || p[0] >= w || p[1] >= h) {
                continue;
            }
            let Some(mut m) = rects::fit_sides(&poly, params.snap_axis_deg) else {
                continue;
            };
            rects::fit_radii(&mut m, &poly, &trusted);
            let Some((pinned, node_corners)) = nodes(&ring, &bnd.arcs, &m) else {
                continue;
            };
            // a corner with a node in it does not vote
            for nc in &node_corners {
                m.votes[nc.corner] = 0.0;
            }
            if !rects::holds(&m, &poly, &trusted, params.tol, None) {
                continue;
            }
            // the radii as drawn: the blur read across the shape's own sides
            // taken out of what the placement read
            let read = rects::edge_sigma(rgb, &m);
            let sigma = read.map_or(0.0, |r| r.0);
            let raw = m.r;
            for k in 0..4 {
                m.r[k] = if m.r[k] > 0.0 { rects::deblur(m.r[k], sigma) } else { 0.0 };
            }
            let clockwise = shoelace(&poly) > 0.0;
            if let Some(log) = log.as_deref_mut() {
                let mut row = vec![1.0, ring.len() as f64];
                for (i, rev) in &ring {
                    row.push(*i as f64);
                    row.push(if *rev { 1.0 } else { 0.0 });
                }
                row.extend([m.x0, m.y0, m.x1, m.y1]);
                row.extend(raw);
                row.extend(m.votes);
                row.push(sigma);
                row.push(read.map_or(-1.0, |r| r.1 as f64));
                row.extend(m.r);
                row.push(if clockwise { 1.0 } else { 0.0 });
                row.extend(pinned.iter().map(|p| p.len() as f64));
                row.push(node_corners.len() as f64);
                for nc in &node_corners {
                    row.extend([nc.corner as f64, nc.before as f64, nc.after as f64, nc.sides.len() as f64]);
                    row.extend(nc.sides.iter().map(|s| *s as f64));
                }
                log.push(row);
            }
            found.push(RectCandidate { ring, poly, trusted, model: m, clockwise, pinned, node_corners, sigma });
        }
    }
    let rounded: Vec<bool> = found.iter().map(|c| (0..4).any(|k| c.model.r[k] > rects::SHARP_SHAPE_R && c.model.votes[k] > 0.0)).collect();
    let mut order: Vec<usize> = (0..found.len()).collect();
    order.sort_by_key(|&k| (!rounded[k], k));
    let mut cands: Vec<RectCandidate> = Vec::new();
    let mut claimed: HashMap<usize, usize> = HashMap::new();
    for k in order {
        let c = &found[k];
        if c.ring.iter().any(|(i, _)| claimed.contains_key(i)) {
            continue;
        }
        for (i, _) in &c.ring {
            claimed.insert(*i, cands.len());
        }
        cands.push(c.clone());
    }
    let all: HashSet<usize> = found.iter().flat_map(|c| c.ring.iter().map(|(i, _)| *i)).collect();
    if cands.is_empty() {
        return (HashSet::new(), Vec::new());
    }
    let used: HashSet<usize> = claimed.keys().copied().collect();
    let (gx, gy) = guides(bnd, &used, params.snap_axis_deg);
    let (models, shape_r, own, own_r) = regular_models(&cands, &gx, &gy, params.tol);
    let mut radii: Vec<f64> = Vec::new();
    for (k, cand) in cands.iter().enumerate() {
        // the model made regular with the others, else on its own
        let mut which = 1.0;
        let mut settled = settle_corners(&bnd.arcs, cand, &models[k], shape_r[k], &claimed, params);
        if settled.is_none() {
            which = 2.0;
            settled = settle_corners(&bnd.arcs, cand, &own[k], own_r[k], &claimed, params);
        }
        let Some((m, plans)) = settled else {
            if let Some(log) = log.as_deref_mut() {
                log.push(vec![2.0, k as f64, 0.0, shape_r[k], own_r[k]]);
            }
            continue;
        };
        if let Some(log) = log.as_deref_mut() {
            let mut row = vec![2.0, k as f64, which, shape_r[k], own_r[k], m.x0, m.y0, m.x1, m.y1];
            row.extend(m.r);
            row.push(plans.len() as f64);
            for (nc, p) in &plans {
                row.extend([nc.corner as f64, p.side as f64, p.s_idx as f64, p.c_idx as f64, p.o_idx as f64, if p.o_last { 1.0 } else { 0.0 }, if p.carries { 1.0 } else { 0.0 }]);
            }
            log.push(row);
        }
        let mut moved: Vec<P> = Vec::new();
        for (nc, plan) in &plans {
            let t = apply_cusp(&mut bnd.arcs, &m, nc.corner, plan, nc.point, params);
            moved.push(t);
        }
        // every other node of the ring goes onto the outline, and the arcs
        // outside the ring that end there with it
        let ring_arcs: HashSet<usize> = cand.ring.iter().map(|(i, _)| *i).collect();
        for &(idx, rev) in &cand.ring {
            let arc = &bnd.arcs[idx];
            let p = if rev { arc.pts[0] } else { *arc.pts.last().unwrap() };
            if moved.iter().any(|t| same(*t, p)) {
                continue;
            }
            let q = rects::point_at(&m, rects::project(&m, p).0);
            move_node(&mut bnd.arcs, p, q, &ring_arcs);
        }
        write_rect(bnd, cand, &m);
        radii.extend(m.r.iter().copied().filter(|r| *r > 0.0));
    }
    radii.sort_by(|a, b| a.partial_cmp(b).unwrap());
    radii.dedup();
    (all, radii)
}

type Settled = (Model, Vec<(NodeCorner, Plan)>);

/// The corners of a regular model that have a node in them, decided, and the
/// model checked against the ring's vertices. See the Python `_settle_corners`.
fn settle_corners(arcs: &[Arc], cand: &RectCandidate, m: &Model, shape_r: f64, claimed: &HashMap<usize, usize>, params: &CurveParams) -> Option<Settled> {
    let mut m = m.clone();
    let free: Vec<(f64, f64)> = (0..4).filter(|&c| m.votes[c] > 0.0 && m.r[c] > 0.0).map(|c| (m.r[c], m.votes[c])).collect();
    let mut r_node = if shape_r > 0.0 {
        shape_r
    } else if !free.is_empty() {
        py_sum(free.iter().map(|(r, v)| r * v)) / py_sum(free.iter().map(|(_, v)| *v))
    } else {
        0.0
    };
    let cap = 0.5 * m.w().min(m.h());
    if cap < r_node {
        r_node = cap;
    }
    let mut plans = Vec::new();
    for nc in &cand.node_corners {
        m.r[nc.corner] = r_node;
        if r_node > 0.0 && rects::project(&rects::apparent(&m, cand.sigma), nc.point).1 <= NODE_ON {
            // the node is on the rounded corner itself
            continue;
        }
        let mut kind = decide_corner(arcs, cand, nc, &m, r_node, params.tol);
        if let Corner::Cusp(plan) = &kind {
            if claimed.contains_key(&plan.o_idx) {
                kind = Corner::Unresolved; // the outside arc is another rectangle's
            }
        }
        match kind {
            Corner::Unresolved => return None,
            Corner::Sharp => m.r[nc.corner] = 0.0,
            Corner::Cusp(plan) => plans.push((nc.clone(), plan)),
        }
    }
    // the snaps are bounded one by one; the sum is checked here, against the
    // model as the placement would read it
    if !rects::holds(&rects::apparent(&m, cand.sigma), &cand.poly, &cand.trusted, FINAL_SLACK * params.tol, Some(2.0 * params.tol)) {
        return None;
    }
    Some((m, plans))
}

/// Rounded corners between two lines, anywhere in the graph, made one circle
/// of a radius shared with the mark's other rounded corners. Returns the
/// number of corners rewritten. See the Python `_fillets`.
pub fn fillets(bnd: &mut Boundary, params: &CurveParams, skip: &HashSet<usize>, anchors: &[f64], mut log: Option<&mut Log>) -> usize {
    let tol = params.tol;
    let r_move = rects::radius_move(tol);
    struct Found {
        idx: usize,
        i: usize,
        j: usize,
        x: P,
        da: P,
        db: P,
        near: Vec<P>,
        r: f64,
        r_max: f64,
    }
    let mut found: Vec<Found> = Vec::new();
    for (idx, arc) in bnd.arcs.iter().enumerate() {
        if skip.contains(&idx) || arc.rect.is_some() || arc.segments.len() < 3 {
            continue;
        }
        let segs = &arc.segments;
        let n = segs.len();
        let pts = &arc.pts;
        let keep = believed(arc);
        for i in 0..n - 2 {
            let Segment::Line { p0: a0, p1: a1 } = segs[i] else {
                continue;
            };
            for gap in [1usize, 2] {
                let j = i + gap + 1;
                if j >= n {
                    break;
                }
                let Segment::Line { p0: b0, p1: b1 } = segs[j] else {
                    continue;
                };
                if segs[i + 1..j].iter().any(|s| matches!(s, Segment::Line { .. })) {
                    continue;
                }
                let (la, lb) = (norm(sub(a1, a0)), norm(sub(b1, b0)));
                if la < 1e-6 || lb < 1e-6 || norm(sub(b0, a1)) > FILLET_SPAN {
                    continue;
                }
                let da = [(a1[0] - a0[0]) / la, (a1[1] - a0[1]) / la];
                let db = [(b1[0] - b0[0]) / lb, (b1[1] - b0[1]) / lb];
                let turn = dot(da, db).clamp(-1.0, 1.0).acos().to_degrees();
                if !(FILLET_TURN.0 <= turn && turn <= FILLET_TURN.1) {
                    continue;
                }
                let Some(x) = intersect(a0, da, b0, db) else {
                    continue;
                };
                let (half, _bis) = rects::fillet_frame(da, db);
                // each line may give a fillet half its length
                let (ea, eb) = (dot(sub(x, a0), da), dot(sub(b1, x), db));
                let room = 0.5 * (if eb < ea { eb } else { ea }) - FILLET_LINE_KEEP;
                if room <= 0.0 {
                    continue;
                }
                let r_max = room * half.tan();
                let lo = nearest(pts, a0);
                let hi = nearest(pts, b1);
                let span: Vec<usize> = if hi > lo {
                    (lo..=hi).collect()
                } else if arc.closed() {
                    (lo..pts.len()).chain(0..=hi).collect()
                } else {
                    continue;
                };
                let near: Vec<P> = span.into_iter().filter(|&k| keep[k]).map(|k| pts[k]).filter(|p| norm(sub(*p, x)) <= room + 1.0).collect();
                if near.len() < 3 {
                    continue;
                }
                let r = rects::fit_fillet(&near, x, da, db, r_max);
                if r <= rects::CHAMFER_R {
                    continue;
                }
                if !fillet_holds(&near, x, da, db, r, tol, tol) {
                    continue;
                }
                found.push(Found { idx, i, j, x, da, db, near, r, r_max });
                break;
            }
        }
    }
    if found.is_empty() {
        return 0;
    }
    // one radius where they agree: a rectangle's radius first, else the group's
    let radii: Vec<f64> = found.iter().map(|f| f.r).collect();
    let mut target = radii.clone();
    let ones = vec![1.0; radii.len()];
    for (mean, members) in rects::cluster_1d(&radii, &ones, r_move) {
        let mut near_anchor: Option<f64> = None;
        for &v in anchors {
            if near_anchor.is_none_or(|b| (v - mean).abs() < (b - mean).abs() || ((v - mean).abs() == (b - mean).abs() && v < b)) {
                near_anchor = Some(v);
            }
        }
        let value = match near_anchor {
            Some(a) if (a - mean).abs() <= r_move => a,
            _ => mean,
        };
        for k in members {
            if (radii[k] - value).abs() <= r_move {
                target[k] = value;
            }
        }
    }
    // rewrite from the back of each arc's list, so the indices still hold
    let mut order: Vec<usize> = (0..found.len()).collect();
    order.sort_by_key(|&k| (found[k].idx, std::cmp::Reverse(found[k].i)));
    let mut changed = 0;
    for k in order {
        let f = &found[k];
        let mut want = target[k];
        if want != f.r && (want > f.r_max || !fillet_holds(&f.near, f.x, f.da, f.db, want, FINAL_SLACK * tol, 2.0 * tol)) {
            want = f.r;
        }
        if let Some(log) = log.as_deref_mut() {
            log.push(vec![3.0, f.idx as f64, f.i as f64, f.j as f64, f.r, f.r_max, want]);
        }
        let (t1, t2, sweep) = rects::fillet_points(f.x, f.da, f.db, want);
        let arc = &mut bnd.arcs[f.idx];
        arc.segments[f.i].set_end_pub(t1);
        arc.segments[f.j].set_start_pub(t2);
        let fillet = Segment::Arc { p0: t1, p1: t2, r: want, large: false, sweep };
        arc.segments.splice(f.i + 1..f.j, [fillet.clone()]);
        resample_corner(arc, f.x, f.da, f.db, t1, t2, &fillet);
        changed += 1;
    }
    changed
}

/// The placed vertices round a rewritten corner, taken again from the lines
/// and the arc. See the Python `_resample_corner`.
fn resample_corner(arc: &mut Arc, x: P, da: P, db: P, t1: P, t2: P, fillet: &Segment) {
    let n = arc.pts.len() as i64;
    let reach = norm(sub(t1, x)) + 1.5;
    let mid = seg_arc_points(fillet, 3)[1];
    let k0 = nearest(&arc.pts, mid) as i64;
    let (mut lo, mut hi) = (k0, k0);
    let (first, last) = if arc.closed() { (0, n - 1) } else { (1, n - 2) };
    let pts = arc.pts.clone();
    let dist = |k: i64| norm(sub(pts[k as usize], x));
    while lo - 1 >= first && dist(lo - 1) <= reach && hi - lo < n - 2 {
        lo -= 1;
    }
    while hi + 1 <= last && dist(hi + 1) <= reach && hi - lo < n - 2 {
        hi += 1;
    }
    if hi - lo < 2 || arc.normal.len() as i64 != n {
        return;
    }
    let (lo, hi) = (lo as usize, hi as usize);
    let start = {
        let s = dot(sub(pts[lo], x), da);
        [x[0] + da[0] * s, x[1] + da[1] * s]
    };
    let end = {
        let s = dot(sub(pts[hi], x), db);
        [x[0] + db[0] * s, x[1] + db[1] * s]
    };
    let mut parts: Vec<Vec<P>> = Vec::new();
    for (p, q) in [(start, t1), (t2, end)] {
        let k = 2usize.max(norm(sub(q, p)).ceil() as usize + 1);
        parts.push(line_points(p, q, k));
    }
    let k = 2usize.max(np_length(&seg_arc_points(fillet, 33)).ceil() as usize + 1);
    let mut new: Vec<P> = parts[0][..parts[0].len() - 1].to_vec();
    let arcp = seg_arc_points(fillet, k);
    new.extend(arcp[..arcp.len() - 1].iter().copied());
    new.extend(parts[1].iter().copied());
    let mut normal = curve_normals(&new, false);
    if vote(&pts[lo..=hi], &arc.normal[lo..=hi], &new, &normal) < 0.0 {
        for v in normal.iter_mut() {
            *v = [-v[0], -v[1]];
        }
    }
    let mut out_pts = pts[..lo].to_vec();
    out_pts.extend(new.iter().copied());
    out_pts.extend(pts[hi + 1..].iter().copied());
    let mut out_n = arc.normal[..lo].to_vec();
    out_n.extend(normal);
    out_n.extend(arc.normal[hi + 1..].iter().copied());
    arc.pts = out_pts;
    arc.normal = out_n;
    if let Some(sl) = &arc.sliver {
        let mut s = sl[..lo].to_vec();
        s.extend(std::iter::repeat_n(false, new.len()));
        s.extend(sl[hi + 1..].iter().copied());
        arc.sliver = Some(s);
    }
}

fn fillet_holds(pts: &[P], x: P, da: P, db: P, r: f64, p95: f64, worst: f64) -> bool {
    let d = rects::fillet_dist(pts, x, da, db, r);
    let max = d.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    np_percentile(&d, 95.0) <= p95 && max <= worst
}

/// Move the node at p to q: every arc there ends at q, and the fitted
/// segments of the arcs not in `keep` move their end with it, a cubic its arm
/// too. See the Python `_move_node`.
fn move_node(arcs: &mut [Arc], p: P, q: P, keep: &HashSet<usize>) {
    if norm(sub(q, p)) <= 1e-12 {
        return;
    }
    let delta = sub(q, p);
    for (idx, at_end) in arcs_at(arcs, p) {
        let arc = &mut arcs[idx];
        let k = if at_end { arc.pts.len() - 1 } else { 0 };
        arc.pts[k] = q;
        if keep.contains(&idx) || arc.segments.is_empty() {
            continue;
        }
        if at_end {
            let last = arc.segments.len() - 1;
            match &mut arc.segments[last] {
                Segment::Cubic { c2, p1, .. } => {
                    *p1 = q;
                    *c2 = [c2[0] + delta[0], c2[1] + delta[1]];
                }
                seg => seg.set_end_pub(q),
            }
        } else {
            match &mut arc.segments[0] {
                Segment::Cubic { p0, c1, .. } => {
                    *p0 = q;
                    *c1 = [c1[0] + delta[0], c1[1] + delta[1]];
                }
                seg => seg.set_start_pub(q),
            }
        }
    }
}
