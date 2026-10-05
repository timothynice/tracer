//! One version: package.json, tauri.conf.json and Cargo.toml say the same thing.
//! `scripts/version.sh X.Y.Z` sets all of them (and Cargo.lock and package-lock.json).

use std::fs;
use std::path::Path;

fn json_version(file: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(file);
    let text = fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let value: serde_json::Value = serde_json::from_str(&text).expect("valid JSON");
    value["version"]
        .as_str()
        .unwrap_or_else(|| panic!("{file} has no string \"version\""))
        .to_string()
}

#[test]
fn tauri_conf_version_is_the_crate_version() {
    assert_eq!(json_version("tauri.conf.json"), env!("CARGO_PKG_VERSION"));
}

#[test]
fn package_json_version_is_the_crate_version() {
    assert_eq!(json_version("../package.json"), env!("CARGO_PKG_VERSION"));
}

#[test]
fn package_lock_version_is_the_crate_version() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../package-lock.json");
    let text = fs::read_to_string(path).expect("package-lock.json");
    let lock: serde_json::Value = serde_json::from_str(&text).expect("valid JSON");
    assert_eq!(lock["version"], env!("CARGO_PKG_VERSION"));
    assert_eq!(lock["packages"][""]["version"], env!("CARGO_PKG_VERSION"));
}
