//! Vexel's pipeline: RGBA in, SVG out.

use crate::boundary::{polygon_area, thin_coverage};
use crate::core::grid::{Grid, Image, Mask};
use crate::core::labels::{self, LabelIndex, Labels};
use crate::core::morphology::dilate_cross;
use crate::curves::{fit_shape, CurveParams, Shape};
use crate::fills::{fit_fill, Fill, FitParams, INVISIBLE_ALPHA};
use crate::merge::{adjacency, merge_regions, MergeParams};
use crate::order::{enclosure, paint_order, shape_labels, shape_mask, Enclosure};
use crate::topology::{self, Boundary};
use crate::overlaps::decompose_overlaps;
use crate::partition::{discontinuity, initial_labels};
use crate::posterize::{posterize_fills, Levels};
use crate::prepare::{prepare, Prepared};
use crate::refine::refine_merge;
use crate::rescue::rescue_features;
use crate::shadows::{detect_shadows, shadow_filter_svg, ShadowPlan};
use crate::strokes::{is_thin_at, stroke_fidelity, stroke_geometry, stroke_svg};
use crate::timing::Timer;
use crate::weights::{interior, interior_at, interior_weights_at};
use rayon::prelude::*;
use std::collections::{BTreeSet, HashMap, HashSet};

const SVG_NS: &str = "xmlns=\"http://www.w3.org/2000/svg\"";
/// Further `refine_merge` passes before a ramp is posterised (see the Python).
const POSTERIZE_JOIN_ROUNDS: usize = 3;
/// The detail whose fit tolerance a posterised trace finds its ramps at: see
/// the Python `POSTERIZE_FIT_DETAIL` for why it sits between 6 and 14.
const POSTERIZE_FIT_DETAIL: f64 = 8.0;

#[derive(Clone)]
pub struct VexelParams {
    pub detail: f64,
    pub min_region: usize,
    pub gradients: bool,
    pub max_stops: usize,
    pub layering: String,
    pub corner_threshold: f64,
    pub curve_tolerance: f64,
    pub shape_fitting: bool,
    pub refine: bool,
    pub upsample: String,
    pub strokes: bool,
    pub shadows: bool,
    pub stroke_tolerance: f64,
    pub overlaps: bool,
    pub path_precision: usize,
}

impl Default for VexelParams {
    fn default() -> Self {
        VexelParams {
            detail: 6.0,
            min_region: 6,
            gradients: true,
            max_stops: 4,
            layering: "stacked".into(),
            corner_threshold: 60.0,
            curve_tolerance: 0.4,
            shape_fitting: true,
            refine: false,
            upsample: "auto".to_string(),
            strokes: true,
            shadows: true,
            stroke_tolerance: 0.2,
            overlaps: true,
            path_precision: 2,
        }
    }
}

/// 8-connected neighbour pairs (a < b) among `of_interest`, in one pass.
fn touching_pairs(l: &Labels, of_interest: &HashSet<i32>) -> HashSet<(i32, i32)> {
    let (h, w) = (l.h, l.w);
    let mut out = HashSet::new();
    for (dy, dx) in [(0isize, 1isize), (1, 0), (1, 1), (1, -1)] {
        for r in 0..h {
            let r2 = r as isize + dy;
            if r2 < 0 || r2 >= h as isize {
                continue;
            }
            for c in 0..w {
                let c2 = c as isize + dx;
                if c2 < 0 || c2 >= w as isize {
                    continue;
                }
                let a = l.data[r * w + c];
                let b = l.data[r2 as usize * w + c2 as usize];
                if a != b && of_interest.contains(&a) && of_interest.contains(&b) {
                    out.insert((a.min(b), a.max(b)));
                }
            }
        }
    }
    out
}

fn mask_pixels(mask: &Mask, xs: &Grid<f64>, ys: &Grid<f64>, rgba: &[[f64; 4]]) -> (Vec<f64>, Vec<f64>, Vec<[f64; 4]>) {
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

/// Hand every pixel of `rim` to the nearest of `cands`. See the Python `split_rim`.
///
/// A rim is a pixel or two wide, so exact distance ties are the rule: the rim
/// pixel's own colour breaks them (the fill it is closer to is the region it is
/// mostly made of), and the lower label breaks what is left, so the answer
/// never rests on the order the candidates came in.
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
fn split_rim(
    l: &mut Labels,
    rim: &Mask,
    cands: &[i32],
    xs: &Grid<f64>,
    ys: &Grid<f64>,
    rgba255: &[[f64; 4]],
    fill_at: &dyn Fn(i32, &[f64], &[f64]) -> Vec<[f64; 4]>,
) {
    let mut cands: Vec<i32> = cands.to_vec();
    cands.sort_unstable();
    let dists: Vec<Grid<f64>> = cands
        .par_iter()
        .map(|n| crate::core::edt::edt_to_true(&labels::mask_of(l, *n)))
        .collect();
    let assign: Vec<(usize, i32)> = (0..l.len())
        .filter(|i| rim.data[*i])
        .map(|i| {
            let nearest = dists.iter().map(|d| d.data[i]).fold(f64::INFINITY, f64::min);
            let mut best = 0usize;
            let mut best_off = f64::INFINITY;
            for (k, n) in cands.iter().enumerate() {
                if dists[k].data[i] > nearest + 1e-9 {
                    continue;
                }
                let f = fill_at(*n, &[xs.data[i]], &[ys.data[i]])[0];
                let off = (0..3).map(|c| (rgba255[i][c] - f[c]).powi(2)).sum::<f64>().sqrt();
                if off < best_off {
                    best_off = off;
                    best = k;
                }
            }
            (i, cands[best])
        })
        .collect();
    for (i, v) in assign {
        l.data[i] = v;
    }
}

struct FitOut {
    fills: HashMap<i32, Fill>,
    visible: HashMap<i32, bool>,
    /// Every region's fill core (`weights::fill_core`), for the rescue.
    core: Mask,
}

#[allow(clippy::too_many_arguments)]
fn fit_regions(
    ids: &[i32],
    l: &Labels,
    index: &LabelIndex,
    xs: &Grid<f64>,
    ys: &Grid<f64>,
    rgba255: &[[f64; 4]],
    alpha: &Grid<f64>,
    fit_params: &FitParams,
) -> FitOut {
    let results: Vec<(i32, Fill, bool, Vec<bool>)> = ids
        .par_iter()
        .map(|lab| {
            let px = index.pixels(*lab);
            // Boundary pixels are anti-aliasing mixtures: weight by distance
            // into the region so the fill (and the visibility test) is driven
            // by pure pixels.
            let (w, core) = interior_at(l, *lab, px);
            let x: Vec<f64> = px.iter().map(|i| xs.data[*i as usize]).collect();
            let y: Vec<f64> = px.iter().map(|i| ys.data[*i as usize]).collect();
            let c: Vec<[f64; 4]> = px.iter().map(|i| rgba255[*i as usize]).collect();
            let fill = fit_fill(&x, &y, &c, fit_params, Some(&w), Some(&core));
            let mut num = 0.0;
            let mut den = 0.0;
            for (k, i) in px.iter().enumerate() {
                num += alpha.data[*i as usize] * w[k];
                den += w[k];
            }
            (*lab, fill, num / den.max(1e-12) > INVISIBLE_ALPHA, core)
        })
        .collect();
    let mut fills = HashMap::new();
    let mut visible = HashMap::new();
    let mut core_map = Grid::filled(l.h, l.w, false);
    for (lab, f, v, core) in results {
        for (k, i) in index.pixels(lab).iter().enumerate() {
            core_map.data[*i as usize] = core[k];
        }
        fills.insert(lab, f);
        visible.insert(lab, v);
    }
    FitOut { fills, visible, core: core_map }
}

/// Union-find over thin regions that touch (within one pixel) and have similar
/// ink colour. Returns groups of labels.
#[allow(clippy::too_many_arguments)]
pub(crate) fn group_thin(
    thin_labels: &[i32],
    l: &Labels,
    rgb: &Image,
    alpha: &Grid<f64>,
    fill_at: crate::boundary::FillAt,
    colour_tol: f64,
) -> Vec<Vec<i32>> {
    if thin_labels.is_empty() {
        return Vec::new();
    }
    let mut inks: HashMap<i32, [f64; 3]> = HashMap::new();
    for lab in thin_labels {
        let m = labels::mask_of(l, *lab);
        let field = thin_coverage(&m, l, rgb, alpha, fill_at);
        let mut num = [0.0f64; 3];
        let mut den = 0.0;
        for i in 0..m.len() {
            if m.data[i] {
                let w = field.data[i].max(1e-3).powi(2);
                let p = rgb.px(i);
                for c in 0..3 {
                    num[c] += p[c] * w;
                }
                den += w;
            }
        }
        inks.insert(*lab, [num[0] / den, num[1] / den, num[2] / den]);
    }
    let mut parent: HashMap<i32, i32> = thin_labels.iter().map(|l| (*l, *l)).collect();
    fn find(parent: &mut HashMap<i32, i32>, mut x: i32) -> i32 {
        while parent[&x] != x {
            let g = parent[&parent[&x]];
            parent.insert(x, g);
            x = g;
        }
        x
    }
    let touching = touching_pairs(l, &thin_labels.iter().copied().collect());
    // Same order the pairwise scan used, so the union-find roots — and every
    // ordering decision downstream of them — are unchanged.
    for (i, a) in thin_labels.iter().enumerate() {
        for b in &thin_labels[i + 1..] {
            let (ia, ib) = (inks[a], inks[b]);
            let d = ((ia[0] - ib[0]).powi(2) + (ia[1] - ib[1]).powi(2) + (ia[2] - ib[2]).powi(2)).sqrt();
            if d > colour_tol {
                continue;
            }
            if touching.contains(&(*a.min(b), *a.max(b))) {
                let (ra, rb) = (find(&mut parent, *a), find(&mut parent, *b));
                parent.insert(ra, rb);
            }
        }
    }
    let mut groups: HashMap<i32, Vec<i32>> = HashMap::new();
    let mut order: Vec<i32> = Vec::new();
    for lab in thin_labels {
        let r = find(&mut parent, *lab);
        if !groups.contains_key(&r) {
            order.push(r);
        }
        groups.entry(r).or_default().push(*lab);
    }
    order.into_iter().map(|r| groups.remove(&r).unwrap()).collect()
}

pub fn trace_rgba(rgba: &[u8], height: usize, width: usize, p: &VexelParams) -> String {
    let mut t = Timer::new();
    let prep: Prepared = prepare(rgba, height, width);
    t.lap("prepare");
    let grad = discontinuity(&prep.features, 0.7);
    t.lap("discontinuity");
    let labels0 = initial_labels(&grad, &prep.features, p.min_region, 1.5);
    t.lap("initial_labels");
    // With gradients off the trace still finds and fits every ramp as one
    // region, and `posterize` cuts the fitted ramps into flat bands below.
    let mut l = merge_regions(
        &labels0,
        &prep.features,
        MergeParams { detail: p.detail, gradients: true, edge_veto: 0.6 },
        Some(&grad),
    );
    t.lap("merge_regions");
    crate::dump::labels("labels_merge", &l);

    let mut xs = Grid::<f64>::new(height, width);
    let mut ys = Grid::<f64>::new(height, width);
    for r in 0..height {
        for c in 0..width {
            xs.data[r * width + c] = c as f64 + 0.5;
            ys.data[r * width + c] = r as f64 + 0.5;
        }
    }
    let rgba255: Vec<[f64; 4]> = (0..height * width)
        .map(|i| {
            let q = prep.rgb.px(i);
            [q[0], q[1], q[2], crate::prepare::alpha255(prep.alpha.data[i])]
        })
        .collect();

    // With gradients off the fills are fitted at POSTERIZE_FIT_DETAIL's
    // tolerance, so a ramp the bands should show is found as a ramp (see the Python).
    let fit_detail = if p.gradients { p.detail } else { p.detail.min(POSTERIZE_FIT_DETAIL) };
    let mut fit_params = FitParams {
        gradients: true,
        max_stops: p.max_stops,
        tol: (fit_detail / 2.0).max(2.0),
    };

    let mut index = LabelIndex::build(&l);
    let mut ids = labels::unique_ids(&l);
    let out = fit_regions(&ids, &l, &index, &xs, &ys, &rgba255, &prep.alpha, &fit_params);
    let mut fills = out.fills;
    let mut visible = out.visible;
    let mut core_map = out.core;
    t.lap("fit_regions");

    // Transparency is one region. The inpainting under alpha = 0 leaves colour
    // seams that fragment the background into many invisible pieces; fold them
    // into a single label so ordering, adjacency and the rescue residual see
    // one transparent field.
    let clear: Vec<i32> = ids.iter().copied().filter(|i| !visible[i]).collect();
    if clear.len() > 1 {
        let keep = clear[0];
        let drop: HashSet<i32> = clear[1..].iter().copied().collect();
        for v in l.data.iter_mut() {
            if drop.contains(v) {
                *v = keep;
            }
        }
        l = labels::relabel_sequential(&l).0;
        index = LabelIndex::build(&l);
        ids = labels::unique_ids(&l);
        let out = fit_regions(&ids, &l, &index, &xs, &ys, &rgba255, &prep.alpha, &fit_params);
        fills = out.fills;
        visible = out.visible;
        core_map = out.core;
    }

    // Rescue thin strokes / small details swallowed by a neighbour: pixels far
    // from any boundary whose colour disagrees with their region's fill. The
    // residual is normalised per region by its own fit error, so a smooth region
    // a gradient model fits imperfectly is not shredded into fragments.
    let mut residual = Grid::<f64>::new(height, width);
    let mut pred: Vec<[f64; 4]> = vec![[0.0; 4]; height * width];
    let per_region: Vec<(Vec<usize>, Vec<[f64; 4]>, Vec<f64>, f64)> = ids
        .par_iter()
        .map(|lab| {
            let idx: Vec<usize> = index.pixels(*lab).iter().map(|i| *i as usize).collect();
            let fill = &fills[lab];
            // A pixel's colour counts in proportion to how much of it shows:
            // under a transparent pixel the colour is inpainted and means
            // nothing (see the Python).
            let at: Vec<[f64; 4]> = idx.iter().map(|i| fill.evaluate_one(xs.data[*i], ys.data[*i])).collect();
            let r: Vec<f64> = idx
                .iter()
                .zip(at.iter())
                .map(|(i, pred)| {
                    let cover = prep.alpha.data[*i];
                    let colour: f64 = (0..3).map(|c| (rgba255[*i][c] - pred[c]).powi(2)).sum();
                    (cover * cover * colour + (rgba255[*i][3] - pred[3]).powi(2)).sqrt()
                })
                .collect();
            let base = 7.5 * p.detail;
            // the swallowed feature itself must not inflate the scale
            let inliers: Vec<f64> = r.iter().copied().filter(|v| *v < base).collect();
            let fit_rms = if inliers.is_empty() {
                0.0
            } else {
                (inliers.iter().map(|v| v * v).sum::<f64>() / inliers.len() as f64).sqrt()
            };
            (idx, at, r, base.max(4.0 * fit_rms))
        })
        .collect();
    for (idx, at, r, denom) in per_region {
        for (k, i) in idx.iter().enumerate() {
            residual.data[*i] = r[k] / denom;
            pred[*i] = at[k];
        }
    }
    // Near an edge, a pixel the edge's anti-aliasing or ringing explains is
    // not part of a feature and does not count towards promoting one.
    let over = Grid { h: height, w: width, data: residual.data.iter().map(|v| *v > 1.0).collect() };
    let explained = crate::rescue::edge_mix(&l, &rgba255, &pred, &prep.alpha, &over);
    t.lap("residual");
    crate::dump::labels("labels_clear", &l);
    let (rescued_labels, rescued) = rescue_features(&l, &residual, 1.0, p.min_region, Some(&explained), Some(&core_map));
    l = rescued_labels;
    crate::dump::labels("labels_rescue", &l);
    if !rescued.is_empty() {
        index = LabelIndex::build(&l);
        ids = labels::unique_ids(&l);
        let out = fit_regions(&ids, &l, &index, &xs, &ys, &rgba255, &prep.alpha, &fit_params);
        fills = out.fills;
        visible = out.visible;
    }

    t.lap("rescue + refit");
    // Join gradient fragments (glows, off-centre radials) that one real fill explains.
    let (refined, refit_fills, changed) = refine_merge(
        &l, &xs, &ys, &rgba255, &grad, fills, &fit_params, 0.6 * p.detail, 60,
    );
    l = refined;
    fills = refit_fills;
    if !p.gradients {
        // Posterised, a region boundary through one smooth field is a visible
        // step, so the ramps are joined as far as one fill explains them.
        let mut again = changed;
        for _ in 0..POSTERIZE_JOIN_ROUNDS {
            if !again {
                break;
            }
            let (refined, refit_fills, more) = refine_merge(
                &l, &xs, &ys, &rgba255, &grad, fills, &fit_params, 0.6 * p.detail, 60,
            );
            l = refined;
            fills = refit_fills;
            again = more;
        }
    }
    if changed {
        index = LabelIndex::build(&l);
        ids = labels::unique_ids(&l);
        visible.clear();
        let vis: Vec<(i32, bool)> = ids
            .par_iter()
            .map(|lab| {
                let px = index.pixels(*lab);
                let w = interior_weights_at(&l, *lab, px);
                let mut num = 0.0;
                let mut den = 0.0;
                for (k, i) in px.iter().enumerate() {
                    num += prep.alpha.data[*i as usize] * w[k];
                    den += w[k];
                }
                (*lab, num / den.max(1e-12) > INVISIBLE_ALPHA)
            })
            .collect();
        for (lab, v) in vis {
            visible.insert(lab, v);
        }
    }

    t.lap("refine_merge");
    crate::dump::labels("labels_refine", &l);
    // Gradients off: every fitted ramp is cut into flat bands along its own
    // level lines, and the band edges are placed on those lines (`posterize`).
    let mut levels = Levels::default();
    if !p.gradients {
        let (banded, band_fills, band_visible, lv) =
            posterize_fills(&l, &fills, &visible, &xs, &ys, &rgba255, &prep.features, p.detail, p.min_region);
        l = banded;
        fills = band_fills;
        visible = band_visible;
        levels = lv;
        crate::dump::labels("labels_posterize", &l);
        index = LabelIndex::build(&l);
        ids = labels::unique_ids(&l);
        fit_params.gradients = false;
    }
    let mut enc = enclosure(&l);
    let mut order = paint_order(&enc);
    t.lap("enclosure");
    let stacked = p.layering == "stacked";
    let curve_params = CurveParams {
        corner_threshold: p.corner_threshold,
        tol: p.curve_tolerance,
        shape_fitting: p.shape_fitting,
        snap_axis_deg: 1.5,
        kind_tol: crate::curves::KIND_TOL,
    };

    let mut invisible: HashSet<i32> = ids
        .iter()
        .copied()
        .filter(|lab| !visible[lab] || fills[lab].alpha_is_invisible())
        .collect();

    // Soft shadows are blurred copies of a shape, not colour fields. Where the
    // blur model explains a band group better than the bands do, the bands are
    // dropped and the caster carries an SVG filter instead.
    let mut shadow_plan = ShadowPlan::default();
    if p.shadows {
        let sil = |lab: i32| shape_mask(&l, &index, lab, &enc, stacked, &invisible);
        shadow_plan = detect_shadows(
            &l, &fills, &visible, &sil, &prep, &xs, &ys, p.min_region, true,
        );
        if let Some(corrected) = shadow_plan.corrected.clone() {
            // The backdrop's fill was partly modelling the shadow's faint outer
            // reach; refit it against colours with the shadow taken back out.
            let mut refit: Vec<i32> = shadow_plan.refit.iter().copied().collect();
            refit.sort_unstable();
            for lab in refit {
                let m = labels::mask_of(&l, lab);
                let (w, core) = interior(&m);
                let (x, y, c) = mask_pixels(&m, &xs, &ys, &corrected);
                fills.insert(lab, fit_fill(&x, &y, &c, &fit_params, Some(&w), Some(&core)));
            }
        }
        if let (Some(canvas), false) = (shadow_plan.canvas, shadow_plan.absorbed.is_empty()) {
            // On a transparent canvas the bands a filter explains are canvas
            // with the shadow drawn over it: they join it, and the caster's
            // edge there is placed against the canvas like the rest of its
            // outline. See the Python.
            let gone: HashSet<i32> = std::mem::take(&mut shadow_plan.absorbed);
            for v in l.data.iter_mut() {
                if gone.contains(v) {
                    *v = canvas;
                }
            }
            for lab in &gone {
                fills.remove(lab);
                visible.remove(lab);
                order.retain(|x| x != lab);
                invisible.remove(lab);
            }
            index = LabelIndex::build(&l);
            ids = labels::unique_ids(&l);
            enc = enclosure(&l);
        }
    }

    t.lap("shadows");
    // Thin regions are drawn lines. A single line often arrives as several
    // regions (split at junctions, broken by anti-aliasing gaps), so thin
    // regions that touch and share an ink colour are grouped and stroked together.
    let mut stroke_of: HashMap<i32, String> = HashMap::new();
    // label painted by a stroke along its middle -> the stroke's first member
    let mut stroked: HashMap<i32, i32> = HashMap::new();
    let mut skip: HashSet<i32> = shadow_plan.absorbed.clone();
    if p.strokes {
        let fills_snapshot = fills.clone();
        let levels_snapshot = levels.clone();
        // A band's outline is anti-aliased against the ramp it was cut from.
        let fill_at = move |lab: i32, qx: &[f64], qy: &[f64]| -> Vec<[f64; 4]> {
            match levels_snapshot.model(lab).or_else(|| fills_snapshot.get(&lab)) {
                Some(f) => f.evaluate(qx, qy),
                None => vec![[0.0; 4]; qx.len()],
            }
        };
        // A band of a posterised ramp is as thin as the ramp is steep: never a line.
        let mut thin_labels: Vec<i32> = order
            .iter()
            .copied()
            .filter(|lab| !invisible.contains(lab) && !levels.band.contains_key(lab) && is_thin_at(height, width, index.pixels(*lab)))
            .collect();

        // A thin region that matches the colour of an adjacent large region is
        // that region's anti-aliased rim, not a line: fold it in so its
        // neighbour's coverage contour handles it, instead of stroking a
        // hairline around it.
        if !thin_labels.is_empty() {
            let nbr_edges = adjacency(&l, None);
            let mut neighbours: HashMap<i32, BTreeSet<i32>> = HashMap::new();
            for (a, b) in nbr_edges.keys() {
                neighbours.entry(*a).or_default().insert(*b);
                neighbours.entry(*b).or_default().insert(*a);
            }
            let thin_set: HashSet<i32> = thin_labels.iter().copied().collect();
            let mut absorbed: Vec<(i32, Vec<i32>)> = Vec::new();
            for t in &thin_labels {
                let m = labels::mask_of(&l, *t);
                let field = thin_coverage(&m, &l, &prep.rgb, &prep.alpha, &fill_at);
                let mut num = [0.0f64; 3];
                let mut den = 0.0;
                let (mut cx, mut cy) = (0.0f64, 0.0f64);
                let mut asum = 0.0;
                let mut an = 0usize;
                for i in 0..m.len() {
                    if !m.data[i] {
                        continue;
                    }
                    let w = field.data[i].max(1e-3).powi(2);
                    let q = prep.rgb.px(i);
                    for c in 0..3 {
                        num[c] += q[c] * w;
                    }
                    den += w;
                    cx += xs.data[i] * w;
                    cy += ys.data[i] * w;
                    asum += prep.alpha.data[i];
                    an += 1;
                }
                let ink = [num[0] / den, num[1] / den, num[2] / den];
                let mean_alpha = asum / an as f64;
                let (cx, cy) = (cx / den, cy / den);
                let mut opaque_nbrs: Vec<i32> = Vec::new();
                let mut colour_match = false;
                for n in neighbours.get(t).cloned().unwrap_or_default() {
                    if thin_set.contains(&n) || invisible.contains(&n) {
                        continue;
                    }
                    let Some(f) = fills.get(&n) else { continue };
                    let nf = f.evaluate_one(cx, cy);
                    if nf[3] > 128.0 {
                        opaque_nbrs.push(n);
                    }
                    let d = ((ink[0] - nf[0]).powi(2) + (ink[1] - nf[1]).powi(2) + (ink[2] - nf[2]).powi(2)).sqrt();
                    if d < 5.0 * p.detail {
                        colour_match = true;
                    }
                }
                // a rim: same colour as a neighbour, or nearly transparent and
                // hugging opaque shapes
                if !opaque_nbrs.is_empty() && (colour_match || mean_alpha < 0.2) {
                    absorbed.push((*t, opaque_nbrs));
                }
            }
            if !absorbed.is_empty() {
                for (t, cands) in &absorbed {
                    let m = labels::mask_of(&l, *t);
                    if cands.len() == 1 {
                        for i in 0..l.len() {
                            if m.data[i] {
                                l.data[i] = cands[0];
                            }
                        }
                    } else {
                        split_rim(&mut l, &m, cands, &xs, &ys, &rgba255, &fill_at);
                    }
                    fills.remove(t);
                    visible.remove(t);
                    order.retain(|x| x != t);
                    invisible.remove(t);
                }
                let absorbed_set: HashSet<i32> = absorbed.iter().map(|(t, _)| *t).collect();
                thin_labels.retain(|lab| !absorbed_set.contains(lab));
                enc = enclosure(&l);
            }
        }

        let groups = group_thin(&thin_labels, &l, &prep.rgb, &prep.alpha, &fill_at, 30.0);
        crate::dump::text("strokes", &format!("thin={:?} groups={:?}\n", thin_labels, groups));
        let transparent = Grid {
            h: height,
            w: width,
            data: l.data.iter().map(|v| invisible.contains(v)).collect(),
        };
        for members in groups {
            let union = Grid {
                h: height,
                w: width,
                data: l.data.iter().map(|v| members.contains(v)).collect(),
            };
            // grow one pixel into transparent surroundings so the faint outer
            // anti-aliasing of a sub-pixel line counts towards its ink area
            let grown_d = dilate_cross(&union);
            let grown = Grid {
                h: height,
                w: width,
                data: (0..union.len())
                    .map(|i| union.data[i] || (grown_d.data[i] && transparent.data[i]))
                    .collect(),
            };
            let field = thin_coverage(&grown, &l, &prep.rgb, &prep.alpha, &fill_at);
            let Some(stroke) = stroke_geometry(&union, &field) else {
                crate::dump::text("strokes", &format!("group {:?}: no geometry\n", members));
                continue;
            };
            crate::dump::text(
                "strokes",
                &format!(
                    "group {:?}: width={:.6} polylines={:?} closed={:?} fidelity={:.6}\n",
                    members,
                    stroke.width,
                    stroke.polylines.iter().map(|p| p.len()).collect::<Vec<_>>(),
                    stroke.closed,
                    stroke_fidelity(&stroke, &field)
                ),
            );
            // A letterform is thin, elongated and of consistent width — it
            // passes every geometric test for being a stroke, and stroking it
            // mangles its terminals and joins. Only the reconstruction tells
            // them apart: what would a constant-width centreline actually paint?
            if stroke_fidelity(&stroke, &field) > p.stroke_tolerance {
                continue;
            }
            // ink colour from the purest (highest-coverage) pixels; opacity 1
            // because the width already accounts for partial coverage
            let mut num = [0.0f64; 3];
            let mut den = 0.0;
            for i in 0..union.len() {
                if union.data[i] {
                    let w = field.data[i].max(1e-3).powi(2);
                    let q = prep.rgb.px(i);
                    for c in 0..3 {
                        num[c] += q[c] * w;
                    }
                    den += w;
                }
            }
            let colour = crate::fills::hex(&[num[0] / den, num[1] / den, num[2] / den]);
            let el = stroke_svg(&stroke, &colour, 1.0, &curve_params, p.path_precision);
            if !el.is_empty() {
                let first = *members
                    .iter()
                    .min_by_key(|m| order.iter().position(|o| o == *m).unwrap_or(usize::MAX))
                    .unwrap();
                stroke_of.insert(first, el);
                skip.extend(members.iter().copied());
                stroked.extend(members.iter().map(|m| (*m, first)));
            }
        }
    }

    t.lap("strokes");
    // Overlaps: a region whose colour is a blend of two neighbours, and whose
    // union with the top neighbour is a simpler shape, is two overlapping shapes
    // with the top one semi-transparent. The overlap region itself is dropped.
    let mut mask_override: HashMap<i32, Mask> = HashMap::new();
    let mut fill_override: HashMap<i32, Fill> = HashMap::new();
    let mut over_backdrop: HashSet<i32> = HashSet::new();
    if p.overlaps && stacked {
        // The middle band of three is by construction a blend of the other
        // two: bands are never read as overlaps.
        let seen: HashMap<i32, bool> = visible.iter().map(|(k, v)| (*k, *v && !levels.band.contains_key(k))).collect();
        let dec = decompose_overlaps(&l, &fills, &seen, &curve_params, (p.detail / 2.0).max(2.0));
        if !dec.empty() && dec.removed.intersection(&skip).count() == 0 {
            over_backdrop = dec.over_backdrop.clone();
            skip.extend(dec.removed.iter().copied());
            mask_override.extend(dec.masks);
            fill_override.extend(dec.fills);
            // a top shape paints after everything it lies on
            for _ in 0..dec.above.len() {
                let mut moved = false;
                for (top, below) in &dec.above {
                    let (Some(it), Some(ib)) = (
                        order.iter().position(|x| x == top),
                        order.iter().position(|x| x == below),
                    ) else {
                        continue;
                    };
                    if it < ib {
                        order.remove(it);
                        let ib = order.iter().position(|x| x == below).unwrap();
                        order.insert(ib + 1, *top);
                        moved = true;
                    }
                }
                if !moved {
                    break;
                }
            }
        }
    }

    // A stroked region is painted by a line of one width along its middle,
    // which cannot follow the region's own outline; its neighbours stop at that
    // outline and nothing is bled into it, so wherever the line falls short of
    // it the canvas showed through. The earliest neighbour painted before the
    // line fills the region underneath. See the Python.
    let mut underlay: HashMap<i32, BTreeSet<i32>> = HashMap::new();
    if stacked && !stroked.is_empty() {
        let mut nbrs: HashMap<i32, BTreeSet<i32>> = HashMap::new();
        for (a, b) in adjacency(&l, None).keys() {
            nbrs.entry(*a).or_default().insert(*b);
            nbrs.entry(*b).or_default().insert(*a);
        }
        let pos: HashMap<i32, usize> = order.iter().enumerate().map(|(i, lab)| (*lab, i)).collect();
        let mut thin: Vec<i32> = stroked.keys().copied().collect();
        thin.sort_unstable();
        for t in thin {
            let (Some(_), Some(&line)) = (pos.get(&t), pos.get(&stroked[&t])) else { continue };
            let owner = nbrs
                .get(&t)
                .into_iter()
                .flatten()
                .filter(|n| pos.contains_key(n) && !skip.contains(n) && !invisible.contains(n) && pos[n] < line)
                .min_by_key(|n| pos[n]);
            if let Some(owner) = owner {
                underlay.entry(*owner).or_default().insert(t);
            }
        }
    }

    t.lap("overlaps");
    // A small input with thin features is traced again at twice its size; the
    // viewBox carries the scale. See `upsample.rs` / the Python `upsample.py`.
    // A band's edges lie on its ramp's level lines: the regions before the cut
    // are the evidence for the upsample, not a narrow band.
    if p.upsample == "always" || (p.upsample == "auto" && crate::upsample::wants_upsample(&levels.unbanded(&l), height, width)) {
        crate::dump::text("upsample", "2x\n");
        let up = crate::upsample::upsample2x(rgba, height, width);
        let mut q = p.clone();
        q.upsample = "never".to_string();
        return crate::upsample::halve(&trace_rgba(&up, 2 * height, 2 * width, &q), width, height);
    }
    let svg = emit(
        &l, &enc, &order, &fills, &invisible, &skip, &stroke_of, &mask_override, &fill_override,
        &shadow_plan, &prep, stacked, &curve_params, p, height, width, rgba,
        &Painting { stroked: &stroked, underlay: &underlay, over_backdrop: &over_backdrop },
        &levels,
    );
    t.lap("emit");
    t.total("trace");
    svg
}

/// A shape's geometry, assembled from the arcs its rings walk.
///
/// A whole-shape primitive is still tried, but only for a shape that is one
/// closed ring: a circle or a rectangle is a claim about the whole outline, and
/// a shape whose outline is stitched from arcs it shares with several
/// neighbours is not one. The arcs themselves are already fitted, so this reuses
/// them and nothing is described twice.
fn shape_from_rings(
    bnd: &Boundary,
    rings: &[Vec<(usize, bool)>],
    member: &HashSet<i32>,
    params: &CurveParams,
) -> Shape {
    if rings.len() == 1 && params.shape_fitting {
        // a ring `topology::rectify` made a rectangle is one already
        let primitive = bnd.primitive(&rings[0]).unwrap_or_else(|| fit_shape(&[bnd.polyline(&rings[0])], params));
        if !matches!(primitive, Shape::Path { .. }) {
            return primitive;
        }
    }
    Shape::Path { contours: rings.iter().map(|r| bnd.segments(r, Some(member))).collect() }
}

/// A fill no pixel shows through: every colour at full opacity.
fn is_opaque(fill: &Fill) -> bool {
    match fill {
        Fill::Solid { rgba } => rgba[3] >= 250.0,
        Fill::Linear { stops, .. } | Fill::Radial { stops, .. } => !stops.is_empty() && stops.iter().all(|s| s.rgba[3] >= 250.0),
    }
}

/// The labels in every hole of `member` that holds only opaque regions painted
/// after the shape (whose own rank is the lowest of its members): the shape
/// paints on underneath them instead of cutting the hole. See the Python
/// `_holes_to_fill`.
fn holes_to_fill(l: &Labels, member: &HashSet<i32>, rank: &HashMap<i32, usize>, opaque: &HashSet<i32>) -> HashSet<i32> {
    let rank_of = |lab: i32| -> i64 { rank.get(&lab).map_or(-1, |v| *v as i64) };
    let own = member.iter().map(|m| rank_of(*m)).min().unwrap_or(-1);
    let outside = Grid { h: l.h, w: l.w, data: l.data.iter().map(|v| !member.contains(v)).collect() };
    let comp = labels::label_mask(&outside, 2);
    let n = comp.data.iter().copied().max().unwrap_or(0).max(0) as usize;
    let mut border = vec![false; n + 1];
    let (h, w) = (l.h, l.w);
    for c in 0..w {
        border[comp.data[c] as usize] = true;
        border[comp.data[(h - 1) * w + c] as usize] = true;
    }
    for r in 0..h {
        border[comp.data[r * w] as usize] = true;
        border[comp.data[r * w + w - 1] as usize] = true;
    }
    let mut labs: Vec<BTreeSet<i32>> = vec![BTreeSet::new(); n + 1];
    for (i, k) in comp.data.iter().enumerate() {
        if *k > 0 && !border[*k as usize] {
            labs[*k as usize].insert(l.data[i]);
        }
    }
    let mut out = HashSet::new();
    for k in 1..=n {
        if border[k] || labs[k].is_empty() {
            continue;
        }
        if labs[k].iter().all(|v| opaque.contains(v) && rank_of(*v) > own) {
            out.extend(labs[k].iter().copied());
        }
    }
    out
}

/// How the regions no fill of their own paints are painted: a stroked label
/// (-> its stroke's first member), the earlier shape filling each stroked label
/// underneath (owner -> labels), and the overlap tops solved over the backdrop.
struct Painting<'a> {
    stroked: &'a HashMap<i32, i32>,
    underlay: &'a HashMap<i32, BTreeSet<i32>>,
    over_backdrop: &'a HashSet<i32>,
}

/// Bilinear samples of an (h·w) per-pixel grid at image points (qx, qy); pixel
/// (r, c) is centred at (c + 0.5, r + 0.5), and points past the outer centres
/// take the edge value. `engine.sample_bilinear` in the Python.
fn sample_bilinear(grid: &[[f64; 4]], h: usize, w: usize, qx: &[f64], qy: &[f64]) -> Vec<[f64; 4]> {
    qx.iter()
        .zip(qy.iter())
        .map(|(x, y)| {
            let fx = (x - 0.5).clamp(0.0, w as f64 - 1.0);
            let fy = (y - 0.5).clamp(0.0, h as f64 - 1.0);
            let x0 = (fx.floor() as usize).min(w - 1);
            let y0 = (fy.floor() as usize).min(h - 1);
            let x1 = (x0 + 1).min(w - 1);
            let y1 = (y0 + 1).min(h - 1);
            let tx = fx - x0 as f64;
            let ty = fy - y0 as f64;
            let mut out = [0.0; 4];
            for c in 0..4 {
                let top = grid[y0 * w + x0][c] * (1.0 - tx) + grid[y0 * w + x1][c] * tx;
                let bottom = grid[y1 * w + x0][c] * (1.0 - tx) + grid[y1 * w + x1][c] * tx;
                out[c] = top * (1.0 - ty) + bottom * ty;
            }
            out
        })
        .collect()
}

#[allow(clippy::too_many_arguments)]
fn emit(
    l: &Labels,
    enc: &Enclosure,
    order: &[i32],
    fills: &HashMap<i32, Fill>,
    invisible: &HashSet<i32>,
    skip: &HashSet<i32>,
    stroke_of: &HashMap<i32, String>,
    mask_override: &HashMap<i32, Mask>,
    fill_override: &HashMap<i32, Fill>,
    shadow_plan: &ShadowPlan,
    prep: &Prepared,
    stacked: bool,
    curve_params: &CurveParams,
    p: &VexelParams,
    height: usize,
    width: usize,
    src: &[u8],
    painting: &Painting,
    levels: &Levels,
) -> String {
    // A band's outline is anti-aliased against the ramp it was cut from.
    let fill_at = |lab: i32, qx: &[f64], qy: &[f64]| -> Vec<[f64; 4]> {
        match levels.model(lab).or_else(|| fills.get(&lab)) {
            Some(f) => f.evaluate(qx, qy),
            None => vec![[0.0; 4]; qx.len()],
        }
    };

    // The boundary, once: every edge between two regions is placed sub-pixel and
    // fitted a single time, so the two regions that share it are handed the same
    // curve and cannot leave a hairline between them.
    let rank: HashMap<i32, usize> = order.iter().enumerate().map(|(i, lab)| (*lab, i)).collect();
    crate::dump::labels("labels_to_topology", l);
    // Nothing bleeds under paint that does not hide it: a top an overlap made
    // translucent, or a region drawn as a line along its middle.
    let mut see_through: HashSet<i32> = fill_override
        .iter()
        .filter(|(_, f)| matches!(f, Fill::Solid { rgba } if rgba[3] < 250.0))
        .map(|(lab, _)| *lab)
        .collect();
    see_through.extend(painting.stroked.keys().copied());
    let painted_by: HashMap<i32, i32> =
        painting.underlay.iter().flat_map(|(owner, ts)| ts.iter().map(move |t| (*t, *owner))).collect();
    // Under a drop shadow on a transparent canvas, the canvas shows the shadow:
    // an edge there is placed against that (`shadows._detect_clear`).
    let ground = match (shadow_plan.canvas, shadow_plan.ground.as_ref()) {
        (Some(c), Some(g)) => Some((c, g)),
        _ => None,
    };
    let place_at = |lab: i32, qx: &[f64], qy: &[f64]| -> Vec<[f64; 4]> {
        match ground {
            Some((c, g)) if c == lab => sample_bilinear(g, height, width, qx, qy),
            _ => fill_at(lab, qx, qy),
        }
    };
    let mut bnd = topology::build(
        l,
        &prep.rgb,
        &prep.alpha,
        &place_at,
        curve_params,
        if stacked { Some(&rank) } else { None },
        &topology::Underlay { see_through, painted_by },
        Some(levels),
    );
    crate::dump::arcs("arcs", &bnd);

    // Regions a shape may be laid under without being seen through them: an
    // opaque fill; a top an overlap made translucent over the backdrop; and a
    // region painted some other way (a stroke along its middle, a blend an
    // overlap explains, a shadow band). See the Python.
    let mut opaque: HashSet<i32> = order
        .iter()
        .copied()
        .filter(|lab| !invisible.contains(lab) && fill_override.get(lab).or_else(|| fills.get(lab)).is_some_and(is_opaque))
        .collect();
    opaque.extend(painting.over_backdrop.iter().copied());
    opaque.extend(skip.iter().copied().filter(|lab| !invisible.contains(lab)));

    let mut defs: Vec<String> = Vec::new();
    // Either a stroke's finished markup, or a record of a shape as it stands in
    // the graph (fitted once; its path is made from the arcs when written), in
    // paint order. Repeated shapes are written once and used (`reuse`).
    let mut items: Vec<Result<crate::refine_render::Rec, String>> = Vec::new();
    for (i, lab) in order.iter().enumerate() {
        if invisible.contains(lab) {
            continue; // transparent canvas or hole: nothing to paint
        }
        if let Some(s) = stroke_of.get(lab) {
            items.push(Err(s.clone()));
        }
        if skip.contains(lab) {
            continue;
        }
        let fill = fill_override.get(lab).or_else(|| fills.get(lab)).unwrap();
        let mut extra = String::new();
        let mut filtered = false;
        if let Some(shadow) = shadow_plan.shadows.get(lab) {
            defs.push(shadow_filter_svg(shadow, &format!("s{}", i + 1), p.path_precision));
            extra = format!(" filter=\"url(#s{})\"", i + 1);
            filtered = true;
        }
        let (d, attrs) = fill.svg(&format!("g{}", i + 1), p.path_precision);
        let mut member = shape_labels(*lab, enc, stacked, invisible);
        if let Some(ts) = painting.underlay.get(lab) {
            member.extend(ts.iter().copied());
        }
        if let Some(mask) = mask_override.get(lab) {
            // An overlap's shape is the union of its own region and the blends
            // it explains: labels in the one graph, so its outline tiles with
            // every neighbour. See the Python.
            for (i, on) in mask.data.iter().enumerate() {
                if *on {
                    member.insert(l.data[i]);
                }
            }
        }
        let mut rings = bnd.rings(&member);
        rings.retain(|r| !r.is_empty());
        if stacked && rings.len() > 1 {
            // A hole whose every region is painted later, opaquely, is not cut:
            // this shape paints on underneath them. See the Python.
            let filled = holes_to_fill(l, &member, &rank, &opaque);
            if !filled.is_empty() {
                member.extend(filled);
                rings = bnd.rings(&member);
                rings.retain(|r| !r.is_empty());
            }
        }
        if rings.is_empty() {
            continue;
        }
        rings.sort_by(|a, b| polygon_area(&bnd.polyline(b)).total_cmp(&polygon_area(&bnd.polyline(a))));
        let mut primitive = None;
        if rings.len() == 1 && curve_params.shape_fitting {
            // a ring `topology::rectify` made a rectangle is one already; the
            // arcs carry the same outline, so a neighbour's edge agrees with it
            let candidate = bnd.primitive(&rings[0]).unwrap_or_else(|| fit_shape(&[bnd.polyline(&rings[0])], curve_params));
            if !matches!(candidate, Shape::Path { .. }) {
                primitive = Some(candidate);
            }
        }
        let rec = crate::refine_render::Rec { member, primitive, rings, fill: fill.clone(), attrs: format!("{}{}", attrs, extra), filtered };
        if !d.is_empty() {
            defs.push(d);
        }
        items.push(Ok(rec));
    }
    if p.refine {
        // Ask the renderer: the two shapes on either side of each arc, drawn as
        // they stand, against the source pixels along the arc.
        let records: Vec<&crate::refine_render::Rec> = items.iter().filter_map(|it| it.as_ref().ok()).collect();
        let owned: Vec<crate::refine_render::Rec> = records.iter().map(|r| crate::refine_render::Rec { member: r.member.clone(), primitive: r.primitive.clone(), rings: r.rings.clone(), fill: r.fill.clone(), attrs: r.attrs.clone(), filtered: r.filtered }).collect();
        crate::refine_render::refine(&mut bnd, &owned, src, height, width, p.path_precision, 3, 0.1);
    }
    let pending: Vec<Result<(Shape, String), String>> = items
        .iter()
        .map(|it| match it {
            Err(markup) => Err(markup.clone()),
            Ok(rec) => Ok((crate::refine_render::shape_of(&bnd, rec), rec.attrs.clone())),
        })
        .collect();
    let shapes: Vec<(Shape, String)> = pending.iter().filter_map(|it| it.as_ref().ok().cloned()).collect();
    let mut timer = crate::timing::Timer::new();
    let (use_defs, use_elements) = crate::reuse::emit(&shapes, p.path_precision, 1);
    timer.lap("emit: reuse");
    defs.extend(use_defs);
    let mut used = use_elements.into_iter();
    let elements: Vec<String> = pending
        .into_iter()
        .map(|it| match it {
            Err(markup) => markup,
            Ok(_) => used.next().unwrap_or_default(),
        })
        .collect();

    let body = if defs.is_empty() { String::new() } else { format!("<defs>{}</defs>", defs.concat()) };
    format!(
        "<svg {} viewBox=\"0 0 {} {}\">{}{}</svg>",
        SVG_NS,
        width,
        height,
        body,
        elements.concat()
    )
}

#[cfg(test)]
mod rim_tests {
    use super::*;

    /// Two opaque regions, 1 and 2, with a one-pixel rim (label 3) between
    /// them: every rim pixel is exactly one step from each, a distance tie.
    fn scene() -> (Labels, Mask, Grid<f64>, Grid<f64>, Vec<[f64; 4]>) {
        let (h, w) = (8usize, 9usize);
        let mut l = Grid::<i32>::new(h, w);
        let mut rim = Mask::new(h, w);
        let mut xs = Grid::<f64>::new(h, w);
        let mut ys = Grid::<f64>::new(h, w);
        let mut rgba = vec![[0.0; 4]; h * w];
        for r in 0..h {
            for c in 0..w {
                let i = r * w + c;
                l.data[i] = if c < 4 { 1 } else if c == 4 { 3 } else { 2 };
                rim.data[i] = c == 4;
                xs.data[i] = c as f64 + 0.5;
                ys.data[i] = r as f64 + 0.5;
                let g = if c < 4 { 250.0 } else if c > 4 { 30.0 } else if r < 4 { 200.0 } else { 80.0 };
                rgba[i] = [g, g, g, 255.0];
            }
        }
        (l, rim, xs, ys, rgba)
    }

    fn fill_at(lab: i32, qx: &[f64], _qy: &[f64]) -> Vec<[f64; 4]> {
        let g = if lab == 1 { 250.0 } else { 30.0 };
        vec![[g, g, g, 255.0]; qx.len()]
    }

    #[test]
    fn a_distance_tie_goes_to_the_fill_the_pixel_is_closer_to() {
        let (mut l, rim, xs, ys, rgba) = scene();
        split_rim(&mut l, &rim, &[2, 1], &xs, &ys, &rgba, &fill_at);
        for r in 0..8 {
            assert_eq!(l.data[r * 9 + 4], if r < 4 { 1 } else { 2 }, "row {r}");
            assert_eq!(l.data[r * 9], 1);
            assert_eq!(l.data[r * 9 + 8], 2);
        }
    }

    #[test]
    fn a_total_tie_goes_to_the_lower_label_whatever_the_candidate_order() {
        let (mut a, rim, xs, ys, mut rgba) = scene();
        for r in 0..8 {
            rgba[r * 9 + 4] = [140.0, 140.0, 140.0, 255.0];
        }
        let mut b = a.clone();
        split_rim(&mut a, &rim, &[1, 2], &xs, &ys, &rgba, &fill_at);
        split_rim(&mut b, &rim, &[2, 1], &xs, &ys, &rgba, &fill_at);
        assert_eq!(a.data, b.data);
        for r in 0..8 {
            assert_eq!(a.data[r * 9 + 4], 1);
        }
    }
}
