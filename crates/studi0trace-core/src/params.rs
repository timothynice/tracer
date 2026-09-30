//! Vexel's parameters: bounds, defaults, the JSON schema the UI builds its
//! controls from, and validation. Ported from the Pydantic `VexelParams`
//! (`backend/studi0trace/engines/vexel/engine.py`), which this table must agree
//! with to the letter until the Python server is retired: `tests/params.rs`
//! holds `schema()` and `defaults()` to the JSON Pydantic itself produces.
//!
//! The fields are in the order Pydantic declares them. The UI lays its controls
//! out in that order (`Object.entries(schema.properties)`), so `schema()` keeps
//! it: serde_json's `preserve_order` feature is on for this crate.
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

/// A parameter the engine will not take. `field` is the offending key (empty
/// when the whole value was the wrong shape).
#[derive(Debug, Clone, PartialEq)]
pub struct ParamError {
    pub code: &'static str,
    pub field: String,
    pub message: String,
}

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
        p.insert("ui".into(), f.ui);
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

fn err(field: &str, message: String) -> ParamError {
    ParamError { code: "validation_error", field: field.to_string(), message }
}

/// Validate `values` (a JSON object; a missing key takes its default) into the
/// engine's parameters. Refuses an unknown key, a value of the wrong type, a
/// number outside its bounds and a choice that is not one of the options, and
/// names the field. An integer may arrive as a whole float (`16.0`), as
/// Pydantic allows; `16.5` is refused.
pub fn parse(values: &Value) -> Result<VexelParams, ParamError> {
    let obj = values.as_object().ok_or_else(|| err("", "parameters must be an object".into()))?;
    let table = fields();
    for key in obj.keys() {
        if !table.iter().any(|f| f.name == key) {
            return Err(err(key, "Extra inputs are not permitted".into()));
        }
    }
    let mut out = VexelParams::default();
    for f in &table {
        let Some(v) = obj.get(f.name) else { continue };
        let not_in_table = || unreachable!("{}: in the table but not set in parse", f.name);
        match f.kind {
            Kind::Number { min, max, .. } => {
                let x = v.as_f64().ok_or_else(|| err(f.name, "Input should be a valid number".into()))?;
                if x < min || x > max {
                    return Err(err(f.name, format!("Input should be between {min} and {max}")));
                }
                match f.name {
                    "detail" => out.detail = x,
                    "corner_threshold" => out.corner_threshold = x,
                    "curve_tolerance" => out.curve_tolerance = x,
                    "stroke_tolerance" => out.stroke_tolerance = x,
                    _ => not_in_table(),
                }
            }
            Kind::Integer { min, max, .. } => {
                let x = v
                    .as_f64()
                    .filter(|x| x.fract() == 0.0)
                    .ok_or_else(|| err(f.name, "Input should be a valid integer".into()))? as i64;
                if x < min || x > max {
                    return Err(err(f.name, format!("Input should be between {min} and {max}")));
                }
                match f.name {
                    "min_region" => out.min_region = x as usize,
                    "max_stops" => out.max_stops = x as usize,
                    "path_precision" => out.path_precision = x as usize,
                    _ => not_in_table(),
                }
            }
            Kind::Bool { .. } => {
                let x = v.as_bool().ok_or_else(|| err(f.name, "Input should be a valid boolean".into()))?;
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
                let x = v
                    .as_str()
                    .filter(|s| options.contains(s))
                    .ok_or_else(|| err(f.name, format!("Input should be {}", options.join(" or "))))?;
                match f.name {
                    "upsample" => out.upsample = x.to_string(),
                    "layering" => out.layering = x.to_string(),
                    _ => not_in_table(),
                }
            }
        }
    }
    Ok(out)
}
