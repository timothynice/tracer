mod common;
use common::sha256_hex;
use studi0trace_core::resample::{premultiply, resize_rgba, unpremultiply, Filter};

/// `splitmix_bytes` of `tools/export_core_fixtures.py`: little-endian bytes of splitmix64's
/// outputs for `seed`, the same stream on both sides.
fn splitmix_bytes(seed: u64, n: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(n + 8);
    for i in 1..=n.div_ceil(8) as u64 {
        let mut z = seed.wrapping_add(i.wrapping_mul(0x9E37_79B9_7F4A_7C15));
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        out.extend_from_slice(&(z ^ (z >> 31)).to_le_bytes());
    }
    out.truncate(n);
    out
}

fn image(seed: u64, w: usize, h: usize, kind: &str) -> Vec<u8> {
    let mut img = splitmix_bytes(seed, w * h * 4);
    for p in img.chunks_exact_mut(4) {
        let a = p[3];
        p[3] = match kind {
            "mixed" => match a % 4 {
                0 => 0,
                1 => 255,
                _ => a,
            },
            "opaque" => 255,
            "clear" => 0,
            _ => panic!("{kind}"),
        };
    }
    img
}

#[test]
fn resizes_match_pillow_byte_for_byte() {
    let fixture = common::fixture_json("resample.json");
    let cases = fixture["cases"].as_array().unwrap();
    let (mut nearest, mut lanczos) = (0, 0);
    for c in cases {
        let spec = c["case"].as_str().unwrap();
        let f: Vec<&str> = spec.split(' ').collect();
        let n = |i: usize| f[i].parse::<usize>().unwrap();
        let (seed, w, h, ow, oh) = (n(0) as u64, n(1), n(2), n(3), n(4));
        let filter = match f[5] {
            "nearest" => Filter::Nearest,
            "lanczos" => Filter::Lanczos,
            other => panic!("{other}"),
        };
        (nearest, lanczos) = (nearest + (filter == Filter::Nearest) as usize, lanczos + (filter == Filter::Lanczos) as usize);
        let got = resize_rgba(&image(seed, w, h, f[6]), w as u32, h as u32, ow as u32, oh as u32, filter).unwrap();
        assert_eq!(got.len(), ow * oh * 4, "{spec}");
        assert!(sha256_hex(&got) == c["sha256"].as_str().unwrap(), "{spec}: not Pillow's bytes");
    }
    // The cases that name the off-by-a-pixel resizes are in the list, for both filters.
    assert!(cases.iter().any(|c| c["case"].as_str().unwrap().contains(" 37 23 36 23 lanczos ")));
    assert!(nearest >= 150 && lanczos >= 150, "{nearest} nearest and {lanczos} lanczos cases");
}

#[test]
fn the_rgba_to_premultiplied_conversions_match_pillow_on_every_pair() {
    let fixture = common::fixture_json("resample.json");
    // A 256 x 256 image: columns are a colour (and two channels derived from it), rows an
    // alpha. The colour can be above its alpha, which a premultiplied image never holds
    // but a Lanczos pass can leave.
    let grid: Vec<u8> = (0..256usize)
        .flat_map(|a| (0..256usize).flat_map(move |c| [c as u8, ((c * 7 + 3) % 256) as u8, (255 - c) as u8, a as u8]))
        .collect();
    let pre = premultiply(&grid);
    assert!(sha256_hex(&pre) == fixture["premultiplied"].as_str().unwrap(), "premultiply");
    let mut back = grid.clone();
    unpremultiply(&mut back);
    assert!(sha256_hex(&back) == fixture["unpremultiplied"].as_str().unwrap(), "unpremultiply");
}

#[test]
fn an_unchanged_size_is_a_copy_and_a_bad_request_is_an_error() {
    let img = image(7, 5, 3, "mixed");
    for filter in [Filter::Nearest, Filter::Lanczos] {
        assert!(resize_rgba(&img, 5, 3, 5, 3, filter).unwrap() == img);
        for (w, h, ow, oh) in [(0, 3, 5, 3), (5, 0, 5, 3), (5, 3, 0, 3), (5, 3, 5, 0)] {
            assert!(resize_rgba(&img, w, h, ow, oh, filter).is_err(), "{w}x{h} -> {ow}x{oh}");
        }
        assert!(resize_rgba(&img[..img.len() - 1], 5, 3, 6, 3, filter).is_err(), "a short buffer");
        assert!(resize_rgba(&img, 5, 4, 6, 3, filter).is_err(), "a buffer of another size");
    }
}

#[test]
fn an_output_over_the_render_cap_is_refused_before_anything_is_allocated() {
    // `resize_rgba` is public and has no other cap: 20 000 x 20 000 is 1.6 GB of pixels
    let one = [10u8, 20, 30, 255];
    for filter in [Filter::Nearest, Filter::Lanczos] {
        for (w, h) in [(20_000, 20_000), (1 << 28, 2), (u32::MAX, u32::MAX), (2, 1 << 28)] {
            let e = resize_rgba(&one, 1, 1, w, h, filter).unwrap_err();
            // the cap, or (where the byte count itself overflows a usize) the older overflow check
            assert!(e.contains("pixels allowed") || e.contains("too large"), "{w}x{h}: {e}");
            assert!((w, h) == (u32::MAX, u32::MAX) || e.contains("pixels allowed"), "{w}x{h}: the cap's words: {e}");
        }
        // at the cap it is allowed (and not allocated here: a thin strip of it)
        assert_eq!(resize_rgba(&one, 1, 1, 1 << 14, 1, filter).map(|v| v.len()), Ok((1 << 14) * 4));
    }
}
