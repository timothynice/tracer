//! What is on screen: the element-id map and the visibility of an outline's samples
//! (`quality.id_svg`, `id_map`, `_lookup`, `visible_samples`).
use super::{CardError, VIS_OFFSET};
use crate::drawing::{Contour, Drawing};
use crate::py;
use crate::render::{self, RenderError};
use std::collections::BTreeMap;
use std::fmt::Write;

/// `f"{x:.4f}"`
fn py_fixed4(x: f64) -> String {
    if x.is_nan() {
        "nan".into()
    } else {
        format!("{x:.4}")
    }
}

/// `np.round(x, 3)`: `rint(x * 1000) / 1000`, ties to even.
fn round3(x: f64) -> f64 {
    (x * 1000.0).round_ties_even() / 1000.0
}

/// `_d`: path data for an outline, its points rounded to 3 decimals.
fn path_d(pts: &[[f64; 2]], closed: bool, out: &mut String) {
    out.push('M');
    let mut first = true;
    for v in pts.iter().flatten() {
        if !first {
            out.push(' ');
        }
        first = false;
        out.push_str(&py::repr(round3(*v)));
    }
    if closed {
        out.push('Z');
    }
}

/// `id_svg`: the drawing with every element that hides what lies under it painted in its own
/// colour (element index + 1 as 0xRRGGBB), the others left out.
pub(super) fn id_svg(drawing: &Drawing, size: (u32, u32)) -> String {
    let (w, h) = size;
    let mut by_el: BTreeMap<usize, Vec<&Contour>> = BTreeMap::new();
    for c in &drawing.contours {
        by_el.entry(c.element).or_default().push(c);
    }
    let mut out = format!(r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">"#);
    for (&e, contours) in &by_el {
        if !drawing.covers.get(e).copied().unwrap_or(false) {
            continue;
        }
        let colour = format!("#{:06x}", e + 1);
        let fills: Vec<&&Contour> = contours.iter().filter(|c| c.stroke.is_none()).collect();
        if let Some(first) = fills.first() {
            out.push_str(r#"<path d=""#);
            for c in &fills {
                path_d(&c.pts, true, &mut out);
            }
            let _ = write!(out, r#"" fill="{colour}" fill-rule="{}"/>"#, first.fill_rule);
        }
        for c in contours {
            if let Some(width) = c.stroke {
                out.push_str(r#"<path d=""#);
                path_d(&c.pts, c.closed, &mut out);
                let _ = write!(
                    out,
                    r#"" fill="none" stroke="{colour}" stroke-width="{}" stroke-linejoin="round" stroke-linecap="round"/>"#,
                    py_fixed4(width)
                );
            }
        }
    }
    out.push_str("</svg>");
    out
}

/// The element on top at each sub-pixel of a `w·scale × h·scale` crisp render, row-major; -1
/// where nothing opaque is.
#[derive(Debug, Clone, PartialEq)]
pub(super) struct IdMap {
    pub h: usize,
    pub w: usize,
    pub ids: Vec<i32>,
}

/// `id_map`: [`id_svg`] rendered without anti-aliasing at `scale`, each colour read back as the
/// index of its element.
pub(super) fn id_map(drawing: &Drawing, size: (u32, u32), scale: u32) -> Result<IdMap, CardError> {
    if drawing.elements >= (1 << 24) - 1 {
        return Err(CardError::TooManyElements);
    }
    let (w, h) = size;
    let (Some(ww), Some(hh)) = (w.checked_mul(scale), h.checked_mul(scale)) else {
        let side = |n: u32| u64::from(n) * u64::from(scale);
        return Err(CardError::Render(RenderError::TooLarge { width: side(w), height: side(h) }));
    };
    let rgba = render::render(&id_svg(drawing, size), ww, hh, true).map_err(CardError::Render)?;
    let ids = rgba
        .as_chunks::<4>()
        .0
        .iter()
        .map(|p| if p[3] >= 128 { ((p[0] as i32) << 16 | (p[1] as i32) << 8 | p[2] as i32) - 1 } else { -1 })
        .collect();
    Ok(IdMap { h: hh as usize, w: ww as usize, ids })
}

/// `_lookup`: the id under each point, the sub-pixel being `floor(x · scale)` (a float that
/// will not fit an i64 saturates, NaN reads as 0, as numpy's cast does on arm64); -1 off the map.
pub(super) fn lookup(ids: &IdMap, pts: impl Iterator<Item = [f64; 2]>, scale: u32) -> Vec<i32> {
    let s = scale as f64;
    pts.map(|[x, y]| {
        let ix = (x * s).floor() as i64;
        let iy = (y * s).floor() as i64;
        if ix >= 0 && (ix as u64) < ids.w as u64 && iy >= 0 && (iy as u64) < ids.h as u64 {
            ids.ids[iy as usize * ids.w + ix as usize]
        } else {
            -1
        }
    })
    .collect()
}

/// `visible_samples`: which samples of an outline are on screen, that is not painted over by
/// later elements both [`VIS_OFFSET`] px to its left and to its right (on a stroke, on its
/// centreline). Every sample is visible without an id map.
pub(super) fn visible_samples(
    q: &[[f64; 2]],
    closed: bool,
    element: usize,
    stroke: Option<f64>,
    ids: Option<&IdMap>,
    scale: u32,
) -> Vec<bool> {
    let n = q.len();
    let Some(ids) = ids.filter(|_| n > 0) else { return vec![true; n] };
    let el = element as i64;
    if stroke.is_some() {
        return lookup(ids, q.iter().copied(), scale).into_iter().map(|v| (v as i64) <= el).collect();
    }
    // the tangent: a central difference, np.gradient's one-sided ones at an open end
    let tan: Vec<[f64; 2]> = (0..n)
        .map(|i| {
            if closed {
                let (a, b) = (q[(i + 1) % n], q[(i + n - 1) % n]);
                [a[0] - b[0], a[1] - b[1]]
            } else if n == 1 {
                [0.0, 0.0]
            } else if i == 0 {
                [(q[1][0] - q[0][0]) / 1.0, (q[1][1] - q[0][1]) / 1.0]
            } else if i == n - 1 {
                [(q[n - 1][0] - q[n - 2][0]) / 1.0, (q[n - 1][1] - q[n - 2][1]) / 1.0]
            } else {
                [(q[i + 1][0] - q[i - 1][0]) / 2.0, (q[i + 1][1] - q[i - 1][1]) / 2.0]
            }
        })
        .collect();
    let normal: Vec<[f64; 2]> = tan
        .iter()
        .map(|t| {
            let norm = (t[0] * t[0] + t[1] * t[1]).sqrt();
            let u = if norm > 1e-12 { [t[0] / norm, t[1] / norm] } else { [0.0, 0.0] };
            [-u[1] * VIS_OFFSET, u[0] * VIS_OFFSET]
        })
        .collect();
    let side = |sign: f64| {
        lookup(ids, q.iter().zip(&normal).map(|(p, d)| [p[0] + sign * d[0], p[1] + sign * d[1]]), scale)
    };
    let (a, b) = (side(1.0), side(-1.0));
    a.iter().zip(&b).map(|(&a, &b)| !((a as i64) > el && (b as i64) > el)).collect()
}
