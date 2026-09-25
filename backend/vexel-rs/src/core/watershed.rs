//! `skimage.segmentation.watershed(image, markers, connectivity=1)`.
//!
//! The result depends on more than the algorithm: ties in pixel value are
//! broken by *age* (insertion order), and ties in age — which happen among the
//! markers, all of which are inserted at age 0 — are broken by the internal
//! layout of skimage's binary heap. Reproducing the segmentation therefore
//! means reproducing that heap, sift-for-sift, and pushing the markers in
//! raster order. A plain `BinaryHeap` gives a different, equally valid
//! segmentation, and every region id downstream shifts with it.

use super::grid::Grid;
use super::labels::Labels;

#[derive(Clone, Copy)]
struct Item {
    value: f64,
    age: u64,
    index: u32,
}

#[inline]
fn smaller(a: &Item, b: &Item) -> bool {
    if a.value != b.value {
        return a.value < b.value;
    }
    a.age < b.age
}

/// skimage's `heap_general.pxi`, reproduced: push sifts up by shifting the
/// parent down, pop moves the last item to the root and sifts down preferring
/// the left child on a tie.
struct Heap {
    data: Vec<Item>,
}

impl Heap {
    fn new() -> Self {
        Heap { data: Vec::new() }
    }

    fn push(&mut self, e: Item) {
        self.data.push(e);
        let mut child = self.data.len() - 1;
        while child > 0 {
            // floor division, as skimage's `heappush` does — `div_ceil` here
            // picks the wrong parent for every odd index and quietly gives a
            // different (still valid) segmentation
            #[allow(clippy::manual_div_ceil)]
            let parent = (child + 1) / 2 - 1;
            if smaller(&e, &self.data[parent]) {
                self.data[child] = self.data[parent];
                child = parent;
            } else {
                break;
            }
        }
        self.data[child] = e;
    }

    fn pop(&mut self) -> Item {
        let top = self.data[0];
        let last = self.data.pop().unwrap();
        if self.data.is_empty() {
            return top;
        }
        self.data[0] = last;
        let n = self.data.len();
        let mut i = 0usize;
        loop {
            let l = 2 * i + 1;
            let r = 2 * i + 2;
            let mut smallest = if l < n && smaller(&self.data[l], &self.data[i]) { l } else { i };
            if r < n && smaller(&self.data[r], &self.data[smallest]) {
                smallest = r;
            }
            if smallest == i {
                break;
            }
            self.data.swap(i, smallest);
            i = smallest;
        }
        top
    }

    fn len(&self) -> usize {
        self.data.len()
    }
}

/// Flood `image` from the non-zero pixels of `markers`. Every pixel ends up
/// carrying a label; ties settle towards the marker reached first.
pub fn watershed(image: &Grid<f64>, markers: &Labels) -> Labels {
    watershed_masked(image, markers, None)
}

/// `watershed(image, markers, mask=mask)`: the flood only enters pixels of
/// `mask` (skimage skips a neighbour outside it, and zeroes markers outside
/// it), and the pixels outside it stay 0.
pub fn watershed_masked(image: &Grid<f64>, markers: &Labels, mask: Option<&Grid<bool>>) -> Labels {
    let (h, w) = (image.h, image.w);
    let mut out = markers.clone();
    let inside = |i: usize| mask.is_none_or(|m| m.data[i]);
    if let Some(m) = mask {
        for (o, k) in out.data.iter_mut().zip(m.data.iter()) {
            if !*k {
                *o = 0;
            }
        }
    }
    let open = |out: &Labels, i: usize| out.data[i] == 0 && inside(i);
    let mut heap = Heap::new();
    let mut age: u64 = 0;

    // Raster order, exactly `np.flatnonzero(output)` — but only the markers
    // that touch an unlabelled pixel. A marker whose four neighbours are all
    // labelled pops, finds nothing to claim and exits; labels are only ever
    // set, never cleared, so it can never acquire one later either. Skipping
    // them leaves every claim and every age untouched and, when the partition
    // absorbs small regions, removes nine tenths of the heap.
    for i in 0..h * w {
        if out.data[i] == 0 {
            continue;
        }
        let (r, c) = (i / w, i % w);
        let touches_unlabelled = (r > 0 && open(&out, i - w))
            || (c > 0 && open(&out, i - 1))
            || (c + 1 < w && open(&out, i + 1))
            || (r + 1 < h && open(&out, i + w));
        if touches_unlabelled {
            heap.push(Item { value: image.data[i], age: 0, index: i as u32 });
        }
    }

    while heap.len() > 0 {
        let e = heap.pop();
        let i = e.index as usize;
        let (r, c) = (i / w, i % w);
        // the neighbour order skimage's `_offsets_to_raveled_neighbors` yields
        // for a 4-connected footprint: −W, −1, +1, +W
        let mut nbrs: [usize; 4] = [usize::MAX; 4];
        if r > 0 {
            nbrs[0] = i - w;
        }
        if c > 0 {
            nbrs[1] = i - 1;
        }
        if c + 1 < w {
            nbrs[2] = i + 1;
        }
        if r + 1 < h {
            nbrs[3] = i + w;
        }
        for n in nbrs {
            if n == usize::MAX || !open(&out, n) {
                continue;
            }
            age += 1;
            out.data[n] = out.data[i];
            heap.push(Item { value: image.data[n], age, index: n as u32 });
        }
    }
    out
}
