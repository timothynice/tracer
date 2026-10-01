mod common;
use serde_json::{json, Value};
use studi0trace_core::params::{self, ParamError};

/// The error `parse` gives for `values`. (`Result::unwrap_err` needs the `Ok` side
/// to be `Debug`, which the engine's `VexelParams` is not.)
fn refused(values: Value) -> ParamError {
    match params::parse(&values) {
        Err(e) => e,
        Ok(_) => panic!("parse accepted {values}"),
    }
}

#[test]
fn the_schema_is_the_pydantic_one() {
    let golden = common::fixture_json("schema.json");
    assert_eq!(params::schema(), golden["schema"]);
    assert_eq!(serde_json::Value::Object(params::defaults()), golden["defaults"]);
}

#[test]
fn the_controls_come_out_in_the_order_the_fields_are_declared() {
    // The UI lays its controls out in `properties` order, which is the order
    // Pydantic declares the fields in; the fixture's own keys are sorted.
    let golden = common::fixture_json("schema.json");
    let want: Vec<&str> = golden["order"].as_array().unwrap().iter().map(|v| v.as_str().unwrap()).collect();
    let schema = params::schema();
    let got: Vec<&str> = schema["properties"].as_object().unwrap().keys().map(String::as_str).collect();
    assert_eq!(got, want);
    let names: Vec<&str> = params::fields().iter().map(|f| f.name).collect();
    assert_eq!(names, want);
    let defaults = params::defaults();
    assert_eq!(defaults.keys().map(String::as_str).collect::<Vec<_>>(), want);
}

#[test]
fn defaults_parse_to_the_engine_defaults() {
    let p = params::parse(&json!({})).unwrap();
    let d = vexel_rs::engine::VexelParams::default();
    assert_eq!((p.detail, p.min_region, p.curve_tolerance, p.layering.as_str()), (d.detail, d.min_region, d.curve_tolerance, d.layering.as_str()));
}

#[test]
fn every_default_in_the_table_is_the_engines_default() {
    // `parse` of the table's own defaults must land on `VexelParams::default()`
    // field for field: the table and the engine are two copies of one number.
    let via_table = params::parse(&serde_json::Value::Object(params::defaults())).unwrap();
    let d = vexel_rs::engine::VexelParams::default();
    assert_eq!(
        (via_table.upsample.as_str(), via_table.detail, via_table.min_region, via_table.gradients, via_table.max_stops, via_table.layering.as_str()),
        (d.upsample.as_str(), d.detail, d.min_region, d.gradients, d.max_stops, d.layering.as_str()),
    );
    assert_eq!(
        (via_table.corner_threshold, via_table.curve_tolerance, via_table.shape_fitting, via_table.refine, via_table.strokes),
        (d.corner_threshold, d.curve_tolerance, d.shape_fitting, d.refine, d.strokes),
    );
    assert_eq!(
        (via_table.shadows, via_table.stroke_tolerance, via_table.overlaps, via_table.path_precision),
        (d.shadows, d.stroke_tolerance, d.overlaps, d.path_precision),
    );
}

#[test]
fn out_of_range_and_unknown_fields_are_refused_with_the_field_named() {
    let e = refused(json!({"detail": 0.5}));
    assert_eq!((e.code, e.field.as_str()), ("validation_error", "detail"));
    let e = refused(json!({"layering": "sideways"}));
    assert_eq!(e.field, "layering");
    let e = refused(json!({"colour": 3}));
    assert_eq!(e.field, "colour");
}

#[test]
fn integers_accept_whole_floats_as_pydantic_does() {
    assert_eq!(params::parse(&json!({"min_region": 16.0})).unwrap().min_region, 16);
    assert!(params::parse(&json!({"min_region": 16.5})).is_err());
}

#[test]
fn every_value_the_engine_reads_is_carried_through() {
    let p = params::parse(&json!({
        "upsample": "never", "detail": 12.5, "min_region": 20, "gradients": false, "max_stops": 7,
        "layering": "cutout", "corner_threshold": 90.0, "curve_tolerance": 1.25, "shape_fitting": false,
        "refine": true, "strokes": false, "shadows": false, "stroke_tolerance": 0.5, "overlaps": false,
        "path_precision": 3,
    }))
    .unwrap();
    assert_eq!((p.upsample.as_str(), p.detail, p.min_region, p.gradients, p.max_stops, p.layering.as_str()), ("never", 12.5, 20, false, 7, "cutout"));
    assert_eq!((p.corner_threshold, p.curve_tolerance, p.shape_fitting, p.refine, p.strokes), (90.0, 1.25, false, true, false));
    assert_eq!((p.shadows, p.stroke_tolerance, p.overlaps, p.path_precision), (false, 0.5, false, 3));
}

#[test]
fn the_bounds_are_inclusive_and_a_wrong_type_is_refused() {
    assert!(params::parse(&json!({"detail": 1.0})).is_ok());
    assert!(params::parse(&json!({"detail": 40.0})).is_ok());
    assert_eq!(refused(json!({"detail": 40.5})).field, "detail");
    assert_eq!(refused(json!({"path_precision": 5})).field, "path_precision");
    assert_eq!(refused(json!({"stroke_tolerance": 0.04})).field, "stroke_tolerance");
    assert_eq!(refused(json!({"gradients": "yes"})).field, "gradients");
    assert_eq!(refused(json!({"detail": "6"})).field, "detail");
    assert_eq!(refused(json!({"detail": null})).field, "detail");
    assert_eq!(refused(json!([1, 2])).code, "validation_error");
}

#[test]
fn a_dump_is_pydantics_model_dump_in_the_order_the_fields_are_declared() {
    // the defaults, and a parse of values whose kinds differ from the field's (an integer for a float)
    let golden = common::fixture_json("schema.json");
    assert_eq!(Value::Object(params::dump(&vexel_rs::engine::VexelParams::default())), golden["defaults"]);
    let parsed = params::parse(&json!({"detail": 10, "min_region": 16.0, "strokes": false})).unwrap();
    let dumped = params::dump(&parsed);
    assert_eq!(dumped["detail"], json!(10.0));
    assert!(dumped["detail"].is_f64() && dumped["min_region"].is_u64());
    let order: Vec<&str> = golden["order"].as_array().unwrap().iter().map(|v| v.as_str().unwrap()).collect();
    assert_eq!(dumped.keys().map(String::as_str).collect::<Vec<_>>(), order);
    assert_eq!((&dumped["strokes"], &dumped["gradients"]), (&json!(false), &json!(true)));
}
