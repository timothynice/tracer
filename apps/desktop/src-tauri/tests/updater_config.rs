//! The updater's configuration: the one endpoint and the app's own public key, updater artifacts only in the
//! release config (a local build has no private key to sign them with), and no updater permission for the webview.

use std::fs;
use std::path::Path;

fn json(file: &str) -> serde_json::Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(file);
    let text = fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    serde_json::from_str(&text).expect("valid JSON")
}

#[test]
fn updates_come_from_the_latest_github_release_signed_with_the_apps_key() {
    let conf = json("tauri.conf.json");
    let updater = &conf["plugins"]["updater"];
    // the signature's trusted comment carries the version it was signed for: a crafted latest.json cannot pair a
    // higher version with an older, genuinely signed artifact
    assert_eq!(updater["requireSignedVersion"], true);
    assert_eq!(updater["endpoints"], serde_json::json!(["https://github.com/timothynice/tracer/releases/latest/download/latest.json"]));
    assert_eq!(
        updater["pubkey"],
        "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDE1QTI0NDhGMThEQjlBNDMKUldSRG10c1lqMFNpRlNUN3hRNVNwSWZRcGM5SXlTNTZ0RlNjYlRxcGFkODJKZWJYYVpJWXpCVk0K"
    );
}

#[test]
fn only_the_release_config_makes_updater_artifacts() {
    assert!(json("tauri.conf.json")["bundle"].get("createUpdaterArtifacts").is_none());
    assert_eq!(json("tauri.release.conf.json"), serde_json::json!({ "bundle": { "createUpdaterArtifacts": true } }));
}

#[test]
fn the_webview_gets_no_updater_permission() {
    let caps = json("capabilities/default.json");
    assert_eq!(caps["permissions"], serde_json::json!(["core:default", "core:window:allow-start-dragging"]));
}
