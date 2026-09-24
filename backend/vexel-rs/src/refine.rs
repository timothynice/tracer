//! Refit-merge: join adjacent smooth regions that one real fill explains.
//!
//! The merge stage judges unions with polynomial proxies. A Gaussian glow or an
//! off-centre radial gradient is poorly approximated by a quadratic, so it
//! fragments into rings even though a single multi-stop radial gradient would
//! reproduce it. Here, for every adjacent pair of *smooth* regions with no
//! visible edge between them, the actual fill is fitted to the union; if it is
//! as good as the parts, they are merged.

use crate::core::grid::{Grid, Mask};
use crate::core::labels::{self, Labels};
use crate::fills::{fit_fill, Fill, FitParams};
use crate::merge::adjacency;
use crate::weights::interior;
use std::collections::{HashMap, HashSet};

/// The region's core pixels (`weights::fill_core`) and their weights. A fill is
/// fitted to its core, so it is judged there: scored over every pixel, a small
/// part's own rim — which its fill does not try to explain — inflated its
/// error, and a union merely as bad as a rim-inflated part passed "as good as
/// the parts". `refine.refine_merge` in the Python.
#[allow(clippy::type_complexity)]
fn gather_core(mask: &Mask, xs: &Grid<f64>, ys: &Grid<f64>, rgba: &[[f64; 4]]) -> (Vec<f64>, Vec<f64>, Vec<[f64; 4]>, Vec<f64>, Vec<bool>, Vec<f64>) {
    let (w, core) = interior(mask);
    let (x, y, c) = gather(mask, xs, ys, rgba);
    let wk: Vec<f64> = w.iter().zip(core.iter()).filter(|(_, k)| **k).map(|(a, _)| *a).collect();
    (x, y, c, w, core, wk)
}

fn core_of<T: Copy>(v: &[T], core: &[bool]) -> Vec<T> {
    v.iter().zip(core.iter()).filter(|(_, k)| **k).map(|(a, _)| *a).collect()
}

pub fn fill_rms(fill: &Fill, xs: &[f64], ys: &[f64], rgba255: &[[f64; 4]], w: &[f64]) -> f64 {
    let mut num = 0.0;
    let mut den = 0.0;
    for i in 0..xs.len() {
        let p = fill.evaluate_one(xs[i], ys[i]);
        let mut e = 0.0;
        for c in 0..4 {
            let d = p[c] - rgba255[i][c];
            e += d * d;
        }
        num += w[i] * e;
        den += w[i];
    }
    (num / (den * 4.0).max(1e-12)).sqrt()
}

fn gather(mask: &Mask, xs: &Grid<f64>, ys: &Grid<f64>, rgba: &[[f64; 4]]) -> (Vec<f64>, Vec<f64>, Vec<[f64; 4]>) {
    let mut x = Vec::new();
    let mut y = Vec::new();
    let mut c = Vec::new();
    for i in 0..mask.len() {
        if mask.data[i] {
            x.push(xs.data[i]);
            y.push(ys.data[i]);
            c.push(rgba[i]);
        }
    }
    (x, y, c)
}

/// Returns (labels, fills, changed). Fills of merged regions are refitted.
// The arguments are the pipeline's state at this point; bundling them into a
// struct just to move the list somewhere else would not make the seam clearer.
#[allow(clippy::too_many_arguments)]
pub fn refine_merge(
    l: &Labels,
    xs: &Grid<f64>,
    ys: &Grid<f64>,
    rgba255: &[[f64; 4]],
    grad: &Grid<f64>,
    fills: HashMap<i32, Fill>,
    params: &FitParams,
    edge_limit: f64,
    max_attempts: usize,
) -> (Labels, HashMap<i32, Fill>, bool) {
    if !params.gradients {
        return (l.clone(), fills, false);
    }
    let mut labels_out = l.clone();
    let mut fills = fills;

    // (fill, rms over the core)
    let fit = |mask: &Mask, labels_now: &Labels| -> (Fill, f64) {
        let _ = labels_now;
        let (x, y, c, w, core, wk) = gather_core(mask, xs, ys, rgba255);
        let f = fit_fill(&x, &y, &c, params, Some(&w), Some(&core));
        let r = fill_rms(&f, &core_of(&x, &core), &core_of(&y, &core), &core_of(&c, &core), &wk);
        (f, r)
    };

    let mut rms: HashMap<i32, f64> = HashMap::new();
    let mut smooth: HashSet<i32> = HashSet::new();
    let mut fill_ids: Vec<i32> = fills.keys().copied().collect();
    fill_ids.sort_unstable();
    for lab in &fill_ids {
        let m = labels::mask_of(&labels_out, *lab);
        let (x, y, c, _, core, wk) = gather_core(&m, xs, ys, rgba255);
        let r = fill_rms(&fills[lab], &core_of(&x, &core), &core_of(&y, &core), &core_of(&c, &core), &wk);
        rms.insert(*lab, r);
        if !matches!(fills[lab], Fill::Solid { .. }) || r > params.tol {
            smooth.insert(*lab);
        }
    }
    if smooth.is_empty() {
        return (labels_out, fills, false);
    }

    let edges = adjacency(&labels_out, Some(grad));
    let mut pairs: Vec<(f64, i32, i32)> = edges
        .iter()
        .filter(|((a, b), (cnt, gsum))| {
            (smooth.contains(a) || smooth.contains(b)) && *cnt > 0.0 && gsum / cnt <= edge_limit
        })
        .map(|((a, b), (cnt, _))| (*cnt, *a, *b))
        .collect();
    // longest shared boundary first; the key is negated in the Python, and the
    // tie order is the dict's — sorting on the labels too keeps this stable
    pairs.sort_by(|p, q| q.0.total_cmp(&p.0).then(p.1.cmp(&q.1)).then(p.2.cmp(&q.2)));

    let mut changed = false;
    let mut attempts = 0usize;
    let mut alias: HashMap<i32, i32> = HashMap::new();
    let root = |alias: &HashMap<i32, i32>, mut i: i32| -> i32 {
        while let Some(n) = alias.get(&i) {
            i = *n;
        }
        i
    };

    for (_, a0, b0) in pairs {
        if attempts >= max_attempts {
            break;
        }
        let (a, b) = (root(&alias, a0), root(&alias, b0));
        if a == b {
            continue;
        }
        attempts += 1;
        let union = Grid {
            h: labels_out.h,
            w: labels_out.w,
            data: labels_out.data.iter().map(|v| *v == a || *v == b).collect(),
        };
        let (f_union, r_union) = fit(&union, &labels_out);
        let a_solid = matches!(fills.get(&a), Some(Fill::Solid { .. }));
        let b_solid = matches!(fills.get(&b), Some(Fill::Solid { .. }));
        if matches!(f_union, Fill::Solid { .. }) && !(a_solid && b_solid) {
            continue; // a gradient collapsing to a solid is not "explained"
        }
        let bar = params.tol.max(1.15 * rms[&a].max(rms[&b]));
        if r_union <= bar {
            for v in labels_out.data.iter_mut() {
                if *v == b {
                    *v = a;
                }
            }
            fills.insert(a, f_union);
            rms.insert(a, r_union);
            fills.remove(&b);
            rms.remove(&b);
            alias.insert(b, a);
            changed = true;
        }
    }

    if changed {
        let mut old_ids: Vec<i32> = fills.keys().copied().collect();
        old_ids.sort_unstable();
        let (relabelled, fwd) = labels::relabel_sequential(&labels_out);
        let mut new_fills = HashMap::new();
        for i in old_ids {
            if let Some(n) = fwd.get(&i) {
                new_fills.insert(*n, fills[&i].clone());
            }
        }
        return (relabelled, new_fills, true);
    }
    (labels_out, fills, false)
}
