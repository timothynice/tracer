//! Vexel, in Rust. The Python package keeps the parameter model and the engine
//! seam; everything between RGBA bytes and the SVG string happens here.

// Index arithmetic is the subject here, not an accident of style: a row-major
// grid is addressed as `r * w + c`, and rewriting those loops as iterator
// chains obscures which index is which.
#![allow(clippy::needless_range_loop)]

pub mod boundary;
pub mod core;
pub mod curves;
pub mod dump;
pub mod engine;
pub mod fills;
pub mod merge;
pub mod order;
pub mod overlaps;
pub mod partition;
pub mod posterize;
pub mod prepare;
pub mod rects;
pub mod refine;
pub mod refine_render;
pub mod regularity;
pub mod rescue;
pub mod reuse;
pub mod shadows;
pub mod stats;
pub mod topology;
pub mod timing;
pub mod strokes;
pub mod symmetry;
pub mod upsample;
pub mod weights;


#[cfg(feature = "extension-module")]
mod python {
    //! The CPython binding: RGBA bytes in, SVG string out, plus the stage hooks
    //! `tools/diffcheck.py` uses to compare this implementation against the
    //! Python one.
    use super::*;
    use pyo3::prelude::*;
    use pyo3::types::PyDict;

    /// Intermediates the differential test harness compares against the Python.
    #[pyfunction]
    fn _stage_grad(rgba: Vec<u8>, h: usize, w: usize) -> Vec<f64> {
        let prep = prepare::prepare(&rgba, h, w);
        partition::discontinuity(&prep.features, 0.7).data
    }

    #[pyfunction]
    fn _stage_features(rgba: Vec<u8>, h: usize, w: usize) -> Vec<f64> {
        prepare::prepare(&rgba, h, w).features.data
    }

    #[pyfunction]
    fn _stage_rgb(rgba: Vec<u8>, h: usize, w: usize) -> Vec<f64> {
        prepare::prepare(&rgba, h, w).rgb.data
    }

    /// Fit one region's fill from the raw pixel lists, so the fill stage can be
    /// compared against the Python's in isolation.
    #[pyfunction]
    #[pyo3(signature = (xs, ys, rgba, weights, gradients, max_stops, tol, core=None))]
    #[allow(clippy::too_many_arguments)]
    fn _fit_fill(
        xs: Vec<f64>,
        ys: Vec<f64>,
        rgba: Vec<f64>,
        weights: Vec<f64>,
        gradients: bool,
        max_stops: usize,
        tol: f64,
        core: Option<Vec<bool>>,
    ) -> (String, Vec<f64>) {
        let col: Vec<[f64; 4]> = rgba.chunks(4).map(|c| [c[0], c[1], c[2], c[3]]).collect();
        let params = fills::FitParams { gradients, max_stops, tol };
        let f = fills::fit_fill(&xs, &ys, &col, &params, Some(&weights), core.as_deref());
        let mut out = Vec::new();
        match &f {
            fills::Fill::Solid { rgba } => out.extend_from_slice(rgba),
            fills::Fill::Linear { x1, y1, x2, y2, stops } => {
                out.extend_from_slice(&[*x1, *y1, *x2, *y2]);
                for s in stops {
                    out.push(s.offset);
                    out.extend_from_slice(&s.rgba);
                }
            }
            fills::Fill::Radial { cx, cy, r, stops } => {
                out.extend_from_slice(&[*cx, *cy, *r]);
                for s in stops {
                    out.push(s.offset);
                    out.extend_from_slice(&s.rgba);
                }
            }
        }
        (f.kind().to_string(), out)
    }

    #[pyfunction]
    fn _medial_axis(mask: Vec<u8>, h: usize, w: usize) -> Vec<u8> {
        let m = crate::core::grid::Grid::from_vec(h, w, mask.iter().map(|v| *v != 0).collect());
        crate::core::skeleton::medial_axis(&m).data.iter().map(|b| *b as u8).collect()
    }

    #[pyfunction]
    fn _stroke_fidelity(
        polylines: Vec<Vec<f64>>,
        closed: Vec<bool>,
        width: f64,
        coverage: Vec<f64>,
        h: usize,
        w: usize,
    ) -> f64 {
        let stroke = strokes::Stroke {
            polylines: polylines.iter().map(|p| p.chunks(2).map(|c| [c[0], c[1]]).collect()).collect(),
            closed,
            caps: Vec::new(),
            width,
        };
        strokes::stroke_fidelity(&stroke, &crate::core::grid::Grid::from_vec(h, w, coverage))
    }


    /// `strokes::is_thin` on a frame-sized mask, for the `strokes` stage.
    #[pyfunction]
    fn _is_thin(mask: Vec<u8>, h: usize, w: usize) -> bool {
        let m = crate::core::grid::Grid::from_vec(h, w, mask.iter().map(|v| *v != 0).collect());
        strokes::is_thin(&m)
    }

    /// `strokes::stroke_geometry` on a mask and its coverage field: the
    /// polylines (flattened xy), which are closed, the cap per polyline and
    /// the width, or `None` where the region is not stroked.
    #[pyfunction]
    #[allow(clippy::type_complexity)]
    fn _stroke_geometry(
        mask: Vec<u8>,
        coverage: Vec<f64>,
        h: usize,
        w: usize,
    ) -> Option<(Vec<Vec<f64>>, Vec<bool>, Vec<String>, f64)> {
        let m = crate::core::grid::Grid::from_vec(h, w, mask.iter().map(|v| *v != 0).collect());
        let cov = crate::core::grid::Grid::from_vec(h, w, coverage);
        let st = strokes::stroke_geometry(&m, &cov)?;
        Some((
            st.polylines.iter().map(|p| p.iter().flat_map(|q| [q[0], q[1]]).collect()).collect(),
            st.closed,
            st.caps.iter().map(|c| c.to_string()).collect(),
            st.width,
        ))
    }

    /// `engine::group_thin` over a given label map and list of thin labels,
    /// with the fills fitted the way the engine fits them.
    #[pyfunction]
    fn _group_thin(rgba: Vec<u8>, h: usize, w: usize, labels: Vec<i32>, thin_labels: Vec<i32>) -> Vec<Vec<i32>> {
        use crate::core::grid::Grid;
        let prep = prepare::prepare(&rgba, h, w);
        let labels: Grid<i32> = Grid::from_vec(h, w, labels);
        let fills = _fills_for(&prep, &labels, h, w);
        let fill_at = |lab: i32, qx: &[f64], qy: &[f64]| -> Vec<[f64; 4]> {
            match fills.get(&lab) {
                Some(f) => f.evaluate(qx, qy),
                None => vec![[0.0; 4]; qx.len()],
            }
        };
        engine::group_thin(&thin_labels, &labels, &prep.rgb, &prep.alpha, &fill_at, 30.0)
    }

    /// `topology::fit_arc` on one placed arc, for `tools/diffcheck.py`'s
    /// `segments` stage: the Python's arc state in, this side's fitted segments
    /// out, each as its kind followed by its numbers (`L x0 y0 x1 y1`,
    /// `C p0 c1 c2 p1`, `A p0 p1 r large sweep`).
    #[pyfunction]
    #[pyo3(signature = (pts, closed, t0, t1, trim0, trim1, sliver, mirror, corner_threshold, tol, snap_axis_deg))]
    #[allow(clippy::too_many_arguments, clippy::type_complexity)]
    fn _fit_arc(
        pts: Vec<f64>,
        closed: bool,
        t0: Option<(f64, f64)>,
        t1: Option<(f64, f64)>,
        trim0: f64,
        trim1: f64,
        sliver: Option<Vec<bool>>,
        mirror: Option<(f64, f64, f64, f64)>,
        corner_threshold: f64,
        tol: f64,
        snap_axis_deg: f64,
    ) -> Vec<(String, Vec<f64>)> {
        let pts: Vec<[f64; 2]> = pts.chunks(2).map(|c| [c[0], c[1]]).collect();
        let params = curves::CurveParams { corner_threshold, tol, shape_fitting: true, snap_axis_deg, kind_tol: curves::KIND_TOL };
        let segs = topology::fit_arc(
            &pts,
            closed,
            t0.map(|t| [t.0, t.1]),
            t1.map(|t| [t.0, t.1]),
            (trim0, trim1),
            sliver.as_deref(),
            mirror.map(|m| ([m.0, m.1], [m.2, m.3])),
            &params,
        );
        segs.iter()
            .map(|s| match s {
                curves::Segment::Line { p0, p1 } => ("L".to_string(), vec![p0[0], p0[1], p1[0], p1[1]]),
                curves::Segment::Cubic { p0, c1, c2, p1 } => {
                    ("C".to_string(), vec![p0[0], p0[1], c1[0], c1[1], c2[0], c2[1], p1[0], p1[1]])
                }
                curves::Segment::Arc { p0, p1, r, large, sweep } => {
                    ("A".to_string(), vec![p0[0], p0[1], p1[0], p1[1], *r, *large as u8 as f64, *sweep as u8 as f64])
                }
            })
            .collect()
    }

    fn seg_out(s: &curves::Segment) -> (String, Vec<f64>) {
        match s {
            curves::Segment::Line { p0, p1 } => ("L".to_string(), vec![p0[0], p0[1], p1[0], p1[1]]),
            curves::Segment::Cubic { p0, c1, c2, p1 } => ("C".to_string(), vec![p0[0], p0[1], c1[0], c1[1], c2[0], c2[1], p1[0], p1[1]]),
            curves::Segment::Arc { p0, p1, r, large, sweep } => {
                ("A".to_string(), vec![p0[0], p0[1], p1[0], p1[1], *r, *large as u8 as f64, *sweep as u8 as f64])
            }
        }
    }

    fn seg_in(kind: &str, v: &[f64]) -> curves::Segment {
        match kind {
            "L" => curves::Segment::Line { p0: [v[0], v[1]], p1: [v[2], v[3]] },
            "C" => curves::Segment::Cubic { p0: [v[0], v[1]], c1: [v[2], v[3]], c2: [v[4], v[5]], p1: [v[6], v[7]] },
            _ => curves::Segment::Arc { p0: [v[0], v[1]], p1: [v[2], v[3]], r: v[4], large: v[5] != 0.0, sweep: v[6] != 0.0 },
        }
    }

    /// The bled copies, for `tools/diffcheck.py`'s `under` stage: the Python's
    /// fitted arcs (pair, nodes, placed vertices, per-vertex step, segments), its
    /// paint order and underlay in; `topology::bleed_arcs` then gives every arc
    /// its copy, and `Boundary::segments` walks the given rings. Out: per arc
    /// (under_into or -1, jog in, jog out, the copy's segments), per ring its
    /// segments.
    #[pyfunction]
    #[allow(clippy::too_many_arguments, clippy::type_complexity)]
    fn _stage_under(
        arcs: Vec<(i32, i32, i64, i64, Vec<f64>, Vec<f64>, Vec<(String, Vec<f64>)>)>,
        padded: Vec<i32>,
        ph: usize,
        pw: usize,
        rank: Vec<(i32, usize)>,
        see_through: Vec<i32>,
        painted_by: Vec<(i32, i32)>,
        rings: Vec<(Vec<(usize, bool)>, Vec<i32>)>,
        corner_threshold: f64,
        tol: f64,
        snap_axis_deg: f64,
    ) -> (Vec<(i64, bool, bool, Vec<(String, Vec<f64>)>)>, Vec<Vec<(String, Vec<f64>)>>) {
        use std::collections::{HashMap, HashSet};
        let params = curves::CurveParams { corner_threshold, tol, shape_fitting: true, snap_axis_deg, kind_tol: curves::KIND_TOL };
        let node = |v: i64| if v < 0 { None } else { Some(v as u64) };
        let pairs = |v: &[f64]| -> Vec<[f64; 2]> { v.chunks(2).map(|c| [c[0], c[1]]).collect() };
        let mut list: Vec<topology::Arc> = arcs
            .into_iter()
            .map(|(a, b, n0, n1, pts, normal, segs)| topology::Arc {
                pair: (a, b),
                pts: pairs(&pts),
                normal: pairs(&normal),
                n0: node(n0),
                n1: node(n1),
                segments: segs.iter().map(|(k, v)| seg_in(k, v)).collect(),
                under: Vec::new(),
                under_into: None,
                under_jog: (false, false),
                t0: None,
                t1: None,
                tip0: false,
                tip1: false,
                trim0: topology::NODE_TRIM,
                trim1: topology::NODE_TRIM,
                sliver: None,
                mirror: None,
            })
            .collect();
        let rank: HashMap<i32, usize> = rank.into_iter().collect();
        let see: HashSet<i32> = see_through.into_iter().collect();
        let by: HashMap<i32, i32> = painted_by.into_iter().collect();
        topology::bleed_arcs(&mut list, &params, Some(&rank), topology::BLEED, &see, &by);
        let copies = list
            .iter()
            .map(|a| (a.under_into.map_or(-1, |v| v as i64), a.under_jog.0, a.under_jog.1, a.under.iter().map(seg_out).collect()))
            .collect();
        let bnd = topology::Boundary::assembled(list, crate::core::grid::Grid::from_vec(ph, pw, padded), Some(rank));
        let walked = rings
            .iter()
            .map(|(ring, member)| {
                let m: HashSet<i32> = member.iter().copied().collect();
                bnd.segments(ring, Some(&m)).iter().map(seg_out).collect()
            })
            .collect();
        (copies, walked)
    }

    #[pyfunction]
    fn _lstsq(a: Vec<f64>, rows: usize, cols: usize, b: Vec<f64>, bcols: usize) -> Vec<f64> {
        use crate::core::linalg::Mat;
        let am = Mat { rows, cols, d: a };
        let bm = Mat { rows, cols: bcols, d: b };
        crate::core::linalg::lstsq(&am, &bm).d
    }

    #[pyfunction]
    fn _rng_choice(pop: u64, size: usize) -> Vec<u64> {
        let mut r = crate::core::rng::Pcg64::seed_1234();
        crate::core::rng::choice_without_replacement(&mut r, pop, size)
    }

    #[pyfunction]
    fn _stage_seed(rgba: Vec<u8>, h: usize, w: usize) -> Vec<i32> {
        let prep = prepare::prepare(&rgba, h, w);
        let grad = partition::discontinuity(&prep.features, 0.7);
        partition::seed_mask(&prep.features, &grad, 1.5, 8.0).data.iter().map(|b| *b as i32).collect()
    }

    #[pyfunction]
    fn _stage_ridge(rgba: Vec<u8>, h: usize, w: usize) -> Vec<i32> {
        let prep = prepare::prepare(&rgba, h, w);
        let grad = partition::discontinuity(&prep.features, 0.7);
        let (ridge, valley) = partition::ridges_and_valleys(&prep.features, &grad, 1.5);
        ridge.data.iter().zip(valley.data.iter()).map(|(r, v)| *r as i32 + 2 * (*v as i32)).collect()
    }

    #[pyfunction]
    fn _stage_watershed(rgba: Vec<u8>, h: usize, w: usize, markers: Vec<i32>) -> Vec<i32> {
        use crate::core::grid::Grid;
        let prep = prepare::prepare(&rgba, h, w);
        let grad = partition::discontinuity(&prep.features, 0.7);
        crate::core::watershed::watershed(&grad, &Grid::from_vec(h, w, markers)).data
    }

    #[pyfunction]
    fn _stage_upsample(rgba: Vec<u8>, h: usize, w: usize) -> Vec<u8> {
        crate::upsample::upsample2x(&rgba, h, w)
    }

    #[pyfunction]
    fn _stage_labels0(rgba: Vec<u8>, h: usize, w: usize, min_region: usize) -> Vec<i32> {
        let prep = prepare::prepare(&rgba, h, w);
        let grad = partition::discontinuity(&prep.features, 0.7);
        partition::initial_labels(&grad, &prep.features, min_region, 1.5).data
    }

    /// The fills every stage hook needs, fitted the way `engine` fits them.
    fn _fills_for(
        prep: &prepare::Prepared,
        labels: &crate::core::grid::Grid<i32>,
        h: usize,
        w: usize,
    ) -> std::collections::HashMap<i32, crate::fills::Fill> {
        use crate::core::labels::{mask_of, LabelIndex};
        use crate::fills::{fit_fill, FitParams};
        use crate::weights::interior;
        let rgba255: Vec<[f64; 4]> = (0..h * w)
            .map(|i| {
                let px = prep.rgb.px(i);
                [px[0], px[1], px[2], prep.alpha.data[i] * 255.0]
            })
            .collect();
        let xs: Vec<f64> = (0..h * w).map(|i| (i % w) as f64 + 0.5).collect();
        let ys: Vec<f64> = (0..h * w).map(|i| (i / w) as f64 + 0.5).collect();
        let params = FitParams { gradients: true, max_stops: 4, tol: 3.0 };
        let index = LabelIndex::build(labels);
        let mut ids: Vec<i32> = labels.data.iter().copied().filter(|v| *v != 0).collect();
        ids.sort_unstable();
        ids.dedup();
        let mut fills = std::collections::HashMap::new();
        for lab in &ids {
            let m = mask_of(labels, *lab);
            let (wt, core) = interior(&m);
            let px = index.pixels(*lab);
            let x: Vec<f64> = px.iter().map(|i| xs[*i as usize]).collect();
            let y: Vec<f64> = px.iter().map(|i| ys[*i as usize]).collect();
            let c: Vec<[f64; 4]> = px.iter().map(|i| rgba255[*i as usize]).collect();
            fills.insert(*lab, fit_fill(&x, &y, &c, &params, Some(&wt), Some(&core)));
        }
        fills
    }

    /// The boundary graph's arcs, for `tools/diffcheck.py`: every arc's label
    /// pair and vertex count followed by its sub-pixel vertices, flattened,
    /// with the arcs in a canonical order so the two implementations line up.
    #[pyfunction]
    fn _stage_arcs(rgba: Vec<u8>, h: usize, w: usize, labels: Vec<i32>, snap: bool, extend: bool) -> Vec<f64> {
        use crate::core::grid::Grid;

        let prep = prepare::prepare(&rgba, h, w);
        // The caller passes the label map, so this compares the boundary graph
        // alone: `labels0` is allowed a slack of a few pixels, and one pixel
        // moving redraws the graph.
        let labels: Grid<i32> = Grid::from_vec(h, w, labels);

        let fills = _fills_for(&prep, &labels, h, w);
        let fill_at = |lab: i32, qx: &[f64], qy: &[f64]| -> Vec<[f64; 4]> {
            match fills.get(&lab) {
                Some(f) => f.evaluate(qx, qy),
                None => vec![[0.0; 4]; qx.len()],
            }
        };
        let cp = curves::CurveParams {
            corner_threshold: 60.0,
            tol: 0.4,
            shape_fitting: true,
            snap_axis_deg: 1.5,
            kind_tol: curves::KIND_TOL,
        };
        let bnd = topology::build_opt(&labels, &prep.rgb, &prep.alpha, &fill_at, &cp, None, &topology::Underlay::default(), snap, extend);

        let mut rows: Vec<Vec<f64>> = bnd
            .arcs
            .iter()
            .map(|a| {
                let mut row = vec![a.pair.0 as f64, a.pair.1 as f64, a.pts.len() as f64];
                for q in &a.pts {
                    row.push(q[0]);
                    row.push(q[1]);
                }
                row
            })
            .collect();
        rows.sort_by(|x, y| x.partial_cmp(y).unwrap_or(std::cmp::Ordering::Equal));
        rows.concat()
    }

    /// Fills handed over from the Python as (label, kind, numbers), the numbers
    /// laid out as `_fit_fill` returns them, so a stage can be fed the Python's
    /// own fills and compare what it does with them alone.
    fn _fills_from(labels: &[i32], kinds: &[String], vals: &[Vec<f64>]) -> std::collections::HashMap<i32, fills::Fill> {
        let stops = |rest: &[f64]| -> Vec<fills::Stop> {
            rest.chunks_exact(5).map(|s| fills::Stop { offset: s[0], rgba: [s[1], s[2], s[3], s[4]] }).collect()
        };
        labels
            .iter()
            .zip(kinds.iter().zip(vals.iter()))
            .map(|(lab, (kind, v))| {
                let f = match kind.as_str() {
                    "solid" => fills::Fill::Solid { rgba: [v[0], v[1], v[2], v[3]] },
                    "linear" => fills::Fill::Linear { x1: v[0], y1: v[1], x2: v[2], y2: v[3], stops: stops(&v[4..]) },
                    _ => fills::Fill::Radial { cx: v[0], cy: v[1], r: v[2], stops: stops(&v[3..]) },
                };
                (*lab, f)
            })
            .collect()
    }

    /// Every region's local colour correction (`topology::LocalFills`) given
    /// one label map and the Python's fills, for `tools/diffcheck.py`: per
    /// region with a grid, in label order, [label, r0, c0, rows, cols] and
    /// then the grid's RGB values row by row.
    #[pyfunction]
    #[allow(clippy::too_many_arguments)]
    fn _stage_local_fills(
        rgba: Vec<u8>,
        h: usize,
        w: usize,
        labels: Vec<i32>,
        fill_labels: Vec<i32>,
        fill_kinds: Vec<String>,
        fill_vals: Vec<Vec<f64>>,
    ) -> Vec<f64> {
        use crate::core::grid::Grid;
        let prep = prepare::prepare(&rgba, h, w);
        let labels: Grid<i32> = Grid::from_vec(h, w, labels);
        let fills = _fills_from(&fill_labels, &fill_kinds, &fill_vals);
        let fill_at = |lab: i32, qx: &[f64], qy: &[f64]| -> Vec<[f64; 4]> {
            match fills.get(&lab) {
                Some(f) => f.evaluate(qx, qy),
                None => vec![[0.0; 4]; qx.len()],
            }
        };
        let local = topology::LocalFills::build(&labels, &prep.rgb, &fill_at);
        let mut ids: Vec<i32> = local.grids.keys().copied().collect();
        ids.sort_unstable();
        let mut out = Vec::new();
        for lab in ids {
            let g = &local.grids[&lab];
            out.extend_from_slice(&[lab as f64, g.r0 as f64, g.c0 as f64, g.h as f64, g.w as f64]);
            for c in &g.corr {
                out.extend_from_slice(c);
            }
        }
        out
    }

    /// The placed vertices of every arc (as `_stage_arcs`), given one label map
    /// and the Python's own fills, so that what is compared is the placement
    /// alone — coverage, the local fills, the crossing search and the rules
    /// applied to its answers — or, with `snap`, the placement and everything
    /// after it that moves a vertex; with `extend`, the wedge extension first.
    #[pyfunction]
    #[allow(clippy::too_many_arguments)]
    fn _stage_place(
        rgba: Vec<u8>,
        h: usize,
        w: usize,
        labels: Vec<i32>,
        fill_labels: Vec<i32>,
        fill_kinds: Vec<String>,
        fill_vals: Vec<Vec<f64>>,
        snap: bool,
        extend: bool,
    ) -> Vec<f64> {
        use crate::core::grid::Grid;
        let prep = prepare::prepare(&rgba, h, w);
        let labels: Grid<i32> = Grid::from_vec(h, w, labels);
        let fills = _fills_from(&fill_labels, &fill_kinds, &fill_vals);
        let fill_at = |lab: i32, qx: &[f64], qy: &[f64]| -> Vec<[f64; 4]> {
            match fills.get(&lab) {
                Some(f) => f.evaluate(qx, qy),
                None => vec![[0.0; 4]; qx.len()],
            }
        };
        let cp = curves::CurveParams {
            corner_threshold: 60.0,
            tol: 0.4,
            shape_fitting: true,
            snap_axis_deg: 1.5,
            kind_tol: curves::KIND_TOL,
        };
        let bnd = topology::build_opt(&labels, &prep.rgb, &prep.alpha, &fill_at, &cp, None, snap, extend);
        let mut rows: Vec<Vec<f64>> = bnd
            .arcs
            .iter()
            .map(|a| {
                let mut row = vec![a.pair.0 as f64, a.pair.1 as f64, a.pts.len() as f64];
                for q in &a.pts {
                    row.push(q[0]);
                    row.push(q[1]);
                }
                row
            })
            .collect();
        rows.sort_by(|x, y| x.partial_cmp(y).unwrap_or(std::cmp::Ordering::Equal));
        rows.concat()
    }

    fn seg_rows(segs: &[curves::Segment]) -> Vec<(String, Vec<f64>)> {
        segs.iter()
            .map(|s| match s {
                curves::Segment::Line { p0, p1 } => ("L".to_string(), vec![p0[0], p0[1], p1[0], p1[1]]),
                curves::Segment::Cubic { p0, c1, c2, p1 } => {
                    ("C".to_string(), vec![p0[0], p0[1], c1[0], c1[1], c2[0], c2[1], p1[0], p1[1]])
                }
                curves::Segment::Arc { p0, p1, r, large, sweep } => {
                    ("A".to_string(), vec![p0[0], p0[1], p1[0], p1[1], *r, *large as u8 as f64, *sweep as u8 as f64])
                }
            })
            .collect()
    }

    fn seg_from_row(kind: &str, v: &[f64]) -> curves::Segment {
        match kind {
            "L" => curves::Segment::Line { p0: [v[0], v[1]], p1: [v[2], v[3]] },
            "C" => curves::Segment::Cubic { p0: [v[0], v[1]], c1: [v[2], v[3]], c2: [v[4], v[5]], p1: [v[6], v[7]] },
            _ => curves::Segment::Arc { p0: [v[0], v[1]], p1: [v[2], v[3]], r: v[4], large: v[5] != 0.0, sweep: v[6] != 0.0 },
        }
    }

    fn points(v: &[f64]) -> Vec<[f64; 2]> {
        v.chunks(2).map(|c| [c[0], c[1]]).collect()
    }

    type ArcOut = (Vec<f64>, Vec<f64>, Vec<(String, Vec<f64>)>, Option<Vec<f64>>, (Option<(f64, f64)>, Option<(f64, f64)>), (f64, f64), bool);

    /// Stage 7a on arcs another implementation fitted, for `tools/diffcheck.py`'s
    /// `rects` stage: the Python hands over its graph as it stood after the arc
    /// fit (every arc's state, the padded label map and the edge index) and
    /// gets back the decision log and every arc as `rectify` + `fillets` left it.
    #[pyfunction]
    #[allow(clippy::too_many_arguments, clippy::type_complexity)]
    fn _stage_rects(
        padded: Vec<i32>,
        ph: usize,
        pw: usize,
        rgb: Vec<f64>,
        h: usize,
        w: usize,
        edge_keys: Vec<u64>,
        edge_vals: Vec<(usize, usize)>,
        pairs: Vec<(i32, i32)>,
        pts: Vec<Vec<f64>>,
        normals: Vec<Vec<f64>>,
        ends: Vec<(Option<u64>, Option<u64>)>,
        segments: Vec<Vec<(String, Vec<f64>)>>,
        tangents: Vec<(Option<(f64, f64)>, Option<(f64, f64)>)>,
        flags: Vec<(bool, bool, f64, f64)>,
        slivers: Vec<Option<Vec<bool>>>,
        mirrors: Vec<Option<(f64, f64, f64, f64)>>,
        corner_threshold: f64,
        tol: f64,
        snap_axis_deg: f64,
    ) -> (Vec<Vec<f64>>, Vec<ArcOut>) {
        use crate::core::grid::{Grid, Image};
        let arcs: Vec<topology::Arc> = (0..pairs.len())
            .map(|k| topology::Arc {
                pair: pairs[k],
                pts: points(&pts[k]),
                normal: points(&normals[k]),
                n0: ends[k].0,
                n1: ends[k].1,
                segments: segments[k].iter().map(|(kind, v)| seg_from_row(kind, v)).collect(),
                under: Vec::new(),
                t0: tangents[k].0.map(|t| [t.0, t.1]),
                t1: tangents[k].1.map(|t| [t.0, t.1]),
                tip0: flags[k].0,
                tip1: flags[k].1,
                trim0: flags[k].2,
                trim1: flags[k].3,
                sliver: slivers[k].clone(),
                mirror: mirrors[k].map(|m| ([m.0, m.1], [m.2, m.3])),
                rect: None,
            })
            .collect();
        let edge_arc: std::collections::HashMap<u64, (usize, usize)> = edge_keys.into_iter().zip(edge_vals).collect();
        let mut bnd = topology::Boundary::from_parts(arcs, Grid::from_vec(ph, pw, padded), edge_arc);
        let image = Image { h, w, c: 3, data: rgb };
        let params = curves::CurveParams { corner_threshold, tol, shape_fitting: true, snap_axis_deg, kind_tol: curves::KIND_TOL };
        let mut log: Vec<Vec<f64>> = Vec::new();
        let (rect_arcs, radii) = topology::rectify::rectify(&mut bnd, &params, &image, Some(&mut log));
        topology::rectify::fillets(&mut bnd, &params, &rect_arcs, &radii, Some(&mut log));
        let out = bnd
            .arcs
            .iter()
            .map(|a| {
                let rect = a.rect.as_ref().map(|s| match s {
                    curves::Shape::Rect { x, y, w, h } => vec![*x, *y, *w, *h, 0.0],
                    curves::Shape::RoundedRect { x, y, w, h, rx } => vec![*x, *y, *w, *h, *rx],
                    _ => Vec::new(),
                });
                (
                    a.pts.iter().flat_map(|p| [p[0], p[1]]).collect(),
                    a.normal.iter().flat_map(|p| [p[0], p[1]]).collect(),
                    seg_rows(&a.segments),
                    rect,
                    (a.t0.map(|t| (t[0], t[1])), a.t1.map(|t| (t[0], t[1]))),
                    (a.trim0, a.trim1),
                    a.sliver.is_some(),
                )
            })
            .collect();
        (log, out)
    }

    /// The label map after cut-off regions have been handed back the pixels
    /// their ink still runs through, for `tools/diffcheck.py`.
    #[pyfunction]
    fn _stage_wedges(rgba: Vec<u8>, h: usize, w: usize, labels: Vec<i32>) -> Vec<i32> {
        use crate::core::grid::Grid;
        let prep = prepare::prepare(&rgba, h, w);
        let labels: Grid<i32> = Grid::from_vec(h, w, labels);
        let fills = _fills_for(&prep, &labels, h, w);
        let fill_at = |lab: i32, qx: &[f64], qy: &[f64]| -> Vec<[f64; 4]> {
            match fills.get(&lab) {
                Some(f) => f.evaluate(qx, qy),
                None => vec![[0.0; 4]; qx.len()],
            }
        };
        let mut padded = Grid::<i32>::new(h + 2, w + 2);
        for r in 0..h {
            for c in 0..w {
                padded.set(r + 1, c + 1, *labels.get(r, c));
            }
        }
        topology::extend_wedges(&padded, &prep.rgb, &prep.alpha, &fill_at).0.data
    }

    /// Which of the `at` pixels a nearby edge explains (`rescue::edge_mix`),
    /// given one label map, colours, fills-at-each-pixel and alpha, for
    /// `tools/diffcheck.py`.
    #[pyfunction]
    #[allow(clippy::too_many_arguments)]
    fn _stage_edge_mix(labels: Vec<i32>, h: usize, w: usize, colour: Vec<f64>, pred: Vec<f64>, alpha: Vec<f64>, at: Vec<bool>) -> Vec<i32> {
        use crate::core::grid::Grid;
        let quad = |v: &[f64]| -> Vec<[f64; 4]> { v.chunks_exact(4).map(|q| [q[0], q[1], q[2], q[3]]).collect() };
        let ex = crate::rescue::edge_mix(
            &Grid::from_vec(h, w, labels),
            &quad(&colour),
            &quad(&pred),
            &Grid::from_vec(h, w, alpha),
            &Grid::from_vec(h, w, at),
        );
        ex.data.iter().map(|b| *b as i32).collect()
    }

    #[pyfunction]
    #[pyo3(signature = (rgba, width, height, params))]
    fn trace(py: Python<'_>, rgba: Vec<u8>, width: usize, height: usize, params: &Bound<'_, PyDict>) -> PyResult<String> {
        let mut p = engine::VexelParams::default();
        macro_rules! get {
            ($name:literal, $field:ident, $ty:ty) => {
                if let Some(v) = params.get_item($name)? {
                    p.$field = v.extract::<$ty>()?;
                }
            };
        }
        get!("detail", detail, f64);
        get!("min_region", min_region, usize);
        get!("gradients", gradients, bool);
        get!("max_stops", max_stops, usize);
        get!("layering", layering, String);
        get!("corner_threshold", corner_threshold, f64);
        get!("curve_tolerance", curve_tolerance, f64);
        get!("shape_fitting", shape_fitting, bool);
        get!("refine", refine, bool);
        get!("upsample", upsample, String);
        get!("strokes", strokes, bool);
        get!("shadows", shadows, bool);
        get!("stroke_tolerance", stroke_tolerance, f64);
        get!("overlaps", overlaps, bool);
        get!("path_precision", path_precision, usize);

        if rgba.len() != width * height * 4 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "rgba buffer does not match width x height x 4",
            ));
        }
        // The trace is pure CPU work on an owned buffer, so the interpreter lock is
        // released for its whole duration: the API runs engines in worker threads
        // and two concurrent traces must actually overlap.
        Ok(py.allow_threads(move || engine::trace_rgba(&rgba, height, width, &p)))
    }

    #[pymodule]
    fn vexel_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(trace, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_grad, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_features, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_rgb, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_labels0, m)?)?;
    m.add_function(wrap_pyfunction!(_stage_upsample, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_arcs, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_rects, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_under, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_wedges, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_local_fills, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_place, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_edge_mix, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_seed, m)?)?;
        m.add_function(wrap_pyfunction!(_rng_choice, m)?)?;
        m.add_function(wrap_pyfunction!(_fit_fill, m)?)?;
        m.add_function(wrap_pyfunction!(_lstsq, m)?)?;
        m.add_function(wrap_pyfunction!(_fit_arc, m)?)?;
        m.add_function(wrap_pyfunction!(_medial_axis, m)?)?;
        m.add_function(wrap_pyfunction!(_stroke_fidelity, m)?)?;
        m.add_function(wrap_pyfunction!(_is_thin, m)?)?;
        m.add_function(wrap_pyfunction!(_stroke_geometry, m)?)?;
        m.add_function(wrap_pyfunction!(_group_thin, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_ridge, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_watershed, m)?)?;
        Ok(())
    }
}
