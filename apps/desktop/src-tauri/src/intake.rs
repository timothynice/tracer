//! Opening a file: read it, let macOS convert what the core has no decoder for (HEIC, HEIF, TIFF) or scale
//! down what is over the core's side cap, then hand the bytes to `Core::upload`, which validates them and
//! names them. `sips` writes the EXIF orientation into the PNG it makes, and the core applies it.
use crate::error::CommandError;
use crate::store::OpenImage;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use studi0trace_core::api::Core;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// PNG, JPEG, GIF, WebP, BMP, or anything else: the core decides.
    Native,
    Heif,
    Tiff,
}

const HEIF_BRANDS: &[&[u8; 4]] = &[b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1", b"avif"];

/// What a file is, by its first bytes (the extension is not trusted, as the server never trusted it).
pub fn sniff(bytes: &[u8]) -> Kind {
    if bytes.starts_with(b"II*\0") || bytes.starts_with(b"MM\0*") {
        return Kind::Tiff;
    }
    if bytes.len() >= 12 && &bytes[4..8] == b"ftyp" && HEIF_BRANDS.iter().any(|b| &bytes[8..12] == *b) {
        return Kind::Heif;
    }
    Kind::Native
}

/// The core's cap on a side (2048).
pub fn max_side() -> u32 {
    studi0trace_core::intake::Limits::default().max_side.expect("the core caps a side")
}

static SCRATCH: AtomicU64 = AtomicU64::new(0);

fn scratch(ext: &str) -> PathBuf {
    std::env::temp_dir().join(format!("studi0trace-{}-{}.{ext}", std::process::id(), SCRATCH.fetch_add(1, Ordering::Relaxed)))
}

/// `src` as PNG bytes, made by `sips`, scaled to fit `max_side` if given.
fn convert(name: &str, src: &Path, max_side: Option<u32>) -> Result<Vec<u8>, CommandError> {
    let out = scratch("png");
    let mut cmd = Command::new("/usr/bin/sips");
    cmd.args(["-s", "format", "png"]);
    if let Some(side) = max_side {
        cmd.arg("--resampleHeightWidthMax").arg(side.to_string());
    }
    let done = cmd.arg(src).arg("--out").arg(&out).output().map_err(|e| CommandError::conversion(name, e))?;
    let read = std::fs::read(&out);
    let _ = std::fs::remove_file(&out);
    if !done.status.success() {
        let stderr = String::from_utf8_lossy(&done.stderr);
        let why = stderr.lines().map(str::trim).find(|l| !l.is_empty()).map(str::to_string).unwrap_or_else(|| done.status.to_string());
        return Err(CommandError::conversion(name, why));
    }
    read.map_err(|e| CommandError::conversion(name, e))
}

fn admit(core: &Core, name: String, path: Option<PathBuf>, bytes: Vec<u8>) -> Result<OpenImage, CommandError> {
    let up: Value = core.upload(&bytes)?;
    Ok(OpenImage {
        id: up["image_id"].as_str().unwrap_or_default().to_string(),
        name,
        path,
        width: up["width"].as_u64().unwrap_or(0) as u32,
        height: up["height"].as_u64().unwrap_or(0) as u32,
        format: up["format"].as_str().unwrap_or_default().to_string(),
        bytes: Arc::new(bytes),
    })
}

pub fn file_name(path: &Path) -> String {
    path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| path.display().to_string())
}

/// Open the file at `path`. With `downscale`, an image the core refuses for its size is scaled to fit the
/// core's side cap and opened again; one that fits is never touched (`sips -Z` would enlarge it).
pub fn open_path(core: &Core, path: &Path, downscale: bool) -> Result<OpenImage, CommandError> {
    let name = file_name(path);
    let raw = std::fs::read(path).map_err(|e| CommandError::io(path, &e))?;
    let bytes = if sniff(&raw) != Kind::Native { convert(&name, path, None)? } else { raw };
    match admit(core, name.clone(), Some(path.to_path_buf()), bytes) {
        Err(e) if downscale && matches!(e.code(), Some("too_many_pixels" | "too_large")) => {
            admit(core, name.clone(), Some(path.to_path_buf()), convert(&name, path, Some(max_side()))?)
        }
        other => other,
    }
}

/// Open bytes that have no file (a sample, a paste).
pub fn open_bytes(core: &Core, name: &str, bytes: Vec<u8>) -> Result<OpenImage, CommandError> {
    if sniff(&bytes) == Kind::Native {
        return admit(core, name.to_string(), None, bytes);
    }
    let tmp = scratch("img");
    std::fs::write(&tmp, &bytes).map_err(|e| CommandError::io(&tmp, &e))?;
    let converted = convert(name, &tmp, None);
    let _ = std::fs::remove_file(&tmp);
    admit(core, name.to_string(), None, converted?)
}

/// A file name the UI sent in a header, where it is percent-encoded (`encodeURIComponent`); left as it is
/// where it does not decode.
pub fn decode_header_name(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let pair = &bytes[i + 1..i + 3];
            let hex = if pair.iter().all(u8::is_ascii_hexdigit) { std::str::from_utf8(pair).ok().and_then(|h| u8::from_str_radix(h, 16).ok()) } else { None };
            match hex {
                Some(b) => {
                    out.push(b);
                    i += 3;
                    continue;
                }
                None => return s.to_string(),
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8(out).unwrap_or_else(|_| s.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::{Path, PathBuf};

    fn fixture(name: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../crates/studi0trace-core/tests/fixtures").join(name)
    }

    fn png(w: u32, h: u32) -> Vec<u8> {
        let mut out = std::io::Cursor::new(Vec::new());
        image::RgbaImage::from_pixel(w, h, image::Rgba([200, 30, 30, 255])).write_to(&mut out, image::ImageFormat::Png).unwrap();
        out.into_inner()
    }

    fn sips(src: &Path, format: &str, out: &Path) {
        let ok = std::process::Command::new("/usr/bin/sips").args(["-s", "format", format]).arg(src).arg("--out").arg(out).output().unwrap();
        assert!(ok.status.success(), "{}", String::from_utf8_lossy(&ok.stderr));
    }

    #[test]
    fn sniffs_what_the_core_cannot_read() {
        assert_eq!(sniff(&png(2, 2)), Kind::Native);
        assert_eq!(sniff(b"MM\0*rest"), Kind::Tiff);
        assert_eq!(sniff(b"II*\0rest"), Kind::Tiff);
        assert_eq!(sniff(b"\0\0\0\x24ftypheic\0\0\0\0"), Kind::Heif);
        assert_eq!(sniff(b"\0\0\0\x18ftypmif1"), Kind::Heif);
        assert_eq!(sniff(b"\0\0\0\x18ftypisom"), Kind::Native); // an MP4 is not an image; the core refuses it
    }

    #[test]
    fn opens_a_png_from_a_path() {
        let core = Core::new();
        let dir = std::env::temp_dir().join(format!("s0t-intake-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("red.png");
        std::fs::write(&path, png(30, 10)).unwrap();
        let img = open_path(&core, &path, false).unwrap();
        assert_eq!((img.name.as_str(), img.width, img.height, img.format.as_str()), ("red.png", 30, 10, "PNG"));
        assert_eq!(img.path.as_deref(), Some(path.as_path()));
        assert_eq!(img.id.len(), 32);
    }

    #[test]
    fn heic_and_tiff_are_converted_and_keep_their_orientation() {
        // intake_exif6.jpg is stored 40 x 20 with EXIF orientation 6: shown 20 x 40
        let core = Core::new();
        let dir = std::env::temp_dir().join(format!("s0t-heic-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        for format in ["heic", "tiff"] {
            let out = dir.join(format!("turned.{format}"));
            sips(&fixture("intake_exif6.jpg"), format, &out);
            let img = open_path(&core, &out, false).unwrap();
            assert_eq!((img.width, img.height, img.format.as_str()), (20, 40, "PNG"), "{format}");
        }
    }

    #[test]
    fn an_image_over_the_cap_is_refused_and_can_be_downscaled() {
        let core = Core::new();
        let dir = std::env::temp_dir().join(format!("s0t-big-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("wide.png");
        std::fs::write(&path, png(3000, 300)).unwrap();
        let refused = open_path(&core, &path, false).unwrap_err();
        assert_eq!(refused.code(), Some("too_many_pixels"));
        let img = open_path(&core, &path, true).unwrap();
        // sips rounds 204.8 one way or the other
        assert!(img.width == 2048 && (204..=205).contains(&img.height), "{}x{}", img.width, img.height);
    }

    #[test]
    fn downscale_never_enlarges() {
        let core = Core::new();
        let dir = std::env::temp_dir().join(format!("s0t-small-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let small = dir.join("small.png");
        std::fs::write(&small, png(40, 20)).unwrap();
        let img = open_path(&core, &small, true).unwrap();
        assert_eq!((img.width, img.height), (40, 20));
        // a HEIC that fits is converted but not scaled either
        let heic = dir.join("small.heic");
        sips(&fixture("intake_exif6.jpg"), "heic", &heic);
        let img = open_path(&core, &heic, true).unwrap();
        assert_eq!((img.width, img.height), (20, 40));
    }

    #[test]
    fn a_downscaled_image_keeps_its_orientation() {
        // stored 3000 x 300 with EXIF orientation 6: shown 300 x 3000, over the cap
        let core = Core::new();
        let dir = std::env::temp_dir().join(format!("s0t-bigturn-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let big = dir.join("turned.jpg");
        let done = std::process::Command::new("/usr/bin/sips").args(["--resampleHeightWidth", "300", "3000"]).arg(fixture("intake_exif6.jpg")).arg("--out").arg(&big).output().unwrap();
        assert!(done.status.success(), "{}", String::from_utf8_lossy(&done.stderr));
        assert_eq!(open_path(&core, &big, false).unwrap_err().code(), Some("too_many_pixels"));
        let img = open_path(&core, &big, true).unwrap();
        assert!(img.height == 2048 && (204..=205).contains(&img.width), "{}x{}", img.width, img.height);
    }

    #[test]
    fn a_failed_conversion_names_one_line_of_sips() {
        let core = Core::new();
        let dir = std::env::temp_dir().join(format!("s0t-bad-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let bad = dir.join("bad.tiff");
        std::fs::write(&bad, b"II*\0 this is not a tiff").unwrap();
        let e = open_path(&core, &bad, false).unwrap_err();
        assert_eq!(e.code(), Some("conversion_failed"));
        let message = e.body["detail"]["message"].as_str().unwrap();
        assert!(message.starts_with("macOS could not convert bad.tiff: ") && !message.contains('\n'), "{message}");
    }

    #[test]
    fn bytes_open_like_files_and_a_missing_file_is_an_io_error() {
        let core = Core::new();
        let img = open_bytes(&core, "paste.png", png(8, 8)).unwrap();
        assert_eq!((img.name.as_str(), img.path, img.width), ("paste.png", None, 8));
        let missing = open_path(&core, Path::new("/nonexistent/none.png"), false).unwrap_err();
        assert_eq!(missing.code(), Some("io_error"));
        assert_eq!(open_bytes(&core, "junk.bin", b"junk".to_vec()).unwrap_err().code(), Some("unsupported_format"));
    }

    #[test]
    fn header_names_are_percent_decoded() {
        assert_eq!(decode_header_name("logo%20%C3%A9t%C3%A9.png"), "logo été.png");
        assert_eq!(decode_header_name("plain.png"), "plain.png");
        assert_eq!(decode_header_name("bad%zz.png"), "bad%zz.png");
        assert_eq!(decode_header_name("a%+1b.png"), "a%+1b.png"); // from_str_radix would take the sign
        assert_eq!(decode_header_name("100%"), "100%");
    }
}
