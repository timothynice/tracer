//! Row-major 2-D buffer. The whole pipeline speaks `Grid`, so an index is a
//! single multiply-add and a row is a contiguous slice.

#[derive(Clone, Debug, PartialEq)]
pub struct Grid<T> {
    pub h: usize,
    pub w: usize,
    pub data: Vec<T>,
}

impl<T: Clone + Default> Grid<T> {
    pub fn new(h: usize, w: usize) -> Self {
        Grid { h, w, data: vec![T::default(); h * w] }
    }
}

impl<T: Clone> Grid<T> {
    pub fn filled(h: usize, w: usize, v: T) -> Self {
        Grid { h, w, data: vec![v; h * w] }
    }

    pub fn from_vec(h: usize, w: usize, data: Vec<T>) -> Self {
        assert_eq!(data.len(), h * w);
        Grid { h, w, data }
    }

    #[inline]
    pub fn idx(&self, r: usize, c: usize) -> usize {
        r * self.w + c
    }

    #[inline]
    pub fn get(&self, r: usize, c: usize) -> &T {
        &self.data[r * self.w + c]
    }

    #[inline]
    pub fn set(&mut self, r: usize, c: usize, v: T) {
        let i = r * self.w + c;
        self.data[i] = v;
    }

    pub fn len(&self) -> usize {
        self.data.len()
    }

    pub fn is_empty(&self) -> bool {
        self.data.is_empty()
    }

    pub fn row(&self, r: usize) -> &[T] {
        &self.data[r * self.w..(r + 1) * self.w]
    }

    pub fn row_mut(&mut self, r: usize) -> &mut [T] {
        let w = self.w;
        &mut self.data[r * w..(r + 1) * w]
    }

    pub fn map<U: Clone, F: Fn(&T) -> U>(&self, f: F) -> Grid<U> {
        Grid { h: self.h, w: self.w, data: self.data.iter().map(f).collect() }
    }

    pub fn same_shape<U>(&self, other: &Grid<U>) -> bool {
        self.h == other.h && self.w == other.w
    }
}

pub type Mask = Grid<bool>;

impl Grid<bool> {
    pub fn count(&self) -> usize {
        self.data.iter().filter(|b| **b).count()
    }

    pub fn any(&self) -> bool {
        self.data.iter().any(|b| *b)
    }

    /// (r0, r1, c0, c1) half-open bounding box of the true pixels, or None when empty.
    pub fn bbox(&self) -> Option<(usize, usize, usize, usize)> {
        let (mut r0, mut r1, mut c0, mut c1) = (usize::MAX, 0usize, usize::MAX, 0usize);
        for r in 0..self.h {
            for c in 0..self.w {
                if self.data[r * self.w + c] {
                    if r < r0 { r0 = r; }
                    if r + 1 > r1 { r1 = r + 1; }
                    if c < c0 { c0 = c; }
                    if c + 1 > c1 { c1 = c + 1; }
                }
            }
        }
        if r0 == usize::MAX { None } else { Some((r0, r1, c0, c1)) }
    }

    pub fn or_with(&mut self, other: &Mask) {
        for (a, b) in self.data.iter_mut().zip(other.data.iter()) {
            *a |= *b;
        }
    }

    pub fn and_not(&self, other: &Mask) -> Mask {
        Grid {
            h: self.h,
            w: self.w,
            data: self.data.iter().zip(other.data.iter()).map(|(a, b)| *a && !*b).collect(),
        }
    }

    pub fn and(&self, other: &Mask) -> Mask {
        Grid {
            h: self.h,
            w: self.w,
            data: self.data.iter().zip(other.data.iter()).map(|(a, b)| *a && *b).collect(),
        }
    }

    pub fn or(&self, other: &Mask) -> Mask {
        Grid {
            h: self.h,
            w: self.w,
            data: self.data.iter().zip(other.data.iter()).map(|(a, b)| *a || *b).collect(),
        }
    }

    pub fn not(&self) -> Mask {
        Grid { h: self.h, w: self.w, data: self.data.iter().map(|a| !*a).collect() }
    }

    /// Crop to a half-open box and pad with `pad` false pixels on every side.
    pub fn crop_pad(&self, r0: usize, r1: usize, c0: usize, c1: usize, pad: usize) -> Mask {
        let h = r1 - r0 + 2 * pad;
        let w = c1 - c0 + 2 * pad;
        let mut out = Grid::filled(h, w, false);
        for r in r0..r1 {
            for c in c0..c1 {
                if self.data[r * self.w + c] {
                    out.data[(r - r0 + pad) * w + (c - c0 + pad)] = true;
                }
            }
        }
        out
    }
}

/// Multi-channel image stored channel-last, matching the numpy layout the
/// Python pipeline uses for `features` (H, W, 4) and `rgb` (H, W, 3).
#[derive(Clone, Debug)]
pub struct Image {
    pub h: usize,
    pub w: usize,
    pub c: usize,
    pub data: Vec<f64>,
}

impl Image {
    pub fn new(h: usize, w: usize, c: usize) -> Self {
        Image { h, w, c, data: vec![0.0; h * w * c] }
    }

    #[inline]
    pub fn px(&self, i: usize) -> &[f64] {
        &self.data[i * self.c..(i + 1) * self.c]
    }

    #[inline]
    pub fn px_mut(&mut self, i: usize) -> &mut [f64] {
        let c = self.c;
        &mut self.data[i * c..(i + 1) * c]
    }

    #[inline]
    pub fn at(&self, r: usize, c: usize) -> &[f64] {
        self.px(r * self.w + c)
    }

    pub fn channel(&self, ch: usize) -> Grid<f64> {
        Grid {
            h: self.h,
            w: self.w,
            data: (0..self.h * self.w).map(|i| self.data[i * self.c + ch]).collect(),
        }
    }

    pub fn set_channel(&mut self, ch: usize, g: &Grid<f64>) {
        for i in 0..self.h * self.w {
            self.data[i * self.c + ch] = g.data[i];
        }
    }

    pub fn len(&self) -> usize {
        self.h * self.w
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}
