//! Stage 6a: the boundary as a planar graph, so neighbours share their edge.
//!
//! Every earlier version of this stage traced each region on its own: its own
//! coverage field, its own marching-squares polyline, its own Bézier fit. Two
//! regions that share an edge therefore described that one edge twice, and the
//! two descriptions were free to drift apart by up to the fitting tolerance each
//! way. What falls between them is painted by neither, so the backdrop shows
//! through as a hairline — dark in a dark preview, white in an exported file.
//!
//! Here the boundary is built once, as a graph. *Nodes* are the lattice points
//! where three or more labels meet. *Arcs* are the maximal runs of lattice edges
//! between two nodes that separate the same pair of labels. Each arc is placed
//! at sub-pixel precision once and fitted once, and the two regions either side
//! of it are handed the same curve, reversed for one of them. They cannot
//! disagree, at any tolerance, because there is only one description of the edge.
//!
//! This mirrors `engines/vexel/topology.py` stage for stage.

use std::collections::HashMap;

use crate::boundary::FillAt;
use crate::core::grid::{Grid, Image};
use crate::core::labels::Labels;
use crate::curves::{
    fit_contour_segments, fit_open, line_through, normalize, reverse_segments, CurveParams, Segment, P,
};

/// How far a shape reaches under the shapes painted over it. One pixel covers an
/// anti-aliased edge, and stays well inside any region wide enough not to have
/// been recovered as a stroke instead.
pub const BLEED: f64 = 1.0;
/// Arc length over which that reach eases off at a junction. Zero still pins the
/// very last vertex to the node — all that is needed to keep the ring closed —
/// and bleeds everything else fully; easing over a longer run measurably reopens
/// the seam near junctions.
pub const TAPER: f64 = 0.0;
/// The bled copy's fitting tolerance, as a fraction of the bleed. It has to stay
/// below it: an error larger than the offset would let the copy wander back over
/// the edge it exists to cover.
pub const UNDER_TOL: f64 = 0.6;
/// How far past the two pixels either side of a label edge the half-coverage
/// search may reach, in pixels.
pub const REACH: f64 = 0.75;

const RIGHT: u8 = 0;
const DOWN: u8 = 1;
const LEFT: u8 = 2;
const UP: u8 = 3;
const STEP: [(i64, i64); 4] = [(0, 1), (1, 0), (0, -1), (-1, 0)];
// For a directed lattice edge leaving (i, j), the padded pixel on its left and
// on its right. The edge bounds a shape when the shape holds the left pixel and
// not the right one, so every ring below runs with the shape on its left.
const LEFT_PIXEL: [(i64, i64); 4] = [(-1, 0), (0, 0), (0, -1), (-1, -1)];
const RIGHT_PIXEL: [(i64, i64); 4] = [(0, 0), (0, -1), (-1, -1), (-1, 0)];

/// One run of boundary between two nodes, parting one pair of labels.
pub struct Arc {
    pub pair: (i32, i32),
    pub pts: Vec<P>,
    /// Unit step per vertex, from the `pair.0` side towards the `pair.1` side.
    pub normal: Vec<P>,
    pub n0: Option<u64>,
    pub n1: Option<u64>,
    pub segments: Vec<Segment>,
    /// The same curve, bled under whichever side paints later.
    pub under: Vec<Segment>,
    pub t0: Option<P>,
    pub t1: Option<P>,
}

impl Arc {
    pub fn closed(&self) -> bool {
        self.n0.is_none()
    }
}

pub struct Boundary {
    pub arcs: Vec<Arc>,
    /// The label map with a one-pixel border of 0, standing for outside the canvas.
    pub padded: Labels,
    edge_arc: HashMap<u64, (usize, usize)>,
    later_is_b: Vec<bool>,
}

#[inline]
fn edge_key(kind: u64, i: u64, j: u64, lat_cols: u64) -> u64 {
    (i * lat_cols + j) * 2 + kind
}

#[inline]
fn undirected(i: i64, j: i64, d: u8, lat_cols: u64) -> u64 {
    match d {
        RIGHT => edge_key(0, i as u64, j as u64, lat_cols),
        LEFT => edge_key(0, i as u64, (j - 1) as u64, lat_cols),
        DOWN => edge_key(1, i as u64, j as u64, lat_cols),
        _ => edge_key(1, (i - 1) as u64, j as u64, lat_cols),
    }
}

struct Edges {
    /// vertex -> the boundary edges meeting there
    incident: HashMap<u64, Vec<u64>>,
    /// edge -> the two padded pixels it parts
    pixels: HashMap<u64, ((usize, usize), (usize, usize))>,
    /// edge -> its two lattice endpoints
    ends: HashMap<u64, (u64, u64)>,
}

/// Every lattice edge with different labels either side.
fn boundary_edges(padded: &Labels) -> Edges {
    let lat_cols = (padded.w + 1) as u64;
    let mut e = Edges { incident: HashMap::new(), pixels: HashMap::new(), ends: HashMap::new() };

    let add = |edges: &mut Edges, key: u64, a: u64, b: u64| {
        edges.incident.entry(a).or_default().push(key);
        edges.incident.entry(b).or_default().push(key);
        edges.ends.insert(key, (a, b));
    };

    // Horizontal edge (i, j)->(i, j+1) parts pixel (i-1, j) above from (i, j) below.
    for i in 1..padded.h {
        for j in 0..padded.w {
            if padded.get(i - 1, j) != padded.get(i, j) {
                let key = edge_key(0, i as u64, j as u64, lat_cols);
                e.pixels.insert(key, ((i - 1, j), (i, j)));
                add(&mut e, key, i as u64 * lat_cols + j as u64, i as u64 * lat_cols + j as u64 + 1);
            }
        }
    }
    // Vertical edge (i, j)->(i+1, j) parts pixel (i, j-1) left from (i, j) right.
    for i in 0..padded.h {
        for j in 1..padded.w {
            if padded.get(i, j - 1) != padded.get(i, j) {
                let key = edge_key(1, i as u64, j as u64, lat_cols);
                e.pixels.insert(key, ((i, j - 1), (i, j)));
                add(&mut e, key, i as u64 * lat_cols + j as u64, (i as u64 + 1) * lat_cols + j as u64);
            }
        }
    }
    e
}

struct Chain {
    edges: Vec<u64>,
    pair: (i32, i32),
    n0: Option<u64>,
    n1: Option<u64>,
}

/// Cut the boundary into arcs: maximal same-pair runs between nodes.
fn chains(padded: &Labels, e: &Edges) -> Vec<Chain> {
    let pair_of = |key: u64| -> (i32, i32) {
        let (pa, pb) = e.pixels[&key];
        let (a, b) = (*padded.get(pa.0, pa.1), *padded.get(pb.0, pb.1));
        if a < b {
            (a, b)
        } else {
            (b, a)
        }
    };
    let pair: HashMap<u64, (i32, i32)> = e.pixels.keys().map(|k| (*k, pair_of(*k))).collect();

    // A vertex is a node unless it is a plain pass-through of a single pair.
    let mut nodes: Vec<u64> = e
        .incident
        .iter()
        .filter(|(_, keys)| keys.len() != 2 || pair[&keys[0]] != pair[&keys[1]])
        .map(|(v, _)| *v)
        .collect();
    nodes.sort_unstable();
    let node_set: std::collections::HashSet<u64> = nodes.iter().copied().collect();

    let step = |key: u64, v: u64| -> u64 {
        let (a, b) = e.ends[&key];
        if a == v {
            b
        } else {
            a
        }
    };

    let mut used: std::collections::HashSet<u64> = std::collections::HashSet::new();
    let mut out: Vec<Chain> = Vec::new();

    for v in &nodes {
        let mut starts = e.incident[v].clone();
        starts.sort_unstable();
        for first in starts {
            if used.contains(&first) {
                continue;
            }
            let mut chain = vec![first];
            used.insert(first);
            let mut cur = step(first, *v);
            let mut key = first;
            while !node_set.contains(&cur) {
                let next: Vec<u64> =
                    e.incident[&cur].iter().copied().filter(|k| *k != key && !used.contains(k)).collect();
                if next.len() != 1 {
                    break;
                }
                key = next[0];
                used.insert(key);
                chain.push(key);
                cur = step(key, cur);
            }
            out.push(Chain { pair: pair[&first], edges: chain, n0: Some(*v), n1: Some(cur) });
        }
    }

    // What is left is a loop with no node on it: a region wholly inside one neighbour.
    let mut loose: Vec<u64> = e.pixels.keys().copied().filter(|k| !used.contains(k)).collect();
    loose.sort_unstable();
    for key in loose {
        if used.contains(&key) {
            continue;
        }
        let mut chain = vec![key];
        used.insert(key);
        let mut cur = step(key, e.ends[&key].0);
        let mut cur_key = key;
        loop {
            let next: Vec<u64> =
                e.incident[&cur].iter().copied().filter(|k| *k != cur_key && !used.contains(k)).collect();
            if next.len() != 1 {
                break;
            }
            cur_key = next[0];
            used.insert(cur_key);
            chain.push(cur_key);
            cur = step(cur_key, cur);
        }
        out.push(Chain { pair: pair[&key], edges: chain, n0: None, n1: None });
    }
    out
}

/// How much of each pixel is `lab` rather than `other`, read from its colour.
fn coverage(
    rgb: &Image,
    alpha: &Grid<f64>,
    pix: &[(usize, usize)],
    lab: i32,
    other: i32,
    fill_at: FillAt,
) -> Vec<f64> {
    // Padded pixel (r, c) is image pixel (r-1, c-1), centred at (c-0.5, r-0.5).
    let qx: Vec<f64> = pix.iter().map(|p| p.1 as f64 - 0.5).collect();
    let qy: Vec<f64> = pix.iter().map(|p| p.0 as f64 - 0.5).collect();
    let f_a = fill_at(lab, &qx, &qy);
    let f_b = fill_at(other, &qx, &qy);
    pix.iter()
        .enumerate()
        .map(|(k, p)| {
            let (r, c) = (p.0 as i64 - 1, p.1 as i64 - 1);
            if r < 0 || c < 0 || r >= rgb.h as i64 || c >= alpha.w as i64 {
                return f64::NAN;
            }
            let (r, c) = (r as usize, c as usize);
            let px = rgb.at(r, c);
            let colour = [px[0], px[1], px[2], *alpha.get(r, c) * 255.0];
            let mut denom = 0.0;
            let mut proj = 0.0;
            for ch in 0..4 {
                let d = f_a[k][ch] - f_b[k][ch];
                denom += d * d;
                proj += (colour[ch] - f_b[k][ch]) * d;
            }
            if denom > 1e-6 {
                proj / denom.max(1e-9)
            } else {
                f64::NAN
            }
        })
        .collect()
}

/// Where coverage passes a half along the line joining two pixel centres, as a
/// fraction of the step from the `a` pixel to the `b` pixel.
///
/// Sampling only those two pixels would nail the outline to the label boundary,
/// and the label boundary is not always right: the partition chamfers a hard
/// corner, dropping the corner pixel into the neighbour even though its colour
/// is plainly the shape's. The old per-region coverage field quietly repaired
/// that, because its half-level could sit a pixel off the labels. So the search
/// reaches one pixel further out on each side — never past a pixel belonging to
/// a third region — and the outline goes where the colour says.
#[allow(clippy::too_many_arguments)]
fn crossing(
    padded: &Labels,
    rgb: &Image,
    alpha: &Grid<f64>,
    p_in: &[(usize, usize)],
    p_out: &[(usize, usize)],
    a: i32,
    b: i32,
    fill_at: FillAt,
) -> Vec<f64> {
    let n = p_in.len();
    let here_raw = coverage(rgb, alpha, p_in, a, b, fill_at);
    let there_raw = coverage(rgb, alpha, p_out, a, b, fill_at);
    let here: Vec<f64> = here_raw.iter().map(|v| if v.is_finite() { *v } else { 1.0 }).collect();
    let there: Vec<f64> = there_raw.iter().map(|v| if v.is_finite() { *v } else { 0.0 }).collect();

    // One step further out on each side, when that pixel still belongs to the
    // same region; otherwise fall back to the near sample.
    let outward = |from: &[(usize, usize)], towards: &[(usize, usize)], want: i32, near: &[f64]| -> Vec<f64> {
        let mut pix = Vec::with_capacity(n);
        let mut valid = Vec::with_capacity(n);
        for k in 0..n {
            let dr = from[k].0 as i64 - towards[k].0 as i64;
            let dc = from[k].1 as i64 - towards[k].1 as i64;
            let (r, c) = (from[k].0 as i64 + dr, from[k].1 as i64 + dc);
            let inside = r >= 0 && c >= 0 && (r as usize) < padded.h && (c as usize) < padded.w;
            let ok = inside && *padded.get(r as usize, c as usize) == want;
            valid.push(ok);
            pix.push(if ok { (r as usize, c as usize) } else { from[k] });
        }
        let cov = coverage(rgb, alpha, &pix, a, b, fill_at);
        (0..n).map(|k| if valid[k] && cov[k].is_finite() { cov[k] } else { near[k] }).collect()
    };
    let before = outward(p_in, p_out, a, &here);
    let after = outward(p_out, p_in, b, &there);

    let at = [-1.0, 0.0, 1.0, 2.0];
    (0..n)
        .map(|k| {
            let level = [before[k], here[k], there[k], after[k]];
            let mut t = 0.5;
            for s in 0..3 {
                let (lo, hi) = (level[s], level[s + 1]);
                if lo >= 0.5 && hi < 0.5 {
                    t = at[s] + (lo - 0.5) / (lo - hi).max(1e-9);
                    break;
                }
            }
            t.clamp(-REACH, 1.0 + REACH)
        })
        .collect()
}

/// Sub-pixel position, and the side-to-side step, for every lattice edge of every arc.
fn place(
    chains: &[Chain],
    e: &Edges,
    padded: &Labels,
    rgb: &Image,
    alpha: &Grid<f64>,
    fill_at: FillAt,
) -> Vec<(Vec<P>, Vec<P>)> {
    chains
        .iter()
        .map(|ch| {
            let (a, b) = ch.pair;
            let mut p_in = Vec::with_capacity(ch.edges.len());
            let mut p_out = Vec::with_capacity(ch.edges.len());
            for key in &ch.edges {
                let (pa, pb) = e.pixels[key];
                if *padded.get(pa.0, pa.1) == a {
                    p_in.push(pa);
                    p_out.push(pb);
                } else {
                    p_in.push(pb);
                    p_out.push(pa);
                }
            }
            let t = if a != 0 && b != 0 {
                crossing(padded, rgb, alpha, &p_in, &p_out, a, b, fill_at)
            } else {
                vec![0.5; ch.edges.len()]
            };
            let mut pts = Vec::with_capacity(ch.edges.len());
            let mut normal = Vec::with_capacity(ch.edges.len());
            for k in 0..ch.edges.len() {
                let c_in = [p_in[k].1 as f64 - 0.5, p_in[k].0 as f64 - 0.5];
                let c_out = [p_out[k].1 as f64 - 0.5, p_out[k].0 as f64 - 0.5];
                // `c_in` is always the `a` pixel's centre and `c_out` the `b`
                // pixel's, so this step points from one side of the arc to the
                // other. It is one pixel long and axis aligned already.
                let step = [c_out[0] - c_in[0], c_out[1] - c_in[1]];
                pts.push([c_in[0] + t[k] * step[0], c_in[1] + t[k] * step[1]]);
                normal.push(step);
            }
            (pts, normal)
        })
        .collect()
}

/// Total-least-squares line through an arc's run-up to one end, skipping the
/// half-pixel marching-squares chamfer at the end itself.
fn approach(pts: &[P], from_start: bool, reach: f64, trim: f64) -> Option<(P, P)> {
    let anchor = if from_start { pts[0] } else { pts[pts.len() - 1] };
    let mut take: Vec<P> = Vec::new();
    let mut wide: Vec<P> = Vec::new();
    for p in pts {
        let d = ((p[0] - anchor[0]).powi(2) + (p[1] - anchor[1]).powi(2)).sqrt();
        if d >= trim && d <= reach {
            take.push(*p);
        }
        if d > 0.0 && d <= 2.0 * reach {
            wide.push(*p);
        }
    }
    let pts = if take.len() >= 2 { take } else { wide };
    if pts.len() < 2 {
        return None;
    }
    Some(line_through(&pts))
}

/// Place each node, and give arcs that run through it a shared tangent.
///
/// Marching squares chamfers a junction the way it chamfers a corner, and each
/// arc arrives at its own chamfered end. Fitting a line to each arc's approach
/// and taking the point closest to all of them recovers the junction and hands
/// every arc the same one. Then, where a third region merely ends against a
/// boundary that carries on — a mark's silhouette, with the colour changing
/// along it — two of the arcs are one smooth curve, and pinning both to a single
/// tangent stops the outline hitching where the fill changes.
fn junctions(arcs: &mut [Arc], corner_threshold: f64) {
    const REACH_PX: f64 = 4.0;
    const TRIM: f64 = 0.8;
    const LIMIT: f64 = 2.0;

    let mut ends: HashMap<u64, Vec<(usize, bool)>> = HashMap::new();
    for (idx, arc) in arcs.iter().enumerate() {
        if arc.closed() || arc.pts.len() < 2 {
            continue;
        }
        ends.entry(arc.n0.unwrap()).or_default().push((idx, true));
        ends.entry(arc.n1.unwrap()).or_default().push((idx, false));
    }
    let mut keys: Vec<u64> = ends.keys().copied().collect();
    keys.sort_unstable();

    for node in keys {
        let incident = ends[&node].clone();
        let lines: Vec<Option<(P, P)>> = incident
            .iter()
            .map(|(i, at_start)| approach(&arcs[*i].pts, *at_start, REACH_PX, TRIM))
            .collect();

        let mut mean = [0.0, 0.0];
        for (i, at_start) in &incident {
            let p = if *at_start { arcs[*i].pts[0] } else { arcs[*i].pts[arcs[*i].pts.len() - 1] };
            mean[0] += p[0];
            mean[1] += p[1];
        }
        mean[0] /= incident.len() as f64;
        mean[1] /= incident.len() as f64;

        let mut target = mean;
        let usable: Vec<(P, P)> = lines.iter().filter_map(|l| *l).collect();
        if usable.len() >= 2 {
            // Sum of the projectors onto each line's normal: the point closest
            // to all of them at once.
            let (mut m00, mut m01, mut m11) = (0.0f64, 0.0f64, 0.0f64);
            let (mut r0, mut r1) = (0.0f64, 0.0f64);
            for (point, dir) in &usable {
                let n = [1.0 - dir[0] * dir[0], -dir[0] * dir[1], 1.0 - dir[1] * dir[1]];
                m00 += n[0];
                m01 += n[1];
                m11 += n[2];
                r0 += n[0] * point[0] + n[1] * point[1];
                r1 += n[1] * point[0] + n[2] * point[1];
            }
            let det = m00 * m11 - m01 * m01;
            if det.abs() > 1e-9 {
                let guess = [(m11 * r0 - m01 * r1) / det, (m00 * r1 - m01 * r0) / det];
                let off = ((guess[0] - mean[0]).powi(2) + (guess[1] - mean[1]).powi(2)).sqrt();
                if off <= LIMIT {
                    target = guess;
                }
            }
        }
        for (i, at_start) in &incident {
            let last = arcs[*i].pts.len() - 1;
            arcs[*i].pts[if *at_start { 0 } else { last }] = target;
        }

        // Which two arcs, if any, are one curve passing through?
        let away: Vec<P> = incident
            .iter()
            .zip(lines.iter())
            .map(|((i, at_start), line)| {
                let pts = &arcs[*i].pts;
                let far = if *at_start { pts[3.min(pts.len() - 1)] } else { pts[pts.len() - 1 - 3.min(pts.len() - 1)] };
                let outward = [far[0] - target[0], far[1] - target[1]];
                match line {
                    Some((_, dir)) => {
                        let dot = dir[0] * outward[0] + dir[1] * outward[1];
                        if dot > 0.0 {
                            normalize(*dir)
                        } else if dot < 0.0 {
                            normalize([-dir[0], -dir[1]])
                        } else {
                            normalize(outward)
                        }
                    }
                    None => normalize(outward),
                }
            })
            .collect();

        let mut best: Option<(f64, usize, usize)> = None;
        for x in 0..away.len() {
            for y in (x + 1)..away.len() {
                let dot = -(away[x][0] * away[y][0] + away[x][1] * away[y][1]);
                let turn = dot.clamp(-1.0, 1.0).acos().to_degrees();
                if best.is_none() || turn < best.unwrap().0 {
                    best = Some((turn, x, y));
                }
            }
        }
        let Some((turn, x, y)) = best else { continue };
        if turn > corner_threshold {
            continue;
        }
        let shared = normalize([away[x][0] - away[y][0], away[x][1] - away[y][1]]);
        if shared[0] == 0.0 && shared[1] == 0.0 {
            continue;
        }
        for (slot, sign) in [(x, 1.0), (y, -1.0)] {
            let (i, at_start) = incident[slot];
            let t = [shared[0] * sign, shared[1] * sign];
            if at_start {
                arcs[i].t0 = Some(t);
            } else {
                arcs[i].t1 = Some(t);
            }
        }
    }
}

/// The arc pushed `amount` towards one side, pinned back to its own ends.
///
/// The ends are nodes that the other arcs meeting there have been fitted to, so
/// the bleed has to reach zero at them or the ring tears open. It reaches zero
/// at the end vertex itself, and over `taper` pixels before it.
fn bled(arc: &Arc, amount: f64, taper: f64) -> Vec<P> {
    let pts = &arc.pts;
    let n = pts.len();
    if n < 3 {
        return pts.clone();
    }
    // The per-vertex step between pixel centres is axis aligned, so it zigzags
    // along a diagonal run and offsetting by it would fold the curve into a
    // staircase. Take the normal from the curve's own tangent instead, and only
    // borrow the step's sign to point it at the right side.
    let closed = arc.closed();
    let mut cum = vec![0.0f64; n];
    for k in 1..n {
        cum[k] = cum[k - 1] + ((pts[k][0] - pts[k - 1][0]).powi(2) + (pts[k][1] - pts[k - 1][1]).powi(2)).sqrt();
    }
    let total = cum[n - 1];

    (0..n)
        .map(|k| {
            let ahead = if closed { pts[(k + 1) % n] } else { pts[(k + 1).min(n - 1)] };
            let behind = if closed { pts[(k + n - 1) % n] } else { pts[k.saturating_sub(1)] };
            let tangent = normalize([ahead[0] - behind[0], ahead[1] - behind[1]]);
            let mut normal = [-tangent[1], tangent[0]];
            if normal[0] * arc.normal[k][0] + normal[1] * arc.normal[k][1] < 0.0 {
                normal = [-normal[0], -normal[1]];
            }
            let scale = if closed {
                1.0
            } else {
                (cum[k].min(total - cum[k]) / taper.max(1e-6)).clamp(0.0, 1.0)
            };
            [pts[k][0] + amount * scale * normal[0], pts[k][1] + amount * scale * normal[1]]
        })
        .collect()
}

/// Interior corners of an open arc: a turn that survives every chord scale.
fn open_corners(pts: &[P], threshold_deg: f64) -> Vec<usize> {
    const SCALES: [f64; 2] = [2.0, 4.0];
    let n = pts.len();
    if n < 5 {
        return Vec::new();
    }
    let mut cum = vec![0.0f64; n];
    for k in 1..n {
        cum[k] = cum[k - 1] + ((pts[k][0] - pts[k - 1][0]).powi(2) + (pts[k][1] - pts[k - 1][1]).powi(2)).sqrt();
    }
    let total = cum[n - 1];
    if total < 2.0 * SCALES[1] {
        return Vec::new();
    }
    let at = |s: f64| -> P {
        let s = s.clamp(0.0, total);
        let mut lo = 0usize;
        let mut hi = n - 1;
        while lo + 1 < hi {
            let mid = (lo + hi) / 2;
            if cum[mid] <= s {
                lo = mid;
            } else {
                hi = mid;
            }
        }
        let span = cum[hi] - cum[lo];
        let f = if span <= 0.0 { 0.0 } else { (s - cum[lo]) / span };
        [pts[lo][0] + f * (pts[hi][0] - pts[lo][0]), pts[lo][1] + f * (pts[hi][1] - pts[lo][1])]
    };

    let mut angles = vec![f64::INFINITY; n];
    for s in SCALES {
        for k in 0..n {
            let back = at(cum[k] - s);
            let fwd = at(cum[k] + s);
            let v1 = [pts[k][0] - back[0], pts[k][1] - back[1]];
            let v2 = [fwd[0] - pts[k][0], fwd[1] - pts[k][1]];
            let n1 = (v1[0] * v1[0] + v1[1] * v1[1]).sqrt();
            let n2 = (v2[0] * v2[0] + v2[1] * v2[1]).sqrt();
            let ang = if n1 < 1e-9 || n2 < 1e-9 {
                0.0
            } else {
                ((v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)).clamp(-1.0, 1.0).acos().to_degrees()
            };
            angles[k] = angles[k].min(ang);
        }
    }
    // The ends are nodes: already placed, already tangent-matched, and the chord
    // either side of them is truncated, which biases the angle there.
    const GUARD: f64 = 1.0;
    for k in 0..n {
        if cum[k] < GUARD || cum[k] > total - GUARD {
            angles[k] = 0.0;
        }
    }

    let mut keep: Vec<usize> = Vec::new();
    for k in 0..n {
        if angles[k] <= threshold_deg {
            continue;
        }
        if let Some(&last) = keep.last() {
            if cum[k] - cum[last] <= SCALES[0] {
                if angles[k] > angles[last] {
                    *keep.last_mut().unwrap() = k;
                }
                continue;
            }
        }
        keep.push(k);
    }
    keep
}

/// One run between two breaks, with the chamfer dropped at any end that is a
/// corner. The end points themselves stand: an arc end is a node that every
/// neighbouring arc has already agreed on.
fn sharpen_piece(pts: &[P], lo: usize, hi: usize, corners: &[usize]) -> Vec<P> {
    const TRIM: f64 = 0.8;
    let piece = &pts[lo..=hi];
    if piece.len() < 4 {
        return piece.to_vec();
    }
    let drop_lo = corners.contains(&lo);
    let drop_hi = corners.contains(&hi);
    let mut out = vec![piece[0]];
    for p in &piece[1..piece.len() - 1] {
        let near_lo = ((p[0] - piece[0][0]).powi(2) + (p[1] - piece[0][1]).powi(2)).sqrt() < TRIM;
        let last = piece[piece.len() - 1];
        let near_hi = ((p[0] - last[0]).powi(2) + (p[1] - last[1]).powi(2)).sqrt() < TRIM;
        if (drop_lo && near_lo) || (drop_hi && near_hi) {
            continue;
        }
        out.push(*p);
    }
    out.push(piece[piece.len() - 1]);
    out
}

fn fit_arc(pts: &[P], closed: bool, t0: Option<P>, t1: Option<P>, params: &CurveParams) -> Vec<Segment> {
    if closed {
        // A region wholly inside one neighbour: no node anywhere on it, so this
        // is an ordinary closed contour and the closed fit is the right one. It
        // keeps the curve G1 across the seam and looks for corners around the
        // wrap, neither of which an open fit can do.
        return if pts.len() < 3 { Vec::new() } else { fit_contour_segments(pts, params) };
    }
    if pts.len() < 2 {
        return Vec::new();
    }
    let corners = open_corners(pts, params.corner_threshold);
    let mut bounds = vec![0usize];
    bounds.extend_from_slice(&corners);
    bounds.push(pts.len() - 1);

    let mut segments: Vec<Segment> = Vec::new();
    for k in 0..bounds.len() - 1 {
        let (lo, hi) = (bounds[k], bounds[k + 1]);
        if hi <= lo {
            continue;
        }
        let piece = sharpen_piece(pts, lo, hi, &corners);
        if piece.len() < 2 {
            continue;
        }
        let ts = if lo == 0 { t0 } else { None };
        let te = if hi == pts.len() - 1 { t1 } else { None };
        segments.extend(fit_open(&piece, params.tol, ts, te));
    }
    snap_axis(segments, params.snap_axis_deg)
}

/// Make a nearly horizontal or vertical line exactly so, as
/// `curves::snap_axis_lines` does for a whole contour — but never moving the
/// arc's own ends, which are nodes the arcs on the other side were fitted to.
fn snap_axis(mut segments: Vec<Segment>, snap_deg: f64) -> Vec<Segment> {
    let n = segments.len();
    for i in 0..n {
        let Segment::Line { p0, p1 } = segments[i] else { continue };
        let ang = (p1[1] - p0[1]).atan2(p1[0] - p0[0]).to_degrees().rem_euclid(180.0);
        let axis = if ang.min(180.0 - ang) <= snap_deg {
            1
        } else if (ang - 90.0).abs() <= snap_deg {
            0
        } else {
            continue;
        };
        let (head, tail) = (i == 0, i + 1 == n);
        if head && tail {
            continue;
        }
        let value = if head {
            p1[axis]
        } else if tail {
            p0[axis]
        } else {
            (p0[axis] + p1[axis]) / 2.0
        };
        let (mut a, mut b) = (p0, p1);
        if !head {
            a[axis] = value;
        }
        if !tail {
            b[axis] = value;
        }
        segments[i] = Segment::Line { p0: a, p1: b };
        if !head {
            segments[i - 1].set_end_pub(a);
        }
        if !tail {
            segments[i + 1].set_start_pub(b);
        }
    }
    segments
}

/// The whole boundary of the label map, placed sub-pixel and fitted once.
///
/// `rank` is the paint order by label. Given it, each arc also gets a copy bled
/// towards whichever side paints later, for the earlier side to use.
pub fn build(
    labels: &Labels,
    rgb: &Image,
    alpha: &Grid<f64>,
    fill_at: FillAt,
    params: &CurveParams,
    rank: Option<&HashMap<i32, usize>>,
) -> Boundary {
    let mut padded = Grid::<i32>::new(labels.h + 2, labels.w + 2);
    for r in 0..labels.h {
        for c in 0..labels.w {
            padded.set(r + 1, c + 1, *labels.get(r, c));
        }
    }

    let edges = boundary_edges(&padded);
    let chain_list = chains(&padded, &edges);
    let placed = place(&chain_list, &edges, &padded, rgb, alpha, fill_at);

    let mut arcs: Vec<Arc> = chain_list
        .iter()
        .zip(placed.into_iter())
        .map(|(ch, (pts, normal))| Arc {
            pair: ch.pair,
            pts,
            normal,
            n0: ch.n0,
            n1: ch.n1,
            segments: Vec::new(),
            under: Vec::new(),
            t0: None,
            t1: None,
        })
        .collect();

    junctions(&mut arcs, params.corner_threshold);
    for arc in arcs.iter_mut() {
        arc.segments = fit_arc(&arc.pts, arc.closed(), arc.t0, arc.t1, params);
    }

    let mut later_is_b = Vec::with_capacity(arcs.len());
    for arc in arcs.iter_mut() {
        let (a, b) = arc.pair;
        let b_later = match rank {
            Some(r) => r.get(&b).map_or(-1i64, |v| *v as i64) > r.get(&a).map_or(-1i64, |v| *v as i64),
            None => false,
        };
        later_is_b.push(b_later);
        if rank.is_some() && BLEED > 0.0 && a != 0 && b != 0 {
            // The bled copy is never seen — the shape that causes it covers it —
            // so it is fitted loosely, but no looser than the bleed can absorb.
            let loose = CurveParams { tol: (2.0 * params.tol).min(UNDER_TOL * BLEED), ..*params };
            let moved = bled(arc, if b_later { BLEED } else { -BLEED }, TAPER);
            arc.under = fit_arc(&moved, arc.closed(), arc.t0, arc.t1, &loose);
        }
    }

    let mut edge_arc = HashMap::new();
    for (idx, ch) in chain_list.iter().enumerate() {
        for (pos, key) in ch.edges.iter().enumerate() {
            edge_arc.insert(*key, (idx, pos));
        }
    }
    Boundary { arcs, padded, edge_arc, later_is_b }
}

/// Closed rings of directed lattice edges with `inside` always on the left.
///
/// Crack following: at each vertex the walk prefers to turn left, then to go
/// straight, then to turn right, which resolves a diagonal touch the same way
/// four-connected labelling does and leaves every ring closed.
fn directed_rings(padded: &Labels, inside: &[bool]) -> Vec<Vec<(i64, i64, u8)>> {
    let (h, w) = (padded.h as i64, padded.w as i64);
    let held = |i: i64, j: i64| -> bool { i >= 0 && j >= 0 && i < h && j < w && inside[(i * w + j) as usize] };
    let valid = |i: i64, j: i64, d: u8| -> bool {
        let l = LEFT_PIXEL[d as usize];
        let r = RIGHT_PIXEL[d as usize];
        held(i + l.0, j + l.1) && !held(i + r.0, j + r.1)
    };

    let mut order: Vec<(i64, i64, u8)> = Vec::new();
    let mut pending: std::collections::HashSet<(i64, i64, u8)> = std::collections::HashSet::new();
    for i in 0..h {
        for j in 0..w {
            if !held(i, j) {
                continue;
            }
            for (d, v) in [(RIGHT, (i + 1, j)), (DOWN, (i, j)), (LEFT, (i, j + 1)), (UP, (i + 1, j + 1))] {
                if valid(v.0, v.1, d) && pending.insert((v.0, v.1, d)) {
                    order.push((v.0, v.1, d));
                }
            }
        }
    }

    let mut rings: Vec<Vec<(i64, i64, u8)>> = Vec::new();
    for seed in &order {
        if !pending.contains(seed) {
            continue;
        }
        let mut ring: Vec<(i64, i64, u8)> = Vec::new();
        let (mut i, mut j, mut d) = *seed;
        while pending.remove(&(i, j, d)) {
            ring.push((i, j, d));
            let (di, dj) = STEP[d as usize];
            i += di;
            j += dj;
            let mut found = false;
            for turn in [(d + 3) % 4, d, (d + 1) % 4, (d + 2) % 4] {
                if valid(i, j, turn) {
                    d = turn;
                    found = true;
                    break;
                }
            }
            if !found {
                break;
            }
        }
        if !ring.is_empty() {
            rings.push(ring);
        }
    }
    rings
}

/// Collapse a ring's per-edge (arc, position) list into whole-arc traversals.
fn runs(seq: &[(usize, usize)]) -> Vec<(usize, bool)> {
    if seq.is_empty() {
        return Vec::new();
    }
    // A ring can start part-way along an arc; rotate so it starts at an arc change.
    let mut start = None;
    for k in 1..seq.len() {
        if seq[k].0 != seq[k - 1].0 {
            start = Some(k);
            break;
        }
    }
    let Some(start) = start else {
        return vec![(seq[0].0, seq[seq.len() - 1].1 < seq[0].1)];
    };
    let rotated: Vec<(usize, usize)> = seq[start..].iter().chain(seq[..start].iter()).copied().collect();

    let mut out: Vec<(usize, bool)> = Vec::new();
    let mut k = 0usize;
    while k < rotated.len() {
        let arc = rotated[k].0;
        let mut j = k;
        while j + 1 < rotated.len() && rotated[j + 1].0 == arc {
            j += 1;
        }
        out.push((arc, rotated[j].1 < rotated[k].1));
        k = j + 1;
    }
    out
}

impl Boundary {
    /// Closed rings bounding the union of `labels`, as (arc index, reversed).
    pub fn rings(&self, labels: &std::collections::HashSet<i32>) -> Vec<Vec<(usize, bool)>> {
        let inside: Vec<bool> = self.padded.data.iter().map(|v| labels.contains(v)).collect();
        let lat_cols = (self.padded.w + 1) as u64;
        directed_rings(&self.padded, &inside)
            .into_iter()
            .filter_map(|ring| {
                let seq: Vec<(usize, usize)> = ring
                    .iter()
                    .filter_map(|(i, j, d)| self.edge_arc.get(&undirected(*i, *j, *d, lat_cols)).copied())
                    .collect();
                let r = runs(&seq);
                if r.is_empty() {
                    None
                } else {
                    Some(r)
                }
            })
            .collect()
    }

    /// The ring's sub-pixel polyline, for whole-shape primitive fitting.
    pub fn polyline(&self, ring: &[(usize, bool)]) -> Vec<P> {
        let mut out = Vec::new();
        for (idx, reverse) in ring {
            let pts = &self.arcs[*idx].pts;
            if *reverse {
                out.extend(pts.iter().rev().copied());
            } else {
                out.extend(pts.iter().copied());
            }
        }
        out
    }

    /// The ring's fitted curve: each arc's one fit, reversed where walked
    /// backwards. Where the region on the other side is painted *later*, the
    /// bled copy is used instead, so the neighbour's anti-aliased edge lands on
    /// this shape's ink rather than on the backdrop.
    pub fn segments(&self, ring: &[(usize, bool)], member: Option<&std::collections::HashSet<i32>>) -> Vec<Segment> {
        let mut out: Vec<Segment> = Vec::new();
        for (idx, reverse) in ring {
            let arc = &self.arcs[*idx];
            let mut segs = &arc.segments;
            if let Some(member) = member {
                if !arc.under.is_empty() {
                    let later = if self.later_is_b[*idx] { arc.pair.1 } else { arc.pair.0 };
                    if !member.contains(&later) {
                        segs = &arc.under;
                    }
                }
            }
            if *reverse {
                out.extend(reverse_segments(segs));
            } else {
                out.extend(segs.iter().cloned());
            }
        }
        out
    }
}
