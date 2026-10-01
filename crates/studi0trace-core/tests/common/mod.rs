// Shared by several test binaries, none of which uses every helper.
#![allow(dead_code)]

use std::path::PathBuf;

pub fn fixture_path(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(name)
}

pub fn fixture_json(name: &str) -> serde_json::Value {
    let text = std::fs::read_to_string(fixture_path(name)).unwrap_or_else(|e| panic!("{name}: {e}; run python -m tools.export_core_fixtures"));
    serde_json::from_str(&text).unwrap()
}

pub fn fixture_bytes(name: &str) -> Vec<u8> {
    std::fs::read(fixture_path(name)).unwrap_or_else(|e| panic!("{name}: {e}"))
}

/// The repo's backend directory, for corpus images the fixtures name.
pub fn backend(rel: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend").join(rel)
}

/// Whether results that went through libm (`atan2`, `cos`, `sin`, ...) are compared to the bit:
/// on macOS on arm64, where the fixtures were exported with Apple's libm, unless
/// `STUDI0TRACE_FORCE_TOLERANT` is set (which runs the tolerant branch there too). Elsewhere a
/// libm may round an ulp the other way, and those results are held to a tolerance.
pub fn exact() -> bool {
    cfg!(all(target_os = "macos", target_arch = "aarch64")) && std::env::var_os("STUDI0TRACE_FORCE_TOLERANT").is_none()
}

/// Lower-case hex SHA-256, for fixtures too large to keep as bytes.
pub fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes).iter().map(|b| format!("{b:02x}")).collect()
}
