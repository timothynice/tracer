//! Stage 2: discontinuity map and the initial, edge-bounded partition.
//!
//! A discontinuity is a *ridge* of the colour gradient, not merely a large
//! gradient: a steep but smooth ramp has a large, flat gradient magnitude and
//! must stay one region, while a hard edge has a peak. Non-maximum suppression
//! along the local gradient orientation separates the two, as in Canny.

use crate::core::filters::{self, Mode};
use crate::core::grid::{Grid, Image, Mask};
use crate::core::labels::{self, Labels};
use crate::core::morphology::{dilate_cross, dilate_cross_n, erode_cross_n};
use rayon::prelude::*;
use crate::core::watershed::{watershed, watershed_masked};
use crate::timing::Timer;

/// Per-pixel colour change (ΔE-like units per pixel) across all feature channels.
pub fn discontinuity(features: &Image, sigma: f64) -> Grid<f64> {
    let (h, w) = (features.h, features.w);
    let mut g2 = Grid::<f64>::new(h, w);
    for c in 0..features.c {
        let g = filters::scharr(&features.channel(c));
        for i in 0..h * w {
            g2.data[i] = (g2.data[i] as f32 + (g.data[i] as f32) * (g.data[i] as f32)) as f64;
        }
    }
    let grad = Grid { h, w, data: g2.data.iter().map(|v| (*v as f32).sqrt() as f64).collect() };
    if sigma > 0.0 {
        filters::gaussian_filter_f32(&grad, sigma, Mode::Reflect, 0.0)
    } else {
        grad
    }
}

/// (ridge, valley) masks from non-maximum / non-minimum tests along the
/// structure-tensor gradient orientation.
pub fn ridges_and_valleys(features: &Image, grad: &Grid<f64>, g_low: f64) -> (Mask, Mask) {
    let mut t = Timer::new();
    let (h, w) = (grad.h, grad.w);
    let n = h * w;
    let mut jxx = Grid::<f64>::new(h, w);
    let mut jyy = Grid::<f64>::new(h, w);
    let mut jxy = Grid::<f64>::new(h, w);
    for c in 0..features.c {
        let ch = features.channel(c);
        let gy = filters::scharr_h(&ch);
        let gx = filters::scharr_v(&ch);
        for i in 0..n {
            jxx.data[i] = (jxx.data[i] as f32 + (gx.data[i] * gx.data[i]) as f32) as f64;
            jyy.data[i] = (jyy.data[i] as f32 + (gy.data[i] * gy.data[i]) as f32) as f64;
            jxy.data[i] = (jxy.data[i] as f32 + (gx.data[i] * gy.data[i]) as f32) as f64;
        }
    }
    t.lap("      ridges/scharr");
    let jxx = filters::gaussian_filter_f32(&jxx, 1.0, Mode::Reflect, 0.0);
    let jyy = filters::gaussian_filter_f32(&jyy, 1.0, Mode::Reflect, 0.0);
    let jxy = filters::gaussian_filter_f32(&jxy, 1.0, Mode::Reflect, 0.0);

    // One independent decision per pixel over four bilinear samples and a
    // trig call; at 768² that is the whole cost of the stage.
    let mut flags: Vec<u8> = vec![0; n];
    flags
        .par_chunks_mut(w)
        .enumerate()
        .for_each(|(r, row)| {
            for (c, slot) in row.iter_mut().enumerate() {
                let i = r * w + c;
                // The orientation and the sample offsets are computed in f32, as
                // the Python's float32 arrays are. At a plateau the near sample can
                // land within 1e-4 of the centre pixel, and `grad >= g_plus` then
                // turns on the last bit: carrying f64 here flips ridge pixels.
                let theta = 0.5f32
                    * (2.0f32 * jxy.data[i] as f32).atan2(jxx.data[i] as f32 - jyy.data[i] as f32);
                let (dx, dy) = (theta.cos(), theta.sin());
                let g = grad.data[i];
                let (rf, cf) = (r as f32, c as f32);
                // map_coordinates writes float32 for a float32 input, and the
                // comparisons below then happen in float32 too
                let at = |sy: f32, sx: f32| -> f32 {
                    filters::bilinear_nearest(grad, (rf + sy) as f64, (cf + sx) as f64) as f32
                };
                let gp = at(dy, dx);
                let gm = at(-dy, -dx);
                // Prominence is judged 1.5 px out: a real edge has fallen to ≈ 40 %
                // there, while the periodic bumps 8-bit quantisation puts on a steep
                // ramp are only a few percent high.
                let gpf = at(1.5f32 * dy, 1.5f32 * dx);
                let gmf = at(-1.5f32 * dy, -1.5f32 * dx);
                let g32 = g as f32;
                let prominent = g32 > 1.10f32 * gp.max(gm) || g32 > 1.10f32 * gpf.max(gmf);
                let is_ridge = g32 > g_low as f32 && g32 >= gp && g32 >= gm && prominent;
                let is_valley = g32 <= gp && g32 <= gm && !is_ridge;
                *slot = is_ridge as u8 | ((is_valley as u8) << 1);
            }
        });
    t.lap("      ridges/nms");
    let ridge = Grid { h, w, data: flags.iter().map(|f| f & 1 != 0).collect() };
    let valley = Grid { h, w, data: flags.iter().map(|f| f & 2 != 0).collect() };
    (ridge, valley)
}

/// Seeds: pixels clear of the ridge band, or valley pixels, that also have a
/// low gradient. NMS loses ridge pixels at T-junctions, and those gap pixels
/// still carry a high gradient, so the gradient test keeps neighbouring regions
/// from leaking into one seed.
pub fn seed_mask(features: &Image, grad: &Grid<f64>, g_low: f64, g_seed: f64) -> Mask {
    let (ridge, valley) = ridges_and_valleys(features, grad, g_low);
    let band = dilate_cross(&ridge);
    let near_band = dilate_cross_n(&band, 2);
    let mut out = Grid::filled(grad.h, grad.w, false);
    for i in 0..grad.len() {
        let leaky = near_band.data[i] && grad.data[i] >= g_seed;
        out.data[i] = (!band.data[i] && !leaky) || valley.data[i];
    }
    out
}

/// Flood regions below `min_region` pixels from their neighbours along low gradient.
fn absorb_small(labels: &Labels, grad: &Grid<f64>, min_region: usize, rounds: usize) -> Labels {
    let mut cur = labels.clone();
    for _ in 0..rounds {
        let sizes = labels::bincount(&cur);
        let small: Vec<bool> = sizes.iter().enumerate().map(|(i, s)| i != 0 && *s < min_region).collect();
        if !small.iter().any(|b| *b) {
            break;
        }
        let mut markers = cur.clone();
        let mut max = 0i32;
        for v in markers.data.iter_mut() {
            if *v >= 0 && small[*v as usize] {
                *v = 0;
            }
            if *v > max {
                max = *v;
            }
        }
        if max == 0 {
            return Grid::filled(cur.h, cur.w, 1);
        }
        cur = watershed(grad, &markers);
    }
    cur
}

/// How many cross erosions find the pieces a neck in a seed joins (`partition.py`).
pub const NECK_ERODE: usize = 3;
/// How far the boundary between two split pieces must stand above their
/// interiors for the split to be a step, not a ramp (`partition.rejoin_ramps`).
pub const NECK_PROMINENCE: f64 = 3.0;
/// ... and the share of the two markers' colour difference it must carry.
pub const NECK_STEP: f64 = 0.25;
/// Fewest pixels a piece needs, after the erosion, to be seeded apart.
pub const NECK_PIECE: usize = 64;

/// The watershed's markers: the seed mask's connected components of at least
/// `floor` pixels, each split where a neck joins areas more than `detail`
/// apart in mean feature colour (`partition.seed_markers`, which says why).
/// Numbered in raster order of each marker's first pixel; with, per marker
/// id, the seed component it was split from (0 for one seeded whole).
pub fn seed_markers(smooth: &Mask, floor: usize, features: &Image, detail: f64, grad: &Grid<f64>) -> (Labels, Vec<i64>) {
    let mut comps = labels::label_mask(smooth, 1);
    if comps.data.iter().copied().max().unwrap_or(0) == 0 {
        return (comps, vec![0]);
    }
    let sizes = labels::bincount(&comps);
    let keep: Vec<bool> = sizes.iter().enumerate().map(|(i, s)| i != 0 && *s >= floor).collect();
    for v in comps.data.iter_mut() {
        if *v > 0 && !keep[*v as usize] {
            *v = 0;
        }
    }
    let seeded = Grid { h: comps.h, w: comps.w, data: comps.data.iter().map(|v| *v > 0).collect() };
    let core = erode_cross_n(&seeded, NECK_ERODE, true);
    let pieces = labels::label_mask(&core, 1);
    let psize = labels::bincount(&pieces);
    let n_p = psize.len();
    let big: Vec<bool> = psize.iter().enumerate().map(|(i, s)| i != 0 && *s >= floor).collect();
    let mut owner = vec![0i32; n_p];
    for (p, c) in pieces.data.iter().zip(comps.data.iter()) {
        owner[*p as usize] = *c;
    }
    let mut count = vec![0usize; sizes.len()];
    let mut by_comp: Vec<Vec<usize>> = vec![Vec::new(); sizes.len()];
    for p in 0..n_p {
        if big[p] {
            count[owner[p] as usize] += 1;
            by_comp[owner[p] as usize].push(p);
        }
    }
    let mut out: Vec<i64> = comps.data.iter().map(|v| *v as i64).collect();
    let mut split = vec![false; sizes.len()];
    if count.iter().any(|n| *n >= 2) {
        // mean feature colour per piece, summed in raster order in f64
        let nc = features.c;
        let mut sums = vec![0.0f64; n_p * nc];
        for (i, p) in pieces.data.iter().enumerate() {
            let px = features.at(i / features.w, i % features.w);
            for ch in 0..nc {
                sums[*p as usize * nc + ch] += px[ch];
            }
        }
        let mean = |p: usize, ch: usize| sums[p * nc + ch] / (psize[p].max(1) as f64);
        let mut group = vec![0i64; n_p];
        let mut next_id = sizes.len() as i64;
        for c in 0..sizes.len() {
            if count[c] < 2 {
                continue;
            }
            let mut ps = by_comp[c].clone();
            ps.sort_by(|a, b| psize[*b].cmp(&psize[*a]).then(a.cmp(b)));
            let mut anchors: Vec<(usize, i64)> = Vec::new();
            for &p in &ps {
                let mut joined = false;
                for &(a, gid) in &anchors {
                    let mut d2 = 0.0f64;
                    for ch in 0..nc {
                        let d = mean(p, ch) - mean(a, ch);
                        d2 += d * d;
                    }
                    if d2 < detail * detail {
                        group[p] = gid;
                        joined = true;
                        break;
                    }
                }
                if !joined && (anchors.is_empty() || psize[p] >= NECK_PIECE) {
                    anchors.push((p, next_id));
                    group[p] = next_id;
                    next_id += 1;
                }
            }
            if anchors.len() >= 2 {
                split[c] = true;
            } else {
                for &p in &ps {
                    group[p] = 0;
                }
            }
        }
        // each group grown back over its component's own seed (`partition.py`)
        let inside = Grid { h: comps.h, w: comps.w, data: comps.data.iter().map(|c| split[*c as usize]).collect() };
        let ids = Grid {
            h: comps.h,
            w: comps.w,
            data: (0..out.len())
                .map(|i| if inside.data[i] { group[pieces.data[i] as usize] as i32 } else { 0 })
                .collect(),
        };
        let ids = watershed_masked(grad, &ids, Some(&inside));
        for i in 0..out.len() {
            if inside.data[i] {
                out[i] = ids.data[i] as i64;
            }
        }
    }
    // renumber 1..K in raster order of each id's first pixel
    let mut map: std::collections::HashMap<i64, i32> = std::collections::HashMap::new();
    let mut data = vec![0i32; out.len()];
    for (i, v) in out.iter().enumerate() {
        if *v == 0 {
            continue;
        }
        let next = map.len() as i32 + 1;
        data[i] = *map.entry(*v).or_insert(next);
    }
    let mut origin = vec![0i64; map.len() + 1];
    for (i, m) in data.iter().enumerate() {
        let c = comps.data[i];
        if *m > 0 && split[c as usize] {
            origin[*m as usize] = c as i64;
        }
    }
    (Grid { h: comps.h, w: comps.w, data }, origin)
}

/// Join again the basins of a split seed that no step divides
/// (`partition.rejoin_ramps`, which says why): two adjacent basins split from
/// one component stay apart only when the mean discontinuity along their
/// shared boundary is at least `NECK_PROMINENCE` times the lower-middle
/// discontinuity over either's marker and `NECK_STEP` times the distance
/// between the markers' mean feature colours. Boundary sums run horizontal pairs,
/// then vertical ones, each in raster order, as the Python's do.
pub fn rejoin_ramps(l: &Labels, markers: &Labels, origin: &[i64], grad: &Grid<f64>, features: &Image) -> Labels {
    if !origin.iter().any(|o| *o > 0) {
        return l.clone();
    }
    let k = origin.len();
    let mut vals: Vec<Vec<f64>> = vec![Vec::new(); k];
    for (m, g) in markers.data.iter().zip(grad.data.iter()) {
        if *m > 0 && origin[*m as usize] > 0 {
            vals[*m as usize].push(*g);
        }
    }
    let mut interior = vec![0.0f64; k];
    for (m, v) in vals.iter_mut().enumerate() {
        if v.is_empty() {
            continue;
        }
        v.sort_by(|a, b| a.total_cmp(b));
        interior[m] = v[(v.len() - 1) / 2];
    }
    // mean feature colour per marker, summed in raster order in f64
    let nc = features.c;
    let mut count = vec![0usize; k];
    let mut sums = vec![0.0f64; k * nc];
    for (i, m) in markers.data.iter().enumerate() {
        let m = *m as usize;
        count[m] += 1;
        let px = features.at(i / features.w, i % features.w);
        for ch in 0..nc {
            sums[m * nc + ch] += px[ch];
        }
    }
    let colour = |m: usize, ch: usize| sums[m * nc + ch] / (count[m].max(1) as f64);
    let (h, w) = (l.h, l.w);
    let mut acc: std::collections::BTreeMap<(i32, i32), (f64, f64)> = std::collections::BTreeMap::new();
    let mut visit = |a: i32, b: i32, g: f64| {
        if a != b && origin[a as usize] > 0 && origin[a as usize] == origin[b as usize] {
            let e = acc.entry((a.min(b), a.max(b))).or_insert((0.0, 0.0));
            e.0 += 1.0;
            e.1 += g;
        }
    };
    for r in 0..h {
        for c in 0..w.saturating_sub(1) {
            let i = r * w + c;
            visit(l.data[i], l.data[i + 1], 0.5 * (grad.data[i] + grad.data[i + 1]));
        }
    }
    for r in 0..h.saturating_sub(1) {
        for c in 0..w {
            let i = r * w + c;
            visit(l.data[i], l.data[i + w], 0.5 * (grad.data[i] + grad.data[i + w]));
        }
    }
    if acc.is_empty() {
        return l.clone();
    }
    let mut parent: Vec<usize> = (0..k).collect();
    fn find(parent: &[usize], mut i: usize) -> usize {
        while parent[i] != i {
            i = parent[i];
        }
        i
    }
    for ((a, b), (cnt, gsum)) in acc.iter() {
        let (a, b) = (*a as usize, *b as usize);
        let edge = gsum / cnt;
        let mut d2 = 0.0f64;
        for ch in 0..nc {
            let d = colour(a, ch) - colour(b, ch);
            d2 += d * d;
        }
        if edge >= NECK_PROMINENCE * interior[a].max(interior[b]) && edge >= NECK_STEP * d2.sqrt() {
            continue;
        }
        let (ra, rb) = (find(&parent, a), find(&parent, b));
        if ra != rb {
            parent[ra.max(rb)] = ra.min(rb);
        }
    }
    let root: Vec<i32> = (0..k).map(|i| find(&parent, i) as i32).collect();
    Grid { h, w, data: l.data.iter().map(|v| root[*v as usize]).collect() }
}

/// Watershed on the discontinuity map, seeded by the connected smooth areas.
pub fn initial_labels(grad: &Grid<f64>, features: &Image, min_region: usize, g_low: f64, detail: f64) -> Labels {
    let mut t = Timer::new();
    let smooth = seed_mask(features, grad, g_low, 8.0);
    t.lap("    labels/seed_mask");
    let (mut markers, mut origin) = seed_markers(&smooth, min_region.max(2), features, detail, grad);
    t.lap("    labels/cc");
    if markers.data.iter().copied().max().unwrap_or(0) == 0 {
        // No smooth area at all: seed from the local minima of the gradient so
        // the watershed still has basins.
        let mn = filters::minimum_filter3(grad);
        let minima = Grid {
            h: grad.h,
            w: grad.w,
            data: (0..grad.len()).map(|i| mn.data[i] == grad.data[i]).collect(),
        };
        markers = labels::label_mask(&minima, 1);
        if markers.data.iter().copied().max().unwrap_or(0) == 0 {
            return Grid::filled(grad.h, grad.w, 1);
        }
        origin = vec![0; markers.data.iter().copied().max().unwrap_or(0) as usize + 1];
    }
    let l = watershed(grad, &markers);
    t.lap("    labels/watershed");
    let l = rejoin_ramps(&l, &markers, &origin, grad, features);
    let l = absorb_small(&l, grad, min_region, 3);
    t.lap("    labels/absorb_small");
    labels::relabel_sequential(&l).0
}
