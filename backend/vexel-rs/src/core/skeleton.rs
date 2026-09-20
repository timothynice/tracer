//! `skimage.morphology.medial_axis`, with one deliberate difference.
//!
//! skimage breaks ties in the processing order with `np.random.default_rng(None)`
//! — seeded from the OS, so the Python engine's skeletons (and therefore which
//! thin regions become strokes) are **not reproducible between runs**. Here the
//! tiebreak is the pixel's raster index, which is deterministic and otherwise
//! plays exactly the same role: it only ever separates pixels that already tie
//! on both distance and cornerness.

use super::edt;
use super::grid::{Grid, Mask};

/// The 3×3 pattern for a 9-bit index, laid out as skimage's `_pattern_of`:
/// bit 0 is up-left, bit 4 the centre, bit 8 down-right.
fn pattern_of(index: usize) -> [bool; 9] {
    let mut p = [false; 9];
    for (b, slot) in p.iter_mut().enumerate() {
        *slot = index & (1 << b) != 0;
    }
    p
}

/// Number of 8-connected components among the true cells of a 3×3 pattern.
fn components(p: &[bool; 9]) -> usize {
    let mut seen = [false; 9];
    let mut n = 0;
    for start in 0..9 {
        if !p[start] || seen[start] {
            continue;
        }
        n += 1;
        let mut stack = vec![start];
        seen[start] = true;
        while let Some(k) = stack.pop() {
            let (r, c) = (k / 3, k % 3);
            for dr in -1isize..=1 {
                for dc in -1isize..=1 {
                    let (nr, nc) = (r as isize + dr, c as isize + dc);
                    if !(0..=2).contains(&nr) || !(0..=2).contains(&nc) {
                        continue;
                    }
                    let m = (nr * 3 + nc) as usize;
                    if p[m] && !seen[m] {
                        seen[m] = true;
                        stack.push(m);
                    }
                }
            }
        }
    }
    n
}

/// skimage's keep/remove table: keep a foreground pixel when removing it would
/// change the local connectivity, or when it has fewer than three neighbours.
fn build_table() -> [bool; 512] {
    let mut t = [false; 512];
    for (index, slot) in t.iter_mut().enumerate() {
        if index & (1 << 4) == 0 {
            continue;
        }
        let p = pattern_of(index);
        let mut without = p;
        without[4] = false;
        let n_true = p.iter().filter(|b| **b).count();
        *slot = components(&p) != components(&without) || n_true < 3;
    }
    t
}

fn cornerness(index: usize) -> u8 {
    (9 - pattern_of(index).iter().filter(|b| **b).count()) as u8
}

fn neighbourhood_index(m: &Grid<u8>, r: usize, c: usize) -> usize {
    let (h, w) = (m.h, m.w);
    let mut acc = 0usize;
    let on = |rr: isize, cc: isize| -> bool {
        rr >= 0 && cc >= 0 && (rr as usize) < h && (cc as usize) < w && m.data[rr as usize * w + cc as usize] != 0
    };
    let (ri, ci) = (r as isize, c as isize);
    if on(ri - 1, ci - 1) {
        acc += 1;
    }
    if on(ri - 1, ci) {
        acc += 2;
    }
    if on(ri - 1, ci + 1) {
        acc += 4;
    }
    if on(ri, ci - 1) {
        acc += 8;
    }
    if on(ri, ci) {
        acc += 16;
    }
    if on(ri, ci + 1) {
        acc += 32;
    }
    if on(ri + 1, ci - 1) {
        acc += 64;
    }
    if on(ri + 1, ci) {
        acc += 128;
    }
    if on(ri + 1, ci + 1) {
        acc += 256;
    }
    acc
}

/// The medial axis of `image`.
pub fn medial_axis(image: &Mask) -> Mask {
    let table = build_table();
    let dist = edt::edt(image);
    let (h, w) = (image.h, image.w);

    let mut result: Grid<u8> = Grid { h, w, data: image.data.iter().map(|b| *b as u8).collect() };

    // cornerness is read from the *original* mask, before any thinning
    let corner: Vec<u8> = (0..h * w)
        .map(|i| {
            if image.data[i] {
                cornerness(neighbourhood_index(&result, i / w, i % w))
            } else {
                0
            }
        })
        .collect();

    let mut order: Vec<usize> = (0..h * w).filter(|i| image.data[*i]).collect();
    // ascending distance, then ascending cornerness, then raster index
    order.sort_by(|a, b| {
        dist.data[*a]
            .partial_cmp(&dist.data[*b])
            .unwrap()
            .then(corner[*a].cmp(&corner[*b]))
            .then(a.cmp(b))
    });

    for i in order {
        let (r, c) = (i / w, i % w);
        if result.data[i] == 0 {
            continue;
        }
        let idx = neighbourhood_index(&result, r, c);
        if !table[idx] {
            result.data[i] = 0;
        }
    }

    Grid { h, w, data: result.data.iter().map(|v| *v != 0).collect() }
}
