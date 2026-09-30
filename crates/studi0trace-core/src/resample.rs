//! Pillow's `Image.resize` for an RGBA8 image, nearest or Lanczos, byte for byte (Pillow 12.x).
//!
//! It exists for one caller: [`crate::render::render`], which does what `quality.render` does
//! when resvg comes out a pixel off the size it was asked for or at another aspect than the
//! box, and forces the size with Pillow. The off-by-a-row case is rare (it starts at 16.8 MP at
//! 1x and 5.6 MP at 3x, see `render.rs`) but the Python handles it, so the core has to. It is
//! not a general resampler: one filter pair, RGBA8 only, no `box`, no `reducing_gap`.
//!
//! What is replicated, each part compared with Pillow 12.3 byte for byte: 346 cases in
//! `tests/resample.rs` (SHA-256 of Pillow's answers, in `resample.json`), and about 4700 more
//! in a scratch run while it was written:
//!
//! - **`Image.resize`** (`PIL/Image.py`): an unchanged size is a copy; for RGBA and any
//!   filter but nearest, the image is converted to premultiplied `RGBa`, resized and converted
//!   back; an image more than 100 times taller than wide that is getting shorter is resized
//!   vertically first, then horizontally (the other way round from the usual order, which
//!   matters to Lanczos's rounding).
//! - **Premultiply** (`rgba2rgbA`): `MULDIV255(c, a) = (t + (t >> 8)) >> 8` with
//!   `t = c * a + 128`. **Unpremultiply** (`rgbA2rgba`): `c` as it is when the alpha is 0 or
//!   255, otherwise `min(255, 255 * c / a)`, integer division. Both were compared on all
//!   256 x 256 (colour, alpha) pairs, including colours above their alpha.
//! - **Lanczos** (`libImaging/Resample.c`): support 3, `sinc(x) * sinc(x / 3)`, one
//!   horizontal pass into an 8-bit image and then one vertical pass, an axis whose size does
//!   not change skipped. Per output pixel the taps run from `(int)(center - support + 0.5)` to
//!   `(int)(center + support + 0.5)` clamped to the image, with `center = (i + 0.5) * scale` and
//!   `support = 3 * max(scale, 1)`; they are normalised by their sum in `f64`, rounded half
//!   away from zero to 22 fractional bits, summed in `i32` from a start of half (`1 << 21`) and
//!   shifted back, clipped to 0..=255. `f64::sin` is the platform libm's, as Pillow's is.
//! - **Nearest** (`Geometry.c`, the affine scale): the source column of output column `i` is
//!   `floor(x)` where `x` starts at `0.5 * scale` and has `scale = in / out` added after each
//!   column, in `f64`; it is accumulated, not computed as `(i + 0.5) * scale`, and the two
//!   differ at the exact integers, where the running sum is sometimes a hair below. Rows the
//!   same. Found by experiment: no closed form matched all 4761 pairs of (in, out) up to 69.
use std::f64::consts::PI;

/// The two filters `quality.render` uses: `NEAREST` for a crisp (id map) render, `LANCZOS`
/// for the rest.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Filter {
    Nearest,
    Lanczos,
}

/// `Image.fromarray(rgba).resize((out_width, out_height), filter)` for an RGBA8 image.
/// `Err` for a zero size or a buffer that is not `width * height * 4` bytes.
pub fn resize_rgba(src: &[u8], width: u32, height: u32, out_width: u32, out_height: u32, filter: Filter) -> Result<Vec<u8>, String> {
    let bytes = |w: u32, h: u32| (w as usize).checked_mul(h as usize).and_then(|n| n.checked_mul(4));
    if width == 0 || height == 0 || out_width == 0 || out_height == 0 {
        return Err("height and width must be > 0".into());
    }
    if bytes(width, height) != Some(src.len()) {
        return Err(format!("a {width}x{height} RGBA image is not {} bytes", src.len()));
    }
    bytes(out_width, out_height).ok_or("the resized image is too large")?;
    let (w, h, ow, oh) = (width as usize, height as usize, out_width as usize, out_height as usize);
    if (w, h) == (ow, oh) {
        return Ok(src.to_vec());
    }
    Ok(match filter {
        Filter::Nearest => nearest(src, w, h, ow, oh),
        Filter::Lanczos => {
            let mut img = premultiply(src);
            if h > w * 100 && oh < h {
                img = vertical(&img, w, h, oh);
                img = horizontal(&img, w, oh, ow);
            } else {
                img = horizontal(&img, w, h, ow);
                img = vertical(&img, ow, h, oh);
            }
            unpremultiply(&mut img);
            img
        }
    })
}

/// For each of `out` positions, the source index Pillow's nearest-neighbour scale picks.
fn nearest_indices(len: usize, out: usize) -> Vec<usize> {
    let scale = len as f64 / out as f64;
    let mut x = 0.0 + scale * 0.5;
    (0..out)
        .map(|_| {
            let i = (x as usize).min(len - 1);
            x += scale;
            i
        })
        .collect()
}

fn nearest(src: &[u8], w: usize, h: usize, ow: usize, oh: usize) -> Vec<u8> {
    let cols = nearest_indices(w, ow);
    let mut out = Vec::with_capacity(ow * oh * 4);
    for y in nearest_indices(h, oh) {
        let row = &src[y * w * 4..(y + 1) * w * 4];
        for &x in &cols {
            out.extend_from_slice(&row[x * 4..x * 4 + 4]);
        }
    }
    out
}

/// Pillow's `convert("RGBa")` from RGBA8: each colour times its alpha, `MULDIV255`.
pub fn premultiply(src: &[u8]) -> Vec<u8> {
    let mul = |c: u8, a: u8| {
        let t = u32::from(c) * u32::from(a) + 128;
        ((t + (t >> 8)) >> 8) as u8
    };
    src.chunks_exact(4).flat_map(|p| [mul(p[0], p[3]), mul(p[1], p[3]), mul(p[2], p[3]), p[3]]).collect()
}

/// Pillow's `convert("RGBA")` from RGBa, in place: a colour is divided by its alpha, unless
/// that is 0 or 255 and it is left as it is; a colour above its alpha clips to 255.
pub fn unpremultiply(img: &mut [u8]) {
    for p in img.chunks_exact_mut(4) {
        let a = u32::from(p[3]);
        if a != 0 && a != 255 {
            for c in &mut p[..3] {
                *c = (255 * u32::from(*c) / a).min(255) as u8;
            }
        }
    }
}

const PRECISION_BITS: u32 = 32 - 8 - 2;

fn sinc(x: f64) -> f64 {
    if x == 0.0 {
        1.0
    } else {
        let x = x * PI;
        x.sin() / x
    }
}

fn lanczos(x: f64) -> f64 {
    if (-3.0..3.0).contains(&x) {
        sinc(x) * sinc(x / 3.0)
    } else {
        0.0
    }
}

/// `precompute_coeffs` and `normalize_coeffs_8bpc` for the whole axis: per output position,
/// the first source index and its fixed-point taps.
fn coefficients(in_size: usize, out_size: usize) -> Vec<(usize, Vec<i32>)> {
    let scale = in_size as f64 / out_size as f64;
    let filterscale = scale.max(1.0);
    let support = 3.0 * filterscale;
    let ss = 1.0 / filterscale;
    (0..out_size)
        .map(|xx| {
            let center = 0.0 + (xx as f64 + 0.5) * scale;
            let xmin = ((center - support + 0.5) as i64).max(0) as usize;
            let xmax = (((center + support + 0.5) as i64).max(0) as usize).min(in_size);
            let mut taps: Vec<f64> = (0..xmax.saturating_sub(xmin)).map(|x| lanczos((x as f64 + xmin as f64 - center + 0.5) * ss)).collect();
            let total: f64 = taps.iter().sum();
            if total != 0.0 {
                taps.iter_mut().for_each(|k| *k /= total);
            }
            let fixed = |k: f64| {
                let scaled = k * f64::from(1u32 << PRECISION_BITS);
                (if k < 0.0 { -0.5 + scaled } else { 0.5 + scaled }) as i32
            };
            (xmin, taps.into_iter().map(fixed).collect())
        })
        .collect()
}

fn clip8(v: i32) -> u8 {
    (v >> PRECISION_BITS).clamp(0, 255) as u8
}

/// One horizontal pass over `h` rows of a `w`-wide image; a no-op copy when the width stays.
fn horizontal(img: &[u8], w: usize, h: usize, ow: usize) -> Vec<u8> {
    if w == ow {
        return img.to_vec();
    }
    let coeffs = coefficients(w, ow);
    let mut out = Vec::with_capacity(ow * h * 4);
    for row in img.chunks_exact(w * 4) {
        for (xmin, taps) in &coeffs {
            let mut acc = [1i32 << (PRECISION_BITS - 1); 4];
            for (x, &k) in taps.iter().enumerate() {
                let p = &row[(xmin + x) * 4..(xmin + x) * 4 + 4];
                for c in 0..4 {
                    acc[c] += i32::from(p[c]) * k;
                }
            }
            out.extend(acc.map(clip8));
        }
    }
    out
}

/// One vertical pass over a `w`-wide image of `h` rows; a no-op copy when the height stays.
fn vertical(img: &[u8], w: usize, h: usize, oh: usize) -> Vec<u8> {
    if h == oh {
        return img.to_vec();
    }
    let coeffs = coefficients(h, oh);
    let mut out = Vec::with_capacity(w * oh * 4);
    let mut acc = vec![0i32; w * 4];
    for (ymin, taps) in &coeffs {
        acc.fill(1 << (PRECISION_BITS - 1));
        for (y, &k) in taps.iter().enumerate() {
            let row = &img[(ymin + y) * w * 4..(ymin + y + 1) * w * 4];
            for (a, &v) in acc.iter_mut().zip(row) {
                *a += i32::from(v) * k;
            }
        }
        out.extend(acc.iter().map(|&a| clip8(a)));
    }
    out
}
