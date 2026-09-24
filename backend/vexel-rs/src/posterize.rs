//! Optional stage for `gradients=False`: cut each fitted gradient into flat bands.
//!
//! A designer posterising a gradient cuts the *gradient*, not the pixels: a
//! linear ramp into strips between parallel straight lines, a radial one into
//! rings between concentric circles. The trace runs as it would with gradients
//! on, so each ramp is one region with a fitted linear or radial fill; that fill
//! is cut at levels of its own ramp parameter t, spaced so every band spans the
//! same colour distance (ΔE, alpha at `ALPHA_FEATURE_SCALE`) and none more than
//! `detail` of it. Band membership is read from t at each pixel centre, never
//! from the pixel's colour, and the band edges are placed on the level lines
//! themselves (`Levels::crossing`, consulted by `topology::place`). See the
//! Python `posterize.py`, which this mirrors.

use crate::core::colour::rgb2lab;
use crate::core::filters::{correlate1d_x, correlate1d_y, gaussian_kernel1d, Accum, Mode};
use crate::core::grid::{Grid, Image, Mask};
use crate::core::labels::{self, LabelIndex, Labels};
use crate::curves::P;
use crate::fills::{fit_fill, interp_stops, Fill, FitParams};
use crate::prepare::ALPHA_FEATURE_SCALE;
use crate::weights::interior_at;
use std::collections::{BTreeMap, HashMap};

/// Narrowest band, in px across its level lines.
pub const BAND_MIN_PX: f64 = 4.0;
/// Samples along t used to measure the ramp's colour distance.
pub const RAMP_SAMPLES: usize = 256;
/// A band piece thinner than this (2·area / perimeter, px) or smaller than
/// `min_region` joins the neighbouring band it shares the most edge with.
pub const THIN_BAND: f64 = 2.0;
/// Does the model's family of level lines match the image's? In a band of at
/// least this many core pixels, the pixels at one t (one 1 px step of the
/// ramp) may spread by at most half a band step (RMS ΔE about their mean).
/// See the Python `FOLLOW_MIN`.
pub const FOLLOW_MIN: usize = 64;
/// A ramp whose lines are not the image's is cut where its pixels cross the
/// levels, after a Gaussian of this sigma within the region...
pub const SMOOTH_SIGMA: f64 = 1.5;
/// ...but only when that cut's band edges move by at most `SETTLED_PX` (pixels
/// changing band per pixel of band edge) when the smoothing is `SETTLED_SIGMA`.
pub const SETTLED_SIGMA: f64 = 3.0;
pub const SETTLED_PX: f64 = 0.25;

/// Where each band came from: its ramp, and the levels that bound it.
#[derive(Clone, Default)]
pub struct Levels {
    /// label -> (group, band index)
    pub band: HashMap<i32, (i32, usize)>,
    /// group -> its fitted linear or radial fill
    pub fields: HashMap<i32, Fill>,
    /// group -> t between band k and k+1, ascending
    pub levels: HashMap<i32, Vec<f64>>,
    /// group -> (row, col, t) for a ramp cut where its pixels cross the
    /// levels: each pixel's own t over the region's bounding box from (row,
    /// col), NaN outside the region
    pub observed: HashMap<i32, (usize, usize, Grid<f64>)>,
}

impl Levels {
    pub fn is_empty(&self) -> bool {
        self.band.is_empty()
    }

    fn between(&self, a: i32, b: i32) -> Option<(&Fill, f64)> {
        let (fa, fb) = (self.band.get(&a)?, self.band.get(&b)?);
        if fa.0 != fb.0 || fa.1.abs_diff(fb.1) != 1 {
            return None;
        }
        Some((&self.fields[&fa.0], self.levels[&fa.0][fa.1.min(fb.1)]))
    }

    /// `l` with every ramp's bands one region again (each labelled as its
    /// lowest band): the shapes the partition drew, before the cut.
    pub fn unbanded(&self, l: &Labels) -> Labels {
        if self.band.is_empty() {
            return l.clone();
        }
        let mut first: HashMap<i32, i32> = HashMap::new();
        let mut labs: Vec<i32> = self.band.keys().copied().collect();
        labs.sort_unstable();
        for lab in labs {
            first.entry(self.band[&lab].0).or_insert(lab);
        }
        let mut out = l.clone();
        for v in out.data.iter_mut() {
            if let Some((group, _)) = self.band.get(v) {
                *v = first[group];
            }
        }
        out
    }

    /// Are `a` and `b` consecutive bands of one ramp?
    pub fn consecutive(&self, a: i32, b: i32) -> bool {
        self.between(a, b).is_some()
    }

    /// Is the edge between `a` and `b` one of a ramp's own level lines (a line
    /// or a circle, which a node can be moved onto)?
    pub fn sibling(&self, a: i32, b: i32) -> bool {
        self.between(a, b).is_some() && !self.observed.contains_key(&self.band[&a].0)
    }

    /// The gradient a band was cut along; None for a band of a ramp whose
    /// level lines were not the image's.
    pub fn model(&self, lab: i32) -> Option<&Fill> {
        self.band.get(&lab).filter(|(g, _)| !self.observed.contains_key(g)).map(|(g, _)| &self.fields[g])
    }

    /// `point` moved onto the level line between bands `a` and `b`: across it,
    /// or along axis `along` only (0 = x, 1 = y). See the Python `Levels.onto`.
    pub fn onto(&self, a: i32, b: i32, point: P, along: Option<usize>) -> P {
        let Some((ramp, level)) = self.between(a, b) else { return point };
        let mut out = point;
        match ramp {
            Fill::Linear { x1, y1, x2, y2, .. } => {
                let d = [x2 - x1, y2 - y1];
                let t = ramp.param(out[0], out[1]);
                match along {
                    None => [out[0] + (level - t) * d[0], out[1] + (level - t) * d[1]],
                    Some(ax) => {
                        if d[ax].abs() < 1e-9 {
                            return out;
                        }
                        out[ax] += (level - t) * (d[0] * d[0] + d[1] * d[1]) / d[ax];
                        out
                    }
                }
            }
            Fill::Radial { cx, cy, r, .. } => {
                let centre = [*cx, *cy];
                let radius = level * r;
                let off = [out[0] - centre[0], out[1] - centre[1]];
                match along {
                    None => {
                        let dist = off[0].hypot(off[1]);
                        if dist < 1e-9 {
                            out
                        } else {
                            [centre[0] + off[0] * (radius / dist), centre[1] + off[1] * (radius / dist)]
                        }
                    }
                    Some(ax) => {
                        let fixed = 1 - ax;
                        let rest = radius * radius - off[fixed] * off[fixed];
                        if rest < 0.0 {
                            return out;
                        }
                        let root = rest.sqrt();
                        out[ax] = centre[ax] + if off[ax] >= 0.0 { root } else { -root };
                        out
                    }
                }
            }
            Fill::Solid { .. } => out,
        }
    }

    /// For a lattice edge between an `a` pixel centred at `c_a` and a `b` pixel
    /// at `c_b`: the fraction of the step from `c_a` to `c_b` at which the ramp
    /// crosses the level between the two bands (NaN where it does not cross
    /// there). None when `a` and `b` are not consecutive bands of one ramp.
    pub fn crossing(&self, a: i32, b: i32, c_a: P, c_b: P) -> Option<f64> {
        let (ramp, level) = self.between(a, b)?;
        let (t_a, t_b) = match self.observed.get(&self.band[&a].0) {
            None => (ramp.param(c_a[0], c_a[1]), ramp.param(c_b[0], c_b[1])),
            // the pixels' own t at the two pixel centres
            Some(seen) => (sample(seen, c_a), sample(seen, c_b)),
        };
        if !t_a.is_finite() || !t_b.is_finite() {
            return Some(f64::NAN);
        }
        let den = t_b - t_a;
        if den.abs() <= 1e-12 {
            return Some(f64::NAN);
        }
        let s = (level - t_a) / den;
        Some(if (0.0..=1.0).contains(&s) { s } else { f64::NAN })
    }
}

/// An observed t grid at pixel centre `c`: NaN off the region (a pixel the
/// wedge extension handed to a band from outside it).
fn sample(seen: &(usize, usize, Grid<f64>), c: P) -> f64 {
    let (r0, c0, grid) = seen;
    let (r, q) = (c[1].floor() as i64 - *r0 as i64, c[0].floor() as i64 - *c0 as i64);
    if r < 0 || q < 0 || r >= grid.h as i64 || q >= grid.w as i64 {
        return f64::NAN;
    }
    grid.data[r as usize * grid.w + q as usize]
}

fn features(c: &[f64; 4]) -> [f64; 4] {
    // `rgb2lab` takes 0..255 and runs the float32 path `prepare` takes
    let lab = rgb2lab(c[0], c[1], c[2]);
    [lab[0], lab[1], lab[2], c[3] / 255.0 * ALPHA_FEATURE_SCALE]
}

fn invert(length: &[f64], ts: &[f64], v: f64) -> f64 {
    // first index with length[i] >= v (`np.searchsorted(side="left")`)
    let i = length.partition_point(|x| *x < v).clamp(1, length.len() - 1);
    let (l0, l1) = (length[i - 1], length[i]);
    let f = if l1 > l0 { (v - l0) / (l1 - l0) } else { 0.0 };
    ts[i - 1] + f * (ts[i] - ts[i - 1])
}

/// The n−1 levels of t between n bands of equal colour distance, none over
/// `step` and none narrower than `BAND_MIN_PX` (a central disc by its
/// diameter), for a ramp seen over t in
/// [t_lo, t_hi]. Empty: one flat colour.
pub fn band_levels(ramp: &Fill, t_lo: f64, t_hi: f64, step: f64) -> Vec<f64> {
    let (lo, hi) = (t_lo.clamp(0.0, 1.0), t_hi.clamp(0.0, 1.0));
    let (reach, stops) = match ramp {
        Fill::Linear { x1, y1, x2, y2, stops } => ((x2 - x1).hypot(y2 - y1), stops),
        Fill::Radial { r, stops, .. } => (*r, stops),
        Fill::Solid { .. } => return Vec::new(),
    };
    let span_px = (hi - lo) * reach;
    if hi - lo <= 1e-9 {
        return Vec::new();
    }
    let ts: Vec<f64> = (0..=RAMP_SAMPLES).map(|i| lo + (hi - lo) * i as f64 / RAMP_SAMPLES as f64).collect();
    let feat: Vec<[f64; 4]> = ts.iter().map(|t| features(&interp_stops(*t, stops))).collect();
    let mut length = Vec::with_capacity(ts.len());
    length.push(0.0);
    let mut acc = 0.0;
    for k in 1..feat.len() {
        acc += (0..4).map(|c| (feat[k][c] - feat[k - 1][c]).powi(2)).sum::<f64>().sqrt();
        length.push(acc);
    }
    let total = acc;
    // Equal colour distances crowd where the ramp is steep, so the count comes
    // down until no band is narrower than BAND_MIN_PX; a radial's innermost band
    // is a disc, as wide as its diameter, when the region holds the centre.
    let disc = matches!(ramp, Fill::Radial { .. }) && lo * reach <= 1.0;
    let most = ((total / step.max(1e-9) - 1e-9).ceil() as i64).min((span_px / BAND_MIN_PX).floor() as i64);
    for n in (2..=most).rev() {
        let levels: Vec<f64> = (1..n).map(|j| invert(&length, &ts, j as f64 * total / n as f64)).collect();
        let mut edges = Vec::with_capacity(levels.len() + 2);
        edges.push(lo);
        edges.extend_from_slice(&levels);
        edges.push(hi);
        let mut widths: Vec<f64> = edges.windows(2).map(|e| (e[1] - e[0]) * reach).collect();
        if disc {
            widths[0] = 2.0 * levels[0] * reach;
        }
        if widths.iter().copied().fold(f64::INFINITY, f64::min) >= BAND_MIN_PX - 1e-9 {
            return levels;
        }
    }
    Vec::new()
}

fn reach_of(ramp: &Fill) -> f64 {
    match ramp {
        Fill::Linear { x1, y1, x2, y2, .. } => (x2 - x1).hypot(y2 - y1),
        Fill::Radial { r, .. } => *r,
        Fill::Solid { .. } => 1.0,
    }
}

/// Are the ramp's level lines the image's? `t` and `feat` are the region's
/// pixels' (raster order), `core` which of them are past the rim: false when,
/// in some band, the pixels at one t spread by more than half of `step`. See
/// the Python `follows`.
pub fn follows(ramp: &Fill, t: &[f64], feat: &[[f64; 4]], core: &[bool], levels: &[f64], step: f64) -> bool {
    let reach = reach_of(ramp);
    let idx: Vec<usize> = (0..t.len()).filter(|k| core[*k]).collect();
    if idx.is_empty() {
        return true;
    }
    let t_min = idx.iter().map(|k| t[*k]).fold(f64::INFINITY, f64::min);
    let at: Vec<usize> = idx.iter().map(|k| ((t[*k] - t_min) * reach).floor() as usize).collect();
    let nb = at.iter().copied().max().unwrap_or(0) + 1;
    let mut count = vec![0.0f64; nb];
    let mut mean = vec![[0.0f64; 4]; nb];
    for (j, k) in idx.iter().enumerate() {
        count[at[j]] += 1.0;
        for c in 0..4 {
            mean[at[j]][c] += feat[*k][c];
        }
    }
    for b in 0..nb {
        let n = count[b].max(1.0);
        for c in 0..4 {
            mean[b][c] /= n;
        }
    }
    let mut n = vec![0usize; levels.len() + 1];
    let mut spread = vec![0.0f64; levels.len() + 1];
    for (j, k) in idx.iter().enumerate() {
        let d: Vec<f64> = (0..4).map(|c| feat[*k][c] - mean[at[j]][c]).collect();
        let dev = ((d[0] * d[0] + d[1] * d[1]) + d[2] * d[2]) + d[3] * d[3];
        let band = levels.partition_point(|v| *v <= t[*k]);
        n[band] += 1;
        spread[band] += dev;
    }
    let bar = (0.5 * step) * (0.5 * step);
    !(0..=levels.len()).any(|k| n[k] >= FOLLOW_MIN && spread[k] / n[k] as f64 > bar)
}

/// Do the band edges of a cut at the pixels' own t (`fine`, raster order over
/// the region `m`) stay put when the smoothing doubles (`coarse`)? See
/// `SETTLED_PX`.
pub fn settled(fine: &[f64], coarse: &[f64], levels: &[f64], m: &Mask) -> bool {
    let mut k = vec![-1i64; m.h * m.w];
    let mut j = 0;
    let mut moved = 0usize;
    for (i, inside) in m.data.iter().enumerate() {
        if *inside {
            let a = levels.partition_point(|v| *v <= fine[j]) as i64;
            let b = levels.partition_point(|v| *v <= coarse[j]) as i64;
            k[i] = a;
            moved += (a != b) as usize;
            j += 1;
        }
    }
    let mut edge = 0usize;
    for r in 0..m.h {
        for c in 0..m.w {
            let here = k[r * m.w + c];
            if here < 0 {
                continue;
            }
            if c + 1 < m.w && k[r * m.w + c + 1] >= 0 && k[r * m.w + c + 1] != here {
                edge += 1;
            }
            if r + 1 < m.h && k[(r + 1) * m.w + c] >= 0 && k[(r + 1) * m.w + c] != here {
                edge += 1;
            }
        }
    }
    edge > 0 && moved as f64 <= SETTLED_PX * edge as f64
}

fn premultiplied(c: &[f64; 4]) -> [f64; 4] {
    let a = c[3] / 255.0;
    [c[0] * a, c[1] * a, c[2] * a, c[3]]
}

fn d2(p: &[f64; 4], q: &[f64; 4]) -> f64 {
    let d = [p[0] - q[0], p[1] - q[1], p[2] - q[2], p[3] - q[3]];
    ((d[0] * d[0] + d[1] * d[1]) + d[2] * d[2]) + d[3] * d[3]
}

/// `scipy.ndimage.gaussian_filter(g, sigma, mode="constant")` on float64, bit
/// for bit (`Accum::Exact`, rows then columns).
fn gaussian_exact(g: &Grid<f64>, sigma: f64) -> Grid<f64> {
    let w = gaussian_kernel1d(sigma);
    correlate1d_x(&correlate1d_y(g, &w, Mode::Constant, 0.0, Accum::Exact), &w, Mode::Constant, 0.0, Accum::Exact)
}

/// Each of the region's pixels' own position t along the ramp (raster order
/// over `m`, the region in the crop `rgba`): the nearest point of the ramp's
/// colour curve over [lo, hi] to the pixel's colour after a Gaussian of
/// `sigma` within the region, both premultiplied. See the Python `observed_t`.
pub fn observed_t(ramp: &Fill, lo: f64, hi: f64, rgba: &[[f64; 4]], m: &Mask, sigma: f64) -> Vec<f64> {
    let stops = match ramp {
        Fill::Linear { stops, .. } | Fill::Radial { stops, .. } => stops,
        Fill::Solid { .. } => return vec![0.0; m.data.iter().filter(|v| **v).count()],
    };
    let inside = Grid { h: m.h, w: m.w, data: m.data.iter().map(|v| if *v { 1.0 } else { 0.0 }).collect() };
    let den = gaussian_exact(&inside, sigma);
    let mut num: Vec<Grid<f64>> = Vec::with_capacity(4);
    for ch in 0..4 {
        let g = Grid {
            h: m.h,
            w: m.w,
            data: (0..m.h * m.w).map(|i| if m.data[i] { premultiplied(&rgba[i])[ch] } else { 0.0 }).collect(),
        };
        num.push(gaussian_exact(&g, sigma));
    }
    let ts: Vec<f64> = (0..=RAMP_SAMPLES).map(|i| lo + (hi - lo) * i as f64 / RAMP_SAMPLES as f64).collect();
    let curve: Vec<[f64; 4]> = ts.iter().map(|t| premultiplied(&interp_stops(*t, stops))).collect();
    let mut out = Vec::new();
    for i in 0..m.h * m.w {
        if !m.data[i] {
            continue;
        }
        let c = [num[0].data[i] / den.data[i], num[1].data[i] / den.data[i], num[2].data[i] / den.data[i], num[3].data[i] / den.data[i]];
        let mut near = 0usize;
        let mut best = f64::INFINITY;
        for (j, q) in curve.iter().enumerate() {
            let d = d2(&c, q);
            if d < best {
                best = d;
                near = j;
            }
        }
        // the nearest point of the two segments either side of the nearest sample
        let mut best_t = ts[near];
        let mut best_d = best;
        for j0 in [near as i64 - 1, near as i64] {
            if j0 < 0 || j0 >= RAMP_SAMPLES as i64 {
                continue;
            }
            let j = j0 as usize;
            let (p0, p1) = (curve[j], curve[j + 1]);
            let e = [p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2], p1[3] - p0[3]];
            let f = [c[0] - p0[0], c[1] - p0[1], c[2] - p0[2], c[3] - p0[3]];
            let ee = ((e[0] * e[0] + e[1] * e[1]) + e[2] * e[2]) + e[3] * e[3];
            let ef = ((e[0] * f[0] + e[1] * f[1]) + e[2] * f[2]) + e[3] * f[3];
            let u = if ee > 0.0 { (ef / ee).clamp(0.0, 1.0) } else { 0.0 };
            let q = [p0[0] + u * e[0], p0[1] + u * e[1], p0[2] + u * e[2], p0[3] + u * e[3]];
            let dist = d2(&c, &q);
            if dist < best_d {
                best_d = dist;
                best_t = ts[j] + u * (ts[j + 1] - ts[j]);
            }
        }
        out.push(best_t);
    }
    out
}

/// Which band pieces (1..=count in `pieces`, a crop, 0 elsewhere) join which
/// neighbour: every piece smaller than `min_region` or thinner than
/// `THIN_BAND`, smallest first, into the sibling it shares the most lattice
/// edges with (the lower label on a tie). Returns {piece: absorber}.
fn absorb(pieces: &Grid<i32>, count: usize, min_region: usize) -> BTreeMap<i32, i32> {
    let mut size = vec![0usize; count + 1];
    let mut perim = vec![0i64; count + 1];
    let mut shared: BTreeMap<(i32, i32), i64> = BTreeMap::new();
    let (h, w) = (pieces.h, pieces.w);
    let at = |r: i64, c: i64| -> i32 {
        if r < 0 || c < 0 || r >= h as i64 || c >= w as i64 {
            0
        } else {
            pieces.data[r as usize * w + c as usize]
        }
    };
    for v in &pieces.data {
        if *v > 0 {
            size[*v as usize] += 1;
        }
    }
    // every lattice edge of the crop padded by one, horizontal then vertical
    for r in -1..=h as i64 {
        for c in -1..=w as i64 {
            for (dr, dc) in [(0i64, 1i64), (1, 0)] {
                let (r2, c2) = (r + dr, c + dc);
                if r2 > h as i64 || c2 > w as i64 {
                    continue;
                }
                let (a, b) = (at(r, c), at(r2, c2));
                if a == b {
                    continue;
                }
                if a > 0 {
                    perim[a as usize] += 1;
                }
                if b > 0 {
                    perim[b as usize] += 1;
                }
                if a > 0 && b > 0 {
                    *shared.entry((a.min(b), a.max(b))).or_insert(0) += 1;
                }
            }
        }
    }
    let mut alive: Vec<bool> = vec![true; count + 1];
    alive[0] = false;
    let mut into: BTreeMap<i32, i32> = BTreeMap::new();
    let weak = |k: usize, size: &[usize], perim: &[i64]| -> bool {
        size[k] < min_region || (2.0 * size[k] as f64 / perim[k].max(1) as f64) < THIN_BAND
    };
    loop {
        let mut cands: Vec<(usize, i32)> =
            (1..=count).filter(|k| alive[*k] && weak(*k, &size, &perim)).map(|k| (size[k], k as i32)).collect();
        cands.sort_unstable();
        let mut moved = false;
        for (_, k) in cands {
            let nbrs: Vec<(i64, i32)> = shared
                .iter()
                .filter(|((x, y), c)| (*x == k || *y == k) && **c > 0)
                .map(|((x, y), c)| (*c, if *x == k { *y } else { *x }))
                .collect();
            if nbrs.is_empty() {
                continue;
            }
            let best = nbrs.iter().map(|(c, _)| *c).max().unwrap();
            let d = nbrs.iter().filter(|(c, _)| *c == best).map(|(_, o)| *o).min().unwrap();
            size[d as usize] += size[k as usize];
            perim[d as usize] += perim[k as usize] - 2 * shared[&(k.min(d), k.max(d))];
            let touching: Vec<((i32, i32), i64)> =
                shared.iter().filter(|((x, y), _)| *x == k || *y == k).map(|(key, c)| (*key, *c)).collect();
            for ((x, y), c) in touching {
                shared.remove(&(x, y));
                let o = if x == k { y } else { x };
                if o != d {
                    *shared.entry((o.min(d), o.max(d))).or_insert(0) += c;
                }
            }
            alive[k as usize] = false;
            into.insert(k, d);
            moved = true;
            break;
        }
        if !moved {
            break;
        }
    }
    // resolve chains (a piece absorbed into one absorbed later)
    let mut out = BTreeMap::new();
    for (k, d) in &into {
        let mut d = *d;
        while let Some(next) = into.get(&d) {
            d = *next;
        }
        out.insert(*k, d);
    }
    out
}

/// Every gradient fill cut into flat bands along its own level lines. Returns
/// the new labels (compact, in the order the regions had, each ramp's bands in
/// order along it), a solid fill and the visibility for every label, and the
/// `Levels` the bands were cut at. A gradient too short to cut becomes its
/// core's flat colour. See the Python `posterize_fills`.
#[allow(clippy::too_many_arguments)]
pub fn posterize_fills(
    l: &Labels,
    fills: &HashMap<i32, Fill>,
    visible: &HashMap<i32, bool>,
    xs: &Grid<f64>,
    ys: &Grid<f64>,
    rgba255: &[[f64; 4]],
    features: &Image,
    step: f64,
    min_region: usize,
) -> (Labels, HashMap<i32, Fill>, HashMap<i32, bool>, Levels) {
    let (h, w) = (l.h, l.w);
    let solid = FitParams { gradients: false, max_stops: 4, tol: 4.0 };
    let index = LabelIndex::build(l);
    let mut out = Grid::<i32>::new(h, w);
    let mut new_fills = HashMap::new();
    let mut new_visible = HashMap::new();
    let mut lv = Levels::default();
    let mut next_id = 1i32;
    for lab in 1..=index.max_label() {
        let px = index.pixels(lab);
        if px.is_empty() {
            continue;
        }
        let mut fill = fills[&lab].clone();
        let mut levels = Vec::new();
        let t: Vec<f64> = px.iter().map(|i| fill.param(xs.data[*i as usize], ys.data[*i as usize])).collect();
        // A thin region's ramp is its anti-aliasing along a line: never cut.
        if !matches!(fill, Fill::Solid { .. }) && !crate::strokes::is_thin_at(h, w, px) {
            let (lo, hi) = t.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(a, b), v| (a.min(*v), b.max(*v)));
            levels = band_levels(&fill, lo, hi, step);
        }
        if levels.is_empty() {
            if !matches!(fill, Fill::Solid { .. }) {
                let (wt, core) = interior_at(l, lab, px);
                let x: Vec<f64> = px.iter().map(|i| xs.data[*i as usize]).collect();
                let y: Vec<f64> = px.iter().map(|i| ys.data[*i as usize]).collect();
                let c: Vec<[f64; 4]> = px.iter().map(|i| rgba255[*i as usize]).collect();
                fill = fit_fill(&x, &y, &c, &solid, Some(&wt), Some(&core));
            }
            for i in px {
                out.data[*i as usize] = next_id;
            }
            new_fills.insert(next_id, fill);
            new_visible.insert(next_id, visible[&lab]);
            next_id += 1;
            continue;
        }
        // the region's bounding box, where the pieces are numbered
        let (mut r0, mut r1, mut c0, mut c1) = (usize::MAX, 0usize, usize::MAX, 0usize);
        for i in px {
            let (r, c) = (*i as usize / w, *i as usize % w);
            r0 = r0.min(r);
            r1 = r1.max(r + 1);
            c0 = c0.min(c);
            c1 = c1.max(c + 1);
        }
        let (bh, bw) = (r1 - r0, c1 - c0);
        let local = |i: u32| -> usize { (i as usize / w - r0) * bw + (i as usize % w - c0) };
        // Are the model's level lines the image's? If not, and the pixels' own
        // are settled, the ramp is cut along those (see the Python).
        let (_, core) = interior_at(l, lab, px);
        let (t_min, t_max) = t.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(a, b), v| (a.min(*v), b.max(*v)));
        let (lo, hi) = (t_min.clamp(0.0, 1.0), t_max.clamp(0.0, 1.0));
        let feat: Vec<[f64; 4]> = px
            .iter()
            .map(|i| {
                let f = features.px(*i as usize);
                [f[0], f[1], f[2], f[3]]
            })
            .collect();
        let mut t = t;
        let mut free = false;
        if !follows(&fill, &t, &feat, &core, &levels, step) {
            let mut m = Mask { h: bh, w: bw, data: vec![false; bh * bw] };
            let mut crop = vec![[0.0f64; 4]; bh * bw];
            for i in px {
                m.data[local(*i)] = true;
                crop[local(*i)] = rgba255[*i as usize];
            }
            let fine = observed_t(&fill, lo, hi, &crop, &m, SMOOTH_SIGMA);
            let coarse = observed_t(&fill, lo, hi, &crop, &m, SETTLED_SIGMA);
            if settled(&fine, &coarse, &levels, &m) {
                free = true;
                // `fine` is in raster order over the crop, which is `px`'s order
                let mut grid = Grid::<f64>::filled(bh, bw, f64::NAN);
                for (k, i) in px.iter().enumerate() {
                    grid.data[local(*i)] = fine[k];
                }
                lv.observed.insert(lab, (r0, c0, grid));
                t = fine;
            }
        }
        // band index per pixel: how many levels lie at or below its t
        let mut k_of = Grid::<i32>::filled(bh, bw, -1);
        for (k, i) in px.iter().enumerate() {
            k_of.data[local(*i)] = levels.partition_point(|v| *v <= t[k]) as i32;
        }
        let mut pieces = Grid::<i32>::new(bh, bw);
        let mut piece_band: Vec<usize> = vec![0];
        let mut count = 0usize;
        for k in 0..=levels.len() {
            let m = Mask { h: bh, w: bw, data: k_of.data.iter().map(|v| *v == k as i32).collect() };
            let cc = labels::label_mask(&m, 1);
            let n = cc.data.iter().copied().max().unwrap_or(0).max(0) as usize;
            for (j, v) in cc.data.iter().enumerate() {
                if *v > 0 {
                    pieces.data[j] = *v + count as i32;
                }
            }
            piece_band.extend(std::iter::repeat_n(k, n));
            count += n;
        }
        let joined = absorb(&pieces, count, min_region);
        for v in pieces.data.iter_mut() {
            if let Some(d) = joined.get(v) {
                *v = *d;
            }
        }
        // the ramp's mean over each band, or for a ramp cut along its pixels'
        // own lines the pixels' mean, alpha-weighted: see the Python
        let mut sums: Vec<[f64; 4]> = vec![[0.0; 4]; count + 1];
        let mut weighted: Vec<[f64; 4]> = vec![[0.0; 4]; count + 1];
        let mut counts = vec![0usize; count + 1];
        for i in px {
            let piece = pieces.data[local(*i)] as usize;
            let c = if free { rgba255[*i as usize] } else { fill.evaluate_one(xs.data[*i as usize], ys.data[*i as usize]) };
            for ch in 0..4 {
                sums[piece][ch] += c[ch];
            }
            for ch in 0..3 {
                weighted[piece][ch] += c[ch] * c[3];
            }
            weighted[piece][3] += c[3];
            counts[piece] += 1;
        }
        let mut id_of = vec![0i32; count + 1];
        for k in 1..=count {
            if joined.contains_key(&(k as i32)) {
                continue;
            }
            id_of[k] = next_id;
            let n = counts[k].max(1) as f64;
            let mut rgba = [sums[k][0] / n, sums[k][1] / n, sums[k][2] / n, sums[k][3] / n];
            if free && weighted[k][3] > 0.0 {
                let a = weighted[k][3];
                rgba = [weighted[k][0] / a, weighted[k][1] / a, weighted[k][2] / a, sums[k][3] / n];
            }
            new_fills.insert(next_id, Fill::Solid { rgba });
            new_visible.insert(next_id, visible[&lab]);
            lv.band.insert(next_id, (lab, piece_band[k]));
            next_id += 1;
        }
        for i in px {
            out.data[*i as usize] = id_of[pieces.data[local(*i)] as usize];
        }
        lv.fields.insert(lab, fill);
        lv.levels.insert(lab, levels);
    }
    (out, new_fills, new_visible, lv)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fills::Stop;

    fn ramp() -> Fill {
        Fill::Linear {
            x1: 0.0,
            y1: 0.0,
            x2: 100.0,
            y2: 0.0,
            stops: vec![
                Stop { offset: 0.0, rgba: [0.0, 0.0, 0.0, 255.0] },
                Stop { offset: 1.0, rgba: [255.0, 255.0, 255.0, 255.0] },
            ],
        }
    }

    #[test]
    fn a_ramp_is_cut_into_bands_of_equal_colour_distance_none_over_the_step() {
        let levels = band_levels(&ramp(), 0.0, 1.0, 14.0);
        // black to white is 100 ΔE: eight bands
        assert_eq!(levels.len(), 7);
        assert!(levels.windows(2).all(|p| p[1] > p[0]));
    }

    #[test]
    fn a_short_ramp_gets_bands_no_narrower_than_the_minimum() {
        // 20 px of ramp and a band asked for every ΔE: grey is uneven in Lab,
        // so equal colour distances are unequal widths, and none may be a sliver
        let levels = band_levels(&ramp(), 0.0, 0.2, 1.0);
        assert!(!levels.is_empty());
        let mut edges = vec![0.0];
        edges.extend_from_slice(&levels);
        edges.push(0.2);
        assert!(edges.windows(2).all(|e| (e[1] - e[0]) * 100.0 >= BAND_MIN_PX - 1e-9), "{edges:?}");
    }

    #[test]
    fn a_band_edge_crosses_its_lattice_edge_exactly_on_the_level() {
        let mut lv = Levels::default();
        lv.band.insert(1, (7, 0));
        lv.band.insert(2, (7, 1));
        lv.band.insert(3, (7, 2));
        lv.fields.insert(7, ramp());
        lv.levels.insert(7, vec![0.3025, 0.6]);
        let s = lv.crossing(1, 2, [30.0, 5.5], [31.0, 5.5]).unwrap();
        assert!((s - 0.25).abs() < 1e-12, "{s}");
        assert!(lv.crossing(1, 3, [30.0, 5.5], [31.0, 5.5]).is_none());
        assert!(lv.crossing(2, 3, [30.0, 5.5], [31.0, 5.5]).unwrap().is_nan());
        let p = lv.onto(1, 2, [29.0, 3.0], None);
        assert!((p[0] - 30.25).abs() < 1e-9 && (p[1] - 3.0).abs() < 1e-12, "{p:?}");
    }
}
