//! Trace an image with the core, as the app would, and write the SVG to stdout.
//!
//!     cargo run -p studi0trace-core --release --example trace -- IMAGE [PRESET|auto] > out.svg
//!
//! `PRESET` is the id of a preset (`balanced`, `logo`, `detailed`, `dense`, `flat`, `cutfile`, as
//! `GET /presets` lists them); its parameters go through [`Core::vectorize`] like the UI's do.
//! `auto` (the default) traces with every candidate, scores them, and keeps the cleanest of the
//! most faithful; what it chose and why goes to stderr. Exits 2 for a call it cannot read, 1 for
//! a request the core refused or a trace that failed.
use std::io::Write;
use std::process::exit;

use serde_json::{json, Value};
use studi0trace_core::api::Core;
use studi0trace_core::presets;

fn usage() -> ! {
    let ids: Vec<String> = presets::all().into_iter().map(|p| p.id).collect();
    eprintln!("usage: trace IMAGE [PRESET|auto] > out.svg\n  PRESET: {} (default: auto)", ids.join(", "));
    exit(2);
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (path, mode) = match args.as_slice() {
        [path] => (path.as_str(), "auto"),
        [path, mode] => (path.as_str(), mode.as_str()),
        _ => usage(),
    };
    let Some(preset) = presets::by_id(mode) else { usage() };
    let auto = preset.kind == "auto";
    let parameters = if auto { json!({}) } else { Value::Object(preset.params) };

    let bytes = std::fs::read(path).unwrap_or_else(|e| {
        eprintln!("trace: cannot read {path}: {e}");
        exit(1);
    });
    let core = Core::new();
    let fail = |e: studi0trace_core::api::ApiError| -> ! {
        eprintln!("trace: {e}");
        exit(1);
    };
    let uploaded = core.upload(&bytes).unwrap_or_else(|e| fail(e));
    let id = uploaded["image_id"].as_str().expect("an upload has an id");
    let response = core.vectorize(id, &parameters, auto).unwrap_or_else(|e| fail(e));

    if auto {
        let result = &response["auto"]["vexel"];
        let label = result["candidates"]
            .as_array()
            .and_then(|all| all.iter().find(|c| c["preset"] == result["pick"]))
            .and_then(|c| c["label"].as_str());
        let reason = result["reason"].as_str().unwrap_or("");
        match label {
            Some(label) => eprintln!("Auto chose {label} \u{2014} {reason}"),
            None => eprintln!("Auto could not choose \u{2014} {reason}"),
        }
    }
    let engine = &response["results"]["vexel"];
    let Some(svg) = engine["svg"].as_str() else {
        eprintln!("trace: {}: {}", engine["error"]["code"].as_str().unwrap_or("engine_failed"), engine["error"]["message"].as_str().unwrap_or("no SVG"));
        exit(1);
    };
    eprintln!("{}x{}, {} paths, {} bytes, {:.0} ms", response["width"], response["height"], engine["stats"]["paths"], svg.len(), engine["elapsed_ms"].as_f64().unwrap_or(0.0));
    if let Err(e) = std::io::stdout().write_all(svg.as_bytes()) {
        eprintln!("trace: cannot write the SVG: {e}");
        exit(1);
    }
}
