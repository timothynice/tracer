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
    Shape,
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
/// The bled copy's fitting tolerance, as a fraction of the bleed. It has to stay
/// below it: an error larger than the offset would let the copy wander back over
/// the edge it exists to cover.
pub const UNDER_TOL: f64 = 0.6;
/// The bled copy is read off the *visible* curve, sampled this far apart
/// (UNDER_STEP / UNDER_SUB densely; every UNDER_SUB-th sample, and every one
/// turning more than UNDER_TURN degrees, is fitted). See the Python `_under`.
pub const UNDER_STEP: f64 = 1.0;
pub const UNDER_SUB: usize = 4;
pub const UNDER_TURN: f64 = 5.0;
/// An offset sample that comes back nearer the visible curve than this share of
/// its bleed is dropped: the loop an inside corner puts in an offset.
pub const UNDER_CLEAR: f64 = 0.9;
/// The fitted copy keeps within this share of the bleed of the offset samples;
/// a fit that does not is tried again as curves only, then at half the
/// tolerance, UNDER_TRIES times, before the samples are used as they stand.
pub const UNDER_DEV: f64 = 0.3;
pub const UNDER_TRIES: usize = 3;
/// Offset samples closer than this are put on one point (see `under`).
pub const UNDER_SAME: f64 = 1e-9;
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
/// A held wedge tip may sit at most this far beyond the end of its sliver
/// (see `junctions`).
pub const TIP_AHEAD: f64 = 1.5;
/// Two unit directions this close to square cannot orient one another.
pub const ORIENT_TIE: f64 = 1e-9;
/// The sub-pixel placement reads each side's colour as the fitted fill plus the
/// fill's residual, averaged over the region's pure pixels with a Gaussian of
/// LOCAL_SIGMA px (cut at LOCAL_TRUNCATE sigma, scipy's default, which
/// `core::filters::gaussian_filter` twins). LOCAL_SUPPORT is the Gaussian mass
/// of pure pixels at which the correction counts half. See `LocalFills`.
pub const LOCAL_SIGMA: f64 = 2.0;
pub const LOCAL_TRUNCATE: f64 = 4.0;
pub const LOCAL_SUPPORT: f64 = 0.05;
/// A region whose fitted alpha is under this is a transparent field: its colour
/// is inpainting and gets no correction.
pub const LOCAL_OPAQUE_ALPHA: f64 = 128.0;
/// The corrected fills are believed in full where they keep at least
/// LOCAL_KEEP1 of the fitted fills' contrast across the edge, not at all below
/// LOCAL_KEEP0, linearly between (see `coverage`).
pub const LOCAL_KEEP0: f64 = 0.25;
pub const LOCAL_KEEP1: f64 = 0.5;

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
    /// The same curve, bled under the side painted later.
    pub under: Vec<Segment>,
    /// The label `under` reaches into.
    pub under_into: Option<i32>,
    /// `under` starts / ends with a jog from / to its node.
    pub under_jog: (bool, bool),
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
    /// A closed arc that is one (rounded) rectangle, as the primitive (`rectify`).
    pub rect: Option<Shape>,
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
    /// Which side of each arc paints later (the Python's `_later_is_b`).
    #[allow(dead_code)]
    later_is_b: Vec<bool>,
    /// Paint order by label, when the shapes are stacked.
    pub rank: Option<HashMap<i32, usize>>,
}

pub mod rectify;

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
/// arc ends meeting there, where they all move to, any pinned tangents, the
/// wedge sides that close to a tip there, and where all the arcs together
/// place the node (what a tip reverts to: see `junctions`).
type Junction = (Vec<ArcEnd>, P, Vec<(usize, P)>, Vec<usize>, P);

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

/// One region's colour correction: the fill's own RGB residual smoothed over
/// the region's pure pixels, on the region's box widened by the kernel radius.
pub struct LocalGrid {
    pub r0: usize,
    pub c0: usize,
    pub h: usize,
    pub w: usize,
    /// Row-major, RGB per cell.
    pub corr: Vec<[f64; 3]>,
}

/// Each region's fill as it actually is near a point: the fitted model plus the
/// model's own residual there, smoothed over the region's pure pixels.
///
/// A region's fill is one gradient fitted to all of it, and on shaded artwork
/// that model can be a dozen levels off near one of the region's edges. The
/// placement projects an edge pixel onto the segment between the two fills, so
/// an error of that size put the region's own pixels at a coverage near a half
/// and a straight edge between two shaded regions came out wavy. The residual,
/// read from pixels whose eight neighbours are all the region's and averaged
/// with a Gaussian of LOCAL_SIGMA px, is what the model misses locally; where a
/// region has no pure pixels nearby the correction fades out with the support.
/// Colour only: alpha is the coverage itself. See `_local_fills` in the Python.
pub struct LocalFills {
    pub grids: HashMap<i32, LocalGrid>,
}

impl LocalFills {
    pub fn build(labels: &Labels, rgb: &Image, fill_at: FillAt) -> LocalFills {
        use crate::core::filters::{Mode, gaussian_filter};
        let (h, w) = (labels.h, labels.w);
        let radius = (LOCAL_TRUNCATE * LOCAL_SIGMA + 0.5).floor() as usize;
        // `ndimage.find_objects`: each positive label's bounding box
        let mut boxes: HashMap<i32, (usize, usize, usize, usize)> = HashMap::new();
        for r in 0..h {
            for c in 0..w {
                let lab = *labels.get(r, c);
                if lab <= 0 {
                    continue;
                }
                let b = boxes.entry(lab).or_insert((r, r + 1, c, c + 1));
                b.0 = b.0.min(r);
                b.1 = b.1.max(r + 1);
                b.2 = b.2.min(c);
                b.3 = b.3.max(c + 1);
            }
        }
        let mut ids: Vec<i32> = boxes.keys().copied().collect();
        ids.sort_unstable();
        let mut grids = HashMap::new();
        for lab in ids {
            let (br0, br1, bc0, bc1) = boxes[&lab];
            let (r0, r1) = (br0.saturating_sub(radius), (br1 + radius).min(h));
            let (c0, c1) = (bc0.saturating_sub(radius), (bc1 + radius).min(w));
            let (gh, gw) = (r1 - r0, c1 - c0);
            let mut m = Grid::<bool>::new(gh, gw);
            for r in 0..gh {
                for c in 0..gw {
                    m.set(r, c, *labels.get(r + r0, c + c0) == lab);
                }
            }
            // pure: all eight neighbours are the region's own (the canvas frame counts as own)
            let pure = crate::core::morphology::erode_square(&m, true);
            let mut xs = Vec::new();
            let mut ys = Vec::new();
            let mut at = Vec::new();
            for r in 0..gh {
                for c in 0..gw {
                    if *pure.get(r, c) {
                        xs.push((c + c0) as f64 + 0.5);
                        ys.push((r + r0) as f64 + 0.5);
                        at.push((r, c));
                    }
                }
            }
            if at.is_empty() {
                continue;
            }
            let model = fill_at(lab, &xs, &ys);
            let mean_alpha = model.iter().map(|v| v[3]).sum::<f64>() / model.len() as f64;
            if mean_alpha < LOCAL_OPAQUE_ALPHA {
                // a transparent field's colour is inpainting, not ink: see the Python
                continue;
            }
            let den = gaussian_filter(&pure.map(|v| if *v { 1.0 } else { 0.0 }), LOCAL_SIGMA, Mode::Constant, 0.0);
            let mut num = Vec::with_capacity(3);
            for ch in 0..3 {
                let mut res = Grid::<f64>::new(gh, gw);
                for (k, (r, c)) in at.iter().enumerate() {
                    res.set(*r, *c, rgb.at(r + r0, c + c0)[ch] - model[k][ch]);
                }
                num.push(gaussian_filter(&res, LOCAL_SIGMA, Mode::Constant, 0.0));
            }
            let corr: Vec<[f64; 3]> = (0..gh * gw)
                .map(|i| {
                    let d = den.data[i] + LOCAL_SUPPORT;
                    [num[0].data[i] / d, num[1].data[i] / d, num[2].data[i] / d]
                })
                .collect();
            grids.insert(lab, LocalGrid { r0, c0, h: gh, w: gw, corr });
        }
        LocalFills { grids }
    }

    /// The fill of `lab` at each point, corrected inside the region's grid.
    pub fn at(&self, fill_at: FillAt, lab: i32, qx: &[f64], qy: &[f64]) -> Vec<[f64; 4]> {
        let mut out = fill_at(lab, qx, qy);
        let Some(g) = self.grids.get(&lab) else {
            return out;
        };
        for (k, v) in out.iter_mut().enumerate() {
            let col = qx[k].floor() as i64 - g.c0 as i64;
            let row = qy[k].floor() as i64 - g.r0 as i64;
            if row < 0 || col < 0 || row >= g.h as i64 || col >= g.w as i64 {
                continue;
            }
            let c = &g.corr[row as usize * g.w + col as usize];
            v[0] += c[0];
            v[1] += c[1];
            v[2] += c[2];
        }
        out
    }
}

/// How much of each pixel is `lab` rather than `other`, read from its colour.
///
/// Given `local`, the reference colours are the fills as they are beside the
/// edge, where those still differ by LOCAL_KEEP1 of what the fitted fills
/// differ by; where the two sides' local colours meet there is no edge in the
/// colour to place, and the fitted fills place it as before. See the Python.
#[allow(clippy::too_many_arguments)]
fn coverage(
    rgb: &Image,
    alpha: &Grid<f64>,
    pix: &[Pixel],
    lab: i32,
    other: i32,
    fill_at: FillAt,
    local: Option<&LocalFills>,
) -> Vec<f64> {
    // Padded pixel (r, c) is image pixel (r-1, c-1), centred at (c-0.5, r-0.5).
    let qx: Vec<f64> = pix.iter().map(|p| p.1 as f64 - 0.5).collect();
    let qy: Vec<f64> = pix.iter().map(|p| p.0 as f64 - 0.5).collect();
    let mut f_a = fill_at(lab, &qx, &qy);
    let mut f_b = fill_at(other, &qx, &qy);
    if let Some(local) = local {
        let l_a = local.at(fill_at, lab, &qx, &qy);
        let l_b = local.at(fill_at, other, &qx, &qy);
        for k in 0..pix.len() {
            let (mut kept2, mut fitted2) = (0.0, 0.0);
            for ch in 0..4 {
                kept2 += (l_a[k][ch] - l_b[k][ch]).powi(2);
                fitted2 += (f_a[k][ch] - f_b[k][ch]).powi(2);
            }
            let kept = kept2.sqrt() / fitted2.sqrt().max(1e-9);
            let wt = ((kept - LOCAL_KEEP0) / (LOCAL_KEEP1 - LOCAL_KEEP0)).clamp(0.0, 1.0);
            for ch in 0..4 {
                f_a[k][ch] += wt * (l_a[k][ch] - f_a[k][ch]);
                f_b[k][ch] += wt * (l_b[k][ch] - f_b[k][ch]);
            }
        }
    }
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
    local: Option<&LocalFills>,
) -> (Vec<f64>, Vec<i8>) {
    let n = p_in.len();
    let here_raw = coverage(rgb, alpha, p_in, a, b, fill_at, local);
    let there_raw = coverage(rgb, alpha, p_out, a, b, fill_at, local);
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
        let cov = coverage(rgb, alpha, &pix, a, b, fill_at, local);
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
            let placed = (over + (t - over) * trust).clamp(-REACH, 1.0 + REACH);
            // Where no crossing was found, which way the samples say the edge
            // lies: -1 all four read as `b`, so it is beyond the `a` pixel; +1
            // all read as `a`, beyond the `b` pixel; 0 found, or they disagree.
            let side = if best.is_finite() {
                0
            } else if level.iter().all(|v| *v < 0.5) {
                -1
            } else if level.iter().all(|v| *v >= 0.5) {
                1
            } else {
                0
            };
            (placed, side)
        })
        .unzip()
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
    local: Option<&LocalFills>,
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
            let (t, side) = if a != 0 && b != 0 {
                crossing(padded, rgb, alpha, &p_in, &p_out, a, b, fill_at, local)
            } else {
                (vec![0.5; ch.edges.len()], vec![0i8; ch.edges.len()])
            };
            let crowded: Vec<bool> = (0..ch.edges.len())
                .map(|k| handed_back.contains(&p_in[k]) || handed_back.contains(&p_out[k]))
                .collect();
            let mut pts: Vec<P> = Vec::with_capacity(ch.edges.len());
            let mut normal = Vec::with_capacity(ch.edges.len());
            let mut placed_t = Vec::with_capacity(ch.edges.len());
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
                placed_t.push(tk);
            }
            if pts.len() >= 3 {
                // A vertex with no crossing within reach along its own step,
                // whose neighbours both found the edge beyond the label edge on
                // the side its samples point to, takes their midpoint: left at
                // the label edge it is a spike. See the Python.
                let beyond_a = |k: usize| placed_t[k] < 0.0 && side[k] == 0;
                let beyond_b = |k: usize| placed_t[k] > 1.0 && side[k] == 0;
                let before = pts.clone();
                for k in 1..pts.len() - 1 {
                    let lone = (side[k] == -1 && beyond_a(k - 1) && beyond_a(k + 1))
                        || (side[k] == 1 && beyond_b(k - 1) && beyond_b(k + 1));
                    if lone {
                        pts[k] = [(before[k - 1][0] + before[k + 1][0]) / 2.0, (before[k - 1][1] + before[k + 1][1]) / 2.0];
                    }
                }
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

/// Does this end of the arc run along a handed-back sliver (its last three vertices)?
fn sliver_at(arc: &Arc, at_start: bool) -> bool {
    const REACH_VERTICES: usize = 3;
    match &arc.sliver {
        None => false,
        Some(s) => {
            let n = s.len();
            let part = if at_start { &s[..REACH_VERTICES.min(n)] } else { &s[n - REACH_VERTICES.min(n)..] };
            part.iter().any(|v| *v)
        }
    }
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
        let plain = target;
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
                        let mut lean = again.1[0] * away[k][0] + again.1[1] * away[k][1];
                        if lean.abs() < ORIENT_TIE {
                            // square to the first line: the sign would be the
                            // line fit's own, so orient it towards its window
                            // (see the Python)
                            let end = if at_start { arcs[i].pts[0] } else { arcs[i].pts[arcs[i].pts.len() - 1] };
                            lean = again.1[0] * (again.0[0] - end[0]) + again.1[1] * (again.0[1] - end[1]);
                        }
                        let flip = lean < 0.0;
                        away[k] = normalize(if flip { [-again.1[0], -again.1[1]] } else { again.1 });
                        if let Some(entry) = pinned.iter_mut().find(|(s, _)| *s == slot) {
                            entry.1 = away[k];
                        }
                    }
                }
                let side_lines: Vec<Option<(P, P, f64)>> = tips.iter().map(|s| lines[*s]).collect();
                let mut tip_at = node_estimate(&side_lines, mean, TIP_LIMIT);
                if tips.iter().any(|s| {
                    let (i, at_start) = incident[*s];
                    sliver_at(&arcs[i], at_start)
                }) {
                    // The handed-back sliver is the colour's own evidence of
                    // how far the wedge reaches, and the arcs end where it
                    // ends. The tip may move across the wedge and at most
                    // TIP_AHEAD further out, never back into it. See the Python.
                    let side_away: Vec<P> = (0..3).filter(|k| *k != through).map(|k| away[k]).collect();
                    let inward = [side_away[0][0] + side_away[1][0], side_away[0][1] + side_away[1][1]];
                    let length = inward[0].hypot(inward[1]);
                    if length > 1e-9 {
                        let inward = [inward[0] / length, inward[1] / length];
                        let along = (tip_at[0] - mean[0]) * inward[0] + (tip_at[1] - mean[1]) * inward[1];
                        let excess = along - along.clamp(-TIP_AHEAD, 0.0);
                        tip_at = [tip_at[0] - excess * inward[0], tip_at[1] - excess * inward[1]];
                    }
                    // ...and it sits on the boundary that carries on through
                    // it, so that boundary stays one line.
                    if let Some((c, d, _)) = lines[long[through]] {
                        let s = (tip_at[0] - c[0]) * d[0] + (tip_at[1] - c[1]) * d[1];
                        tip_at = [c[0] + d[0] * s, c[1] + d[1] * s];
                    }
                    // Placed from the sliver, the tip no longer needs the side
                    // lines' directions, and on a curved side they are wrong
                    // for a tangent: the sides leave the tip along their own
                    // vertices. See the Python.
                    pinned.retain(|(s, _)| !tips.contains(s));
                }
                target = on_border(tip_at, arcs, &incident);
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
        moves.push((incident, target, pinned, tips, plain));
    }

    // A tip is placed from its two sides alone and may travel up to TIP_LIMIT,
    // which on a short arc is further than the arc is long: the tip then lands
    // beyond the node at the arc's other end, the arc between them runs
    // backwards, and its fit is a hairpin that crosses both neighbours. A node
    // that would turn an arc round goes back to where all its arcs together
    // place it. See the Python `_junctions`.
    let mut at: HashMap<ArcEnd, usize> = HashMap::new();
    for (m, mv) in moves.iter().enumerate() {
        for key in &mv.0 {
            at.insert(*key, m);
        }
    }
    let mut targets: Vec<P> = moves.iter().map(|mv| mv.1).collect();
    let mut reverted = vec![false; moves.len()];
    for _ in 0..moves.len() {
        let mut undo: Vec<usize> = Vec::new();
        for (idx, arc) in arcs.iter().enumerate() {
            if arc.closed() || short[idx] {
                continue;
            }
            let (Some(&m0), Some(&m1)) = (at.get(&(idx, true)), at.get(&(idx, false))) else { continue };
            if m0 == m1 {
                continue;
            }
            let (first, last) = (arc.pts[0], arc.pts[arc.pts.len() - 1]);
            let chord = [last[0] - first[0], last[1] - first[1]];
            let d = [targets[m1][0] - targets[m0][0], targets[m1][1] - targets[m0][1]];
            if d[0] * chord[0] + d[1] * chord[1] > 0.0 {
                continue;
            }
            for m in [m0, m1] {
                if !moves[m].3.is_empty() && !reverted[m] {
                    undo.push(m);
                }
            }
        }
        if undo.is_empty() {
            break;
        }
        undo.sort_unstable();
        undo.dedup();
        for m in undo {
            targets[m] = moves[m].4;
            reverted[m] = true;
        }
    }

    for (m, (incident, _, pinned, tips, _)) in moves.into_iter().enumerate() {
        let target = targets[m];
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

/// +1 when `pair.1` lies on the left of the arc as its vertices run, else -1.
///
/// One sign for the whole arc, voted by every vertex: an arc parts the same two
/// regions all the way along, with the same one on its left. Asked vertex by
/// vertex, the vote failed at a stair step and pushed that vertex a pixel *out*
/// of the later shape. See the Python `_side`.
fn side(arc: &Arc) -> f64 {
    let pts = &arc.pts;
    let n = pts.len().min(arc.normal.len());
    let closed = arc.closed();
    let mut sum = 0.0;
    for k in 0..n {
        let ahead = if closed { pts[(k + 1) % n] } else { pts[(k + 1).min(n - 1)] };
        let behind = if closed { pts[(k + n - 1) % n] } else { pts[k.saturating_sub(1)] };
        let t = [ahead[0] - behind[0], ahead[1] - behind[1]];
        sum += -t[1] * arc.normal[k][0] + t[0] * arc.normal[k][1];
    }
    if sum < 0.0 {
        -1.0
    } else {
        1.0
    }
}

/// `m + 1` parameters from 0 to 1, as `np.linspace(0, 1, m + 1)` makes them.
fn unit_steps(m: usize) -> Vec<f64> {
    let step = 1.0 / m as f64;
    let mut t: Vec<f64> = (0..=m).map(|k| k as f64 * step).collect();
    t[m] = 1.0;
    t
}

/// Points along a fitted curve no more than `step` apart, with the unit tangent
/// at each. Every segment is sampled end to end, so a join is sampled once from
/// each side and a corner carries both of its tangents. See the Python `_sample`.
pub fn sample(segments: &[Segment], step: f64) -> (Vec<P>, Vec<P>) {
    let mut points: Vec<P> = Vec::new();
    let mut tangents: Vec<P> = Vec::new();
    for seg in segments {
        let (pts, mut tan): (Vec<P>, Vec<P>) = match *seg {
            Segment::Line { p0, p1 } => {
                let d = [p1[0] - p0[0], p1[1] - p0[1]];
                let m = (((d[0] * d[0] + d[1] * d[1]).sqrt() / step).ceil() as usize).max(1);
                let t = unit_steps(m);
                (t.iter().map(|u| [p0[0] + u * d[0], p0[1] + u * d[1]]).collect(), vec![d; m + 1])
            }
            Segment::Cubic { p0, c1, c2, p1 } => {
                let length = 0.5 * (dist(p1, p0) + (dist(c1, p0) + dist(c2, c1) + dist(p1, c2)));
                let m = ((length / step).ceil() as usize).max(2);
                let t = unit_steps(m);
                (
                    t.iter().map(|u| crate::curves::bezier(p0, c1, c2, p1, *u)).collect(),
                    t.iter().map(|u| crate::curves::bezier_d1(p0, c1, c2, p1, *u)).collect(),
                )
            }
            Segment::Arc { p0, p1, r, large, sweep } => {
                let c = crate::reuse::arc_centre(p0, p1, r, large, sweep);
                let r = dist(p0, c).max(1e-9);
                let a0 = (p0[1] - c[1]).atan2(p0[0] - c[0]);
                let a1 = (p1[1] - c[1]).atan2(p1[0] - c[0]);
                let tau = 2.0 * std::f64::consts::PI;
                let span = if sweep { (a1 - a0).rem_euclid(tau) } else { -((a0 - a1).rem_euclid(tau)) };
                let m = ((span.abs() * r / step).ceil() as usize).max(2);
                let t = unit_steps(m);
                let a: Vec<f64> = t.iter().map(|u| a0 + span * u).collect();
                let mut pts: Vec<P> = a.iter().map(|v| [c[0] + r * v.cos(), c[1] + r * v.sin()]).collect();
                pts[0] = p0;
                pts[m] = p1;
                let s = if span >= 0.0 { 1.0 } else { -1.0 };
                (pts, a.iter().map(|v| [-v.sin() * s, v.cos() * s]).collect())
            }
        };
        // a control point on its end: the chord to the next sample
        let n = pts.len();
        for k in 0..n {
            if (tan[k][0] * tan[k][0] + tan[k][1] * tan[k][1]).sqrt() < 1e-9 {
                tan[k] = if k + 1 < n { [pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1]] } else { [pts[n - 1][0] - pts[n - 2][0], pts[n - 1][1] - pts[n - 2][1]] };
            }
        }
        for k in 0..n {
            let len = (tan[k][0] * tan[k][0] + tan[k][1] * tan[k][1]).sqrt().max(1e-12);
            tangents.push([tan[k][0] / len, tan[k][1] / len]);
        }
        points.extend(pts);
    }
    (points, tangents)
}

/// Distance from each point of `q` to the polyline `poly`. A distance over
/// `within` may come back as anything over `within` (only the segments near a
/// block of points are searched). See the Python `_clearance`.
fn clearance(q: &[P], poly: &[P], within: f64) -> Vec<f64> {
    if poly.len() < 2 || q.is_empty() {
        if poly.len() == 1 {
            return q.iter().map(|p| dist(*p, poly[0])).collect();
        }
        return vec![f64::INFINITY; q.len()];
    }
    let segs: Vec<(P, P, f64, P, P)> = poly
        .windows(2)
        .map(|w| {
            let ab = [w[1][0] - w[0][0], w[1][1] - w[0][1]];
            let den = (ab[0] * ab[0] + ab[1] * ab[1]).max(1e-18);
            let lo = [w[0][0].min(w[1][0]), w[0][1].min(w[1][1])];
            let hi = [w[0][0].max(w[1][0]), w[0][1].max(w[1][1])];
            (w[0], ab, den, lo, hi)
        })
        .collect();
    let mut out = vec![f64::INFINITY; q.len()];
    for (block, chunk) in q.chunks(256).enumerate() {
        let near: Vec<&(P, P, f64, P, P)> = if within.is_finite() {
            let mut lo = [f64::INFINITY; 2];
            let mut hi = [f64::NEG_INFINITY; 2];
            for p in chunk {
                lo = [lo[0].min(p[0]), lo[1].min(p[1])];
                hi = [hi[0].max(p[0]), hi[1].max(p[1])];
            }
            let (lo, hi) = ([lo[0] - within, lo[1] - within], [hi[0] + within, hi[1] + within]);
            segs.iter().filter(|s| s.4[0] >= lo[0] && s.4[1] >= lo[1] && s.3[0] <= hi[0] && s.3[1] <= hi[1]).collect()
        } else {
            segs.iter().collect()
        };
        if near.is_empty() {
            continue;
        }
        for (k, p) in chunk.iter().enumerate() {
            let mut best = f64::INFINITY;
            for (a, ab, den, _, _) in &near {
                let t = (((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / den).clamp(0.0, 1.0);
                let dx = p[0] - (a[0] + t * ab[0]);
                let dy = p[1] - (a[1] + t * ab[1]);
                best = best.min(dx * dx + dy * dy);
            }
            out[block * 256 + k] = best.sqrt();
        }
    }
    out
}

/// How far each ray `pts[i] + t·normal[i]` (0 <= t <= far) runs before it meets
/// one of the polylines in `walls`; infinity where it meets none. See the
/// Python `_ray_gap`.
fn ray_gap(pts: &[P], normal: &[P], walls: &[&[P]], far: f64) -> Vec<f64> {
    let mut gap = vec![f64::INFINITY; pts.len()];
    if pts.is_empty() {
        return gap;
    }
    let mut lo = [f64::INFINITY; 2];
    let mut hi = [f64::NEG_INFINITY; 2];
    for p in pts {
        lo = [lo[0].min(p[0]), lo[1].min(p[1])];
        hi = [hi[0].max(p[0]), hi[1].max(p[1])];
    }
    let (lo, hi) = ([lo[0] - far, lo[1] - far], [hi[0] + far, hi[1] + far]);
    for wall in walls {
        if wall.len() < 2 {
            continue;
        }
        let mut wlo = [f64::INFINITY; 2];
        let mut whi = [f64::NEG_INFINITY; 2];
        for p in wall.iter() {
            wlo = [wlo[0].min(p[0]), wlo[1].min(p[1])];
            whi = [whi[0].max(p[0]), whi[1].max(p[1])];
        }
        if whi[0] < lo[0] || whi[1] < lo[1] || wlo[0] > hi[0] || wlo[1] > hi[1] {
            continue;
        }
        for w in wall.windows(2) {
            let (a, e) = (w[0], [w[1][0] - w[0][0], w[1][1] - w[0][1]]);
            // a ray reaches no further than `far` from its origin
            let (slo, shi) = ([a[0].min(w[1][0]) - far, a[1].min(w[1][1]) - far], [a[0].max(w[1][0]) + far, a[1].max(w[1][1]) + far]);
            for (i, p) in pts.iter().enumerate() {
                if p[0] < slo[0] || p[1] < slo[1] || p[0] > shi[0] || p[1] > shi[1] {
                    continue;
                }
                let n = normal[i];
                let den = n[0] * e[1] - n[1] * e[0];
                if den.abs() <= 1e-12 {
                    continue;
                }
                let ap = [a[0] - p[0], a[1] - p[1]];
                let t = (ap[0] * e[1] - ap[1] * e[0]) / den;
                let u = (ap[0] * n[1] - ap[1] * n[0]) / den;
                if (0.0..=1.0).contains(&u) && (0.0..=far).contains(&t) && t < gap[i] {
                    gap[i] = t;
                }
            }
        }
    }
    gap
}

/// The arc's visible curve pushed `amount` towards one side (towards `pair.1`
/// when positive), for the side painted earlier to use: an offset of the curve
/// actually drawn, never more than halfway to the far side of the shape bled
/// under (`walls`), fitted loosely and checked against the offset. An open
/// arc's copy is pinned to its two nodes by a jog at each end; the flags say
/// which ends have one. See the Python `_under`.
pub fn under(arc: &Arc, amount: f64, params: &CurveParams, walls: &[&[P]]) -> (Vec<Segment>, (bool, bool)) {
    if arc.segments.is_empty() {
        return (Vec::new(), (false, false));
    }
    let bleed = amount.abs();
    let (pts, tangent) = sample(&arc.segments, UNDER_STEP / UNDER_SUB as f64);
    let s = 1f64.copysign(amount) * side(arc);
    let normal: Vec<P> = tangent.iter().map(|t| [s * -t[1], s * t[0]]).collect();
    let mut reach = vec![bleed; pts.len()];
    if !walls.is_empty() {
        let ahead = ray_gap(&pts, &normal, walls, 2.0 * bleed);
        let back: Vec<P> = normal.iter().map(|n| [-n[0], -n[1]]).collect();
        let behind = ray_gap(&pts, &back, walls, bleed);
        for k in 0..pts.len() {
            reach[k] = reach[k].min(0.5 * ahead[k]);
            // a far side met *behind* the edge: the shape's two edges crossed
            if behind[k] < bleed {
                reach[k] = 0.0;
            }
        }
    }
    let offset: Vec<P> = (0..pts.len()).map(|k| [pts[k][0] + reach[k] * normal[k][0], pts[k][1] + reach[k] * normal[k][1]]).collect();
    let clear = clearance(&offset, &pts, bleed);
    let mut moved: Vec<P> = (0..offset.len()).filter(|k| clear[*k] >= UNDER_CLEAR * reach[*k] - 1e-9).map(|k| offset[k]).collect();
    let closed = arc.closed();
    // A smooth join is sampled once from each side and its two offsets land a
    // rounding error apart: the second is put exactly on the first, so the
    // fit's splits do not turn on the last bit of the offset. See the Python
    // `_under`.
    let same: Vec<usize> = (1..moved.len()).filter(|k| dist(moved[*k], moved[*k - 1]) <= UNDER_SAME).collect();
    for k in same {
        moved[k] = moved[k - 1];
    }
    if closed && moved.len() > 1 && dist(moved[moved.len() - 1], moved[0]) <= UNDER_SAME {
        let first = moved[0];
        let last = moved.len() - 1;
        moved[last] = first;
    }
    if moved.len() < 2 || (closed && moved.len() < 4) {
        return (arc.segments.clone(), (false, false));
    }
    let mut dense = moved.clone();
    if closed {
        dense.push(moved[0]);
    }
    // every UNDER_SUB-th sample, and every one where the offset turns
    let steps: Vec<P> = dense.windows(2).map(|w| [w[1][0] - w[0][0], w[1][1] - w[0][1]]).collect();
    let lengths: Vec<f64> = steps.iter().map(|d| (d[0] * d[0] + d[1] * d[1]).sqrt().max(1e-12)).collect();
    let cos_limit = UNDER_TURN.to_radians().cos();
    let mut pick: Vec<usize> = (0..dense.len()).step_by(UNDER_SUB).collect();
    for k in 1..steps.len() {
        let cos_turn = (steps[k][0] * steps[k - 1][0] + steps[k][1] * steps[k - 1][1]) / (lengths[k] * lengths[k - 1]);
        if cos_turn < cos_limit {
            pick.push(k);
        }
    }
    pick.push(dense.len() - 1);
    pick.sort_unstable();
    pick.dedup();
    let run: Vec<P> = pick.iter().map(|k| dense[*k]).collect();
    let mut fitted: Option<Vec<Segment>> = None;
    // lines first, as the visible curve was fitted; then curves only; then tighter
    for k in 0..=UNDER_TRIES {
        let segs = if k == 0 {
            fit_stretch(&run, params.tol, None, None, params.kind_tol)
        } else {
            fit_open(&run, params.tol * 0.5f64.powi(k as i32 - 1), None, None)
        };
        let (probe, _) = sample(&segs, UNDER_STEP / UNDER_SUB as f64);
        if !probe.is_empty() && clearance(&probe, &dense, 2.0 * bleed).into_iter().fold(f64::NEG_INFINITY, f64::max) <= UNDER_DEV * bleed {
            fitted = Some(segs);
            break;
        }
    }
    let fitted = fitted.unwrap_or_else(|| {
        run.windows(2).filter(|w| dist(w[1], w[0]) > 1e-9).map(|w| Segment::Line { p0: w[0], p1: w[1] }).collect()
    });
    if closed {
        return (fitted, (false, false));
    }
    let head = arc.segments[0].start();
    let tail = arc.segments[arc.segments.len() - 1].end();
    let first = moved[0];
    let last = moved[moved.len() - 1];
    let jog = (dist(first, head) > 1e-9, dist(tail, last) > 1e-9);
    let mut out: Vec<Segment> = Vec::with_capacity(fitted.len() + 2);
    if jog.0 {
        out.push(Segment::Line { p0: head, p1: first });
    }
    out.extend(fitted);
    if jog.1 {
        out.push(Segment::Line { p0: last, p1: tail });
    }
    (out, jog)
}

/// Every fitted arc's bled copy (`under`, `under_into`, `under_jog`), for the
/// side painted earlier to use; and, per arc, whether `pair.1` paints later.
///
/// One copy per arc, into the side first painted later (its own shape's paint,
/// or that of the shape filling it underneath, `painted_by`), never into a
/// `see_through` label, reaching no further than halfway to that shape's far
/// side where crossing it would show over something painted before the copy's
/// painter. See the Python `_bleed_arcs`.
pub fn bleed_arcs(
    arcs: &mut [Arc],
    params: &CurveParams,
    rank: Option<&HashMap<i32, usize>>,
    bleed: f64,
    see_through: &std::collections::HashSet<i32>,
    painted_by: &HashMap<i32, i32>,
) -> Vec<bool> {
    let rank_of = |lab: i32| -> i64 { rank.and_then(|r| r.get(&lab)).map_or(-1, |v| *v as i64) };
    let mut by_label: HashMap<i32, Vec<usize>> = HashMap::new();
    for (idx, arc) in arcs.iter().enumerate() {
        let (a, b) = arc.pair;
        by_label.entry(a.min(b)).or_default().push(idx);
        if a != b {
            by_label.entry(a.max(b)).or_default().push(idx);
        }
    }
    // The earliest paint on each label: its own shape's, or that of the shape
    // filling it underneath.
    let mut floor: HashMap<i32, i64> = rank.map(|r| r.iter().map(|(k, v)| (*k, *v as i64)).collect()).unwrap_or_default();
    if let Some(r) = rank {
        for (lab, owner) in painted_by {
            if let (Some(a), Some(b)) = (r.get(lab), r.get(owner)) {
                floor.insert(*lab, (*a).min(*b) as i64);
            }
        }
    }
    let floor_of = |lab: i32| -> i64 { floor.get(&lab).copied().unwrap_or(-1) };
    let mut drawn: Vec<Option<Vec<P>>> = vec![None; arcs.len()];
    let loose = CurveParams { tol: (2.0 * params.tol).min(UNDER_TOL * bleed), kind_tol: f64::INFINITY, ..*params };

    let mut later_is_b = Vec::with_capacity(arcs.len());
    for idx in 0..arcs.len() {
        let (a, b) = arcs[idx].pair;
        later_is_b.push(rank.is_some() && rank_of(b) > rank_of(a));
        if rank.is_none() || bleed <= 0.0 || a == 0 || b == 0 {
            continue;
        }
        let (into, side_lab) = if floor_of(a) < floor_of(b) { (b, a) } else { (a, b) };
        if see_through.contains(&into) || floor_of(side_lab) >= rank_of(into) {
            continue;
        }
        // the latest shape to use the copy: the side's own, when it is earlier
        let painter = if rank_of(side_lab) < rank_of(into) { rank_of(side_lab) } else { floor_of(side_lab) };
        // The far side of the shape bled under, where crossing it would put the
        // painter's colour over something painted before it.
        let mut wall_idx: Vec<usize> = Vec::new();
        for &j in by_label.get(&into).map(|v| v.as_slice()).unwrap_or(&[]) {
            let other = if arcs[j].pair.1 == into { arcs[j].pair.0 } else { arcs[j].pair.1 };
            if j == idx || arcs[j].segments.is_empty() || other == 0 || rank_of(other) >= painter {
                continue;
            }
            if drawn[j].is_none() {
                drawn[j] = Some(sample(&arcs[j].segments, UNDER_STEP).0);
            }
            wall_idx.push(j);
        }
        let walls: Vec<&[P]> = wall_idx.iter().map(|j| drawn[*j].as_deref().unwrap()).collect();
        let (copy, jog) = under(&arcs[idx], if into == b { bleed } else { -bleed }, &loose, &walls);
        arcs[idx].under = copy;
        arcs[idx].under_jog = jog;
        arcs[idx].under_into = Some(into);
    }
    later_is_b
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
    // A corner inside a node's approach window is believed no more than the
    // other vertices there: where the node was moved back up its approach, the
    // chain overshoots it and doubles back, and that fold kept as a break was a
    // hook past the node that crossed the neighbour. See the Python `_fit_arc`.
    let corners: Vec<usize> = open_corners(pts, params.corner_threshold)
        .into_iter()
        .filter(|k| dist(pts[*k], pts[0]) >= trims.0 && dist(pts[*k], pts[n - 1]) >= trims.1)
        .collect();
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
        segments.extend(fit_stretch(piece, params.tol, ts, te, params.kind_tol));
    }
    snap_axis(segments, params.snap_axis_deg)
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
        segments.extend(fit_stretch(piece, params.tol, ts, te, params.kind_tol));
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

/// What the bled copies may not reach under: paint that does not hide what
/// lies beneath it (a translucent fill, a line drawn along a region's middle),
/// and, for a label no fill of its own paints (a stroked region), the earlier
/// shape filling it underneath. See the Python `build`.
#[derive(Default)]
pub struct Underlay {
    pub see_through: std::collections::HashSet<i32>,
    pub painted_by: HashMap<i32, i32>,
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
    underlay: &Underlay,
) -> Boundary {
    build_opt(labels, rgb, alpha, fill_at, params, rank, underlay, true, true)
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
    underlay: &Underlay,
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
    let mut timer = crate::timing::Timer::new();
    // The placement reads colour against the fills as they are beside each edge.
    let local = LocalFills::build(labels, rgb, fill_at);
    let placed = place(&chain_list, &edges, &padded, rgb, alpha, fill_at, &handed_back, Some(&local));
    timer.lap("topology: chains + place");

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
            under_into: None,
            under_jog: (false, false),
            t0: None,
            t1: None,
            tip0: false,
            tip1: false,
            trim0: NODE_TRIM,
            trim1: NODE_TRIM,
            sliver: if crowded.iter().any(|c| *c) { Some(crowded) } else { None },
            mirror: None,
            rect: None,
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
        return Boundary { arcs, padded, edge_arc, later_is_b: later, rank: rank.cloned() };
    }
    // A region that is mirror- or rotationally symmetric is made exactly so
    // before its nodes are placed and its curves fitted (see the Python).
    {
        let mut early = Boundary { arcs, padded: padded.clone(), edge_arc: edge_arc.clone(), later_is_b: Vec::new(), rank: None };
        symmetrize_boundary(&mut early);
        arcs = early.arcs;
    }
    timer.lap("topology: symmetry");
    junctions(&mut arcs, &padded, params.corner_threshold, params.tol);
    timer.lap("topology: junctions");
    for arc in arcs.iter_mut() {
        arc.segments = fit_arc(&arc.pts, arc.closed(), arc.t0, arc.t1, (arc.trim0, arc.trim1), arc.sliver.as_deref(), arc.mirror, params);
    }
    timer.lap("topology: fit");
    // Rounded rectangles drawn as a designer draws them: one radius per shape
    // and per size of shape, edges on shared guides (see `rectify`); and a
    // rounded corner between two lines anywhere else is one circle, of the
    // radius the rest of the mark's corners share where they agree.
    let mut whole = Boundary { arcs, padded, edge_arc, later_is_b: Vec::new(), rank: None };
    let (rect_arcs, radii) = rectify::rectify(&mut whole, params, rgb, None);
    rectify::fillets(&mut whole, params, &rect_arcs, &radii, None);
    let Boundary { mut arcs, padded, edge_arc, .. } = whole;
    timer.lap("topology: rectangles");
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

    let later_is_b = bleed_arcs(&mut arcs, params, rank, BLEED, &underlay.see_through, &underlay.painted_by);
    timer.lap("topology: bleed");

    Boundary { arcs, padded, edge_arc, later_is_b, rank: rank.cloned() }
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
    /// A boundary of arcs fitted elsewhere, for `tools/diffcheck.py`'s `under`
    /// stage: `segments` needs only the arcs, the padded label map and the rank.
    pub fn assembled(arcs: Vec<Arc>, padded: Labels, rank: Option<HashMap<i32, usize>>) -> Self {
        let later_is_b = vec![false; arcs.len()];
        Boundary { arcs, padded, edge_arc: HashMap::new(), later_is_b, rank }
    }

    /// A boundary from its parts, for `tools/diffcheck.py`'s stage hooks: the
    /// arcs as another implementation left them, and its edge index.
    pub fn from_parts(arcs: Vec<Arc>, padded: Labels, edge_arc: HashMap<u64, (usize, usize)>) -> Boundary {
        let later_is_b = vec![false; arcs.len()];
        Boundary { arcs, padded, edge_arc, later_is_b, rank: None }
    }

    /// The whole-shape primitive a ring already is, when `rectify` made it one.
    pub fn primitive(&self, ring: &[(usize, bool)]) -> Option<Shape> {
        if ring.len() == 1 {
            return self.arcs[ring[0].0].rect.clone();
        }
        None
    }

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
    /// this shape's ink rather than on the backdrop. Two copies meeting at a
    /// node where everything else is painted later are joined directly (the
    /// shape runs past the node, under the later ones), and a gap between two
    /// pieces (an arc too short to carry both its nodes) is bridged with a
    /// line. See the Python `Boundary.segments`.
    pub fn segments(&self, ring: &[(usize, bool)], member: Option<&std::collections::HashSet<i32>>) -> Vec<Segment> {
        let rank_of = |lab: i32| -> i64 { self.rank.as_ref().and_then(|r| r.get(&lab)).map_or(-1, |v| *v as i64) };
        let own: Option<i64> = match member {
            Some(m) if !m.is_empty() && self.rank.is_some() => m.iter().map(|x| rank_of(*x)).min(),
            _ => None,
        };
        // (segments, (jog in, jog out), start node, end node)
        let mut pieces: Vec<(Vec<Segment>, (bool, bool), Option<u64>, Option<u64>)> = Vec::with_capacity(ring.len());
        for (idx, reverse) in ring {
            let arc = &self.arcs[*idx];
            let mut segs = &arc.segments;
            let mut jog = (false, false);
            if let Some(member) = member {
                if !arc.under.is_empty() && !arc.under_into.is_some_and(|u| member.contains(&u)) {
                    // the far side is painted after this shape: reach under it
                    if own.is_none_or(|o| arc.under_into.map_or(-1, rank_of) > o) {
                        segs = &arc.under;
                        jog = arc.under_jog;
                    }
                }
            }
            if *reverse {
                pieces.push((reverse_segments(segs), (jog.1, jog.0), arc.n1, arc.n0));
            } else {
                pieces.push((segs.clone(), jog, arc.n0, arc.n1));
            }
        }
        if let (Some(member), Some(own)) = (member, own) {
            let n = pieces.len();
            if n > 1 {
                for k in 0..n {
                    let q = (k + 1) % n;
                    let (p_end, q_start) = (pieces[k].3, pieces[q].2);
                    if !(pieces[k].1 .1 && pieces[q].1 .0) || p_end.is_none() || p_end != q_start || !self.all_later(p_end.unwrap(), member, own) {
                        continue;
                    }
                    if pieces[k].0.is_empty() || pieces[q].0.is_empty() {
                        continue;
                    }
                    let from = pieces[k].0[pieces[k].0.len() - 1].start();
                    let to = pieces[q].0[0].end();
                    let last = pieces[k].0.len() - 1;
                    pieces[k].0[last] = Segment::Line { p0: from, p1: to };
                    pieces[q].0.remove(0);
                    pieces[k].1 .1 = false;
                    pieces[q].1 .0 = false;
                }
            }
        }
        let mut out: Vec<Segment> = Vec::new();
        for (segs, _, _, _) in pieces {
            for seg in segs {
                if let Some(prev) = out.last() {
                    let (a, b) = (prev.end(), seg.start());
                    if dist(a, b) > 1e-6 {
                        out.push(Segment::Line { p0: a, p1: b });
                    }
                }
                out.push(seg);
            }
        }
        out
    }

    /// Every label at a lattice node is the shape's own or painted after it.
    fn all_later(&self, node: u64, member: &std::collections::HashSet<i32>, own: i64) -> bool {
        let lat_cols = (self.padded.w + 1) as u64;
        let (i, j) = ((node / lat_cols) as usize, (node % lat_cols) as usize);
        let rank_of = |lab: i32| -> i64 { self.rank.as_ref().and_then(|r| r.get(&lab)).map_or(-1, |v| *v as i64) };
        for r in i.saturating_sub(1)..(i + 1).min(self.padded.h) {
            for c in j.saturating_sub(1)..(j + 1).min(self.padded.w) {
                let v = *self.padded.get(r, c);
                if !(member.contains(&v) || (v != 0 && rank_of(v) > own)) {
                    return false;
                }
            }
        }
        true
    }
}

#[cfg(test)]
mod node_tests {
    use super::*;

    #[test]
    fn local_fills_read_the_colour_beside_the_edge() {
        // the Python's test_local_fills_read_the_colour_beside_the_edge
        let (h, w) = (20, 20);
        let mut labels: Labels = Grid::new(h, w);
        let mut rgb = Image::new(h, w, 3);
        for r in 0..h {
            for c in 0..w {
                let (lab, col) = if c < 10 { (1, [20.0, 120.0, 250.0]) } else { (2, [10.0, 150.0, 250.0]) };
                labels.set(r, c, lab);
                rgb.data[(r * w + c) * 3..(r * w + c) * 3 + 3].copy_from_slice(&col);
            }
        }
        let fill = |lab: i32, qx: &[f64], _qy: &[f64]| -> Vec<[f64; 4]> {
            let v = if lab == 1 { [20.0, 100.0, 250.0, 255.0] } else { [10.0, 150.0, 250.0, 255.0] };
            vec![v; qx.len()]
        };
        let local = LocalFills::build(&labels, &rgb, &fill);
        let at_edge = local.at(&fill, 1, &[9.5], &[10.5])[0];
        assert!((at_edge[1] - 120.0).abs() < 3.0, "{at_edge:?}");
        let right = local.at(&fill, 2, &[10.5], &[10.5])[0];
        for (got, want) in right.iter().zip([10.0, 150.0, 250.0, 255.0]) {
            assert!((got - want).abs() < 1e-6, "{right:?}");
        }
    }

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

#[cfg(test)]
mod under_tests {
    //! Twins of the Python `_under` / `Boundary.segments` tests in
    //! `tests/test_vexel_topology.py`.
    use super::*;
    use std::collections::HashSet;

    fn arc(pair: (i32, i32), pts: Vec<P>, normal: P, n0: Option<u64>, n1: Option<u64>, segments: Vec<Segment>) -> Arc {
        let n = pts.len();
        Arc {
            pair,
            pts,
            normal: vec![normal; n],
            n0,
            n1,
            segments,
            under: Vec::new(),
            under_into: None,
            under_jog: (false, false),
            t0: None,
            t1: None,
            tip0: false,
            tip1: false,
            trim0: NODE_TRIM,
            trim1: NODE_TRIM,
            sliver: None,
            mirror: None,
        }
    }

    fn straight(y: f64) -> Arc {
        let pts: Vec<P> = (0..21).map(|k| [k as f64, y]).collect();
        let seg = Segment::Line { p0: pts[0], p1: pts[20] };
        arc((1, 2), pts, [0.0, 1.0], Some(0), Some(1), vec![seg])
    }

    fn points(segs: &[Segment]) -> Vec<P> {
        segs.iter().flat_map(|s| [s.start(), s.end()]).collect()
    }

    fn params() -> CurveParams {
        CurveParams { corner_threshold: 60.0, tol: 0.4, shape_fitting: true, snap_axis_deg: 1.5, kind_tol: crate::curves::KIND_TOL }
    }

    #[test]
    fn the_bled_copy_stops_halfway_across_a_thin_later_shape() {
        let a = straight(10.0);
        let wall: Vec<P> = (0..25).map(|k| [k as f64 - 2.0, 10.5]).collect();
        let (segs, jog) = under(&a, 1.0, &params(), &[&wall]);
        let top = points(&segs).iter().map(|p| p[1]).fold(f64::NEG_INFINITY, f64::max);
        assert_eq!(jog, (true, true));
        assert!(top <= 10.4, "the copy crossed the far side: {top:.2}");
        assert!(top >= 10.2, "the copy did not bleed at all: {top:.2}");
    }

    #[test]
    fn the_bled_copy_does_not_reach_where_a_shapes_edges_have_crossed() {
        let a = straight(10.0);
        let behind: Vec<P> = (0..9).map(|k| [k as f64 + 6.0, 9.7]).collect();
        let (segs, _) = under(&a, 1.0, &params(), &[&behind]);
        let (probe, _) = sample(&segs, 0.25);
        let mid: Vec<&P> = probe.iter().filter(|p| p[0] > 7.5 && p[0] < 13.5).collect();
        let top = mid.iter().map(|p| p[1]).fold(f64::NEG_INFINITY, f64::max);
        assert!(!mid.is_empty() && top <= 10.3, "the copy reached into a crossed sliver: {top:.2}");
    }

    #[test]
    fn one_side_for_the_whole_arc() {
        // a stair step's own lattice step is square to the curve; the arc votes
        let mut a = straight(10.0);
        a.normal[5] = [1.0, 0.0];
        a.normal[6] = [-1.0, 0.0];
        assert_eq!(side(&a), 1.0);
        let b = arc((1, 2), a.pts.clone(), [0.0, -1.0], Some(0), Some(1), a.segments.clone());
        assert_eq!(side(&b), -1.0);
    }

    /// Twin of `test_a_bled_copy_does_not_turn_on_the_last_bit_of_a_smooth_join`:
    /// the silverpeak badge's arc (26, 138), whose copy this engine split 2 px
    /// from the Python's while each smooth join was handed to the fit as two
    /// vertices a rounding error apart.
    #[test]
    fn a_bled_copy_does_not_turn_on_the_last_bit_of_a_smooth_join() {
        let c = |p0: P, c1: P, c2: P, p1: P| Segment::Cubic { p0, c1, c2, p1 };
        let segs = vec![
            c([600.6582867801136, 381.58124894983234], [599.3829822291585, 383.34094833479594], [597.8606221538046, 386.2687495606855], [595.5, 386.92069397275225]),
            c([595.5, 386.92069397275225], [593.8630926138252, 387.3727665849522], [592.1336850978171, 386.68693044693777], [590.5, 386.980469877491]),
            c([590.5, 386.980469877491], [589.410881879614, 387.17616188281715], [587.2672882785189, 388.4513126638197], [587.0, 388.5]),
            c([587.0, 388.5], [586.7744474487237, 388.5410850522558], [586.656932260966, 387.8605751794979], [586.5, 388.02771045517073]),
            c([586.5, 388.02771045517073], [585.4884668520069, 389.10500884175764], [584.9976496271961, 390.5978658107586], [584.0035466733675, 391.6912689570962]),
        ];
        let mut pts: Vec<P> = segs.iter().map(|s| s.start()).collect();
        pts.push(segs[segs.len() - 1].end());
        let a = arc((26, 138), pts, [-0.6, -0.8], Some(0), Some(1), segs);
        let loose = CurveParams { tol: 0.6, kind_tol: f64::INFINITY, ..params() };
        let (copy, jog) = under(&a, 1.0, &loose, &[]);
        assert_eq!(jog, (true, true));
        let kinds: String = copy.iter().map(|s| match s { Segment::Line { .. } => 'L', Segment::Cubic { .. } => 'C', Segment::Arc { .. } => 'A' }).collect();
        assert_eq!(kinds, "LCCCCCL");
        let want: [P; 7] = [[599.849, 380.994], [595.729, 385.769], [590.61, 385.953], [587.483, 387.159], [585.771, 387.343], [583.264, 391.019], [584.004, 391.691]];
        for (s, w) in copy.iter().zip(want) {
            let e = s.end();
            assert!((e[0] - w[0]).abs() < 2e-3 && (e[1] - w[1]).abs() < 2e-3, "copy ends at {e:?}, the Python's at {w:?}");
        }
    }

    #[test]
    fn ring_bridges_an_arc_with_no_segments() {
        // three arcs around label 1; the middle one is too short to carry both
        // of its nodes and was fitted to nothing: the ring still closes
        let (a, b, c) = ([0.0, 0.0], [4.0, 0.0], [4.0, 1.6]);
        let arcs = vec![
            arc((1, 2), vec![a, b], [0.0, 1.0], Some(0), Some(1), vec![Segment::Line { p0: a, p1: b }]),
            arc((1, 3), vec![b, c], [1.0, 0.0], Some(1), Some(2), Vec::new()),
            arc((1, 4), vec![c, a], [0.0, 1.0], Some(2), Some(0), vec![Segment::Line { p0: c, p1: a }]),
        ];
        let bnd = Boundary::assembled(arcs, Grid::from_vec(3, 3, vec![0; 9]), None);
        let member: HashSet<i32> = [1].into_iter().collect();
        let segs = bnd.segments(&[(0, false), (1, false), (2, false)], Some(&member));
        assert_eq!(segs.len(), 3);
        for w in segs.windows(2) {
            assert!(dist(w[0].end(), w[1].start()) < 1e-9, "the ring jumps");
        }
    }

    #[test]
    fn copies_meeting_at_an_all_later_node_are_joined() {
        // label 1 (painted first) meets 2 and 3 (both later) at the lattice node
        // between pixels (1,1),(1,2),(2,1),(2,2) of a 4x4 padded map
        let lat_cols = 5u64;
        let node = 2 * lat_cols + 2;
        let padded = Grid::from_vec(4, 4, vec![0, 0, 0, 0, 0, 1, 2, 0, 0, 3, 2, 0, 0, 0, 0, 0]);
        let x = [2.0, 2.0];
        let jog_a = [2.0, 1.0];
        let jog_b = [1.0, 2.0];
        let mut first = arc((1, 2), vec![[2.0, 0.0], x], [1.0, 0.0], Some(9), Some(node), vec![Segment::Line { p0: [2.0, 0.0], p1: x }]);
        first.under = vec![Segment::Line { p0: [2.0, 0.0], p1: [2.5, 0.5] }, Segment::Line { p0: [2.5, 0.5], p1: jog_a }, Segment::Line { p0: jog_a, p1: x }];
        first.under_into = Some(2);
        first.under_jog = (true, true);
        let mut second = arc((1, 3), vec![x, [0.0, 2.0]], [0.0, 1.0], Some(node), Some(9), vec![Segment::Line { p0: x, p1: [0.0, 2.0] }]);
        second.under = vec![Segment::Line { p0: x, p1: jog_b }, Segment::Line { p0: jog_b, p1: [0.5, 2.5] }, Segment::Line { p0: [0.5, 2.5], p1: [0.0, 2.0] }];
        second.under_into = Some(3);
        second.under_jog = (true, true);
        let rank: HashMap<i32, usize> = [(1, 0), (2, 1), (3, 2)].into_iter().collect();
        let bnd = Boundary::assembled(vec![first, second], padded, Some(rank));
        let member: HashSet<i32> = [1].into_iter().collect();
        let segs = bnd.segments(&[(0, false), (1, false)], Some(&member));
        // the out-jog of the first and the in-jog of the second became one line
        // from the first copy straight to the second, past the node
        assert!(segs.iter().all(|s| dist(s.end(), x) > 1e-9 && dist(s.start(), x) > 1e-9), "a copy still jogs back to the node");
        assert!(segs.iter().any(|s| dist(s.start(), jog_a) < 1e-9 && dist(s.end(), jog_b) < 1e-9));
    }
}
