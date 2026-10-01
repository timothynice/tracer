//! Path data to polylines: `path_polylines`, `_cubic`, `_quad`, `_arc` and `_segments_in`
//! (`quality.py` lines 111-114 and 248-396), with the Python number and text handling the
//! rest of [`crate::drawing`] shares.
//!
//! `path_polylines` keeps its current point in a numpy array, and a moveto of fewer than two
//! numbers leaves one of length 0 or 1 there, which numpy then broadcasts (a length-1 point
//! adds to both coordinates) or refuses. `Pt` keeps that length, so the Rust draws and
//! raises exactly where the Python does; `tests/drawing.rs` checks every prefix of a path.
use super::{geometry, DrawingError, MAX_POINTS, STEP};
use regex::Regex;
use std::borrow::Cow;
use std::f64::consts::PI;
use std::sync::OnceLock;

// ---------------------------------------------------------------- Python's numbers and text

pub(super) fn re(cell: &'static OnceLock<Regex>, pat: impl FnOnce() -> String) -> &'static Regex {
    cell.get_or_init(|| Regex::new(&pat()).unwrap())
}

static NUM: OnceLock<Regex> = OnceLock::new();
static CMD: OnceLock<Regex> = OnceLock::new();
static DIGIT: OnceLock<Regex> = OnceLock::new();

/// `_NUM`
fn num_re() -> &'static Regex {
    re(&NUM, || r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?".into())
}

/// `_CMD`
fn cmd_re() -> &'static Regex {
    re(&CMD, || r"([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)".into())
}

/// `float()` of a `_NUM` match. Python's `\d` and `float()` both take any Unicode decimal
/// digit; Rust's parser takes ASCII, so the others are translated first.
fn py_float(s: &str) -> f64 {
    let ascii: Cow<str> = if s.is_ascii() {
        Cow::Borrowed(s)
    } else {
        Cow::Owned(s.chars().map(|c| if c.is_ascii() { c } else { char::from(b'0' + digit_value(c)) }).collect())
    };
    // every `_NUM` match is a float literal; NaN is unreachable
    ascii.parse().unwrap_or(f64::NAN)
}

/// The value of a non-ASCII decimal digit. Unicode encodes them in runs of ten from zero, so
/// it is the count of digits before this one in its unbroken stretch of them, mod ten.
fn digit_value(c: char) -> u8 {
    let digit = re(&DIGIT, || r"\A\d\z".into());
    let mut cp = c as u32;
    let mut before = 0u32;
    while let Some(prev) = cp.checked_sub(1).and_then(char::from_u32) {
        if !digit.is_match(prev.encode_utf8(&mut [0; 4])) {
            break;
        }
        before += 1;
        cp -= 1;
    }
    (before % 10) as u8
}

/// `[float(x) for x in _NUM.findall(s)]`
pub(super) fn nums(s: &str) -> Vec<f64> {
    num_re().find_iter(s).map(|m| py_float(m.as_str())).collect()
}

/// `float(_NUM.search(s).group(0))`, if it finds one.
pub(super) fn first_num(s: &str) -> Option<f64> {
    num_re().find(s).map(|m| py_float(m.as_str()))
}

/// Python's `max(a, b)`: `a` unless `b` beats it, so a NaN survives only as `a`.
pub(super) fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// `math.ceil`, which raises on NaN (`ValueError`) and infinity (`OverflowError`).
pub(super) fn py_ceil(x: f64) -> Result<f64, DrawingError> {
    if x.is_nan() {
        Err(geometry("cannot convert float NaN to integer"))
    } else if x.is_infinite() {
        Err(geometry("cannot convert float infinity to integer"))
    } else {
        Ok(x.ceil())
    }
}

/// `cos(a)` and `sin(a)` as numpy and CPython get them: two separate calls to libm's `cos` and
/// `sin`. Asked for both of one argument, LLVM fuses the calls into one `sincos`
/// (`__sincos_stret` on Apple), whose `sin` rounds differently from libm's for about one
/// argument in 500, and only in an optimised build. Hiding the argument from the optimiser for
/// each call keeps them apart. Never `f64::sin_cos`, which is that fused call.
pub(crate) fn cos_sin(a: f64) -> (f64, f64) {
    (std::hint::black_box(a).cos(), std::hint::black_box(a).sin())
}

/// `(math.cos(a), math.sin(a))`, which raise on an infinite angle.
pub(super) fn py_cos_sin(a: f64) -> Result<(f64, f64), DrawingError> {
    if a.is_infinite() {
        return Err(geometry("math domain error"));
    }
    Ok(cos_sin(a))
}

// ---------------------------------------------------------------- numpy's points

/// A 1-D numpy array of length 0, 1 or 2: a point as `path_polylines` holds it. A length-1
/// point keeps its value in both slots, so broadcasting it is reading `v`.
#[derive(Clone, Copy, Debug)]
struct Pt {
    len: usize,
    v: [f64; 2],
}

/// numpy's broadcast of two 1-D shapes.
fn broadcast(a: usize, b: usize) -> Result<usize, DrawingError> {
    match (a, b) {
        _ if a == b || b == 1 => Ok(a),
        (1, _) => Ok(b),
        _ => Err(geometry(format!("operands could not be broadcast together with shapes ({a},) ({b},)"))),
    }
}

impl Pt {
    const ZERO: Pt = Pt { len: 2, v: [0.0, 0.0] };

    fn xy(x: f64, y: f64) -> Pt {
        Pt { len: 2, v: [x, y] }
    }

    /// `np.array(v[i:i + 2])`, which may be short.
    fn of(v: &[f64]) -> Pt {
        match *v {
            [] => Pt { len: 0, v: [f64::NAN; 2] },
            [x] => Pt { len: 1, v: [x, x] },
            [x, y, ..] => Pt::xy(x, y),
        }
    }

    fn zip(self, o: Pt, f: impl Fn(f64, f64) -> f64) -> Result<Pt, DrawingError> {
        Ok(Pt { len: broadcast(self.len, o.len)?, v: [f(self.v[0], o.v[0]), f(self.v[1], o.v[1])] })
    }

    fn add(self, o: Pt) -> Result<Pt, DrawingError> {
        self.zip(o, |a, b| a + b)
    }

    fn sub(self, o: Pt) -> Result<Pt, DrawingError> {
        self.zip(o, |a, b| a - b)
    }

    fn times(self, k: f64) -> Pt {
        Pt { len: self.len, v: [k * self.v[0], k * self.v[1]] }
    }

    /// `p[i]`
    fn at(self, i: usize) -> Result<f64, DrawingError> {
        if i < self.len {
            Ok(self.v[i])
        } else {
            Err(geometry(format!("index {i} is out of bounds for axis 0 with size {}", self.len)))
        }
    }

    /// `np.linalg.norm(p)`: `sqrt(p @ p)`, which Accelerate sums unfused.
    fn norm(self) -> f64 {
        match self.len {
            0 => 0.0,
            1 => (self.v[0] * self.v[0]).sqrt(),
            _ => (self.v[0] * self.v[0] + self.v[1] * self.v[1]).sqrt(),
        }
    }
}

/// `np.allclose(a, b)`: `|a - b| <= 1e-8 + 1e-5·|b|` with `b` finite, or `a == b`, everywhere.
fn allclose(a: Pt, b: Pt) -> Result<bool, DrawingError> {
    let len = broadcast(a.len, b.len)?;
    Ok((0..len).all(|i| {
        let (x, y) = (a.v[i], b.v[i]);
        ((x - y).abs() <= 1e-8 + 1e-5 * y.abs() && y.is_finite()) || x == y
    }))
}

/// The points left to sample before a drawing is refused as too large.
pub(super) struct Budget(pub(super) usize);

impl Budget {
    pub(super) fn take(&mut self, n: usize) -> Result<(), DrawingError> {
        self.0 = self.0.checked_sub(n).ok_or(DrawingError::TooLarge)?;
        Ok(())
    }
}

// ---------------------------------------------------------------- subpaths

/// One subpath: its points, whether it was closed, and how many columns numpy holds it in.
/// That is 2, except for a subpath of short points (`M1e999 m-1e999 z`), whose rows then hold
/// their one value twice; placing such a subpath on the canvas raises.
#[derive(Debug, Clone, PartialEq)]
pub struct Subpath {
    pub pts: Vec<[f64; 2]>,
    pub closed: bool,
    pub columns: usize,
}

/// The rows of the subpath being drawn: `pts` in `path_polylines`, stacked by `np.vstack`
/// when flushed, which refuses rows of different widths.
#[derive(Default)]
struct Rows {
    pts: Vec<[f64; 2]>,
    columns: Option<usize>,
}

impl Rows {
    fn width(&mut self, w: usize) -> Result<(), DrawingError> {
        match self.columns {
            Some(c) if c != w => Err(geometry(format!(
                "all the input array dimensions except for the concatenation axis must match exactly, \
                 but along dimension 1, the array at index 0 has size {c} and the array at index 1 has size {w}"
            ))),
            _ => {
                self.columns = Some(w);
                Ok(())
            }
        }
    }

    /// `pts.append(p[None, :])`
    fn push(&mut self, p: Pt, budget: &mut Budget) -> Result<(), DrawingError> {
        self.width(p.len)?;
        budget.take(1)?;
        self.pts.push(p.v);
        Ok(())
    }

    /// `pts.append(block)` of an (n, 2) block of samples.
    fn extend(&mut self, block: Vec<[f64; 2]>) -> Result<(), DrawingError> {
        self.width(2)?;
        self.pts.extend(block);
        Ok(())
    }

    /// `pts[-1][-1]`: the last row, as a point.
    fn last(&self) -> Option<Pt> {
        self.pts.last().map(|&v| Pt { len: self.columns.unwrap_or(2), v })
    }

    /// `flush`: a subpath of two rows or more is kept.
    fn flush(&mut self, closed: bool, out: &mut Vec<Subpath>) {
        let rows = std::mem::take(self);
        if rows.pts.len() >= 2 {
            out.push(Subpath { pts: rows.pts, closed, columns: rows.columns.unwrap_or(2) });
        }
    }
}

// ---------------------------------------------------------------- curves

/// `np.linspace(0, 1, n + 1)[1:]` as numpy makes it: `i * (1 / n)`, and exactly 1 at the end.
fn unit_steps(n: usize) -> impl Iterator<Item = f64> {
    let step = 1.0 / n as f64;
    (1..=n).map(move |i| if i == n { 1.0 } else { i as f64 * step })
}

fn cubic_pt(p0: Pt, c1: Pt, c2: Pt, p1: Pt, budget: &mut Budget) -> Result<Vec<[f64; 2]>, DrawingError> {
    let length = c1.sub(p0)?.norm() + c2.sub(c1)?.norm() + p1.sub(c2)?.norm();
    let n = py_ceil(length / STEP)?.clamp(4.0, 400.0) as usize;
    budget.take(n)?;
    Ok(unit_steps(n)
        .map(|t| {
            let a = 1.0 - t;
            // numpy's `** 3` is libm's pow, and `** 2` a square
            let (a3, t3) = (a.powf(3.0), t.powf(3.0));
            [0, 1].map(|j| a3 * p0.v[j] + 3.0 * (a * a) * t * c1.v[j] + 3.0 * a * (t * t) * c2.v[j] + t3 * p1.v[j])
        })
        .collect())
}

fn quad_pt(p0: Pt, c: Pt, p1: Pt, budget: &mut Budget) -> Result<Vec<[f64; 2]>, DrawingError> {
    let c1 = p0.add(c.sub(p0)?.times(2.0 / 3.0))?;
    let c2 = p1.add(c.sub(p1)?.times(2.0 / 3.0))?;
    cubic_pt(p0, c1, c2, p1, budget)
}

#[allow(clippy::too_many_arguments)]
fn arc_pt(p0: Pt, rx: f64, ry: f64, phi_deg: f64, large: bool, sweep: bool, p1: Pt, budget: &mut Budget)
    -> Result<Vec<[f64; 2]>, DrawingError> {
    if rx == 0.0 || ry == 0.0 || allclose(p0, p1)? {
        budget.take(1)?;
        return Ok(vec![p1.v]);
    }
    let (mut rx, mut ry) = (rx.abs(), ry.abs());
    let (cp, sp) = py_cos_sin(phi_deg.to_radians())?;
    let (dx, dy) = ((p0.v[0] - p1.v[0]) / 2.0, (p0.v[1] - p1.v[1]) / 2.0);
    let x1 = cp * dx + sp * dy;
    let y1 = -sp * dx + cp * dy;
    let lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry);
    if lam > 1.0 {
        let s = lam.sqrt();
        (rx, ry) = (rx * s, ry * s);
    }
    let num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1;
    let den = rx * rx * y1 * y1 + ry * ry * x1 * x1;
    let mut co = if den > 0.0 { py_max(0.0, num / den).sqrt() } else { 0.0 };
    if large == sweep {
        co = -co;
    }
    let cx1 = co * rx * y1 / ry;
    let cy1 = -co * ry * x1 / rx;
    let cx = cp * cx1 - sp * cy1 + (p0.at(0)? + p1.v[0]) / 2.0;
    let cy = sp * cx1 + cp * cy1 + (p0.at(1)? + p1.v[1]) / 2.0;
    // `ang`, the Python's integers 1 and 0 kept as factors: 0·∞ is NaN and 0·-x is -0
    let ang = |ux: f64, uy: f64, vx: f64, vy: f64| (ux * vy - uy * vx).atan2(ux * vx + uy * vy);
    let t1 = ang(1.0, 0.0, (x1 - cx1) / rx, (y1 - cy1) / ry);
    let mut dt = ang((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry);
    if !sweep && dt > 0.0 {
        dt -= 2.0 * PI;
    } else if sweep && dt < 0.0 {
        dt += 2.0 * PI;
    }
    let n = py_ceil(dt.abs() * py_max(rx, ry) / STEP)?.clamp(4.0, 2000.0) as usize;
    budget.take(n)?;
    let mut pts: Vec<[f64; 2]> = unit_steps(n)
        .map(|s| {
            let t = t1 + dt * s;
            let (cos, sin) = cos_sin(t);
            [cx + rx * cos * cp - ry * sin * sp, cy + rx * cos * sp + ry * sin * cp]
        })
        .collect();
    if let Some(last) = pts.last_mut() {
        *last = p1.v;
    }
    Ok(pts)
}

/// `_cubic`: a cubic Bézier sampled at one step per [`STEP`] of its control polygon (4 to
/// 400 steps), excluding `p0`.
pub fn cubic(p0: [f64; 2], c1: [f64; 2], c2: [f64; 2], p1: [f64; 2]) -> Result<Vec<[f64; 2]>, DrawingError> {
    let [p0, c1, c2, p1] = [p0, c1, c2, p1].map(|[x, y]| Pt::xy(x, y));
    cubic_pt(p0, c1, c2, p1, &mut Budget(MAX_POINTS))
}

/// `_quad`: a quadratic Bézier, as the cubic with the same curve.
pub fn quad(p0: [f64; 2], c: [f64; 2], p1: [f64; 2]) -> Result<Vec<[f64; 2]>, DrawingError> {
    let [p0, c, p1] = [p0, c, p1].map(|[x, y]| Pt::xy(x, y));
    quad_pt(p0, c, p1, &mut Budget(MAX_POINTS))
}

/// `_arc`: an SVG endpoint arc per the SVG implementation notes (radii scaled up to reach),
/// sampled at one step per [`STEP`] of its larger radius (4 to 2000 steps), excluding `p0`
/// and ending on `p1` exactly; just `p1` for a zero radius or a zero-length arc.
pub fn arc(p0: [f64; 2], rx: f64, ry: f64, phi_deg: f64, large: bool, sweep: bool, p1: [f64; 2])
    -> Result<Vec<[f64; 2]>, DrawingError> {
    let [p0, p1] = [p0, p1].map(|[x, y]| Pt::xy(x, y));
    arc_pt(p0, rx, ry, phi_deg, large, sweep, p1, &mut Budget(MAX_POINTS))
}

// ---------------------------------------------------------------- path data

/// `path_polylines`: every subpath of `d` with two points or more, curves sampled.
///
/// The Python's reading of the grammar, which is not quite the SVG spec's: numbers left over
/// after a command's last whole set are dropped; a moveto's extra pairs are lines; `Z` ends
/// the subpath (adding the start unless the last point is already there) and what follows
/// without a moveto starts a new one from the start point, which it does not include.
pub fn path_polylines(d: &str) -> Result<Vec<Subpath>, DrawingError> {
    polylines(d, &mut Budget(MAX_POINTS))
}

pub(super) fn polylines(d: &str, budget: &mut Budget) -> Result<Vec<Subpath>, DrawingError> {
    let mut out = Vec::new();
    let (mut cur, mut start) = (Pt::ZERO, Pt::ZERO);
    let mut rows = Rows::default();
    // the control point S and T reflect, and the command it came from: '\0' for the Python's
    // initial "", which is `in` any string
    let mut last_c: Option<Pt> = None;
    let mut last_cmd = '\0';
    for caps in cmd_re().captures_iter(d) {
        let cmd = caps[1].chars().next().unwrap_or('Z');
        let v = nums(&caps[2]);
        let rel = cmd.is_ascii_lowercase();
        let c = cmd.to_ascii_uppercase();
        let pair = |k: usize| Pt::xy(v[k], v[k + 1]);
        match c {
            'Z' => {
                if let Some(last) = rows.last() {
                    if !allclose(last, start)? {
                        rows.push(start, budget)?;
                    }
                    rows.flush(true, &mut out);
                }
                cur = start;
                last_c = None;
                last_cmd = c;
                continue;
            }
            'M' => {
                rows.flush(false, &mut out);
                let first = Pt::of(&v[..v.len().min(2)]);
                cur = if rel { cur.add(first)? } else { first };
                start = cur;
                rows.push(cur, budget)?;
                for k in (2..v.len().saturating_sub(1)).step_by(2) {
                    cur = if rel { cur.add(pair(k))? } else { pair(k) };
                    rows.push(cur, budget)?;
                }
                last_c = None;
            }
            'L' | 'H' | 'V' => {
                let step = if c == 'L' { 2 } else { 1 };
                for k in (0..(v.len() + 1).saturating_sub(step)).step_by(step) {
                    cur = match c {
                        'L' if rel => cur.add(pair(k))?,
                        'L' => pair(k),
                        'H' => Pt::xy(if rel { cur.at(0)? + v[k] } else { v[k] }, cur.at(1)?),
                        _ => Pt::xy(cur.at(0)?, if rel { cur.at(1)? + v[k] } else { v[k] }),
                    };
                    rows.push(cur, budget)?;
                }
                last_c = None;
            }
            'C' | 'S' | 'Q' | 'T' => {
                let step = match c {
                    'C' => 6,
                    'S' | 'Q' => 4,
                    _ => 2,
                };
                for k in (0..(v.len() + 1).saturating_sub(step)).step_by(step) {
                    let base = if rel { cur } else { Pt::ZERO };
                    // the control point an S or a T reflects, after a segment of its kind
                    let reflect = |kinds: &str| match last_c {
                        Some(lc) if last_cmd == '\0' || kinds.contains(last_cmd) => cur.times(2.0).sub(lc),
                        _ => Ok(cur),
                    };
                    let (ctrl, block, p) = match c {
                        'C' => {
                            let (c1, c2, p) = (base.add(pair(k))?, base.add(pair(k + 2))?, base.add(pair(k + 4))?);
                            (c2, cubic_pt(cur, c1, c2, p, budget)?, p)
                        }
                        'S' => {
                            let (c1, c2, p) = (reflect("CS")?, base.add(pair(k))?, base.add(pair(k + 2))?);
                            (c2, cubic_pt(cur, c1, c2, p, budget)?, p)
                        }
                        'Q' => {
                            let (q, p) = (base.add(pair(k))?, base.add(pair(k + 2))?);
                            (q, quad_pt(cur, q, p, budget)?, p)
                        }
                        _ => {
                            let (q, p) = (reflect("QT")?, base.add(pair(k))?);
                            (q, quad_pt(cur, q, p, budget)?, p)
                        }
                    };
                    rows.extend(block)?;
                    last_c = Some(ctrl);
                    last_cmd = c;
                    cur = p;
                }
                continue;
            }
            _ => {
                // 'A'
                for k in (0..v.len().saturating_sub(6)).step_by(7) {
                    let p = if rel { cur.add(pair(k + 5))? } else { pair(k + 5) };
                    rows.extend(arc_pt(cur, v[k], v[k + 1], v[k + 2], v[k + 3] != 0.0, v[k + 4] != 0.0, p, budget)?)?;
                    cur = p;
                }
                last_c = None;
            }
        }
        last_cmd = c;
    }
    rows.flush(false, &mut out);
    Ok(out)
}

/// `_segments_in`: the segments `d` draws, by how many numbers each command is given.
pub fn segments_in(d: &str) -> usize {
    cmd_re()
        .captures_iter(d)
        .map(|caps| {
            let k = num_re().find_iter(&caps[2]).count();
            match caps[1].to_ascii_uppercase().as_str() {
                "M" => (k / 2).saturating_sub(1),
                "L" | "T" => k / 2,
                "H" | "V" => k,
                "C" => k / 6,
                "S" | "Q" => k / 4,
                "A" => k / 7,
                _ => 0,
            }
        })
        .sum()
}
