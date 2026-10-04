//! The third-party notices: the generated file is committed, bundled as a resource and names what the app is
//! built from (`scripts/notices.sh` regenerates it).

use std::fs;
use std::path::Path;

fn read(file: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(file);
    fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

#[test]
fn the_notices_name_the_crates_the_packages_and_the_font() {
    let html = read("resources/THIRD_PARTY_NOTICES.html");
    // the entries themselves, not words a licence text happens to contain
    for entry in ["<li>tauri 2.", "<li>serde 1.", "<h3>react <span", "<h3>react-dom <span", "<h3>Poppins <span", "<h3>MIT License</h3>", "SIL Open Font License"] {
        assert!(html.contains(entry), "the notices have no entry {entry}");
    }
    // a package that ships no licence file still carries its notice, with its author as the holder
    assert!(!html.contains("No licence file is shipped"), "a package without a licence text");
    assert!(html.contains("<h3>react-remove-scroll-bar <span") && html.contains("Copyright (c) Anton Korzunov"));
    assert!(html.contains("Permission is hereby granted, free of charge"), "no licence text");
    assert!(!html.contains("/Users/"), "an absolute path of the machine that generated it");
}

#[test]
fn the_notices_ship_in_the_bundle_under_their_own_name() {
    let conf: serde_json::Value = serde_json::from_str(&read("tauri.conf.json")).expect("valid JSON");
    assert_eq!(conf["bundle"]["resources"], serde_json::json!({ "resources/THIRD_PARTY_NOTICES.html": "THIRD_PARTY_NOTICES.html" }));
}
