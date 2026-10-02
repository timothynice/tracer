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

/// The six booleans of the engine's parameters, in the order they are declared in the table.
const BOOLS: [&str; 6] = ["gradients", "shape_fitting", "refine", "strokes", "shadows", "overlaps"];

/// What the engine would read for each of [`BOOLS`].
fn bools_of(p: &vexel_rs::engine::VexelParams) -> [bool; 6] {
    [p.gradients, p.shape_fitting, p.refine, p.strokes, p.shadows, p.overlaps]
}

#[test]
fn each_boolean_goes_to_its_own_field_in_the_engine_and_in_the_dump() {
    // The test above sets five of the six to false, so a crossed arm in `check` or `dump` (the
    // value of one field read into another) would pass it. Here one boolean at a time leaves its
    // default and nothing else moves, and then three patterns in which no two differ alike.
    let default = bools_of(&vexel_rs::engine::VexelParams::default());
    let dumped_bools = |dumped: &serde_json::Map<String, Value>| -> [bool; 6] { BOOLS.map(|name| dumped[name].as_bool().unwrap()) };
    assert_eq!(default, [true, true, false, true, true, true], "the defaults this test is written around moved");
    for (i, name) in BOOLS.iter().enumerate() {
        let mut want = default;
        want[i] = !want[i];
        let p = params::parse(&json!({ *name: want[i] })).unwrap();
        assert_eq!(bools_of(&p), want, "only {name} was set");
        assert_eq!(dumped_bools(&params::dump(&p)), want, "dump of {name}");
    }
    for pattern in [[false, true, true, false, true, false], [true, false, true, true, false, false], [false, false, false, true, true, true]] {
        let values = Value::Object(BOOLS.iter().zip(pattern).map(|(name, on)| (name.to_string(), json!(on))).collect());
        let p = params::parse(&values).unwrap();
        assert_eq!(bools_of(&p), pattern, "{values}");
        assert_eq!(dumped_bools(&params::dump(&p)), pattern, "dump of {values}");
    }
}

#[test]
fn a_dump_of_a_parse_gives_back_every_value_each_in_its_own_field() {
    // distinct values for every field, twice over, so that no two fields can be swapped unseen
    for values in [
        json!({"upsample": "never", "detail": 12.5, "min_region": 20, "gradients": false, "max_stops": 7, "layering": "cutout",
               "corner_threshold": 90.0, "curve_tolerance": 1.25, "shape_fitting": true, "refine": true, "strokes": false,
               "shadows": true, "stroke_tolerance": 0.5, "overlaps": false, "path_precision": 3}),
        json!({"upsample": "always", "detail": 33.25, "min_region": 9, "gradients": true, "max_stops": 5, "layering": "stacked",
               "corner_threshold": 45.5, "curve_tolerance": 0.75, "shape_fitting": false, "refine": false, "strokes": true,
               "shadows": false, "stroke_tolerance": 0.2, "overlaps": true, "path_precision": 1}),
    ] {
        let p = params::parse(&values).unwrap();
        let dumped = Value::Object(params::dump(&p));
        // equal as values, kinds included (an integer field stays an integer, a float a float)
        assert_eq!(dumped, values);
        assert_eq!((p.detail, p.corner_threshold, p.curve_tolerance, p.stroke_tolerance), (values["detail"].as_f64().unwrap(), values["corner_threshold"].as_f64().unwrap(), values["curve_tolerance"].as_f64().unwrap(), values["stroke_tolerance"].as_f64().unwrap()));
        assert_eq!((p.min_region as u64, p.max_stops as u64, p.path_precision as u64), (values["min_region"].as_u64().unwrap(), values["max_stops"].as_u64().unwrap(), values["path_precision"].as_u64().unwrap()));
        assert_eq!((p.upsample.as_str(), p.layering.as_str()), (values["upsample"].as_str().unwrap(), values["layering"].as_str().unwrap()));
    }
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

// ---------------------------------------------------------------- refusals, described as Pydantic describes them

/// Everything `check` says of `values`, as (type, field, message, input, ctx).
fn all_refusals(values: Value) -> Vec<(&'static str, String, String, Value, Option<Value>)> {
    match params::check(&values) {
        Err(errors) => errors.into_iter().map(|e| (e.kind, e.field, e.message, e.input, e.ctx.map(Value::Object))).collect(),
        Ok(_) => panic!("check accepted {values}"),
    }
}

#[test]
fn each_kind_of_refusal_is_the_one_pydantic_reports_with_its_words_input_and_context() {
    let one = |values: Value| {
        let mut all = all_refusals(values);
        assert_eq!(all.len(), 1, "{all:?}");
        all.remove(0)
    };
    // (the same cases are answered by the real app in tests/fixtures/api.json and held to it by tests/api.rs)
    assert_eq!(
        one(json!({"detail": 0.5})),
        ("greater_than_equal", "detail".into(), "Input should be greater than or equal to 1".into(), json!(0.5), Some(json!({"ge": 1.0})))
    );
    assert_eq!(
        one(json!({"min_region": 201})),
        ("less_than_equal", "min_region".into(), "Input should be less than or equal to 200".into(), json!(201), Some(json!({"le": 200})))
    );
    assert_eq!(one(json!({"stroke_tolerance": 0.04})).2, "Input should be greater than or equal to 0.05");
    assert_eq!(one(json!({"corner_threshold": 151})).4, Some(json!({"le": 150.0})));
    assert_eq!(one(json!({"detail": null})), ("float_type", "detail".into(), "Input should be a valid number".into(), json!(null), None));
    assert_eq!(one(json!({"min_region": null})).0, "int_type");
    assert_eq!(
        one(json!({"min_region": 16.5})),
        ("int_from_float", "min_region".into(), "Input should be a valid integer, got a number with a fractional part".into(), json!(16.5), None)
    );
    assert_eq!(one(json!({"gradients": 1})), ("bool_type", "gradients".into(), "Input should be a valid boolean".into(), json!(1), None));
    assert_eq!(
        one(json!({"layering": "sideways"})),
        (
            "literal_error",
            "layering".into(),
            "Input should be 'stacked' or 'cutout'".into(),
            json!("sideways"),
            Some(json!({"expected": "'stacked' or 'cutout'"}))
        )
    );
    assert_eq!(one(json!({"upsample": 3})).2, "Input should be 'auto', 'never' or 'always'");
    assert_eq!(one(json!({"colour": {"a": 1}})), ("extra_forbidden", "colour".into(), "Extra inputs are not permitted".into(), json!({"a": 1}), None));
    assert_eq!(
        one(json!("abc")),
        (
            "model_type",
            "".into(),
            "Input should be a valid dictionary or instance of VexelParams".into(),
            json!("abc"),
            Some(json!({"class_name": "VexelParams"}))
        )
    );
}

#[test]
fn every_error_is_reported_the_fields_in_the_order_they_are_declared_and_then_the_keys_that_are_not_fields() {
    // Pydantic's order, not the order the keys arrived in
    let all = all_refusals(json!({"colour": 1, "path_precision": 9, "layering": "x", "detail": 0, "shine": 2, "max_stops": 9}));
    let fields: Vec<&str> = all.iter().map(|e| e.1.as_str()).collect();
    assert_eq!(fields, ["detail", "max_stops", "layering", "path_precision", "colour", "shine"]);
    assert_eq!(all.iter().map(|e| e.0).collect::<Vec<_>>(), ["greater_than_equal", "less_than_equal", "literal_error", "less_than_equal", "extra_forbidden", "extra_forbidden"]);
    // `parse` is the first of them
    let first = refused(json!({"colour": 1, "detail": 0}));
    assert_eq!((first.field.as_str(), first.kind), ("detail", "greater_than_equal"));
}

#[test]
fn a_refusal_reads_as_field_colon_message_and_is_an_error() {
    let e = refused(json!({"detail": 0.5}));
    assert_eq!(e.to_string(), "detail: Input should be greater than or equal to 1");
    let whole = refused(json!(5));
    assert_eq!(whole.to_string(), "Input should be a valid dictionary or instance of VexelParams");
    let as_error: &dyn std::error::Error = &e;
    assert!(as_error.source().is_none());
    let boxed: Box<dyn std::error::Error + Send + Sync> = Box::new(e);
    assert!(boxed.to_string().starts_with("detail: "));
}

#[test]
fn the_schema_is_the_text_get_engines_sends_a_propertys_keys_and_its_hints_sorted() {
    // Pydantic writes the keys of every property (and of the `ui` hints inside) alphabetically,
    // and keeps the properties in the order they are declared
    let schema = params::schema();
    for (name, property) in schema["properties"].as_object().unwrap() {
        let keys: Vec<&str> = property.as_object().unwrap().keys().map(String::as_str).collect();
        let mut sorted = keys.clone();
        sorted.sort_unstable();
        assert_eq!(keys, sorted, "{name}");
        let hints: Vec<&str> = property["ui"].as_object().unwrap().keys().map(String::as_str).collect();
        let mut sorted = hints.clone();
        sorted.sort_unstable();
        assert_eq!(hints, sorted, "{name}.ui");
    }
    let top: Vec<&str> = schema.as_object().unwrap().keys().map(String::as_str).collect();
    assert_eq!(top, ["additionalProperties", "properties", "title", "type"]);
    let declared: Vec<&str> = params::fields().iter().map(|f| f.name).collect();
    let listed: Vec<&str> = schema["properties"].as_object().unwrap().keys().map(String::as_str).collect();
    assert_eq!(listed, declared);
}
