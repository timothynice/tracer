//! Writing exports. The UI hands over the bytes and a suggested name; this side picks the place (a save panel,
//! or beside the original, named as Finder names copies) and writes, so the webview never names a path to write.
use crate::error::CommandError;
use serde::{Deserialize, Serialize};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};


/// A suggested file name made safe to write: no folders, not hidden, not empty.
pub fn safe_name(name: &str, fallback_ext: &str) -> String {
    let cleaned: String = name.chars().map(|c| if matches!(c, '/' | ':' | '\0') { '-' } else { c }).collect();
    let trimmed = cleaned.trim().trim_start_matches('.');
    if trimmed.is_empty() {
        format!("Untitled.{fallback_ext}")
    } else {
        trimmed.to_string()
    }
}

/// `dir/name`, or the first of `dir/stem 2.ext`, `dir/stem 3.ext`, ... that is free.
pub fn unique_path(dir: &Path, name: &str) -> PathBuf {
    let first = dir.join(name);
    if !first.exists() {
        return first;
    }
    let p = Path::new(name);
    let stem = p.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_else(|| name.to_string());
    let ext = p.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
    (2..).map(|n| dir.join(format!("{stem} {n}{ext}"))).find(|c| !c.exists()).expect("a free name")
}

/// A temporary file's name: short whatever the target's name is (a name of 255 bytes leaves no room to add to it),
/// and never the same twice in a process (the process id and a counter; the file is also made with `create_new`).
fn temp_name() -> String {
    static COUNTER: AtomicU32 = AtomicU32::new(0);
    format!(".s0t-{:08x}{:08x}.tmp", std::process::id(), COUNTER.fetch_add(1, Ordering::Relaxed))
}

fn write_io(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let mut tries = 0;
    let (tmp, mut file) = loop {
        let tmp = path.with_file_name(temp_name());
        match std::fs::OpenOptions::new().write(true).create_new(true).open(&tmp) {
            Ok(file) => break (tmp, file),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists && tries < 16 => tries += 1,
            Err(e) => return Err(e),
        }
    };
    file.write_all(bytes)
        .and_then(|_| {
            // best effort: some volumes (SMB shares, some external disks) refuse the full sync macOS asks for, and the
            // rename below is what makes the write whole
            let _ = file.sync_all();
            std::fs::rename(&tmp, path)
        })
        .inspect_err(|_| {
            let _ = std::fs::remove_file(&tmp);
        })
}

/// Write through a temporary file in the same folder, so nothing ever sees half a file.
/// The SVG the frontend holds, as a PDF: a vector PDF, gradients kept, filters rasterised.
pub fn to_pdf(svg: &[u8]) -> Result<Vec<u8>, CommandError> {
    let refuse = || CommandError::bad_request("This vector could not be converted to PDF.");
    let text = std::str::from_utf8(svg).map_err(|_| refuse())?;
    let tree = svg2pdf::usvg::Tree::from_str(text, &svg2pdf::usvg::Options::default()).map_err(|_| refuse())?;
    svg2pdf::to_pdf(&tree, svg2pdf::ConversionOptions::default(), svg2pdf::PageOptions::default()).map_err(|_| refuse())
}

pub fn write_file(path: &Path, bytes: &[u8]) -> Result<(), CommandError> {
    write_io(path, bytes).map_err(|e| CommandError::io_write(path, &e))
}

/// A write that did not happen. `elsewhere` says the place is the trouble (a folder that is gone, read-only or
/// not ours), so another place may do; a refusal, a long name or a full disk will not be mended by one.
#[derive(Debug)]
pub struct WriteError {
    pub error: CommandError,
    pub elsewhere: bool,
}

fn same_file(a: &Path, b: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    if let (Ok(x), Ok(y)) = (std::fs::metadata(a), std::fs::metadata(b)) {
        return (x.dev(), x.ino()) == (y.dev(), y.ino());
    }
    // not there yet: the same folder and the same name
    let resolve = |p: &Path| Some(p.parent()?.canonicalize().ok()?.join(p.file_name()?));
    matches!((resolve(a), resolve(b)), (Some(x), Some(y)) if x == y)
}

/// [`write_file`] for an export of `original`'s trace: it refuses to write over the original itself.
pub fn save(path: &Path, bytes: &[u8], original: Option<&Path>) -> Result<(), WriteError> {
    if original.is_some_and(|o| same_file(path, o)) {
        return Err(WriteError { error: CommandError::new(400, "is_original", "That is the original image; choose another name."), elsewhere: false });
    }
    write_io(path, bytes).map_err(|e| {
        use std::io::ErrorKind::{NotFound, PermissionDenied, ReadOnlyFilesystem};
        WriteError { error: CommandError::io_write(path, &e), elsewhere: matches!(e.kind(), NotFound | PermissionDenied | ReadOnlyFilesystem) }
    })
}

/// The name a save panel suggests: `name`, unless that is the original's own file name (a 1× PNG of `logo.png`),
/// which would offer to replace it; then `stem.traced.ext`.
pub fn panel_name(name: &str, original: Option<&Path>) -> String {
    let same = original.and_then(|o| o.file_name()).is_some_and(|o| o.to_string_lossy().eq_ignore_ascii_case(name));
    if !same {
        return name.to_string();
    }
    let p = Path::new(name);
    match (p.file_stem(), p.extension()) {
        (Some(stem), Some(ext)) => format!("{}.traced.{}", stem.to_string_lossy(), ext.to_string_lossy()),
        _ => format!("{name}.traced"),
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct Item {
    pub id: String,
    pub name: String,
    pub svg: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Failure {
    pub id: String,
    pub name: String,
    pub message: String,
}

/// Export All's answer: what was written and what was not.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Written {
    pub written: Vec<String>,
    pub failed: Vec<Failure>,
}

/// Every item into `dir` under a free name; a write that fails is reported and the rest go on.
pub fn write_all(dir: &Path, items: &[Item]) -> Written {
    let mut answer = Written { written: Vec::new(), failed: Vec::new() };
    for item in items {
        let path = unique_path(dir, &safe_name(&item.name, "svg"));
        match write_file(&path, item.svg.as_bytes()) {
            Ok(()) => answer.written.push(path.display().to_string()),
            Err(e) => answer.failed.push(Failure { id: item.id.clone(), name: item.name.clone(), message: e.message().to_string() }),
        }
    }
    answer
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Destination {
    Ask,
    Beside,
}

impl Destination {
    pub fn parse(s: &str) -> Destination {
        if s == "beside" {
            Destination::Beside
        } else {
            Destination::Ask
        }
    }
}

/// Where an export goes without a panel: beside the original, when there is one and the setting says so.
pub fn beside(original: Option<&Path>, destination: Destination, name: &str) -> Option<PathBuf> {
    match (destination, original.and_then(Path::parent)) {
        (Destination::Beside, Some(dir)) => Some(unique_path(dir, name)),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("s0t-export-{tag}-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_vector_with_a_gradient_and_a_filter_becomes_a_pdf() {
        let svg = r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="g"><stop offset="0" stop-color="#f00"/><stop offset="1" stop-color="#00f"/></linearGradient><filter id="b"><feGaussianBlur stdDeviation="2"/></filter></defs><rect width="64" height="64" fill="url(#g)"/><circle cx="32" cy="32" r="10" filter="url(#b)"/></svg>"##;
        let pdf = to_pdf(svg.as_bytes()).unwrap();
        assert!(pdf.starts_with(b"%PDF-"));
        let text = String::from_utf8_lossy(&pdf);
        // the gradient is a PDF shading, still a vector; the blur is a raster image, the one thing a PDF cannot draw
        assert!(text.contains("/ShadingType"), "the gradient should be a shading");
        assert!(text.contains("/Subtype /Image") || text.contains("/Subtype/Image"), "the filter should be rasterised");
        if let Ok(path) = std::env::var("STUDI0TRACE_DUMP_PDF") {
            std::fs::write(path, &pdf).unwrap();
        }
    }

    #[test]
    fn what_is_not_an_svg_is_refused_as_such() {
        assert_eq!(to_pdf(b"<svg").unwrap_err().code(), Some("bad_request"));
        assert_eq!(to_pdf(&[0xff, 0xfe]).unwrap_err().code(), Some("bad_request"));
    }

    #[test]
    fn names_are_made_safe() {
        assert_eq!(safe_name("logo.svg", "svg"), "logo.svg");
        assert_eq!(safe_name("a/b:c.svg", "svg"), "a-b-c.svg");
        assert_eq!(safe_name("..hidden.svg", "svg"), "hidden.svg");
        assert_eq!(safe_name("   ", "png"), "Untitled.png");
    }

    #[test]
    fn copies_are_named_as_finder_names_them() {
        let dir = temp("unique");
        assert_eq!(unique_path(&dir, "logo.svg"), dir.join("logo.svg"));
        std::fs::write(dir.join("logo.svg"), "1").unwrap();
        assert_eq!(unique_path(&dir, "logo.svg"), dir.join("logo 2.svg"));
        std::fs::write(dir.join("logo 2.svg"), "2").unwrap();
        assert_eq!(unique_path(&dir, "logo.svg"), dir.join("logo 3.svg"));
        std::fs::write(dir.join("README"), "x").unwrap();
        assert_eq!(unique_path(&dir, "README"), dir.join("README 2"));
    }

    #[test]
    fn writes_whole_files_and_leaves_nothing_behind() {
        let dir = temp("write");
        let path = dir.join("out.svg");
        write_file(&path, b"<svg/>").unwrap();
        write_file(&path, b"<svg>2</svg>").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"<svg>2</svg>");
        let names: Vec<_> = std::fs::read_dir(&dir).unwrap().map(|e| e.unwrap().file_name()).collect();
        assert_eq!(names, vec![std::ffi::OsString::from("out.svg")]);
        assert_eq!(write_file(Path::new("/nonexistent-dir/x.svg"), b"x").unwrap_err().code(), Some("io_error"));
    }

    #[test]
    fn beside_the_original_only_when_asked_and_possible() {
        let dir = temp("beside");
        let original = dir.join("logo.png");
        assert_eq!(beside(Some(&original), Destination::Beside, "logo.svg"), Some(dir.join("logo.svg")));
        assert_eq!(beside(Some(&original), Destination::Ask, "logo.svg"), None);
        assert_eq!(beside(None, Destination::Beside, "logo.svg"), None);
        assert_eq!(Destination::parse("beside"), Destination::Beside);
        assert_eq!(Destination::parse("anything"), Destination::Ask);
    }

    #[test]
    fn a_png_that_would_be_named_like_the_original_is_named_traced() {
        let orig = Path::new("/p/logo.png");
        assert_eq!(panel_name("logo.png", Some(orig)), "logo.traced.png");
        assert_eq!(panel_name("LOGO.PNG", Some(orig)), "LOGO.traced.PNG");
        assert_eq!(panel_name("logo@2x.png", Some(orig)), "logo@2x.png");
        assert_eq!(panel_name("logo.svg", Some(orig)), "logo.svg");
        assert_eq!(panel_name("logo.png", None), "logo.png");
    }

    #[test]
    fn the_original_is_never_written_over() {
        let dir = temp("original");
        let original = dir.join("logo.png");
        std::fs::write(&original, b"original").unwrap();
        let refused = save(&original, b"x", Some(&original)).unwrap_err();
        assert_eq!(refused.error.code(), Some("is_original"));
        assert_eq!(refused.error.body["detail"]["message"], "That is the original image; choose another name.");
        assert!(!refused.elsewhere);
        // the same file by another spelling: a dot segment, a hard link, a symlink
        assert_eq!(save(&dir.join(".").join("logo.png"), b"x", Some(&original)).unwrap_err().error.code(), Some("is_original"));
        let link = dir.join("link.png");
        std::fs::hard_link(&original, &link).unwrap();
        assert_eq!(save(&link, b"x", Some(&original)).unwrap_err().error.code(), Some("is_original"));
        let sym = dir.join("sym.png");
        std::os::unix::fs::symlink(&original, &sym).unwrap();
        assert_eq!(save(&sym, b"x", Some(&original)).unwrap_err().error.code(), Some("is_original"));
        assert_eq!(std::fs::read(&original).unwrap(), b"original");
        // another name is fine, and so is no original at all
        save(&dir.join("logo.traced.png"), b"traced", Some(&original)).unwrap();
        save(&dir.join("other.png"), b"o", None).unwrap();
        assert_eq!(std::fs::read(dir.join("logo.traced.png")).unwrap(), b"traced");
    }

    #[test]
    fn a_name_of_the_longest_length_writes() {
        let dir = temp("long");
        let name = format!("{}.svg", "n".repeat(251)); // 255 bytes: the most a name can be
        let path = dir.join(&name);
        write_file(&path, b"<svg/>").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"<svg/>");
        write_file(&path, b"<svg>2</svg>").unwrap();
        let names: Vec<_> = std::fs::read_dir(&dir).unwrap().map(|e| e.unwrap().file_name().len()).collect();
        assert_eq!(names, vec![255]);
    }

    #[test]
    fn temporary_names_are_short_and_do_not_repeat() {
        let a = temp_name();
        let b = temp_name();
        assert_ne!(a, b);
        for n in [&a, &b] {
            assert!(n.starts_with(".s0t-") && n.ends_with(".tmp") && n.len() == ".s0t-".len() + 16 + ".tmp".len(), "{n}");
            assert!(n[5..21].chars().all(|c| c.is_ascii_hexdigit()));
        }
    }

    #[test]
    fn a_folder_that_cannot_be_written_asks_for_another_place() {
        let dir = temp("fallback");
        // a folder that is gone
        let gone = save(&dir.join("vanished").join("x.svg"), b"x", None).unwrap_err();
        assert!(gone.elsewhere);
        assert!(gone.error.body["detail"]["message"].as_str().unwrap().contains("could not be found"));
        // a folder that is read-only
        use std::os::unix::fs::PermissionsExt;
        let ro = dir.join("ro");
        std::fs::create_dir_all(&ro).unwrap();
        std::fs::set_permissions(&ro, std::fs::Permissions::from_mode(0o555)).unwrap();
        let denied = save(&ro.join("x.svg"), b"x", None);
        std::fs::set_permissions(&ro, std::fs::Permissions::from_mode(0o755)).unwrap();
        match denied {
            Err(e) => {
                assert!(e.elsewhere);
                assert_eq!(e.error.body["detail"]["message"], "Studi0Trace cannot write to the folder of \u{201c}x.svg\u{201d}.");
                assert!(!ro.join("x.svg").exists());
            }
            // root can write into a folder without write permission: nothing to assert about a refusal
            Ok(()) => eprintln!("skipped the read-only folder check: this user can write there anyway (root?)"),
        }
        // a failure that another place would not mend does not ask for one
        let long = save(&dir.join(format!("{}.svg", "n".repeat(300))), b"x", None).unwrap_err();
        assert!(!long.elsewhere);
    }

    #[test]
    fn export_all_writes_what_it_can_and_names_what_it_could_not() {
        let dir = temp("all");
        std::fs::write(dir.join("a.svg"), "old").unwrap();
        let items = vec![
            Item { id: "1".into(), name: "a.svg".into(), svg: "<svg>1</svg>".into() },
            Item { id: "2".into(), name: format!("{}.svg", "n".repeat(300)), svg: "<svg>2</svg>".into() },
            Item { id: "3".into(), name: "c.svg".into(), svg: "<svg>3</svg>".into() },
        ];
        let answer = write_all(&dir, &items);
        assert_eq!(answer.written, vec![dir.join("a 2.svg").display().to_string(), dir.join("c.svg").display().to_string()]);
        assert_eq!(answer.failed.len(), 1);
        assert_eq!((answer.failed[0].id.as_str(), answer.failed[0].name.as_str()), ("2", items[1].name.as_str()));
        assert!(!answer.failed[0].message.contains(&dir.display().to_string()));
        assert_eq!(std::fs::read(dir.join("a.svg")).unwrap(), b"old");
        assert_eq!(std::fs::read(dir.join("c.svg")).unwrap(), b"<svg>3</svg>");
    }
}
