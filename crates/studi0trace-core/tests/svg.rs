mod common;
use studi0trace_core::svg;

#[test]
fn finishing_and_stats_match_the_python() {
    for case in common::fixture_json("svg.json").as_array().unwrap() {
        let s = case["svg"].as_str().unwrap();
        assert_eq!(svg::normalize_dimensions(s, 64, 32), case["normalized"].as_str().unwrap());
        assert_eq!(serde_json::to_value(svg::stats(s)).unwrap(), case["stats"]);
    }
}
