//! `intake::load` against what Pillow does with the same bytes
//! (`backend/studi0trace/imaging/intake.py`). Fixtures: `tools.export_core_fixtures --only intake`.
mod common;
use studi0trace_core::intake::{load, Image, Limits};

const BIG: usize = 1 << 30;

/// Sum and position-weighted sum of the RGBA bytes, as the exporter computes them.
fn checksums(img: &Image) -> (u64, u64) {
    let sum = img.rgba.iter().map(|b| *b as u64).sum();
    let weighted = img.rgba.iter().enumerate().map(|(i, b)| *b as u64 * (i as u64 % 251 + 1)).sum();
    (sum, weighted)
}

fn decode(file: &str) -> Image {
    load(&common::fixture_bytes(file), Limits::default()).unwrap_or_else(|e| panic!("{file}: {} ({})", e.code, e.message))
}

#[test]
fn lossless_formats_decode_exactly_as_pillow_does() {
    let g = common::fixture_json("intake.json");
    let mut checked = 0;
    for (name, case) in g.as_object().unwrap() {
        if case["lossless"].as_bool() != Some(true) {
            continue;
        }
        let img = decode(case["file"].as_str().unwrap());
        assert_eq!(img.format, case["format"].as_str().unwrap(), "{name}");
        assert_eq!((img.width as u64, img.height as u64), (case["width"].as_u64().unwrap(), case["height"].as_u64().unwrap()), "{name}");
        assert_eq!(img.rgba.len(), img.width as usize * img.height as usize * 4, "{name}");
        let (sum, weighted) = checksums(&img);
        assert_eq!(sum, case["rgba_sum"].as_u64().unwrap(), "{name}: pixels differ from Pillow's");
        assert_eq!(weighted, case["rgba_weighted"].as_u64().unwrap(), "{name}: pixels are in another order than Pillow's");
        checked += 1;
    }
    // png, gif, webp, bmp, three alpha files, palette / grey / grey+alpha PNGs, two animations
    assert_eq!(checked, 12, "a fixture went missing from intake.json");
}

#[test]
fn animated_files_give_their_first_frame() {
    // The exporter asserts each file has two frames that differ, and records Pillow's first.
    let g = common::fixture_json("intake.json");
    for name in ["anim_gif", "anim_webp"] {
        let img = decode(g[name]["file"].as_str().unwrap());
        assert_eq!((img.width, img.height), (32, 32), "{name}");
        assert_eq!(checksums(&img).0, g[name]["rgba_sum"].as_u64().unwrap(), "{name}: not the first frame");
    }
}

#[test]
fn jpeg_decodes_to_within_two_levels_of_pillow() {
    let g = common::fixture_json("intake.json");
    let img = decode("intake_jpg.jpg");
    assert_eq!(img.format, "JPEG");
    let sum = checksums(&img).0;
    let want = g["jpg"]["rgba_sum"].as_u64().unwrap();
    assert!((sum as f64 - want as f64).abs() / (img.rgba.len() as f64) < 2.0);

    // Byte by byte against Pillow's own decode: a different IDCT may round differently, not
    // by more than a level or two on average.
    let pillow = common::fixture_bytes("intake_jpg.rgba");
    assert_eq!(pillow.len(), img.rgba.len());
    let diffs: Vec<u8> = img.rgba.iter().zip(&pillow).map(|(a, b)| a.abs_diff(*b)).collect();
    let mean = diffs.iter().map(|d| *d as f64).sum::<f64>() / diffs.len() as f64;
    let max = *diffs.iter().max().unwrap();
    eprintln!("jpeg vs Pillow: mean |diff| {mean:.4}, max {max}");
    assert!(mean < 2.0, "mean {mean}");
}

#[test]
fn exif_orientation_is_applied() {
    let g = common::fixture_json("intake.json");
    let img = decode("intake_exif6.jpg");
    assert_eq!((img.width, img.height), (20, 40));
    assert_eq!((img.width as u64, img.height as u64), (g["exif6"]["width"].as_u64().unwrap(), g["exif6"]["height"].as_u64().unwrap()));
}

#[test]
fn limits_and_garbage_give_the_pythons_codes() {
    let png = common::fixture_bytes("intake_png.png");
    assert_eq!(load(&png, Limits { max_bytes: 10, max_pixels: 1 << 40 }).unwrap_err().code, "too_large");
    assert_eq!(load(&png, Limits { max_bytes: BIG, max_pixels: 100 }).unwrap_err().code, "too_many_pixels");
    assert_eq!(load(b"not an image", Limits::default()).unwrap_err().code, "unsupported_format");
    assert_eq!(load(&png[..png.len() / 2], Limits::default()).unwrap_err().code, "corrupt_image");
}

#[test]
fn every_rejection_carries_the_pythons_words() {
    let g = common::fixture_json("intake.json");
    for (name, case) in g["errors"].as_object().unwrap() {
        let bytes = match case["file"].as_str() {
            Some(file) => common::fixture_bytes(file),
            // `too_large_mb` has no file: zeros one byte over its 3 MB limit.
            None if name == "too_large_mb" => vec![0; 3 * 1024 * 1024 + 1],
            None if name == "garbage" => b"not an image".to_vec(),
            None if name == "truncated_png" => {
                let png = common::fixture_bytes("intake_png.png");
                png[..png.len() / 2].to_vec()
            }
            None => panic!("{name}: no bytes to replay"),
        };
        let limits = Limits { max_bytes: case["max_bytes"].as_u64().unwrap() as usize, max_pixels: case["max_pixels"].as_u64().unwrap() };
        let err = load(&bytes, limits).expect_err(name);
        assert_eq!(err.code, case["code"].as_str().unwrap(), "{name}");
        assert_eq!(err.message, case["message"].as_str().unwrap(), "{name}");
        assert_eq!(err.to_string(), err.message, "{name}: Display is the message");
    }
}

#[test]
fn the_pixel_limit_answers_from_the_header_before_any_decoding() {
    // intake_bomb.png: a header claiming 10000x10000 with the body cut off after 40 bytes. A
    // decode would fail with `corrupt_image`; the limit must fire first.
    let bomb = common::fixture_bytes("intake_bomb.png");
    let err = load(&bomb, Limits::default()).unwrap_err();
    assert_eq!((err.code, err.message.as_str()), ("too_many_pixels", "Image exceeds the 40 megapixel limit"));
    // Raise the limit and the same bytes are what they are: a truncated file.
    let err = load(&bomb, Limits { max_bytes: BIG, max_pixels: 1 << 40 }).unwrap_err();
    assert_eq!(err.code, "corrupt_image");
}
