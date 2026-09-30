mod common;

#[test]
fn the_crate_links_against_the_engine() {
    assert!(!studi0trace_core::VERSION.is_empty());
    let svg = vexel_rs::engine::trace_rgba(&[255, 0, 0, 255].repeat(16 * 16), 16, 16, &vexel_rs::engine::VexelParams::default());
    assert!(svg.starts_with("<svg"));
}
