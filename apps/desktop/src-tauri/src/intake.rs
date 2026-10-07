//! Opening a file: look at it before reading it, let macOS convert what the core has no decoder for (HEIC, HEIF,
//! TIFF) or scale down what is over the core's limits, then hand the bytes to `Core::upload`, which validates
//! them and names them. Nothing is read that the core would refuse for its size: a file over the byte limit is
//! refused from its length, HEIC/HEIF/TIFF are never read raw (`sips` reads them from the path), and their
//! dimensions are asked of `sips` before they are converted. `sips` writes the EXIF orientation into the PNG it
//! makes, and the core applies it.
use crate::error::CommandError;
use crate::store::OpenImage;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::io::Read;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use studi0trace_core::api::{ApiError, Core};
use studi0trace_core::intake::{IntakeError, Limits};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// PNG, JPEG, GIF, WebP, BMP, or anything else: the core decides.
    Native,
    Heif,
    Tiff,
}

const HEIF_BRANDS: &[&[u8; 4]] = &[b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1", b"avif"];

/// As many bytes as [`sniff`] looks at.
const SNIFF_BYTES: u64 = 16;

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
    Limits::default().max_side.expect("the core caps a side")
}

/// The core's refusal, in its own words (`intake::load`): the status and body `Core::upload` would answer.
fn refusal(code: &'static str, message: String) -> CommandError {
    ApiError::from(IntakeError { code, message }).into()
}

fn too_large(limits: &Limits) -> CommandError {
    refusal("too_large", format!("File exceeds the {} MB limit", limits.max_bytes / (1024 * 1024)))
}

/// The core's `too_many_pixels` for a `w` x `h` image, side first as the core checks; `None` when it fits.
fn too_many_pixels(w: u32, h: u32, limits: &Limits) -> Option<CommandError> {
    if let Some(side) = limits.max_side.filter(|&side| w.max(h) > side) {
        return Some(refusal("too_many_pixels", format!("Image exceeds the {side}x{side} pixel limit")));
    }
    (u64::from(w) * u64::from(h) > limits.max_pixels).then(|| refusal("too_many_pixels", format!("Image exceeds the {} megapixel limit", limits.max_pixels / 1_000_000)))
}

/// The longest side a `w` x `h` image is scaled to so it fits `limits`; `None` when it fits as it is.
fn fitted_side(w: u32, h: u32, limits: &Limits) -> Option<u32> {
    let long = w.max(h);
    let mut side = limits.max_side.map_or(long, |cap| long.min(cap));
    let pixels = u64::from(w) * u64::from(h);
    if pixels > limits.max_pixels {
        side = side.min((f64::from(long) * (limits.max_pixels as f64 / pixels as f64).sqrt()).floor() as u32);
    }
    (side < long).then_some(side)
}

/// The width and height `sips` reads from the file's header (stored, before any EXIF turn; neither the
/// longest side nor the pixel count cares). `None` when `sips` cannot say.
fn probe(path: &Path) -> Option<(u32, u32)> {
    let out = Command::new("/usr/bin/sips").args(["-g", "pixelWidth", "-g", "pixelHeight"]).arg(path).output().ok()?;
    if !out.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&out.stdout);
    // the first line is the path itself
    let get = |key: &str| text.lines().skip(1).find_map(|l| l.trim().strip_prefix(key)?.trim().parse().ok());
    Some((get("pixelWidth:")?, get("pixelHeight:")?))
}

static SCRATCH: AtomicU64 = AtomicU64::new(0);

fn scratch(ext: &str) -> PathBuf {
    std::env::temp_dir().join(format!("studi0trace-{}-{}.{ext}", std::process::id(), SCRATCH.fetch_add(1, Ordering::Relaxed)))
}

/// What `sips` does to the pixels on the way to PNG.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Fit {
    /// Nothing: a HEIC, HEIF or TIFF that fits, as PNG.
    AsIs,
    /// Scaled so its longest side is this (which also writes 8 bits a channel).
    Side(u32),
    /// At its own size in 8 bits a channel. `sips -s format png` alone keeps 16 bits, and matching to sRGB
    /// is what makes it write 8: exact for an untagged or sRGB file, a colour conversion for any other.
    EightBit,
}

const SRGB_PROFILE: &str = "/System/Library/ColorSync/Profiles/sRGB Profile.icc";

/// `src` as PNG bytes, made by `sips`.
fn convert(name: &str, src: &Path, fit: Fit) -> Result<Vec<u8>, CommandError> {
    let out = scratch("png");
    let mut cmd = Command::new("/usr/bin/sips");
    cmd.args(["-s", "format", "png"]);
    match fit {
        Fit::AsIs => {}
        Fit::Side(side) => {
            cmd.arg("--resampleHeightWidthMax").arg(side.to_string());
        }
        Fit::EightBit => {
            cmd.args(["--matchTo", SRGB_PROFILE]);
        }
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

/// The image at `src` made to fit the core: scaled to fit when a side or the pixel count is over, re-encoded
/// at its own size in 8 bits when only its bytes are (`sips -Z` would enlarge it to the cap).
fn shrink(name: &str, src: &Path) -> Result<Vec<u8>, CommandError> {
    match probe(src) {
        Some((w, h)) => convert(name, src, fitted_side(w, h, &Limits::default()).map_or(Fit::EightBit, Fit::Side)),
        None => convert(name, src, Fit::Side(max_side())),
    }
}

/// The desktop's id for an image: the core's content id, and with a path a hash of the two (32 lowercase hex
/// digits, as the core's), so one file's bytes in two folders are two images, each with its own folder.
fn image_id(core_id: &str, path: Option<&Path>) -> String {
    let Some(path) = path else { return core_id.to_string() };
    let mut hash = Sha256::new();
    hash.update(core_id.as_bytes());
    hash.update([0]);
    hash.update(path.as_os_str().as_bytes());
    hash.finalize()[..16].iter().map(|b| format!("{b:02x}")).collect()
}

fn admit(core: &Core, name: &str, path: Option<&Path>, bytes: Vec<u8>) -> Result<OpenImage, CommandError> {
    let up: Value = core.upload(&bytes)?;
    Ok(OpenImage {
        id: image_id(up["image_id"].as_str().unwrap_or_default(), path),
        name: name.to_string(),
        path: path.map(Path::to_path_buf),
        width: up["width"].as_u64().unwrap_or(0) as u32,
        height: up["height"].as_u64().unwrap_or(0) as u32,
        format: up["format"].as_str().unwrap_or_default().to_string(),
        bytes: Arc::new(bytes),
        original: None,
    })
}

pub fn file_name(path: &Path) -> String {
    path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| path.display().to_string())
}

fn is_size_refusal(e: &CommandError) -> bool {
    matches!(e.code(), Some("too_many_pixels" | "too_large"))
}

/// Open the file at `src`, named `name`, recorded with `path` (`None` for a scratch copy of bytes). With
/// `downscale`, an image the core refuses for its size is made to fit ([`shrink`]); one that fits is never
/// touched.
fn open_file(core: &Core, name: &str, src: &Path, path: Option<&Path>, downscale: bool) -> Result<OpenImage, CommandError> {
    let io = |e: std::io::Error| CommandError::io(src, &e);
    let limits = Limits::default();
    let file = std::fs::File::open(src).map_err(io)?;
    let len = file.metadata().map_err(io)?.len();
    let mut head = Vec::with_capacity(SNIFF_BYTES as usize);
    file.take(SNIFF_BYTES).read_to_end(&mut head).map_err(io)?;
    let first = if sniff(&head) == Kind::Native {
        if len > limits.max_bytes as u64 {
            Err(too_large(&limits))
        } else {
            admit(core, name, path, std::fs::read(src).map_err(io)?)
        }
    } else {
        match probe(src).and_then(|(w, h)| too_many_pixels(w, h, &limits)) {
            Some(refused) => Err(refused),
            None => convert(name, src, Fit::AsIs).and_then(|png| admit(core, name, path, png)),
        }
    };
    match first {
        Err(e) if downscale && is_size_refusal(&e) => admit(core, name, path, shrink(name, src)?),
        other => other,
    }
}

/// Open the file at `path` (see [`open_file`]).
pub fn open_path(core: &Core, path: &Path, downscale: bool) -> Result<OpenImage, CommandError> {
    open_file(core, &file_name(path), path, Some(path), downscale)
}

/// Open bytes that have no file (a sample, a paste).
pub fn open_bytes(core: &Core, name: &str, bytes: Vec<u8>) -> Result<OpenImage, CommandError> {
    if sniff(&bytes) == Kind::Native {
        return admit(core, name, None, bytes);
    }
    let tmp = scratch("img");
    std::fs::write(&tmp, &bytes).map_err(|e| CommandError::io_write(&tmp, &e))?;
    drop(bytes);
    let opened = open_file(core, name, &tmp, None, false);
    let _ = std::fs::remove_file(&tmp);
    opened
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

    fn dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("s0t-{tag}-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// What the core itself answers for a file one byte over its limit.
    fn cores_too_large() -> CommandError {
        let max = studi0trace_core::intake::Limits::default().max_bytes;
        CommandError::from(Core::new().upload(&vec![0u8; max + 1]).unwrap_err())
    }

    #[test]
    fn a_huge_file_is_refused_without_being_read() {
        use std::io::Write;
        let path = dir("huge").join("huge.png");
        let mut f = std::fs::File::create(&path).unwrap();
        f.write_all(&png(2, 2)[..16]).unwrap();
        f.set_len(3 << 30).unwrap(); // sparse: 3 GB on paper, nothing on disk
        drop(f);
        let start = std::time::Instant::now();
        let refused = open_path(&Core::new(), &path, false).unwrap_err();
        let took = start.elapsed();
        let _ = std::fs::remove_file(&path);
        assert_eq!(refused, cores_too_large());
        assert_eq!((refused.status, refused.code()), (400, Some("too_large")));
        assert!(took < std::time::Duration::from_millis(100), "took {took:?}: the file was read");
    }

    /// An 1800 px 16-bit PNG of noise: over the byte limit at a size within the cap.
    fn noisy_16_bit(path: &Path) {
        let mut state = 0x2545_f491_4f6c_dd1du64;
        let img = image::ImageBuffer::<image::Rgba<u16>, Vec<u16>>::from_fn(1800, 1800, |_, _| {
            let mut next = || {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                state as u16
            };
            image::Rgba([next(), next(), next(), u16::MAX])
        });
        img.save_with_format(path, image::ImageFormat::Png).unwrap();
        let max = studi0trace_core::intake::Limits::default().max_bytes as u64;
        assert!(std::fs::metadata(path).unwrap().len() > max);
    }

    #[test]
    fn downscale_of_a_file_too_large_within_the_cap_keeps_its_size() {
        let core = Core::new();
        let path = dir("deep").join("deep.png");
        noisy_16_bit(&path);
        assert_eq!(open_path(&core, &path, false).unwrap_err(), cores_too_large());
        let img = open_path(&core, &path, true).unwrap();
        assert_eq!((img.width, img.height), (1800, 1800));
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn identical_files_in_two_folders_are_two_images() {
        let core = Core::new();
        let images = crate::store::Images::default();
        let (a, b) = (dir("twin-a").join("same.png"), dir("twin-b").join("same.png"));
        std::fs::write(&a, png(12, 12)).unwrap();
        std::fs::write(&b, png(12, 12)).unwrap();
        let first = images.insert(open_path(&core, &a, false).unwrap());
        let second = images.insert(open_path(&core, &b, false).unwrap());
        assert_ne!(first.id, second.id);
        assert_eq!(images.len(), 2);
        assert_eq!((first.path.as_deref(), second.path.as_deref()), (a.to_str(), b.to_str()));
        for id in [&first.id, &second.id] {
            assert!(id.len() == 32 && id.chars().all(|c| matches!(c, '0'..='9' | 'a'..='f')), "{id}");
        }
        // the same file again is the same entry, and bytes without a path keep the core's own id
        assert_eq!(images.insert(open_path(&core, &a, false).unwrap()).id, first.id);
        assert_eq!(images.len(), 2);
        let up = core.upload(&png(12, 12)).unwrap();
        assert_eq!(open_bytes(&core, "paste.png", png(12, 12)).unwrap().id, up["image_id"].as_str().unwrap());
    }

    #[test]
    fn a_heic_over_the_cap_is_refused_before_it_is_converted() {
        // 3000 x 2400 of noise: its PNG at full size is over the byte limit, so a conversion first would be
        // refused `too_large`, quoting 20 MB for a file of a few
        let dir = dir("bigheic");
        let noise = dir.join("noise.png");
        let mut state = 0x9e37_79b9_7f4a_7c15u64;
        image::RgbImage::from_fn(3000, 2400, |_, _| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            image::Rgb([state as u8, (state >> 8) as u8, (state >> 16) as u8])
        })
        .save_with_format(&noise, image::ImageFormat::Png)
        .unwrap();
        let heic = dir.join("noise.heic");
        let made = std::process::Command::new("/usr/bin/sips").args(["-s", "format", "heic"]).arg(&noise).arg("--out").arg(&heic).output().unwrap();
        if !made.status.success() || !heic.exists() {
            eprintln!("skipped: sips cannot write HEIC here");
            return;
        }
        assert_eq!(probe(&heic), Some((3000, 2400)));
        let core = Core::new();
        let refused = open_path(&core, &heic, false).unwrap_err();
        // the core's own answer for an image over the cap
        let cores = CommandError::from(core.upload(&png(3000, 300)).unwrap_err());
        assert_eq!(refused, cores);
        assert_eq!(refused.code(), Some("too_many_pixels"));
        let img = open_path(&core, &heic, true).unwrap();
        assert!(img.width == 2048 && (1638..=1639).contains(&img.height), "{}x{}", img.width, img.height);
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
