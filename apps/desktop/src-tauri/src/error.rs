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

    pub fn io(path: &Path, err: &std::io::Error) -> Self {
        Self::new(500, "io_error", format!("{}: {err}", path.display()))
    }

    /// The core's own words for an id it does not hold.
    pub fn expired() -> Self {
        Self::new(404, "image_expired", "Upload expired or unknown; upload it again")
    }

    pub fn bad_request(why: impl Into<String>) -> Self {
        Self::new(400, "bad_request", why)
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

    #[test]
    fn the_apps_own_codes() {
        assert_eq!(CommandError::cancelled().code(), Some("cancelled"));
        assert_eq!(CommandError::crashed("signal 9").code(), Some("engine_crashed"));
        assert_eq!(CommandError::expired().code(), Some("image_expired"));
        let e = CommandError::io(std::path::Path::new("/nope/x.png"), &std::io::Error::from(std::io::ErrorKind::NotFound));
        assert_eq!(e.code(), Some("io_error"));
        assert!(e.body["detail"]["message"].as_str().unwrap().starts_with("/nope/x.png: "));
    }
}
