//! Where the fixtures came from (`tests/fixtures/provenance.json`, written by
//! `backend/tools/export_core_fixtures.py`). The exact comparisons of the other tests are true of that
//! environment and no other, so this prints it (`cargo test --test provenance -- --nocapture`), and holds
//! the one thing `common::exact()` assumes of it.
mod common;

#[test]
fn the_fixtures_say_what_they_were_made_with() {
    let record = common::fixture_json("provenance.json");
    let exporters = record["exporters"].as_object().expect("provenance.json lists the exporters");
    assert!(!exporters.is_empty());
    for (name, r) in exporters {
        let env = &r["environment"];
        println!("{name:<10} {} {} | python {} numpy {} scipy {} scikit-image {} Pillow {} resvg-py {} | engine {} | commit {}{}",
            env["system"], env["machine"], env["python"], env["numpy"], env["scipy"], env["scikit-image"], env["Pillow"], env["resvg-py"],
            env["vexel_backend"], r["commit"], if r["sources_differ_from_commit"] == true { " (+ uncommitted changes)" } else { "" });
        // what `common::exact()` takes for granted
        assert_eq!((env["system"].as_str(), env["machine"].as_str(), env["vexel_backend"].as_str()), (Some("Darwin"), Some("arm64"), Some("rust")), "{name}");
    }
}
