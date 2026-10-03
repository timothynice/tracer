//! What a command answers when it fails: the status and the body the Python server would have sent
//! (`{"detail": {"code", "message"}}`, or the 422's list), so the UI reads one shape whoever answered.
use serde::Serialize;
use serde_json::{json, Value};
use std::path::Path;
use studi0trace_core::api::ApiError;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CommandError {
    pub status: u16,
    pub body: Value,
}

impl CommandError {
    pub fn new(status: u16, code: &str, message: impl Into<String>) -> Self {
        CommandError { status, body: json!({ "detail": { "code": code, "message": message.into() } }) }
    }

    pub fn code(&self) -> Option<&str> {
        self.body["detail"]["code"].as_str()
    }

    /// The words the user reads.
    pub fn message(&self) -> &str {
        self.body["detail"]["message"].as_str().unwrap_or_default()
    }

    pub fn cancelled() -> Self {
        Self::new(499, "cancelled", "The trace was cancelled")
    }

    pub fn crashed(why: impl std::fmt::Display) -> Self {
        Self::new(500, "engine_crashed", format!("The trace crashed ({why})"))
    }

    /// The worker could not be started. Its binary is the app's own, found where the app was launched from: gone
    /// from there, the app was moved (or deleted) while it ran, and only a fresh launch finds it again.
    pub fn spawn(err: &std::io::Error) -> Self {
        if err.kind() == std::io::ErrorKind::NotFound {
            Self::new(500, "app_moved", "Studi0Trace was moved while it was open. Quit and open it again.")
        } else {
            Self::crashed(format!("the worker did not start: {err}"))
        }
    }

    pub fn conversion(name: &str, why: impl std::fmt::Display) -> Self {
        Self::new(400, "conversion_failed", format!("macOS could not convert {name}: {why}"))
    }

    /// A file that could not be read, in words for a person: the file's name, never its path or the errno (those
    /// go to stderr).
    pub fn io(path: &Path, err: &std::io::Error) -> Self {
        Self::file_error(path, err, false)
    }

    /// A file that could not be written (see [`CommandError::io`]).
    pub fn io_write(path: &Path, err: &std::io::Error) -> Self {
        Self::file_error(path, err, true)
    }

    fn file_error(path: &Path, err: &std::io::Error, writing: bool) -> Self {
        use std::io::ErrorKind::{NotFound, PermissionDenied, ReadOnlyFilesystem};
        eprintln!("studi0trace: {} {}: {err}", if writing { "writing" } else { "reading" }, path.display());
        let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| path.display().to_string());
        let message = match err.kind() {
            NotFound => format!("\u{201c}{name}\u{201d} could not be found. It may have been moved or deleted."),
            PermissionDenied | ReadOnlyFilesystem if writing => format!("Studi0Trace cannot write to the folder of \u{201c}{name}\u{201d}."),
            PermissionDenied | ReadOnlyFilesystem => format!("Studi0Trace cannot read \u{201c}{name}\u{201d}."),
            _ => format!("\u{201c}{name}\u{201d}: {}", without_errno(&err.to_string())),
        };
        Self::new(500, "io_error", message)
    }

    /// The core's own words for an id it does not hold.
    pub fn expired() -> Self {
        Self::new(404, "image_expired", "Upload expired or unknown; upload it again")
    }

    pub fn bad_request(why: impl Into<String>) -> Self {
        Self::new(400, "bad_request", why)
    }
}

/// `Permission denied (os error 13)` → `Permission denied`.
fn without_errno(text: &str) -> &str {
    match text.rfind(" (os error ") {
        Some(i) if text.ends_with(')') => &text[..i],
        _ => text,
    }
}

impl From<ApiError> for CommandError {
    fn from(e: ApiError) -> Self {
        CommandError { status: e.status, body: e.response_body() }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_core_refusal_keeps_its_status_and_body() {
        let core = studi0trace_core::api::Core::new();
        let refused = core.upload(b"not an image").unwrap_err();
        let e = CommandError::from(refused.clone());
        assert_eq!((e.status, &e.body), (refused.status, &refused.response_body()));
        assert_eq!(e.code(), Some("unsupported_format"));
    }

    #[test]
    fn a_worker_that_cannot_be_found_means_the_app_was_moved() {
        let moved = CommandError::spawn(&std::io::Error::from(std::io::ErrorKind::NotFound));
        assert_eq!(moved.code(), Some("app_moved"));
        assert_eq!(moved.body["detail"]["message"], "Studi0Trace was moved while it was open. Quit and open it again.");
        let other = CommandError::spawn(&std::io::Error::from(std::io::ErrorKind::PermissionDenied));
        assert_eq!(other.code(), Some("engine_crashed"));
    }

    fn kind(k: std::io::ErrorKind) -> std::io::Error {
        std::io::Error::from(k)
    }
    fn words(e: &CommandError) -> &str {
        e.body["detail"]["message"].as_str().unwrap()
    }

    #[test]
    fn a_file_that_is_not_there_says_it_may_have_moved() {
        let p = Path::new("/Users/t/Pictures/old/logo.png");
        for e in [CommandError::io(p, &kind(std::io::ErrorKind::NotFound)), CommandError::io_write(p, &kind(std::io::ErrorKind::NotFound))] {
            assert_eq!(e.code(), Some("io_error"));
            assert_eq!(words(&e), "\u{201c}logo.png\u{201d} could not be found. It may have been moved or deleted.");
        }
    }

    #[test]
    fn permission_and_read_only_name_the_file_and_not_the_path() {
        let p = Path::new("/Volumes/Disc/ro-logo.svg");
        let denied = kind(std::io::ErrorKind::PermissionDenied);
        let read_only = std::io::Error::from_raw_os_error(30); // EROFS
        assert_eq!(words(&CommandError::io_write(p, &denied)), "Studi0Trace cannot write to the folder of \u{201c}ro-logo.svg\u{201d}.");
        assert_eq!(words(&CommandError::io_write(p, &read_only)), "Studi0Trace cannot write to the folder of \u{201c}ro-logo.svg\u{201d}.");
        assert_eq!(words(&CommandError::io(p, &denied)), "Studi0Trace cannot read \u{201c}ro-logo.svg\u{201d}.");
        assert_eq!(words(&CommandError::io(p, &read_only)), "Studi0Trace cannot read \u{201c}ro-logo.svg\u{201d}.");
    }

    #[test]
    fn anything_else_keeps_its_reason_without_the_errno() {
        let p = Path::new("/a/b/c/long.svg");
        let too_long = std::io::Error::from_raw_os_error(63); // ENAMETOOLONG
        let m = words(&CommandError::io_write(p, &too_long)).to_string();
        assert!(m.starts_with("\u{201c}long.svg\u{201d}: ") && !m.contains("os error") && !m.contains("/a/b"), "{m}");
        let plain = std::io::Error::new(std::io::ErrorKind::Other, "disk is on fire");
        assert_eq!(words(&CommandError::io(p, &plain)), "\u{201c}long.svg\u{201d}: disk is on fire");
    }

    #[test]
    fn the_apps_own_codes() {
        assert_eq!(CommandError::cancelled().code(), Some("cancelled"));
        assert_eq!(CommandError::crashed("signal 9").code(), Some("engine_crashed"));
        assert_eq!(CommandError::expired().code(), Some("image_expired"));
        let e = CommandError::io(std::path::Path::new("/nope/x.png"), &std::io::Error::from(std::io::ErrorKind::NotFound));
        assert_eq!(e.code(), Some("io_error"));
        assert!(!words(&e).contains("/nope"));
    }
}
