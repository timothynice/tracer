//! Bytes a person dropped in -> an RGBA8 image, oriented as a viewer shows it.
//!
//! Ported from `backend/studi0trace/imaging/intake.py` (`load_upload`); the error codes
//! and the words of the messages are its own, because the frontend shows them. The
//! order of the checks is its order too: size, then format, then the longer side and the
//! pixel count from the header (so a decompression bomb is refused before a byte of it is
//! decoded), then
//! the pixels, the EXIF orientation and RGBA8. Nothing here touches the filesystem.
//!
//! # Where the pixels agree with Pillow's
//!
//! `tests/intake.rs` holds each of these to fixtures exported from Pillow:
//!
//! - **Byte for byte:** 8-bit PNG, GIF, WebP (lossless and lossy) and BMP, palette
//!   transparency, grey, grey+alpha and alpha included; 16-bit-per-channel PNG in RGB,
//!   RGBA and grey+alpha, which Pillow narrows by taking each channel's high byte.
//! - **Close:** JPEG, where two IDCTs round differently: a mean of 0.07 levels and a
//!   maximum of 3 on the fixture.
//! - **Deliberately not Pillow:** 16-bit *greyscale* PNG. Pillow reads it as mode `I;16`
//!   and its conversion to RGBA clips at 255 instead of scaling, so every sample above
//!   255 of 65535 (all but the darkest 0.4%) comes out white (`0x80FF` -> 255). Here it
//!   keeps the high byte like every other 16-bit channel (`0x80FF` -> 128). The Python
//!   reference is wrong there and is not copied.
//!
//! # Two decoders, two kinds of damage
//!
//! Pillow refuses a JPEG that stops before its end-of-image marker ("image file is
//! truncated"). The decoder behind `image` runs lax and returns what it has, with the
//! rest of the picture filled in, so `jpeg_is_truncated` asks the question of the
//! bytes first and a cut-short JPEG is `corrupt_image` here too.
use image::metadata::Orientation;
use image::{DynamicImage, ImageDecoder, ImageFormat, ImageReader, Rgba, RgbaImage};
use std::fmt;
use std::io::Cursor;

/// A decoded upload. `format` is the name Pillow gives the file's real format, whatever
/// the client called it: `"PNG" | "JPEG" | "GIF" | "WEBP" | "BMP"`.
#[derive(Debug, Clone)]
pub struct Image {
    pub rgba: Vec<u8>,
    pub width: u32,
    pub height: u32,
    pub format: String,
}

/// A rejected upload. `code` is stable and safe to send to clients; `message` is what they show.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IntakeError {
    pub code: &'static str,
    pub message: String,
}

impl fmt::Display for IntakeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for IntakeError {}

/// What an upload may be. Without `max_side`, `max_pixels` above 2^26 (about 67.1 MP) lets
/// through images that Auto cannot score (its renders at 2x would exceed
/// [`crate::render::MAX_PIXELS`]); see [`crate::api::Core::with_limits`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    /// The file's size in bytes (20 MiB by default).
    pub max_bytes: usize,
    /// Width times height, checked from the header before the pixels are decoded (40 MP by default).
    pub max_pixels: u64,
    /// The width and the height, checked from the header before the pixel count (4096 by
    /// default, which keeps the default image at 16.8 MP; `None` is no cap on a side). The
    /// engine's time and memory grow with the image: a 4.2 MP trace took 97 s and 7.5 GB.
    pub max_side: Option<u32>,
}

impl Default for Limits {
    // backend/studi0trace/settings.py: max_upload_bytes, max_image_pixels, max_image_side
    fn default() -> Self {
        Limits { max_bytes: 20 * 1024 * 1024, max_pixels: 40_000_000, max_side: Some(4096) }
    }
}

fn err(code: &'static str, message: impl Into<String>) -> IntakeError {
    IntakeError { code, message: message.into() }
}

fn unrecognised() -> IntakeError {
    err("unsupported_format", "File is not a recognised image")
}

fn corrupt() -> IntakeError {
    err("corrupt_image", "Image data is corrupt or truncated")
}

/// What Pillow calls a format the intake accepts, or `Err` with its rejection.
fn accepted(format: Option<ImageFormat>) -> Result<&'static str, IntakeError> {
    // Pillow names the formats it can open but the intake refuses (`img.format`); a format
    // Pillow cannot open at all is "not a recognised image", as it is here.
    let other = match format {
        Some(ImageFormat::Png) => return Ok("PNG"),
        Some(ImageFormat::Jpeg) => return Ok("JPEG"),
        Some(ImageFormat::Gif) => return Ok("GIF"),
        Some(ImageFormat::WebP) => return Ok("WEBP"),
        Some(ImageFormat::Bmp) => return Ok("BMP"),
        Some(ImageFormat::Tiff) => "TIFF",
        Some(ImageFormat::Ico) => "ICO",
        Some(ImageFormat::Pnm) => "PPM",
        Some(ImageFormat::Dds) => "DDS",
        Some(ImageFormat::Tga) => "TGA",
        Some(ImageFormat::Avif) => "AVIF",
        Some(ImageFormat::Qoi) => "QOI",
        _ => return Err(unrecognised()),
    };
    Err(err("unsupported_format", format!("Unsupported image format: {other}")))
}

pub fn load(bytes: &[u8], limits: Limits) -> Result<Image, IntakeError> {
    if bytes.len() > limits.max_bytes {
        return Err(err("too_large", format!("File exceeds the {} MB limit", limits.max_bytes / (1024 * 1024))));
    }

    let reader = ImageReader::new(Cursor::new(bytes)).with_guessed_format().map_err(|_| unrecognised())?;
    let format = accepted(reader.format())?;

    // Pillow's `open` reads the header only, and a header it cannot parse is "not a
    // recognised image"; the decoder's constructor is the same step here.
    let mut decoder = reader.into_decoder().map_err(|_| unrecognised())?;
    let (w, h) = decoder.dimensions();
    if let Some(side) = limits.max_side.filter(|&side| w.max(h) > side) {
        return Err(err("too_many_pixels", format!("Image exceeds the {side}x{side} pixel limit")));
    }
    if u64::from(w) * u64::from(h) > limits.max_pixels {
        return Err(err("too_many_pixels", format!("Image exceeds the {} megapixel limit", limits.max_pixels / 1_000_000)));
    }

    // Pillow's `img.load()` is where a JPEG that was cut short fails; the decoder here would not.
    if format == "JPEG" && jpeg_is_truncated(bytes) {
        return Err(corrupt());
    }

    // A bad EXIF block is not a bad image: Pillow's `exif_transpose` leaves it as it is.
    let orientation = decoder.orientation().unwrap_or(Orientation::NoTransforms);
    // The first frame of an animation, as Pillow's `load` gives it.
    let mut img = DynamicImage::from_decoder(decoder).map_err(|_| corrupt())?;
    img.apply_orientation(orientation);
    let rgba = to_rgba8(img);
    Ok(Image { width: rgba.width(), height: rgba.height(), rgba: rgba.into_raw(), format: format.into() })
}

/// RGBA8 from whatever the decoder produced. 8-bit samples pass through as they are. A
/// 16-bit sample becomes its high byte, `v >> 8`, in every channel, which is what Pillow
/// does for RGB, RGBA and grey+alpha; for 16-bit grey it is the deliberate difference
/// described at the top (Pillow clips, this scales). `DynamicImage::to_rgba8` would round
/// instead (`(v + 128) / 257`), which is one level off Pillow on about half of all samples.
fn to_rgba8(img: DynamicImage) -> RgbaImage {
    let hi = |v: u16| (v >> 8) as u8;
    match img {
        DynamicImage::ImageLuma16(b) => RgbaImage::from_fn(b.width(), b.height(), |x, y| {
            let g = hi(b.get_pixel(x, y).0[0]);
            Rgba([g, g, g, 255])
        }),
        DynamicImage::ImageLumaA16(b) => RgbaImage::from_fn(b.width(), b.height(), |x, y| {
            let [g, a] = b.get_pixel(x, y).0;
            Rgba([hi(g), hi(g), hi(g), hi(a)])
        }),
        DynamicImage::ImageRgb16(b) => RgbaImage::from_fn(b.width(), b.height(), |x, y| {
            let [r, g, bl] = b.get_pixel(x, y).0;
            Rgba([hi(r), hi(g), hi(bl), 255])
        }),
        DynamicImage::ImageRgba16(b) => RgbaImage::from_fn(b.width(), b.height(), |x, y| {
            let [r, g, bl, a] = b.get_pixel(x, y).0;
            Rgba([hi(r), hi(g), hi(bl), hi(a)])
        }),
        other => other.into_rgba8(),
    }
}

/// Whether `d`, a JPEG whose header a decoder has accepted, has a scan that never ends:
/// no end-of-image marker (`FF D9`) after the first start-of-scan (`FF DA`). Bytes after
/// the EOI are not looked at (a second picture, as in MPO and "motion photo" files, may follow).
///
/// It walks the markers rather than searching for `FF D9`: a segment is skipped by its
/// length field, so an EXIF thumbnail (a whole JPEG inside APP1, with an EOI of its own)
/// and a table whose bytes happen to read `FF D9` are not mistaken for the picture's end.
/// Inside entropy-coded data an `FF` is always followed by `00` (a stuffed byte) or a
/// restart marker, so the first `FF D9` met outside a skipped segment is the real EOI.
///
/// A segment that cannot be walked before the first scan is left to the decoder, which has
/// already parsed that far; from the scan on, a segment with a nonsense length or one that
/// runs past the end of the data is the cut, and so is running out of data. Every read is
/// bounds-checked, so hostile bytes get an answer and never a panic.
fn jpeg_is_truncated(d: &[u8]) -> bool {
    let mut i = 2; // past SOI
    let mut in_scan = false;
    loop {
        // The next marker: any run of non-marker bytes (entropy data, or junk the header
        // parser tolerated), then FF, any fill FFs, and the marker's code.
        while i < d.len() && d[i] != 0xFF {
            i += 1;
        }
        while i < d.len() && d[i] == 0xFF {
            i += 1;
        }
        let Some(&code) = d.get(i) else { return in_scan };
        i += 1;
        match code {
            // A stuffed FF, TEM, RSTn, SOI: markers that carry no length.
            0x00 | 0x01 | 0xD0..=0xD8 => {}
            0xD9 => return false,
            _ => {
                let starts_scan = code == 0xDA;
                let cut = in_scan || starts_scan;
                // The length counts its own two bytes.
                let Some(n) = d.get(i..i + 2).map(|b| usize::from(u16::from_be_bytes([b[0], b[1]]))) else { return cut };
                if n < 2 || i + n > d.len() {
                    return cut;
                }
                i += n;
                in_scan |= starts_scan;
            }
        }
    }
}

#[cfg(test)]
mod jpeg_walk {
    use super::jpeg_is_truncated;

    const SOI: &[u8] = &[0xFF, 0xD8];
    const EOI: &[u8] = &[0xFF, 0xD9];

    /// A marker segment: FF, the code, a big-endian length that counts itself, the body.
    fn seg(code: u8, body: &[u8]) -> Vec<u8> {
        let n = (body.len() + 2) as u16;
        [&[0xFF, code][..], &n.to_be_bytes(), body].concat()
    }

    fn sos() -> Vec<u8> {
        seg(0xDA, &[1, 1, 0, 0, 63, 0])
    }

    fn cat(parts: &[&[u8]]) -> Vec<u8> {
        parts.concat()
    }

    /// Entropy-coded bytes as a scan carries them: stuffed FF00, restart markers, plain bytes.
    const SCAN: &[u8] = &[0x12, 0xFF, 0x00, 0x34, 0xFF, 0xD0, 0x56, 0xFF, 0x00, 0xFF, 0xD1, 0x78];

    #[test]
    fn a_scan_closed_by_eoi_is_whole() {
        let jpeg = cat(&[SOI, &seg(0xE0, b"JFIF\0"), &seg(0xDB, &[0; 65]), &sos(), SCAN, EOI]);
        assert!(!jpeg_is_truncated(&jpeg));
    }

    #[test]
    fn a_scan_that_just_stops_is_truncated() {
        let whole = cat(&[SOI, &seg(0xE0, b"JFIF\0"), &sos(), SCAN, EOI]);
        for n in 0..whole.len() - 2 {
            let cut = &whole[..n];
            // Cuts in the header are the header parser's to refuse; from the end of SOS's own
            // header on, there is a scan and it has no end.
            if n >= whole.len() - SCAN.len() - 2 {
                assert!(jpeg_is_truncated(cut), "cut to {n}");
            }
        }
        assert!(jpeg_is_truncated(&whole[..whole.len() - 1]), "a lone FF is not an EOI");
        assert!(jpeg_is_truncated(&cat(&[SOI, &sos()])), "cut right after SOS");
    }

    #[test]
    fn bytes_after_the_eoi_are_not_the_checks_business() {
        // MPO and "motion photo" files carry a second picture, or anything, after the first EOI.
        let first = cat(&[SOI, &sos(), SCAN, EOI]);
        let more = cat(&[&first, &first, &[0; 40]]);
        assert!(!jpeg_is_truncated(&more));
    }

    #[test]
    fn an_eoi_inside_a_skipped_segment_is_not_the_pictures() {
        // An EXIF thumbnail is a whole JPEG inside APP1: its FFD9 must not close the main scan.
        let thumb = cat(&[SOI, &sos(), SCAN, EOI]);
        let app1 = seg(0xE1, &[b"Exif\0\0".as_slice(), &thumb].concat());
        let cut = cat(&[SOI, &app1, &sos(), SCAN]);
        assert!(jpeg_is_truncated(&cut));
        assert!(!jpeg_is_truncated(&cat(&[&cut, EOI])));
    }

    #[test]
    fn progressive_scans_are_walked_not_searched() {
        // Two scans with a table between them whose bytes happen to hold FF D9: skipped by length.
        let table = seg(0xC4, &[0x10, 0xFF, 0xD9, 0x00, 0x01]);
        let two = cat(&[SOI, &sos(), SCAN, &table, &sos(), SCAN]);
        assert!(jpeg_is_truncated(&two), "the second scan has no end");
        assert!(!jpeg_is_truncated(&cat(&[&two, EOI])));
    }

    #[test]
    fn fill_bytes_before_a_marker_are_allowed() {
        let jpeg = cat(&[SOI, &[0xFF, 0xFF, 0xFF], &seg(0xE0, b"JFIF\0")[1..], &sos(), SCAN, &[0xFF, 0xFF], EOI]);
        assert!(!jpeg_is_truncated(&jpeg));
    }

    #[test]
    fn hostile_lengths_and_short_input_are_answered_not_panicked_on() {
        for bytes in [
            &[][..],
            &[0xFF],
            SOI,
            &[0xFF, 0xD8, 0xFF],
            &[0xFF, 0xD8, 0xFF, 0xE0],
            &[0xFF, 0xD8, 0xFF, 0xE0, 0xFF],
            &[0xFF, 0xD8, 0xFF, 0xE0, 0xFF, 0xFF, 0x00],
            &[0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x00],
            &[0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x01],
        ] {
            let _ = jpeg_is_truncated(bytes); // the header parser owns these; just no panic
        }
        // A scan header whose length runs off the end, or is nonsense, is a cut scan.
        assert!(jpeg_is_truncated(&[0xFF, 0xD8, 0xFF, 0xDA, 0xFF, 0xFF, 0x00]));
        assert!(jpeg_is_truncated(&[0xFF, 0xD8, 0xFF, 0xDA, 0x00, 0x00]));
        assert!(jpeg_is_truncated(&[0xFF, 0xD8, 0xFF, 0xDA, 0x00]));
        assert!(jpeg_is_truncated(&[0xFF, 0xD8, 0xFF, 0xDA]));
        // Inside the scan, a segment that runs off the end is truncation too.
        assert!(jpeg_is_truncated(&cat(&[SOI, &sos(), SCAN, &[0xFF, 0xC4, 0xFF, 0xFF, 1, 2]])));
        assert!(jpeg_is_truncated(&cat(&[SOI, &sos(), SCAN, &[0xFF, 0xC4, 0x00, 0x00]])));
    }

    #[test]
    fn no_mutation_of_a_valid_stream_panics() {
        let base = cat(&[SOI, &seg(0xE0, b"JFIF\0"), &seg(0xC4, &[0x10, 0xFF, 0xD9]), &sos(), SCAN, &sos(), SCAN, EOI]);
        let mut state = 0x2545_F491_4F6C_DD1Du64;
        let mut next = || {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            state
        };
        for _ in 0..20_000 {
            let mut d = base.clone();
            for _ in 0..(next() % 4 + 1) {
                let at = (next() % d.len() as u64) as usize;
                d[at] = match next() % 4 { 0 => 0xFF, 1 => 0x00, 2 => 0xD9, _ => next() as u8 };
            }
            d.truncate((next() % (d.len() as u64 + 1)) as usize);
            let _ = jpeg_is_truncated(&d);
        }
    }
}
