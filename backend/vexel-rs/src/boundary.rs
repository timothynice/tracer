//! Stage 6: sub-pixel boundaries from anti-aliasing.
//!
//! For a shape, every pixel on the one-pixel ring inside and outside the mask
//! gets a *coverage* estimate: how much of that pixel the shape covers,
//! inferred by projecting the pixel's colour onto the segment between the
//! shape's fill and the neighbouring region's fill evaluated there. The
//! 0.5 iso-contour of that field is the outline at sub-pixel precision — where
//! the artist drew it, not on a pixel border.

use crate::core::contours::{find_contours, pad};
use crate::core::grid::{Grid, Image, Mask};
use crate::core::labels::Labels;
use crate::core::morphology::{dilate_cross, erode_cross};

/// `(label, xs, ys) -> rgba255`: the reconstructed fill of `label` at pixel centres.
pub type FillAt<'a> = &'a dyn Fn(i32, &[f64], &[f64]) -> Vec<[f64; 4]>;

/// A field computed on a region's own neighbourhood rather than the whole frame,
/// with the offset needed to read it back in image coordinates.
///
/// Every stage that asks a question about one region used to allocate and walk a
/// frame-sized array to do it; on an illustration with two hundred regions that
/// is two hundred sweeps of the image for work that touches a few thousand
/// pixels. The window is the region's bounding box grown by two — far enough
/// that the one-pixel coverage ring and the neighbour lookup behind it both fit.
pub struct Cropped {
    pub field: Grid<f64>,
    pub r0: usize,
    pub c0: usize,
}

fn window(mask: &Mask) -> Option<(usize, usize, usize, usize)> {
    let (r0, r1, c0, c1) = mask.bbox()?;
    Some((
        r0.saturating_sub(2),
        (r1 + 2).min(mask.h),
        c0.saturating_sub(2),
        (c1 + 2).min(mask.w),
    ))
}

fn crop_mask(m: &Mask, r0: usize, r1: usize, c0: usize, c1: usize) -> Mask {
    let w = c1 - c0;
    let mut out = Grid::filled(r1 - r0, w, false);
    for r in r0..r1 {
        out.data[(r - r0) * w..(r - r0 + 1) * w].copy_from_slice(&m.data[r * m.w + c0..r * m.w + c1]);
    }
    out
}

fn crop_labels(l: &Labels, r0: usize, r1: usize, c0: usize, c1: usize) -> Labels {
    let w = c1 - c0;
    let mut out = Grid::<i32>::new(r1 - r0, w);
    for r in r0..r1 {
        out.data[(r - r0) * w..(r - r0 + 1) * w].copy_from_slice(&l.data[r * l.w + c0..r * l.w + c1]);
    }
    out
}

/// For every pixel, the label of a 4-neighbour outside `mask` (0 where none).
fn outside_label(labels: &Labels, mask: &Mask) -> Labels {
    let (h, w) = (labels.h, labels.w);
    let mut out = Grid::<i32>::new(h, w);
    // the Python rolls the arrays in the order (0,1), (0,-1), (1,0), (-1,0) and
    // keeps the first hit, so the neighbour order is left, right, up, down
    let shifts: [(isize, isize); 4] = [(0, 1), (0, -1), (1, 0), (-1, 0)];
    for (sr, sc) in shifts {
        for r in 0..h {
            for c in 0..w {
                let i = r * w + c;
                if out.data[i] != 0 {
                    continue;
                }
                // np.roll by (sr, sc) reads from (r - sr, c - sc)
                let rr = r as isize - sr;
                let cc = c as isize - sc;
                if rr < 0 || cc < 0 || rr >= h as isize || cc >= w as isize {
                    continue;
                }
                let j = rr as usize * w + cc as usize;
                if !mask.data[j] {
                    out.data[i] = labels.data[j];
                }
            }
        }
    }
    out
}

fn pixel_colour(rgb: &Image, alpha: &Grid<f64>, i: usize) -> [f64; 4] {
    let p = rgb.px(i);
    [p[0], p[1], p[2], alpha.data[i] * 255.0]
}

/// 1 inside, 0 outside, estimated coverage on the boundary ring — computed on
/// the region's own window (see `Cropped`).
pub fn coverage_field(
    mask: &Mask,
    label: i32,
    labels: &Labels,
    rgb: &Image,
    alpha: &Grid<f64>,
    fill_at: FillAt,
) -> Cropped {
    let Some((wr0, wr1, wc0, wc1)) = window(mask) else {
        return Cropped { field: Grid::new(1, 1), r0: 0, c0: 0 };
    };
    let m = crop_mask(mask, wr0, wr1, wc0, wc1);
    let l = crop_labels(labels, wr0, wr1, wc0, wc1);
    let (h, w) = (m.h, m.w);
    let mut field = Grid { h, w, data: m.data.iter().map(|b| *b as u8 as f64).collect() };

    let inner = m.and_not(&erode_cross(&m, true));
    let outer = dilate_cross(&m).and_not(&m);
    let ring = inner.or(&outer);
    if !ring.any() {
        return Cropped { field, r0: wr0, c0: wc0 };
    }

    let idx: Vec<usize> = (0..h * w).filter(|i| ring.data[*i]).collect();
    let xs: Vec<f64> = idx.iter().map(|i| (i % w + wc0) as f64 + 0.5).collect();
    let ys: Vec<f64> = idx.iter().map(|i| (i / w + wr0) as f64 + 0.5).collect();
    let pixel: Vec<[f64; 4]> = idx
        .iter()
        .map(|i| {
            let g = (i / w + wr0) * rgb.w + (i % w + wc0);
            pixel_colour(rgb, alpha, g)
        })
        .collect();
    let f_in = fill_at(label, &xs, &ys);

    // the "other side" label: for inner-ring pixels a 4-neighbour outside the
    // mask, for outer-ring pixels the pixel's own label
    let outside = outside_label(&l, &m);
    let other: Vec<i32> = idx
        .iter()
        .map(|i| if m.data[*i] { outside.data[*i] } else { l.data[*i] })
        .collect();

    let mut f_out = f_in.clone();
    let mut labs: Vec<i32> = other.clone();
    labs.sort_unstable();
    labs.dedup();
    for lab in labs {
        if lab == 0 {
            continue; // no neighbour information: fall back to the binary field
        }
        let sel: Vec<usize> = (0..idx.len()).filter(|k| other[*k] == lab).collect();
        let sx: Vec<f64> = sel.iter().map(|k| xs[*k]).collect();
        let sy: Vec<f64> = sel.iter().map(|k| ys[*k]).collect();
        let v = fill_at(lab, &sx, &sy);
        for (n, k) in sel.iter().enumerate() {
            f_out[*k] = v[n];
        }
    }

    for k in 0..idx.len() {
        let mut denom = 0.0;
        let mut proj = 0.0;
        for c in 0..4 {
            let d = f_in[k][c] - f_out[k][c];
            denom += d * d;
            proj += (pixel[k][c] - f_out[k][c]) * d;
        }
        let cov = if denom > 1e-6 { proj / denom.max(1e-6) } else { field.data[idx[k]] };
        field.data[idx[k]] = cov.clamp(0.0, 1.0);
    }
    Cropped { field, r0: wr0, c0: wc0 }
}

/// Coverage for a *thin* region, whose pixels are all mixtures.
///
/// The region's own fitted fill is unreliable (it was fitted to mixtures), so
/// the ink colour is taken as the pixel farthest from the surrounding fill and
/// every pixel's coverage is its projection onto the ink–background segment.
/// Returned frame-sized (the callers index it by pixel) but only ever written
/// inside the region's own window.
pub fn thin_coverage(
    mask: &Mask,
    labels: &Labels,
    rgb: &Image,
    alpha: &Grid<f64>,
    fill_at: FillAt,
) -> Grid<f64> {
    let (fh, fw) = (mask.h, mask.w);
    let mut field = Grid::<f64>::new(fh, fw);
    let Some((wr0, wr1, wc0, wc1)) = window(mask) else {
        return field;
    };
    // the neighbour lookup only needs the region's own window; the result is
    // still returned frame-sized because the callers index it by pixel
    let m = crop_mask(mask, wr0, wr1, wc0, wc1);
    let l = crop_labels(labels, wr0, wr1, wc0, wc1);
    let w = m.w;
    let idx: Vec<usize> = (0..m.len()).filter(|i| m.data[*i]).collect();
    if idx.is_empty() {
        return field;
    }
    let global = |i: usize| -> usize { (i / w + wr0) * fw + (i % w + wc0) };
    let xs: Vec<f64> = idx.iter().map(|i| (i % w + wc0) as f64 + 0.5).collect();
    let ys: Vec<f64> = idx.iter().map(|i| (i / w + wr0) as f64 + 0.5).collect();
    let pixel: Vec<[f64; 4]> = idx.iter().map(|i| pixel_colour(rgb, alpha, global(*i))).collect();
    let outside = outside_label(&l, &m);
    let other: Vec<i32> = idx.iter().map(|i| outside.data[*i]).collect();

    let mut f_out = pixel.clone();
    let mut labs: Vec<i32> = other.clone();
    labs.sort_unstable();
    labs.dedup();
    for lab in labs {
        if lab == 0 {
            continue;
        }
        let sel: Vec<usize> = (0..idx.len()).filter(|k| other[*k] == lab).collect();
        let sx: Vec<f64> = sel.iter().map(|k| xs[*k]).collect();
        let sy: Vec<f64> = sel.iter().map(|k| ys[*k]).collect();
        let v = fill_at(lab, &sx, &sy);
        for (n, k) in sel.iter().enumerate() {
            f_out[*k] = v[n];
        }
    }

    // pixels with no outside neighbour: use the region-wide median background
    let missing: Vec<usize> = (0..idx.len()).filter(|k| other[*k] == 0).collect();
    let present: Vec<usize> = (0..idx.len()).filter(|k| other[*k] != 0).collect();
    if !missing.is_empty() && !present.is_empty() {
        let mut med = [0.0f64; 4];
        for c in 0..4 {
            let mut vals: Vec<f64> = present.iter().map(|k| f_out[*k][c]).collect();
            vals.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let n = vals.len();
            med[c] = if n % 2 == 1 { vals[n / 2] } else { 0.5 * (vals[n / 2 - 1] + vals[n / 2]) };
        }
        for k in missing {
            f_out[k] = med;
        }
    }

    let mut best = 0usize;
    let mut best_contrast = f64::NEG_INFINITY;
    for k in 0..idx.len() {
        let mut s = 0.0;
        for c in 0..4 {
            let d = pixel[k][c] - f_out[k][c];
            s += d * d;
        }
        if s > best_contrast {
            best_contrast = s;
            best = k;
        }
    }
    let ink = pixel[best];

    for k in 0..idx.len() {
        let mut denom = 0.0;
        let mut proj = 0.0;
        for c in 0..4 {
            let d = ink[c] - f_out[k][c];
            denom += d * d;
            proj += (pixel[k][c] - f_out[k][c]) * d;
        }
        let mut cov = if denom > 1e-6 { proj / denom.max(1e-6) } else { 1.0 };
        // Against transparency the alpha channel *is* the coverage, and it
        // resolves the ambiguity a projection cannot (a 0.5 px black line
        // against a 1 px grey one).
        if f_out[k][3] < 40.0 {
            cov = pixel[k][3] / 255.0;
        }
        field.data[global(idx[k])] = cov.clamp(0.0, 1.0);
    }
    field
}

/// Closed 0.5-level iso-contours as xy point lists in SVG pixel space.
pub fn contours(cropped: &Cropped, min_area: f64) -> Vec<Vec<[f64; 2]>> {
    let padded = pad(&cropped.field, 0.0);
    let (dx, dy) = (cropped.c0 as f64, cropped.r0 as f64);
    let mut out: Vec<Vec<[f64; 2]>> = Vec::new();
    for c in find_contours(&padded, 0.5) {
        if c.len() < 4 {
            continue;
        }
        let mut xy: Vec<[f64; 2]> = c.iter().map(|(r, cc)| [cc - 1.0 + 0.5 + dx, r - 1.0 + 0.5 + dy]).collect();
        let first = xy[0];
        let last = xy[xy.len() - 1];
        // np.allclose(xy[0], xy[-1])
        let close = |a: f64, b: f64| (a - b).abs() <= 1e-8 + 1e-5 * b.abs();
        if close(first[0], last[0]) && close(first[1], last[1]) {
            xy.pop();
        }
        if xy.len() < 3 {
            continue;
        }
        if polygon_area(&xy) < min_area {
            continue;
        }
        out.push(xy);
    }
    out
}

pub fn polygon_area(xy: &[[f64; 2]]) -> f64 {
    let n = xy.len();
    let mut s = 0.0;
    for i in 0..n {
        let j = (i + 1) % n;
        s += xy[i][0] * xy[j][1] - xy[i][1] * xy[j][0];
    }
    (0.5 * s).abs()
}
