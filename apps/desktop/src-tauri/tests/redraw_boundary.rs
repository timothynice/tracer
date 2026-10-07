//! What AI redraw must not touch: the webview's CSP gains no `connect-src` (the capabilities are held by
//! updater_config.rs), the trace worker and the core never reach the network, and every redraw command is
//! registered.
use std::fs;
use std::path::Path;

fn read(file: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(file);
    fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

#[test]
fn the_webview_csp_is_unchanged_and_has_no_connect_src() {
    let conf: serde_json::Value = serde_json::from_str(&read("tauri.conf.json")).unwrap();
    let csp = conf["app"]["security"]["csp"].as_str().unwrap();
    assert!(!csp.contains("connect-src"), "{csp}");
    assert_eq!(csp, "default-src 'self' ipc: http://ipc.localhost; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:");
}

#[test]
fn the_trace_worker_and_the_core_stay_offline() {
    let worker = read("src/worker.rs");
    for word in ["reqwest", "redraw", "keychain", "openai"] {
        assert!(!worker.contains(word), "worker.rs names {word}");
    }
    let main = read("src/main.rs");
    for word in ["redraw", "keychain"] {
        assert!(!main.contains(word), "main.rs names {word}");
    }
    let core = read("../../../crates/studi0trace-core/Cargo.toml");
    for dep in ["reqwest", "hyper", "rustls", "security-framework"] {
        assert!(!core.contains(dep), "the core depends on {dep}");
    }
}

#[test]
fn every_redraw_command_is_registered() {
    let lib = read("src/lib.rs");
    for cmd in ["redraw_key_status", "set_redraw_key", "delete_redraw_key", "image_roughness", "redraw_image", "cancel_redraw", "accept_redraw", "discard_redraw", "revert_redraw"] {
        assert!(lib.contains(&format!("commands::{cmd},")), "{cmd} is not in the handler list");
    }
}
