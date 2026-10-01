//! The painted geometry of an SVG, ported from `quality.parse` and its helpers
//! (`backend/studi0trace/imaging/quality.py`, lines 111-581): every outline the scorecard
//! measures, sampled into polylines in source pixels, with what paints it. Path data is read
//! in [`path`] (`path_polylines` and the curves); the elements, transforms and paint here.
//!
//! The Python is the definition, quirks included, and `tests/drawing.rs` holds this module to
//! it on traced output, vector truths, hand-written SVGs that take every branch, and every
//! prefix of a path. What that means in places:
//!
//! - **What is drawn.** `path`, `rect`, `circle`, `ellipse`, `polygon`, `polyline` and `line`,
//!   under `svg` and `g`, and a `use` whose `href` names one of them directly. A `use` of a
//!   group or a symbol draws nothing; neither do the children of any other element (`a`,
//!   `switch`). A nested `svg` is a group with no transform and no opacity. There is no CSS:
//!   `<style>`, `class`, `display` and `visibility` are ignored. A fill is closed whatever the
//!   shape; a `line` is never filled.
//! - **Numbers** are `_NUM`'s: arc flags are not split from what follows (`a1 1 0 00.5.5` is
//!   five numbers), text that is not a number is skipped, and a command short of numbers draws
//!   nothing. Any Unicode decimal digit counts, as for Python's `\d` and `float()`.
//! - **What the Python raises**, on a moveto of fewer than two numbers followed by what numpy
//!   cannot broadcast, on `translate()` with no number, on `math.cos` of an infinite angle, on
//!   NaN or infinity in a sample count, is raised here too ([`DrawingError::Geometry`]).
//! - **Arithmetic** is float64 in numpy's order. Where numpy hands the work to Accelerate
//!   (`m @ t`, `pts @ m.T`, `det`) the fused multiply-adds its kernels use on arm64 are used
//!   as well (measured against numpy 2.5.3). `cos` and `sin` are libm's, called apart as numpy
//!   calls them: an optimised build would otherwise fuse them into a `sincos` that rounds `sin`
//!   differently. On the fixtures every point agrees to the bit, in either build.
//!
//! # XML: roxmltree, where the Python has ElementTree (expat)
//!
//! Both refuse what is not well-formed, expand internal entities, ignore the encoding a
//! declaration names (the input is already text) and skip comments and PIs. roxmltree's
//! `attribute("href")` also answers for `xlink:href`; ElementTree's `get` does not, so every
//! lookup here asks for an attribute in no namespace (`attr`). Where they still part: expat
//! applies `<!ATTLIST>` defaults from an internal DTD subset and roxmltree does not; and expat
//! reads an undeclared entity as nothing when the DOCTYPE names an external DTD, where
//! roxmltree refuses the document. And an element with `xmlns=""` is in roxmltree's empty
//! namespace, which matches no SVG element, so it (and what it holds) is left out, where
//! ElementTree reads it as no namespace at all and draws it (a `<rect xmlns="">` in an SVG
//! is 1 contour there and 0 here). None of these occurs in engine output or the bench's truths.
//!
//! # Where this module differs, on purpose
//!
//! - **Size.** More than [`MAX_POINTS`] points in one drawing is refused
//!   ([`DrawingError::TooLarge`]); the Python has no limit and tries to allocate them.
//! - **Depth.** The Python's walk recurses and its `RecursionError` comes after about 990
//!   nested groups, a few either way by element and by how deep its caller already is. This
//!   refuses an element more than [`MAX_DEPTH`] levels below the root anywhere in the
//!   document, before parsing, because roxmltree's parser recurses too (about 0.9 KB of stack
//!   a level) where expat does not: expat would read deep nesting inside `<defs>` that the walk
//!   never enters. At the limit, parsing wants about 0.9 MiB of stack in an optimised build
//!   and 16 MiB in an unoptimised one.
use regex::Regex;
use roxmltree::{Document, Node, ParsingOptions};
use std::borrow::Cow;
use std::collections::HashMap;
use std::f64::consts::PI;
use std::fmt;
use std::sync::OnceLock;

pub mod path;

use path::{cos_sin, first_num, nums, polylines, py_ceil, py_cos_sin, py_max, re, Budget};
pub use path::{arc, cubic, path_polylines, quad, segments_in, Subpath};

/// `quality.STEP`: the sampling step along every outline, px.
pub const STEP: f64 = 0.25;
/// `quality.COVER_ALPHA`: an element this opaque (opacity × fill- or stroke-opacity) covers.
pub const COVER_ALPHA: f64 = 0.5;
/// Points sampled and placed in one drawing, past which [`parse`] and [`path_polylines`]
/// refuse: 256 MiB of them.
pub const MAX_POINTS: usize = 1 << 24;
/// An element more levels than this below the root is refused, where the Python's recursion
/// gives out (987 nested groups around a shape are drawn, 988 are refused).
pub const MAX_DEPTH: usize = 988;

const SVG_NS: &str = "http://www.w3.org/2000/svg";
const XLINK_NS: &str = "http://www.w3.org/1999/xlink";

/// Why a drawing could not be read.
#[derive(Debug, Clone, PartialEq)]
pub enum DrawingError {
    /// The XML does not parse (the Python's `ET.ParseError`).
    Xml(String),
    /// What numpy or `math` raises on the geometry: a broadcast or stacking error, an index out
    /// of range, NaN or infinity where a count is needed (`ValueError`, `IndexError`,
    /// `OverflowError`), in the Python's words.
    Geometry(String),
    /// Nested deeper than [`MAX_DEPTH`] (the Python's `RecursionError`).
    TooDeep,
    /// More than [`MAX_POINTS`] points; the Python would try to allocate them.
    TooLarge,
}

impl fmt::Display for DrawingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            DrawingError::Xml(e) => write!(f, "the SVG does not parse: {e}"),
            DrawingError::Geometry(e) => write!(f, "the geometry cannot be sampled: {e}"),
            DrawingError::TooDeep => write!(f, "elements are nested more than {MAX_DEPTH} deep"),
            DrawingError::TooLarge => write!(f, "the drawing samples to more than {MAX_POINTS} points"),
        }
    }
}

impl std::error::Error for DrawingError {}

fn geometry(e: impl Into<String>) -> DrawingError {
    DrawingError::Geometry(e.into())
}

// ---------------------------------------------------------------- Python's text handling

/// Python's `\w` and `\s` in a str pattern: `str.isalnum()` or `_`, and `str.isspace()`,
/// which takes U+001C-001F as well as Unicode's White_Space.
const PY_W: &str = r"[\p{L}\p{N}_]";
const PY_S: &str = r"[\s\x1C-\x1F]";

/// `str(x)` for a float, as the Python formats numbers into path data and transforms and
/// reads them back: a finite value round-trips either way, and `inf` or `nan` hold no number.
fn py_repr(x: f64) -> String {
    if x.is_nan() {
        "nan".into()
    } else if x.is_infinite() {
        (if x > 0.0 { "inf" } else { "-inf" }).into()
    } else {
        format!("{x}")
    }
}

/// `str.strip()`
fn py_strip(s: &str) -> &str {
    s.trim_matches(|c: char| c.is_whitespace() || ('\x1c'..='\x1f').contains(&c))
}

/// Python's `min(a, b)`: `a` unless `b` is below it, so a NaN survives only as `a`.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

// ---------------------------------------------------------------- transforms

/// A 3×3 affine matrix, rows first, as the Python's `np.ndarray`.
pub type Matrix = [[f64; 3]; 3];

pub const IDENTITY: Matrix = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]];

static TRANSFORM: OnceLock<Regex> = OnceLock::new();

/// `a @ b` for 3×3 float64 as numpy computes it here: Accelerate accumulates each entry in
/// one fused chain (measured against numpy 2.5.3 on arm64). An infinity times a zero is NaN,
/// as there.
pub fn mat_mul(a: &Matrix, b: &Matrix) -> Matrix {
    let mut c = [[0.0; 3]; 3];
    for (i, row) in c.iter_mut().enumerate() {
        for (j, v) in row.iter_mut().enumerate() {
            *v = a[i][2].mul_add(b[2][j], a[i][1].mul_add(b[1][j], a[i][0] * b[0][j]));
        }
    }
    c
}

/// `_matrix`: an SVG `transform` list. `translate`, `scale`, `rotate` (about a centre when
/// given three numbers) and `matrix` (of exactly six numbers); anything else, `skewX` and
/// `skewY` included, is the identity. `translate()`, `scale()` and `rotate()` with no number
/// raise, and so does a rotation by infinity.
pub fn matrix(transform: Option<&str>) -> Result<Matrix, DrawingError> {
    let mut m = IDENTITY;
    let Some(transform) = transform.filter(|t| !t.is_empty()) else {
        return Ok(m);
    };
    let pattern = re(&TRANSFORM, || format!(r"({PY_W}+){PY_S}*\(([^)]*)\)"));
    for caps in pattern.captures_iter(transform) {
        let v = nums(&caps[2]);
        let first = || v.first().copied().ok_or_else(|| geometry("list index out of range"));
        let mut t = IDENTITY;
        match &caps[1] {
            "translate" => {
                t[0][2] = first()?;
                t[1][2] = v.get(1).copied().unwrap_or(0.0);
            }
            "scale" => {
                t[0][0] = first()?;
                t[1][1] = v.get(1).copied().unwrap_or(t[0][0]);
            }
            "rotate" => {
                let (cos, sin) = py_cos_sin(first()?.to_radians())?;
                t = [[cos, -sin, 0.0], [sin, cos, 0.0], [0.0, 0.0, 1.0]];
                if v.len() >= 3 {
                    let c = [[1.0, 0.0, v[1]], [0.0, 1.0, v[2]], [0.0, 0.0, 1.0]];
                    let ci = [[1.0, 0.0, -v[1]], [0.0, 1.0, -v[2]], [0.0, 0.0, 1.0]];
                    t = mat_mul(&mat_mul(&c, &t), &ci);
                }
            }
            "matrix" if v.len() == 6 => t = [[v[0], v[2], v[4]], [v[1], v[3], v[5]], [0.0, 0.0, 1.0]],
            _ => {}
        }
        m = mat_mul(&m, &t);
    }
    Ok(m)
}

/// `_apply`: `pts @ m[:2, :2].T + m[:2, 2]`. Accelerate multiplies the rows in pairs with a
/// fused multiply-add and an odd last row without one, and so does this.
pub fn apply(m: &Matrix, pts: &[[f64; 2]]) -> Vec<[f64; 2]> {
    let fused_to = pts.len() - pts.len() % 2;
    pts.iter()
        .enumerate()
        .map(|(i, &[x, y])| {
            [0, 1].map(|j| {
                let lin = if i < fused_to { y.mul_add(m[j][1], x * m[j][0]) } else { x * m[j][0] + y * m[j][1] };
                lin + m[j][2]
            })
        })
        .collect()
}

/// `np.linalg.det(m[:2, :2])`: LAPACK's LU with partial pivoting (the column scaled by the
/// pivot's reciprocal, the update fused), then numpy's `sign * exp(sum(log|u_ii|))`, and 0 for
/// a matrix the factorisation finds singular.
fn det2(m: &Matrix) -> f64 {
    let (mut a, mut b, mut c, mut d) = (m[0][0], m[0][1], m[1][0], m[1][1]);
    let mut sign = 1.0;
    if c.abs() > a.abs() {
        (a, b, c, d) = (c, d, a, b);
        sign = -1.0;
    }
    if a == 0.0 {
        return 0.0;
    }
    let l = if a.abs() >= f64::MIN_POSITIVE { c * (1.0 / a) } else { c / a };
    let u = (-l).mul_add(b, d);
    if u == 0.0 {
        return 0.0;
    }
    let mut logdet = 0.0;
    for x in [a, u] {
        if x < 0.0 {
            sign = -sign;
        }
        logdet += x.abs().ln();
    }
    sign * logdet.exp()
}

// ---------------------------------------------------------------- shapes

fn rect_in(x: f64, y: f64, w: f64, h: f64, rx: f64, ry: f64, budget: &mut Budget) -> Result<Subpath, DrawingError> {
    let (rx, ry) = (py_min(rx, w / 2.0), py_min(ry, h / 2.0));
    if rx <= 0.0 || ry <= 0.0 {
        budget.take(5)?;
        let pts = vec![[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]];
        return Ok(Subpath { pts, closed: true, columns: 2 });
    }
    // the Python writes the outline as path data and reads it back, NaN and all
    let [x0, x1, x2, x3] = [x, x + rx, x + w - rx, x + w].map(py_repr);
    let [y0, y1, y2, y3] = [y, y + ry, y + h - ry, y + h].map(py_repr);
    let arc = format!("A{} {} 0 0 1", py_repr(rx), py_repr(ry));
    let d = format!("M{x1} {y0}L{x2} {y0}{arc} {x3} {y1}L{x3} {y2}{arc} {x2} {y3}L{x1} {y3}{arc} {x0} {y2}L{x0} {y1}{arc} {x1} {y0}Z");
    let first = polylines(&d, budget)?.into_iter().next();
    first.map(|s| Subpath { closed: true, ..s }).ok_or_else(|| geometry("list index out of range"))
}

/// `_rect`: a rect's outline, closed, its corners rounded by `rx` and `ry` (each at most half
/// the side) as four arcs.
pub fn rect(x: f64, y: f64, w: f64, h: f64, rx: f64, ry: f64) -> Result<Vec<[f64; 2]>, DrawingError> {
    // a rounded rect's path has lines and arcs, so its points are never short ones
    rect_in(x, y, w, h, rx, ry, &mut Budget(MAX_POINTS)).map(|s| s.pts)
}

fn ellipse_in(cx: f64, cy: f64, rx: f64, ry: f64, budget: &mut Budget) -> Result<Subpath, DrawingError> {
    let n = py_ceil(2.0 * PI * py_max(rx, ry) / STEP)?.max(16.0).min(MAX_POINTS as f64) as usize;
    budget.take(n + 1)?;
    // `np.linspace(0, 2π, n + 1)`
    let step = 2.0 * PI / n as f64;
    let pts = (0..=n)
        .map(|i| {
            let (cos, sin) = cos_sin(if i == n { 2.0 * PI } else { i as f64 * step });
            [cx + rx * cos, cy + ry * sin]
        })
        .collect();
    Ok(Subpath { pts, closed: true, columns: 2 })
}

/// `_ellipse`: at least 16 steps, one per [`STEP`] of the larger radius, and the first point
/// again at the end.
pub fn ellipse(cx: f64, cy: f64, rx: f64, ry: f64) -> Result<Vec<[f64; 2]>, DrawingError> {
    ellipse_in(cx, cy, rx, ry, &mut Budget(MAX_POINTS)).map(|s| s.pts)
}

// ---------------------------------------------------------------- the drawing

/// `quality.Contour`: one outline of one painted element.
#[derive(Debug, Clone, PartialEq)]
pub struct Contour {
    /// Index of the painted element, in paint order.
    pub element: usize,
    /// Points in source px. A `polygon` or `polyline` may leave fewer than two.
    pub pts: Vec<[f64; 2]>,
    pub closed: bool,
    /// The stroke width in source px for a stroked centreline; `None` for a fill.
    pub stroke: Option<f64>,
    /// The fill or stroke as written (`#f00`, `url(#g)`, ...).
    pub paint: String,
    /// `evenodd` or `nonzero`.
    pub fill_rule: String,
}

/// `quality.Drawing`
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Drawing {
    pub contours: Vec<Contour>,
    /// Shape elements met, in paint order, painted or not.
    pub elements: usize,
    /// Segments the elements are drawn with ([`segments_in`] for a path).
    pub segments: usize,
    /// Elements that are stroked.
    pub strokes: usize,
    /// Per element: whether it is opaque enough to hide what it is painted over.
    pub covers: Vec<bool>,
}

/// `el.get(name)`: the attribute of that name in no namespace. (roxmltree's `attribute` with a
/// bare name takes the first of that local name in any namespace: `xlink:href` for `href`.)
fn attr<'a>(el: Node<'a, '_>, name: &str) -> Option<&'a str> {
    el.attributes().find(|a| a.namespace().is_none() && a.name() == name).map(|a| a.value())
}

/// `_f`: the first number in an attribute, or 0.
fn number_attr(el: Node, name: &str) -> f64 {
    attr(el, name).and_then(first_num).unwrap_or(0.0)
}

/// `_PAINT`, and the paint dict the Python passes down: one value per name.
const PAINT: [&str; 6] = ["fill", "stroke", "stroke-width", "fill-rule", "fill-opacity", "stroke-opacity"];
const FILL: usize = 0;
const STROKE: usize = 1;
const STROKE_WIDTH: usize = 2;
const FILL_RULE: usize = 3;
const FILL_OPACITY: usize = 4;
const STROKE_OPACITY: usize = 5;

static STYLE: [OnceLock<Regex>; 6] = [const { OnceLock::new() }; 6];
static OPACITY: OnceLock<Regex> = OnceLock::new();

#[derive(Clone, Default)]
struct Paint([Option<String>; 6]);

impl Paint {
    fn get(&self, k: usize) -> Option<&str> {
        self.0[k].as_deref()
    }
}

/// `_paint`: the inherited properties, overridden by the element's attributes and those by
/// its `style`.
fn paint(el: Node, inherited: &Paint) -> Paint {
    let mut out = inherited.clone();
    let style = attr(el, "style").unwrap_or("");
    for (i, key) in PAINT.iter().enumerate() {
        if let Some(v) = attr(el, key) {
            out.0[i] = Some(v.to_string());
        }
        let pattern = re(&STYLE[i], || format!("(?:^|;){PY_S}*{}{PY_S}*:{PY_S}*([^;]+)", regex::escape(key)));
        if let Some(m) = pattern.captures(style) {
            out.0[i] = Some(py_strip(&m[1]).to_string());
        }
    }
    out
}

/// `_unit`: an opacity, `50%` as 0.5, and 1 when there is no number.
fn unit(v: Option<&str>) -> f64 {
    match v.and_then(|s| first_num(s).map(|x| (s, x))) {
        Some((s, x)) if py_strip(s).ends_with('%') => x / 100.0,
        Some((_, x)) => x,
        None => 1.0,
    }
}

/// `_opacity`: `opacity` from the style, else the attribute.
fn opacity(el: Node) -> f64 {
    let pattern = re(&OPACITY, || format!("(?:^|;){PY_S}*opacity{PY_S}*:{PY_S}*([^;]+)"));
    match pattern.captures(attr(el, "style").unwrap_or("")) {
        Some(m) => unit(Some(&m[1])),
        None => unit(attr(el, "opacity")),
    }
}

/// `el.tag.replace(_SVG, "")`: the local name in the SVG namespace or in none, ElementTree's
/// `{uri}name` otherwise (which then matches nothing).
fn tag<'a>(el: Node<'a, '_>) -> Cow<'a, str> {
    let name = el.tag_name();
    match name.namespace() {
        None => Cow::Borrowed(name.name()),
        Some(ns) if ns == SVG_NS => Cow::Borrowed(name.name()),
        Some(ns) => Cow::Owned(format!("{{{ns}}}{}", name.name()).replace(&format!("{{{SVG_NS}}}"), "")),
    }
}

/// What `walk` does not enter.
const SKIPPED: [&str; 12] = [
    "defs", "linearGradient", "radialGradient", "filter", "mask", "clipPath",
    "symbol", "pattern", "style", "title", "desc", "metadata",
];

/// `parse`'s closures, `shape` and `walk`, with what they share.
struct Walker<'a, 'input> {
    defs: HashMap<&'a str, Node<'a, 'input>>,
    drawing: Drawing,
    budget: Budget,
}

impl<'a, 'input> Walker<'a, 'input> {
    /// `_apply`, for a subpath: numpy refuses to multiply one of the wrong width.
    fn place(&mut self, m: &Matrix, p: &Subpath) -> Result<Vec<[f64; 2]>, DrawingError> {
        if p.columns != 2 {
            return Err(geometry(format!(
                "matmul: Input operand 1 has a mismatch in its core dimension 0 (size 2 is different from {})",
                p.columns
            )));
        }
        self.budget.take(p.pts.len())?;
        Ok(apply(m, &p.pts))
    }

    /// The outlines of a shape element, counting its segments; `None` for any other element.
    fn outlines(&mut self, el: Node, tag: &str) -> Result<Option<Vec<Subpath>>, DrawingError> {
        let budget = &mut self.budget;
        let f = |name| number_attr(el, name);
        let (polys, segments) = match tag {
            "path" => {
                let d = attr(el, "d").unwrap_or("");
                (polylines(d, budget)?, segments_in(d))
            }
            "rect" => {
                let (rx, ry) = (attr(el, "rx"), attr(el, "ry"));
                let rxv = if rx.is_some() { f("rx") } else if ry.is_some() { f("ry") } else { 0.0 };
                let ryv = if ry.is_some() { f("ry") } else { rxv };
                let outline = rect_in(f("x"), f("y"), f("width"), f("height"), rxv, ryv, budget)?;
                (vec![outline], if rxv > 0.0 { 8 } else { 4 })
            }
            "circle" => {
                let r = f("r");
                (vec![ellipse_in(f("cx"), f("cy"), r, r, budget)?], 4)
            }
            "ellipse" => (vec![ellipse_in(f("cx"), f("cy"), f("rx"), f("ry"), budget)?], 4),
            "polygon" | "polyline" => {
                let v = nums(attr(el, "points").unwrap_or(""));
                let mut pts = v.as_chunks::<2>().0.to_vec();
                if tag == "polygon" && !pts.is_empty() {
                    pts.push(pts[0]);
                }
                budget.take(pts.len())?;
                let segments = pts.len().saturating_sub(1);
                (vec![Subpath { pts, closed: tag == "polygon", columns: 2 }], segments)
            }
            "line" => {
                budget.take(2)?;
                (vec![Subpath { pts: vec![[f("x1"), f("y1")], [f("x2"), f("y2")]], closed: false, columns: 2 }], 1)
            }
            _ => return Ok(None),
        };
        self.drawing.segments += segments;
        Ok(Some(polys))
    }

    /// `shape`: one element's contours, filled and stroked. Kept out of `walk`, whose frame is
    /// on the stack once for every level of nesting.
    #[inline(never)]
    fn shape(&mut self, el: Node, m: &Matrix, inherited: &Paint, alpha: f64) -> Result<(), DrawingError> {
        let tag = tag(el);
        let m = mat_mul(m, &matrix(attr(el, "transform"))?);
        let Some(polys) = self.outlines(el, &tag)? else {
            return Ok(());
        };
        let paint = paint(el, inherited);
        let alpha = alpha * opacity(el);
        let fill = paint.get(FILL).unwrap_or("#000");
        let stroke = paint.get(STROKE).filter(|s| !s.is_empty() && *s != "none");
        let rule = if py_strip(paint.get(FILL_RULE).unwrap_or("")) == "evenodd" { "evenodd" } else { "nonzero" };
        let index = self.drawing.elements;
        self.drawing.elements += 1;
        let lin = det2(&m).abs().sqrt();
        let filled = fill != "none" && tag != "line";
        let opaque = (filled && alpha * unit(paint.get(FILL_OPACITY)) >= COVER_ALPHA)
            || (stroke.is_some() && alpha * unit(paint.get(STROKE_OPACITY)) >= COVER_ALPHA);
        self.drawing.covers.push(opaque);
        if filled {
            for p in &polys {
                let pts = self.place(&m, p)?;
                let (paint, fill_rule) = (fill.into(), rule.into());
                self.drawing.contours.push(Contour { element: index, pts, closed: true, stroke: None, paint, fill_rule });
            }
        }
        if let Some(stroke) = stroke {
            self.drawing.strokes += 1;
            let w = paint.get(STROKE_WIDTH).and_then(first_num).unwrap_or(1.0) * lin;
            for p in &polys {
                let pts = self.place(&m, p)?;
                let (paint, fill_rule) = (stroke.into(), "nonzero".into());
                self.drawing.contours.push(Contour { element: index, pts, closed: p.closed, stroke: Some(w), paint, fill_rule });
            }
        }
        Ok(())
    }

    /// `walk`: an element and, for `svg` and `g`, its children, in document order.
    fn walk(&mut self, el: Node<'a, 'input>, m: &Matrix, inherited: &Paint, alpha: f64, depth: usize)
        -> Result<(), DrawingError> {
        if depth > MAX_DEPTH {
            return Err(DrawingError::TooDeep);
        }
        let tag = tag(el);
        if SKIPPED.contains(&&*tag) {
            return Ok(());
        }
        if tag == "use" {
            let named = |href: Option<&'a str>| href.filter(|h| !h.is_empty());
            let href = named(attr(el, "href")).or(named(el.attribute((XLINK_NS, "href")))).unwrap_or("");
            let Some(&target) = self.defs.get(href.trim_start_matches('#')) else {
                return Ok(());
            };
            let at = format!("translate({} {})", py_repr(number_attr(el, "x")), py_repr(number_attr(el, "y")));
            let mm = mat_mul(&mat_mul(m, &matrix(attr(el, "transform"))?), &matrix(Some(&at))?);
            return self.shape(target, &mm, &paint(el, inherited), alpha * opacity(el));
        }
        if tag == "svg" || tag == "g" {
            let (mm, a) = match &*tag {
                "svg" => (*m, alpha),
                _ => (mat_mul(m, &matrix(attr(el, "transform"))?), alpha * opacity(el)),
            };
            let inner = paint(el, inherited);
            for child in el.children().filter(Node::is_element) {
                self.walk(child, &mm, &inner, a, depth + 1)?;
            }
            return Ok(());
        }
        self.shape(el, m, inherited, alpha)
    }
}

// ---------------------------------------------------------------- the XML

/// The index just past the first `end` at or after `from`, or the end of `b`.
fn after(b: &[u8], from: usize, end: &[u8]) -> usize {
    let from = from.min(b.len());
    b[from..].windows(end.len()).position(|w| w == end).map_or(b.len(), |i| from + i + end.len())
}

/// The end of the start tag whose name begins at `i`, quotes honoured, and whether it is empty.
fn tag_end(b: &[u8], mut i: usize) -> (usize, bool) {
    let mut quote = None;
    while i < b.len() {
        match (quote, b[i]) {
            (None, b'"' | b'\'') => quote = Some(b[i]),
            (Some(q), c) if c == q => quote = None,
            (None, b'>') => return (i + 1, b[i - 1] == b'/'),
            _ => {}
        }
        i += 1;
    }
    (b.len(), false)
}

/// The end of the `<!` declaration whose keyword begins at `i` (a DOCTYPE and its internal
/// subset), and how many `<` its quoted literals hold: markup an entity may expand.
fn declaration(b: &[u8], mut i: usize) -> (usize, usize) {
    let (mut quote, mut brackets, mut markup) = (None, 0usize, 0usize);
    while i < b.len() {
        let c = b[i];
        match quote {
            Some(q) if c == q => quote = None,
            Some(_) => markup += (c == b'<') as usize,
            None if c == b'"' || c == b'\'' => quote = Some(c),
            None if b[i..].starts_with(b"<!--") => {
                i = after(b, i + 4, b"-->");
                continue;
            }
            None if b[i..].starts_with(b"<?") => {
                i = after(b, i + 2, b"?>");
                continue;
            }
            None if c == b'[' => brackets += 1,
            None if c == b']' => brackets = brackets.saturating_sub(1),
            None if c == b'>' && brackets == 0 => return (i + 1, markup),
            None => {}
        }
        i += 1;
    }
    (b.len(), markup)
}

/// How deep the elements of `svg` nest (the root at 1), read from its text before roxmltree,
/// which recurses a level at a time, is let at it. Markup inside a DOCTYPE's literals counts
/// as though every entity opened all of it at the deepest point, so this is never less than
/// the parse will meet.
fn nesting(svg: &str) -> usize {
    let b = svg.as_bytes();
    let (mut i, mut depth, mut deepest, mut entities) = (0, 0usize, 0usize, 0usize);
    while let Some(at) = b[i..].iter().position(|&c| c == b'<') {
        i += at;
        let rest = &b[i..];
        i = if rest.starts_with(b"<!--") {
            after(b, i + 4, b"-->")
        } else if rest.starts_with(b"<![CDATA[") {
            after(b, i + 9, b"]]>")
        } else if rest.starts_with(b"<?") {
            after(b, i + 2, b"?>")
        } else if rest.starts_with(b"<!") {
            let (end, markup) = declaration(b, i + 2);
            entities += markup;
            end
        } else if rest.starts_with(b"</") {
            depth = depth.saturating_sub(1);
            after(b, i + 2, b">")
        } else {
            depth += 1;
            deepest = deepest.max(depth);
            let (end, empty) = tag_end(b, i + 1);
            depth -= empty as usize;
            end
        };
    }
    deepest + entities
}

/// Refuses an SVG whose elements nest more than [`MAX_DEPTH`] deep ([`DrawingError::TooDeep`]),
/// from its text alone: a linear scan that builds nothing. [`parse`] begins with it. Call it
/// before handing the SVG to anything else that parses it, because roxmltree recurses a level
/// at a time (about 0.9 KB of stack a level) in [`parse`] and in resvg, so in
/// [`crate::render::render`]: 100 000 nested groups overflow the stack of any thread. What is
/// not XML at all is not this function's to refuse; it answers `Ok`, and the parser says so.
pub fn check_nesting(svg: &str) -> Result<(), DrawingError> {
    if nesting(svg) > MAX_DEPTH + 1 {
        return Err(DrawingError::TooDeep);
    }
    Ok(())
}

/// `parse`: the painted geometry of `svg` in source pixels, root user units mapped through
/// the viewBox onto a `size` (w, h) raster when one is given.
pub fn parse(svg: &str, size: Option<(u32, u32)>) -> Result<Drawing, DrawingError> {
    check_nesting(svg)?;
    let options = ParsingOptions { allow_dtd: true, ..ParsingOptions::default() };
    let doc = Document::parse_with_options(svg, options).map_err(|e| DrawingError::Xml(e.to_string()))?;
    let root = doc.root_element();
    let mut defs = HashMap::new();
    for el in root.descendants().filter(Node::is_element) {
        if let Some(id) = attr(el, "id") {
            defs.insert(id, el);
        }
    }
    let mut base = IDENTITY;
    let vb = nums(attr(root, "viewBox").unwrap_or(""));
    if let Some((w, h)) = size.filter(|_| vb.len() == 4 && vb[2] > 0.0 && vb[3] > 0.0) {
        let [sx, sy, tx, ty] = [w as f64 / vb[2], h as f64 / vb[3], -vb[0], -vb[1]].map(py_repr);
        base = matrix(Some(&format!("scale({sx} {sy}) translate({tx} {ty})")))?;
    }
    let mut walker = Walker { defs, drawing: Drawing::default(), budget: Budget(MAX_POINTS) };
    walker.walk(root, &base, &Paint::default(), 1.0, 0)?;
    Ok(walker.drawing)
}
