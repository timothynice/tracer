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
