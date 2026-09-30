//! Stroke recovery: thin regions become stroked centreline paths.
//!
//! A one- or two-pixel line traced as a *filled* region is a sliver of
//! semi-transparent fill — visually close, but not what the artist drew. When a
//! region is thin everywhere we recover the centreline with the medial axis,
//! estimate the stroke width from ink area ÷ centreline length (so anti-aliased
//! sub-pixel lines come out at their true width), fit the centreline with
//! curves and emit `<path fill="none" stroke=… stroke-width=…>`.

use crate::core::edt::edt;
use crate::core::grid::{Grid, Mask};
use crate::core::skeleton::medial_axis;
use crate::curves::{fit_closed_smooth, fit_open, path_d, CurveParams, P};
use std::collections::{BTreeSet, HashMap, HashSet};

const OFFSETS: [(isize, isize); 8] =
    [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)];

pub struct Stroke {
    pub polylines: Vec<Vec<P>>,
    pub closed: Vec<bool>,
    /// "butt" | "round" per polyline (ignored when closed)
    pub caps: Vec<&'static str>,
    pub width: f64,
}

/// Nearest-pixel coverage sample in SVG space (pixel centres at +0.5).
fn sample(field: &Grid<f64>, x: f64, y: f64) -> f64 {
    let r = y.floor();
    let c = x.floor();
    if r < 0.0 || c < 0.0 || r >= field.h as f64 || c >= field.w as f64 {
        return 0.0;
    }
    field.data[r as usize * field.w + c as usize]
}

/// Bilinear coverage sample in SVG space (pixel centres at +0.5, zero beyond
/// the last centres); the Python's `strokes._sample_bilinear`.
fn sample_bilinear(field: &Grid<f64>, x: f64, y: f64) -> f64 {
    let (fx, fy) = (x - 0.5, y - 0.5);
    let (x0, y0) = (fx.floor(), fy.floor());
    let (tx, ty) = (fx - x0, fy - y0);
    let at = |r: f64, c: f64| -> f64 {
        if r < 0.0 || c < 0.0 || r >= field.h as f64 || c >= field.w as f64 {
            0.0
        } else {
            field.data[r as usize * field.w + c as usize]
        }
    };
    let top = at(y0, x0) * (1.0 - tx) + at(y0, x0 + 1.0) * tx;
    let bottom = at(y0 + 1.0, x0) * (1.0 - tx) + at(y0 + 1.0, x0 + 1.0) * tx;
    top * (1.0 - ty) + bottom * ty
}

/// The centreline is refined to the coverage's centroid across the stroke,
/// sampled bilinearly every REFINE_STEP px out to REFINE_REACH px past the
/// half width, REFINE_ITER times (`strokes.py` has the measurements).
const REFINE_STEP: f64 = 0.25;
const REFINE_REACH: f64 = 1.0;
const REFINE_ITER: usize = 2;

/// Move every vertex along its normal to the centroid of the coverage across
/// the stroke there: the medial axis is a pixel path, half a pixel off the
/// middle of a two-pixel line on whichever side the thinning order chose, and
/// the coverage says where the middle is to a fraction of a pixel.
fn refine_centreline(xy: &[P], closed: bool, coverage: &Grid<f64>, width: f64) -> Vec<P> {
    let n = xy.len();
    if n < 2 {
        return xy.to_vec();
    }
    let mut out = xy.to_vec();
    // truncated, as the Python's `int()`: at the 0.25 px width floor the
    // count is 4.5, which round() would take to 5 and Python's round to 4
    let steps = ((width / 2.0 + REFINE_REACH) / REFINE_STEP) as i64;
    let offsets: Vec<f64> = (-steps..=steps).map(|k| k as f64 * REFINE_STEP).collect();
    for _ in 0..REFINE_ITER {
        let cur = out.clone();
        for i in 0..n {
            let (prev, nxt) = if closed {
                (cur[(i + n - 1) % n], cur[(i + 1) % n])
            } else {
                (cur[i.saturating_sub(1)], cur[(i + 1).min(n - 1)])
            };
            let (tx, ty) = (nxt[0] - prev[0], nxt[1] - prev[1]);
            let length = tx.hypot(ty);
            if length < 1e-9 {
                continue;
            }
            let (nx, ny) = (-ty / length, tx / length);
            let mut total = 0.0;
            let mut moment = 0.0;
            for s in &offsets {
                let c = sample_bilinear(coverage, cur[i][0] + s * nx, cur[i][1] + s * ny);
                total += c;
                moment += s * c;
            }
            if total <= 1e-9 {
                continue;
            }
            let shift = moment / total;
            out[i] = [cur[i][0] + shift * nx, cur[i][1] + shift * ny];
        }
    }
    out
}

/// Extend the medial axis to the stroke's real end and pick the cap style.
///
/// The medial axis stops about w/2 short of a line's end. A butt-ended line has
/// ink at the corners of the extended tip; a round-ended one does not.
fn finish_ends(xy: &[P], width: f64, coverage: &Grid<f64>) -> (Vec<P>, &'static str) {
    if xy.len() < 2 || width <= 0.0 {
        return (xy.to_vec(), "round");
    }
    let mut out = xy.to_vec();
    let mut votes_butt = 0;
    for end in [0usize, 1] {
        let (pi, qi) = if end == 0 { (0, 1) } else { (out.len() - 1, out.len() - 2) };
        let p = out[pi];
        let q = out[qi];
        let mut t = [p[0] - q[0], p[1] - q[1]];
        let n = (t[0] * t[0] + t[1] * t[1]).sqrt();
        if n < 1e-9 {
            continue;
        }
        t = [t[0] / n, t[1] / n];
        let normal = [-t[1], t[0]];
        let tip = [p[0] + t[0] * width / 2.0, p[1] + t[1] * width / 2.0];
        let corner_ink: Vec<f64> = [-1.0f64, 1.0]
            .iter()
            .map(|s| {
                sample(
                    coverage,
                    tip[0] + s * normal[0] * 0.4 * width,
                    tip[1] + s * normal[1] * 0.4 * width,
                )
            })
            .collect();
        let centre_ink = sample(coverage, p[0] + t[0] * width * 0.35, p[1] + t[1] * width * 0.35);
        let min_corner = corner_ink.iter().copied().fold(f64::INFINITY, f64::min);
        if centre_ink > 0.3 && min_corner > 0.35 {
            votes_butt += 1;
            out[pi] = tip;
        } else if centre_ink > 0.3 {
            out[pi] = [p[0] + t[0] * width * 0.15, p[1] + t[1] * width * 0.15];
        }
    }
    (out, if votes_butt == 2 { "butt" } else { "round" })
}

/// `is_thin` for a region given as a pixel index list, so the caller does not
/// have to materialise a frame-sized mask per label.
pub fn is_thin_at(h: usize, w: usize, pixels: &[u32]) -> bool {
    const MAX_HALF_WIDTH: f64 = 1.75;
    const MIN_PIXELS: usize = 8;
    if pixels.len() < MIN_PIXELS {
        return false;
    }
    let (mut r0, mut r1, mut c0, mut c1) = (usize::MAX, 0usize, usize::MAX, 0usize);
    for i in pixels {
        let (r, c) = (*i as usize / w, *i as usize % w);
        r0 = r0.min(r);
        r1 = r1.max(r + 1);
        c0 = c0.min(c);
        c1 = c1.max(c + 1);
    }
    let _ = h;
    let (ch, cw) = (r1 - r0 + 2, c1 - c0 + 2);
    let mut crop = Grid::filled(ch, cw, false);
    for i in pixels {
        let (r, c) = (*i as usize / w, *i as usize % w);
        crop.data[(r - r0 + 1) * cw + (c - c0 + 1)] = true;
    }
    edt(&crop).data.iter().copied().fold(0.0, f64::max) <= MAX_HALF_WIDTH + 0.5
}

/// True when no pixel of the region is farther than `max_half_width` from its boundary.
pub fn is_thin(mask: &Mask) -> bool {
    const MAX_HALF_WIDTH: f64 = 1.75;
    const MIN_PIXELS: usize = 8;
    if mask.count() < MIN_PIXELS {
        return false;
    }
    let Some((r0, r1, c0, c1)) = mask.bbox() else { return false };
    let crop = mask.crop_pad(r0, r1, c0, c1, 1);
    let d = edt(&crop);
    d.data.iter().copied().fold(0.0, f64::max) <= MAX_HALF_WIDTH + 0.5
}

/// Split a skeleton into paths: endpoint→(endpoint|junction) chains, then cycles.
fn trace_skeleton(skel: &Mask) -> Vec<(Vec<(usize, usize)>, bool)> {
    let w = skel.w;
    let pts: BTreeSet<(usize, usize)> = (0..skel.len())
        .filter(|i| skel.data[*i])
        .map(|i| (i / w, i % w))
        .collect();
    if pts.is_empty() {
        return Vec::new();
    }
    let nbrs = |p: (usize, usize)| -> Vec<(usize, usize)> {
        OFFSETS
            .iter()
            .filter_map(|(dr, dc)| {
                let r = p.0 as isize + dr;
                let c = p.1 as isize + dc;
                if r < 0 || c < 0 {
                    return None;
                }
                let q = (r as usize, c as usize);
                if pts.contains(&q) {
                    Some(q)
                } else {
                    None
                }
            })
            .collect()
    };
    let degree: HashMap<(usize, usize), usize> = pts.iter().map(|p| (*p, nbrs(*p).len())).collect();
    let mut used: HashSet<((usize, usize), (usize, usize))> = HashSet::new();
    let edge = |a: (usize, usize), b: (usize, usize)| if a <= b { (a, b) } else { (b, a) };

    let mut paths: Vec<(Vec<(usize, usize)>, bool)> = Vec::new();
    let walk = |start: (usize, usize), first: (usize, usize), used: &mut HashSet<_>| -> Vec<(usize, usize)> {
        let mut path = vec![start, first];
        used.insert(edge(start, first));
        let (mut prev, mut cur) = (start, first);
        while degree[&cur] == 2 {
            let Some(nxt) = nbrs(cur).into_iter().find(|q| *q != prev) else { break };
            let e = edge(cur, nxt);
            if used.contains(&e) {
                break;
            }
            used.insert(e);
            path.push(nxt);
            prev = cur;
            cur = nxt;
        }
        path
    };

    // chains from endpoints and junctions
    for p in pts.iter() {
        if degree[p] == 2 {
            continue;
        }
        for q in nbrs(*p) {
            if !used.contains(&edge(*p, q)) {
                let path = walk(*p, q, &mut used);
                paths.push((path, false));
            }
        }
    }
    // remaining pure cycles
    for p in pts.iter() {
        if degree[p] != 2 {
            continue;
        }
        for q in nbrs(*p) {
            if used.contains(&edge(*p, q)) {
                continue;
            }
            let cyc = walk(*p, q, &mut used);
            if cyc.len() > 3 && cyc[cyc.len() - 1] == cyc[0] {
                paths.push((cyc[..cyc.len() - 1].to_vec(), true));
            } else if cyc.len() > 3 && nbrs(cyc[0]).contains(&cyc[cyc.len() - 1]) {
                paths.push((cyc, true));
            } else {
                paths.push((cyc, false));
            }
            break;
        }
    }
    paths
}

/// Moving average along the polyline: removes the medial axis' pixel zig-zag,
/// which would otherwise inflate the centreline length (and shrink the width).
fn smooth(xy: &[P], closed: bool) -> Vec<P> {
    const WINDOW: usize = 5;
    let n = xy.len();
    if n < WINDOW {
        return xy.to_vec();
    }
    let k = WINDOW / 2;
    if closed {
        let mut padded: Vec<P> = Vec::with_capacity(n + 2 * k);
        padded.extend_from_slice(&xy[n - k..]);
        padded.extend_from_slice(xy);
        padded.extend_from_slice(&xy[..k]);
        return (0..n)
            .map(|i| {
                let mut s = [0.0f64; 2];
                for j in 0..WINDOW {
                    s[0] += padded[i + j][0];
                    s[1] += padded[i + j][1];
                }
                [s[0] / WINDOW as f64, s[1] / WINDOW as f64]
            })
            .collect();
    }
    let mut out = xy.to_vec();
    for i in 1..n - 1 {
        let lo = i.saturating_sub(k);
        let hi = (i + k + 1).min(n);
        let m = (hi - lo) as f64;
        let mut s = [0.0f64; 2];
        for p in &xy[lo..hi] {
            s[0] += p[0];
            s[1] += p[1];
        }
        out[i] = [s[0] / m, s[1] / m];
    }
    out
}

fn polyline_length(xy: &[P], closed: bool) -> f64 {
    let mut s = 0.0;
    for i in 1..xy.len() {
        s += (xy[i][0] - xy[i - 1][0]).hypot(xy[i][1] - xy[i - 1][1]);
    }
    if closed && xy.len() > 1 {
        let last = xy[xy.len() - 1];
        s += (last[0] - xy[0][0]).hypot(last[1] - xy[0][1]);
    }
    s
}

/// Centreline polylines (SVG pixel space) and width for a thin region.
///
/// `coverage` is the per-pixel ink coverage of the region (0–1), e.g. alpha
/// against a transparent background or the anti-aliasing coverage field.
pub fn stroke_geometry(mask: &Mask, coverage: &Grid<f64>) -> Option<Stroke> {
    const MIN_LENGTH: f64 = 3.0;
    let (r0, r1, c0, c1) = mask.bbox()?;
    let crop = mask.crop_pad(r0, r1, c0, c1, 1);
    let skel_padded = medial_axis(&crop);
    let mut skel = Grid::filled(r1 - r0, c1 - c0, false);
    for r in 0..skel.h {
        for c in 0..skel.w {
            skel.data[r * skel.w + c] = skel_padded.data[(r + 1) * crop.w + (c + 1)];
        }
    }
    if !skel.any() {
        return None;
    }
    let paths = trace_skeleton(&skel);
    if paths.is_empty() {
        return None;
    }
    // All ink the caller attributed to this stroke (the coverage field may
    // extend one pixel beyond `mask` to catch faint anti-aliased pixels).
    let ink_area: f64 = coverage.data.iter().sum();

    let mut polylines: Vec<Vec<P>> = Vec::new();
    let mut closed: Vec<bool> = Vec::new();
    let mut total_len = 0.0;
    for (pts, is_cycle) in paths {
        let xy: Vec<P> = pts
            .iter()
            .map(|(r, c)| [(*c + c0) as f64 + 0.5, (*r + r0) as f64 + 0.5])
            .collect();
        let xy = smooth(&xy, is_cycle);
        total_len += polyline_length(&xy, is_cycle);
        polylines.push(xy);
        closed.push(is_cycle);
    }
    if total_len < MIN_LENGTH {
        return None;
    }
    let mut width = (ink_area / total_len).max(0.25);
    // the skeleton's pixel path is put on the coverage's middle; the length,
    // and so the width, are then those of the line actually drawn
    let polylines: Vec<Vec<P>> = polylines
        .iter()
        .zip(closed.iter())
        .map(|(xy, c)| refine_centreline(xy, *c, coverage, width))
        .collect();
    let total_len: f64 = polylines.iter().zip(closed.iter()).map(|(xy, c)| polyline_length(xy, *c)).sum();
    if total_len < MIN_LENGTH {
        return None;
    }
    width = (ink_area / total_len).max(0.25);
    // Stroke only when a filled region would serve badly: sub-pixel / one-pixel
    // lines, or genuinely line-like features. Short thick pieces such as letter
    // stems stay filled shapes.
    if total_len < 4.0 * width || (width >= 1.5 && total_len < 8.0 * width) {
        return None;
    }
    // drop spurs much shorter than the stroke is wide (medial-axis artefacts)
    let keep: Vec<usize> = (0..polylines.len())
        .filter(|i| closed[*i] || polyline_length(&polylines[*i], false) >= MIN_LENGTH.max(1.5 * width))
        .collect();
    if keep.is_empty() {
        return None;
    }
    // the width was measured against the un-extended centreline; re-estimate
    // after extending open ends so ink area / length stays consistent
    let mut finished: Vec<Vec<P>> = Vec::new();
    let mut caps: Vec<&'static str> = Vec::new();
    let mut kept_closed: Vec<bool> = Vec::new();
    for i in &keep {
        if closed[*i] {
            finished.push(polylines[*i].clone());
            caps.push("round");
        } else {
            let (xy, cap) = finish_ends(&polylines[*i], width, coverage);
            finished.push(xy);
            caps.push(cap);
        }
        kept_closed.push(closed[*i]);
    }
    let new_len: f64 = finished
        .iter()
        .zip(kept_closed.iter())
        .map(|(xy, c)| polyline_length(xy, *c))
        .sum();
    // each round-capped line adds about one width of cap area
    let round_len: f64 = caps.iter().filter(|c| **c == "round").count() as f64 * width;
    width = (ink_area / (new_len + 0.5 * round_len).max(1e-6)).max(0.25);
    Some(Stroke { polylines: finished, closed: kept_closed, caps, width })
}

/// The centreline is fitted at this share of the curve tolerance: an error on
/// a stroke's centreline shows on both of its edges (`strokes.py` has the
/// measurements).
pub const STROKE_FIT_SHARE: f64 = 0.5;

/// One `<path>` per cap style (closed loops join the round group).
pub fn stroke_svg(stroke: &Stroke, colour: &str, opacity: f64, params: &CurveParams, precision: usize) -> String {
    let mut round: Vec<String> = Vec::new();
    let mut butt: Vec<String> = Vec::new();
    let tol = params.tol * STROKE_FIT_SHARE;
    for (i, xy) in stroke.polylines.iter().enumerate() {
        let cap = stroke.caps.get(i).copied().unwrap_or("round");
        if stroke.closed[i] && xy.len() >= 4 {
            round.push(path_d(&[fit_closed_smooth(xy, tol)], precision));
        } else if xy.len() >= 2 {
            let d = path_d(&[fit_open(xy, tol, None, None)], precision);
            let d = d.strip_suffix('Z').map(|s| s.to_string()).unwrap_or(d);
            if cap == "butt" {
                butt.push(d);
            } else {
                round.push(d);
            }
        }
    }
    let op = if opacity >= 0.995 { String::new() } else { format!(" stroke-opacity=\"{:.3}\"", opacity) };
    let w = {
        let s = format!("{:.*}", precision.max(2), stroke.width);
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    };
    let mut out = String::new();
    // the Python builds the dict {"round": …, "butt": …} and emits in that order
    for (cap, parts) in [("round", &round), ("butt", &butt)] {
        if !parts.is_empty() {
            out.push_str(&format!(
                "<path d=\"{}\" fill=\"none\" stroke=\"{}\" stroke-width=\"{}\" stroke-linecap=\"{}\" stroke-linejoin=\"round\"{}/>",
                parts.concat(),
                colour,
                w,
                cap,
                op
            ));
        }
    }
    out
}

/// RMS between the coverage a constant-width centreline would paint and the
/// coverage actually measured.
///
/// A drawn line *is* a constant-width centreline, so this is near zero. A
/// letterform is made of strokes too — geometrically it passes every test for
/// thinness, elongation and width consistency — but its terminals and joins are
/// not what a single centreline paints, and that shows up here.
///
/// The prediction is exact to the sub-pixel: each pixel centre's distance to
/// the centreline itself, and the coverage of a band of the stroke's width
/// across a unit pixel at that distance (the Python's `stroke_fidelity`).
pub fn stroke_fidelity(stroke: &Stroke, coverage: &Grid<f64>) -> f64 {
    let half = stroke.width / 2.0;
    let reach = half + 2.0;
    let dist = polyline_distance(&stroke.polylines, &stroke.closed, coverage.h, coverage.w, reach);
    let mut num = 0.0;
    let mut n = 0usize;
    for i in 0..coverage.len() {
        let d = dist.data[i];
        if d <= reach {
            let predicted = ((d + 0.5).min(half) - (d - 0.5).max(-half)).clamp(0.0, 1.0);
            let e = predicted - coverage.data[i];
            num += e * e;
            n += 1;
        }
    }
    if n == 0 {
        return f64::INFINITY;
    }
    (num / n as f64).sqrt()
}

/// Distance from every pixel centre within `reach` of the polylines to the
/// nearest point on them; infinity elsewhere. An open polyline's band ends
/// where it ends: a pixel whose centre projects past an end takes no distance
/// from the end segment (the Python's `_polyline_distance`).
fn polyline_distance(polylines: &[Vec<P>], closed: &[bool], h: usize, w: usize, reach: f64) -> Grid<f64> {
    let mut dist = Grid::filled(h, w, f64::INFINITY);
    for (xy, is_closed) in polylines.iter().zip(closed.iter()) {
        let mut pts = xy.clone();
        if *is_closed && !xy.is_empty() {
            pts.push(xy[0]);
        }
        let butt = !*is_closed;
        let last = pts.len().saturating_sub(2);
        for (k, pair) in pts.windows(2).enumerate() {
            let ([ax, ay], [bx, by]) = (pair[0], pair[1]);
            let c0 = ((ax.min(bx) - reach).floor().max(0.0)) as usize;
            let c1 = (((ax.max(bx) + reach).ceil() as i64 + 1).max(0) as usize).min(w);
            let r0 = ((ay.min(by) - reach).floor().max(0.0)) as usize;
            let r1 = (((ay.max(by) + reach).ceil() as i64 + 1).max(0) as usize).min(h);
            if c1 <= c0 || r1 <= r0 {
                continue;
            }
            let (dx, dy) = (bx - ax, by - ay);
            let l2 = dx * dx + dy * dy;
            for r in r0..r1 {
                let cy = r as f64 + 0.5;
                for c in c0..c1 {
                    let cx = c as f64 + 0.5;
                    let raw = if l2 < 1e-18 { 0.0 } else { ((cx - ax) * dx + (cy - ay) * dy) / l2 };
                    let t = raw.clamp(0.0, 1.0);
                    let mut dd = (cx - (ax + t * dx)).hypot(cy - (ay + t * dy));
                    if butt && k == 0 && raw < 0.0 {
                        dd = f64::INFINITY;
                    }
                    if butt && k == last && raw > 1.0 {
                        dd = f64::INFINITY;
                    }
                    let kk = r * w + c;
                    if dd < dist.data[kk] {
                        dist.data[kk] = dd;
                    }
                }
            }
        }
    }
    dist
}
