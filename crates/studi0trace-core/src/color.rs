//! Colour fidelity: compositing on white, sRGB -> CIELAB (D65, 2 degrees) and CIEDE2000,
//! each as the Python computes it (`studi0trace.imaging.quality`, which leans on
//! scikit-image's `rgb2lab` and `deltaE_ciede2000` with kL = kC = kH = 1).
//!
//! The arithmetic follows skimage 0.26's `colorconv.py` and `delta_e.py` operation by
//! operation, in the same order, so the two agree to the last few bits rather than merely
//! to the tolerance the tests allow. Where numpy does something the obvious Rust would not
//! (`x ** 7` is libm's `pow`, not a chain of multiplications; `np.maximum` propagates NaN)
//! the Rust does what numpy does, and says so.
use std::f64::consts::PI;
use std::sync::OnceLock;

/// Alpha-composite RGBA over white (`quality.to_rgb_on_white`): float32 arithmetic, `+ 0.5`,
/// clip to 0..=255, then a truncating cast. Each step rounds to float32 the way numpy's
/// separate multiply and add ufuncs do; nothing here is fused. A trailing partial pixel
/// (a length that is not a multiple of four) is ignored.
pub fn rgb_on_white(rgba: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(rgba.len() / 4 * 3);
    let (pixels, _partial_pixel) = rgba.as_chunks::<4>();
    for &[r, g, b, a] in pixels {
        let alpha = a as f32 / 255.0;
        let under = 255.0 * (1.0 - alpha);
        for c in [r, g, b] {
            out.push(((c as f32 * alpha + under) + 0.5).clamp(0.0, 255.0) as u8);
        }
    }
    out
}

/// The sRGB transfer function's inverse, for a channel already scaled to 0..=1.
fn linear(c: f64) -> f64 {
    if c > 0.04045 {
        ((c + 0.055) / 1.055).powf(2.4)
    } else {
        c / 12.92
    }
}

/// `linear(v / 255)` for every byte value, built once: the same function on the same inputs,
/// so each pixel gets the very bits a per-pixel call would.
fn linear_table() -> &'static [f64; 256] {
    static TABLE: OnceLock<[f64; 256]> = OnceLock::new();
    TABLE.get_or_init(|| std::array::from_fn(|v| linear(v as f64 / 255.0)))
}

/// skimage's `xyz_from_rgb` (the sRGB matrix, D65), rows are X, Y, Z.
const XYZ_FROM_RGB: [[f64; 3]; 3] = [
    [0.412453, 0.357580, 0.180423],
    [0.212671, 0.715160, 0.072169],
    [0.019334, 0.119193, 0.950227],
];

/// skimage's D65 / 2 degree white (`_illuminants["D65"]["2"]`), which is not the matrix's own
/// row sums.
const WHITE: [f64; 3] = [0.95047, 1.0, 1.08883];

/// The cube-root companding of `xyz2lab`, with its linear segment below 0.008856.
fn lab_f(t: f64) -> f64 {
    if t > 0.008856 {
        t.cbrt()
    } else {
        7.787 * t + 16.0 / 116.0
    }
}

/// sRGB (RGB8, three bytes a pixel) to CIELAB, D65 and the 2 degree observer, as
/// `rgb2lab(rgb / 255.0)`. A trailing partial pixel is ignored.
pub fn lab(rgb: &[u8]) -> Vec<[f64; 3]> {
    let table = linear_table();
    let (pixels, _partial_pixel) = rgb.as_chunks::<3>();
    pixels
        .iter()
        .map(|&[r, g, b]| {
            let v = [table[r as usize], table[g as usize], table[b as usize]];
            // `arr @ xyz_from_rgb.T`, then `arr / white`.
            let f = std::array::from_fn::<f64, 3, _>(|r| {
                let m = &XYZ_FROM_RGB[r];
                lab_f((v[0] * m[0] + v[1] * m[1] + v[2] * m[2]) / WHITE[r])
            });
            [116.0 * f[1] - 16.0, 500.0 * (f[0] - f[1]), 200.0 * (f[1] - f[2])]
        })
        .collect()
}

/// `np.deg2rad` and `np.rad2deg` multiply by these, not by a division.
const RAD_PER_DEG: f64 = PI / 180.0;
const DEG_PER_RAD: f64 = 180.0 / PI;
/// `25 ** 7` in the Python is an exact integer.
const POW_25_7: f64 = 6_103_515_625.0;

/// skimage's `_cart2polar_2pi`: radius, and an angle in [0, 2*pi) rather than (-pi, pi].
/// (It adds `0` to a non-negative angle, which turns a `-0.0` into `+0.0`.)
fn polar_2pi(x: f64, y: f64) -> (f64, f64) {
    let t = y.atan2(x);
    (x.hypot(y), t + if t < 0.0 { 2.0 * PI } else { 0.0 })
}

/// CIEDE2000 between two Lab colours, `deltaE_ciede2000(a, b)` with kL = kC = kH = 1
/// (multiplying by those 1s changes no bit, so they are not written out).
pub fn ciede2000(a: [f64; 3], b: [f64; 3]) -> f64 {
    let ([l1, a1, b1], [l2, a2, b2]) = (a, b);

    // Distort `a` by the mean chroma, and take polar coordinates from the distorted pair.
    let cbar = 0.5 * (a1.hypot(b1) + a2.hypot(b2));
    let c7 = cbar.powf(7.0);
    let g = 0.5 * (1.0 - (c7 / (c7 + POW_25_7)).sqrt());
    let scale = 1.0 + g;
    let (c1, h1) = polar_2pi(a1 * scale, b1);
    let (c2, h2) = polar_2pi(a2 * scale, b2);

    // Lightness.
    let lbar = 0.5 * (l1 + l2);
    let tmp = (lbar - 50.0) * (lbar - 50.0);
    let sl = 1.0 + 0.015 * tmp / (20.0 + tmp).sqrt();
    let l_term = (l2 - l1) / sl;

    // Chroma.
    let cbar = 0.5 * (c1 + c2);
    let sc = 1.0 + 0.045 * cbar;
    let c_term = (c2 - c1) / sc;

    // Hue.
    let h_diff = h2 - h1;
    let h_sum = h1 + h2;
    let cc = c1 * c2;

    let mut dh = h_diff;
    if h_diff > PI {
        dh -= 2.0 * PI;
    }
    if h_diff < -PI {
        dh += 2.0 * PI;
    }
    if cc == 0.0 {
        dh = 0.0; // if r == 0, dtheta == 0
    }
    let dh_term = 2.0 * cc.sqrt() * (dh / 2.0).sin();

    let mut hbar = h_sum;
    if cc != 0.0 && h_diff.abs() > PI {
        if h_sum < 2.0 * PI {
            hbar += 2.0 * PI;
        } else {
            hbar -= 2.0 * PI;
        }
    }
    if cc == 0.0 {
        hbar *= 2.0;
    }
    hbar *= 0.5;

    let t = 1.0 - 0.17 * (hbar - 30.0 * RAD_PER_DEG).cos()
        + 0.24 * (2.0 * hbar).cos()
        + 0.32 * (3.0 * hbar + 6.0 * RAD_PER_DEG).cos()
        - 0.20 * (4.0 * hbar - 63.0 * RAD_PER_DEG).cos();
    let sh = 1.0 + 0.015 * cbar * t;
    let h_term = dh_term / sh;

    // Hue rotation.
    let c7 = cbar.powf(7.0);
    let rc = 2.0 * (c7 / (c7 + POW_25_7)).sqrt();
    let z = (hbar * DEG_PER_RAD - 275.0) / 25.0;
    let dtheta = 30.0 * RAD_PER_DEG * (-(z * z)).exp();
    let r_term = -(2.0 * dtheta).sin() * rc * c_term * h_term;

    // `dE2 = L**2; dE2 += C**2; dE2 += H**2; dE2 += R`, then `sqrt(np.maximum(dE2, 0))`.
    let de2 = l_term * l_term + c_term * c_term + h_term * h_term + r_term;
    // np.maximum keeps a NaN where f64::max would drop it; a NaN stays a NaN out.
    (if de2 < 0.0 { 0.0 } else { de2 }).sqrt()
}

/// CIEDE2000 per pixel of two equally long RGB8 images (`quality.delta_e_map`), in raster
/// order. Panics if the images are not the same size, where numpy would raise.
pub fn delta_e_map(a_rgb: &[u8], b_rgb: &[u8]) -> Vec<f64> {
    assert_eq!(a_rgb.len(), b_rgb.len(), "delta_e_map: the two images must be the same size");
    ciede2000_map(&lab(a_rgb), &lab(b_rgb))
}

fn ciede2000_map(a: &[[f64; 3]], b: &[[f64; 3]]) -> Vec<f64> {
    assert_eq!(a.len(), b.len(), "delta_e: the two images must be the same size");
    a.iter().zip(b).map(|(x, y)| ciede2000(*x, *y)).collect()
}

/// (mean, 95th percentile) of the per-pixel CIEDE2000 between two Lab images: what
/// `Reference.fidelity` computes from the source's cached Lab and each candidate's. Both
/// are NaN for empty images. Panics if the images are not the same size.
pub fn delta_e_lab(a: &[[f64; 3]], b: &[[f64; 3]]) -> (f64, f64) {
    let de = ciede2000_map(a, b);
    // A plain left-to-right sum: numpy's pairwise sum differs from it by about 1e-13 relative
    // on a 512 x 512 image, far below anything the scorecard compares at.
    (de.iter().sum::<f64>() / de.len() as f64, percentile(&de, 95.0))
}

/// (mean, 95th percentile) of the per-pixel CIEDE2000 between two RGB8 images
/// (`quality.delta_e`). Both are NaN for empty images. Panics on unequal sizes.
pub fn delta_e(a_rgb: &[u8], b_rgb: &[u8]) -> (f64, f64) {
    assert_eq!(a_rgb.len(), b_rgb.len(), "delta_e: the two images must be the same size");
    delta_e_lab(&lab(a_rgb), &lab(b_rgb))
}

/// `np.percentile(values, q)` with its default linear method, on an unsorted slice.
///
/// Returns NaN for an empty slice (numpy raises there, and a library call that can be handed
/// an empty image should not panic: NaN is also what `mean` of nothing is) and for a NaN in
/// `values` or in `q`, as numpy does for a NaN in the data. A `q` outside 0..=100, which
/// numpy rejects, gives the smallest or the largest value.
pub fn percentile(values: &[f64], q: f64) -> f64 {
    if values.is_empty() || q.is_nan() || values.iter().any(|v| v.is_nan()) {
        return f64::NAN;
    }
    let mut v = values.to_vec();
    v.sort_by(f64::total_cmp);
    let last = v.len() - 1;
    // numpy: virtual index `(n - 1) * (q / 100)`, weight `virtual - floor(virtual)`.
    let pos = last as f64 * (q / 100.0);
    if pos >= last as f64 {
        return v[last];
    }
    if pos < 0.0 {
        return v[0];
    }
    let lo = pos.floor() as usize;
    let gamma = pos - lo as f64;
    let (a, b) = (v[lo], v[lo + 1]);
    let diff = b - a;
    // numpy's `_lerp`: from the near end of the interval, whichever that is.
    if gamma >= 0.5 {
        b - diff * (1.0 - gamma)
    } else {
        a + diff * gamma
    }
}
