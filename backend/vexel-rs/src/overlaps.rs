//! Overlap decomposition: blended regions become overlapping shapes.
//!
//! Where a semi-transparent shape T lies over another shape X, the traced image
//! has an extra region C whose colour is a blend: `C = α·T + (1−α)·X`. A
//! designer drew T and X, not C. Every region C explained this way by a pair of
//! its neighbours is absorbed into *both* shapes: T (painted on top with
//! `fill-opacity α`) and X (beneath). Absorption is transitive. A top shape only
//! keeps its absorptions when the extended outline is *simpler* than the bare
//! region.

use crate::core::contours::{find_contours, pad};
use crate::core::grid::{Grid, Mask};
use crate::core::labels::{self, Labels};
use crate::core::morphology::convex_hull_area;
use crate::curves::{fit_shape, CurveParams, Shape, P};
use crate::fills::Fill;
use crate::merge::adjacency;
use std::collections::{HashMap, HashSet};

#[derive(Default)]
pub struct Decomposition {
    /// extended footprint per shape label
    pub masks: HashMap<i32, Mask>,
    /// top shapes: true colour + opacity
    pub fills: HashMap<i32, Fill>,
    /// blended regions that are no longer drawn
    pub removed: HashSet<i32>,
    /// (top, below) ordering constraints
    pub above: Vec<(i32, i32)>,
}

impl Decomposition {
    pub fn empty(&self) -> bool {
        self.removed.is_empty()
    }
}

fn solidity(mask: &Mask) -> f64 {
    let area = mask.count() as f64;
    if area == 0.0 {
        return 0.0;
    }
    area / convex_hull_area(mask)
}

/// The union outline is a primitive, or clearly more convex than the part.
fn simpler(union: &Mask, part: &Mask, curve_params: &CurveParams) -> bool {
    let field = Grid { h: union.h, w: union.w, data: union.data.iter().map(|b| *b as u8 as f64).collect() };
    let polys: Vec<Vec<P>> = find_contours(&pad(&field, 0.0), 0.5)
        .into_iter()
        .filter(|c| c.len() >= 8)
        .map(|c| c.iter().map(|(r, cc)| [cc - 0.5, r - 0.5]).collect())
        .collect();
    if polys.len() == 1 && !matches!(fit_shape(&polys, curve_params), Shape::Path { .. }) {
        return true;
    }
    solidity(union) > solidity(part) + 0.06
}

fn dot3(a: &[f64], b: &[f64]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn dist3(a: &[f64], b: &[f64]) -> f64 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt()
}

/// Solve `C = α·Tc + (1−α)·X` for (α, Tc, rms residual); colours rgba 0–255.
fn blend(t_vis: &[f64; 4], c_vis: &[f64; 4], x_vis: &[f64; 4], bg: Option<&[f64; 4]>) -> Option<(f64, [f64; 3], f64)> {
    let (alpha, tc): (f64, [f64; 3]);
    if t_vis[3] < 250.0 {
        // semi-transparent over transparency: alpha is the opacity
        let a = t_vis[3] / 255.0;
        if !(0.12..=0.95).contains(&a) {
            return None; // an almost-opaque shape does not produce a visible blend
        }
        alpha = a;
        tc = [t_vis[0], t_vis[1], t_vis[2]];
    } else {
        let bg = bg?;
        let d1 = [t_vis[0] - c_vis[0], t_vis[1] - c_vis[1], t_vis[2] - c_vis[2]];
        let d2 = [bg[0] - x_vis[0], bg[1] - x_vis[1], bg[2] - x_vis[2]];
        let den = dot3(&d2, &d2);
        if den < 1e-6 {
            return None;
        }
        let one_minus = dot3(&d1, &d2) / den;
        let a = 1.0 - one_minus;
        if !(0.12..=0.97).contains(&a) {
            return None;
        }
        let mut t = [0.0f64; 3];
        for i in 0..3 {
            t[i] = (t_vis[i] - one_minus * bg[i]) / a;
        }
        if t.iter().any(|v| *v < -12.0 || *v > 267.0) {
            return None;
        }
        alpha = a;
        tc = [t[0].clamp(0.0, 255.0), t[1].clamp(0.0, 255.0), t[2].clamp(0.0, 255.0)];
    }
    let mut resid = 0.0;
    for i in 0..3 {
        let pred = alpha * tc[i] + (1.0 - alpha) * x_vis[i];
        resid += (pred - c_vis[i]).powi(2);
    }
    let resid = (resid / 3.0).sqrt();
    // The blend must be a *different* colour from both parents; otherwise C is
    // just an anti-aliased sliver of one of them, not an overlap.
    if dist3(c_vis, t_vis) < 6.0 || dist3(c_vis, x_vis) < 6.0 {
        return None;
    }
    Some((alpha, tc, resid))
}

pub fn decompose_overlaps(
    l: &Labels,
    fills: &HashMap<i32, Fill>,
    visible: &HashMap<i32, bool>,
    curve_params: &CurveParams,
    tol: f64,
) -> Decomposition {
    const MAX_REGION_FRAC: f64 = 0.6;
    let mut dec = Decomposition::default();
    let edges = adjacency(l, None);
    let mut nbrs: HashMap<i32, HashSet<i32>> = HashMap::new();
    for (a, b) in edges.keys() {
        nbrs.entry(*a).or_default().insert(*b);
        nbrs.entry(*b).or_default().insert(*a);
    }
    let solid_rgba = |lab: i32| -> Option<[f64; 4]> {
        match fills.get(&lab) {
            Some(Fill::Solid { rgba }) => Some(*rgba),
            _ => None,
        }
    };
    let mut solid_vis: Vec<i32> = fills
        .keys()
        .copied()
        .filter(|lab| visible.get(lab).copied().unwrap_or(false) && solid_rgba(*lab).is_some())
        .collect();
    solid_vis.sort_unstable();
    if solid_vis.len() < 3 {
        return dec;
    }
    let solid_set: HashSet<i32> = solid_vis.iter().copied().collect();

    let counts = labels::bincount(l);
    let areas: HashMap<i32, usize> = solid_vis.iter().map(|lab| (*lab, counts[*lab as usize])).collect();

    let (h, w) = (l.h, l.w);
    let mut border_labels: HashSet<i32> = HashSet::new();
    for c in 0..w {
        border_labels.insert(l.data[c]);
        border_labels.insert(l.data[(h - 1) * w + c]);
    }
    for r in 0..h {
        border_labels.insert(l.data[r * w]);
        border_labels.insert(l.data[r * w + w - 1]);
    }
    let background = solid_vis
        .iter()
        .filter(|lab| border_labels.contains(lab))
        .max_by_key(|lab| areas[lab])
        .copied();
    let bg_colour = background.and_then(|b| {
        let c = solid_rgba(b)?;
        if c[3] > 250.0 {
            Some(c)
        } else {
            None
        }
    });
    let total = l.len();

    // 1. explain each candidate region by the best (top, under) pair of its neighbours
    let mut explained: HashMap<i32, (i32, i32, f64, [f64; 3])> = HashMap::new();
    for c in &solid_vis {
        if Some(*c) == background || areas[c] as f64 > MAX_REGION_FRAC * total as f64 {
            continue;
        }
        let mut cand: Vec<i32> = nbrs
            .get(c)
            .map(|s| s.iter().copied().filter(|n| solid_set.contains(n) && Some(*n) != background).collect())
            .unwrap_or_default();
        cand.sort_unstable();
        let mut best: Option<(f64, i32, i32, f64, [f64; 3])> = None;
        for t in &cand {
            for x in &cand {
                if t == x {
                    continue;
                }
                let Some(res) = blend(&solid_rgba(*t).unwrap(), &solid_rgba(*c).unwrap(), &solid_rgba(*x).unwrap(), bg_colour.as_ref())
                else {
                    continue;
                };
                if res.2 > tol {
                    continue;
                }
                if best.as_ref().is_none_or(|b| res.2 < b.0) {
                    best = Some((res.2, *t, *x, res.0, res.1));
                }
            }
        }
        if let Some((_, t, x, a, tc)) = best {
            explained.insert(*c, (t, x, a, tc));
        }
    }
    if explained.is_empty() {
        return dec;
    }

    // 2. a top shape keeps its absorptions only if the extended outline is simpler
    let mut tops: HashMap<i32, Vec<i32>> = HashMap::new();
    let mut expl_keys: Vec<i32> = explained.keys().copied().collect();
    expl_keys.sort_unstable();
    for c in &expl_keys {
        tops.entry(explained[c].0).or_default().push(*c);
    }
    let mut accepted: HashMap<i32, (i32, i32, f64, [f64; 3])> = HashMap::new();
    let mut top_keys: Vec<i32> = tops.keys().copied().collect();
    top_keys.sort_unstable();
    for t in &top_keys {
        let cs = &tops[t];
        let t_mask = labels::mask_of(l, *t);
        let mut union = t_mask.clone();
        for i in 0..union.len() {
            if cs.contains(&l.data[i]) {
                union.data[i] = true;
            }
        }
        if simpler(&union, &t_mask, curve_params) {
            for c in cs {
                accepted.insert(*c, explained[c]);
            }
        }
    }
    if accepted.is_empty() {
        return dec;
    }

    // 3. footprints: T ∪ C for tops, X ∪ C beneath, transitively through removed regions
    let mut members: HashMap<i32, HashSet<i32>> = HashMap::new();
    fn add(
        accepted: &HashMap<i32, (i32, i32, f64, [f64; 3])>,
        members: &mut HashMap<i32, HashSet<i32>>,
        shape: i32,
        region: i32,
        depth: usize,
    ) {
        if let Some((t, x, _, _)) = accepted.get(&shape) {
            if depth < 8 {
                // the shape is itself a removed overlap: pass it to its owners
                add(accepted, members, *t, region, depth + 1);
                add(accepted, members, *x, region, depth + 1);
                return;
            }
        }
        members.entry(shape).or_default().insert(region);
    }

    let mut acc_keys: Vec<i32> = accepted.keys().copied().collect();
    acc_keys.sort_unstable();
    for c in &acc_keys {
        let (t, x, _, _) = accepted[c];
        add(&accepted, &mut members, t, *c, 0);
        add(&accepted, &mut members, x, *c, 0);
        dec.removed.insert(*c);
    }

    /// Follow a removed region to the shape that owns it (0 = top, 1 = under); cycle-safe.
    fn resolve(accepted: &HashMap<i32, (i32, i32, f64, [f64; 3])>, mut shape: i32, index: usize) -> i32 {
        let mut seen: HashSet<i32> = HashSet::new();
        seen.insert(shape);
        while let Some(e) = accepted.get(&shape) {
            shape = if index == 0 { e.0 } else { e.1 };
            if !seen.insert(shape) {
                break;
            }
        }
        shape
    }

    for c in &acc_keys {
        let (t, x, alpha, tc) = accepted[c];
        if !accepted.contains_key(&t) {
            dec.fills.insert(t, Fill::Solid { rgba: [tc[0], tc[1], tc[2], alpha * 255.0] });
        }
        let top_owner = resolve(&accepted, t, 0);
        let under = resolve(&accepted, x, 1);
        if top_owner != under && !accepted.contains_key(&top_owner) && !accepted.contains_key(&under) {
            dec.above.push((top_owner, under));
        }
    }

    let mut shape_keys: Vec<i32> = members.keys().copied().collect();
    shape_keys.sort_unstable();
    for shape in shape_keys {
        if dec.removed.contains(&shape) {
            continue;
        }
        let regs = &members[&shape];
        let mask = Grid {
            h,
            w,
            data: l.data.iter().map(|v| *v == shape || regs.contains(v)).collect(),
        };
        dec.masks.insert(shape, mask);
    }
    dec
}
