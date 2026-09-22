//! Vexel, in Rust. The Python package keeps the parameter model and the engine
//! seam; everything between RGBA bytes and the SVG string happens here.

// Index arithmetic is the subject here, not an accident of style: a row-major
// grid is addressed as `r * w + c`, and rewriting those loops as iterator
// chains obscures which index is which.
#![allow(clippy::needless_range_loop)]

pub mod boundary;
pub mod core;
pub mod curves;
pub mod engine;
pub mod fills;
pub mod merge;
pub mod order;
pub mod overlaps;
pub mod partition;
pub mod posterize;
pub mod prepare;
pub mod refine;
pub mod rescue;
pub mod shadows;
pub mod stats;
pub mod topology;
pub mod timing;
pub mod strokes;
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
    #[pyo3(signature = (xs, ys, rgba, weights, gradients, max_stops, tol))]
    fn _fit_fill(
        xs: Vec<f64>,
        ys: Vec<f64>,
        rgba: Vec<f64>,
        weights: Vec<f64>,
        gradients: bool,
        max_stops: usize,
        tol: f64,
    ) -> (String, Vec<f64>) {
        let col: Vec<[f64; 4]> = rgba.chunks(4).map(|c| [c[0], c[1], c[2], c[3]]).collect();
        let params = fills::FitParams { gradients, max_stops, tol };
        let f = fills::fit_fill(&xs, &ys, &col, &params, Some(&weights));
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
        use crate::weights::interior_weights;
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
            let wt = interior_weights(&m);
            let px = index.pixels(*lab);
            let x: Vec<f64> = px.iter().map(|i| xs[*i as usize]).collect();
            let y: Vec<f64> = px.iter().map(|i| ys[*i as usize]).collect();
            let c: Vec<[f64; 4]> = px.iter().map(|i| rgba255[*i as usize]).collect();
            fills.insert(*lab, fit_fill(&x, &y, &c, &params, Some(&wt)));
        }
        fills
    }

    /// The boundary graph's arcs, for `tools/diffcheck.py`: every arc's label
    /// pair and vertex count followed by its sub-pixel vertices, flattened,
    /// with the arcs in a canonical order so the two implementations line up.
    #[pyfunction]
    fn _stage_arcs(rgba: Vec<u8>, h: usize, w: usize, labels: Vec<i32>, snap: bool, extend: bool) -> Vec<f64> {
        use crate::core::grid::Grid;
        use std::collections::HashMap;

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
        m.add_function(wrap_pyfunction!(_stage_arcs, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_wedges, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_seed, m)?)?;
        m.add_function(wrap_pyfunction!(_rng_choice, m)?)?;
        m.add_function(wrap_pyfunction!(_fit_fill, m)?)?;
        m.add_function(wrap_pyfunction!(_lstsq, m)?)?;
        m.add_function(wrap_pyfunction!(_medial_axis, m)?)?;
        m.add_function(wrap_pyfunction!(_stroke_fidelity, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_ridge, m)?)?;
        m.add_function(wrap_pyfunction!(_stage_watershed, m)?)?;
        Ok(())
    }
}
