//! `intake::load` against what Pillow does with the same bytes
//! (`backend/studi0trace/imaging/intake.py`). Fixtures: `tools.export_core_fixtures --only intake`.
//!
//! What is held to Pillow, and how tightly:
//! - 8-bit PNG, GIF, WebP (lossless and lossy) and BMP, palette, grey and alpha included,
//!   and 16-bit RGB, RGBA and grey+alpha PNG: every byte the same.
//! - JPEG: a fifth of a level on average (a different IDCT; 0.07 measured), the EXIF turn the way
//!   Pillow turns it (non-uniform pictures, four orientations), and the same rejections.
//! - 16-bit *grey* PNG: deliberately not Pillow. Pillow clips it to 255 (everything above the
//!   darkest 0.4% turns white); the intake keeps the high byte, as it does for every 16-bit channel.
mod common;
use studi0trace_core::intake::{load, Image, Limits};

const BIG: usize = 1 << 30;

/// Sum and position-weighted sum of the RGBA bytes, as the exporter computes them.
fn checksums(img: &Image) -> (u64, u64) {
    let sum = img.rgba.iter().map(|b| *b as u64).sum();
    let weighted = img.rgba.iter().enumerate().map(|(i, b)| *b as u64 * (i as u64 % 251 + 1)).sum();
    (sum, weighted)
}

/// `load` that must fail, without printing the pixels of the image it wrongly accepted.
fn refused(bytes: &[u8], limits: Limits, what: &str) -> studi0trace_core::intake::IntakeError {
    match load(bytes, limits) {
        Ok(img) => panic!("{what}: accepted as a {}x{} {} image", img.width, img.height, img.format),
        Err(e) => e,
    }
}

fn decode(file: &str) -> Image {
    load(&common::fixture_bytes(file), Limits::default()).unwrap_or_else(|e| panic!("{file}: {} ({})", e.code, e.message))
}

/// Every fixture the exporter marks `lossless`: 8-bit samples, so there is one right answer.
#[test]
fn eight_bit_lossless_files_decode_exactly_as_pillow_does() {
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

/// Mean and maximum of the absolute differences between two RGBA buffers of one size.
fn differences(a: &[u8], b: &[u8]) -> (f64, u8) {
    assert_eq!(a.len(), b.len());
    let diffs: Vec<u8> = a.iter().zip(b).map(|(x, y)| x.abs_diff(*y)).collect();
    (diffs.iter().map(|d| *d as f64).sum::<f64>() / diffs.len() as f64, *diffs.iter().max().unwrap())
}

#[test]
fn jpeg_decodes_to_within_a_fifth_of_a_level_of_pillow() {
    let g = common::fixture_json("intake.json");
    let img = decode("intake_jpg.jpg");
    assert_eq!(img.format, "JPEG");
    let sum = checksums(&img).0;
    let want = g["jpg"]["rgba_sum"].as_u64().unwrap();
    assert!((sum as f64 - want as f64).abs() / (img.rgba.len() as f64) < 0.2);

    // Byte by byte against Pillow's own decode: a different IDCT rounds some samples the other
    // way. Measured on this 96 x 96 quality-90 picture: a mean of 0.07 of a level and a
    // largest difference of 3; the bounds leave room for the decoder's next release and no more.
    let pillow = common::fixture_bytes("intake_jpg.rgba");
    let (mean, max) = differences(&img.rgba, &pillow);
    eprintln!("jpeg vs Pillow: mean |diff| {mean:.4}, max {max}");
    assert!(mean < 0.2, "mean {mean}");
    assert!(max <= 4, "max {max}");
}

#[test]
fn exif_orientation_is_applied_the_way_pillow_applies_it() {
    // A picture with a white 8x8 marker at its stored top-left and ramps of red and green across it,
    // saved with orientations 6 and 8 (the rotations), 2 (a mirror) and 5 (the transpose). A uniform
    // picture comes out the same however it is turned; this one tells every turn from every other
    // by its size, by where the marker went (exactly: the box of the pixels whose blue is high) and
    // by the whole picture against what Pillow's `exif_transpose` made of the same file.
    let g = common::fixture_json("intake.json");
    let cases = g["exif"].as_object().unwrap();
    assert_eq!(cases.keys().collect::<Vec<_>>(), ["2", "5", "6", "8"]);
    let mut boxes = std::collections::BTreeSet::new();
    for (orientation, case) in cases {
        let img = decode(case["file"].as_str().unwrap());
        let (w, h) = (case["width"].as_u64().unwrap() as u32, case["height"].as_u64().unwrap() as u32);
        assert_eq!((img.width, img.height), (w, h), "orientation {orientation}");
        let (mut x0, mut y0, mut x1, mut y1, mut n) = (u32::MAX, u32::MAX, 0, 0, 0);
        for (i, px) in img.rgba.chunks_exact(4).enumerate() {
            if px[2] > 150 {
                let (x, y) = (i as u32 % w, i as u32 / w);
                (x0, y0, x1, y1, n) = (x0.min(x), y0.min(y), x1.max(x), y1.max(y), n + 1);
            }
        }
        let want: Vec<u64> = case["box"].as_array().unwrap().iter().map(|v| v.as_u64().unwrap()).collect();
        assert_eq!(([x0, y0, x1, y1].map(u64::from).to_vec(), n), (want, 64), "orientation {orientation}: where the marker went ({})", case["corner"]);
        boxes.insert((w, h, x0, y0));
        let pillow = common::fixture_bytes(case["rgba_file"].as_str().unwrap());
        let (mean, max) = differences(&img.rgba, &pillow);
        eprintln!("exif {orientation}: vs Pillow mean |diff| {mean:.4}, max {max}");
        assert!(mean < 0.05 && max <= 4, "orientation {orientation}: the picture is not Pillow's turned picture: mean {mean}, max {max}");
    }
    // four different turns, four different results
    assert_eq!(boxes.len(), 4, "{boxes:?}");
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
        let err = refused(&bytes, limits, name);
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

fn unhex(hex: &str) -> Vec<u8> {
    assert!(hex.len() % 2 == 0);
    (0..hex.len()).step_by(2).map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap()).collect()
}

#[test]
fn sixteen_bit_channels_keep_their_high_byte() {
    let g = common::fixture_json("intake.json");
    let cases = g["bit16"].as_object().unwrap();
    assert_eq!(cases.len(), 4, "grey, rgb, la, rgba");
    let mut failures = Vec::new();
    for (name, case) in cases {
        let img = decode(case["file"].as_str().unwrap());
        assert_eq!((img.format.as_str(), img.width, img.height), ("PNG", 16, 16), "{name}");
        let want = unhex(case["rgba_hex"].as_str().unwrap());
        assert_eq!(want.len(), img.rgba.len(), "{name}");
        // Report how many bytes differ and the first, with its pixel, rather than a 1 KB dump.
        let wrong = img.rgba.iter().zip(&want).filter(|(a, b)| a != b).count();
        if let Some(i) = img.rgba.iter().zip(&want).position(|(a, b)| a != b) {
            failures.push(format!("{name} ({}): {wrong} bytes differ, the first at byte {i} (pixel {}): {} for {}", case["basis"], i / 4, img.rgba[i], want[i]));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn sixteen_bit_grey_is_the_high_byte_not_pillows_clip() {
    // The first sixteen samples of the fixture, and what they must become. 0x0182 -> 1 (a
    // rounding conversion says 2), 0x80FF -> 128 (Pillow's I;16 path says 255).
    let samples: [u16; 16] = [0x0000, 0x0001, 0x007F, 0x00FF, 0x0100, 0x0182, 0x0183, 0x7FFF, 0x8000, 0x80FF, 0x8100, 0xFEFF, 0xFF00, 0xFF7F, 0xFF80, 0xFFFF];
    let want: [u8; 16] = [0, 0, 0, 0, 1, 1, 1, 127, 128, 128, 129, 254, 255, 255, 255, 255];
    assert!(samples.iter().zip(&want).all(|(s, w)| (s >> 8) as u8 == *w));
    let img = decode("intake_16_grey.png");
    for (i, w) in want.iter().enumerate() {
        assert_eq!(&img.rgba[i * 4..i * 4 + 4], &[*w, *w, *w, 255], "sample {:#06x}", samples[i]);
    }
    // The colour channels follow the same rule, and there Pillow agrees: the RGB fixture's
    // second pixel is the samples (0x00FF, 0x0100, 0x0182), which Pillow reads as (0, 1, 1).
    let rgb = decode("intake_16_rgb.png");
    assert_eq!(&rgb.rgba[4..8], &[0, 1, 1, 255]);
}

#[test]
fn a_jpeg_cut_short_is_corrupt_not_half_grey() {
    // Pillow: "image file is truncated". zune-jpeg, in the lax mode `image` runs it in,
    // decodes what there is and fills the rest in.
    let g = common::fixture_json("intake.json");
    for name in ["truncated_jpg_half", "truncated_jpg_after_sos", "truncated_jpg_no_eoi", "truncated_jpg_thumb_half"] {
        let case = &g["errors"][name];
        let bytes = common::fixture_bytes(case["file"].as_str().unwrap());
        let err = refused(&bytes, Limits::default(), name);
        assert_eq!((err.code, err.message.as_str()), ("corrupt_image", "Image data is corrupt or truncated"), "{name}");
    }
}

#[test]
fn every_proper_prefix_of_a_jpeg_is_rejected() {
    // Whatever the cut, the file is refused (in the header, or as truncated); the whole file loads.
    for file in ["intake_jpg.jpg", "intake_jpg_thumb.jpg"] {
        let bytes = common::fixture_bytes(file);
        assert!(load(&bytes, Limits::default()).is_ok(), "{file}");
        for n in 0..bytes.len() {
            refused(&bytes[..n], Limits::default(), &format!("{file} cut to {n} of {} bytes", bytes.len()));
        }
    }
}

#[test]
fn a_jpeg_with_a_thumbnail_or_with_more_after_its_eoi_still_loads() {
    let g = common::fixture_json("intake.json");
    for (name, case) in g["jpeg_ok"].as_object().unwrap() {
        let img = decode(case["file"].as_str().unwrap());
        assert_eq!((img.format.as_str(), img.width as u64, img.height as u64), ("JPEG", case["width"].as_u64().unwrap(), case["height"].as_u64().unwrap()), "{name}");
        let sum = checksums(&img).0 as f64;
        let want = case["rgba_sum"].as_u64().unwrap() as f64;
        assert!((sum - want).abs() / (img.rgba.len() as f64) < 2.0, "{name}");
    }
}
