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
    CORNER_REACH,
    CurveParams,
    MERGE_DEG,
    P,
    Segment,
    corners_from_runs,
    dist,
    fit_contour_segments,
    fit_open,
    fit_stretch,
    intersect,
    line_runs,
    line_through,
    merge_lines,
    normalize,
    reverse_segments,
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
/// An arc's approach to a node is read as a line over `reach` px at least, and
/// grows in 2 px steps to APPROACH_MAX while the run stays straight to
/// APPROACH_RMS: at a shallow junction the lines' directions place the node.
pub const APPROACH_MAX: f64 = 12.0;
pub const APPROACH_RMS: f64 = 0.08;
pub const FALLBACK_RMS: f64 = 0.5;
/// Not 1.5: lattice-midpoint vertices sit at exact multiples of a half. See the Python.
pub const APPROACH_TRIM: f64 = 1.6;
/// The crossing of the incident lines is trusted when a vertex's placement
/// noise, PLACEMENT_SIGMA px, projects to at most NODE_UNCERTAINTY px along the
/// weakest direction of the crossing. See the Python.
pub const PLACEMENT_SIGMA: f64 = 0.06;
pub const NODE_UNCERTAINTY: f64 = 0.6;
/// A wedge tip is placed from its two sides alone and may move further.
pub const TIP_LIMIT: f64 = 4.0;
/// Vertices this close to a node are not believed: a junction's pixels mix
/// three fills. A wedge tip's sliver is worse still. See the Python.
pub const NODE_TRIM: f64 = 1.6;
pub const TIP_TRIM: f64 = 4.1;
/// ...but never more than this share of the arc's own length from either end.
pub const TRIM_SHARE: f64 = 0.3;
/// An arc shorter than this has no direction worth reading; nodes it joins are
/// one junction, and it collapses onto them.
pub const SHORT_ARC: f64 = 2.1;
/// Two arcs leaving a node are one smooth curve only if one line or one cubic
/// fits SMOOTH_SPAN px of each, node in the middle, within SMOOTH_TOL of the
/// tolerance; pairs turning more than SMOOTH_MAX_TURN are not tried.
pub const SMOOTH_SPAN: f64 = 10.1;
pub const SMOOTH_TOL: f64 = 0.75;
pub const SMOOTH_MAX_TURN: f64 = 90.0;

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
    /// This end is the tip of a wedge closing to a point.
    pub tip0: bool,
    pub tip1: bool,
    /// Vertices within this of each end are not believed (NODE_TRIM, widened by the node's move).
    pub trim0: f64,
    pub trim1: f64,
    /// Per vertex: placed on a pixel handed back to a cut-off wedge (a three-fill mixture).
    pub sliver: Option<Vec<bool>>,
    /// A closed arc's mirror axis (point, unit direction), when it has one.
    pub mirror: Option<(P, P)>,
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

/// A padded pixel, as (row, column).
type Pixel = (usize, usize);
/// One node of a junction: the arc it belongs to, and whether at its start.
type ArcEnd = (usize, bool);
/// A junction's decision, held back until every node has been worked out: the
/// arc ends meeting there, where they all move to, and any pinned tangents.
type Junction = (Vec<ArcEnd>, P, Vec<(usize, P)>, Vec<usize>);

struct Edges {
    /// vertex -> the boundary edges meeting there
    incident: HashMap<u64, Vec<u64>>,
    /// edge -> the two padded pixels it parts
    pixels: HashMap<u64, (Pixel, Pixel)>,
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
    pix: &[Pixel],
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
    p_in: &[Pixel],
    p_out: &[Pixel],
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
    let outward = |from: &[Pixel], towards: &[Pixel], want: i32, near: &[f64]| -> Vec<f64> {
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
            // Of the crossings on offer, the one nearest the label edge wins.
            // See the Python.
            let mut t = 0.5;
            let mut best = f64::INFINITY;
            let mut slope = 0.0;
            for s in 0..3 {
                let (lo, hi) = (level[s], level[s + 1]);
                if lo >= 0.5 && hi < 0.5 {
                    let cand = at[s] + (lo - 0.5) / (lo - hi).max(1e-9);
                    if (cand - 0.5).abs() < best {
                        best = (cand - 0.5).abs();
                        slope = lo - hi;
                        t = cand;
                    }
                }
            }
            // Only a steep ramp may step outside the two pixels either side of
            // the label edge: see the Python.
            let trust = ((slope - 0.15) / 0.35).clamp(0.0, 1.0);
            let over = t.clamp(0.0, 1.0);
            (over + (t - over) * trust).clamp(-REACH, 1.0 + REACH)
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
    handed_back: &std::collections::HashSet<(usize, usize)>,
) -> Vec<(Vec<P>, Vec<P>, Vec<bool>)> {
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
            let crowded: Vec<bool> = (0..ch.edges.len())
                .map(|k| handed_back.contains(&p_in[k]) || handed_back.contains(&p_out[k]))
                .collect();
            let mut pts: Vec<P> = Vec::with_capacity(ch.edges.len());
            let mut normal = Vec::with_capacity(ch.edges.len());
            for k in 0..ch.edges.len() {
                let c_in = [p_in[k].1 as f64 - 0.5, p_in[k].0 as f64 - 0.5];
                let c_out = [p_out[k].1 as f64 - 0.5, p_out[k].0 as f64 - 0.5];
                // `c_in` is always the `a` pixel's centre and `c_out` the `b`
                // pixel's, so this step points from one side of the arc to the
                // other. It is one pixel long and axis aligned already.
                let step = [c_out[0] - c_in[0], c_out[1] - c_in[1]];
                // Where a sliver was rebuilt the two boundaries either side of
                // it share a pixel, and reaching outside that pixel is a claim
                // the other boundary has an equal call on. See the Python.
                let tk = if crowded[k] { t[k].clamp(0.0, 1.0) } else { t[k] };
                pts.push([c_in[0] + tk * step[0], c_in[1] + tk * step[1]]);
                normal.push(step);
            }
            pts = unfold(&pts);
            if !handed_back.is_empty() {
                pts = settle(&pts, &crowded);
            }
            (pts, normal, crowded)
        })
        .collect()
}

/// Largest angle between a region's two arcs at a node that still counts as the
/// region closing to a point rather than turning a corner.
pub const WEDGE_ANGLE: f64 = 75.0;
/// A cut-off region is handed back pixels while its share of the mixture holds
/// above this, allowing this many misses along the way, and only if the run it
/// collects is at least this long — one stray pixel is noise, not a taper.
pub const WEDGE_FLOOR: f64 = 0.35;
pub const WEDGE_PATIENCE: usize = 2;
pub const WEDGE_RUN: usize = 3;
/// How far past a right angle a turn has to go before it is read as the outline
/// doubling back rather than turning a corner: cos of the turn, so 0.5 is 120°.
pub const FOLD: f64 = 0.5;
/// Seen over this many pixels either side, a real corner still turns; a vertex
/// that merely reached past its neighbour does not.
pub const WIDE: f64 = 3.0;
pub const CORNER_WIDE: f64 = 0.5;

/// The region that ends at a node, and the arc that carries on past it.
///
/// A region closes to a point when its two arcs leave the node at an acute
/// angle *and* the ground between them is its own — a big region with a sharp
/// corner has the same angle but the acute sector belongs to its neighbour, and
/// reading the label a couple of pixels along the bisector is what tells them
/// apart. See the Python.
fn wedge(padded: &Labels, target: P, pairs: &[(i32, i32)], away: &[P]) -> Option<(i32, usize)> {
    let mut labs: Vec<i32> = pairs.iter().flat_map(|p| [p.0, p.1]).filter(|v| *v != 0).collect();
    labs.sort_unstable();
    labs.dedup();
    let mut best: Option<(f64, i32, Vec<usize>)> = None;
    for lab in labs {
        let sides: Vec<usize> = (0..pairs.len()).filter(|k| pairs[*k].0 == lab || pairs[*k].1 == lab).collect();
        if sides.len() != 2 {
            continue;
        }
        let dot = away[sides[0]][0] * away[sides[1]][0] + away[sides[0]][1] * away[sides[1]][1];
        let turn = dot.clamp(-1.0, 1.0).acos().to_degrees();
        if best.as_ref().is_none_or(|b| turn < b.0) {
            best = Some((turn, lab, sides));
        }
    }
    let (turn, lab, sides) = best?;
    if turn > WEDGE_ANGLE {
        return None;
    }
    let mut bis = [away[sides[0]][0] + away[sides[1]][0], away[sides[0]][1] + away[sides[1]][1]];
    let len = (bis[0] * bis[0] + bis[1] * bis[1]).sqrt();
    if len < 1e-6 {
        return None;
    }
    bis = [bis[0] / len, bis[1] / len];
    let mut inside = false;
    for step in [1.5f64, 2.5, 3.5] {
        let r = (target[1] + bis[1] * step).floor() as i64 + 1;
        let c = (target[0] + bis[0] * step).floor() as i64 + 1;
        if r >= 0 && c >= 0 && (r as usize) < padded.h && (c as usize) < padded.w
            && *padded.get(r as usize, c as usize) == lab
        {
            inside = true;
            break;
        }
    }
    if !inside {
        return None;
    }
    let through: Vec<usize> = (0..pairs.len()).filter(|k| !sides.contains(k)).collect();
    if through.len() != 1 {
        return None;
    }
    // A shape cut off by the canvas edge is not closing to a point: the arc
    // that would carry on is the canvas border. See the Python.
    if pairs[through[0]].0 == 0 || pairs[through[0]].1 == 0 {
        return None;
    }
    // the boundary has to carry on the other way, or this is a corner, not a tip
    if -(away[through[0]][0] * bis[0] + away[through[0]][1] * bis[1]) < 0.5 {
        return None;
    }
    Some((lab, through[0]))
}

/// How much of each colour is the third fill, in a mixture of all three.
/// See the Python: least squares over the two free weights, folded back onto
/// the simplex so no share is negative and the three sum to one.
fn mix_share(colour: &[[f64; 4]], f_c: &[[f64; 4]], f_a: &[[f64; 4]], f_b: &[[f64; 4]]) -> Vec<f64> {
    (0..colour.len())
        .map(|k| {
            let (mut uu, mut vv, mut uv, mut tu, mut tv) = (0.0, 0.0, 0.0, 0.0, 0.0);
            for ch in 0..4 {
                let u = f_c[k][ch] - f_b[k][ch];
                let v = f_a[k][ch] - f_b[k][ch];
                let t = colour[k][ch] - f_b[k][ch];
                uu += u * u;
                vv += v * v;
                uv += u * v;
                tu += t * u;
                tv += t * v;
            }
            let det = uu * vv - uv * uv;
            if det.abs() <= 1e-9 {
                return 0.0;
            }
            let sc = ((tu * vv - tv * uv) / det).clamp(0.0, 1.0);
            let sa = ((tv * uu - tu * uv) / det).clamp(0.0, 1.0);
            let total = sc + sa;
            let share = if total > 1.0 { sc / total.max(1e-9) } else { sc };
            // Rounded before anyone compares it to a threshold: see the Python.
            (share * 100.0).round() / 100.0
        })
        .collect()
}

/// Give a region cut off at a point the pixels its ink still runs through.
///
/// A region that tapers to an acute point cannot be carried all the way by a
/// label map: below a pixel wide there is no pixel to give it, so the watershed
/// hands those to whichever neighbour is winning and the region stops dead. The
/// trace then shows a blunt cut where the artwork has a long fine taper. The ink
/// is still there — along the stretch where the two neighbours now meet directly
/// the pixels are a mixture of three fills, and the third share says how much of
/// each is still the region that was cut off. See the Python.
pub fn extend_wedges(
    padded: &Labels,
    rgb: &Image,
    alpha: &Grid<f64>,
    fill_at: FillAt,
) -> (Labels, std::collections::HashSet<(usize, usize)>) {
    let chain_list = chains(padded, &boundary_edges(padded));
    if chain_list.is_empty() {
        return (padded.clone(), std::collections::HashSet::new());
    }
    let edges = boundary_edges(padded);
    // A provisional graph, placed at the lattice edges' midpoints: enough to say
    // which region closes to a point where, and which stretch carries on.
    let mid: Vec<Vec<P>> = chain_list
        .iter()
        .map(|ch| {
            ch.edges
                .iter()
                .map(|k| {
                    let (pa, pb) = edges.pixels[k];
                    [
                        (pa.1 as f64 + pb.1 as f64) / 2.0 - 0.5,
                        (pa.0 as f64 + pb.0 as f64) / 2.0 - 0.5,
                    ]
                })
                .collect()
        })
        .collect();

    let mut ends: HashMap<u64, Vec<ArcEnd>> = HashMap::new();
    for (idx, ch) in chain_list.iter().enumerate() {
        if ch.n0.is_none() || mid[idx].len() < 2 {
            continue;
        }
        ends.entry(ch.n0.unwrap()).or_default().push((idx, true));
        ends.entry(ch.n1.unwrap()).or_default().push((idx, false));
    }
    let mut nodes: Vec<u64> = ends.keys().copied().collect();
    nodes.sort_unstable();

    let colour_at = |q: (usize, usize)| -> [f64; 4] {
        let (r, c) = (q.0 as i64 - 1, q.1 as i64 - 1);
        if r < 0 || c < 0 || r >= rgb.h as i64 || c >= alpha.w as i64 {
            return [0.0; 4];
        }
        let px = rgb.at(r as usize, c as usize);
        [px[0], px[1], px[2], *alpha.get(r as usize, c as usize) * 255.0]
    };

    let mut out = padded.clone();
    let mut taken: std::collections::HashSet<(usize, usize)> = std::collections::HashSet::new();

    for node in nodes {
        let incident = ends[&node].clone();
        if incident.len() != 3 {
            continue;
        }
        let away: Vec<P> = incident
            .iter()
            .map(|(i, at_start)| {
                let pts = &mid[*i];
                let far = if *at_start { pts[3.min(pts.len() - 1)] } else { pts[pts.len() - 1 - 3.min(pts.len() - 1)] };
                let anchor = if *at_start { pts[0] } else { pts[pts.len() - 1] };
                let step = [far[0] - anchor[0], far[1] - anchor[1]];
                let len = (step[0] * step[0] + step[1] * step[1]).sqrt();
                if len > 1e-9 { [step[0] / len, step[1] / len] } else { [0.0, 0.0] }
            })
            .collect();
        let (i0, s0) = incident[0];
        let here = if s0 { mid[i0][0] } else { mid[i0][mid[i0].len() - 1] };
        let pairs: Vec<(i32, i32)> = incident.iter().map(|(i, _)| chain_list[*i].pair).collect();
        let Some((lab, through)) = wedge(padded, here, &pairs, &away) else { continue };
        let (idx, at_start) = incident[through];
        let (a, b) = chain_list[idx].pair;
        if a == 0 || b == 0 {
            continue;
        }
        let mut order: Vec<u64> = chain_list[idx].edges.clone();
        if !at_start {
            order.reverse();
        }

        let mut run: Vec<(usize, usize)> = Vec::new();
        let mut misses = 0usize;
        let mut side: Option<i32> = None;
        for key in order.iter() {
            let (pa, pb) = edges.pixels[key];
            let mut options: Vec<(usize, usize)> = Vec::new();
            for q in [pa, pb] {
                let here_lab = *out.get(q.0, q.1);
                if (here_lab != a && here_lab != b) || taken.contains(&q) {
                    continue;
                }
                if let Some(prev) = run.last() {
                    let dr = (q.0 as i64 - prev.0 as i64).abs();
                    let dc = (q.1 as i64 - prev.1 as i64).abs();
                    if dr.max(dc) != 1 {
                        continue;
                    }
                } else {
                    let mut touches = false;
                    for dr in -1i64..=1 {
                        for dc in -1i64..=1 {
                            let (r, c) = (q.0 as i64 + dr, q.1 as i64 + dc);
                            if r >= 0 && c >= 0 && (r as usize) < out.h && (c as usize) < out.w
                                && *out.get(r as usize, c as usize) == lab
                            {
                                touches = true;
                            }
                        }
                    }
                    if !touches {
                        continue;
                    }
                }
                options.push(q);
            }
            if let Some(s) = side {
                let on_side: Vec<(usize, usize)> = options.iter().copied().filter(|q| *out.get(q.0, q.1) == s).collect();
                if !on_side.is_empty() {
                    options = on_side;
                }
            }
            if options.is_empty() {
                misses += 1;
                if misses > WEDGE_PATIENCE {
                    break;
                }
                continue;
            }
            let qx: Vec<f64> = options.iter().map(|q| q.1 as f64 - 0.5).collect();
            let qy: Vec<f64> = options.iter().map(|q| q.0 as f64 - 0.5).collect();
            let cols: Vec<[f64; 4]> = options.iter().map(|q| colour_at(*q)).collect();
            let share = mix_share(&cols, &fill_at(lab, &qx, &qy), &fill_at(a, &qx, &qy), &fill_at(b, &qx, &qy));
            let mut pick = 0usize;
            for k in 1..share.len() {
                if share[k] > share[pick] {
                    pick = k;
                }
            }
            if share[pick] < WEDGE_FLOOR {
                misses += 1;
                if misses > WEDGE_PATIENCE {
                    break;
                }
                continue;
            }
            misses = 0;
            let chosen = options[pick];
            if side.is_none() {
                side = Some(*out.get(chosen.0, chosen.1));
            }
            run.push(chosen);
            taken.insert(chosen);
        }
        // One stray pixel is noise; a region that really was cut off leaves a run.
        if run.len() >= WEDGE_RUN {
            for q in bridged(&run, &out, lab, a, b, &colour_at, fill_at) {
                out.set(q.0, q.1, lab);
            }
        } else {
            for q in &run {
                taken.remove(q);
            }
        }
    }
    (out, taken)
}

/// The run, with a pixel put in wherever it steps diagonally.
///
/// Four-connectivity is not a nicety: `directed_rings` breaks a diagonal touch
/// the four-connected way, so a chain that only meets at the corners is read
/// back as a string of one-pixel islands. See the Python.
fn bridged(
    run: &[(usize, usize)],
    out: &Labels,
    lab: i32,
    a: i32,
    b: i32,
    colour_at: &dyn Fn((usize, usize)) -> [f64; 4],
    fill_at: FillAt,
) -> Vec<(usize, usize)> {
    let mut chain: Vec<(usize, usize)> = Vec::new();
    'seek: for dr in -1i64..=1 {
        for dc in -1i64..=1 {
            if dr == 0 && dc == 0 {
                continue;
            }
            let (r, c) = (run[0].0 as i64 + dr, run[0].1 as i64 + dc);
            if r >= 0 && c >= 0 && (r as usize) < out.h && (c as usize) < out.w
                && *out.get(r as usize, c as usize) == lab
            {
                chain.push((r as usize, c as usize));
                break 'seek;
            }
        }
    }
    chain.extend_from_slice(run);

    let mut result: Vec<(usize, usize)> = Vec::new();
    for k in 0..chain.len() {
        let q = chain[k];
        if k > 0 {
            let p = chain[k - 1];
            let dr = (q.0 as i64 - p.0 as i64).abs();
            let dc = (q.1 as i64 - p.1 as i64).abs();
            if dr.max(dc) == 1 && dr + dc == 2 {
                let options: Vec<(usize, usize)> = [(p.0, q.1), (q.0, p.1)]
                    .into_iter()
                    .filter(|o| {
                        let l = *out.get(o.0, o.1);
                        l == a || l == b
                    })
                    .collect();
                if !options.is_empty() {
                    let qx: Vec<f64> = options.iter().map(|o| o.1 as f64 - 0.5).collect();
                    let qy: Vec<f64> = options.iter().map(|o| o.0 as f64 - 0.5).collect();
                    let cols: Vec<[f64; 4]> = options.iter().map(|o| colour_at(*o)).collect();
                    let share = mix_share(&cols, &fill_at(lab, &qx, &qy), &fill_at(a, &qx, &qy), &fill_at(b, &qx, &qy));
                    let mut pick = 0usize;
                    for j in 1..share.len() {
                        if share[j] > share[pick] {
                            pick = j;
                        }
                    }
                    result.push(options[pick]);
                }
            }
        }
        if *out.get(q.0, q.1) != lab {
            result.push(q);
        }
    }
    result
}

/// Stop the placed outline doubling back on itself.
///
/// A vertex sits where coverage passes a half along the segment joining two
/// pixel centres, and that crossing may reach a little outside those two pixels
/// — which is what lets the outline sit where a hard corner really is. Where two
/// boundaries run through the same pixel both reach, and they can reach past
/// each other: consecutive vertices come out in the wrong order along the arc,
/// and the fit reads that as a curve that turns back.
///
/// A corner is sharp at every scale; a vertex that reached past its neighbour is
/// sharp only against them. See the Python.
fn unfold(pts: &[P]) -> Vec<P> {
    let n = pts.len();
    if n < 5 {
        return pts.to_vec();
    }
    let mut cum = vec![0.0f64; n];
    for k in 1..n {
        cum[k] = cum[k - 1]
            + ((pts[k][0] - pts[k - 1][0]).powi(2) + (pts[k][1] - pts[k - 1][1]).powi(2)).sqrt();
    }
    let total = cum[n - 1];
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

    let mut folds: Vec<usize> = Vec::new();
    for k in 1..n - 1 {
        let ahead = [pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1]];
        let behind = [pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]];
        let scale = (ahead[0] * ahead[0] + ahead[1] * ahead[1]).sqrt()
            * (behind[0] * behind[0] + behind[1] * behind[1]).sqrt();
        if scale <= 1e-12 {
            continue;
        }
        if (ahead[0] * behind[0] + ahead[1] * behind[1]) / scale >= -FOLD {
            continue;
        }
        let back = at(cum[k] - WIDE);
        let fwd = at(cum[k] + WIDE);
        let u = [pts[k][0] - back[0], pts[k][1] - back[1]];
        let v = [fwd[0] - pts[k][0], fwd[1] - pts[k][1]];
        let span = (u[0] * u[0] + u[1] * u[1]).sqrt() * (v[0] * v[0] + v[1] * v[1]).sqrt();
        let wide = if span <= 1e-12 { 0.0 } else { (u[0] * v[0] + u[1] * v[1]) / span };
        if wide > CORNER_WIDE {
            folds.push(k);
        }
    }
    let mut out = pts.to_vec();
    for k in folds {
        out[k] = [(out[k - 1][0] + out[k + 1][0]) / 2.0, (out[k - 1][1] + out[k + 1][1]) / 2.0];
    }
    out
}

/// Average a vertex with its neighbours where the arc runs along a sliver that
/// was handed back: those are the noisiest vertices the stage produces, because
/// the pixel under them is a mixture of three fills and the chain the sliver was
/// rebuilt from is a staircase. See the Python.
fn settle(pts: &[P], along_sliver: &[bool]) -> Vec<P> {
    if pts.len() < 3 || !along_sliver.iter().any(|v| *v) {
        return pts.to_vec();
    }
    (0..pts.len())
        .map(|k| {
            if k == 0 || k + 1 == pts.len() || !along_sliver[k] {
                pts[k]
            } else {
                [
                    (pts[k - 1][0] + 2.0 * pts[k][0] + pts[k + 1][0]) / 4.0,
                    (pts[k - 1][1] + 2.0 * pts[k][1] + pts[k + 1][1]) / 4.0,
                ]
            }
        })
        .collect()
}

/// Total-least-squares line through an arc's run-up to one end, skipping the
/// half-pixel marching-squares chamfer at the end itself.
fn approach(pts: &[P], from_start: bool, reach: f64, trim: f64, grow_to: f64, exclude: Option<&[bool]>) -> Option<(P, P, f64)> {
    // Total-least-squares line through an arc's run-up to one end, over a
    // window that grows while the run stays straight. Same arithmetic, same
    // order, as the Python `_approach`.
    let ordered: Vec<P> = if from_start { pts.to_vec() } else { pts.iter().rev().copied().collect() };
    let anchor = ordered[0];
    let d: Vec<f64> = ordered
        .iter()
        .map(|p| ((p[0] - anchor[0]).powi(2) + (p[1] - anchor[1]).powi(2)).sqrt())
        .collect();
    // Vertices placed on a wedge's handed-back sliver are biased; a line through
    // them leans by degrees. See the Python.
    let ok: Vec<bool> = match exclude {
        None => vec![true; ordered.len()],
        Some(ex) => {
            if from_start { ex.iter().map(|x| !*x).collect() } else { ex.iter().rev().map(|x| !*x).collect() }
        }
    };
    let mut best: Option<(P, P, f64)> = None;
    let mut far = reach;
    while far <= grow_to + 1e-9 {
        let take: Vec<P> = ordered
            .iter()
            .zip(d.iter())
            .zip(ok.iter())
            .filter(|((_, dd), o)| **dd >= trim && **dd <= far && **o)
            .map(|((p, _), _)| *p)
            .collect();
        if take.len() >= 2 {
            let (centre, dir) = line_through(&take);
            let mut ss = 0.0f64;
            for p in &take {
                let off = (p[0] - centre[0]) * (-dir[1]) + (p[1] - centre[1]) * dir[0];
                ss += off * off;
            }
            let rms = (ss / take.len() as f64).sqrt();
            if best.is_some() && rms > APPROACH_RMS {
                break;
            }
            // uncertainty grows for short, sparse windows: see the Python
            let dsel: Vec<f64> = ordered.iter().zip(d.iter()).zip(ok.iter()).filter(|((_, dd), o)| **dd >= trim && **dd <= far && **o).map(|((_, dd), _)| *dd).collect();
            let span = (dsel.iter().cloned().fold(f64::NEG_INFINITY, f64::max) - dsel.iter().cloned().fold(f64::INFINITY, f64::min)).max(0.5);
            let lean = PLACEMENT_SIGMA * (12.0 / take.len() as f64).sqrt() * (reach / span);
            best = Some((centre, dir, (rms * rms + lean * lean).sqrt()));
        }
        far += 2.0;
    }
    if best.is_none() {
        let mut wide: Vec<P> = ordered
            .iter()
            .zip(d.iter())
            .zip(ok.iter())
            .filter(|((_, dd), o)| **dd > 0.0 && **dd <= 2.0 * reach && **o)
            .map(|((p, _), _)| *p)
            .collect();
        if wide.len() < 2 {
            wide = ordered.iter().zip(d.iter()).filter(|(_, dd)| **dd > 0.0 && **dd <= 2.0 * reach).map(|(p, _)| *p).collect();
        }
        if wide.len() < 2 {
            return None;
        }
        let (centre, dir) = line_through(&wide);
        best = Some((centre, dir, FALLBACK_RMS));
    }
    best
}

/// Where the incident approach lines cross, when they pin it down; else `mean`.
/// See the Python `_node_estimate` for the eigenvalue argument.
fn node_estimate(lines: &[Option<(P, P, f64)>], mean: P, limit: f64) -> P {
    let usable: Vec<&(P, P, f64)> = lines.iter().flatten().collect();
    if usable.len() < 2 {
        return mean;
    }
    let (mut m00, mut m01, mut m11) = (0.0f64, 0.0f64, 0.0f64);
    let (mut w00, mut w01, mut w11) = (0.0f64, 0.0f64, 0.0f64);
    let (mut r0, mut r1) = (0.0f64, 0.0f64);
    for (point, dir, rms) in usable {
        let n = [1.0 - dir[0] * dir[0], -dir[0] * dir[1], 1.0 - dir[1] * dir[1]];
        m00 += n[0];
        m01 += n[1];
        m11 += n[2];
        // weighted by the inverse residual variance: see the Python
        let w = 1.0 / (rms * rms + PLACEMENT_SIGMA * PLACEMENT_SIGMA);
        w00 += w * n[0];
        w01 += w * n[1];
        w11 += w * n[2];
        r0 += w * (n[0] * point[0] + n[1] * point[1]);
        r1 += w * (n[1] * point[0] + n[2] * point[1]);
    }
    let half = (m00 + m11) / 2.0;
    let spread = (((m00 - m11) / 2.0).powi(2) + m01 * m01).sqrt();
    let lam_min = half - spread;
    if lam_min <= 1e-9 {
        return mean;
    }
    if PLACEMENT_SIGMA / lam_min.sqrt() > NODE_UNCERTAINTY {
        return mean;
    }
    let det = w00 * w11 - w01 * w01;
    if det.abs() <= 1e-300 {
        return mean;
    }
    let guess = [(w11 * r0 - w01 * r1) / det, (w00 * r1 - w01 * r0) / det];
    let mv = [guess[0] - mean[0], guess[1] - mean[1]];
    let away = (mv[0] * mv[0] + mv[1] * mv[1]).sqrt();
    if away > limit {
        [mean[0] + mv[0] * (limit / away), mean[1] + mv[1] * (limit / away)]
    } else {
        [mean[0] + mv[0], mean[1] + mv[1]]
    }
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
fn arc_length(pts: &[P]) -> f64 {
    pts.windows(2).map(|w| ((w[1][0] - w[0][0]).powi(2) + (w[1][1] - w[0][1]).powi(2)).sqrt()).sum()
}

/// Are two arcs leaving one node a single smooth curve? See the Python
/// `_smooth_through`: one line or one cubic through SMOOTH_SPAN px of each,
/// the node in the middle, inside SMOOTH_TOL of the tolerance.
fn smooth_through(pa: &[P], pb: &[P], node: P, tol: f64, exclude_a: Option<&[bool]>, exclude_b: Option<&[bool]>) -> bool {
    fn head(p: &[P], exclude: Option<&[bool]>) -> Vec<P> {
        let mut out = Vec::new();
        let mut cum = 0.0;
        for k in 0..p.len() {
            if k > 0 {
                cum += ((p[k][0] - p[k - 1][0]).powi(2) + (p[k][1] - p[k - 1][1]).powi(2)).sqrt();
            }
            let excluded = exclude.is_some_and(|e| e[k]);
            if cum >= APPROACH_TRIM && cum <= SMOOTH_SPAN && !excluded {
                out.push(p[k]);
            }
        }
        out
    }
    let a = head(pa, exclude_a);
    let b = head(pb, exclude_b);
    if a.len() < 3 || b.len() < 3 {
        return false;
    }
    let mut joined: Vec<P> = a.iter().rev().copied().collect();
    joined.push(node);
    joined.extend(b.iter().copied());
    fit_open(&joined, SMOOTH_TOL * tol, None, None).len() == 1
}

/// A node on the canvas edge stays on it. See the Python `_on_border`.
fn on_border(target: P, arcs: &[Arc], incident: &[ArcEnd]) -> P {
    const REACH: f64 = 6.1;
    let mut out = target;
    for (i, at_start) in incident {
        let arc = &arcs[*i];
        if (arc.pair.0 != 0 && arc.pair.1 != 0) || arc.pts.len() < 3 {
            continue;
        }
        let q: &[P] = if *at_start { &arc.pts[1..] } else { &arc.pts[..arc.pts.len() - 1] };
        let near: Vec<P> = q.iter().copied().filter(|p| ((p[0] - target[0]).powi(2) + (p[1] - target[1]).powi(2)).sqrt() <= REACH).collect();
        if near.len() < 2 {
            continue;
        }
        for axis in 0..2 {
            let lo = near.iter().map(|p| p[axis]).fold(f64::INFINITY, f64::min);
            let hi = near.iter().map(|p| p[axis]).fold(f64::NEG_INFINITY, f64::max);
            if hi - lo < 1e-9 {
                out[axis] = near[0][axis];
            }
        }
    }
    out
}

fn find_root(parent: &mut HashMap<u64, u64>, mut node: u64) -> u64 {
    while parent[&node] != node {
        let p = parent[&node];
        let gp = parent[&p];
        parent.insert(node, gp);
        node = gp;
    }
    node
}

fn junctions(arcs: &mut [Arc], padded: &Labels, corner_threshold: f64, tol: f64) {
    const REACH_PX: f64 = 4.1;
    const TRIM: f64 = APPROACH_TRIM;
    const LIMIT: f64 = 2.0;

    let mut ends: HashMap<u64, Vec<ArcEnd>> = HashMap::new();
    let mut short = vec![false; arcs.len()];
    for (idx, arc) in arcs.iter().enumerate() {
        if arc.closed() || arc.pts.len() < 2 {
            continue;
        }
        ends.entry(arc.n0.unwrap()).or_default().push((idx, true));
        ends.entry(arc.n1.unwrap()).or_default().push((idx, false));
        if arc_length(&arc.pts) < SHORT_ARC {
            short[idx] = true;
        }
    }

    // Nodes joined by a short arc are one junction. See the Python.
    let mut parent: HashMap<u64, u64> = ends.keys().map(|n| (*n, *n)).collect();
    for idx in 0..arcs.len() {
        if !short[idx] {
            continue;
        }
        let a = find_root(&mut parent, arcs[idx].n0.unwrap());
        let b = find_root(&mut parent, arcs[idx].n1.unwrap());
        if a != b {
            parent.insert(a.max(b), a.min(b));
        }
    }
    let mut nodes: Vec<u64> = ends.keys().copied().collect();
    nodes.sort_unstable();
    let mut order: Vec<u64> = Vec::new();
    let mut groups: HashMap<u64, Vec<ArcEnd>> = HashMap::new();
    for node in nodes {
        let r = find_root(&mut parent, node);
        if !groups.contains_key(&r) {
            order.push(r);
        }
        groups.entry(r).or_default().extend(ends[&node].iter().copied());
    }

    // A group stands only if one point serves every node in it. See the Python.
    let mut resolved: Vec<Vec<ArcEnd>> = Vec::new();
    for r in order {
        let incident = groups[&r].clone();
        let mut members: Vec<u64> = incident.iter().map(|(i, at_start)| if *at_start { arcs[*i].n0.unwrap() } else { arcs[*i].n1.unwrap() }).collect();
        members.sort_unstable();
        members.dedup();
        if members.len() > 1 {
            let lines: Vec<Option<(P, P, f64)>> = incident
                .iter()
                .map(|(i, at_start)| if short[*i] { None } else { approach(&arcs[*i].pts, *at_start, REACH_PX, TRIM, APPROACH_MAX, arcs[*i].sliver.as_deref()) })
                .collect();
            let mut mean = [0.0, 0.0];
            for (i, at_start) in &incident {
                let p = if *at_start { arcs[*i].pts[0] } else { arcs[*i].pts[arcs[*i].pts.len() - 1] };
                mean[0] += p[0];
                mean[1] += p[1];
            }
            mean[0] /= incident.len() as f64;
            mean[1] /= incident.len() as f64;
            let target = node_estimate(&lines, mean, LIMIT);
            let far = incident
                .iter()
                .map(|(i, at_start)| {
                    let p = if *at_start { arcs[*i].pts[0] } else { arcs[*i].pts[arcs[*i].pts.len() - 1] };
                    ((p[0] - target[0]).powi(2) + (p[1] - target[1]).powi(2)).sqrt()
                })
                .fold(0.0f64, f64::max);
            // a group stands only where the long arcs actually crossed: see the Python
            let crossed = target[0] != mean[0] || target[1] != mean[1];
            if !crossed || far > SHORT_ARC {
                for node in members {
                    resolved.push(ends[&node].clone());
                }
                continue;
            }
        }
        resolved.push(incident);
    }

    // Every junction is worked out from the arcs as they were placed, and only
    // then are any of them moved: see the Python.
    let mut moves: Vec<Junction> = Vec::new();
    for incident in resolved {
        let mut lines: Vec<Option<(P, P, f64)>> = incident
            .iter()
            .map(|(i, at_start)| {
                if short[*i] {
                    None
                } else {
                    approach(&arcs[*i].pts, *at_start, REACH_PX, TRIM, APPROACH_MAX, arcs[*i].sliver.as_deref())
                }
            })
            .collect();

        let mut mean = [0.0, 0.0];
        for (i, at_start) in &incident {
            let p = if *at_start { arcs[*i].pts[0] } else { arcs[*i].pts[arcs[*i].pts.len() - 1] };
            mean[0] += p[0];
            mean[1] += p[1];
        }
        mean[0] /= incident.len() as f64;
        mean[1] /= incident.len() as f64;

        let mut target = on_border(node_estimate(&lines, mean, LIMIT), arcs, &incident);
        // Which two arcs, if any, are one curve passing through? Only the long
        // arcs have a direction; a short arc's ends just take the node.
        let long: Vec<usize> = (0..incident.len()).filter(|s| !short[incident[*s].0]).collect();
        let mut away: Vec<P> = long
            .iter()
            .map(|slot| {
                let (i, at_start) = incident[*slot];
                let pts = &arcs[i].pts;
                let far = if at_start { pts[3.min(pts.len() - 1)] } else { pts[pts.len() - 1 - 3.min(pts.len() - 1)] };
                let outward = [far[0] - target[0], far[1] - target[1]];
                match lines[*slot] {
                    Some((_, dir, _)) => {
                        let dot = dir[0] * outward[0] + dir[1] * outward[1];
                        if dot > 0.0 {
                            normalize(dir)
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

        let mut pinned: Vec<(usize, P)> = Vec::new();
        let mut tips: Vec<usize> = Vec::new();
        if long.len() == 3 {
            let pairs: Vec<(i32, i32)> = long.iter().map(|s| arcs[incident[*s].0].pair).collect();
            if let Some((_lab, through)) = wedge(padded, target, &pairs, &away) {
                // A region closing to a point does not put a corner in anything.
                // The boundary that carries on leaves along its own line, and
                // each side of the wedge along *its* own line. See the Python.
                let axis = away[through];
                for k in 0..3 {
                    pinned.push((long[k], if k == through { axis } else { away[k] }));
                }
                tips = (0..3).filter(|k| *k != through).map(|k| long[k]).collect();
                // The sides are read again from beyond the tip's trim.
                for k in 0..3 {
                    if k == through {
                        continue;
                    }
                    let slot = long[k];
                    let (i, at_start) = incident[slot];
                    if let Some(again) = approach(&arcs[i].pts, at_start, TIP_TRIM + REACH_PX, TIP_TRIM, 2.0 * APPROACH_MAX, arcs[i].sliver.as_deref()) {
                        lines[slot] = Some(again);
                        let flip = again.1[0] * away[k][0] + again.1[1] * away[k][1] < 0.0;
                        away[k] = normalize(if flip { [-again.1[0], -again.1[1]] } else { again.1 });
                        if let Some(entry) = pinned.iter_mut().find(|(s, _)| *s == slot) {
                            entry.1 = away[k];
                        }
                    }
                }
                let side_lines: Vec<Option<(P, P, f64)>> = tips.iter().map(|s| lines[*s]).collect();
                target = on_border(node_estimate(&side_lines, mean, TIP_LIMIT), arcs, &incident);
            }
        }
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
        let _ = corner_threshold;
        if pinned.is_empty() {
            if let Some((turn, x, y)) = best {
                if turn <= SMOOTH_MAX_TURN {
                    let from_node = |slot: usize| -> (Vec<P>, Option<Vec<bool>>) {
                        let (i, at_start) = incident[slot];
                        let arc = &arcs[i];
                        if at_start {
                            (arc.pts.clone(), arc.sliver.clone())
                        } else {
                            (
                                arc.pts.iter().rev().copied().collect(),
                                arc.sliver.as_ref().map(|sl| sl.iter().rev().copied().collect()),
                            )
                        }
                    };
                    let (pa, sa) = from_node(long[x]);
                    let (pb, sb) = from_node(long[y]);
                    if smooth_through(&pa, &pb, target, tol, sa.as_deref(), sb.as_deref()) {
                        let shared = normalize([away[x][0] - away[y][0], away[x][1] - away[y][1]]);
                        if shared[0] != 0.0 || shared[1] != 0.0 {
                            pinned.push((long[x], shared));
                            pinned.push((long[y], [-shared[0], -shared[1]]));
                        }
                    }
                }
            }
        }
        moves.push((incident, target, pinned, tips));
    }

    for (incident, target, pinned, tips) in moves {
        for (slot, (i, at_start)) in incident.iter().enumerate() {
            let last = arcs[*i].pts.len() - 1;
            let idx = if *at_start { 0 } else { last };
            let old = arcs[*i].pts[idx];
            // Vertices between the arc's old end and the node it was given
            // would double back on the curve, so the trim reaches past the move.
            let moved = ((target[0] - old[0]).powi(2) + (target[1] - old[1]).powi(2)).sqrt();
            let is_tip = tips.contains(&slot);
            // A short arc keeps its vertices. See the Python.
            let radius = (if is_tip { TIP_TRIM } else { NODE_TRIM }).max(moved + 1.0).min(TRIM_SHARE * arc_length(&arcs[*i].pts));
            arcs[*i].pts[idx] = target;
            if *at_start {
                arcs[*i].trim0 = radius;
            } else {
                arcs[*i].trim1 = radius;
            }
            if is_tip {
                if *at_start {
                    arcs[*i].tip0 = true;
                } else {
                    arcs[*i].tip1 = true;
                }
            }
            if let Some((_, tan)) = pinned.iter().find(|(s, _)| *s == slot) {
                if *at_start {
                    arcs[*i].t0 = Some(*tan);
                } else {
                    arcs[*i].t1 = Some(*tan);
                }
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
/// Where an arc's interior corner really is. See the Python `_sharp_corner`.
fn sharp_corner(pts: &[P], k: usize) -> P {
    const REACH: f64 = 3.1;
    const TRIM: f64 = 0.8;
    fn near(q: &[P]) -> Vec<P> {
        let d: Vec<f64> = q.iter().map(|p| ((p[0] - q[0][0]).powi(2) + (p[1] - q[0][1]).powi(2)).sqrt()).collect();
        let mut sel: Vec<P> = q.iter().zip(&d).filter(|(_, dd)| **dd >= TRIM && **dd <= REACH).map(|(p, _)| *p).collect();
        if sel.len() < 2 {
            sel = q.iter().zip(&d).filter(|(_, dd)| **dd > 0.0 && **dd <= 2.0 * REACH).map(|(p, _)| *p).collect();
        }
        sel
    }
    let before: Vec<P> = pts[..=k].iter().rev().copied().collect();
    let a = near(&before);
    let b = near(&pts[k..]);
    if a.len() >= 2 && b.len() >= 2 {
        let (p, d) = line_through(&a);
        let (q, e) = line_through(&b);
        if let Some(x) = intersect(p, d, q, e) {
            if ((x[0] - pts[k][0]).powi(2) + (x[1] - pts[k][1]).powi(2)).sqrt() <= 1.5 {
                return x;
            }
        }
    }
    pts[k]
}

fn sharpen_piece(pts: &[P], lo: usize, hi: usize, corners: &[usize], trims: (f64, f64), sliver: Option<&[bool]>) -> Vec<P> {
    // One run between two breaks, with the vertices next to a break dropped
    // where they cannot be trusted: the chamfer next to an interior corner, the
    // approach window next to a node. See the Python.
    const TRIM: f64 = 0.8;
    let piece = &pts[lo..=hi];
    if piece.len() < 4 {
        return piece.to_vec();
    }
    let start_trim = if lo == 0 { trims.0 } else if corners.contains(&lo) { TRIM } else { 0.0 };
    let end_trim = if hi == pts.len() - 1 { trims.1 } else if corners.contains(&hi) { TRIM } else { 0.0 };
    let last = piece[piece.len() - 1];
    let mut out = vec![piece[0]];
    for (j, p) in piece[1..piece.len() - 1].iter().enumerate() {
        let near_lo = ((p[0] - piece[0][0]).powi(2) + (p[1] - piece[0][1]).powi(2)).sqrt() < start_trim;
        let near_hi = ((p[0] - last[0]).powi(2) + (p[1] - last[1]).powi(2)).sqrt() < end_trim;
        if (start_trim > 0.0 && near_lo) || (end_trim > 0.0 && near_hi) {
            continue;
        }
        if let Some(sl) = sliver {
            if sl[lo + 1 + j] {
                continue;
            }
        }
        out.push(*p);
    }
    out.push(last);
    out
}

#[allow(clippy::too_many_arguments)]
pub fn fit_arc(pts: &[P], closed: bool, t0: Option<P>, t1: Option<P>, trims: (f64, f64), sliver: Option<&[bool]>, mirror: Option<(P, P)>, params: &CurveParams) -> Vec<Segment> {
    if closed {
        if let Some(axis) = mirror {
            if pts.len() >= 3 {
                if let Some(m) = fit_mirrored(pts, axis, params) {
                    return m;
                }
            }
        }
        // A region wholly inside one neighbour: no node anywhere on it, so this
        // is an ordinary closed contour and the closed fit is the right one. It
        // keeps the curve G1 across the seam and looks for corners around the
        // wrap, neither of which an open fit can do.
        return if pts.len() < 3 { Vec::new() } else { fit_contour_segments(pts, params) };
    }
    let n = pts.len();
    if n < 2 {
        return Vec::new();
    }
    let span = ((pts[n - 1][0] - pts[0][0]).powi(2) + (pts[n - 1][1] - pts[0][1]).powi(2)).sqrt();
    if span < 1e-6 && arc_length(pts) < 2.0 * SHORT_ARC {
        // Both ends were placed on the same node: the one-pixel arc between two
        // nodes of a corner pixel, collapsed. The ring runs straight through.
        return Vec::new();
    }
    let corners = open_corners(pts, params.corner_threshold);
    let sharp: Vec<P> = corners.iter().map(|k| sharp_corner(pts, *k)).collect();
    let mut bounds = vec![0usize, n - 1];
    bounds.extend_from_slice(&corners);
    bounds.sort_unstable();
    bounds.dedup();

    let mut pieces: Vec<Vec<P>> = Vec::new();
    let mut spans: Vec<(usize, usize)> = Vec::new();
    for k in 0..bounds.len() - 1 {
        let (lo, hi) = (bounds[k], bounds[k + 1]);
        if hi <= lo {
            continue;
        }
        let mut piece = sharpen_piece(pts, lo, hi, &corners, trims, sliver);
        if piece.len() < 2 {
            continue;
        }
        if let Some(c) = corners.iter().position(|x| *x == lo) {
            piece[0] = sharp[c];
        }
        if let Some(c) = corners.iter().position(|x| *x == hi) {
            let last = piece.len() - 1;
            piece[last] = sharp[c];
        }
        pieces.push(piece);
        spans.push((lo, hi));
    }
    // Interior corners move to where the neighbouring line runs cross. See the Python.
    corners_from_runs(&mut pieces, false);
    let mut segments: Vec<Segment> = Vec::new();
    for (piece, (lo, hi)) in pieces.iter().zip(spans.iter()) {
        let ts = if *lo == 0 { t0 } else { None };
        let te = if *hi == n - 1 { t1 } else { None };
        // Lines first: see the Python `_fit_arc`.
        segments.extend(fit_stretch(piece, params.tol, ts, te));
    }
    snap_axis(segments, params.snap_axis_deg)
}

/// Fit a bled copy: its interior as an ordinary arc, joined to the shared
/// nodes by two explicit one-pixel jogs. See the Python `_fit_under`.
fn fit_under(moved: &[P], arc: &Arc, params: &CurveParams) -> Vec<Segment> {
    let n = moved.len();
    if arc.closed() || n < 4 {
        return fit_arc(moved, arc.closed(), arc.t0, arc.t1, (arc.trim0, arc.trim1), arc.sliver.as_deref(), None, params);
    }
    let inner_sliver: Option<Vec<bool>> = arc.sliver.as_ref().map(|sl| sl[1..n - 1].to_vec());
    let mut out = vec![Segment::Line { p0: moved[0], p1: moved[1] }];
    out.extend(fit_arc(&moved[1..n - 1], false, None, None, (0.0, 0.0), inner_sliver.as_deref(), None, params));
    out.push(Segment::Line { p0: moved[n - 2], p1: moved[n - 1] });
    out
}

/// Make a nearly horizontal or vertical line exactly so, as
/// `curves::snap_axis_lines` does for a whole contour — but never moving the
/// arc's own ends, which are nodes the arcs on the other side were fitted to.
/// Symmetrise every single-ring region's placed vertices in place. See the Python `_symmetrize`.
fn symmetrize_boundary(bnd: &mut Boundary) -> usize {
    let mut labels: Vec<i32> = bnd.padded.data.iter().copied().filter(|v| *v != 0).collect();
    labels.sort_unstable();
    labels.dedup();
    let h = bnd.padded.h as f64 - 2.0;
    let w = bnd.padded.w as f64 - 2.0;
    let mut changed = 0;
    for lab in labels {
        let member: std::collections::HashSet<i32> = [lab].into_iter().collect();
        let rings = bnd.rings(&member);
        if rings.len() != 1 {
            continue;
        }
        let ring = &rings[0];
        let poly = bnd.polyline(ring);
        if poly.iter().any(|p| p[0] <= 0.0 || p[1] <= 0.0 || p[0] >= w || p[1] >= h) {
            continue;
        }
        let (sym, axes) = crate::symmetry::ring_symmetries(&poly);
        let Some(sym) = sym else { continue };
        let mut pos = 0;
        for (idx, rev) in ring {
            let k = bnd.arcs[*idx].pts.len();
            let piece = &sym[pos..pos + k];
            bnd.arcs[*idx].pts = if *rev { piece.iter().rev().copied().collect() } else { piece.to_vec() };
            pos += k;
        }
        if ring.len() == 1 && bnd.arcs[ring[0].0].closed() && !axes.is_empty() {
            bnd.arcs[ring[0].0].mirror = Some(axes[0]);
        }
        changed += 1;
    }
    changed
}

pub const MIRROR_CORNER_REACH: f64 = 3.1;

/// See the Python `_axis_corner_from_run`.
fn axis_corner_from_run(piece: &mut Vec<P>, at_start: bool, c: P, d: P) {
    let runs = line_runs(piece);
    let Some(run) = (if at_start { runs.first() } else { runs.last() }) else { return };
    let n = piece.len();
    let (reach, end): (f64, P) = if at_start {
        (piece[..=run.i].windows(2).map(|w| dist(w[0], w[1])).sum(), piece[0])
    } else {
        (piece[run.j..].windows(2).map(|w| dist(w[0], w[1])).sum(), piece[n - 1])
    };
    if reach > CORNER_REACH {
        return;
    }
    let Some(hit) = intersect(run.c, run.d, c, d) else { return };
    if dist(hit, end) > 2.0 {
        return;
    }
    if at_start {
        piece[0] = hit;
    } else {
        piece[n - 1] = hit;
    }
}

fn reflect_segment(seg: &Segment, c: P, d: P) -> Segment {
    use crate::symmetry::reflect;
    match seg {
        Segment::Line { p0, p1 } => Segment::Line { p0: reflect(*p0, c, d), p1: reflect(*p1, c, d) },
        Segment::Arc { p0, p1, r, large, sweep } => Segment::Arc { p0: reflect(*p0, c, d), p1: reflect(*p1, c, d), r: *r, large: *large, sweep: !*sweep },
        Segment::Cubic { p0, c1, c2, p1 } => Segment::Cubic { p0: reflect(*p0, c, d), c1: reflect(*c1, c, d), c2: reflect(*c2, c, d), p1: reflect(*p1, c, d) },
    }
}

/// A closed, mirror-symmetric ring fitted on one half and reflected. See the Python `_fit_mirrored`.
fn fit_mirrored(pts: &[P], axis: (P, P), params: &CurveParams) -> Option<Vec<Segment>> {
    let (c, d) = axis;
    let nrm = [-d[1], d[0]];
    let n = pts.len();
    let side: Vec<f64> = pts.iter().map(|p| (p[0] - c[0]) * nrm[0] + (p[1] - c[1]) * nrm[1]).collect();
    let crossings: Vec<usize> = (0..n).filter(|&i| (side[i] >= 0.0) != (side[(i + 1) % n] >= 0.0)).collect();
    if crossings.len() != 2 {
        return None;
    }
    let (mut i0, mut i1) = (crossings[0], crossings[1]);
    if side[(i0 + 1) % n] < 0.0 {
        std::mem::swap(&mut i0, &mut i1);
    }
    let count = (i1 + n - i0) % n;
    let idx: Vec<usize> = (0..count).map(|k| (i0 + 1 + k) % n).collect();
    if idx.len() < 4 {
        return None;
    }
    let half_inner: Vec<P> = idx.iter().map(|&i| pts[i]).collect();
    let crossing = |a: usize, b: usize| -> P {
        let (pa, pb) = (pts[a], pts[b]);
        let (sa, sb) = (side[a], side[b]);
        let f = if sa != sb { sa / (sa - sb) } else { 0.5 };
        let x = [pa[0] + f * (pb[0] - pa[0]), pa[1] + f * (pb[1] - pa[1])];
        let t = (x[0] - c[0]) * d[0] + (x[1] - c[1]) * d[1];
        [c[0] + d[0] * t, c[1] + d[1] * t]
    };
    let x0 = crossing(i0, (i0 + 1) % n);
    let x1 = crossing(i1, (i1 + 1) % n);
    let approach = |points: &[P], at: P| -> Option<(P, P)> {
        let dist: Vec<f64> = points.iter().map(|p| ((p[0] - at[0]).powi(2) + (p[1] - at[1]).powi(2)).sqrt()).collect();
        let mut sel: Vec<P> = points.iter().zip(&dist).filter(|(_, dd)| **dd >= 0.8 && **dd <= MIRROR_CORNER_REACH).map(|(p, _)| *p).collect();
        if sel.len() < 2 {
            sel = points.iter().zip(&dist).filter(|(_, dd)| **dd > 0.0 && **dd <= 2.0 * MIRROR_CORNER_REACH).map(|(p, _)| *p).collect();
        }
        if sel.len() < 2 {
            return None;
        }
        Some(line_through(&sel))
    };
    let joint = |x: P, points: &[P], into: bool| -> (P, Option<P>) {
        let Some((p, direction)) = approach(points, x) else { return (x, None) };
        let lean = (direction[0] * d[0] + direction[1] * d[1]).abs().min(1.0).asin().to_degrees();
        if 2.0 * lean > params.corner_threshold {
            if let Some(hit) = intersect(p, direction, c, d) {
                if ((hit[0] - x[0]).powi(2) + (hit[1] - x[1]).powi(2)).sqrt() <= 1.5 {
                    return (hit, None);
                }
            }
            return (x, None);
        }
        (x, Some(if into { nrm } else { [-nrm[0], -nrm[1]] }))
    };
    let m = half_inner.len().min(12);
    let (start, t_start) = joint(x0, &half_inner[..m], true);
    let tail: Vec<P> = half_inner[half_inner.len() - m..].iter().rev().copied().collect();
    let (end, t_end) = joint(x1, &tail, false);
    let mut half: Vec<P> = Vec::with_capacity(half_inner.len() + 2);
    half.push(start);
    half.extend_from_slice(&half_inner);
    half.push(end);
    let corners = open_corners(&half, params.corner_threshold);
    let sharp: HashMap<usize, P> = corners.iter().map(|&k| (k, sharp_corner(&half, k))).collect();
    let mut bounds: Vec<usize> = vec![0, half.len() - 1];
    bounds.extend(corners.iter().copied());
    bounds.sort_unstable();
    bounds.dedup();
    let mut pieces: Vec<Vec<P>> = Vec::new();
    let mut spans: Vec<(usize, usize)> = Vec::new();
    for k in 0..bounds.len() - 1 {
        let (lo, hi) = (bounds[k], bounds[k + 1]);
        let mut piece = sharpen_piece(&half, lo, hi, &corners, (0.0, 0.0), None);
        if piece.len() < 2 {
            continue;
        }
        if let Some(p) = sharp.get(&lo) {
            piece[0] = *p;
        }
        if let Some(p) = sharp.get(&hi) {
            let last = piece.len() - 1;
            piece[last] = *p;
        }
        pieces.push(piece);
        spans.push((lo, hi));
    }
    corners_from_runs(&mut pieces, false);
    // a corner on the axis is placed from the straight run that leads into it (see the Python)
    if !pieces.is_empty() && t_start.is_none() {
        axis_corner_from_run(&mut pieces[0], true, c, d);
    }
    if !pieces.is_empty() && t_end.is_none() {
        let last = pieces.len() - 1;
        axis_corner_from_run(&mut pieces[last], false, c, d);
    }
    let mut segments: Vec<Segment> = Vec::new();
    for (piece, (lo, hi)) in pieces.iter().zip(&spans) {
        let ts = if *lo == 0 { t_start } else { None };
        let te = if *hi == half.len() - 1 { t_end } else { None };
        segments.extend(fit_stretch(piece, params.tol, ts, te));
    }
    if segments.is_empty() {
        return None;
    }
    let segments = snap_axis(segments, params.snap_axis_deg);
    let reflected: Vec<Segment> = segments.iter().map(|s| reflect_segment(s, c, d)).collect();
    let mut all = segments.clone();
    all.extend(reverse_segments(&reflected));
    let mut full = merge_lines(all);
    if full.len() > 1 {
        if let (Segment::Line { p0: a0, p1: a1 }, Segment::Line { p0: b0, p1: b1 }) = (full[0].clone(), full[full.len() - 1].clone()) {
            let a = [a1[0] - a0[0], a1[1] - a0[1]];
            let b = [b1[0] - b0[0], b1[1] - b0[1]];
            let (la, lb) = ((a[0] * a[0] + a[1] * a[1]).sqrt(), (b[0] * b[0] + b[1] * b[1]).sqrt());
            if la > 0.0 && lb > 0.0 {
                let cosv = ((a[0] * b[0] + a[1] * b[1]) / (la * lb)).clamp(-1.0, 1.0);
                if cosv.acos().to_degrees() <= MERGE_DEG {
                    let last = full.len() - 1;
                    full[last] = Segment::Line { p0: b0, p1: a1 };
                    full.remove(0);
                }
            }
        }
    }
    Some(full)
}

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
    build_opt(labels, rgb, alpha, fill_at, params, rank, true, true)
}

/// `snap = false` stops after placement, for `tools/diffcheck.py` to compare the
/// two stages apart.
#[allow(clippy::too_many_arguments)]
pub fn build_opt(
    labels: &Labels,
    rgb: &Image,
    alpha: &Grid<f64>,
    fill_at: FillAt,
    params: &CurveParams,
    rank: Option<&HashMap<i32, usize>>,
    snap: bool,
    extend: bool,
) -> Boundary {
    let mut padded = Grid::<i32>::new(labels.h + 2, labels.w + 2);
    for r in 0..labels.h {
        for c in 0..labels.w {
            padded.set(r + 1, c + 1, *labels.get(r, c));
        }
    }
    let (padded, handed_back) = if extend {
        extend_wedges(&padded, rgb, alpha, fill_at)
    } else {
        (padded, std::collections::HashSet::new())
    };

    let edges = boundary_edges(&padded);
    let chain_list = chains(&padded, &edges);
    let placed = place(&chain_list, &edges, &padded, rgb, alpha, fill_at, &handed_back);

    let mut arcs: Vec<Arc> = chain_list
        .iter()
        .zip(placed)
        .map(|(ch, (pts, normal, crowded))| Arc {
            pair: ch.pair,
            pts,
            normal,
            n0: ch.n0,
            n1: ch.n1,
            segments: Vec::new(),
            under: Vec::new(),
            t0: None,
            t1: None,
            tip0: false,
            tip1: false,
            trim0: NODE_TRIM,
            trim1: NODE_TRIM,
            sliver: if crowded.iter().any(|c| *c) { Some(crowded) } else { None },
            mirror: None,
        })
        .collect();

    let mut edge_arc = HashMap::new();
    for (idx, ch) in chain_list.iter().enumerate() {
        for (pos, key) in ch.edges.iter().enumerate() {
            edge_arc.insert(*key, (idx, pos));
        }
    }
    if !snap {
        let later = vec![false; arcs.len()];
        return Boundary { arcs, padded, edge_arc, later_is_b: later };
    }
    // A region that is mirror- or rotationally symmetric is made exactly so
    // before its nodes are placed and its curves fitted (see the Python).
    {
        let mut early = Boundary { arcs, padded: padded.clone(), edge_arc: edge_arc.clone(), later_is_b: Vec::new() };
        symmetrize_boundary(&mut early);
        arcs = early.arcs;
    }
    junctions(&mut arcs, &padded, params.corner_threshold, params.tol);
    for arc in arcs.iter_mut() {
        arc.segments = fit_arc(&arc.pts, arc.closed(), arc.t0, arc.t1, (arc.trim0, arc.trim1), arc.sliver.as_deref(), arc.mirror, params);
    }
    // Across the graph: lines meant to be parallel, perpendicular or on an axis
    // are made exactly so. Nodes never move, so the ring still closes.
    {
        let mut lists: Vec<(&mut Vec<Segment>, bool)> = arcs
            .iter_mut()
            .map(|a| {
                let closed = a.closed();
                (&mut a.segments, closed)
            })
            .collect();
        crate::regularity::regularize(&mut lists, params.snap_axis_deg);
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
            let under = fit_under(&moved, arc, &loose);
            arc.under = under;
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
        // One arc, walked from somewhere along it and wrapping onto its own
        // start: see the Python.
        let step = if seq.len() > 1 { seq[1].1 as i64 - seq[0].1 as i64 } else { 1 };
        return vec![(seq[0].0, step == -1 || step > 1)];
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

#[cfg(test)]
mod node_tests {
    use super::*;

    #[test]
    fn node_estimate_recovers_a_shallow_crossing_and_declines_a_parallel_one() {
        let d1: P = [1.0, 0.0];
        let a = 15f64.to_radians();
        let d2: P = [a.cos(), a.sin()];
        let lines = [Some(([0.0, 20.0], d1, 0.0)), Some(([10.0 - 5.0 * d2[0], 20.0 - 5.0 * d2[1]], d2, 0.0))];
        let got = node_estimate(&lines, [9.0, 20.5], 4.0);
        assert!((got[0] - 10.0).abs() < 1e-9 && (got[1] - 20.0).abs() < 1e-9, "{got:?}");

        let a5 = 5f64.to_radians();
        let d3: P = [a5.cos(), a5.sin()];
        let lines5 = [Some(([0.0, 20.0], d1, 0.0)), Some(([10.0 - 5.0 * d3[0], 20.0 - 5.0 * d3[1]], d3, 0.0))];
        assert_eq!(node_estimate(&lines5, [9.0, 20.5], 4.0), [9.0, 20.5]);
    }

    #[test]
    fn approach_grows_along_a_straight_run_and_reports_its_scatter() {
        let pts: Vec<P> = (0..40).map(|k| [k as f64 * 0.5, 0.0]).collect();
        let (centre, dir, rms) = approach(&pts, true, 4.0, 0.8, APPROACH_MAX, None).unwrap();
        assert!(rms < 0.05 && dir[1].abs() < 1e-12, "{centre:?} {dir:?} {rms}");  // the residual carries the direction uncertainty of the window
        assert!(centre[0] > 4.0, "the window should have grown past the first reach: {centre:?}");
    }
}
