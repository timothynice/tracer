//! Tests for the primitives that replace `scipy.ndimage`, `skimage` and
//! `numpy.linalg`.
//!
//! These are the parts with no Python counterpart in the tree, so they are
//! checked against their definitions rather than against the reference
//! implementation; `tools/diffcheck.py` does the cross-implementation half.

use vexel_rs::core::contours::find_contours;
use vexel_rs::core::edt::edt;
use vexel_rs::core::filters::{self, Accum, Mode};
use vexel_rs::core::grid::Grid;
use vexel_rs::core::labels::{label_mask, relabel_sequential, LabelIndex};
use vexel_rs::core::linalg::{eigh2, lstsq, solve, Mat};
use vexel_rs::core::morphology::{dilate_cross, erode_cross, fill_holes};
use vexel_rs::core::optimise::{default_simplex, nelder_mead, NmOptions};
use vexel_rs::core::rng::{choice_without_replacement, Pcg64};
use vexel_rs::core::skeleton::medial_axis;
use vexel_rs::core::watershed::{watershed, watershed_masked};

fn approx(a: f64, b: f64, tol: f64) {
    assert!((a - b).abs() <= tol, "{a} != {b} (tolerance {tol})");
}

#[test]
fn gaussian_kernel_is_normalised_and_symmetric() {
    for sigma in [0.5, 0.7, 1.0, 4.0, 12.5] {
        let k = filters::gaussian_kernel1d(sigma);
        assert_eq!(k.len() % 2, 1, "kernel must have a centre tap");
        assert_eq!(k.len(), 2 * ((4.0 * sigma + 0.5) as usize) + 1, "scipy's truncate = 4 radius");
        approx(k.iter().sum::<f64>(), 1.0, 1e-12);
        for i in 0..k.len() / 2 {
            approx(k[i], k[k.len() - 1 - i], 1e-15);
        }
    }
}

#[test]
fn blur_of_a_constant_field_is_that_constant() {
    // only under a border mode that extends the field; with Constant the edge
    // must fall off instead
    let n = 25;
    let g = Grid::filled(n, n, 3.0);
    let out = filters::gaussian_filter(&g, 1.5, Mode::Reflect, 0.0);
    for v in &out.data {
        approx(*v, 3.0, 1e-9);
    }
    // with a constant border the kernel reaches outside: only pixels further
    // than the truncation radius from every edge keep the full value
    let out = filters::gaussian_filter(&g, 1.5, Mode::Constant, 0.0);
    assert!(out.data[0] < 3.0, "a constant-padded corner must be pulled down");
    approx(out.data[(n / 2) * n + n / 2], 3.0, 1e-6);
}

#[test]
fn reflect_mode_mirrors_about_the_edge_between_pixels() {
    // scipy's `reflect` is (d c b a | a b c d), not numpy's (c b | a b c d)
    let g = Grid::from_vec(1, 4, vec![1.0, 2.0, 3.0, 4.0]);
    let w = [1.0, 0.0, 0.0]; // out[i] = in[i - 1]
    let out = filters::correlate1d_x(&g, &w, Mode::Reflect, 0.0, Accum::Exact);
    approx(out.data[0], 1.0, 1e-12); // in[-1] mirrors to in[0]
    approx(out.data[1], 1.0, 1e-12);
}

#[test]
fn fast_and_exact_accumulation_agree_to_rounding() {
    let mut g = Grid::<f64>::new(31, 37);
    for (i, v) in g.data.iter_mut().enumerate() {
        *v = ((i * 7919) % 251) as f64;
    }
    let w = filters::gaussian_kernel1d(3.0);
    let a = filters::correlate1d_x(&g, &w, Mode::Constant, 0.0, Accum::Exact);
    let b = filters::correlate1d_x(&g, &w, Mode::Constant, 0.0, Accum::Fast);
    for (x, y) in a.data.iter().zip(b.data.iter()) {
        approx(*x, *y, 1e-9 * x.abs().max(1.0));
    }
}

#[test]
fn scharr_sees_a_step_and_ignores_a_flat_field() {
    let mut g = Grid::<f64>::new(7, 7);
    for r in 0..7 {
        for c in 0..7 {
            g.data[r * 7 + c] = if c >= 4 { 10.0 } else { 0.0 };
        }
    }
    let v = filters::scharr_v(&g); // derivative across columns
    let h = filters::scharr_h(&g); // derivative down rows
    assert!(v.data[3 * 7 + 3].abs() > 5.0, "a vertical step is a column derivative");
    approx(h.data[3 * 7 + 3], 0.0, 1e-9);
}

#[test]
fn distance_transform_matches_hand_computed_distances() {
    // one false pixel in the middle: every other pixel's distance is Euclidean
    let mut m = Grid::filled(5, 5, true);
    m.data[2 * 5 + 2] = false;
    let d = edt(&m);
    approx(d.data[2 * 5 + 2], 0.0, 1e-12);
    approx(d.data[2 * 5 + 3], 1.0, 1e-12);
    approx(d.data[5 + 1], 2f64.sqrt(), 1e-12);
    approx(d.data[0], (8f64).sqrt(), 1e-12);
}

#[test]
fn connected_components_are_numbered_in_scan_order() {
    // two blobs; the one whose first pixel comes first in raster order is 1
    let w = 6;
    let at = |r: usize, c: usize| r * w + c;
    let mut m = Grid::filled(4, w, false);
    m.data[at(0, 4)] = true; // top-right blob, first in scan order
    m.data[at(0, 5)] = true;
    m.data[at(2, 0)] = true; // bottom-left blob
    m.data[at(3, 0)] = true;
    let l = label_mask(&m, 1);
    assert_eq!(l.data[at(0, 4)], 1);
    assert_eq!(l.data[at(0, 5)], 1);
    assert_eq!(l.data[at(2, 0)], 2);
    assert_eq!(l.data[at(3, 0)], 2);
}

#[test]
fn diagonal_neighbours_only_join_under_connectivity_two() {
    let mut m = Grid::filled(3, 3, false);
    m.data[0] = true;
    m.data[4] = true;
    assert_eq!(label_mask(&m, 1).data.iter().copied().max().unwrap(), 2);
    assert_eq!(label_mask(&m, 2).data.iter().copied().max().unwrap(), 1);
}

#[test]
fn relabel_sequential_keeps_the_sorted_order() {
    let l = Grid::from_vec(1, 5, vec![0, 7, 3, 7, 40]);
    let (out, fwd) = relabel_sequential(&l);
    assert_eq!(out.data, vec![0, 2, 1, 2, 3]);
    assert_eq!(fwd[&3], 1);
    assert_eq!(fwd[&40], 3);
}

#[test]
fn label_index_returns_each_region_in_raster_order() {
    let l = Grid::from_vec(2, 3, vec![1, 2, 1, 0, 2, 2]);
    let idx = LabelIndex::build(&l);
    assert_eq!(idx.pixels(1), &[0, 2]);
    assert_eq!(idx.pixels(2), &[1, 4, 5]);
    assert_eq!(idx.area(2), 3);
    assert!(idx.pixels(9).is_empty());
}

#[test]
fn watershed_splits_a_ridge_between_two_markers() {
    // a valley-ridge-valley profile; the two markers should meet on the ridge
    let w = 7;
    let mut image = Grid::<f64>::new(1, w);
    for c in 0..w {
        image.data[c] = 3.0 - (c as f64 - 3.0).abs(); // peak at the centre
    }
    let mut markers = Grid::<i32>::new(1, w);
    markers.data[0] = 1;
    markers.data[w - 1] = 2;
    let out = watershed(&image, &markers);
    assert_eq!(out.data[0], 1);
    assert_eq!(out.data[w - 1], 2);
    assert!(out.data.iter().all(|v| *v != 0), "every pixel is claimed");
    assert_eq!(out.data[1], 1);
    assert_eq!(out.data[w - 2], 2);
}

#[test]
fn a_masked_watershed_floods_only_inside_the_mask() {
    // skimage's `watershed(image, markers, mask=mask)`: a marker outside the
    // mask is dropped, the flood never enters a pixel outside it, and those
    // pixels stay 0 — so a masked-out pixel is a wall the flood cannot cross
    let w = 7;
    let image = Grid::<f64>::new(1, w);
    let mut markers = Grid::<i32>::new(1, w);
    markers.data[0] = 1;
    markers.data[w - 1] = 2;
    let mut mask = Grid::filled(1, w, true);
    mask.data[2] = false;
    mask.data[w - 1] = false;
    let out = watershed_masked(&image, &markers, Some(&mask));
    assert_eq!(out.data, vec![1, 1, 0, 0, 0, 0, 0]);
    let open = watershed_masked(&image, &markers, None);
    assert_eq!(open.data, watershed(&image, &markers).data);
}

#[test]
fn morphology_grows_and_shrinks_by_one_four_neighbour() {
    let mut m = Grid::filled(5, 5, false);
    m.data[2 * 5 + 2] = true;
    let d = dilate_cross(&m);
    assert_eq!(d.count(), 5, "a point becomes a plus");
    assert!(!d.data[5 + 1], "the corners are not 4-neighbours");
    assert_eq!(erode_cross(&d, false).count(), 1);
}

#[test]
fn fill_holes_closes_an_enclosed_gap_but_not_a_bay() {
    let mut m = Grid::filled(5, 5, false);
    for r in 1..4 {
        for c in 1..4 {
            m.data[r * 5 + c] = true;
        }
    }
    m.data[2 * 5 + 2] = false; // a hole
    assert_eq!(fill_holes(&m).count(), 9);

    let mut bay = m.clone();
    bay.data[2 * 5 + 1] = false; // now the hole opens to the outside
    assert_eq!(fill_holes(&bay).count(), bay.count());
}

#[test]
fn marching_squares_traces_a_square_at_the_half_level() {
    let mut f = Grid::<f64>::new(6, 6);
    for r in 2..4 {
        for c in 2..4 {
            f.data[r * 6 + c] = 1.0;
        }
    }
    let cs = find_contours(&f, 0.5);
    assert_eq!(cs.len(), 1);
    let poly = &cs[0];
    for (r, c) in poly {
        assert!((1.5..=4.5).contains(r) && (1.5..=4.5).contains(c), "({r}, {c}) off the square");
    }
    let (first, last) = (poly[0], poly[poly.len() - 1]);
    assert_eq!(first, last, "a contour that does not touch the border closes");
}

#[test]
fn medial_axis_of_a_bar_is_a_line_and_is_reproducible() {
    let (h, w) = (9, 21);
    let mut m = Grid::filled(h, w, false);
    for r in 3..6 {
        for c in 2..19 {
            m.data[r * w + c] = true;
        }
    }
    let a = medial_axis(&m);
    let b = medial_axis(&m);
    assert_eq!(a.data, b.data, "skimage's is seeded from the OS; this one must not be");
    assert!(a.count() >= 10, "the bar's spine survives");
    assert!(a.count() < m.count() / 2, "the bar is thinned");
    for i in 0..a.len() {
        assert!(!a.data[i] || m.data[i], "the skeleton stays inside the shape");
    }
}

#[test]
fn lstsq_recovers_an_exact_fit() {
    // y = 2 + 3x, fitted by [1, x]
    let n = 12;
    let mut a = Mat::zeros(n, 2);
    let mut b = Mat::zeros(n, 1);
    for i in 0..n {
        let x = i as f64 * 0.37;
        a.set(i, 0, 1.0);
        a.set(i, 1, x);
        b.set(i, 0, 2.0 + 3.0 * x);
    }
    let coef = lstsq(&a, &b);
    approx(coef.at(0, 0), 2.0, 1e-10);
    approx(coef.at(1, 0), 3.0, 1e-10);
}

#[test]
fn lstsq_survives_a_rank_deficient_design() {
    // the second column is a copy of the first
    let mut a = Mat::zeros(4, 2);
    let mut b = Mat::zeros(4, 1);
    for i in 0..4 {
        a.set(i, 0, 1.0);
        a.set(i, 1, 1.0);
        b.set(i, 0, 5.0);
    }
    let coef = lstsq(&a, &b);
    approx(coef.at(0, 0) + coef.at(1, 0), 5.0, 1e-9);
}

#[test]
fn lstsq_handles_an_ill_conditioned_vandermonde() {
    // this is the radial surrogate's design, and the reason it is a QR
    let n = 200;
    let mut a = Mat::zeros(n, 4);
    let mut b = Mat::zeros(n, 1);
    for i in 0..n {
        let r = i as f64 / (n - 1) as f64;
        for p in 0..4 {
            a.set(i, p, r.powi(p as i32));
        }
        b.set(i, 0, 1.0 - 2.0 * r + 0.5 * r * r * r);
    }
    let coef = lstsq(&a, &b);
    approx(coef.at(0, 0), 1.0, 1e-8);
    approx(coef.at(1, 0), -2.0, 1e-7);
    approx(coef.at(2, 0), 0.0, 1e-7);
    approx(coef.at(3, 0), 0.5, 1e-7);
}

#[test]
fn eigh2_orders_ascending_and_returns_an_orthonormal_pair() {
    let (vals, vecs) = eigh2(4.0, 1.0, 2.0);
    assert!(vals[0] <= vals[1]);
    approx(vals[0] + vals[1], 6.0, 1e-12);
    approx(vals[0] * vals[1], 7.0, 1e-12);
    approx(vecs[0][0] * vecs[1][0] + vecs[0][1] * vecs[1][1], 0.0, 1e-12);
    for v in vecs {
        approx((v[0] * v[0] + v[1] * v[1]).sqrt(), 1.0, 1e-12);
    }
}

#[test]
fn solve_handles_a_pivot_on_the_diagonal() {
    let a = Mat { rows: 2, cols: 2, d: vec![0.0, 1.0, 1.0, 0.0] };
    let b = Mat { rows: 2, cols: 1, d: vec![3.0, 5.0] };
    let x = solve(&a, &b).expect("the system is non-singular");
    approx(x.at(0, 0), 5.0, 1e-12);
    approx(x.at(1, 0), 3.0, 1e-12);
    assert!(solve(&Mat::zeros(2, 2), &b).is_none(), "a singular system has no solution");
}

#[test]
fn nelder_mead_finds_the_rosenbrock_minimum() {
    let f = |v: &[f64]| (1.0 - v[0]).powi(2) + 100.0 * (v[1] - v[0] * v[0]).powi(2);
    let opts = NmOptions { xatol: 1e-8, fatol: 1e-10, maxiter: 4000, maxfev: usize::MAX };
    let out = nelder_mead(f, default_simplex(&[-1.2, 1.0]), &opts);
    approx(out[0], 1.0, 1e-4);
    approx(out[1], 1.0, 1e-4);
}

#[test]
fn nelder_mead_respects_its_iteration_cap() {
    let mut calls = 0usize;
    let f = |v: &[f64]| {
        calls += 1;
        v[0] * v[0]
    };
    let opts = NmOptions { xatol: 0.0, fatol: 0.0, maxiter: 3, maxfev: usize::MAX };
    nelder_mead(f, default_simplex(&[5.0]), &opts);
    assert!(calls <= 2 + 3 * 4, "three iterations cannot cost more than a handful of evaluations");
}

#[test]
fn pcg64_reproduces_numpys_stream_for_seed_1234() {
    // `np.random.default_rng(1234).bit_generator.random_raw(4)`
    let mut r = Pcg64::seed_1234();
    assert_eq!(r.next_u64(), 18016930633132456890);
    assert_eq!(r.next_u64(), 7013373421822782593);
    assert_eq!(r.next_u64(), 17030886991259909300);
    assert_eq!(r.next_u64(), 4827373169039523470);
}

#[test]
fn choice_reproduces_numpys_draw() {
    // `np.random.default_rng(1234).choice(10000, 8, replace=False)`
    let mut r = Pcg64::seed_1234();
    let got = choice_without_replacement(&mut r, 10000, 8);
    assert_eq!(got, vec![1048, 2616, 1713, 9230, 9786, 3800, 9874, 9761]);
}

#[test]
fn choice_never_repeats_and_stays_in_range() {
    let mut r = Pcg64::seed_1234();
    let got = choice_without_replacement(&mut r, 5000, 2500);
    assert_eq!(got.len(), 2500);
    assert!(got.iter().all(|v| *v < 5000));
    let mut sorted = got.clone();
    sorted.sort_unstable();
    sorted.dedup();
    assert_eq!(sorted.len(), 2500, "sampling is without replacement");
}
