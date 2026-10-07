//! The pure arithmetic of a redraw: the colour the source is padded with, the padded canvas, the size the model is
//! asked for, where the original's framing sits in the reply, and the size the redraw is given.

use super::{Model, REQUEST_SIDE};

/// `gpt-image-2`: the widest a request may be (3:1, and 1:3 the other way), its largest size and its pixel floor.
pub const MAX_ASPECT: u32 = 3;
pub const MAX_REQUEST: (u32, u32) = (3840, 2160);
pub const MIN_PIXELS: u64 = 655_360;
/// `gpt-image-1.5`'s sizes, the only ones it takes.
pub const FIXED_SIZES: [(u32, u32); 3] = [(1024, 1024), (1536, 1024), (1024, 1536)];

/// The padded canvas (`width` x `height`) and where the source's top-left corner sits in it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Pad {
    pub left: u32,
    pub top: u32,
    pub width: u32,
    pub height: u32,
}

/// A rectangle of pixels.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Rect {
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
}

/// What a source is sent as: its padding and the size the model is asked for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Plan {
    pub pad: Pad,
    pub request: (u32, u32),
}

/// The median, channel by channel, of the outermost one-pixel frame of an RGB image (each pixel once; for a
/// one-pixel image, that pixel).
pub fn border_colour(rgb: &[u8], w: u32, h: u32) -> [u8; 3] {
    let (w, h) = (w as usize, h as usize);
    let mut at = Vec::with_capacity(2 * (w + h));
    for x in 0..w {
        at.push(x);
        if h > 1 {
            at.push((h - 1) * w + x);
        }
    }
    for y in 1..h.saturating_sub(1) {
        at.push(y * w);
        if w > 1 {
            at.push(y * w + w - 1);
        }
    }
    let mut out = [0u8; 3];
    for (c, slot) in out.iter_mut().enumerate() {
        let mut v: Vec<u8> = at.iter().map(|&i| rgb[i * 3 + c]).collect();
        v.sort_unstable();
        *slot = v[v.len() / 2];
    }
    out
}

/// The canvas a `w` x `h` source is padded to so it has the aspect `p:q`: taller when it is wider than that,
/// wider when narrower, as it is when it has it. Symmetric; the odd pixel goes below or to the right.
pub fn pad_to(w: u32, h: u32, p: u32, q: u32) -> Pad {
    let (w64, h64, p64, q64) = (u64::from(w), u64::from(h), u64::from(p), u64::from(q));
    if w64 * q64 > h64 * p64 {
        let height = (w64 * q64).div_ceil(p64) as u32;
        Pad { left: 0, top: (height - h) / 2, width: w, height }
    } else if w64 * q64 < h64 * p64 {
        let width = (h64 * p64).div_ceil(q64) as u32;
        Pad { left: (width - w) / 2, top: 0, width, height: h }
    } else {
        Pad { left: 0, top: 0, width: w, height: h }
    }
}

fn round16(x: f64) -> u32 {
    ((x / 16.0).round() as u32).max(1) * 16
}

fn ceil16(x: f64) -> u32 {
    ((x / 16.0).ceil() as u32).max(1) * 16
}

/// The size asked for a padded canvas of `pw` x `ph`: its aspect with the long side `long`, both sides multiples
/// of 16, raised if needed to [`MIN_PIXELS`].
pub fn fit_request(pw: u32, ph: u32, long: u32) -> (u32, u32) {
    let (mut rw, mut rh) = if pw >= ph {
        (long, round16(f64::from(long) * f64::from(ph) / f64::from(pw)))
    } else {
        (round16(f64::from(long) * f64::from(pw) / f64::from(ph)), long)
    };
    while u64::from(rw) * u64::from(rh) < MIN_PIXELS {
        let k = (MIN_PIXELS as f64 / (u64::from(rw) * u64::from(rh)) as f64).sqrt();
        (rw, rh) = (ceil16(f64::from(rw) * k), ceil16(f64::from(rh) * k));
    }
    (rw, rh)
}

/// The one of [`FIXED_SIZES`] whose aspect is closest to `w` x `h`'s, by the ratio of the two (the first on a tie).
pub fn closest_fixed(w: u32, h: u32) -> (u32, u32) {
    let a = (f64::from(w) / f64::from(h)).ln();
    let distance = |s: &(u32, u32)| (a - (f64::from(s.0) / f64::from(s.1)).ln()).abs();
    *FIXED_SIZES.iter().min_by(|x, y| distance(x).total_cmp(&distance(y))).expect("three sizes")
}

/// How a `w` x `h` source is sent to `model`.
pub fn plan(model: Model, w: u32, h: u32) -> Plan {
    match model {
        Model::GptImage2 => {
            let pad = if w > MAX_ASPECT * h {
                pad_to(w, h, MAX_ASPECT, 1)
            } else if h > MAX_ASPECT * w {
                pad_to(w, h, 1, MAX_ASPECT)
            } else {
                pad_to(w, h, w, h)
            };
            Plan { pad, request: fit_request(pad.width, pad.height, REQUEST_SIDE) }
        }
        Model::GptImage15 => {
            let size = closest_fixed(w, h);
            Plan { pad: pad_to(w, h, size.0, size.1), request: size }
        }
    }
}

/// An RGB source of `w` x `h` on the padded canvas, the rest painted `colour`.
pub fn pad_rgb(rgb: &[u8], w: u32, h: u32, pad: Pad, colour: [u8; 3]) -> Vec<u8> {
    let (pw, ph) = (pad.width as usize, pad.height as usize);
    let mut out: Vec<u8> = colour.iter().copied().cycle().take(pw * ph * 3).collect();
    let row = w as usize * 3;
    for y in 0..h as usize {
        let at = ((y + pad.top as usize) * pw + pad.left as usize) * 3;
        out[at..at + row].copy_from_slice(&rgb[y * row..(y + 1) * row]);
    }
    out
}

/// Where the `w` x `h` source of `pad` sits in a reply of `reply_w` x `reply_h`: the request-to-padded scale of
/// each axis (read from the reply's own size, in case it is not the one asked), with the padding cropped away.
pub fn crop_back(pad: Pad, w: u32, h: u32, reply_w: u32, reply_h: u32) -> Rect {
    let span = |start: u32, len: u32, scale: f64, max: u32| {
        let a = ((f64::from(start) * scale).round() as u32).min(max - 1);
        let b = ((f64::from(start + len) * scale).round() as u32).clamp(a + 1, max);
        (a, b - a)
    };
    let (x, width) = span(pad.left, w, f64::from(reply_w) / f64::from(pad.width), reply_w);
    let (y, height) = span(pad.top, h, f64::from(reply_h) / f64::from(pad.height), reply_h);
    Rect { x, y, width, height }
}

/// The pixels of `rect` of an RGBA image `w` pixels wide.
pub fn crop_rgba(rgba: &[u8], w: u32, rect: Rect) -> Vec<u8> {
    let (stride, row) = (w as usize * 4, rect.width as usize * 4);
    let mut out = Vec::with_capacity(row * rect.height as usize);
    for y in rect.y as usize..(rect.y + rect.height) as usize {
        let at = y * stride + rect.x as usize * 4;
        out.extend_from_slice(&rgba[at..at + row]);
    }
    out
}

/// The redraw's size: the original's aspect at the largest size within the app's side cap (2048), which is never
/// smaller than the original (the intake caps it there too).
pub fn final_size(w: u32, h: u32) -> (u32, u32) {
    let side = crate::intake::max_side();
    let other = |a: u32, b: u32| ((f64::from(side) * f64::from(a) / f64::from(b)).round() as u32).clamp(1, side);
    if w >= h {
        (side, other(h, w))
    } else {
        (other(w, h), side)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::redraw::{Model, REQUEST_SIDE};

    /// `plan` and the two things every plan must hold: the source inside the canvas, the padding symmetric.
    fn props(model: Model, w: u32, h: u32) -> Plan {
        let p = plan(model, w, h);
        assert!(p.pad.left + w <= p.pad.width && p.pad.top + h <= p.pad.height, "{w}x{h}: {p:?}");
        assert!(p.pad.width - w - 2 * p.pad.left <= 1 && p.pad.height - h - 2 * p.pad.top <= 1, "{w}x{h}: {p:?}");
        p
    }

    #[test]
    fn a_wide_source_is_padded_to_three_to_one() {
        let p = props(Model::GptImage2, 392, 100);
        assert_eq!(p.pad, Pad { left: 0, top: 15, width: 392, height: 131 });
        assert_eq!(p.request, (2048, 688));
    }

    #[test]
    fn a_tall_source_is_padded_to_one_to_three() {
        let p = props(Model::GptImage2, 100, 500);
        assert_eq!(p.pad, Pad { left: 33, top: 0, width: 167, height: 500 });
        assert_eq!(p.request, (688, 2048));
    }

    #[test]
    fn a_square_and_an_exact_one_to_three_are_not_padded() {
        assert_eq!(props(Model::GptImage2, 300, 300), Plan { pad: Pad { left: 0, top: 0, width: 300, height: 300 }, request: (2048, 2048) });
        assert_eq!(props(Model::GptImage2, 100, 300), Plan { pad: Pad { left: 0, top: 0, width: 100, height: 300 }, request: (688, 2048) });
        assert_eq!(props(Model::GptImage2, 300, 100).request, (2048, 688));
    }

    #[test]
    fn tiny_and_full_size_sources() {
        assert_eq!(props(Model::GptImage2, 1, 1).request, (2048, 2048));
        assert_eq!(props(Model::GptImage2, 4, 4).request, (2048, 2048));
        assert_eq!(props(Model::GptImage2, 2048, 2048).request, (2048, 2048));
        assert_eq!(props(Model::GptImage2, 2048, 1024).request, (2048, 1024));
        let p = props(Model::GptImage2, 2, 7);
        assert_eq!(p.pad, Pad { left: 0, top: 0, width: 3, height: 7 });
        assert_eq!(p.request, (880, 2048));
    }

    #[test]
    fn every_request_is_a_size_gpt_image_2_takes() {
        let sides = [1u32, 2, 3, 7, 16, 100, 333, 599, 600, 1000, 1999, 2048];
        for &w in &sides {
            for &h in &sides {
                let p = props(Model::GptImage2, w, h);
                let (rw, rh) = p.request;
                assert!(rw % 16 == 0 && rh % 16 == 0, "{w}x{h}: {rw}x{rh}");
                assert_eq!(rw.max(rh), REQUEST_SIDE, "{w}x{h}: {rw}x{rh}");
                assert!(rw <= 3 * rh && rh <= 3 * rw, "{w}x{h}: {rw}x{rh}");
                assert!(rw.max(rh) <= MAX_REQUEST.0 && rw.min(rh) <= MAX_REQUEST.1, "{w}x{h}: {rw}x{rh}");
                assert!(u64::from(rw) * u64::from(rh) >= MIN_PIXELS, "{w}x{h}: {rw}x{rh}");
                assert!(p.pad.width <= MAX_ASPECT * p.pad.height && p.pad.height <= MAX_ASPECT * p.pad.width, "{w}x{h}: {p:?}");
            }
        }
    }

    #[test]
    fn the_pixel_minimum_raises_a_small_request() {
        assert_eq!(fit_request(1, 1, 512), (816, 816));
        assert_eq!(fit_request(3, 1, 512), (1392, 480));
        let (w, h) = fit_request(3, 1, 512);
        assert!(u64::from(w) * u64::from(h) >= MIN_PIXELS);
        // above it, nothing moves
        assert_eq!(fit_request(300, 300, REQUEST_SIDE), (2048, 2048));
    }

    #[test]
    fn gpt_image_1_5_takes_the_closest_fixed_size_and_pads_to_it() {
        let p = props(Model::GptImage15, 100, 500);
        assert_eq!(p.request, (1024, 1536));
        assert_eq!(p.pad, Pad { left: 117, top: 0, width: 334, height: 500 });
        let p = props(Model::GptImage15, 392, 100);
        assert_eq!(p.request, (1536, 1024));
        assert_eq!(p.pad, Pad { left: 0, top: 81, width: 392, height: 262 });
        assert_eq!(props(Model::GptImage15, 300, 300), Plan { pad: Pad { left: 0, top: 0, width: 300, height: 300 }, request: (1024, 1024) });
        assert_eq!(props(Model::GptImage15, 130, 100).request, (1536, 1024));
        assert_eq!(FIXED_SIZES, [(1024, 1024), (1536, 1024), (1024, 1536)]);
    }

    #[test]
    fn the_border_colour_is_the_median_of_the_outer_frame() {
        // 4 x 3: a white frame with one red pixel on it, black inside
        let (w, h) = (4u32, 3u32);
        let mut rgb = vec![255u8; (w * h * 3) as usize];
        let mut set = |x: u32, y: u32, c: [u8; 3]| {
            let i = ((y * w + x) * 3) as usize;
            rgb[i..i + 3].copy_from_slice(&c);
        };
        set(1, 1, [0, 0, 0]);
        set(2, 1, [0, 0, 0]);
        set(3, 0, [255, 0, 0]);
        assert_eq!(border_colour(&rgb, w, h), [255, 255, 255]);
        assert_eq!(border_colour(&[10, 20, 30], 1, 1), [10, 20, 30]);
    }

    #[test]
    fn padding_paints_the_border_colour_around_the_source() {
        let pad = Pad { left: 1, top: 0, width: 3, height: 1 };
        assert_eq!(pad_rgb(&[9, 9, 9], 1, 1, pad, [1, 2, 3]), vec![1, 2, 3, 9, 9, 9, 1, 2, 3]);
        let pad = Pad { left: 0, top: 1, width: 1, height: 2 };
        assert_eq!(pad_rgb(&[9, 9, 9], 1, 1, pad, [1, 2, 3]), vec![1, 2, 3, 9, 9, 9]);
    }

    #[test]
    fn the_crop_back_finds_the_original_framing_in_the_reply() {
        let p = plan(Model::GptImage2, 392, 100);
        assert_eq!(crop_back(p.pad, 392, 100, 2048, 688), Rect { x: 0, y: 79, width: 2048, height: 525 });
        let p = plan(Model::GptImage2, 100, 500);
        assert_eq!(crop_back(p.pad, 100, 500, 688, 2048), Rect { x: 136, y: 0, width: 412, height: 2048 });
        // unpadded: the whole reply, at whatever size it came
        let p = plan(Model::GptImage2, 300, 300);
        assert_eq!(crop_back(p.pad, 300, 300, 2048, 2048), Rect { x: 0, y: 0, width: 2048, height: 2048 });
        assert_eq!(crop_back(p.pad, 300, 300, 1024, 1024), Rect { x: 0, y: 0, width: 1024, height: 1024 });
    }

    #[test]
    fn crop_rgba_takes_the_rectangle() {
        let rgba: Vec<u8> = (0..4 * 3 * 4).map(|v| v as u8).collect(); // 4 x 3
        let out = crop_rgba(&rgba, 4, Rect { x: 1, y: 1, width: 2, height: 2 });
        assert_eq!(out, [&rgba[20..28], &rgba[36..44]].concat());
    }

    #[test]
    fn the_redraw_is_the_largest_size_within_2048_at_the_original_aspect() {
        assert_eq!(final_size(392, 100), (2048, 522));
        assert_eq!(final_size(100, 500), (410, 2048));
        assert_eq!(final_size(300, 300), (2048, 2048));
        assert_eq!(final_size(2048, 1024), (2048, 1024));
        assert_eq!(final_size(4, 4), (2048, 2048));
        assert_eq!(final_size(2048, 1), (2048, 1));
        for (w, h) in [(392, 100), (100, 500), (7, 3), (2048, 2048), (1999, 3)] {
            let (fw, fh) = final_size(w, h);
            assert!(fw >= w && fh >= h && fw.max(fh) == 2048, "{w}x{h}: {fw}x{fh}");
        }
    }
}
