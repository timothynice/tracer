//! Writing exports. The UI hands over the bytes and a suggested name; this side picks the place (a save panel,
//! or beside the original, named as Finder names copies) and writes, so the webview never names a path to write.
use crate::error::CommandError;
use std::path::{Path, PathBuf};


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

/// Write through a temporary file in the same folder, so nothing ever sees half a file.
pub fn write_file(path: &Path, bytes: &[u8]) -> Result<(), CommandError> {
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let tmp = path.with_file_name(format!(".{name}.studi0trace-tmp"));
    std::fs::write(&tmp, bytes).and_then(|_| std::fs::rename(&tmp, path)).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        CommandError::io(path, &e)
    })
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
}
