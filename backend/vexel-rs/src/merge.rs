//! Stage 3: model-aware greedy region merging on the region adjacency graph.

use crate::core::grid::{Grid, Image};
use crate::core::labels::{self, Labels};
use crate::stats;
use std::collections::{BinaryHeap, HashMap, HashSet};

#[derive(Clone, Copy)]
pub struct MergeParams {
    pub detail: f64,
    pub gradients: bool,
    /// A shared boundary whose mean gradient exceeds `edge_veto · detail` is a
    /// real edge: never merge across it, however well a gradient model would
    /// "explain" the union.
    pub edge_veto: f64,
}

impl MergeParams {
    /// Per-pixel penalty unit for model parameters: a planar fit must cut MSE
    /// by 2·mu, a quadratic by 5·mu, to be preferred.
    pub fn mu(&self) -> f64 {
        (self.detail / 2.0).powi(2) / 4.0
    }
}

/// `{(a, b): [boundary_pixels, boundary_gradient_sum]}` for a < b, 4-connected.
pub fn adjacency(labels: &Labels, grad: Option<&Grid<f64>>) -> HashMap<(i32, i32), (f64, f64)> {
    let (h, w) = (labels.h, labels.w);
    let mut out: HashMap<(i32, i32), (f64, f64)> = HashMap::new();
    let bump = |a: i32, b: i32, g: f64, out: &mut HashMap<(i32, i32), (f64, f64)>| {
        let key = if a < b { (a, b) } else { (b, a) };
        let e = out.entry(key).or_insert((0.0, 0.0));
        e.0 += 1.0;
        e.1 += g;
    };
    for r in 0..h {
        for c in 0..w {
            let i = r * w + c;
            if c + 1 < w && labels.data[i] != labels.data[i + 1] {
                let g = grad.map_or(0.0, |g| 0.5 * (g.data[i] + g.data[i + 1]));
                bump(labels.data[i], labels.data[i + 1], g, &mut out);
            }
            if r + 1 < h && labels.data[i] != labels.data[i + w] {
                let g = grad.map_or(0.0, |g| 0.5 * (g.data[i] + g.data[i + w]));
                bump(labels.data[i], labels.data[i + w], g, &mut out);
            }
        }
    }
    out
}

/// `{(a, b): share of the boundary's pixel pairs on which the discontinuity
/// is a ridge}` for a < b: the partition's test of a step (`rejoin_ramps`,
/// `NECK_RIDGE` over `NECK_REACH`) — the pair's discontinuity at least
/// NECK_RIDGE times the *lower* of the two NECK_REACH pixels to either side
/// along its axis, held inside the image (a small region's far sample is its
/// other edge: see the Python). Horizontal pairs then vertical, raster order,
/// as the Python sums them.
pub fn boundary_ridges(labels: &Labels, grad: &Grid<f64>) -> HashMap<(i32, i32), f64> {
    use crate::partition::{NECK_REACH, NECK_RIDGE};
    let (h, w) = (labels.h, labels.w);
    let mut acc: HashMap<(i32, i32), (f64, f64)> = HashMap::new();
    let mut visit = |a: i32, b: i32, g: f64, before: f64, after: f64| {
        if a != b {
            let e = acc.entry((a.min(b), a.max(b))).or_insert((0.0, 0.0));
            e.0 += 1.0;
            if g >= NECK_RIDGE * before.min(after) {
                e.1 += 1.0;
            }
        }
    };
    for r in 0..h {
        for c in 0..w.saturating_sub(1) {
            let i = r * w + c;
            let before = grad.data[r * w + c.saturating_sub(NECK_REACH)];
            let after = grad.data[r * w + (c + 1 + NECK_REACH).min(w - 1)];
            visit(labels.data[i], labels.data[i + 1], 0.5 * (grad.data[i] + grad.data[i + 1]), before, after);
        }
    }
    for r in 0..h.saturating_sub(1) {
        for c in 0..w {
            let i = r * w + c;
            let before = grad.data[r.saturating_sub(NECK_REACH) * w + c];
            let after = grad.data[(r + 1 + NECK_REACH).min(h - 1) * w + c];
            visit(labels.data[i], labels.data[i + w], 0.5 * (grad.data[i] + grad.data[i + w]), before, after);
        }
    }
    acc.into_iter().map(|(k, (n, r))| (k, r / n)).collect()
}

/// A heap entry ordered exactly like the Python tuple
/// `(distance, version[a], version[b], a, b)` under `heapq`.
#[derive(PartialEq)]
struct Entry {
    d: f64,
    va: i64,
    vb: i64,
    a: usize,
    b: usize,
}

impl Eq for Entry {}

impl Ord for Entry {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        // reversed: `BinaryHeap` is a max-heap and we want the smallest first
        other
            .d
            .total_cmp(&self.d)
            .then(other.va.cmp(&self.va))
            .then(other.vb.cmp(&self.vb))
            .then(other.a.cmp(&self.a))
            .then(other.b.cmp(&self.b))
    }
}

impl PartialOrd for Entry {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

/// A crisp edge is a real boundary however small its step (`merge.py` says
/// why and how the constants were chosen): the boundary's mean discontinuity
/// is at least `EDGE_PROMINENCE` times the median discontinuity inside either
/// region, at least `EDGE_FLOOR`, and a ridge at more than half its pairs.
/// Across such an edge only colours within `EDGE_SAME` merge, as solids.
pub const EDGE_PROMINENCE: f64 = 8.0;
pub const EDGE_FLOOR: f64 = 0.75;
pub const EDGE_SAME: f64 = 2.0;

/// `{(a, b): boundary pairs that are a ridge}` for a < b (`merge.ridge_pairs`):
/// the pair's discontinuity is `NECK_RIDGE` times the higher of the pixels
/// `NECK_REACH` before and after it along its axis, indices held in the image.
pub fn ridge_pairs(labels: &Labels, grad: &Grid<f64>) -> HashMap<(i32, i32), f64> {
    use crate::partition::{NECK_REACH, NECK_RIDGE};
    let (h, w) = (labels.h, labels.w);
    let mut out: HashMap<(i32, i32), f64> = HashMap::new();
    let mut bump = |a: i32, b: i32, hit: bool| {
        let key = if a < b { (a, b) } else { (b, a) };
        let e = out.entry(key).or_insert(0.0);
        if hit {
            *e += 1.0;
        }
    };
    for r in 0..h {
        for c in 0..w.saturating_sub(1) {
            let i = r * w + c;
            if labels.data[i] != labels.data[i + 1] {
                let centre = 0.5 * (grad.data[i] + grad.data[i + 1]);
                let before = grad.data[r * w + c.saturating_sub(NECK_REACH)];
                let after = grad.data[r * w + (c + 1 + NECK_REACH).min(w - 1)];
                bump(labels.data[i], labels.data[i + 1], centre >= NECK_RIDGE * before.max(after));
            }
        }
    }
    for r in 0..h.saturating_sub(1) {
        for c in 0..w {
            let i = r * w + c;
            if labels.data[i] != labels.data[i + w] {
                let centre = 0.5 * (grad.data[i] + grad.data[i + w]);
                let before = grad.data[r.saturating_sub(NECK_REACH) * w + c];
                let after = grad.data[(r + 1 + NECK_REACH).min(h - 1) * w + c];
                bump(labels.data[i], labels.data[i + w], centre >= NECK_RIDGE * before.max(after));
            }
        }
    }
    out
}

/// Per label, the lower-middle median discontinuity over its pixels more than
/// a pixel from any label change, over its whole self if it has none
/// (`merge.interior_floor`).
pub fn interior_floor(labels: &Labels, grad: &Grid<f64>) -> Vec<f64> {
    let k = labels.data.iter().copied().max().unwrap_or(0) as usize + 1;
    let band = crate::order::boundary_band(labels);
    let mut inside: Vec<Vec<f64>> = vec![Vec::new(); k];
    let mut all: Vec<Vec<f64>> = vec![Vec::new(); k];
    for i in 0..labels.data.len() {
        let l = labels.data[i] as usize;
        all[l].push(grad.data[i]);
        if !band.data[i] {
            inside[l].push(grad.data[i]);
        }
    }
    let mut out = vec![0.0f64; k];
    for l in 0..k {
        let v = if inside[l].is_empty() { &mut all[l] } else { &mut inside[l] };
        if v.is_empty() {
            continue;
        }
        v.sort_by(|a, b| a.total_cmp(b));
        out[l] = v[(v.len() - 1) / 2];
    }
    out
}

/// Greedy merging by `stats::merge_distance` until no pair is below
/// `params.detail`. Returns compact labels 1..K'.
pub fn merge_regions(labels: &Labels, features: &Image, params: MergeParams, grad: Option<&Grid<f64>>) -> Labels {
    let (h, w) = (labels.h, labels.w);
    let (xn, yn) = stats::normalised_coords(h, w);
    let n_ch = features.c;
    let mut st = stats::accumulate(labels, &xn, &yn, features);
    let k = st.k();
    let mu = params.mu();

    let mut cost: Vec<f64> = (0..k).map(|i| stats::region_cost(st.row(i), n_ch, mu, params.gradients).0).collect();

    let edges = adjacency(labels, grad);
    let ridges = match grad {
        Some(g) => ridge_pairs(labels, g),
        None => HashMap::new(),
    };
    let mut floor: Vec<f64> = match grad {
        Some(g) => interior_floor(labels, g),
        None => vec![0.0; k],
    };
    floor.resize(k, 0.0);
    let mut nbrs: HashMap<i32, HashSet<i32>> = HashMap::new();
    // per pair: boundary pairs, sum of the boundary discontinuity, ridge pairs
    let mut edge_stats: HashMap<(i32, i32), (f64, f64, f64)> = HashMap::new();
    for ((a, b), cg) in edges.iter() {
        nbrs.entry(*a).or_default().insert(*b);
        nbrs.entry(*b).or_default().insert(*a);
        let key = (*a.min(b), *a.max(b));
        edge_stats.insert(key, (cg.0, cg.1, ridges.get(&key).copied().unwrap_or(0.0)));
    }

    let veto = if grad.is_some() { params.edge_veto * params.detail } else { f64::INFINITY };

    let mut parent: Vec<usize> = (0..k).collect();
    let mut version = vec![0i64; k];
    let mut alive = vec![true; k];
    alive[0] = false;

    fn find(parent: &mut [usize], mut i: usize) -> usize {
        while parent[i] != i {
            parent[i] = parent[parent[i]];
            i = parent[i];
        }
        i
    }

    let distance = |st: &stats::Stats, cost: &Vec<f64>, edge_stats: &HashMap<(i32, i32), (f64, f64, f64)>, floor: &Vec<f64>, a: usize, b: usize| -> f64 {
        let union = st.union(a, b);
        let (na, nb) = (st.row(a)[0], st.row(b)[0]);
        let cu = stats::region_cost(&union, n_ch, mu, params.gradients).0;
        let d_best = stats::merge_distance(cu, cost[a], cost[b], na, nb);
        let key = (a.min(b) as i32, a.max(b) as i32);
        let (cnt, gsum, ridge) = edge_stats.get(&key).copied().unwrap_or((0.0, 0.0, 0.0));
        let bg = if cnt > 0.0 { gsum / cnt } else { 0.0 };
        let strong = bg > veto;
        let prominent = cnt > 0.0 && bg >= EDGE_FLOOR && bg >= EDGE_PROMINENCE * floor[a].max(floor[b]) && 2.0 * ridge > cnt;
        if params.gradients && (strong || prominent) {
            // A real edge runs between them. Two pieces of the same flat colour
            // may still merge; a gradient model must not be allowed to
            // "explain" a hard step across a visible edge. Across an edge that
            // is only prominent, `EDGE_SAME` at the most.
            let cu_solid = stats::region_cost(&union, n_ch, mu, false).0;
            let ca_solid = stats::region_cost(st.row(a), n_ch, mu, false).0;
            let cb_solid = stats::region_cost(st.row(b), n_ch, mu, false).0;
            let d_solid = stats::merge_distance(cu_solid, ca_solid, cb_solid, na, nb);
            let limit = if strong { 0.5 * params.detail } else { (0.5 * params.detail).min(EDGE_SAME) };
            return if d_solid < limit { d_solid } else { f64::INFINITY };
        }
        d_best
    };

    let mut heap: BinaryHeap<Entry> = BinaryHeap::new();
    let mut keys: Vec<(i32, i32)> = edges.keys().copied().collect();
    // the Python iterates the dict in insertion order; the heap imposes a total
    // order on distinct entries, so any deterministic push order gives the same
    // pops — sorting keeps this reproducible across HashMap layouts
    keys.sort_unstable();
    for (a, b) in keys {
        let (a, b) = (a as usize, b as usize);
        let d = distance(&st, &cost, &edge_stats, &floor, a, b);
        if d.is_finite() {
            heap.push(Entry { d, va: version[a], vb: version[b], a, b });
        }
    }

    while let Some(e) = heap.pop() {
        if e.d >= params.detail {
            break;
        }
        let (a, b) = (e.a, e.b);
        if !(alive[a] && alive[b]) || version[a] != e.va || version[b] != e.vb {
            continue;
        }
        st.add_into(a, b);
        cost[a] = stats::region_cost(st.row(a), n_ch, mu, params.gradients).0;
        floor[a] = floor[a].max(floor[b]);
        alive[b] = false;
        parent[b] = a;
        version[a] += 1;

        let moved: Vec<i32> = nbrs.remove(&(b as i32)).unwrap_or_default().into_iter().collect();
        for c in moved {
            if let Some(set) = nbrs.get_mut(&c) {
                set.remove(&(b as i32));
            }
            if c as usize != a {
                nbrs.entry(a as i32).or_default().insert(c);
                nbrs.entry(c).or_default().insert(a as i32);
                let bc = edge_stats.remove(&((b as i32).min(c), (b as i32).max(c))).unwrap_or((0.0, 0.0, 0.0));
                let ac = edge_stats.entry(((a as i32).min(c), (a as i32).max(c))).or_insert((0.0, 0.0, 0.0));
                ac.0 += bc.0;
                ac.1 += bc.1;
                ac.2 += bc.2;
            }
        }
        edge_stats.remove(&((a as i32).min(b as i32), (a as i32).max(b as i32)));

        let mut around: Vec<i32> = nbrs.get(&(a as i32)).cloned().unwrap_or_default().into_iter().collect();
        around.sort_unstable();
        for c in around {
            let c = c as usize;
            let d = distance(&st, &cost, &edge_stats, &floor, a, c);
            if d.is_finite() {
                heap.push(Entry { d, va: version[a], vb: version[c], a, b: c });
            }
        }
    }

    let roots: Vec<i32> = (0..k).map(|i| find(&mut parent, i) as i32).collect();
    let merged = Grid {
        h,
        w,
        data: labels.data.iter().map(|l| roots[*l as usize]).collect(),
    };
    labels::relabel_sequential(&merged).0
}
