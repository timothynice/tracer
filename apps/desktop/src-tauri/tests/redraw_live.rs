//! The one test that calls OpenAI, by hand and on your bill:
//! `OPENAI_API_KEY=… cargo test -p studi0trace-desktop --release -- --ignored live_redraw --nocapture`
//! (`REDRAW_MODEL=gpt-image-1.5` tries the other model). It redraws a core fixture through `redraw::openai`,
//! `geometry` and `drift`, writes the redraw to the temp folder and prints its size, drift and verdict.
use std::sync::Arc;
use std::time::Instant;
use studi0trace_desktop::redraw::{self, geometry, openai, Model, Options, Quality, Source};

#[test]
#[ignore = "calls OpenAI with OPENAI_API_KEY, billed to that account; run it by hand"]
fn live_redraw() {
    // without a key there is nothing to call: skip, never fail
    let Some(key) = std::env::var("OPENAI_API_KEY").ok().filter(|k| !k.trim().is_empty()) else {
        eprintln!("live_redraw: OPENAI_API_KEY is not set; skipped");
        return;
    };
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../crates/studi0trace-core/tests/fixtures/scorecard_heldout_u2049_jpeg_source.png");
    let source = Arc::new(Source::decode(&std::fs::read(&path).unwrap()).unwrap());
    let options = Options { model: Model::parse(&std::env::var("REDRAW_MODEL").unwrap_or_default()), quality: Quality::Medium };
    let api = openai::Api::new(&openai::base_url(), &key).unwrap();
    let started = Instant::now();
    let done = tauri::async_runtime::block_on(redraw::redraw(&api, source.clone(), options, Arc::new(|p| eprintln!("phase: {p:?}"))))
        .unwrap_or_else(|e| panic!("{}: {}", e.code().unwrap_or("?"), e.message()));
    let out = std::env::temp_dir().join("studi0trace-live-redraw.png");
    std::fs::write(&out, &done.png).unwrap();
    println!(
        "{} {}x{} -> {}x{} in {:.1} s; Edges matched: {:.0} %, Colour shift: ΔE {:.1}, {:?}; written to {}",
        options.model.id(),
        source.width,
        source.height,
        done.width,
        done.height,
        started.elapsed().as_secs_f64(),
        done.drift.edge_f1 * 100.0,
        done.drift.delta_e,
        done.drift.verdict,
        out.display()
    );
    assert_eq!((done.width, done.height), geometry::final_size(source.width, source.height));
}
