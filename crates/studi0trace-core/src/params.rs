//! Vexel's parameters: bounds, defaults, the JSON schema the UI builds its
//! controls from, and validation. Ported from the Pydantic `VexelParams`
//! (`backend/studi0trace/engines/vexel/engine.py`), which this table must agree
//! with to the letter until the Python server is retired: `tests/params.rs`
//! holds `schema()` and `defaults()` to the JSON Pydantic itself produces.
//!
//! The fields are in the order Pydantic declares them. The UI lays its controls
//! out in that order (`Object.entries(schema.properties)`), so `schema()` keeps
//! it: serde_json's `preserve_order` feature is on for this crate. Inside a
//! property the keys are sorted, as Pydantic writes them, so the schema is the
//! same text as `GET /engines` sends and not only the same value.
//!
//! # What `parse` refuses, and how it says so
//!
//! [`check`] reads the values as Pydantic's *strict* mode would, which is stricter than the
//! lax mode the route validates with: a number must be a JSON number (not `"6"` or `true`),
//! a boolean a JSON boolean (not `"yes"` or `1`), and an integer a whole number (`16.0` is
//! 16, `16.5` is refused). Everything it refuses it describes as Pydantic describes the
//! same refusal ([`ParamError::kind`] is the error's `type`, [`ParamError::message`] its
//! `msg`, with `input` and `ctx`), and in Pydantic's order (the fields in the order they are
//! declared, then the keys that are not fields), so the route's 422 can be written from it
//! to the letter. Where the lax mode would have accepted the value the refusal is the
//! strict one (`float_type`, not a conversion), and the UI, which sends numbers, booleans
//! and the options of a select, never meets it.
use serde_json::{json, Map, Value};
use vexel_rs::engine::VexelParams;

/// What a parameter is, and the numbers that bound it.
#[derive(Debug, Clone, Copy)]
pub enum Kind {
    Number { default: f64, min: f64, max: f64 },
    Integer { default: i64, min: i64, max: i64 },
    Bool { default: bool },
    Choice { default: &'static str, options: &'static [&'static str] },
}

/// One parameter: its name, kind, the sentence the UI shows and the `ui` hint
/// (control, group, step, label, unit) it is drawn from.
#[derive(Debug, Clone)]
pub struct Field {
    pub name: &'static str,
    pub kind: Kind,
    pub description: &'static str,
    pub ui: Value,
}

/// A parameter the engine will not take, described as Pydantic describes the same refusal.
/// `field` is the offending key (empty when the whole value was the wrong shape).
#[derive(Debug, Clone, PartialEq)]
pub struct ParamError {
    /// Always `"validation_error"`: the code a client keys on.
    pub code: &'static str,
    pub field: String,
    /// Pydantic's `msg`: "Input should be greater than or equal to 1".
    pub message: String,
    /// Pydantic's error `type`: `greater_than_equal`, `less_than_equal`, `float_type`, `int_type`,
    /// `int_from_float`, `bool_type`, `literal_error`, `extra_forbidden` or `model_type`.
    pub kind: &'static str,
    /// The value that was refused, as it arrived.
    pub input: Value,
    /// Pydantic's `ctx` (`{"ge": 1.0}`, `{"expected": "'a' or 'b'"}`) for the errors that have one.
    pub ctx: Option<Map<String, Value>>,
}

impl std::fmt::Display for ParamError {
    /// `field: message`, as the frontend shows it; a refusal of the whole value is its message.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.field.is_empty() {
            f.write_str(&self.message)
        } else {
            write!(f, "{}: {}", self.field, self.message)
        }
    }
}

impl std::error::Error for ParamError {}

/// The parameter table, in declaration order.
///
/// A function, not a `const FIELDS`: the `ui` hints are `serde_json::Value`s,
/// which cannot be built in a const context.
pub fn fields() -> Vec<Field> {
    use Kind::*;
    vec![
        Field {
            name: "upsample",
            kind: Choice { default: "auto", options: &["auto", "never", "always"] },
            description: "Trace a small input (≤ 192 px) at twice its size when its own trace shows a region thinner than 2.2 px",
            ui: json!({"control": "select", "group": "Regions", "label": "Small-input upsampling"}),
        },
        Field {
            name: "detail",
            kind: Number { default: 6.0, min: 1.0, max: 40.0 },
            description: "Colour difference (ΔE) below which neighbouring regions merge; lower keeps more regions",
            ui: json!({"control": "slider", "step": 0.5, "group": "Regions"}),
        },
        Field {
            name: "min_region",
            kind: Integer { default: 6, min: 1, max: 200 },
            description: "Regions smaller than this many pixels are absorbed",
            ui: json!({"control": "slider", "step": 1, "group": "Regions", "label": "Speckle floor", "unit": "px"}),
        },
        Field {
            name: "gradients",
            kind: Bool { default: true },
            description: "Reconstruct linear and radial gradients instead of flat bands",
            ui: json!({"control": "toggle", "group": "Fills"}),
        },
        Field {
            name: "max_stops",
            kind: Integer { default: 4, min: 2, max: 8 },
            description: "Maximum colour stops per gradient",
            ui: json!({"control": "slider", "step": 1, "group": "Fills", "label": "Gradient stops"}),
        },
        Field {
            name: "layering",
            kind: Choice { default: "stacked", options: &["stacked", "cutout"] },
            description: "Stacked shapes in painter's order (seamless, editable) or exact non-overlapping cut-outs",
            ui: json!({"control": "select", "group": "Output"}),
        },
        Field {
            name: "corner_threshold",
            kind: Number { default: 60.0, min: 20.0, max: 150.0 },
            description: "Turning angle (degrees) above which an outline point is a corner",
            ui: json!({"control": "slider", "step": 1, "group": "Curves", "unit": "°"}),
        },
        Field {
            name: "curve_tolerance",
            kind: Number { default: 0.4, min: 0.1, max: 2.0 },
            description: "Maximum distance (px) between the fitted curve and the traced outline",
            ui: json!({"control": "slider", "step": 0.05, "group": "Curves", "unit": "px"}),
        },
        Field {
            name: "shape_fitting",
            kind: Bool { default: true },
            description: "Emit circles, ellipses and rectangles as primitives when they fit",
            ui: json!({"control": "toggle", "group": "Curves", "label": "Whole-shape fitting"}),
        },
        Field {
            name: "refine",
            kind: Bool { default: false },
            description: "Render each edge's two shapes and nudge the curve until the pixels match the source (slow)",
            ui: json!({"control": "toggle", "group": "Curves", "label": "Render refinement"}),
        },
        Field {
            name: "strokes",
            kind: Bool { default: true },
            description: "Recover thin lines as stroked centreline paths instead of filled slivers",
            ui: json!({"control": "toggle", "group": "Curves", "label": "Stroke recovery"}),
        },
        Field {
            name: "shadows",
            kind: Bool { default: true },
            description: "Rebuild drop shadows, glows and inner shadows as SVG filters instead of banded paths",
            ui: json!({"control": "toggle", "group": "Effects"}),
        },
        // 0.13: over corpus + held-out every thin group that is a drawn line
        // scores at most 0.118 against its own coverage (hairlines under a pixel
        // the highest), and the letter stems and blobs a stroke would mangle
        // 0.141 up (the Python carries the same note).
        Field {
            name: "stroke_tolerance",
            kind: Number { default: 0.13, min: 0.05, max: 1.0 },
            description: "Largest error a centreline may leave before the thin region is drawn filled instead of stroked; lower keeps more shapes filled",
            ui: json!({"control": "slider", "step": 0.01, "group": "Curves", "label": "Stroke tolerance"}),
        },
        Field {
            name: "overlaps",
            kind: Bool { default: true },
            description: "Rebuild semi-transparent overlaps as two overlapping shapes with opacity",
            ui: json!({"control": "toggle", "group": "Fills", "label": "Overlap decomposition"}),
        },
        Field {
            name: "path_precision",
            kind: Integer { default: 2, min: 0, max: 4 },
            description: "Decimal places in coordinates",
            ui: json!({"control": "slider", "step": 1, "group": "Output"}),
        },
    ]
}

/// Pydantic's field title: the name with underscores as spaces, each word capitalised.
fn title(name: &str) -> String {
    name.split('_')
        .map(|word| {
            let mut chars = word.chars();
            chars.next().map(|first| first.to_uppercase().collect::<String>() + chars.as_str()).unwrap_or_default()
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// The JSON Schema of the parameters, equal to Pydantic's
/// `VexelParams.model_json_schema()`; `GET /engines` and the UI read it.
pub fn schema() -> Value {
    let mut props = Map::new();
    for f in fields() {
        let mut p = Map::new();
        p.insert("title".into(), json!(title(f.name)));
        p.insert("description".into(), json!(f.description));
        let mut ui = f.ui;
        if let Value::Object(hints) = &mut ui {
            hints.sort_keys();
        }
        p.insert("ui".into(), ui);
        match f.kind {
            Kind::Number { default, min, max } => {
                p.insert("type".into(), json!("number"));
                p.insert("default".into(), json!(default));
                p.insert("minimum".into(), json!(min));
                p.insert("maximum".into(), json!(max));
            }
            Kind::Integer { default, min, max } => {
                p.insert("type".into(), json!("integer"));
                p.insert("default".into(), json!(default));
                p.insert("minimum".into(), json!(min));
                p.insert("maximum".into(), json!(max));
            }
            Kind::Bool { default } => {
                p.insert("type".into(), json!("boolean"));
                p.insert("default".into(), json!(default));
            }
            Kind::Choice { default, options } => {
                p.insert("type".into(), json!("string"));
                p.insert("default".into(), json!(default));
                p.insert("enum".into(), json!(options));
            }
        }
        p.sort_keys(); // Pydantic writes a property's keys in alphabetical order
        props.insert(f.name.into(), Value::Object(p));
    }
    json!({"additionalProperties": false, "properties": props, "title": "VexelParams", "type": "object"})
}

/// Every parameter at its default, equal to Pydantic's `VexelParams().model_dump()`.
pub fn defaults() -> Map<String, Value> {
    fields()
        .into_iter()
        .map(|f| {
            let default = match f.kind {
                Kind::Number { default, .. } => json!(default),
                Kind::Integer { default, .. } => json!(default),
                Kind::Bool { default } => json!(default),
                Kind::Choice { default, .. } => json!(default),
            };
            (f.name.to_string(), default)
        })
        .collect()
}

/// The parameters as Pydantic's `VexelParams.model_dump()` gives them: every field, in the order
/// they are declared, numbers as the kind the field is (`10.0`, not `10`, for a `float`).
pub fn dump(p: &VexelParams) -> Map<String, Value> {
    fields()
        .into_iter()
        .map(|f| {
            let v = match f.name {
                "upsample" => json!(p.upsample),
                "detail" => json!(p.detail),
                "min_region" => json!(p.min_region),
                "gradients" => json!(p.gradients),
                "max_stops" => json!(p.max_stops),
                "layering" => json!(p.layering),
                "corner_threshold" => json!(p.corner_threshold),
                "curve_tolerance" => json!(p.curve_tolerance),
                "shape_fitting" => json!(p.shape_fitting),
                "refine" => json!(p.refine),
                "strokes" => json!(p.strokes),
                "shadows" => json!(p.shadows),
                "stroke_tolerance" => json!(p.stroke_tolerance),
                "overlaps" => json!(p.overlaps),
                "path_precision" => json!(p.path_precision),
                other => unreachable!("{other}: in the table but not dumped"),
            };
            (f.name.to_string(), v)
        })
        .collect()
}

fn refuse(field: &str, kind: &'static str, message: String, input: &Value, ctx: Option<Value>) -> ParamError {
    ParamError {
        code: "validation_error",
        field: field.to_string(),
        message,
        kind,
        input: input.clone(),
        ctx: ctx.and_then(|c| c.as_object().cloned()),
    }
}

/// Pydantic's `expected` of a `Literal`: `'a'`, `'a' or 'b'`, `'a', 'b' or 'c'`.
fn expected(options: &[&str]) -> String {
    let quoted: Vec<String> = options.iter().map(|o| format!("'{o}'")).collect();
    match quoted.as_slice() {
        [] => String::new(),
        [only] => only.clone(),
        [init @ .., last] => format!("{} or {}", init.join(", "), last),
    }
}

/// Validate `values` (a JSON object; a missing key takes its default) into the
/// engine's parameters, or say everything that is wrong with it, in Pydantic's order.
/// See the module documentation for what is refused and how it is described.
pub fn check(values: &Value) -> Result<VexelParams, Vec<ParamError>> {
    let Some(obj) = values.as_object() else {
        return Err(vec![refuse(
            "",
            "model_type",
            "Input should be a valid dictionary or instance of VexelParams".into(),
            values,
            Some(json!({"class_name": "VexelParams"})),
        )]);
    };
    let table = fields();
    let mut out = VexelParams::default();
    let mut errors = Vec::new();
    for f in &table {
        let Some(v) = obj.get(f.name) else { continue };
        let not_in_table = || unreachable!("{}: in the table but not set in parse", f.name);
        match f.kind {
            Kind::Number { min, max, .. } => {
                let Some(x) = v.as_f64() else {
                    errors.push(refuse(f.name, "float_type", "Input should be a valid number".into(), v, None));
                    continue;
                };
                if x < min {
                    errors.push(refuse(f.name, "greater_than_equal", format!("Input should be greater than or equal to {min}"), v, Some(json!({"ge": min}))));
                } else if x > max {
                    errors.push(refuse(f.name, "less_than_equal", format!("Input should be less than or equal to {max}"), v, Some(json!({"le": max}))));
                } else {
                    match f.name {
                        "detail" => out.detail = x,
                        "corner_threshold" => out.corner_threshold = x,
                        "curve_tolerance" => out.curve_tolerance = x,
                        "stroke_tolerance" => out.stroke_tolerance = x,
                        _ => not_in_table(),
                    }
                }
            }
            Kind::Integer { min, max, .. } => {
                let Some(x) = v.as_f64() else {
                    errors.push(refuse(f.name, "int_type", "Input should be a valid integer".into(), v, None));
                    continue;
                };
                if x.fract() != 0.0 {
                    errors.push(refuse(f.name, "int_from_float", "Input should be a valid integer, got a number with a fractional part".into(), v, None));
                    continue;
                }
                let x = x as i64;
                if x < min {
                    errors.push(refuse(f.name, "greater_than_equal", format!("Input should be greater than or equal to {min}"), v, Some(json!({"ge": min}))));
                } else if x > max {
                    errors.push(refuse(f.name, "less_than_equal", format!("Input should be less than or equal to {max}"), v, Some(json!({"le": max}))));
                } else {
                    match f.name {
                        "min_region" => out.min_region = x as usize,
                        "max_stops" => out.max_stops = x as usize,
                        "path_precision" => out.path_precision = x as usize,
                        _ => not_in_table(),
                    }
                }
            }
            Kind::Bool { .. } => {
                let Some(x) = v.as_bool() else {
                    errors.push(refuse(f.name, "bool_type", "Input should be a valid boolean".into(), v, None));
                    continue;
                };
                match f.name {
                    "gradients" => out.gradients = x,
                    "shape_fitting" => out.shape_fitting = x,
                    "refine" => out.refine = x,
                    "strokes" => out.strokes = x,
                    "shadows" => out.shadows = x,
                    "overlaps" => out.overlaps = x,
                    _ => not_in_table(),
                }
            }
            Kind::Choice { options, .. } => {
                let Some(x) = v.as_str().filter(|s| options.contains(s)) else {
                    let want = expected(options);
                    errors.push(refuse(f.name, "literal_error", format!("Input should be {want}"), v, Some(json!({"expected": want}))));
                    continue;
                };
                match f.name {
                    "upsample" => out.upsample = x.to_string(),
                    "layering" => out.layering = x.to_string(),
                    _ => not_in_table(),
                }
            }
        }
    }
    // the keys that are not fields come after the fields' own errors, in the order they arrived
    for (key, v) in obj {
        if !table.iter().any(|f| f.name == key) {
            errors.push(refuse(key, "extra_forbidden", "Extra inputs are not permitted".into(), v, None));
        }
    }
    if errors.is_empty() {
        Ok(out)
    } else {
        Err(errors)
    }
}

/// [`check`], reporting the first thing wrong with `values` only. Refuses an unknown key, a
/// value of the wrong type, a number outside its bounds and a choice that is not one of the
/// options, and names the field. An integer may arrive as a whole float (`16.0`), as
/// Pydantic allows; `16.5` is refused.
pub fn parse(values: &Value) -> Result<VexelParams, ParamError> {
    check(values).map_err(|mut all| all.remove(0))
}
