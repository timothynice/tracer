//! Residual rescue: recover small features swallowed by a larger region.
//!
//! Thin strokes (≈ 1–3 px) and small high-contrast details can lose their seed
//! in the partition and end up inside a neighbouring region. After fills are
//! fitted they stand out as pixels whose colour disagrees strongly with their
//! region's fill. Those pixels — excluding the anti-aliasing ring along region
//! boundaries, which disagrees for a different reason — are grouped into
//! connected components and promoted to regions of their own.

use crate::core::grid::{Grid, Mask};
use crate::core::labels::{self, Labels};
use crate::core::morphology::dilate_cross;
use crate::order::boundary_band;

/// How far an edge's rendering reaches into the regions either side: the
/// support radius of the resamplers artwork goes through (bilinear 1, bicubic
/// 2, Lanczos-3 3 px). See `rescue.EDGE_REACH`.
pub const EDGE_REACH: f64 = 3.0;
/// How far along the line from its own fill to the fill across the edge a
/// pixel may sit and still be its own region's rendering of that edge, either
/// way. See `rescue.EDGE_SHARE`.
pub const EDGE_SHARE: f64 = 0.5;
/// How far off that line, as a fraction of the edge's contrast, a mix may
/// stray. See `rescue.EDGE_CONE`.
pub const EDGE_CONE: f64 = 0.1;

/// The offsets within `reach`, grouped by distance, nearest first.
fn disk_rings(reach: f64) -> Vec<Vec<(i64, i64)>> {
    let r = reach.floor() as i64;
    let mut by: std::collections::BTreeMap<i64, Vec<(i64, i64)>> = std::collections::BTreeMap::new();
    for dy in -r..=r {
        for dx in -r..=r {
            let d2 = dy * dy + dx * dx;
            if d2 != 0 && (d2 as f64) <= reach * reach {
                by.entry(d2).or_default().push((dy, dx));
            }
        }
    }
    by.into_values().collect()
}

/// Pixels of `at` whose colour the edge beside them explains: a pixel whose
/// colour projects onto the line from its own fill (`pred` at the pixel) to
/// the fill of the region nearest it within `EDGE_REACH` (`pred` at that
/// region's pixel) at no more than `EDGE_SHARE` of the way, towards or away,
/// and lies within `EDGE_CONE` times the edge's contrast of it — the edge's
/// anti-aliasing or ringing, not a feature. Only the regions at the least
/// distance are asked, any of them may explain it; distances weight colour by
/// the pixel's alpha, as the rescue residual does. Twin of `rescue.edge_mix`.
pub fn edge_mix(l: &Labels, colour: &[[f64; 4]], pred: &[[f64; 4]], alpha: &Grid<f64>, at: &Mask) -> Mask {
    let (h, w) = (l.h as i64, l.w as i64);
    let rings = disk_rings(EDGE_REACH);
    let mut out = Grid::filled(l.h, l.w, false);
    for i in 0..l.len() {
        if !at.data[i] {
            continue;
        }
        let (r, c) = ((i / l.w) as i64, (i % l.w) as i64);
        let a = alpha.data[i];
        let wt = [a, a, a, 1.0];
        let d: [f64; 4] = std::array::from_fn(|k| (colour[i][k] - pred[i][k]) * wt[k]);
        'rings: for ring in rings.iter() {
            let mut met = false;
            for (dy, dx) in ring.iter() {
                let (nr, nc) = (r + dy, c + dx);
                if nr < 0 || nr >= h || nc < 0 || nc >= w {
                    continue;
                }
                let j = (nr * w + nc) as usize;
                if l.data[j] == l.data[i] {
                    continue;
                }
                met = true;
                let v: [f64; 4] = std::array::from_fn(|k| (pred[j][k] - pred[i][k]) * wt[k]);
                let vv: f64 = v.iter().map(|x| x * x).sum();
                if vv <= 0.0 {
                    continue;
                }
                let t = d.iter().zip(v.iter()).map(|(p, q)| p * q).sum::<f64>() / vv;
                if t.abs() > EDGE_SHARE {
                    continue;
                }
                let off: f64 = (0..4).map(|k| (d[k] - t * v[k]).powi(2)).sum();
                if off <= EDGE_CONE * EDGE_CONE * vv {
                    out.data[i] = true;
                    break 'rings;
                }
            }
            if met {
                break;
            }
        }
    }
    out
}

/// Promote connected components of high-residual interior pixels to new regions.
/// Returns the new labels and the ids of the rescued regions.
///
/// `explained` (from `edge_mix`) marks pixels a nearby edge accounts for; a
/// component they mostly make up is that edge's rendering, not a feature.
///
/// `core` is every region's fill core (`weights::fill_core`). A fill is not
/// fitted to its region's edge band, so it does not model the band: a
/// sharpening halo two or three pixels inside a glyph's edge disagrees with it
/// for the edge's reason, not because a stroke was swallowed. A component is
/// only evidence of a feature if it reaches into the core; edge-band pixels may
/// belong to one that does (the darkest band of a drop shadow runs right up to
/// its caster). `rescue.rescue_features` in the Python.
pub fn rescue_features(
    l: &Labels,
    residual: &Grid<f64>,
    threshold: f64,
    min_region: usize,
    explained: Option<&Mask>,
    core: Option<&Mask>,
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
    // A component the edge beside it mostly explains (more than half its
    // pixels) is that edge's rendering, unless what is left is a feature's
    // worth of pixels on its own. See `rescue.rescue_features`.
    let mut rim = vec![0usize; sizes.len()];
    if let Some(ex) = explained {
        for i in 0..comps.len() {
            if ex.data[i] && candidates.data[i] {
                rim[comps.data[i] as usize] += 1;
            }
        }
    }
    let floor = min_region.max(3);
    let mut reaches = vec![core.is_none(); sizes.len()];
    if let Some(core) = core {
        for i in 0..comps.len() {
            if core.data[i] && comps.data[i] > 0 {
                reaches[comps.data[i] as usize] = true;
            }
        }
    }
    let keep: Vec<usize> = (1..sizes.len())
        .filter(|i| {
            let rest = sizes[*i] - rim[*i];
            sizes[*i] >= floor && (rest >= floor || rest >= rim[*i]) && reaches[*i]
        })
        .collect();
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
