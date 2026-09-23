//! `skimage.morphology.medial_axis`, with a fixed thinning order.
//!
//! skimage breaks ties in the processing order with `np.random.default_rng(None)`
//! — seeded from the OS, so out of the box its skeletons (and therefore which
//! thin regions become strokes) are **not reproducible between runs**. Both
//! engines break the tie the same way instead: by a hash of the pixel's raster
//! index (`pixel_key`, the splitmix64 finaliser), which the Python side hands
//! the same ordering (`strokes.medial_axis`). It separates only pixels
//! that already tie on distance and cornerness, and it is as even-handed as the
//! random draw. The raster index itself is not: it thins the same side of a
//! line first every time, and the skeleton of a two-pixel ring then sits half a
//! pixel off centre all the way round — far enough for the stroke fidelity gate
//! to fail a ring the random draws pass. `tools/diffcheck.py`'s `skeleton` stage
//! holds the two skeletons to pixel-for-pixel equality.

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

/// A predictable pseudo-random key per raster index: the splitmix64 finaliser,
/// a bijection on u64, so distinct pixels never tie. Mirrors `strokes._pixel_keys`.
pub fn pixel_key(index: usize) -> u64 {
    let mut z = (index as u64).wrapping_add(0x9E37_79B9_7F4A_7C15);
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
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
    // ascending distance, then ascending cornerness, then the pixel's hash key
    order.sort_by(|a, b| {
        dist.data[*a]
            .partial_cmp(&dist.data[*b])
            .unwrap()
            .then(corner[*a].cmp(&corner[*b]))
            .then(pixel_key(*a).cmp(&pixel_key(*b)))
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pixel_key_is_splitmix64s_finaliser() {
        // the reference values `strokes._pixel_keys` produces for 0..4
        assert_eq!(pixel_key(0), 0xe220_a839_7b1d_cdaf);
        assert_eq!(pixel_key(1), 0x910a_2dec_8902_5cc1);
        assert_eq!(pixel_key(2), 0x9758_35de_1c97_56ce);
        assert_eq!(pixel_key(3), 0x1d0b_14e4_db01_8fed);
    }

    #[test]
    fn a_two_pixel_bar_thins_from_both_sides() {
        // Every pixel of a two-pixel bar ties with the one across from it on
        // distance and cornerness. Thinning in raster order removes the top row
        // everywhere and leaves the skeleton on the bottom row, half a pixel off
        // centre; the hashed order takes from both rows.
        let (h, w) = (7, 40);
        let mut bar = Grid::filled(h, w, false);
        for r in 3..5 {
            for c in 2..38 {
                bar.data[r * w + c] = true;
            }
        }
        let skel = medial_axis(&bar);
        let rows: std::collections::BTreeSet<usize> =
            (0..h * w).filter(|i| skel.data[*i]).map(|i| i / w).collect();
        assert_eq!(rows, [3usize, 4].into_iter().collect());
        assert_eq!(skel.data.iter().filter(|b| **b).count(), 36);
    }
}
