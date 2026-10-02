//! An SVG to RGBA8 pixels with resvg, as `quality.render` gets them from `resvg_py`.
//!
//! The bench and the scorecard render with `resvg-py` 0.5.0. Its binary is resvg 0.48.1 over
//! usvg 0.48.1 and tiny-skia 0.12.0 (`resvg_py.__resvg_version__`, and the crate paths baked
//! into the wheel), not the 0.48.0 the task brief names, so this crate pins `resvg = "=0.48.1"`.
//! The same release, the same rasteriser and the same arithmetic are what make a render here
//! agree with the Python's, pixel for pixel on the fixtures (`tests/render.rs`).
//!
//! # What `svg_to_bytes(width=, height=)` does
//!
//! Found by experiment against the wheel (every line is a case in `render_cases.json`):
//!
//! - **Size.** It is resvg's CLI `--width W --height H`: the SVG's own size, rounded to
//!   whole pixels (`tree.size().to_int_size()`), is scaled to fit *inside* `W x H` with its
//!   aspect kept (`IntSize::scale_to`, whose far side rounds up), and the pixmap has that
//!   size, not `W x H`. The transform is `scale(fit.w / int_w, fit.h / int_h)` of those
//!   rounded sizes, so a 7.5 x 3.5 SVG drawn at 16 x 8 is scaled exactly 2 x 2.
//! - **Units.** It leaves usvg's `dpi` at 0, so `pt`, `in`, `mm`, `cm` and `pc` are lengths
//!   of zero and the SVG is refused as having an invalid size; `px`, `em` (16 px) and `%`
//!   work. Copied here, because the scorecard's SVGs are in unitless pixels and the
//!   reference is the definition.
//! - **Options.** Everything else it leaves at its own defaults, which are not usvg's: a font
//!   size of 16 (usvg: 12), so an `em` is 16 px, and no languages at all (usvg: `en`), so a
//!   `<switch>` skips every child with a `systemLanguage` and takes the first without one.
//! - **Crisp.** `shape_rendering="crisp_edges"` sets usvg's *default* `shape-rendering`,
//!   the one an element gets when neither it nor an ancestor says otherwise; an attribute in
//!   the SVG wins. It is the same as `optimize_speed`: no anti-aliasing on paths. Set the
//!   same way here (`Options::shape_rendering`).
//! - **Text.** `skip_system_fonts=True` leaves the font database empty, so every `<text>`
//!   lays out to nothing. This crate is built without resvg's `text`, which gives the same
//!   pixels and keeps a font stack out of the WebAssembly build; `tests/render.rs` holds the
//!   two together.
//! - **Refusals.** A width or height of 0 is a `ValueError`; an SVG that does not parse, or
//!   has a size of zero or less, is a `ValueError` with usvg's words.
//!
//! # Where this crate differs, on purpose
//!
//! - **Images.** There are no raster decoders (`raster-images` is off): an `<image>` is
//!   left out, where resvg-py would draw it. A traced SVG carries none. Nor does the core
//!   read the filesystem: usvg's default `href` resolver opens whatever path an `<image>`
//!   names, so that resolver is replaced by one that finds nothing. (A `data:` URL of a
//!   nested SVG is still resolved; it is bytes already in the document.)
//! - **Depth.** An SVG whose elements nest more than [`drawing::MAX_DEPTH`] (988) deep is refused,
//!   from its text and before resvg parses it ([`drawing::check_nesting`]): usvg's parser recurses,
//!   and 100 000 nested groups would overflow the stack of any thread (a few hundred do a 2 MiB one;
//!   a wasm build's is 1 MiB).
//! - **Size.** A render is refused above [`MAX_PIXELS`], and so is a request above it, before
//!   anything is allocated. resvg-py tries to allocate what it is asked; at 100 000 x 100 000
//!   that is a 40 GB pixmap.
//!
//! # The resize
//!
//! `quality.render` forces the size it was asked for: if the PNG resvg-py returns is another
//! size, Pillow resizes it to exactly `(width, height)` (`NEAREST` for crisp, `LANCZOS`
//! otherwise) and [`render`] does the same through [`crate::resample`]. That happens in two
//! situations. One is a box of another aspect than the SVG: Pillow stretches, it does not
//! letterbox. The other is an ordinary one: `IntSize::scale_to` computes `ceil(f32 * f32 / f32)`
//! (`th * W / H`), and once `th * W` passes 2^24 and is not a multiple of what an f32 holds
//! there, the product rounds and the fit can come out a row too tall (a 6930 x 5399 viewBox
//! drawn at 6930 x 5399 is fitted as 6930 x 5400). Measured over random sizes, the first upload
//! that does it is 16.8 MP at 1x, 2x and 4x and 5.6 MP at 3x; among sides of 4100 to 7000 px
//! about 4% of sizes do at 1x, and among sides of 1000 to 2900 px about 1.4% do at 3x. The
//! intake admits 4096 x 4096 by default (more where a shell raises its limits) and the scorecard
//! renders at the source size and at 2x to 4x, so it meets
//! this on uploads the Python handles. [`render_fit`] gives the pixels resvg made at the size it
//! made them, before any resize.
//!
//! Nothing here touches the filesystem or spawns a thread, and resvg pulls in no C code.
use crate::drawing;
use crate::resample;
use resvg::{tiny_skia, usvg};

/// The most pixels a render may have: 2^28, a 1 GiB RGBA buffer. The intake admits 4096 x 4096
/// by default and the scorecard renders at 2x, which is 67 MP, so it fits with room (as 40 MP
/// without the side cap does, at 160 MP).
pub const MAX_PIXELS: u64 = 1 << 28;

/// Why a render, or a resize, was refused. `Display` is the words the Python (resvg-py, Pillow)
/// would raise where it raises, and the port's own where it refuses what the Python would try.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RenderError {
    /// A render asked for at a width (`"width"`) or a height (`"height"`) of 0, in resvg-py's words.
    ZeroSide(&'static str),
    /// A resize from or to no pixels, in Pillow's words.
    ZeroResize,
    /// An image buffer that is not `width * height * 4` bytes.
    Buffer { width: u32, height: u32, len: usize },
    /// More than [`MAX_PIXELS`] pixels, asked for or fitted, or more than a `usize` of bytes.
    TooLarge { width: u64, height: u64 },
    /// Elements nested deeper than [`drawing::MAX_DEPTH`], refused from the text alone.
    TooDeep,
    /// The SVG does not parse (usvg's message).
    Svg(String),
    /// tiny-skia would not make the pixmap.
    Pixmap,
}

impl std::fmt::Display for RenderError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RenderError::ZeroSide(side) => write!(f, "The value of '{side}' must be a positive integer"),
            RenderError::ZeroResize => write!(f, "height and width must be > 0"),
            RenderError::Buffer { width, height, len } => write!(f, "a {width}x{height} RGBA image is not {len} bytes"),
            RenderError::TooLarge { width, height } => write!(f, "a {width}x{height} render is more than the {MAX_PIXELS} pixels allowed"),
            RenderError::TooDeep => write!(f, "{}", drawing::DrawingError::TooDeep),
            RenderError::Svg(e) => write!(f, "{e}"),
            RenderError::Pixmap => write!(f, "cannot create pixmap"),
        }
    }
}

impl std::error::Error for RenderError {}

/// A finished render: straight-alpha RGBA8, `width * height * 4` bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rendered {
    pub width: u32,
    pub height: u32,
    pub rgba: Vec<u8>,
}

/// `svg` at exactly `width x height`, straight-alpha RGBA8, anti-aliased or (`crisp`) not:
/// `quality.render`. The render is [`render_fit`]'s; if that is another size it is resized
/// to this one as Pillow does (nearest for crisp, Lanczos otherwise), whatever the reason.
pub fn render(svg: &str, width: u32, height: u32, crisp: bool) -> Result<Vec<u8>, RenderError> {
    check_box(width, height)?;
    check_pixels(width, height)?;
    let r = render_fit(svg, width, height, crisp)?;
    if (r.width, r.height) == (width, height) {
        return Ok(r.rgba);
    }
    let filter = if crisp { resample::Filter::Nearest } else { resample::Filter::Lanczos };
    resample::resize_rgba(&r.rgba, r.width, r.height, width, height, filter)
}

fn check_box(width: u32, height: u32) -> Result<(), RenderError> {
    if width == 0 {
        return Err(RenderError::ZeroSide("width"));
    }
    if height == 0 {
        return Err(RenderError::ZeroSide("height"));
    }
    Ok(())
}

/// A `width x height` image of more than [`MAX_PIXELS`] is refused, before anything is allocated.
pub(crate) fn check_pixels(width: u32, height: u32) -> Result<(), RenderError> {
    let (width, height) = (u64::from(width), u64::from(height));
    if width * height > MAX_PIXELS {
        return Err(RenderError::TooLarge { width, height });
    }
    Ok(())
}

/// What `resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height,
/// skip_system_fonts=True[, shape_rendering="crisp_edges"])` returns, decoded: the SVG
/// fitted inside `width x height` (so its size may be smaller on one side), as straight alpha.
pub fn render_fit(svg: &str, width: u32, height: u32, crisp: bool) -> Result<Rendered, RenderError> {
    check_box(width, height)?;
    // resvg's parser recurses a level of nesting at a time (about 3.5 KB of stack a level in all, so a
    // few hundred levels overflow a 2 MiB thread, and 100 000 any thread): every way into it, `render`
    // and the scorecard's, is refused here from the text alone, before anything parses it
    drawing::check_nesting(svg).map_err(|_| RenderError::TooDeep)?;

    let mut opt = usvg::Options::default();
    opt.dpi = 0.0;
    opt.font_size = 16.0;
    opt.languages = Vec::new();
    if crisp {
        opt.shape_rendering = usvg::ShapeRendering::CrispEdges;
    }
    opt.image_href_resolver.resolve_string = Box::new(|_, _| None);
    let tree = usvg::Tree::from_str(svg, &opt).map_err(|e| RenderError::Svg(e.to_string()))?;

    // resvg's `FitTo::Size`: the rounded size scaled into the box, and a transform that
    // maps the rounded size onto the result.
    let natural = tree.size().to_int_size();
    let target = tiny_skia::IntSize::from_wh(width, height).ok_or(RenderError::Pixmap)?;
    let fit = natural.scale_to(target);
    check_pixels(fit.width(), fit.height())?;
    let mut pixmap = tiny_skia::Pixmap::new(fit.width(), fit.height()).ok_or(RenderError::Pixmap)?;
    let (from, to) = (natural.to_size(), fit.to_size());
    let ts = tiny_skia::Transform::from_scale(to.width() / from.width(), to.height() / from.height());
    resvg::render(&tree, ts, &mut pixmap.as_mut());

    // resvg-py hands the pixmap to `encode_png`, which demultiplies with this function, and
    // Pillow reads the straight bytes back.
    Ok(Rendered { width: fit.width(), height: fit.height(), rgba: pixmap.take_demultiplied() })
}
