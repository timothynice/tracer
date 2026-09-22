//! Vexel's pipeline: RGBA in, SVG out.

use crate::boundary::{contours, coverage_field, polygon_area, thin_coverage};
use crate::core::grid::{Grid, Image, Mask};
use crate::core::labels::{self, LabelIndex, Labels};
use crate::core::morphology::dilate_cross;
use crate::curves::{fit_shape, shape_svg, CurveParams, Shape};
use crate::fills::{fit_fill, Fill, FitParams};
use crate::merge::{adjacency, merge_regions, MergeParams};
use crate::order::{enclosure, paint_order, shape_labels, shape_mask, Enclosure};
use crate::topology::{self, Boundary};
use crate::overlaps::decompose_overlaps;
use crate::partition::{discontinuity, initial_labels};
use crate::posterize::posterize_regions;
use crate::prepare::{prepare, Prepared};
use crate::refine::refine_merge;
use crate::rescue::rescue_features;
use crate::shadows::{detect_shadows, shadow_filter_svg, ShadowPlan};
use crate::strokes::{is_thin_at, stroke_fidelity, stroke_geometry, stroke_svg};
use crate::timing::Timer;
use crate::weights::{interior_weights, interior_weights_at};
use rayon::prelude::*;
use std::collections::{BTreeSet, HashMap, HashSet};

const SVG_NS: &str = "xmlns=\"http://www.w3.org/2000/svg\"";

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

struct FitOut {
    fills: HashMap<i32, Fill>,
    visible: HashMap<i32, bool>,
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
    let results: Vec<(i32, Fill, bool)> = ids
        .par_iter()
        .map(|lab| {
            let px = index.pixels(*lab);
            // Boundary pixels are anti-aliasing mixtures: weight by distance
            // into the region so the fill (and the visibility test) is driven
            // by pure pixels.
            let w = interior_weights_at(l, *lab, px);
            let x: Vec<f64> = px.iter().map(|i| xs.data[*i as usize]).collect();
            let y: Vec<f64> = px.iter().map(|i| ys.data[*i as usize]).collect();
            let c: Vec<[f64; 4]> = px.iter().map(|i| rgba255[*i as usize]).collect();
            let fill = fit_fill(&x, &y, &c, fit_params, Some(&w));
            let mut num = 0.0;
            let mut den = 0.0;
            for (k, i) in px.iter().enumerate() {
                num += alpha.data[*i as usize] * w[k];
                den += w[k];
            }
            (*lab, fill, num / den.max(1e-12) > 0.04)
        })
        .collect();
    let mut fills = HashMap::new();
    let mut visible = HashMap::new();
    for (lab, f, v) in results {
        fills.insert(lab, f);
        visible.insert(lab, v);
    }
    FitOut { fills, visible }
}

/// Union-find over thin regions that touch (within one pixel) and have similar
/// ink colour. Returns groups of labels.
#[allow(clippy::too_many_arguments)]
fn group_thin(
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
    let mut l = merge_regions(
        &labels0,
        &prep.features,
        MergeParams { detail: p.detail, gradients: p.gradients, edge_veto: 0.6 },
        Some(&grad),
    );
    t.lap("merge_regions");
    if !p.gradients {
        l = posterize_regions(&l, &prep.features, p.detail, p.min_region, &grad);
    }

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
            [q[0], q[1], q[2], prep.alpha.data[i] * 255.0]
        })
        .collect();

    let fit_params = FitParams {
        gradients: p.gradients,
        max_stops: p.max_stops,
        tol: (p.detail / 2.0).max(2.0),
    };

    let mut index = LabelIndex::build(&l);
    let mut ids = labels::unique_ids(&l);
    let out = fit_regions(&ids, &l, &index, &xs, &ys, &rgba255, &prep.alpha, &fit_params);
    let mut fills = out.fills;
    let mut visible = out.visible;
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
    }

    // Rescue thin strokes / small details swallowed by a neighbour: pixels far
    // from any boundary whose colour disagrees with their region's fill. The
    // residual is normalised per region by its own fit error, so a smooth region
    // a gradient model fits imperfectly is not shredded into fragments.
    let mut residual = Grid::<f64>::new(height, width);
    let per_region: Vec<(Vec<usize>, Vec<f64>, f64)> = ids
        .par_iter()
        .map(|lab| {
            let idx: Vec<usize> = index.pixels(*lab).iter().map(|i| *i as usize).collect();
            let fill = &fills[lab];
            let r: Vec<f64> = idx
                .iter()
                .map(|i| {
                    let pred = fill.evaluate_one(xs.data[*i], ys.data[*i]);
                    (0..4).map(|c| (rgba255[*i][c] - pred[c]).powi(2)).sum::<f64>().sqrt()
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
            (idx, r, base.max(4.0 * fit_rms))
        })
        .collect();
    for (idx, r, denom) in per_region {
        for (k, i) in idx.iter().enumerate() {
            residual.data[*i] = r[k] / denom;
        }
    }
    t.lap("residual");
    let (rescued_labels, rescued) = rescue_features(&l, &residual, 1.0, p.min_region);
    l = rescued_labels;
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
                (*lab, num / den.max(1e-12) > 0.04)
            })
            .collect();
        for (lab, v) in vis {
            visible.insert(lab, v);
        }
    }

    t.lap("refine_merge");
    let mut enc = enclosure(&l);
    let mut order = paint_order(&enc);
    t.lap("enclosure");
    let stacked = p.layering == "stacked";
    let curve_params = CurveParams {
        corner_threshold: p.corner_threshold,
        tol: p.curve_tolerance,
        shape_fitting: p.shape_fitting,
        snap_axis_deg: 1.5,
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
                let w = interior_weights(&m);
                let (x, y, c) = mask_pixels(&m, &xs, &ys, &corrected);
                fills.insert(lab, fit_fill(&x, &y, &c, &fit_params, Some(&w)));
            }
        }
    }

    t.lap("shadows");
    // Thin regions are drawn lines. A single line often arrives as several
    // regions (split at junctions, broken by anti-aliasing gaps), so thin
    // regions that touch and share an ink colour are grouped and stroked together.
    let mut stroke_of: HashMap<i32, String> = HashMap::new();
    let mut skip: HashSet<i32> = shadow_plan.absorbed.clone();
    if p.strokes {
        let fills_snapshot = fills.clone();
        let fill_at = move |lab: i32, qx: &[f64], qy: &[f64]| -> Vec<[f64; 4]> {
            match fills_snapshot.get(&lab) {
                Some(f) => f.evaluate(qx, qy),
                None => vec![[0.0; 4]; qx.len()],
            }
        };
        let mut thin_labels: Vec<i32> = order
            .iter()
            .copied()
            .filter(|lab| !invisible.contains(lab) && is_thin_at(height, width, index.pixels(*lab)))
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
                        // each rim pixel joins the nearest opaque neighbour
                        let dists: Vec<Grid<f64>> = cands
                            .par_iter()
                            .map(|n| crate::core::edt::edt_to_true(&labels::mask_of(&l, *n)))
                            .collect();
                        let assign: Vec<(usize, i32)> = (0..l.len())
                            .filter(|i| m.data[*i])
                            .map(|i| {
                                let mut best = 0usize;
                                for k in 1..dists.len() {
                                    if dists[k].data[i] < dists[best].data[i] {
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
            let Some(stroke) = stroke_geometry(&union, &field) else { continue };
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
            }
        }
    }

    t.lap("strokes");
    // Overlaps: a region whose colour is a blend of two neighbours, and whose
    // union with the top neighbour is a simpler shape, is two overlapping shapes
    // with the top one semi-transparent. The overlap region itself is dropped.
    let mut mask_override: HashMap<i32, Mask> = HashMap::new();
    let mut fill_override: HashMap<i32, Fill> = HashMap::new();
    if p.overlaps && stacked {
        let dec = decompose_overlaps(&l, &fills, &visible, &curve_params, fit_params.tol);
        if !dec.empty() && dec.removed.intersection(&skip).count() == 0 {
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

    t.lap("overlaps");
    let svg = emit(
        &l, &enc, &order, &fills, &invisible, &skip, &stroke_of, &mask_override, &fill_override,
        &shadow_plan, &prep, stacked, &curve_params, p, height, width,
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
        let primitive = fit_shape(&[bnd.polyline(&rings[0])], params);
        if !matches!(primitive, Shape::Path { .. }) {
            return primitive;
        }
    }
    Shape::Path { contours: rings.iter().map(|r| bnd.segments(r, Some(member))).collect() }
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
) -> String {
    let fill_at = |lab: i32, qx: &[f64], qy: &[f64]| -> Vec<[f64; 4]> {
        match fills.get(&lab) {
            Some(f) => f.evaluate(qx, qy),
            None => vec![[0.0; 4]; qx.len()],
        }
    };
    // Colour actually visible at (qx, qy) when a shape's footprint was
    // extended: the fill of whichever original region lies under each pixel.
    let fill_at_visible = |q_lab: i32, qx: &[f64], qy: &[f64]| -> Vec<[f64; 4]> {
        qx.iter()
            .zip(qy.iter())
            .map(|(x, y)| {
                let r = (*y as usize).min(height - 1);
                let c = (*x as usize).min(width - 1);
                let under = l.data[r * width + c];
                match fills.get(&under) {
                    Some(f) => f.evaluate_one(*x, *y),
                    None => fill_at(q_lab, &[*x], &[*y])[0],
                }
            })
            .collect()
    };

    // The boundary, once: every edge between two regions is placed sub-pixel and
    // fitted a single time, so the two regions that share it are handed the same
    // curve and cannot leave a hairline between them.
    let rank: HashMap<i32, usize> = order.iter().enumerate().map(|(i, lab)| (*lab, i)).collect();
    let bnd = topology::build(
        l,
        &prep.rgb,
        &prep.alpha,
        &fill_at,
        curve_params,
        if stacked { Some(&rank) } else { None },
    );

    let mut defs: Vec<String> = Vec::new();
    let mut elements: Vec<String> = Vec::new();
    for (i, lab) in order.iter().enumerate() {
        if invisible.contains(lab) {
            continue; // transparent canvas or hole: nothing to paint
        }
        if let Some(s) = stroke_of.get(lab) {
            elements.push(s.clone());
        }
        if skip.contains(lab) {
            continue;
        }
        let fill = fill_override.get(lab).or_else(|| fills.get(lab)).unwrap();
        let mut extra = String::new();
        if let Some(shadow) = shadow_plan.shadows.get(lab) {
            defs.push(shadow_filter_svg(shadow, &format!("s{}", i + 1), p.path_precision));
            extra = format!(" filter=\"url(#s{})\"", i + 1);
        }
        let shape = match mask_override.get(lab) {
            // An overlap-decomposed shape has a footprint of its own, which is
            // not a union of whole regions, so it is still traced on its own.
            Some(mask) => {
                let field = coverage_field(mask, *lab, l, &prep.rgb, &prep.alpha, &fill_at_visible);
                let mut polys = contours(&field, 0.5);
                if polys.is_empty() {
                    continue;
                }
                polys.sort_by(|a, b| polygon_area(b).total_cmp(&polygon_area(a)));
                fit_shape(&polys, curve_params)
            }
            None => {
                let member = shape_labels(*lab, enc, stacked, invisible);
                let mut rings = bnd.rings(&member);
                rings.retain(|r| !r.is_empty());
                if rings.is_empty() {
                    continue;
                }
                rings.sort_by(|a, b| {
                    polygon_area(&bnd.polyline(b)).total_cmp(&polygon_area(&bnd.polyline(a)))
                });
                shape_from_rings(&bnd, &rings, &member, curve_params)
            }
        };
        let (d, attrs) = fill.svg(&format!("g{}", i + 1), p.path_precision);
        if !d.is_empty() {
            defs.push(d);
        }
        elements.push(shape_svg(&shape, &format!("{}{}", attrs, extra), p.path_precision));
    }

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
