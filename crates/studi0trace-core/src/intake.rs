//! Bytes a person dropped in -> an RGBA8 image, oriented as a viewer shows it.
//!
//! Ported from `backend/studi0trace/imaging/intake.py` (`load_upload`); the error codes
//! and the words of the messages are its own, because the frontend shows them. The
//! order of the checks is its order too: size, then format, then the pixel count from
//! the header (so a decompression bomb is refused before a byte of it is decoded), then
//! the pixels, the EXIF orientation and RGBA8. Nothing here touches the filesystem.
use image::metadata::Orientation;
use image::{DynamicImage, ImageDecoder, ImageFormat, ImageReader};
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    pub max_bytes: usize,
    pub max_pixels: u64,
}

impl Default for Limits {
    // backend/studi0trace/settings.py: max_upload_bytes, max_image_pixels
    fn default() -> Self {
        Limits { max_bytes: 20 * 1024 * 1024, max_pixels: 40_000_000 }
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
    if u64::from(w) * u64::from(h) > limits.max_pixels {
        return Err(err("too_many_pixels", format!("Image exceeds the {} megapixel limit", limits.max_pixels / 1_000_000)));
    }

    // A bad EXIF block is not a bad image: Pillow's `exif_transpose` leaves it as it is.
    let orientation = decoder.orientation().unwrap_or(Orientation::NoTransforms);
    // The first frame of an animation, as Pillow's `load` gives it.
    let mut img = DynamicImage::from_decoder(decoder).map_err(|_| corrupt())?;
    img.apply_orientation(orientation);
    let rgba = img.into_rgba8();
    Ok(Image { width: rgba.width(), height: rgba.height(), rgba: rgba.into_raw(), format: format.into() })
}
