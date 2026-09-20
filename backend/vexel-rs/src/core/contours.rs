//! `skimage.measure.find_contours(array, 0.5)`: marching squares plus the
//! deque-joining that turns the loose segments into ordered contours.
//!
//! Segment endpoints are compared for exact float equality, which is sound
//! because two squares sharing an edge interpolate the same two corner values
//! and so produce the same bits.

use super::grid::Grid;
use std::collections::HashMap;

pub type Pt = (f64, f64); // (row, col)

#[inline]
fn key(p: Pt) -> (u64, u64) {
    (p.0.to_bits(), p.1.to_bits())
}

#[inline]
fn frac(from: f64, to: f64, level: f64) -> f64 {
    if to == from {
        return 0.0;
    }
    (level - from) / (to - from)
}

/// The 16 marching-squares cases, in skimage's orientation and with its
/// `vertex_connect_high=False` resolution of the two saddles.
fn segments(a: &Grid<f64>, level: f64) -> Vec<(Pt, Pt)> {
    let mut out = Vec::new();
    for r0 in 0..a.h.saturating_sub(1) {
        let r1 = r0 + 1;
        for c0 in 0..a.w.saturating_sub(1) {
            let c1 = c0 + 1;
            let ul = a.data[r0 * a.w + c0];
            let ur = a.data[r0 * a.w + c1];
            let ll = a.data[r1 * a.w + c0];
            let lr = a.data[r1 * a.w + c1];
            if ul.is_nan() || ur.is_nan() || ll.is_nan() || lr.is_nan() {
                continue;
            }
            let mut case = 0u8;
            if ul > level {
                case += 1;
            }
            if ur > level {
                case += 2;
            }
            if ll > level {
                case += 4;
            }
            if lr > level {
                case += 8;
            }
            if case == 0 || case == 15 {
                continue;
            }
            let top: Pt = (r0 as f64, c0 as f64 + frac(ul, ur, level));
            let bottom: Pt = (r1 as f64, c0 as f64 + frac(ll, lr, level));
            let left: Pt = (r0 as f64 + frac(ul, ll, level), c0 as f64);
            let right: Pt = (r0 as f64 + frac(ur, lr, level), c1 as f64);
            match case {
                1 => out.push((top, left)),
                2 => out.push((right, top)),
                3 => out.push((right, left)),
                4 => out.push((left, bottom)),
                5 => out.push((top, bottom)),
                6 => {
                    out.push((right, top));
                    out.push((left, bottom));
                }
                7 => out.push((right, bottom)),
                8 => out.push((bottom, right)),
                9 => {
                    out.push((top, left));
                    out.push((bottom, right));
                }
                10 => out.push((bottom, top)),
                11 => out.push((bottom, left)),
                12 => out.push((left, right)),
                13 => out.push((top, right)),
                14 => out.push((left, top)),
                _ => {}
            }
        }
    }
    out
}

/// skimage's `_assemble_contours`: grow contours by their ends, join two when a
/// segment bridges them, and keep the creation order so the output is stable.
fn assemble(segs: &[(Pt, Pt)]) -> Vec<Vec<Pt>> {
    let mut contours: HashMap<usize, std::collections::VecDeque<Pt>> = HashMap::new();
    let mut starts: HashMap<(u64, u64), usize> = HashMap::new();
    let mut ends: HashMap<(u64, u64), usize> = HashMap::new();
    let mut next = 0usize;

    for (from, to) in segs {
        if key(*from) == key(*to) {
            continue;
        }
        let tail_num = starts.remove(&key(*to));
        let head_num = ends.remove(&key(*from));
        match (tail_num, head_num) {
            (Some(t), Some(hd)) => {
                if t == hd {
                    contours.get_mut(&t).unwrap().push_back(*to);
                    // a closed ring: it has no free end any more
                } else if t > hd {
                    let tail = contours.remove(&t).unwrap();
                    let head = contours.get_mut(&hd).unwrap();
                    head.extend(tail);
                    let (f, l) = (head[0], head[head.len() - 1]);
                    starts.insert(key(f), hd);
                    ends.insert(key(l), hd);
                } else {
                    let head = contours.remove(&hd).unwrap();
                    let tail = contours.get_mut(&t).unwrap();
                    for p in head.into_iter().rev() {
                        tail.push_front(p);
                    }
                    let (f, l) = (tail[0], tail[tail.len() - 1]);
                    starts.insert(key(f), t);
                    ends.insert(key(l), t);
                }
            }
            (None, None) => {
                let mut d = std::collections::VecDeque::new();
                d.push_back(*from);
                d.push_back(*to);
                contours.insert(next, d);
                starts.insert(key(*from), next);
                ends.insert(key(*to), next);
                next += 1;
            }
            (Some(t), None) => {
                contours.get_mut(&t).unwrap().push_front(*from);
                starts.insert(key(*from), t);
            }
            (None, Some(hd)) => {
                contours.get_mut(&hd).unwrap().push_back(*to);
                ends.insert(key(*to), hd);
            }
        }
    }

    let mut ids: Vec<usize> = contours.keys().copied().collect();
    ids.sort_unstable();
    ids.into_iter().map(|i| contours.remove(&i).unwrap().into_iter().collect()).collect()
}

/// Contours of `a` at `level`, each an ordered list of (row, col) points.
pub fn find_contours(a: &Grid<f64>, level: f64) -> Vec<Vec<Pt>> {
    assemble(&segments(a, level))
}

/// Pad `field` with one ring of `value` — the Python pads with 0 before tracing
/// so a shape touching the image border still closes.
pub fn pad(field: &Grid<f64>, value: f64) -> Grid<f64> {
    let (h, w) = (field.h + 2, field.w + 2);
    let mut out = Grid::filled(h, w, value);
    for r in 0..field.h {
        out.data[(r + 1) * w + 1..(r + 1) * w + 1 + field.w]
            .copy_from_slice(&field.data[r * field.w..(r + 1) * field.w]);
    }
    out
}
