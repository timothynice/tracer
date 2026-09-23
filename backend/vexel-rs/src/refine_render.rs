//! Stage 9b (opt-in): render-and-compare refinement. Mirrors `refine_render.py`
//! move for move; the rasteriser is tiny-skia, the one resvg draws with, so a
//! solid or gradient fill lands on the same pixels the Python side sees.

use std::collections::HashSet;

use tiny_skia::{Color, FillRule, GradientStop, LinearGradient, Paint, Path, PathBuilder, Pixmap, Point, RadialGradient, Shader, SpreadMode, Transform};

use crate::curves::{Segment, Shape, P};
use crate::fills::Fill;
use crate::symmetry::Grid;
use crate::topology::{Arc, Boundary};

pub const SCALE: usize = 4;
pub const HALF: f64 = 8.0;
pub const BAND: f64 = 2.0;
pub const NOISE: f64 = 0.02;

/// One painted shape as the assembly sees it: the labels it paints, a
/// whole-shape primitive or the rings whose fitted arcs make its path, its fill.
pub struct Rec {
    pub member: HashSet<i32>,
    pub primitive: Option<Shape>,
    pub rings: Vec<Vec<(usize, bool)>>,
    pub fill: Fill,
    pub attrs: String,
    pub filtered: bool,
}

pub fn shape_of(bnd: &Boundary, rec: &Rec) -> Shape {
    match &rec.primitive {
        Some(s) => s.clone(),
        None => Shape::Path { contours: rec.rings.iter().map(|r| bnd.segments(r, Some(&rec.member))).collect() },
    }
}

fn round_to(v: f64, precision: usize) -> f64 {
    // what `fmt` writes into the file, read back: the Python renders its markup
    format!("{:.*}", precision, v).parse().unwrap_or(v)
}

fn arc_cubics(pb: &mut PathBuilder, p0: P, p1: P, r: f64, large: bool, sweep: bool, pr: usize) {
    let c = crate::reuse::arc_centre(p0, p1, r, large, sweep);
    let a0 = (p0[1] - c[1]).atan2(p0[0] - c[0]);
    let a1 = (p1[1] - c[1]).atan2(p1[0] - c[0]);
    let tau = 2.0 * std::f64::consts::PI;
    let span = if sweep { (a1 - a0).rem_euclid(tau) } else { -((a0 - a1).rem_euclid(tau)) };
    let rr = ((p0[0] - c[0]).powi(2) + (p0[1] - c[1]).powi(2)).sqrt();
    let pieces = ((span.abs() / (std::f64::consts::PI / 2.0)).ceil() as usize).max(1);
    let step = span / pieces as f64;
    let k = 4.0 / 3.0 * (step / 4.0).tan();
    for i in 0..pieces {
        let (s, e) = (a0 + step * i as f64, a0 + step * (i + 1) as f64);
        let (ps, pe) = ([c[0] + rr * s.cos(), c[1] + rr * s.sin()], [c[0] + rr * e.cos(), c[1] + rr * e.sin()]);
        let c1 = [ps[0] - k * rr * s.sin(), ps[1] + k * rr * s.cos()];
        let c2 = [pe[0] + k * rr * e.sin(), pe[1] - k * rr * e.cos()];
        let end = if i + 1 == pieces { p1 } else { pe };
        pb.cubic_to(round_to(c1[0], pr) as f32, round_to(c1[1], pr) as f32, round_to(c2[0], pr) as f32, round_to(c2[1], pr) as f32, round_to(end[0], pr) as f32, round_to(end[1], pr) as f32);
    }
}

fn path_of(shape: &Shape, pr: usize) -> Option<(Path, FillRule)> {
    let r = |v: f64| round_to(v, pr) as f32;
    match shape {
        Shape::Circle { cx, cy, r: rad } => PathBuilder::from_circle(r(*cx), r(*cy), r(*rad)).map(|p| (p, FillRule::Winding)),
        Shape::Ellipse { cx, cy, rx, ry, angle_deg } => {
            let rect = tiny_skia::Rect::from_xywh(r(*cx) - r(*rx), r(*cy) - r(*ry), 2.0 * r(*rx), 2.0 * r(*ry))?;
            let path = PathBuilder::from_oval(rect)?;
            let path = if angle_deg.abs() > 0.05 { path.transform(Transform::from_rotate_at(round_to(*angle_deg, 2) as f32, r(*cx), r(*cy)))? } else { path };
            Some((path, FillRule::Winding))
        }
        Shape::Rect { x, y, w, h } => Some((PathBuilder::from_rect(tiny_skia::Rect::from_xywh(r(*x), r(*y), r(*w), r(*h))?), FillRule::Winding)),
        Shape::RoundedRect { x, y, w, h, rx } => {
            let (x0, y0, x1, y1, q) = (r(*x), r(*y), r(*x) + r(*w), r(*y) + r(*h), r(*rx));
            let k = 0.552_284_75 * q;
            let mut pb = PathBuilder::new();
            pb.move_to(x0 + q, y0);
            pb.line_to(x1 - q, y0);
            pb.cubic_to(x1 - q + k, y0, x1, y0 + q - k, x1, y0 + q);
            pb.line_to(x1, y1 - q);
            pb.cubic_to(x1, y1 - q + k, x1 - q + k, y1, x1 - q, y1);
            pb.line_to(x0 + q, y1);
            pb.cubic_to(x0 + q - k, y1, x0, y1 - q + k, x0, y1 - q);
            pb.line_to(x0, y0 + q);
            pb.cubic_to(x0, y0 + q - k, x0 + q - k, y0, x0 + q, y0);
            pb.close();
            pb.finish().map(|p| (p, FillRule::Winding))
        }
        Shape::Path { contours } => {
            let mut pb = PathBuilder::new();
            for contour in contours {
                let Some(first) = contour.first() else { continue };
                let s = first.start();
                pb.move_to(r(s[0]), r(s[1]));
                for seg in contour {
                    match seg {
                        Segment::Line { p1, .. } => pb.line_to(r(p1[0]), r(p1[1])),
                        Segment::Cubic { c1, c2, p1, .. } => pb.cubic_to(r(c1[0]), r(c1[1]), r(c2[0]), r(c2[1]), r(p1[0]), r(p1[1])),
                        Segment::Arc { p0, p1, r: rad, large, sweep } => arc_cubics(&mut pb, *p0, *p1, *rad, *large, *sweep, pr),
                    }
                }
                pb.close();
            }
            pb.finish().map(|p| (p, if contours.len() > 1 { FillRule::EvenOdd } else { FillRule::Winding }))
        }
    }
}

fn colour(rgba: [f64; 4]) -> Color {
    let c = |v: f64| (v.clamp(0.0, 255.0) / 255.0) as f32;
    Color::from_rgba(c(rgba[0]), c(rgba[1]), c(rgba[2]), c(rgba[3])).unwrap_or(Color::BLACK)
}

fn shader(fill: &Fill, t: Transform, pr: usize) -> Option<Shader<'static>> {
    let r = |v: f64| round_to(v, pr) as f32;
    match fill {
        Fill::Solid { rgba } => Some(Shader::SolidColor(colour(*rgba))),
        Fill::Linear { x1, y1, x2, y2, stops } => LinearGradient::new(
            Point::from_xy(r(*x1), r(*y1)),
            Point::from_xy(r(*x2), r(*y2)),
            stops.iter().map(|s| GradientStop::new(s.offset.clamp(0.0, 1.0) as f32, colour(s.rgba))).collect(),
            SpreadMode::Pad,
            t,
        ),
        Fill::Radial { cx, cy, r: rad, stops } => RadialGradient::new(
            Point::from_xy(r(*cx), r(*cy)),
            Point::from_xy(r(*cx), r(*cy)),
            r(*rad),
            stops.iter().map(|s| GradientStop::new(s.offset.clamp(0.0, 1.0) as f32, colour(s.rgba))).collect(),
            SpreadMode::Pad,
            t,
        ),
    }
}

struct Crop {
    x0: usize,
    y0: usize,
    w: usize,
    h: usize,
    band: Vec<bool>,
    src: Vec<[f64; 3]>, // over white
}

fn on_white(r: f64, g: f64, b: f64, a: f64) -> [f64; 3] {
    let al = a / 255.0;
    [r * al + 255.0 * (1.0 - al), g * al + 255.0 * (1.0 - al), b * al + 255.0 * (1.0 - al)]
}

impl Crop {
    fn new(at: P, height: usize, width: usize, arc_pts: &[P], src: &[u8]) -> Crop {
        let x0 = (at[0] - HALF).floor().max(0.0) as usize;
        let y0 = (at[1] - HALF).floor().max(0.0) as usize;
        let x1 = ((at[0] + HALF).ceil() as usize).min(width);
        let y1 = ((at[1] + HALF).ceil() as usize).min(height);
        let (w, h) = (x1.saturating_sub(x0), y1.saturating_sub(y0));
        let grid = Grid::new(arc_pts);
        let mut band = Vec::with_capacity(w * h);
        let mut s = Vec::with_capacity(w * h);
        for y in y0..y1 {
            for x in x0..x1 {
                let (d, _) = grid.nearest_within([x as f64 + 0.5, y as f64 + 0.5], BAND + 1e-9);
                band.push(d <= BAND);
                let i = (y * width + x) * 4;
                s.push(on_white(src[i] as f64, src[i + 1] as f64, src[i + 2] as f64, src[i + 3] as f64));
            }
        }
        Crop { x0, y0, w, h, band, src: s }
    }

    fn any(&self) -> bool {
        self.band.iter().any(|b| *b)
    }

    fn error(&self, shapes: &[(Shape, &Fill)], pr: usize) -> f64 {
        if self.w == 0 || self.h == 0 || !self.any() {
            return 0.0;
        }
        let Some(mut pix) = Pixmap::new((self.w * SCALE) as u32, (self.h * SCALE) as u32) else { return 0.0 };
        let t = Transform::from_row(SCALE as f32, 0.0, 0.0, SCALE as f32, -(self.x0 as f32) * SCALE as f32, -(self.y0 as f32) * SCALE as f32);
        for (shape, fill) in shapes {
            let Some((path, rule)) = path_of(shape, pr) else { continue };
            let Some(sh) = shader(fill, t, pr) else { continue };
            let mut paint = Paint::default();
            paint.shader = sh;
            paint.anti_alias = true;
            pix.fill_path(&path, &paint, rule, t, None);
        }
        let px = pix.pixels();
        let (mut sum, mut n) = (0.0, 0usize);
        for y in 0..self.h {
            for x in 0..self.w {
                if !self.band[y * self.w + x] {
                    continue;
                }
                let mut acc = [0.0f64; 4];
                for dy in 0..SCALE {
                    for dx in 0..SCALE {
                        let c = px[(y * SCALE + dy) * self.w * SCALE + x * SCALE + dx].demultiply();
                        acc[0] += c.red() as f64;
                        acc[1] += c.green() as f64;
                        acc[2] += c.blue() as f64;
                        acc[3] += c.alpha() as f64;
                    }
                }
                let k = (SCALE * SCALE) as f64;
                let got = on_white(acc[0] / k, acc[1] / k, acc[2] / k, acc[3] / k);
                let want = self.src[y * self.w + x];
                sum += (got[0] - want[0]).abs() + (got[1] - want[1]).abs() + (got[2] - want[2]).abs();
                n += 3;
            }
        }
        if n == 0 { 0.0 } else { sum / n as f64 }
    }
}

#[derive(Clone, Copy)]
enum Kind {
    C1,
    C2,
    Joint,
}

fn normal(a: P, b: P) -> P {
    let d = [b[0] - a[0], b[1] - a[1]];
    let n = (d[0] * d[0] + d[1] * d[1]).sqrt();
    if n > 1e-9 { [-d[1] / n, d[0] / n] } else { [0.0, 1.0] }
}

fn moves(segments: &[Segment]) -> Vec<(Kind, usize, P)> {
    let mut out = Vec::new();
    for (k, seg) in segments.iter().enumerate() {
        if let Segment::Cubic { p0, p1, .. } = seg {
            let n = normal(*p0, *p1);
            out.push((Kind::C1, k, n));
            out.push((Kind::C2, k, n));
        }
        if k + 1 < segments.len() {
            let nxt = &segments[k + 1];
            if matches!(seg, Segment::Arc { .. }) || matches!(nxt, Segment::Arc { .. }) {
                continue;
            }
            let t_in = match seg {
                Segment::Cubic { c2, p1, .. } => [p1[0] - c2[0], p1[1] - c2[1]],
                _ => [seg.end()[0] - seg.start()[0], seg.end()[1] - seg.start()[1]],
            };
            let t_out = match nxt {
                Segment::Cubic { c1, p0, .. } => [c1[0] - p0[0], c1[1] - p0[1]],
                _ => [nxt.end()[0] - nxt.start()[0], nxt.end()[1] - nxt.start()[1]],
            };
            let (ni, no) = ((t_in[0].powi(2) + t_in[1].powi(2)).sqrt().max(1e-9), (t_out[0].powi(2) + t_out[1].powi(2)).sqrt().max(1e-9));
            let t = [t_in[0] / ni + t_out[0] / no, t_in[1] / ni + t_out[1] / no];
            let n = [-t[1], t[0]];
            let nn = (n[0] * n[0] + n[1] * n[1]).sqrt();
            out.push((Kind::Joint, k, if nn > 1e-9 { [n[0] / nn, n[1] / nn] } else { normal(seg.start(), seg.end()) }));
        }
    }
    out
}

fn add(p: P, d: P) -> P {
    [p[0] + d[0], p[1] + d[1]]
}

fn apply(segments: &mut [Segment], mv: (Kind, usize, P), delta: P) {
    let (kind, k, _) = mv;
    match kind {
        Kind::C1 => {
            if let Segment::Cubic { c1, .. } = &mut segments[k] {
                *c1 = add(*c1, delta);
            }
        }
        Kind::C2 => {
            if let Segment::Cubic { c2, .. } = &mut segments[k] {
                *c2 = add(*c2, delta);
            }
        }
        Kind::Joint => {
            match &mut segments[k] {
                Segment::Cubic { c2, p1, .. } => {
                    *p1 = add(*p1, delta);
                    *c2 = add(*c2, delta);
                }
                Segment::Line { p1, .. } | Segment::Arc { p1, .. } => *p1 = add(*p1, delta),
            }
            match &mut segments[k + 1] {
                Segment::Cubic { c1, p0, .. } => {
                    *p0 = add(*p0, delta);
                    *c1 = add(*c1, delta);
                }
                Segment::Line { p0, .. } | Segment::Arc { p0, .. } => *p0 = add(*p0, delta),
            }
        }
    }
}

fn point(segments: &[Segment], mv: (Kind, usize, P)) -> P {
    let (kind, k, _) = mv;
    match (&segments[k], kind) {
        (Segment::Cubic { c1, .. }, Kind::C1) => *c1,
        (Segment::Cubic { c2, .. }, Kind::C2) => *c2,
        (seg, _) => seg.end(),
    }
}

fn apply_node(arcs: &mut [Arc], ends: &[(usize, bool)], delta: P) {
    for (i, at_start) in ends {
        let segs = &mut arcs[*i].segments;
        if *at_start {
            match &mut segs[0] {
                Segment::Cubic { p0, c1, .. } => {
                    *p0 = add(*p0, delta);
                    *c1 = add(*c1, delta);
                }
                Segment::Line { p0, .. } | Segment::Arc { p0, .. } => *p0 = add(*p0, delta),
            }
        } else {
            let last = segs.len() - 1;
            match &mut segs[last] {
                Segment::Cubic { p1, c2, .. } => {
                    *p1 = add(*p1, delta);
                    *c2 = add(*c2, delta);
                }
                Segment::Line { p1, .. } | Segment::Arc { p1, .. } => *p1 = add(*p1, delta),
            }
        }
    }
}

fn neighbours<'a>(bnd: &Boundary, records: &'a [Rec], labels: &[i32]) -> Vec<(Shape, &'a Fill)> {
    records
        .iter()
        .filter(|r| !r.filtered && labels.iter().any(|l| r.member.contains(l)))
        .map(|r| (shape_of(bnd, r), &r.fill))
        .collect()
}

/// Refine the graph's fitted segments in place; the number of moves kept. See the Python `refine`.
pub fn refine(bnd: &mut Boundary, records: &[Rec], src: &[u8], height: usize, width: usize, precision: usize, iterations: usize, step: f64) -> usize {
    let mut kept = 0;
    // node id → the (arc, at_start) ends that meet there, in first-seen order
    let mut nodes: Vec<(u64, Vec<(usize, bool)>)> = Vec::new();
    for (i, arc) in bnd.arcs.iter().enumerate() {
        if arc.closed() || arc.segments.is_empty() {
            continue;
        }
        for (node, at_start) in [(arc.n0, true), (arc.n1, false)] {
            let Some(node) = node else { continue };
            match nodes.iter_mut().find(|(n, _)| *n == node) {
                Some((_, ends)) => ends.push((i, at_start)),
                None => nodes.push((node, vec![(i, at_start)])),
            }
        }
    }
    for _ in 0..iterations {
        for (_node, ends) in &nodes {
            if ends.iter().any(|(i, _)| bnd.arcs[*i].pair.0 == 0 || bnd.arcs[*i].pair.1 == 0) {
                continue;
            }
            if ends.iter().any(|(i, at_start)| if *at_start { bnd.arcs[*i].tip0 } else { bnd.arcs[*i].tip1 }) {
                continue;
            }
            let mut labels: Vec<i32> = ends.iter().flat_map(|(i, _)| [bnd.arcs[*i].pair.0, bnd.arcs[*i].pair.1]).collect();
            labels.sort_unstable();
            labels.dedup();
            let (first, at_start) = ends[0];
            let seg = if at_start { &bnd.arcs[first].segments[0] } else { bnd.arcs[first].segments.last().unwrap() };
            let at = if at_start { seg.start() } else { seg.end() };
            let shapes = neighbours(bnd, records, &labels);
            if shapes.len() < 2 {
                continue;
            }
            let pts: Vec<P> = ends.iter().flat_map(|(i, _)| bnd.arcs[*i].pts.iter().copied()).collect();
            let crop = Crop::new(at, height, width, &pts, src);
            if !crop.any() {
                continue;
            }
            let mut best = crop.error(&shapes, precision);
            for delta in [[step, 0.0], [-step, 0.0], [0.0, step], [0.0, -step]] {
                apply_node(&mut bnd.arcs, ends, delta);
                let err = crop.error(&neighbours(bnd, records, &labels), precision);
                if err < best - NOISE {
                    best = err;
                    kept += 1;
                    continue;
                }
                apply_node(&mut bnd.arcs, ends, [-delta[0], -delta[1]]);
            }
        }
        for i in 0..bnd.arcs.len() {
            let (pair, n_pts, n_segs) = (bnd.arcs[i].pair, bnd.arcs[i].pts.len(), bnd.arcs[i].segments.len());
            if n_segs == 0 || pair.0 == 0 || pair.1 == 0 || n_pts < 2 {
                continue;
            }
            let labels = [pair.0, pair.1];
            if neighbours(bnd, records, &labels).len() < 2 {
                continue;
            }
            for mv in moves(&bnd.arcs[i].segments.clone()) {
                let at = point(&bnd.arcs[i].segments, mv);
                let crop = Crop::new(at, height, width, &bnd.arcs[i].pts, src);
                if !crop.any() {
                    continue;
                }
                let mut best = crop.error(&neighbours(bnd, records, &labels), precision);
                for sign in [1.0, -1.0] {
                    let delta = [mv.2[0] * sign * step, mv.2[1] * sign * step];
                    apply(&mut bnd.arcs[i].segments, mv, delta);
                    let err = crop.error(&neighbours(bnd, records, &labels), precision);
                    if err < best - NOISE {
                        best = err;
                        kept += 1;
                        break;
                    }
                    apply(&mut bnd.arcs[i].segments, mv, [-delta[0], -delta[1]]);
                }
            }
        }
    }
    kept
}
