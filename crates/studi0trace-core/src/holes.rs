//! Holes: the parts of a trace that leave the source showing through. A port of
//! `quality.holes` and of the opaque mask `quality.Reference` caches for it.
//!
//! The trace is rendered at `scale` times the source's size, anti-aliased. Wherever the source
//! is solidly opaque (its [`opaque`] interior), a sub-pixel the render covers less than
//! [`HOLE_COVER`] is short. Short sub-pixels that touch, corners included, make a cluster; a
//! cluster with a sub-pixel covered under [`PINHOLE_COVER`] is a pinhole, which is visible as a
//! tick. That is the count the bench's artifact index weighs most.
//!
//! The port is to the bit: every number is what the Python computes, in the order it computes
//! it. What a naive port gets wrong, each found by experiment against numpy 2.5 and scipy 1.18
//! (and pinned by `tests/holes.rs` against `holes.json`):
//!
//! - **Opaque is float32.** `src_rgba[..., 3].astype(np.float32) / 255.0 >= 0.99`: the Python
//!   `0.99` is a weak scalar and meets a float32 array as a float32, so the test is
//!   `f32(alpha) / 255f32 >= 0.99f32`. It comes to alpha 253 and up, in float64 too; the port
//!   keeps the float32 so that it does not depend on that.
//! - **Erosion has a border of nothing.** `binary_erosion(mask, ones((3, 3)), border_value=0)`
//!   reads outside the image as false, so the outermost ring of pixels is never opaque, and a
//!   source under 3 px across has no interior at all. [`vexel_rs::core::morphology::erode_square`]
//!   is that function and is used as it is; `tests/holes.rs` holds it to scipy on random masks.
//! - **Short is alpha 242 and down.** `alpha < 0.95 * 255.0` compares a `uint8` with a float64
//!   242.25. Each source pixel is a `scale x scale` block of sub-pixels, all of which take the
//!   pixel's opaque flag.
//! - **Clusters are numbered by `ndimage.label(short, ones((3, 3)))`**: 8-connected, in the
//!   raster order of each cluster's first sub-pixel. [`vexel_rs::core::labels::label_mask`] with
//!   connectivity 2 numbers them the same way (held to scipy on random masks in the tests).
//!   The numbering shows in the answer: the clusters are listed by deficit, and equal deficits
//!   (equal holes are common) stay in label order because Python's `list.sort` is stable, as is
//!   `sort_by`.
//! - **`np.bincount(labels, weights=w)` adds sequentially**, in the index order of
//!   `np.nonzero(short)`: row-major. The sums of `1 - cover`, of `y` and of `x` are accumulated
//!   in that order here, one running total per cluster.
//! - **`(1.0 - cover).sum()` is numpy's pairwise sum**, which differs from a running sum by
//!   about 3e-14 relative on 200 000 terms. It is `edges::pairwise_sum`, the same port of
//!   `DOUBLE_pairwise_sum` the Gaussian's normalisation uses, so `hole_px` is the Python's to
//!   the bit.
//! - `np.minimum.at` is an ordinary minimum; `x / scale` and `deficit / scale ** 2` are float64
//!   divisions by the integers (as floats), and a centre is divided by the cluster's size
//!   before it is divided by the scale.
//! - **The early returns.** A source with no opaque pixel (an empty or transparent one, or one
//!   with a side under 3 px, or a zero-sized one) answers with zeros before the SVG is
//!   looked at, so an SVG that does not parse and a scale of 0 do not raise for it. A render
//!   with nothing short answers with zeros too.
//!
//! # Memory
//!
//! A render of `w * scale x h * scale` is 4 bytes a sub-pixel, and it is dropped once the short
//! sub-pixels are found. Labelling then holds 8 bytes a sub-pixel (`label_mask`'s provisional
//! and final labels) plus one flag, which is about 0.6 GB for the default intake's largest
//! source (4096 x 4096) at 2x (67 M sub-pixels) and 1.5 GB for 40 MP (160 M) when the holes
//! are small. The worst case is one giant hole, and then what is built from the labels comes
//! to about 26.6 bytes a sub-pixel (measured): about 1.8 GB at 67 M sub-pixels, 4 GB at 160 M. The Python holds arrays of the same order (about 70 bytes a sub-pixel).
//! The scorecard renders large sources at 2x (`quality.assess`), and [`render::MAX_PIXELS`]
//! refuses a render of over 2^28 sub-pixels.
//!
//! # The SVG
//!
//! The SVG goes straight to [`render::render`], as in the Python. `render` (resvg, roxmltree)
//! recurses with the SVG's nesting, and refuses what nests deeper than
//! [`crate::drawing::MAX_DEPTH`] before it parses.
use crate::edges::pairwise_sum;
use crate::render;
use serde_json::{Map, Value};
use vexel_rs::core::grid::Grid;
use vexel_rs::core::labels::label_mask;
use vexel_rs::core::morphology::erode_square;

/// `quality.HOLE_SCALE`: the render scale the scorecard looks for holes at.
pub const HOLE_SCALE: u32 = 4;
/// `quality.HOLE_COVER`: a sub-pixel covered less than this, where the source is opaque, is a hole.
pub const HOLE_COVER: f64 = 0.95;
/// `quality.PINHOLE_COVER`: a hole cluster with a sub-pixel under this is visible as a tick.
pub const PINHOLE_COVER: f64 = 0.5;

/// A source's opaque interior: one flag per source pixel, row-major, set where the alpha is at
/// least 0.99 and so are all eight neighbours' (`quality.Reference.opaque`).
pub type Opaque = Vec<bool>;

/// One cluster of adjacent short sub-pixels (one entry of the Python's `_clusters`).
#[derive(Debug, Clone, PartialEq)]
pub struct Cluster {
    /// Where its sub-pixels are centred, in source pixels (`x` is a column, `y` a row). A
    /// sub-pixel is counted at its own index, so a lone one in column 5 of a 4x render is at 1.25.
    pub x: f64,
    pub y: f64,
    /// How many sub-pixels it has.
    pub subpx: usize,
    /// The least cover (0 to 1) of any of them.
    pub min_cover: f64,
    /// How much source area it leaves showing, in source pixels: the sum of `1 - cover`, over `scale ** 2`.
    pub deficit_px: f64,
    /// Whether `min_cover` is under [`PINHOLE_COVER`].
    pub pinhole: bool,
}

impl Cluster {
    /// This cluster as the Python's dictionary: `x`, `y`, `subpx`, `min_cover`, `deficit_px`, `pinhole`.
    pub fn to_map(&self) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("x".into(), Value::from(self.x));
        m.insert("y".into(), Value::from(self.y));
        m.insert("subpx".into(), Value::from(self.subpx as u64));
        m.insert("min_cover".into(), Value::from(self.min_cover));
        m.insert("deficit_px".into(), Value::from(self.deficit_px));
        m.insert("pinhole".into(), Value::from(self.pinhole));
        m
    }
}

/// What `quality.holes` returns.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Holes {
    /// Short sub-pixels in all.
    pub hole_subpx: usize,
    /// Their total deficit, in source pixels.
    pub hole_px: f64,
    /// How many clusters they make (`clusters.len()`).
    pub hole_clusters: usize,
    /// How many of those are pinholes.
    pub pinholes: usize,
    /// The clusters, largest deficit first; equal deficits in the order the clusters were found.
    pub clusters: Vec<Cluster>,
}

impl Holes {
    /// The dictionary `quality.holes` returns, with its keys in its order: `hole_subpx`,
    /// `hole_px`, `hole_clusters`, `pinholes`, and `_clusters` as an array of
    /// [`Cluster::to_map`] objects. (The scorecard merges this into its own card and drops
    /// `_clusters`, a location key, unless the detail is asked for.)
    pub fn to_map(&self) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("hole_subpx".into(), Value::from(self.hole_subpx as u64));
        m.insert("hole_px".into(), Value::from(self.hole_px));
        m.insert("hole_clusters".into(), Value::from(self.hole_clusters as u64));
        m.insert("pinholes".into(), Value::from(self.pinholes as u64));
        m.insert("_clusters".into(), Value::Array(self.clusters.iter().map(|c| Value::Object(c.to_map())).collect()));
        m
    }
}

/// The opaque interior of a source (`quality.Reference.opaque`): where its alpha is at least
/// 0.99 (float32 arithmetic, so alpha 253 and up), eroded by a 3 x 3 square with nothing outside
/// the image, so the outermost ring of pixels is never in it. `src_rgba` is `h x w x 4` bytes,
/// row-major; the result has `h * w` flags.
///
/// # Panics
///
/// If `src_rgba` is not `h * w * 4` bytes. ([`holes`] checks that first and reports it.)
pub fn opaque(src_rgba: &[u8], h: usize, w: usize) -> Opaque {
    assert_eq!(h.checked_mul(w).and_then(|p| p.checked_mul(4)), Some(src_rgba.len()), "opaque: {} bytes for a {h}x{w} RGBA image", src_rgba.len());
    let solid: Vec<bool> = src_rgba.chunks_exact(4).map(|p| p[3] as f32 / 255.0f32 >= 0.99f32).collect();
    erode_square(&Grid::from_vec(h, w, solid), false).data
}

/// `quality.holes(svg, src_rgba, scale, opaque)`: render `svg` at `scale` times the source's
/// size and count the clusters of sub-pixels it leaves under-covered where the source is
/// opaque. `src_rgba` is the source, `h x w x 4` bytes; `opaque` is its [`opaque`] interior if
/// the caller has it already, and is computed from `src_rgba` if not (when it is given the
/// source itself is not read).
///
/// The Python raises where this returns `Err`: a `scale` of 0 (`The value of 'width' must be a
/// positive integer`) or an SVG that does not render, but only for a source that has an opaque
/// pixel; the others are answered with zeros first. The port adds what numpy would refuse by its
/// shapes: a source or a mask of another size than `h x w`.
pub fn holes(svg: &str, src_rgba: &[u8], h: usize, w: usize, scale: u32, opaque: Option<&[bool]>) -> Result<Holes, String> {
    let pixels = h.checked_mul(w).filter(|p| p.checked_mul(4).is_some()).ok_or_else(|| format!("holes: a {h}x{w} source is too large"))?;
    let computed;
    let mask: &[bool] = match opaque {
        Some(m) if m.len() != pixels => return Err(format!("holes: a mask of {} flags for a {h}x{w} source, which has {pixels}", m.len())),
        Some(m) => m,
        None if src_rgba.len() != pixels * 4 => {
            return Err(format!("holes: {} bytes of RGBA for a {h}x{w} source, which has {}", src_rgba.len(), pixels * 4))
        }
        None => {
            computed = self::opaque(src_rgba, h, w);
            &computed
        }
    };
    if !mask.iter().any(|&b| b) {
        return Ok(Holes::default());
    }

    let s = scale as usize;
    let too_large = || format!("holes: a {w}x{h} source at {scale}x is too large to render");
    let (bw, bh) = (w.checked_mul(s).ok_or_else(too_large)?, h.checked_mul(s).ok_or_else(too_large)?);
    let rgba = render::render(svg, u32::try_from(bw).map_err(|_| too_large())?, u32::try_from(bh).map_err(|_| too_large())?, false)?;
    if rgba.len() != bw * bh * 4 {
        return Err(format!("holes: a {bw}x{bh} render came back with {} bytes", rgba.len()));
    }

    // Every sub-pixel under the cover, where the source is opaque, in row-major order (what
    // `np.nonzero` lists): its place in the render and its alpha.
    let thresh = HOLE_COVER * 255.0;
    let mut short = vec![false; bw * bh];
    let mut at: Vec<u32> = Vec::new();
    let mut alpha: Vec<u8> = Vec::new();
    for sy in 0..bh {
        let opaque_row = &mask[(sy / s) * w..(sy / s + 1) * w];
        let row = &rgba[sy * bw * 4..(sy + 1) * bw * 4];
        for (x, _) in opaque_row.iter().enumerate().filter(|(_, o)| **o) {
            for sx in x * s..(x + 1) * s {
                let a = row[sx * 4 + 3];
                if (a as f64) < thresh {
                    short[sy * bw + sx] = true;
                    at.push((sy * bw + sx) as u32);
                    alpha.push(a);
                }
            }
        }
    }
    drop(rgba);
    if at.is_empty() {
        return Ok(Holes::default());
    }

    // One running total per cluster, added to in the order of `at`, as `np.bincount` adds.
    struct Total {
        size: usize,
        miss: f64,
        ys: f64,
        xs: f64,
        least: f64,
    }
    let labels = label_mask(&Grid::from_vec(bh, bw, short), 2);
    let mut totals: Vec<Total> = Vec::new();
    let mut miss = Vec::with_capacity(at.len());
    for (&i, &a) in at.iter().zip(&alpha) {
        let label = labels.data[i as usize];
        assert!(label > 0, "a short sub-pixel without a label");
        let label = label as usize;
        if label > totals.len() {
            totals.resize_with(label, || Total { size: 0, miss: 0.0, ys: 0.0, xs: 0.0, least: f64::INFINITY });
        }
        let cover = a as f64 / 255.0;
        let m = 1.0 - cover;
        miss.push(m);
        let t = &mut totals[label - 1];
        t.size += 1;
        t.miss += m;
        t.ys += (i as usize / bw) as f64;
        t.xs += (i as usize % bw) as f64;
        t.least = t.least.min(cover);
    }

    let scale_f = scale as f64;
    let scale_sq = (u64::from(scale) * u64::from(scale)) as f64;
    let mut clusters: Vec<Cluster> = totals
        .iter()
        .map(|t| Cluster {
            x: t.xs / t.size as f64 / scale_f,
            y: t.ys / t.size as f64 / scale_f,
            subpx: t.size,
            min_cover: t.least,
            deficit_px: t.miss / scale_sq,
            pinhole: t.least < PINHOLE_COVER,
        })
        .collect();
    let pinholes = clusters.iter().filter(|c| c.pinhole).count();
    // `clusters.sort(key=lambda c: -c["deficit_px"])`: stable, so equal deficits keep label order.
    clusters.sort_by(|a, b| b.deficit_px.total_cmp(&a.deficit_px));
    Ok(Holes { hole_subpx: at.len(), hole_px: pairwise_sum(&miss) / scale_sq, hole_clusters: clusters.len(), pinholes, clusters })
}
