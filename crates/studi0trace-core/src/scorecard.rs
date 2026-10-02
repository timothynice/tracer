//! The artifact scorecard and the fidelity assessment of one trace: a port of
//! `quality.Reference`, `scorecard`, `artifact_index`, `is_clean`, `assess` and
//! `ARTIFACT_KEYS`, composed of the modules each number comes from ([`crate::holes`] for the
//! pinholes, [`crate::geometry`] for the outline measures, [`crate::color`] and
//! [`crate::edges`] for the fidelity).
//!
//! A card is a [`serde_json::Map`] in the order the Python's dictionary has, so that it can be
//! written out as it stands: the fidelity keys (`delta_e_mean`, `delta_e_p95`, `edge_f1`), the
//! hole keys (`hole_subpx`, `hole_px`, `hole_clusters`, `pinholes`), the geometry keys
//! (`elements` ... `segments_100px`), and last `artifact_index`. Keys that start with `_` are
//! locations (where each defect is), and only [`ScoreOptions::detail`] keeps them.
//!
//! # What it takes to agree with the Python
//!
//! Every number but two is what the modules it is built from already hold to the bit (on the
//! platform the fixtures were exported on; see `tests/scorecard.rs`). The two are
//! `delta_e_mean` and `delta_e_p95`: the Lab of a colour differs from scikit-image's by up to
//! 1e-13 (numpy's matrix product fuses; [`crate::color::lab`] cannot). The mean is numpy's own
//! pairwise sum (`edges::pairwise_sum`), so that is the only difference left: over the
//! 96 corpus items `tools.diffcheck scorecard` measured 2.0e-14 relative for the mean and
//! 9.2e-14 for the 95th percentile, far below anything that is ranked on them. (A left-to-right
//! sum was 2e-11 relative off on a 4.2 MP trace, and grows with the pixel count.)
//!
//! # What it refuses that the Python does not
//!
//! - **Source shapes.** A [`Reference`] of no pixels, or of bytes that are not `h x w x 4`, is
//!   an [`ScoreError::Source`] (numpy raises on the first, and cannot hold the second).
//! - **Deep nesting.** The first thing done with an SVG is [`drawing::check_nesting`], which
//!   refuses what nests deeper than [`drawing::MAX_DEPTH`] ([`DrawingError::TooDeep`], as
//!   [`geometry::card`] would, but before the render): resvg's parser recurses with the depth
//!   of the SVG and 100 000 nested groups overflow any thread's stack.
//! - **A card with a term missing.** [`artifact_index`] of a card that lacks a term, or holds
//!   something that is no number there, is NaN where the Python raises `KeyError`, and
//!   [`is_clean`] of one is `false` (a NaN `artifact_index` is `null` in a card, as every
//!   non-finite number is in JSON). The one exception is `rect_skewed`, which [`is_clean`]
//!   reads with a default of 0, as the Python does: older results do not have it.
//!
//! # Stack and memory
//!
//! The nesting guard stops what no thread could parse, not what is merely deep: an SVG nested
//! near [`drawing::MAX_DEPTH`] takes resvg about 3.5 KB of stack a level to render, so 987
//! groups want about 3.5 MiB in an optimised build (a 2 MiB thread, a rayon worker's or a
//! test's, overflows past about 600) and far more in an unoptimised one. The SVGs the engine
//! writes nest a few levels. A caller that scores an SVG it did not make should do it on a
//! thread with room, as `tests/scorecard.rs` does.
//!
//! A [`Reference`] holds about 34 bytes a pixel (the source, its composite on white, its Lab as
//! three `f64`s, and three masks): about 140 MB for the default intake's largest image
//! (2048 x 2048), 1.4 GB at 40 MP. The Python's holds the same arrays.
use crate::color::{delta_e_lab, lab, rgb_on_white};
use crate::drawing::{self, DrawingError};
use crate::edges;
use crate::geometry::{self, CardError, ID_SCALE};
use crate::holes::{self, HolesError, HOLE_SCALE};
use crate::render::{self, RenderError};
use serde_json::{Map, Value};
use std::fmt;

/// `quality.assess`: a source of at most this many pixels (640 x 640) is looked at for holes at
/// [`HOLE_SCALE`] times its size; a larger one at [`LARGE_HOLE_SCALE`] times, so that the render
/// stays affordable.
pub const FULL_SCALE_PIXELS: usize = 640 * 640;
/// `quality.assess`: the hole scale above [`FULL_SCALE_PIXELS`].
pub const LARGE_HOLE_SCALE: u32 = 2;
/// `quality.is_clean`: a trace whose wobble is this many degrees per 100 px of outline, or
/// more, is not clean.
pub const CLEAN_WOBBLE: f64 = 25.0;
/// `quality._edge_f1`'s `tolerance_px` as `Reference.fidelity` and `Reference.edges_wide` use it.
const EDGE_TOLERANCE_PX: usize = 2;

/// `quality.ARTIFACT_KEYS`: the keys of a card that count defects or measure the drawing, in
/// the order the bench reports them.
pub const ARTIFACT_KEYS: [&str; 18] = [
    "hole_subpx", "hole_px", "hole_clusters", "pinholes", "slivers", "sliver_area_px", "degenerate", "thin_strokes",
    "wobble_deg_100px", "inflections", "rect_like", "radius_inconsistent", "rect_bowed", "rect_skewed", "elements", "segments",
    "segments_100px", "artifact_index",
];

/// Why a source could not be scored, or an SVG could not be.
#[derive(Debug, Clone, PartialEq)]
pub enum ScoreError {
    /// The source pixels or the rendered ones are not an `h x w x 4` image of at least one pixel.
    Source(String),
    /// The SVG did not render ([`render::render`]).
    Render(RenderError),
    /// The hole count failed ([`holes::holes`]): a render that did not come out, or a hole
    /// scale of 0.
    Holes(HolesError),
    /// The outline measures failed, or the SVG nests too deep to be read at all.
    Card(CardError),
}

impl fmt::Display for ScoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ScoreError::Source(e) => write!(f, "{e}"),
            ScoreError::Render(e) => write!(f, "the SVG does not render: {e}"),
            ScoreError::Holes(e) => write!(f, "holes cannot be counted: {e}"),
            ScoreError::Card(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for ScoreError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            ScoreError::Card(e) => Some(e),
            _ => None,
        }
    }
}

impl From<CardError> for ScoreError {
    fn from(e: CardError) -> Self {
        ScoreError::Card(e)
    }
}

/// `quality.Reference`: everything about the source that a score needs, computed once for every
/// candidate traced from it: its colour on white and in Lab, its edges (and those dilated by a
/// disk of 2 px, which is how far an edge may be from the trace's and still match), and the
/// opaque interior the hole count looks in. The fields are the Python's attributes, image
/// arrays flattened row-major.
#[derive(Clone)]
pub struct Reference {
    /// The source as it was given, `height * width * 4` bytes.
    pub rgba: Vec<u8>,
    pub height: usize,
    pub width: usize,
    /// The source composited on white ([`rgb_on_white`]), `height * width * 3` bytes.
    pub rgb: Vec<u8>,
    /// [`lab`] of `rgb`, one triple per pixel.
    pub lab: Vec<[f64; 3]>,
    /// Canny edges of `rgb` ([`edges::edges`]).
    pub edges: Vec<bool>,
    /// `edges` dilated by a disk of radius 2 ([`edges::dilate_disk`]).
    pub edges_wide: Vec<bool>,
    /// The opaque interior of `rgba` ([`holes::opaque`]).
    pub opaque: Vec<bool>,
}

impl fmt::Debug for Reference {
    // a megapixel of Lab triples is not a message
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Reference {{ {} x {} }}", self.width, self.height)
    }
}

/// The pixel count of a `h x w` RGBA image, which may be none, if its sides fit a `u32` and its
/// bytes (4 a pixel) a `usize`: the check that comes before any `pixels * 4`.
fn checked_pixels(h: usize, w: usize) -> Result<usize, ScoreError> {
    let sides = u32::try_from(h).is_ok() && u32::try_from(w).is_ok();
    h.checked_mul(w).filter(|p| sides && p.checked_mul(4).is_some()).ok_or_else(|| ScoreError::Source(format!("a {w}x{h} image is too large")))
}

/// [`checked_pixels`], and at least one: a reference of no pixels cannot be built.
fn pixels_of(h: usize, w: usize) -> Result<usize, ScoreError> {
    match checked_pixels(h, w)? {
        0 => Err(ScoreError::Source(format!("a {w}x{h} image has no pixels"))),
        p => Ok(p),
    }
}

impl Reference {
    /// The reference of an RGBA source of `h` rows of `w` pixels (`src_rgba` is `h * w * 4`
    /// bytes, row-major). Refuses (as numpy does, and cannot hold) an image of no pixels, or
    /// one whose bytes are not that many.
    pub fn new(src_rgba: &[u8], h: usize, w: usize) -> Result<Reference, ScoreError> {
        let pixels = pixels_of(h, w)?;
        if src_rgba.len() != pixels * 4 {
            return Err(ScoreError::Source(format!("{} bytes of RGBA for a {w}x{h} image, which has {}", src_rgba.len(), pixels * 4)));
        }
        let rgb = rgb_on_white(src_rgba);
        let lab = lab(&rgb);
        let edges = edges::edges(&rgb, h, w);
        let edges_wide = edges::dilate_disk(&edges, h, w, EDGE_TOLERANCE_PX);
        let opaque = holes::opaque(src_rgba, h, w);
        Ok(Reference { rgba: src_rgba.to_vec(), height: h, width: w, rgb, lab, edges, edges_wide, opaque })
    }

    /// `Reference.fidelity`: how far a render of a trace (`out_rgba`, straight-alpha RGBA of
    /// this source's size) is from the source: the mean and 95th percentile of the CIEDE2000
    /// between them, and the F1 of their edges matched within 2 px. The keys are `delta_e_mean`,
    /// `delta_e_p95` and `edge_f1`, in that order.
    pub fn fidelity(&self, out_rgba: &[u8]) -> Result<Map<String, Value>, ScoreError> {
        let want = self.width * self.height * 4;
        if out_rgba.len() != want {
            return Err(ScoreError::Source(format!("{} bytes of RGBA for a {}x{} render, which has {want}", out_rgba.len(), self.width, self.height)));
        }
        let out_rgb = rgb_on_white(out_rgba);
        let (mean, p95) = delta_e_lab(&self.lab, &lab(&out_rgb));
        let out_edges = edges::edges(&out_rgb, self.height, self.width);
        let f1 = edges::f1(&self.edges, &out_edges, Some(&self.edges_wide), self.height, self.width, EDGE_TOLERANCE_PX);
        let mut m = Map::new();
        m.insert("delta_e_mean".into(), Value::from(mean));
        m.insert("delta_e_p95".into(), Value::from(p95));
        m.insert("edge_f1".into(), Value::from(f1));
        Ok(m)
    }
}

/// What `quality.scorecard` takes besides the SVG and the source. [`Default`] is the Python's
/// defaults.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ScoreOptions<'a> {
    /// Keep the `_` keys, which list where each defect is (`detail`). Default off.
    pub detail: bool,
    /// Score only the outline that is on screen (`visibility`). Default on.
    pub visibility: bool,
    /// The render scale the holes are looked for at ([`HOLE_SCALE`]).
    pub hole_scale: u32,
    /// The render scale of the id map that tells what is on screen ([`ID_SCALE`]).
    pub id_scale: u32,
    /// The source's [`holes::opaque`] interior if the caller has it already (a
    /// [`Reference`] does); when it is given the source's pixels are not read.
    pub opaque: Option<&'a [bool]>,
}

impl Default for ScoreOptions<'_> {
    fn default() -> Self {
        ScoreOptions { detail: false, visibility: true, hole_scale: HOLE_SCALE, id_scale: ID_SCALE, opaque: None }
    }
}

/// `quality.scorecard`: the artifact counts of one trace of an `h x w` RGBA source. The keys
/// are `hole_subpx`, `hole_px`, `hole_clusters`, `pinholes`, the geometry card's keys
/// ([`geometry::card`], sized to the source) and `artifact_index`; the `_` keys only with
/// [`ScoreOptions::detail`].
pub fn scorecard(svg: &str, src_rgba: &[u8], h: usize, w: usize, opts: &ScoreOptions) -> Result<Map<String, Value>, ScoreError> {
    check_nesting(svg)?;
    card_of(svg, src_rgba, h, w, opts)
}

/// The SVG is read by a recursive parser; see [`drawing::check_nesting`].
fn check_nesting(svg: &str) -> Result<(), ScoreError> {
    drawing::check_nesting(svg).map_err(|e: DrawingError| ScoreError::Card(CardError::Drawing(e)))
}

fn card_of(svg: &str, src_rgba: &[u8], h: usize, w: usize, opts: &ScoreOptions) -> Result<Map<String, Value>, ScoreError> {
    // before `holes` multiplies by 4: a size whose bytes do not fit is refused, not wrapped (a
    // source of no pixels is not: `holes` answers it with zeros, as the Python's does)
    checked_pixels(h, w)?;
    let size = (u32::try_from(w).expect("checked_pixels"), u32::try_from(h).expect("checked_pixels"));
    // the Python's dictionary literal evaluates the holes first
    let holes = holes::holes(svg, src_rgba, h, w, opts.hole_scale, opts.opaque).map_err(ScoreError::Holes)?;
    let mut card = holes.to_map();
    card.extend(geometry::card(svg, Some(size), opts.visibility, opts.id_scale)?);
    let index = artifact_index(&card);
    card.insert("artifact_index".into(), Value::from(index));
    if !opts.detail {
        card.retain(|k, _| !k.starts_with('_'));
    }
    Ok(card)
}

/// The number a card holds under `key`, if it holds one.
fn number(card: &Map<String, Value>, key: &str) -> Option<f64> {
    card.get(key).and_then(Value::as_f64)
}

/// `quality.artifact_index`: one number for ranking cleanliness, 0 = clean. Each term is
/// scaled so that one clearly visible defect of its kind costs about 1. NaN if the card lacks
/// a term (the Python raises `KeyError`).
pub fn artifact_index(card: &Map<String, Value>) -> f64 {
    let terms = [
        "pinholes", "hole_clusters", "slivers", "degenerate", "thin_strokes", "radius_inconsistent", "rect_bowed", "rect_skewed",
        "wobble_deg_100px", "inflections",
    ]
    .map(|k| number(card, k));
    let [Some(pinholes), Some(hole_clusters), Some(slivers), Some(degenerate), Some(thin_strokes), Some(radius_inconsistent), Some(rect_bowed), Some(rect_skewed), Some(wobble), Some(inflections)] =
        terms
    else {
        return f64::NAN;
    };
    // Python's `max(0, x)`: x only when it is greater
    let clusters_that_are_not_pinholes = {
        let d = hole_clusters - pinholes;
        if d > 0.0 {
            d
        } else {
            0.0
        }
    };
    1.0 * pinholes
        + 0.1 * clusters_that_are_not_pinholes
        + 1.0 * slivers
        + 1.0 * degenerate
        + 1.0 * thin_strokes
        + 0.5 * (radius_inconsistent + rect_bowed + rect_skewed)
        + 0.25 * wobble
        + 1.0 * inflections
}

/// `quality.is_clean`: no visible defect of any kind the bench tracks: no pinhole, no sliver or
/// sub-pixel stroke, wobble under [`CLEAN_WOBBLE`] degrees per 100 px, no uneven rectangle.
/// The same test `bench.presets_report` counts as CLEAN. A card with no `rect_skewed` counts it
/// as 0; one that lacks any other term is not clean (the Python raises `KeyError`).
pub fn is_clean(card: &Map<String, Value>) -> bool {
    let rect_skewed = match card.get("rect_skewed") {
        None => Some(0.0),
        Some(v) => v.as_f64(),
    };
    let terms = ["pinholes", "slivers", "degenerate", "thin_strokes", "wobble_deg_100px", "radius_inconsistent", "rect_bowed"].map(|k| number(card, k));
    let ([Some(pinholes), Some(slivers), Some(degenerate), Some(thin_strokes), Some(wobble), Some(radius_inconsistent), Some(rect_bowed)], Some(rect_skewed)) =
        (terms, rect_skewed)
    else {
        return false;
    };
    pinholes == 0.0
        && slivers + degenerate + thin_strokes == 0.0
        && wobble < CLEAN_WOBBLE
        && radius_inconsistent + rect_bowed + rect_skewed == 0.0
}

/// The hole scale `quality.assess` picks for a `width x height` source when it is not told:
/// [`HOLE_SCALE`] up to 640 x 640 pixels, [`LARGE_HOLE_SCALE`] above, so that a large image
/// stays affordable. Every candidate of one image is scored at the same scale.
pub fn default_hole_scale(width: usize, height: usize) -> u32 {
    if (width as u128) * (height as u128) <= FULL_SCALE_PIXELS as u128 {
        HOLE_SCALE
    } else {
        LARGE_HOLE_SCALE
    }
}

/// `quality.assess`: the fidelity and the scorecard of one trace of `r`'s source, as one card:
/// `delta_e_mean`, `delta_e_p95`, `edge_f1`, then [`scorecard`]'s keys (without the `_` ones).
/// The SVG is rendered anti-aliased at the source's size for the fidelity. `hole_scale` is
/// [`default_hole_scale`] when `None`.
pub fn assess(svg: &str, r: &Reference, hole_scale: Option<u32>) -> Result<Map<String, Value>, ScoreError> {
    // before the first render: resvg's parser recurses with the depth of the SVG
    check_nesting(svg)?;
    let hole_scale = hole_scale.unwrap_or_else(|| default_hole_scale(r.width, r.height));
    // the sides fit a u32: `Reference::new` checked
    let out = render::render(svg, r.width as u32, r.height as u32, false).map_err(ScoreError::Render)?;
    let mut card = r.fidelity(&out)?;
    drop(out);
    let opts = ScoreOptions { hole_scale, opaque: Some(&r.opaque), ..ScoreOptions::default() };
    card.extend(card_of(svg, &r.rgba, r.height, r.width, &opts)?);
    Ok(card)
}
