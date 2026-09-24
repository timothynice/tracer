//! The rescue's edge test, on the scene `tests/test_vexel_rescue.py` uses: a
//! mid-grey region against white with whole columns repainted.

use vexel_rs::core::grid::Grid;
use vexel_rs::rescue::{edge_mix, rescue_features};

const H: usize = 24;
const W: usize = 40;
const HOST: [f64; 3] = [60.0, 60.0, 70.0];
const OTHER: [f64; 3] = [250.0, 250.0, 250.0];

fn toward(s: f64) -> [f64; 3] {
    std::array::from_fn(|k| HOST[k] + s * (OTHER[k] - HOST[k]))
}

fn scene(bands: &[(usize, [f64; 3])]) -> (Grid<i32>, Vec<[f64; 4]>, Vec<[f64; 4]>, Grid<f64>) {
    let mut labels = Grid::filled(H, W, 1i32);
    let mut pred = vec![[HOST[0], HOST[1], HOST[2], 255.0]; H * W];
    for r in 0..H {
        for c in 30..W {
            labels.set(r, c, 2);
            pred[r * W + c] = [OTHER[0], OTHER[1], OTHER[2], 255.0];
        }
    }
    let mut colour = pred.clone();
    for (col, rgb) in bands {
        for r in 0..H {
            colour[r * W + col] = [rgb[0], rgb[1], rgb[2], 255.0];
        }
    }
    (labels, colour, pred, Grid::filled(H, W, 1.0))
}

fn column(m: &Grid<bool>, c: usize) -> Vec<bool> {
    (0..H).map(|r| *m.get(r, c)).collect()
}

#[test]
fn edge_mix_explains_an_edge_ringing_both_ways_and_nothing_else() {
    let (labels, colour, pred, alpha) = scene(&[
        (27, toward(0.12)),
        (28, toward(-0.10)),
        (25, toward(0.60)),
        (26, [200.0, 40.0, 40.0]),
        (20, toward(0.12)),
    ]);
    let ex = edge_mix(&labels, &colour, &pred, &alpha, &Grid::filled(H, W, true));
    assert!(column(&ex, 27).iter().all(|b| *b) && column(&ex, 28).iter().all(|b| *b));
    for c in [25, 26, 20] {
        assert!(column(&ex, c).iter().all(|b| !*b), "column {c} is not an edge's rendering");
    }
}

#[test]
fn a_ringing_band_is_not_rescued_but_a_line_beside_it_is() {
    let (labels, colour, pred, alpha) = scene(&[(27, toward(0.15)), (10, [200.0, 40.0, 40.0])]);
    let residual = Grid::from_vec(
        H,
        W,
        (0..H * W)
            .map(|i| (0..4).map(|k| (colour[i][k] - pred[i][k]).powi(2)).sum::<f64>().sqrt() / 20.0)
            .collect(),
    );
    let at = Grid::from_vec(H, W, residual.data.iter().map(|v| *v > 1.0).collect());
    let ex = edge_mix(&labels, &colour, &pred, &alpha, &at);
    let (out, rescued) = rescue_features(&labels, &residual, 1.0, 3, Some(&ex), None);
    assert_eq!(rescued.len(), 1);
    assert!((2..H - 2).all(|r| *out.get(r, 10) == rescued[0]));
    assert!((0..H).all(|r| *out.get(r, 27) != rescued[0]));
    let (_, before) = rescue_features(&labels, &residual, 1.0, 3, None, None);
    assert_eq!(before.len(), 2);
}
