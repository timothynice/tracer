//! The outline measures of the artifact scorecard, ported from `quality.geometry_card` and its
//! helpers (`backend/studi0trace/imaging/quality.py`): wobble, inflections, slivers, degenerate
//! subpaths, thin strokes and how (rounded) rectangles are drawn, over what is on screen.
//!
//! The Python is the definition, and the port is to the bit wherever numpy's arithmetic can be
//! followed (`tests/geometry.rs` holds whole cards to `geometry.json`; the module's own tests
//! hold each helper to `geometry_helpers.json`). Every outline is resampled at [`STEP`] px; the
//! turning between chords of one sample and of [`WOBBLE_SCALE`] px gives the wobble, at
//! [`INFLECT_SCALE`] px the inflections, and corners grown over the curve they sit on give the
//! rectangles. The id map (`ids`) is the drawing rendered crisp with each covering element in a
//! colour of its own; a sample counts only where later elements do not paint over it on both
//! sides. What it took, measured against numpy 2.5.3 on arm64:
//!
//! - **Order.** `x.sum()` over a fresh array is numpy's pairwise sum (`edges::pairwise_sum`);
//!   `np.cumsum`, `axis=0` means and `np.bincount` add left to right; `np.linalg.norm` of a row
//!   is `sqrt(dx·dx + dy·dy)`, unfused. `np.interp` fuses its `slope · dx + y`.
//! - **Accelerate.** `_area`'s `np.dot` and the wobble locator's `np.convolve` go to
//!   Accelerate's `cblas_ddot`, which sums in a tree of its own with fused multiply-adds;
//!   `accelerate` reproduces it (see there for how it was read off).
//! - **Python's numbers.** `round()` ties to even; `%` on floats takes the divisor's sign;
//!   `max`/`min` keep the first of equals; `round(x, 2)` rounds the exact binary value, as
//!   formatting does; `math.degrees`/`radians` multiply by `180/π`, `π/180`; `atan2` and `sqrt`
//!   are libm's and IEEE's, here as there.
//! - **What numpy casts.** `np.floor(x).astype(np.int64)` saturates and reads NaN as 0 on
//!   arm64, as Rust's `as` does, so the id-map lookup of a point at 1e300 or NaN is the Python's.
//!
//! Where it is not to the bit: a side's direction in `_rect_like` comes from LAPACK's SVD in the
//! Python and from the 2×2 scatter here; they agree to about 1e-15 (the bow and skew of a side,
//! which only meet thresholds and a `round(_, 2)`).
//!
//! What is to the bit everywhere and what only on macOS on arm64: resampling, wrapping, the
//! dilations, the area and every Accelerate-order sum are IEEE arithmetic (`mul_add` included),
//! the same on every platform. `atan2` (the turning, hence the wobble, inflections and corners)
//! and the drawing's `cos`/`sin` are libm's, and Apple's is not correctly rounded everywhere
//! (about 1 input in 1 300 of the fixtures'): another libm can differ from the fixtures by an ulp
//! there, which the tests allow off that platform (`tests/geometry.rs`, the module's tests).
//!
//! # Where it differs on purpose
//!
//! - [`MAX_SAMPLES`] samples over all the outlines of a drawing is the most this resamples
//!   ([`CardError::TooLarge`]); the Python has no limit. Memory peaks at about 135 bytes a
//!   sample of the longest outline (measured).
//! - A float that JSON cannot hold (an infinite length, a NaN) is `null` in the map, where the
//!   Python's dict holds the float.
//! - Errors are values: what the Python raises (`IndexError` on a polygon of no points,
//!   `OverflowError` on an infinite length, `ParseError`, ...) is a [`CardError`].
//!
//! The SVG is parsed ([`drawing::parse`]) before anything renders it, so what nests too deep for
//! the renderer's recursion is refused first.
use crate::drawing::{self, DrawingError};
use crate::edges::pairwise_sum;
use serde_json::{Map, Value};
use std::f64::consts::PI;
use std::fmt;

mod accelerate;
mod ids;
mod outline;
mod rects;
#[cfg(test)]
mod tests;

pub use crate::drawing::STEP;
/// `quality.SLIVER_AREA`: px².
pub const SLIVER_AREA: f64 = 4.0;
/// `quality.SLIVER_THICK`: px, 2·area/perimeter.
pub const SLIVER_THICK: f64 = 1.0;
/// `quality.THIN_STROKE`: px stroke width.
pub const THIN_STROKE: f64 = 1.0;
/// `quality.WOBBLE_SCALE`: px chord for the designer-scale tangent.
pub const WOBBLE_SCALE: f64 = 4.0;
/// `quality.INFLECT_SCALE`: px chord for the curvature sign test.
pub const INFLECT_SCALE: f64 = 6.0;
/// `quality.INFLECT_HYST`: degrees of turning before a sign change counts.
pub const INFLECT_HYST: f64 = 3.0;
/// `quality.CORNER_SNAP`: degrees turned within 2 px: a corner, where inflections do not count.
pub const CORNER_SNAP: f64 = 20.0;
/// `quality.CORNER_GUARD`: px cut out either side of a corner.
pub const CORNER_GUARD: f64 = 3.0;
/// `quality.CORNER_WINDOW`: px window a corner's turning must reach [`CORNER_SEED_DEG`] within.
pub const CORNER_WINDOW: f64 = 10.0;
/// `quality.CORNER_SEED_DEG`: a corner is looked for where the window turns this far (a radius
/// up to ~12.7 px).
pub const CORNER_SEED_DEG: f64 = 45.0;
/// `quality.GROW_MAX`: px a corner grows over the curve it sits on, at most.
pub const GROW_MAX: f64 = 40.0;
/// `quality.CORNER_MIN_DEG`: and it has to turn this far in all.
pub const CORNER_MIN_DEG: f64 = 60.0;
/// `quality.CORNER_MAX_DEG`
pub const CORNER_MAX_DEG: f64 = 120.0;
/// `quality.CURVED`: rad/px: a sample turning faster than this is part of a corner's curve.
pub const CURVED: f64 = 0.04;
/// `quality.RADIUS_SPREAD`: px.
pub const RADIUS_SPREAD: f64 = 1.0;
/// `quality.ROUND_R`
pub const ROUND_R: f64 = 1.5;
/// `quality.SHARP_R`
pub const SHARP_R: f64 = 0.75;
/// `quality.RECT_BOW`: px a rect-like contour's side may bow off its line.
pub const RECT_BOW: f64 = 0.25;
/// `quality.RECT_SKEW`: degrees its sides may be off parallel / perpendicular.
pub const RECT_SKEW: f64 = 1.0;
/// `quality.RECT_SIDE_DEG`: a side turning more than this is not a side.
pub const RECT_SIDE_DEG: f64 = 30.0;
/// `quality.RECT_GRID`: sides further than this off a right-angle grid make a trapezoid, not a
/// rect.
pub const RECT_GRID: f64 = 6.0;
/// `quality.ID_SCALE`: render scale of the element-id map (4x reads the same to ~1% at 3x the
/// cost).
pub const ID_SCALE: u32 = 2;
/// `quality.VIS_OFFSET`: px either side of an outline sample that must both be painted over to
/// hide it.
pub const VIS_OFFSET: f64 = 0.35;
/// Samples, over all the outlines of one drawing, past which [`card`] refuses (the Python has
/// no limit): 4M px of outline at [`STEP`].
pub const MAX_SAMPLES: usize = 1 << 24;

/// Why a card could not be made.
#[derive(Debug, Clone, PartialEq)]
pub enum CardError {
    /// The SVG could not be read ([`drawing::parse`]).
    Drawing(DrawingError),
    /// More elements than an id map has colours for (the Python's `ValueError`).
    TooManyElements,
    /// The id map could not be rendered ([`crate::render::render`]).
    Render(String),
    /// What numpy or Python raises on the outlines: an index into no points (`IndexError`), a
    /// length that is infinite or NaN (`OverflowError`, `ValueError`), in the Python's words.
    Geometry(String),
    /// More than [`MAX_SAMPLES`] samples; the Python would try to allocate them.
    TooLarge,
}

impl fmt::Display for CardError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CardError::Drawing(e) => write!(f, "{e}"),
            CardError::TooManyElements => write!(f, "too many elements for an id map"),
            CardError::Render(e) => write!(f, "the id map does not render: {e}"),
            CardError::Geometry(e) => write!(f, "the outlines cannot be measured: {e}"),
            CardError::TooLarge => write!(f, "the outlines resample to more than {MAX_SAMPLES} samples"),
        }
    }
}

impl std::error::Error for CardError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            CardError::Drawing(e) => Some(e),
            _ => None,
        }
    }
}

impl From<DrawingError> for CardError {
    fn from(e: DrawingError) -> Self {
        CardError::Drawing(e)
    }
}

/// `int(round(x))`: ties to even; NaN and infinity raise.
fn py_round(x: f64) -> Result<i64, CardError> {
    if x.is_nan() {
        Err(CardError::Geometry("cannot convert float NaN to integer".into()))
    } else if x.is_infinite() {
        Err(CardError::Geometry("cannot convert float infinity to integer".into()))
    } else {
        Ok(x.round_ties_even() as i64)
    }
}

/// `math.degrees`
fn degrees(x: f64) -> f64 {
    x * (180.0 / PI)
}

/// `math.radians`
fn radians(x: f64) -> f64 {
    x * (PI / 180.0)
}

/// `round(x, 2)`: the exact value rounded to two decimals, ties to even, read back.
fn round2(x: f64) -> f64 {
    if x.is_finite() {
        format!("{x:.2}").parse().unwrap()
    } else {
        x
    }
}

/// Python's `max(a, b)`: `b` only when it is greater.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// `q.mean(axis=0)`: each column added in order and divided by the count.
fn centre(q: &[[f64; 2]]) -> [f64; 2] {
    let (mut sx, mut sy) = (0.0, 0.0);
    for (i, p) in q.iter().enumerate() {
        sx = if i == 0 { p[0] } else { sx + p[0] };
        sy = if i == 0 { p[1] } else { sy + p[1] };
    }
    [sx / q.len() as f64, sy / q.len() as f64]
}

fn float(x: f64) -> Value {
    Value::from(x)
}

fn floats(v: &[f64]) -> Value {
    Value::Array(v.iter().map(|&x| float(x)).collect())
}

/// `geometry_card`: the outline measures of `svg`, under the keys and in the order the Python
/// returns them, the `_`-prefixed location lists included. With `visibility` (and a `size` to
/// render the id map at, `id_scale` times over) only what is on screen is scored; `size` also
/// maps the root viewBox onto that many pixels, as [`drawing::parse`] does.
pub fn card(svg: &str, size: Option<(u32, u32)>, visibility: bool, id_scale: u32) -> Result<Map<String, Value>, CardError> {
    let drawing = drawing::parse(svg, size)?;
    let ids = match size {
        Some(size) if visibility && !drawing.contours.is_empty() => Some(ids::id_map(&drawing, size, id_scale)?),
        _ => None,
    };
    let (mut length, mut hidden_len, mut wobble, mut sliver_area) = (0.0, 0.0, 0.0, 0.0);
    let (mut inflections, mut slivers, mut degenerate, mut thin_strokes) = (0u64, 0u64, 0u64, 0u64);
    let (mut radius_bad, mut rects, mut bowed, mut skewed) = (0u64, 0u64, 0u64, 0u64);
    let (mut loc_wobble, mut loc_flip, mut loc_sliver, mut loc_radius) = (vec![], vec![], vec![], vec![]);
    let kw = py_round(WOBBLE_SCALE / STEP)?.max(1) as usize;
    let ki = py_round(INFLECT_SCALE / STEP)?.max(1) as usize;
    let mut budget = MAX_SAMPLES;
    for c in &drawing.contours {
        let q = outline::resample(&c.pts, c.closed, &mut budget)?;
        let n = q.len();
        let visible = |q: &[[f64; 2]]| ids::visible_samples(q, c.closed, c.element, c.stroke, ids.as_ref(), id_scale);
        if n < 3 {
            match c.stroke {
                None => degenerate += 1,
                Some(width) if width < THIN_STROKE && visible(&q).contains(&true) => thin_strokes += 1,
                Some(_) => {}
            }
            continue;
        }
        let vis = visible(&q);
        let any_visible = vis.contains(&true);
        if let Some(width) = c.stroke {
            if width < THIN_STROKE && any_visible {
                thin_strokes += 1;
                let mid = c.pts[c.pts.len() / 2];
                loc_sliver.push(floats(&[mid[0], mid[1], 0.0, width]));
            }
        }
        let ring = q.iter().zip(q.iter().skip(1).chain(if c.closed { q.first() } else { None }));
        let seg: Vec<f64> = ring.map(|(a, b)| ((b[0] - a[0]) * (b[0] - a[0]) + (b[1] - a[1]) * (b[1] - a[1])).sqrt()).collect();
        let per = pairwise_sum(&seg);
        if c.stroke.is_none() {
            let area = rects::area(&q).abs();
            if area < 0.05 {
                degenerate += 1;
                continue;
            }
            let thick = 2.0 * area / py_max(per, 1e-9);
            if area < SLIVER_AREA || thick < SLIVER_THICK {
                if any_visible {
                    slivers += 1;
                    sliver_area += area;
                    let cen = centre(&q);
                    loc_sliver.push(floats(&[cen[0], cen[1], area, thick]));
                }
                continue; // a sliver's own turning is not an outline's wobble
            }
        }
        if per < 2.0 * WOBBLE_SCALE {
            continue;
        }
        let shown = (vis.iter().filter(|&&v| v).count() as f64 / n as f64) * per;
        hidden_len += per - shown;
        if shown <= 0.0 {
            continue;
        }
        length += shown;
        let excess_at = outline::cancelled(&q, c.closed, kw);
        let vis_at: Vec<bool> = if c.closed {
            vis.clone()
        } else {
            let h = kw / 2;
            std::iter::repeat_n(vis[0], h).chain(vis.iter().copied()).chain(std::iter::repeat_n(vis[n - 1], h)).collect()
        };
        let on_screen: Vec<f64> = excess_at.iter().zip(&vis_at).filter(|(_, &v)| v).map(|(&e, _)| e).collect();
        let excess = py_max(0.0, pairwise_sum(&on_screen));
        wobble += degrees(excess);
        // where: cancelled turning summed over a 2L window, reported on screen only
        if excess > radians(5.0) {
            if excess_at.len() < 2 * kw {
                return Err(CardError::Geometry("operands could not be broadcast together".into()));
            }
            let local = accelerate::convolve_same_ones(&excess_at, 2 * kw);
            let off = if c.closed { 0 } else { kw / 2 };
            let at = (0..local.len()).filter(|&j| local[j] > radians(20.0) && vis_at[j]);
            for j in at.step_by(kw) {
                let p = q[(j as i64 - off as i64).max(0).min(n as i64 - 1) as usize];
                loc_wobble.push(floats(&[p[0], p[1], degrees(local[j])]));
            }
        }
        for j in outline::inflections(&q, c.closed, ki)? {
            if vis[j % n] {
                inflections += 1;
                let p = q.get(j).ok_or_else(|| CardError::Geometry(format!("index {j} is out of bounds for axis 0 with size {n}")))?;
                loc_flip.push(floats(p));
            }
        }
        if c.closed && c.stroke.is_none() {
            let net = pairwise_sum(&outline::turns(&q, true, 1));
            let corners = rects::corners(&q, if net >= 0.0 { 1.0 } else { -1.0 })?;
            if let Some(rect) = rects::rect_like(&q, &corners, Some(&vis)) {
                rects += 1;
                let bad = rect.spread > RADIUS_SPREAD || rect.mixed;
                radius_bad += bad as u64;
                bowed += (rect.bow > RECT_BOW) as u64;
                skewed += (rect.skew > RECT_SKEW) as u64;
                if bad || rect.bow > RECT_BOW || rect.skew > RECT_SKEW {
                    let cen = centre(&q);
                    let radii: Vec<f64> = rect.radii.iter().map(|&r| round2(r)).collect();
                    loc_radius.push(Value::Array(vec![
                        float(cen[0]),
                        float(cen[1]),
                        floats(&radii),
                        float(round2(rect.bow)),
                        float(round2(rect.skew)),
                    ]));
                }
            }
        }
    }
    let per100 = if length > 0.0 { 100.0 / length } else { 0.0 };
    let mut out = Map::new();
    out.insert("elements".into(), Value::from(drawing.elements as u64));
    out.insert("segments".into(), Value::from(drawing.segments as u64));
    out.insert("strokes".into(), Value::from(drawing.strokes as u64));
    out.insert("outline_len_px".into(), float(length));
    out.insert("hidden_len_px".into(), float(hidden_len));
    out.insert("slivers".into(), Value::from(slivers));
    out.insert("sliver_area_px".into(), float(sliver_area));
    out.insert("degenerate".into(), Value::from(degenerate));
    out.insert("thin_strokes".into(), Value::from(thin_strokes));
    out.insert("wobble_deg_100px".into(), float(wobble * per100));
    out.insert("inflections".into(), Value::from(inflections));
    out.insert("rect_like".into(), Value::from(rects));
    out.insert("radius_inconsistent".into(), Value::from(radius_bad));
    out.insert("rect_bowed".into(), Value::from(bowed));
    out.insert("rect_skewed".into(), Value::from(skewed));
    out.insert("segments_100px".into(), float(drawing.segments as f64 * per100));
    out.insert("_wobble_at".into(), Value::Array(loc_wobble));
    out.insert("_flips_at".into(), Value::Array(loc_flip));
    out.insert("_slivers_at".into(), Value::Array(loc_sliver));
    out.insert("_radius_at".into(), Value::Array(loc_radius));
    Ok(out)
}
