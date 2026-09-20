//! `skimage.color.rgb2lab` for sRGB, D65, 2-degree observer.
//!
//! The dtype path matters. The Python hands `rgb2lab` a float32 array, so
//! skimage does the companding and the XYZ matrix in float32 and only widens to
//! float64 at `arr / xyz_ref_white` (the white point is a float64 array). The
//! partition's ridge test compares a pixel's gradient with an interpolated
//! neighbour's, and on a flat tile those agree only when both sides round
//! identically — so this reproduces the widths rather than computing throughout
//! in double.

/// sRGB (0..255, as float32) to CIE L*a*b*.
pub fn rgb2lab(r: f64, g: f64, b: f64) -> [f64; 3] {
    let compand = |c: f64| -> f32 {
        let c = (c as f32) / 255.0f32;
        if c > 0.04045f32 {
            ((c + 0.055f32) / 1.055f32).powf(2.4f32)
        } else {
            c / 12.92f32
        }
    };
    let (r, g, b) = (compand(r), compand(g), compand(b));
    // skimage's xyz_from_rgb, cast to the array dtype before the matmul
    let x = 0.412_453f32 * r + 0.357_580f32 * g + 0.180_423f32 * b;
    let y = 0.212_671f32 * r + 0.715_160f32 * g + 0.072_169f32 * b;
    let z = 0.019_334f32 * r + 0.119_193f32 * g + 0.950_227f32 * b;

    // from here the white point is float64, so numpy promotes and so do we
    let f = |t: f64| -> f64 {
        if t > 0.008_856 {
            t.cbrt()
        } else {
            7.787 * t + 16.0 / 116.0
        }
    };
    let fx = f(x as f64 / 0.95047);
    let fy = f(y as f64 / 1.0);
    let fz = f(z as f64 / 1.08883);
    [116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)]
}
