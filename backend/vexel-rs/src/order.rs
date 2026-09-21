//! Stage 5: enclosure tree and painter's order.
//!
//! A region B is *enclosed* by A when B does not touch the image border and the
//! only label adjacent to the outside of B's hole-filled footprint is A.
//! Enclosed regions paint after their parent; in stacked mode a region's shape
//! includes all of its descendants so children cover it seamlessly.

use crate::core::grid::{Grid, Mask};
use crate::core::labels::{self, LabelIndex, Labels};
use crate::core::morphology::{dilate_cross, fill_holes};
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};

pub struct Enclosure {
    pub parent: HashMap<i32, Option<i32>>,
    pub children: HashMap<i32, Vec<i32>>,
    pub area: HashMap<i32, usize>,
    pub border: HashMap<i32, usize>,
}

impl Enclosure {
    pub fn descendants(&self, label: i32) -> Vec<i32> {
        let mut out = Vec::new();
        let mut stack: Vec<i32> = self.children.get(&label).cloned().unwrap_or_default();
        while let Some(c) = stack.pop() {
            out.push(c);
            if let Some(g) = self.children.get(&c) {
                stack.extend(g.iter().copied());
            }
        }
        out
    }
}

pub fn enclosure(l: &Labels) -> Enclosure {
    let (h, w) = (l.h, l.w);
    let ids = labels::unique_ids(l);
    let counts = labels::bincount(l);
    let area: HashMap<i32, usize> = ids.iter().map(|i| (*i, counts[*i as usize])).collect();

    let mut border: HashMap<i32, usize> = ids.iter().map(|i| (*i, 0usize)).collect();
    let on_border = |v: i32, border: &mut HashMap<i32, usize>| {
        if v != 0 {
            *border.entry(v).or_insert(0) += 1;
        }
    };
    for c in 0..w {
        on_border(l.data[c], &mut border);
        on_border(l.data[(h - 1) * w + c], &mut border);
    }
    for r in 0..h {
        on_border(l.data[r * w], &mut border);
        on_border(l.data[r * w + w - 1], &mut border);
    }

    // The hole fill and the ring scan are independent per label, and on busy art
    // there are hundreds of them; this is where the Python spends 7 % of its time.
    let parents: Vec<(i32, Option<i32>)> = ids
        .par_iter()
        .map(|i| {
            if border.get(i).copied().unwrap_or(0) > 0 {
                return (*i, None);
            }
            let mask = labels::mask_of(l, *i);
            let filled = fill_holes(&mask);
            let ring = dilate_cross(&filled).and_not(&filled);
            let mut outer: HashSet<i32> = HashSet::new();
            for (k, on) in ring.data.iter().enumerate() {
                if *on {
                    let v = l.data[k];
                    if v != 0 && v != *i {
                        outer.insert(v);
                    }
                }
            }
            (*i, if outer.len() == 1 { outer.into_iter().next() } else { None })
        })
        .collect();

    let parent: HashMap<i32, Option<i32>> = parents.into_iter().collect();
    let mut children: HashMap<i32, Vec<i32>> = ids.iter().map(|i| (*i, Vec::new())).collect();
    for i in &ids {
        if let Some(Some(p)) = parent.get(i) {
            children.entry(*p).or_default().push(*i);
        }
    }
    for v in children.values_mut() {
        v.sort_by_key(|c| std::cmp::Reverse(area.get(c).copied().unwrap_or(0)));
    }
    Enclosure { parent, children, area, border }
}

/// Background-most root first, then depth-first by enclosure, siblings largest first.
pub fn paint_order(enc: &Enclosure) -> Vec<i32> {
    let mut roots: Vec<i32> = enc
        .parent
        .iter()
        .filter(|(_, p)| p.is_none())
        .map(|(i, _)| *i)
        .collect();
    roots.sort_by(|a, b| {
        let ka = (
            std::cmp::Reverse(enc.border.get(a).copied().unwrap_or(0)),
            std::cmp::Reverse(enc.area.get(a).copied().unwrap_or(0)),
        );
        let kb = (
            std::cmp::Reverse(enc.border.get(b).copied().unwrap_or(0)),
            std::cmp::Reverse(enc.area.get(b).copied().unwrap_or(0)),
        );
        ka.cmp(&kb)
    });
    let mut out = Vec::new();
    for r in roots {
        visit(enc, r, &mut out);
    }
    out
}

fn visit(enc: &Enclosure, i: i32, out: &mut Vec<i32>) {
    out.push(i);
    if let Some(cs) = enc.children.get(&i) {
        for c in cs.clone() {
            visit(enc, c, out);
        }
    }
}

/// The labels this element paints over: its own, plus (when stacked) every
/// descendant painted on top of it. Invisible descendants — transparent holes —
/// are left out, or the hole would vanish under its parent.
pub fn shape_labels(label: i32, enc: &Enclosure, stacked: bool, invisible: &HashSet<i32>) -> HashSet<i32> {
    let mut out = HashSet::new();
    out.insert(label);
    if !stacked {
        return out;
    }
    let mut stack: Vec<i32> = enc
        .children
        .get(&label)
        .map(|v| v.iter().copied().filter(|c| !invisible.contains(c)).collect())
        .unwrap_or_default();
    while let Some(c) = stack.pop() {
        out.insert(c);
        if let Some(g) = enc.children.get(&c) {
            stack.extend(g.iter().copied().filter(|x| !invisible.contains(x)));
        }
    }
    out
}

/// Pixels this element paints: its own, plus (when stacked) every descendant
/// that will be painted on top. Invisible descendants — transparent holes — are
/// never covered, or the hole would disappear under the parent.
pub fn shape_mask(
    l: &Labels,
    index: &LabelIndex,
    label: i32,
    enc: &Enclosure,
    stacked: bool,
    invisible: &HashSet<i32>,
) -> Mask {
    let mut mask = Grid::filled(l.h, l.w, false);
    for i in index.pixels(label) {
        mask.data[*i as usize] = true;
    }
    if !stacked {
        return mask;
    }
    let mut stack: Vec<i32> = enc
        .children
        .get(&label)
        .map(|v| v.iter().copied().filter(|c| !invisible.contains(c)).collect())
        .unwrap_or_default();
    while let Some(c) = stack.pop() {
        for i in index.pixels(c) {
            mask.data[*i as usize] = true;
        }
        if let Some(g) = enc.children.get(&c) {
            stack.extend(g.iter().copied().filter(|x| !invisible.contains(x)));
        }
    }
    mask
}

/// Pixels within one pixel of a label change.
pub fn boundary_band(l: &Labels) -> Mask {
    let (h, w) = (l.h, l.w);
    let mut edge = Grid::filled(h, w, false);
    for r in 0..h {
        for c in 0..w {
            let i = r * w + c;
            if c + 1 < w && l.data[i] != l.data[i + 1] {
                edge.data[i] = true;
                edge.data[i + 1] = true;
            }
            if r + 1 < h && l.data[i] != l.data[i + w] {
                edge.data[i] = true;
                edge.data[i + w] = true;
            }
        }
    }
    dilate_cross(&edge)
}
