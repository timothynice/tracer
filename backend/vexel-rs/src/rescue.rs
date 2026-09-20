//! Residual rescue: recover small features swallowed by a larger region.
//!
//! Thin strokes (≈ 1–3 px) and small high-contrast details can lose their seed
//! in the partition and end up inside a neighbouring region. After fills are
//! fitted they stand out as pixels whose colour disagrees strongly with their
//! region's fill. Those pixels — excluding the anti-aliasing ring along region
//! boundaries, which disagrees for a different reason — are grouped into
//! connected components and promoted to regions of their own.

use crate::core::grid::Grid;
use crate::core::labels::{self, Labels};
use crate::core::morphology::dilate_cross;
use crate::order::boundary_band;

/// Promote connected components of high-residual interior pixels to new regions.
/// Returns the new labels and the ids of the rescued regions.
pub fn rescue_features(
    l: &Labels,
    residual: &Grid<f64>,
    threshold: f64,
    min_region: usize,
) -> (Labels, Vec<i32>) {
    let band = boundary_band(l);
    let candidates = Grid {
        h: l.h,
        w: l.w,
        data: (0..l.len()).map(|i| residual.data[i] > threshold && !band.data[i]).collect(),
    };
    if !candidates.any() {
        return (l.clone(), Vec::new());
    }
    // Components are formed on a one-pixel dilation so a stroke broken by
    // anti-aliasing gaps or junctions is rescued as one feature, not as shards.
    let mut comps = labels::label_mask(&dilate_cross(&candidates), 2);
    for i in 0..comps.len() {
        if !candidates.data[i] {
            comps.data[i] = 0;
        }
    }
    let sizes = labels::bincount(&comps);
    let floor = min_region.max(3);
    let keep: Vec<usize> = (1..sizes.len()).filter(|i| sizes[*i] >= floor).collect();
    if keep.is_empty() {
        return (l.clone(), Vec::new());
    }

    let mut out = l.clone();
    let first_id = l.data.iter().copied().max().unwrap_or(0) + 1;
    let mut rescued: Vec<i32> = Vec::new();
    for (n, comp) in keep.into_iter().enumerate() {
        let next_id = first_id + n as i32;
        let m = Grid {
            h: l.h,
            w: l.w,
            data: comps.data.iter().map(|v| *v == comp as i32).collect(),
        };
        // slightly grow the component so its own anti-aliasing pixels come along
        let grown = dilate_cross(&m);
        for i in 0..out.len() {
            if grown.data[i] && (residual.data[i] > threshold * 0.5 || m.data[i]) {
                out.data[i] = next_id;
            }
        }
        rescued.push(next_id);
    }
    let (out, fwd) = labels::relabel_sequential(&out);
    let rescued = rescued
        .into_iter()
        .filter_map(|i| fwd.get(&i).copied())
        .collect();
    (out, rescued)
}
