//! Binary morphology with the 4-connected (cross) structuring element, which
//! is the only one the pipeline uses, plus hole filling.

use super::grid::{Grid, Mask};

/// `scipy.ndimage.binary_dilation(mask, cross)` — border_value = 0.
pub fn dilate_cross(m: &Mask) -> Mask {
    let (h, w) = (m.h, m.w);
    let mut out = m.clone();
    for r in 0..h {
        for c in 0..w {
            if m.data[r * w + c] {
                continue;
            }
            let up = r > 0 && m.data[(r - 1) * w + c];
            let dn = r + 1 < h && m.data[(r + 1) * w + c];
            let lf = c > 0 && m.data[r * w + c - 1];
            let rt = c + 1 < w && m.data[r * w + c + 1];
            out.data[r * w + c] = up || dn || lf || rt;
        }
    }
    out
}

pub fn dilate_cross_n(m: &Mask, iterations: usize) -> Mask {
    let mut out = m.clone();
    for _ in 0..iterations {
        out = dilate_cross(&out);
    }
    out
}

/// `scipy.ndimage.binary_erosion(mask, cross, border_value=b)`.
pub fn erode_cross(m: &Mask, border_value: bool) -> Mask {
    let (h, w) = (m.h, m.w);
    let mut out = m.clone();
    for r in 0..h {
        for c in 0..w {
            if !m.data[r * w + c] {
                continue;
            }
            let up = if r > 0 { m.data[(r - 1) * w + c] } else { border_value };
            let dn = if r + 1 < h { m.data[(r + 1) * w + c] } else { border_value };
            let lf = if c > 0 { m.data[r * w + c - 1] } else { border_value };
            let rt = if c + 1 < w { m.data[r * w + c + 1] } else { border_value };
            out.data[r * w + c] = up && dn && lf && rt;
        }
    }
    out
}

pub fn erode_cross_n(m: &Mask, iterations: usize, border_value: bool) -> Mask {
    let mut out = m.clone();
    for _ in 0..iterations {
        out = erode_cross(&out, border_value);
    }
    out
}

/// `scipy.ndimage.binary_fill_holes` with the cross structure: everything not
/// reachable from the border through false pixels becomes true.
pub fn fill_holes(m: &Mask) -> Mask {
    let (h, w) = (m.h, m.w);
    let mut outside = Grid::filled(h, w, false);
    let mut stack: Vec<usize> = Vec::new();
    let push = |i: usize, outside: &mut Mask, stack: &mut Vec<usize>| {
        if !m.data[i] && !outside.data[i] {
            outside.data[i] = true;
            stack.push(i);
        }
    };
    for c in 0..w {
        push(c, &mut outside, &mut stack);
        push((h - 1) * w + c, &mut outside, &mut stack);
    }
    for r in 0..h {
        push(r * w, &mut outside, &mut stack);
        push(r * w + w - 1, &mut outside, &mut stack);
    }
    while let Some(i) = stack.pop() {
        let (r, c) = (i / w, i % w);
        if r > 0 {
            push(i - w, &mut outside, &mut stack);
        }
        if r + 1 < h {
            push(i + w, &mut outside, &mut stack);
        }
        if c > 0 {
            push(i - 1, &mut outside, &mut stack);
        }
        if c + 1 < w {
            push(i + 1, &mut outside, &mut stack);
        }
    }
    Grid { h, w, data: outside.data.iter().map(|o| !*o).collect() }
}

/// `skimage.morphology.convex_hull_image` reduced to what `overlaps` needs: the
/// area of the convex hull of the true pixels, counted as pixel centres inside
/// the hull polygon.
pub fn convex_hull_area(m: &Mask) -> f64 {
    let mut pts: Vec<(f64, f64)> = Vec::new();
    for r in 0..m.h {
        for c in 0..m.w {
            if m.data[r * m.w + c] {
                // the pixel's four corners, as skimage does before hulling
                pts.push((c as f64 - 0.5, r as f64 - 0.5));
                pts.push((c as f64 + 0.5, r as f64 - 0.5));
                pts.push((c as f64 - 0.5, r as f64 + 0.5));
                pts.push((c as f64 + 0.5, r as f64 + 0.5));
            }
        }
    }
    if pts.len() < 3 {
        return m.count() as f64;
    }
    let hull = convex_hull(&mut pts);
    if hull.len() < 3 {
        return m.count() as f64;
    }
    // count pixel centres inside the hull (skimage rasterises the hull the same way)
    let mut n = 0usize;
    for r in 0..m.h {
        for c in 0..m.w {
            if point_in_hull(&hull, c as f64, r as f64) {
                n += 1;
            }
        }
    }
    n as f64
}

fn cross(o: (f64, f64), a: (f64, f64), b: (f64, f64)) -> f64 {
    (a.0 - o.0) * (b.1 - o.1) - (a.1 - o.1) * (b.0 - o.0)
}

/// Monotone chain; returns the hull counter-clockwise in screen coordinates.
pub fn convex_hull(pts: &mut Vec<(f64, f64)>) -> Vec<(f64, f64)> {
    pts.sort_by(|a, b| a.partial_cmp(b).unwrap());
    pts.dedup();
    let n = pts.len();
    if n < 3 {
        return pts.clone();
    }
    let mut hull: Vec<(f64, f64)> = Vec::with_capacity(2 * n);
    for &p in pts.iter() {
        while hull.len() >= 2 && cross(hull[hull.len() - 2], hull[hull.len() - 1], p) <= 0.0 {
            hull.pop();
        }
        hull.push(p);
    }
    let lower = hull.len() + 1;
    for &p in pts.iter().rev() {
        while hull.len() >= lower && cross(hull[hull.len() - 2], hull[hull.len() - 1], p) <= 0.0 {
            hull.pop();
        }
        hull.push(p);
    }
    hull.pop();
    hull
}

fn point_in_hull(hull: &[(f64, f64)], x: f64, y: f64) -> bool {
    // hull is convex and consistently wound; inside means never strictly outside an edge
    let n = hull.len();
    let mut sign = 0i8;
    for i in 0..n {
        let a = hull[i];
        let b = hull[(i + 1) % n];
        let d = (b.0 - a.0) * (y - a.1) - (b.1 - a.1) * (x - a.0);
        if d.abs() < 1e-12 {
            continue;
        }
        let s = if d > 0.0 { 1i8 } else { -1i8 };
        if sign == 0 {
            sign = s;
        } else if sign != s {
            return false;
        }
    }
    true
}
