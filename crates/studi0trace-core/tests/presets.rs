mod common;
use studi0trace_core::presets;

#[test]
fn presets_are_the_apis_list() {
    let got = serde_json::to_value(presets::all()).unwrap();
    assert_eq!(got, common::fixture_json("presets.json"));
}

#[test]
fn the_list_is_in_the_apis_order_with_the_apis_keys_in_the_apis_order() {
    // `assert_eq!` on a `Value` ignores key order; the frontend gets the JSON as written, and
    // FastAPI writes a model's fields as declared and a dict as it was built.
    let got = serde_json::to_value(presets::all()).unwrap();
    let list = got.as_array().unwrap();
    let ids: Vec<&str> = list.iter().map(|p| p["id"].as_str().unwrap()).collect();
    assert_eq!(ids, ["auto", "balanced", "logo", "detailed", "dense", "flat", "cutfile"]);
    for p in list {
        let keys: Vec<&str> = p.as_object().unwrap().keys().map(String::as_str).collect();
        assert_eq!(keys, ["id", "label", "engine", "description", "detail", "sample", "params", "kind", "auto_candidate"]);
    }
    let logo = presets::by_id("logo").unwrap();
    let params: Vec<&str> = logo.params.keys().map(String::as_str).collect();
    assert_eq!(params, ["detail", "min_region", "curve_tolerance", "corner_threshold"]);
}

#[test]
fn auto_tries_its_candidates_in_order() {
    let ids: Vec<String> = presets::auto_candidates().into_iter().map(|p| p.id).collect();
    assert_eq!(ids, ["balanced", "logo", "detailed", "dense"]);
}

#[test]
fn a_preset_is_found_by_its_id() {
    let auto = presets::by_id("auto").expect("auto");
    assert_eq!(auto.kind, "auto");
    assert!(auto.params.is_empty());
    // "{candidates}" is filled from the candidates' own labels, in preference order.
    assert!(!auto.description.contains("{candidates}"), "{}", auto.description);
    assert!(auto.description.contains("Balanced, Logo & icon, Detailed illustration and Simplified"), "{}", auto.description);
    let logo = presets::by_id("logo").expect("logo");
    assert_eq!((logo.kind.as_str(), logo.auto_candidate), ("preset", true));
    assert!(presets::by_id("nope").is_none());
}
