//! The edge band is the edge's: a region's fill is fitted from its core
//! (`weights::fill_core`), and the rescue only promotes what reaches a core.
//! Mirrors `tests/test_vexel_rescue.py` and `tests/test_vexel_fills.py`.

use vexel_rs::core::grid::{Grid, Mask};
use vexel_rs::core::labels::Labels;
use vexel_rs::fills::{fit_fill, Fill, FitParams};
use vexel_rs::rescue::rescue_features;
use vexel_rs::weights::interior;

fn glyph_on_white() -> Labels {
    // label 1 = white, label 2 = a glyph from column 10 on
    let mut l = Grid::filled(40, 40, 1i32);
    for r in 0..40 {
        for c in 10..40 {
            l.data[r * 40 + c] = 2;
        }
    }
    l
}

fn core_map(l: &Labels) -> Mask {
    let mut core = Grid::filled(l.h, l.w, false);
    for lab in [1, 2] {
        let m = Grid { h: l.h, w: l.w, data: l.data.iter().map(|v| *v == lab).collect() };
        let (_, k) = interior(&m);
        let mut j = 0;
        for i in 0..m.data.len() {
            if m.data[i] {
                core.data[i] = k[j];
                j += 1;
            }
        }
    }
    core
}

#[test]
fn a_sharpening_halo_along_an_edge_is_not_a_feature() {
    let l = glyph_on_white();
    let core = core_map(&l);
    let mut residual = Grid::filled(40, 40, 0.0f64);
    for r in 0..40 {
        residual.data[r * 40 + 12] = 1.7; // depth 3 into the glyph: the edge's ring
    }
    let (out, rescued) = rescue_features(&l, &residual, 1.0, 3, None, Some(&core));
    assert!(rescued.is_empty());
    assert_eq!(out.data, l.data);
    // without the core rule the same ring is promoted
    let (_, rescued) = rescue_features(&l, &residual, 1.0, 3, None, None);
    assert_eq!(rescued.len(), 1);
    // five pixels in, it is a swallowed line
    let mut residual = Grid::filled(40, 40, 0.0f64);
    for r in 5..35 {
        residual.data[r * 40 + 14] = 1.7;
    }
    let (_, rescued) = rescue_features(&l, &residual, 1.0, 3, None, Some(&core));
    assert_eq!(rescued.len(), 1);
}

#[test]
fn a_thin_region_keeps_every_pixel_as_its_core() {
    // a two-pixel line has no pixel deeper than REACH: it is all edge band
    let mut m = Grid::filled(10, 40, false);
    for r in 4..6 {
        for c in 2..38 {
            m.data[r * 40 + c] = true;
        }
    }
    let (_, core) = interior(&m);
    assert!(core.iter().all(|v| *v));
}

#[test]
fn an_anti_aliased_counter_is_solid() {
    // the "e" counter of the Vexel wordmark, top to bottom: grey rim rows
    // either side of white. Fitted with its rim it was a grey-white-grey ramp.
    let profile = [202.0, 246.0, 252.0, 251.0, 254.0, 254.0, 254.0, 252.0, 255.0, 170.0];
    let (h, w, pad) = (profile.len() + 6, 38 + 6, 3);
    let mut m = Grid::filled(h, w, false);
    for r in pad..pad + profile.len() {
        for c in pad..pad + 38 {
            m.data[r * w + c] = true;
        }
    }
    let (wt, core) = interior(&m);
    let (mut xs, mut ys, mut col) = (Vec::new(), Vec::new(), Vec::new());
    for r in 0..h {
        for c in 0..w {
            if m.data[r * w + c] {
                xs.push(c as f64 + 0.5);
                ys.push(r as f64 + 0.5);
                let v = profile[r - pad];
                col.push([v, v, v, 255.0]);
            }
        }
    }
    let params = FitParams { gradients: true, max_stops: 4, tol: 3.0 };
    match fit_fill(&xs, &ys, &col, &params, Some(&wt), Some(&core)) {
        Fill::Solid { rgba } => assert!((rgba[0] - 253.0).abs() < 1.5, "{rgba:?}"),
        other => panic!("expected a solid, got {other:?}"),
    }
}
