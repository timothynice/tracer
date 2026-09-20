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
    let mut nbrs: HashMap<i32, HashSet<i32>> = HashMap::new();
    let mut edge_stats: HashMap<(i32, i32), (f64, f64)> = HashMap::new();
    for ((a, b), cg) in edges.iter() {
        nbrs.entry(*a).or_default().insert(*b);
        nbrs.entry(*b).or_default().insert(*a);
        edge_stats.insert((*a.min(b), *a.max(b)), *cg);
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

    let distance = |st: &stats::Stats, cost: &Vec<f64>, edge_stats: &HashMap<(i32, i32), (f64, f64)>, a: usize, b: usize| -> f64 {
        let union = st.union(a, b);
        let (na, nb) = (st.row(a)[0], st.row(b)[0]);
        let cu = stats::region_cost(&union, n_ch, mu, params.gradients).0;
        let d_best = stats::merge_distance(cu, cost[a], cost[b], na, nb);
        let key = (a.min(b) as i32, a.max(b) as i32);
        let (cnt, gsum) = edge_stats.get(&key).copied().unwrap_or((0.0, 0.0));
        let bg = if cnt > 0.0 { gsum / cnt } else { 0.0 };
        if params.gradients && bg > veto {
            // A real edge runs between them. Two pieces of the same flat colour
            // may still merge; a gradient model must not be allowed to
            // "explain" a hard step across a visible edge.
            let cu_solid = stats::region_cost(&union, n_ch, mu, false).0;
            let ca_solid = stats::region_cost(st.row(a), n_ch, mu, false).0;
            let cb_solid = stats::region_cost(st.row(b), n_ch, mu, false).0;
            let d_solid = stats::merge_distance(cu_solid, ca_solid, cb_solid, na, nb);
            return if d_solid < 0.5 * params.detail { d_solid } else { f64::INFINITY };
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
        let d = distance(&st, &cost, &edge_stats, a, b);
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
                let bc = edge_stats.remove(&((b as i32).min(c), (b as i32).max(c))).unwrap_or((0.0, 0.0));
                let ac = edge_stats.entry(((a as i32).min(c), (a as i32).max(c))).or_insert((0.0, 0.0));
                ac.0 += bc.0;
                ac.1 += bc.1;
            }
        }
        edge_stats.remove(&((a as i32).min(b as i32), (a as i32).max(b as i32)));

        let mut around: Vec<i32> = nbrs.get(&(a as i32)).cloned().unwrap_or_default().into_iter().collect();
        around.sort_unstable();
        for c in around {
            let c = c as usize;
            let d = distance(&st, &cost, &edge_stats, a, c);
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
