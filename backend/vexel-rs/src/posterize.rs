//! Optional stage for `gradients=False`: split smooth regions into flat bands.
//!
//! The partition never breaks a smooth ramp (that is the point of Vexel), so
//! with gradient fills disabled a ramp would collapse to one flat colour. Users
//! who turn gradients off expect posterised bands instead; this quantises the
//! Lab colour of each non-flat region with a step of `detail` and relabels
//! connected components.

use crate::core::grid::{Grid, Image};
use crate::core::labels::{self, Labels};
use crate::core::watershed::watershed;
use std::collections::HashMap;

pub fn posterize_regions(
    l: &Labels,
    features: &Image,
    detail: f64,
    min_region: usize,
    grad: &Grid<f64>,
) -> Labels {
    let (h, w) = (l.h, l.w);
    let mut out = Grid::<i32>::new(h, w);
    let mut next_id = 1i32;
    let step = detail.max(1.0);

    for lab in labels::unique_ids(l) {
        let idx: Vec<usize> = (0..l.len()).filter(|i| l.data[*i] == lab).collect();
        let n = idx.len() as f64;
        let mut mean = [0.0f64; 3];
        for i in &idx {
            for c in 0..3 {
                mean[c] += features.data[i * 4 + c] / n;
            }
        }
        let rms = (idx
            .iter()
            .map(|i| (0..3).map(|c| (features.data[i * 4 + c] - mean[c]).powi(2)).sum::<f64>())
            .sum::<f64>()
            / n)
            .sqrt();
        if rms <= step / 2.0 {
            for i in &idx {
                out.data[*i] = next_id;
            }
            next_id += 1;
            continue;
        }
        // connected components per quantised colour, in the order np.unique
        // yields the quantised keys
        let key_of = |i: usize| -> i64 {
            let q: Vec<i64> = (0..3).map(|c| (features.data[i * 4 + c] / step).floor() as i64).collect();
            (q[0] * 7919 + q[1]) * 7907 + q[2]
        };
        let mut uniq: Vec<i64> = idx.iter().map(|i| key_of(*i)).collect();
        uniq.sort_unstable();
        uniq.dedup();
        let mut comps = Grid::<i32>::new(h, w);
        let mut offset = 0i32;
        for k in uniq {
            // the Python labels `key == k` over the *whole* frame, not just
            // this region, so components outside it are numbered too
            let m = Grid {
                h,
                w,
                data: (0..l.len()).map(|i| l.data[i] == lab && key_of(i) == k).collect(),
            };
            let cc = labels::label_mask(&m, 1);
            let mut max = offset;
            for i in 0..cc.len() {
                if cc.data[i] > 0 {
                    comps.data[i] = cc.data[i] + offset;
                    if comps.data[i] > max {
                        max = comps.data[i];
                    }
                }
            }
            offset = max;
        }
        let mut local_max = 0i32;
        for i in &idx {
            out.data[*i] = comps.data[*i] + next_id - 1;
            if out.data[*i] > local_max {
                local_max = out.data[*i];
            }
        }
        next_id = local_max + 1;
    }

    // absorb tiny bands along the gradient like the partition does
    let sizes = labels::bincount(&out);
    let small: HashMap<i32, bool> = (1..sizes.len()).map(|i| (i as i32, sizes[i] < min_region)).collect();
    if small.values().any(|b| *b) {
        let mut markers = out.clone();
        let mut max = 0i32;
        for v in markers.data.iter_mut() {
            if *v > 0 && small.get(v).copied().unwrap_or(false) {
                *v = 0;
            }
            if *v > max {
                max = *v;
            }
        }
        if max > 0 {
            out = watershed(grad, &markers);
        }
    }
    labels::relabel_sequential(&out).0
}
