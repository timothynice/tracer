//! Connected components and sequential relabelling, numbered the way
//! `skimage.measure.label` and `skimage.segmentation.relabel_sequential` do.

use super::grid::{Grid, Mask};
use std::collections::HashMap;

pub type Labels = Grid<i32>;

/// `skimage.measure.label(mask, connectivity=c)`: components numbered 1..K in
/// raster-scan order of their first pixel, background 0.
pub fn label_mask(m: &Mask, connectivity: u8) -> Labels {
    let (h, w) = (m.h, m.w);
    let mut parent: Vec<u32> = Vec::with_capacity(64);
    let mut prov = vec![0u32; h * w]; // provisional labels, 1-based into `parent`

    fn find(parent: &mut [u32], mut i: u32) -> u32 {
        while parent[i as usize] != i {
            parent[i as usize] = parent[parent[i as usize] as usize];
            i = parent[i as usize];
        }
        i
    }
    fn union(parent: &mut [u32], a: u32, b: u32) {
        let (ra, rb) = (find(parent, a), find(parent, b));
        if ra != rb {
            let (lo, hi) = if ra < rb { (ra, rb) } else { (rb, ra) };
            parent[hi as usize] = lo;
        }
    }

    parent.push(0); // slot 0 unused
    for r in 0..h {
        for c in 0..w {
            let i = r * w + c;
            if !m.data[i] {
                continue;
            }
            let mut best: Option<u32> = None;
            let mut neighbours: Vec<u32> = Vec::with_capacity(4);
            if c > 0 && prov[i - 1] != 0 {
                neighbours.push(prov[i - 1]);
            }
            if r > 0 && prov[i - w] != 0 {
                neighbours.push(prov[i - w]);
            }
            if connectivity >= 2 && r > 0 {
                if c > 0 && prov[i - w - 1] != 0 {
                    neighbours.push(prov[i - w - 1]);
                }
                if c + 1 < w && prov[i - w + 1] != 0 {
                    neighbours.push(prov[i - w + 1]);
                }
            }
            for n in &neighbours {
                let rn = find(&mut parent, *n);
                best = Some(match best {
                    None => rn,
                    Some(b) => b.min(rn),
                });
            }
            let lab = match best {
                Some(b) => b,
                None => {
                    let id = parent.len() as u32;
                    parent.push(id);
                    id
                }
            };
            prov[i] = lab;
            for n in &neighbours {
                union(&mut parent, lab, *n);
            }
        }
    }

    // renumber roots by raster order of first appearance
    let mut remap: HashMap<u32, i32> = HashMap::new();
    let mut next = 1i32;
    let mut out = Grid::<i32>::new(h, w);
    for i in 0..h * w {
        if prov[i] == 0 {
            continue;
        }
        let root = find(&mut parent, prov[i]);
        let e = remap.entry(root).or_insert_with(|| {
            let v = next;
            next += 1;
            v
        });
        out.data[i] = *e;
    }
    out
}

/// `skimage.segmentation.relabel_sequential`: the sorted distinct non-zero
/// labels become 1..K. Returns the relabelled grid and the forward map.
pub fn relabel_sequential(l: &Labels) -> (Labels, HashMap<i32, i32>) {
    let mut uniq: Vec<i32> = Vec::new();
    let mut seen = vec![false; 0];
    let max = l.data.iter().copied().max().unwrap_or(0);
    if max >= 0 {
        seen = vec![false; (max + 1) as usize];
        for v in &l.data {
            if *v > 0 {
                seen[*v as usize] = true;
            }
        }
        for (v, s) in seen.iter().enumerate() {
            if *s {
                uniq.push(v as i32);
            }
        }
    }
    let mut fwd: HashMap<i32, i32> = HashMap::with_capacity(uniq.len());
    let mut table = vec![0i32; (max.max(0) + 1) as usize];
    for (i, v) in uniq.iter().enumerate() {
        table[*v as usize] = (i + 1) as i32;
        fwd.insert(*v, (i + 1) as i32);
    }
    let out = Grid {
        h: l.h,
        w: l.w,
        data: l.data.iter().map(|v| if *v > 0 { table[*v as usize] } else { 0 }).collect(),
    };
    (out, fwd)
}

/// Sorted distinct non-zero labels.
pub fn unique_ids(l: &Labels) -> Vec<i32> {
    let max = l.data.iter().copied().max().unwrap_or(0);
    if max <= 0 {
        return Vec::new();
    }
    let mut seen = vec![false; (max + 1) as usize];
    for v in &l.data {
        if *v > 0 {
            seen[*v as usize] = true;
        }
    }
    (1..=max).filter(|v| seen[*v as usize]).collect()
}

/// Pixel counts per label, index 0..=max.
pub fn bincount(l: &Labels) -> Vec<usize> {
    let max = l.data.iter().copied().max().unwrap_or(0).max(0) as usize;
    let mut out = vec![0usize; max + 1];
    for v in &l.data {
        if *v >= 0 {
            out[*v as usize] += 1;
        }
    }
    out
}

pub fn mask_of(l: &Labels, lab: i32) -> Mask {
    Grid { h: l.h, w: l.w, data: l.data.iter().map(|v| *v == lab).collect() }
}

/// Pixel lists per label, built in one pass.
///
/// Most of the pipeline asks "give me this region's pixels" once per region.
/// Answering that by scanning the whole frame costs O(K·N) — on busy art that
/// is a hundred sweeps of a megapixel, and it was the largest single cost in
/// the shadow stage. Here the labels are counting-sorted once and every later
/// question is a slice.
pub struct LabelIndex {
    offsets: Vec<usize>,
    pixels: Vec<u32>,
}

impl LabelIndex {
    pub fn build(l: &Labels) -> Self {
        let max = l.data.iter().copied().max().unwrap_or(0).max(0) as usize;
        let mut counts = vec![0usize; max + 2];
        for v in &l.data {
            if *v >= 0 {
                counts[*v as usize + 1] += 1;
            }
        }
        for i in 1..counts.len() {
            counts[i] += counts[i - 1];
        }
        let offsets = counts.clone();
        let mut cursor = counts;
        let mut pixels = vec![0u32; l.len()];
        for (i, v) in l.data.iter().enumerate() {
            if *v < 0 {
                continue;
            }
            let slot = &mut cursor[*v as usize];
            pixels[*slot] = i as u32;
            *slot += 1;
        }
        LabelIndex { offsets, pixels }
    }

    /// The pixel indices of `label`, in raster order.
    pub fn pixels(&self, label: i32) -> &[u32] {
        if label < 0 || label as usize + 1 >= self.offsets.len() {
            return &[];
        }
        let a = self.offsets[label as usize];
        let b = self.offsets[label as usize + 1];
        &self.pixels[a..b]
    }

    pub fn area(&self, label: i32) -> usize {
        self.pixels(label).len()
    }

    pub fn max_label(&self) -> i32 {
        self.offsets.len() as i32 - 2
    }
}
