# Studi0Trace Core Crate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pure-Rust crate, `studi0trace-core`, that does everything the Python server does around the Vexel engine (image intake, parameters and their schema, presets, SVG finishing, the quality scorecard, Auto), behind one facade the Mac app and the web build will both call.

**Architecture:** A Cargo workspace at the repo root with two members: the existing engine `backend/vexel-rs` (unchanged location) and the new `crates/studi0trace-core`, which depends on it. Every piece of the core is a port of a Python module and is held to it by golden JSON fixtures that a dev-only Python script exports from the reference implementation. The core returns the same JSON shapes the FastAPI routes return today, so the frontend changes only its transport in later plans.

**Tech Stack:** Rust 2021; `serde`/`serde_json`; `image` 0.25 (png, jpeg, gif, webp, bmp); `resvg` `=0.48.0` (the release `resvg-py` 0.5.0 builds on); `regex`; `rayon`; optional `pyo3` 0.22 for the parity stage. Python 3.12 dev tooling in `backend/` for fixtures.

Design and roadmap: `docs/superpowers/specs/2026-09-24-studi0trace-local-app-design.md`.

## Global Constraints

- No Python in anything the core needs at runtime; `pyo3` is an optional feature (`python`) used only by `tools/diffcheck.py`.
- The core must build for `wasm32-unknown-unknown` later: no filesystem access, no threads outside `rayon`, no system fonts, no C dependencies. Data files are embedded with `include_str!`/`include_bytes!`.
- JSON shapes are the frontend's (`frontend/src/lib/api.ts`): field names in `snake_case`, exactly as the FastAPI responses serialise them.
- Error codes are the Python's, verbatim: `too_large`, `unsupported_format`, `too_many_pixels`, `corrupt_image`, `validation_error`, `engine_failed`, `auto_unavailable`.
- Limits: `max_bytes` 20 MiB (20 × 1024 × 1024) and `max_pixels` 40,000,000, the Python `Settings` defaults in `backend/studi0trace/settings.py` (`max_upload_bytes`, `max_image_pixels`).
- Ports keep the Python's arithmetic order; where numpy/scipy/skimage semantics matter (percentile interpolation, `np.unwrap`, EDT, `binary_erosion` border value) the port replicates them and says so in a comment.
- Every task ends with `cargo test -p studi0trace-core` green and a commit whose message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The engine crate `vexel-rs` is not modified except where a task says so.

## File structure

```
Cargo.toml                                        workspace (new)
crates/studi0trace-core/Cargo.toml                crate manifest (new)
crates/studi0trace-core/src/lib.rs                module list, re-exports
crates/studi0trace-core/src/params.rs             Params, bounds, validation, JSON schema, -> VexelParams
crates/studi0trace-core/src/presets.rs            presets.json + preset_details.json, embedded
crates/studi0trace-core/src/intake.rs             bytes -> Image (RGBA8, EXIF-oriented), IntakeError
crates/studi0trace-core/src/svg.rs                normalize_dimensions, svg_stats
crates/studi0trace-core/src/color.rs              rgb on white, sRGB -> Lab, CIEDE2000
crates/studi0trace-core/src/edges.rs              Canny (skimage-compatible), disk dilation, edge F1
crates/studi0trace-core/src/render.rs             resvg rendering, anti-aliased and crisp
crates/studi0trace-core/src/drawing.rs            SVG -> Drawing of contours (quality.parse and helpers)
crates/studi0trace-core/src/holes.rs              quality.holes
crates/studi0trace-core/src/geometry.rs           quality.geometry_card and helpers
crates/studi0trace-core/src/scorecard.rs          Reference, scorecard, artifact_index, is_clean, assess
crates/studi0trace-core/src/auto.rs               choose, issues, summary, run_auto
crates/studi0trace-core/src/api.rs                Core facade and response types
crates/studi0trace-core/src/python.rs             feature "python": pyo3 module studi0trace_core
crates/studi0trace-core/examples/trace.rs         CLI demo: trace a file with a preset or Auto
crates/studi0trace-core/tests/fixtures/           golden JSON/PNG exported from the Python
crates/studi0trace-core/tests/common/mod.rs       fixture loading helpers
crates/studi0trace-core/tests/*.rs                one test file per module
backend/studi0trace/engines/presets.json          the preset bundles, shared by Python and Rust (moved out of presets.py)
backend/tools/export_core_fixtures.py             writes tests/fixtures from the Python reference
backend/tools/diffcheck.py                        + a `scorecard` stage (Rust core vs Python quality)
```

---

### Task 1: Workspace, crate skeleton and the fixture exporter

**Files:**
- Create: `Cargo.toml`, `crates/studi0trace-core/Cargo.toml`, `crates/studi0trace-core/src/lib.rs`, `crates/studi0trace-core/tests/common/mod.rs`, `crates/studi0trace-core/tests/smoke.rs`, `backend/tools/export_core_fixtures.py`
- Modify: `backend/vexel-rs/Cargo.toml` (move `[profile.release]` to the workspace root; cargo ignores profiles in members)

**Interfaces:**
- Produces: `studi0trace_core::VERSION: &str`; `tests/common::fixture_json(name) -> serde_json::Value`, `tests/common::fixture_bytes(name) -> Vec<u8>`; the exporter CLI `python -m tools.export_core_fixtures [--only NAME,...]` writing `crates/studi0trace-core/tests/fixtures/<name>.json`.

- [ ] **Step 1: Write the workspace manifest**

```toml
# Cargo.toml (repo root)
[workspace]
resolver = "2"
members = ["backend/vexel-rs", "crates/studi0trace-core"]

[profile.release]
opt-level = 3
lto = "fat"
codegen-units = 1
```

Delete the `[profile.release]` block from `backend/vexel-rs/Cargo.toml`.

- [ ] **Step 2: Write the crate manifest and lib.rs**

```toml
# crates/studi0trace-core/Cargo.toml
[package]
name = "studi0trace-core"
version = "0.1.0"
edition = "2021"
license = "MIT"
description = "Studi0Trace: everything around the Vexel engine, for the app and the web build"

[lib]
crate-type = ["rlib", "cdylib"]

[dependencies]
vexel-rs = { path = "../../backend/vexel-rs" }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
regex = "1"
rayon = "1.10"
image = { version = "0.25", default-features = false, features = ["png", "jpeg", "gif", "webp", "bmp"] }
pyo3 = { version = "0.22", features = ["abi3-py312"], optional = true }

[features]
default = []
python = ["dep:pyo3", "pyo3/extension-module"]
```

```rust
// crates/studi0trace-core/src/lib.rs
//! Studi0Trace's core: everything between the bytes a person drops in and the
//! SVG they save, around the Vexel engine. The Python in `backend/studi0trace`
//! is the reference each module was ported from.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
```

- [ ] **Step 3: Write the fixture helpers and a smoke test**

```rust
// crates/studi0trace-core/tests/common/mod.rs
use std::path::PathBuf;

pub fn fixture_path(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(name)
}

pub fn fixture_json(name: &str) -> serde_json::Value {
    let text = std::fs::read_to_string(fixture_path(name)).unwrap_or_else(|e| panic!("{name}: {e}; run python -m tools.export_core_fixtures"));
    serde_json::from_str(&text).unwrap()
}

pub fn fixture_bytes(name: &str) -> Vec<u8> {
    std::fs::read(fixture_path(name)).unwrap_or_else(|e| panic!("{name}: {e}"))
}

/// The repo's backend directory, for corpus images the fixtures name.
pub fn backend(rel: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend").join(rel)
}
```

```rust
// crates/studi0trace-core/tests/smoke.rs
mod common;

#[test]
fn the_crate_links_against_the_engine() {
    assert!(!studi0trace_core::VERSION.is_empty());
    let svg = vexel_rs::engine::trace_rgba(&[255, 0, 0, 255].repeat(16 * 16), 16, 16, &vexel_rs::engine::VexelParams::default());
    assert!(svg.starts_with("<svg"));
}
```

- [ ] **Step 4: Write the exporter's frame**

```python
# backend/tools/export_core_fixtures.py
"""Golden fixtures for crates/studi0trace-core, exported from the Python reference.

    .venv/bin/python -m tools.export_core_fixtures            # everything
    .venv/bin/python -m tools.export_core_fixtures --only schema,presets

Each exporter writes one JSON file into the crate's tests/fixtures. The Rust
tests compare against them, so the Python stays the definition of what the
core does until the Python server is retired.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "crates" / "studi0trace-core" / "tests" / "fixtures"
EXPORTERS: dict[str, callable] = {}


def exporter(name: str):
    def wrap(fn):
        EXPORTERS[name] = fn
        return fn
    return wrap


def write(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = {s for s in args.only.split(",") if s}
    for name, fn in EXPORTERS.items():
        if not only or name in only:
            fn()
            print("wrote", name)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Build and test the workspace**

Run: `cargo test --workspace --release` from the repo root.
Expected: `vexel-rs` tests pass unchanged and `the_crate_links_against_the_engine` passes. Then `cd backend && .venv/bin/python -m maturin develop --release -m vexel-rs/Cargo.toml` still builds (maturin reads the workspace profile).

- [ ] **Step 6: Commit**

```bash
git add Cargo.toml crates backend/vexel-rs/Cargo.toml backend/tools/export_core_fixtures.py
git commit -m "core: Cargo workspace and the studi0trace-core crate skeleton, with the fixture exporter"
```

---

### Task 2: Parameters, validation and the JSON schema

**Files:**
- Create: `crates/studi0trace-core/src/params.rs`, `crates/studi0trace-core/tests/params.rs`
- Modify: `crates/studi0trace-core/src/lib.rs` (add `pub mod params;`), `backend/tools/export_core_fixtures.py` (add the `schema` exporter)

**Interfaces:**
- Consumes: `vexel_rs::engine::VexelParams` (fields listed in `backend/vexel-rs/src/engine.rs:33-49`).
- Produces: `params::FIELDS: &[Field]`; `params::schema() -> serde_json::Value` (equal to Pydantic's `VexelParams.model_json_schema()`); `params::defaults() -> serde_json::Map<String, Value>` (equal to `VexelParams().model_dump()`); `params::parse(&Value) -> Result<vexel_rs::engine::VexelParams, ParamError>` where `ParamError { code: "validation_error", field: String, message: String }`.

- [ ] **Step 1: Export the golden schema**

Add to the exporter:

```python
@exporter("schema")
def _schema():
    from studi0trace.engines.vexel.engine import VexelParams
    write("schema", {"schema": VexelParams.model_json_schema(), "defaults": VexelParams().model_dump()})
```

Run: `cd backend && .venv/bin/python -m tools.export_core_fixtures --only schema`.

- [ ] **Step 2: Write the failing tests**

```rust
// crates/studi0trace-core/tests/params.rs
mod common;
use serde_json::json;
use studi0trace_core::params;

#[test]
fn the_schema_is_the_pydantic_one() {
    let golden = common::fixture_json("schema.json");
    assert_eq!(params::schema(), golden["schema"]);
    assert_eq!(serde_json::Value::Object(params::defaults()), golden["defaults"]);
}

#[test]
fn defaults_parse_to_the_engine_defaults() {
    let p = params::parse(&json!({})).unwrap();
    let d = vexel_rs::engine::VexelParams::default();
    assert_eq!((p.detail, p.min_region, p.curve_tolerance, p.layering.as_str()), (d.detail, d.min_region, d.curve_tolerance, d.layering.as_str()));
}

#[test]
fn out_of_range_and_unknown_fields_are_refused_with_the_field_named() {
    let e = params::parse(&json!({"detail": 0.5})).unwrap_err();
    assert_eq!((e.code, e.field.as_str()), ("validation_error", "detail"));
    let e = params::parse(&json!({"layering": "sideways"})).unwrap_err();
    assert_eq!(e.field, "layering");
    let e = params::parse(&json!({"colour": 3})).unwrap_err();
    assert_eq!(e.field, "colour");
}

#[test]
fn integers_accept_whole_floats_as_pydantic_does() {
    assert_eq!(params::parse(&json!({"min_region": 16.0})).unwrap().min_region, 16);
    assert!(params::parse(&json!({"min_region": 16.5})).is_err());
}
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `cargo test -p studi0trace-core --test params`
Expected: compile error, `params` not found.

- [ ] **Step 4: Implement params.rs**

The field table is the single source of truth once the Python is retired; copy each description, bound and `ui` hint verbatim from `backend/studi0trace/engines/vexel/engine.py` (`class VexelParams`, lines 67-132). The title is Pydantic's: the field name with underscores as spaces, each word capitalised.

```rust
// crates/studi0trace-core/src/params.rs
//! Vexel's parameters: bounds, defaults, the JSON schema the UI builds its
//! controls from, and validation. Ported from the Pydantic `VexelParams`.
use serde_json::{json, Map, Value};
use vexel_rs::engine::VexelParams;

pub enum Kind {
    Number { default: f64, min: f64, max: f64 },
    Integer { default: i64, min: i64, max: i64 },
    Bool { default: bool },
    Choice { default: &'static str, options: &'static [&'static str] },
}

pub struct Field {
    pub name: &'static str,
    pub kind: Kind,
    pub description: &'static str,
    pub ui: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ParamError {
    pub code: &'static str,
    pub field: String,
    pub message: String,
}

fn ui(v: Value) -> Value { v }

pub fn fields() -> Vec<Field> {
    use Kind::*;
    vec![
        Field { name: "upsample", kind: Choice { default: "auto", options: &["auto", "never", "always"] },
            description: "Trace a small input (≤ 192 px) at twice its size when its own trace shows a region thinner than 2.2 px",
            ui: ui(json!({"control": "select", "group": "Regions", "label": "Small-input upsampling"})) },
        Field { name: "detail", kind: Number { default: 6.0, min: 1.0, max: 40.0 },
            description: "Colour difference (ΔE) below which neighbouring regions merge; lower keeps more regions",
            ui: ui(json!({"control": "slider", "step": 0.5, "group": "Regions"})) },
        Field { name: "min_region", kind: Integer { default: 6, min: 1, max: 200 },
            description: "Regions smaller than this many pixels are absorbed",
            ui: ui(json!({"control": "slider", "step": 1, "group": "Regions", "label": "Speckle floor", "unit": "px"})) },
        Field { name: "gradients", kind: Bool { default: true },
            description: "Reconstruct linear and radial gradients instead of flat bands",
            ui: ui(json!({"control": "toggle", "group": "Fills"})) },
        Field { name: "max_stops", kind: Integer { default: 4, min: 2, max: 8 },
            description: "Maximum colour stops per gradient",
            ui: ui(json!({"control": "slider", "step": 1, "group": "Fills", "label": "Gradient stops"})) },
        Field { name: "layering", kind: Choice { default: "stacked", options: &["stacked", "cutout"] },
            description: "Stacked shapes in painter's order (seamless, editable) or exact non-overlapping cut-outs",
            ui: ui(json!({"control": "select", "group": "Output"})) },
        Field { name: "corner_threshold", kind: Number { default: 60.0, min: 20.0, max: 150.0 },
            description: "Turning angle (degrees) above which an outline point is a corner",
            ui: ui(json!({"control": "slider", "step": 1, "group": "Curves", "unit": "°"})) },
        Field { name: "curve_tolerance", kind: Number { default: 0.4, min: 0.1, max: 2.0 },
            description: "Maximum distance (px) between the fitted curve and the traced outline",
            ui: ui(json!({"control": "slider", "step": 0.05, "group": "Curves", "unit": "px"})) },
        Field { name: "shape_fitting", kind: Bool { default: true },
            description: "Emit circles, ellipses and rectangles as primitives when they fit",
            ui: ui(json!({"control": "toggle", "group": "Curves", "label": "Whole-shape fitting"})) },
        Field { name: "refine", kind: Bool { default: false },
            description: "Render each edge's two shapes and nudge the curve until the pixels match the source (slow)",
            ui: ui(json!({"control": "toggle", "group": "Curves", "label": "Render refinement"})) },
        Field { name: "strokes", kind: Bool { default: true },
            description: "Recover thin lines as stroked centreline paths instead of filled slivers",
            ui: ui(json!({"control": "toggle", "group": "Curves", "label": "Stroke recovery"})) },
        Field { name: "shadows", kind: Bool { default: true },
            description: "Rebuild drop shadows, glows and inner shadows as SVG filters instead of banded paths",
            ui: ui(json!({"control": "toggle", "group": "Effects"})) },
        Field { name: "stroke_tolerance", kind: Number { default: 0.2, min: 0.05, max: 1.0 },
            description: "Largest error a centreline may leave before the thin region is drawn filled instead of stroked; lower keeps more shapes filled",
            ui: ui(json!({"control": "slider", "step": 0.01, "group": "Curves", "label": "Stroke tolerance"})) },
        Field { name: "overlaps", kind: Bool { default: true },
            description: "Rebuild semi-transparent overlaps as two overlapping shapes with opacity",
            ui: ui(json!({"control": "toggle", "group": "Fills", "label": "Overlap decomposition"})) },
        Field { name: "path_precision", kind: Integer { default: 2, min: 0, max: 4 },
            description: "Decimal places in coordinates",
            ui: ui(json!({"control": "slider", "step": 1, "group": "Output"})) },
    ]
}

fn title(name: &str) -> String {
    name.split('_').map(|w| { let mut c = w.chars(); c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default() }).collect::<Vec<_>>().join(" ")
}

pub fn schema() -> Value {
    let mut props = Map::new();
    for f in fields() {
        let mut p = Map::new();
        p.insert("title".into(), json!(title(f.name)));
        p.insert("description".into(), json!(f.description));
        p.insert("ui".into(), f.ui.clone());
        match f.kind {
            Kind::Number { default, min, max } => { p.insert("type".into(), json!("number")); p.insert("default".into(), json!(default)); p.insert("minimum".into(), json!(min)); p.insert("maximum".into(), json!(max)); }
            Kind::Integer { default, min, max } => { p.insert("type".into(), json!("integer")); p.insert("default".into(), json!(default)); p.insert("minimum".into(), json!(min)); p.insert("maximum".into(), json!(max)); }
            Kind::Bool { default } => { p.insert("type".into(), json!("boolean")); p.insert("default".into(), json!(default)); }
            Kind::Choice { default, options } => { p.insert("type".into(), json!("string")); p.insert("default".into(), json!(default)); p.insert("enum".into(), json!(options)); }
        }
        props.insert(f.name.into(), Value::Object(p));
    }
    json!({"additionalProperties": false, "properties": props, "title": "VexelParams", "type": "object"})
}

pub fn defaults() -> Map<String, Value> {
    fields().into_iter().map(|f| (f.name.to_string(), match f.kind {
        Kind::Number { default, .. } => json!(default),
        Kind::Integer { default, .. } => json!(default),
        Kind::Bool { default } => json!(default),
        Kind::Choice { default, .. } => json!(default),
    })).collect()
}

fn err(field: &str, message: String) -> ParamError { ParamError { code: "validation_error", field: field.to_string(), message } }

/// Validate `values` (a JSON object, missing keys take their defaults) into the engine's parameters.
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
        match f.kind {
            Kind::Number { min, max, .. } => {
                let x = v.as_f64().ok_or_else(|| err(f.name, "Input should be a valid number".into()))?;
                if x < min || x > max { return Err(err(f.name, format!("Input should be between {min} and {max}"))); }
                match f.name { "detail" => out.detail = x, "corner_threshold" => out.corner_threshold = x, "curve_tolerance" => out.curve_tolerance = x, "stroke_tolerance" => out.stroke_tolerance = x, _ => unreachable!() }
            }
            Kind::Integer { min, max, .. } => {
                let x = v.as_f64().filter(|x| x.fract() == 0.0).ok_or_else(|| err(f.name, "Input should be a valid integer".into()))? as i64;
                if x < min || x > max { return Err(err(f.name, format!("Input should be between {min} and {max}"))); }
                match f.name { "min_region" => out.min_region = x as usize, "max_stops" => out.max_stops = x as usize, "path_precision" => out.path_precision = x as usize, _ => unreachable!() }
            }
            Kind::Bool { .. } => {
                let x = v.as_bool().ok_or_else(|| err(f.name, "Input should be a valid boolean".into()))?;
                match f.name { "gradients" => out.gradients = x, "shape_fitting" => out.shape_fitting = x, "refine" => out.refine = x, "strokes" => out.strokes = x, "shadows" => out.shadows = x, "overlaps" => out.overlaps = x, _ => unreachable!() }
            }
            Kind::Choice { options, .. } => {
                let x = v.as_str().filter(|s| options.contains(s)).ok_or_else(|| err(f.name, format!("Input should be {}", options.join(" or "))))?;
                match f.name { "upsample" => out.upsample = x.to_string(), "layering" => out.layering = x.to_string(), _ => unreachable!() }
            }
        }
    }
    Ok(out)
}
```

- [ ] **Step 5: Run the tests**

Run: `cargo test -p studi0trace-core --test params`
Expected: 4 passed. If `the_schema_is_the_pydantic_one` fails, print both with `serde_json::to_string_pretty` and fix the table, never the fixture.

- [ ] **Step 6: Commit**

```bash
git add crates/studi0trace-core backend/tools/export_core_fixtures.py
git commit -m "core: Vexel's parameters, validation and the JSON schema the UI reads, equal to the Pydantic model"
```

---

### Task 3: Presets shared by both implementations

**Files:**
- Create: `backend/studi0trace/engines/presets.json`, `crates/studi0trace-core/src/presets.rs`, `crates/studi0trace-core/tests/presets.rs`
- Modify: `backend/studi0trace/engines/presets.py` (read the bundles from presets.json), `crates/studi0trace-core/src/lib.rs`, the exporter (`presets`)

**Interfaces:**
- Produces: `presets::Preset { id, label, engine, description, detail, sample, params: Map<String, Value>, kind, auto_candidate }` (serde, field names as the API's); `presets::all() -> Vec<Preset>`; `presets::auto_candidates() -> Vec<Preset>` (preference order); `presets::by_id(&str) -> Option<Preset>`.

- [ ] **Step 1: Move the bundles into presets.json**

Copy the `_PRESETS` list from `presets.py` into JSON verbatim (keys `id`, `label`, `kind` where present, `auto_candidate` where present, `description`, `sample`, `params`), in the same order. Then make `presets.py` read it:

```python
BUNDLES_FILE = Path(__file__).with_name("presets.json")
_PRESETS: list[dict[str, Any]] = json.loads(BUNDLES_FILE.read_text(encoding="utf-8"))
```

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_api.py tests/test_auto.py tests/test_bench_presets.py`
Expected: all pass (behaviour unchanged).

- [ ] **Step 2: Export the golden list**

```python
@exporter("presets")
def _presets():
    from studi0trace.engines.presets import all_presets
    write("presets", [p.model_dump() for p in all_presets()])
```

- [ ] **Step 3: Write the failing test**

```rust
// crates/studi0trace-core/tests/presets.rs
mod common;
use studi0trace_core::presets;

#[test]
fn presets_are_the_apis_list() {
    let got = serde_json::to_value(presets::all()).unwrap();
    assert_eq!(got, common::fixture_json("presets.json"));
}

#[test]
fn auto_tries_its_candidates_in_order() {
    let ids: Vec<String> = presets::auto_candidates().into_iter().map(|p| p.id).collect();
    assert_eq!(ids, ["balanced", "logo", "detailed", "dense"]);
}
```

- [ ] **Step 4: Implement presets.rs**

```rust
// crates/studi0trace-core/src/presets.rs
//! The named parameter bundles and their measured one-line claims. The bundles
//! are `backend/studi0trace/engines/presets.json`, shared with the Python; the
//! lines are written by `bench.presets_eval --write-details`.
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

const BUNDLES: &str = include_str!("../../../backend/studi0trace/engines/presets.json");
const DETAILS: &str = include_str!("../../../backend/studi0trace/engines/preset_details.json");
const UNMEASURED: &str = "not measured yet";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Preset {
    pub id: String,
    pub label: String,
    pub engine: String,
    pub description: String,
    pub detail: String,
    pub sample: String,
    pub params: Map<String, Value>,
    pub kind: String,
    pub auto_candidate: bool,
}

#[derive(Deserialize)]
struct Bundle { id: String, label: String, #[serde(default)] kind: Option<String>, #[serde(default)] auto_candidate: bool, description: String, sample: String, params: Map<String, Value> }

fn and(words: &[String]) -> String {
    match words.len() { 0 => String::new(), 1 => words[0].clone(), n => format!("{} and {}", words[..n - 1].join(", "), words[n - 1]) }
}

pub fn all() -> Vec<Preset> {
    let bundles: Vec<Bundle> = serde_json::from_str(BUNDLES).expect("presets.json");
    let details: Value = serde_json::from_str(DETAILS).unwrap_or(Value::Null);
    let lines = details.get("lines").cloned().unwrap_or(Value::Null);
    let candidates: Vec<String> = bundles.iter().filter(|b| b.auto_candidate).map(|b| b.label.clone()).collect();
    let listed = and(&candidates);
    bundles.into_iter().map(|b| Preset {
        detail: lines.get(&b.id).and_then(|v| v.as_str()).unwrap_or(UNMEASURED).to_string(),
        description: b.description.replace("{candidates}", &listed),
        kind: b.kind.unwrap_or_else(|| "preset".into()),
        engine: "vexel".into(), id: b.id, label: b.label, sample: b.sample, params: b.params, auto_candidate: b.auto_candidate,
    }).collect()
}

pub fn auto_candidates() -> Vec<Preset> { all().into_iter().filter(|p| p.auto_candidate).collect() }

pub fn by_id(id: &str) -> Option<Preset> { all().into_iter().find(|p| p.id == id) }
```

Add `pub mod presets;` to lib.rs.

- [ ] **Step 5: Run the tests**

Run: `cargo test -p studi0trace-core --test presets`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/studi0trace/engines crates/studi0trace-core backend/tools/export_core_fixtures.py
git commit -m "presets: the bundles live in presets.json, read by the Python and embedded in the core"
```

---

### Task 4: Image intake

**Files:**
- Create: `crates/studi0trace-core/src/intake.rs`, `crates/studi0trace-core/tests/intake.rs`, fixtures `intake_*.{png,jpg,gif,webp,bmp}` and `intake.json`
- Modify: lib.rs, the exporter (`intake`)

**Interfaces:**
- Produces: `intake::Image { rgba: Vec<u8>, width: u32, height: u32, format: String }` (format one of `"PNG" | "JPEG" | "GIF" | "WEBP" | "BMP"`, as Pillow names them); `intake::load(bytes: &[u8], limits: Limits) -> Result<Image, IntakeError>`; `Limits { max_bytes: usize, max_pixels: u64 }` with `Limits::default()` = the Python `Settings` defaults; `IntakeError { code: &'static str, message: String }`.

- [ ] **Step 1: Export fixtures from Pillow**

```python
@exporter("intake")
def _intake():
    import hashlib, io
    from PIL import Image
    from studi0trace.imaging.intake import load_upload
    src = Image.open(ROOT / "backend/bench/corpus/real/logo/vexel-wordmark-512.png").convert("RGBA").resize((96, 96))
    cases = {}
    for fmt, ext in (("PNG", "png"), ("JPEG", "jpg"), ("GIF", "gif"), ("WEBP", "webp"), ("BMP", "bmp")):
        buf = io.BytesIO()
        (src.convert("RGB") if fmt in ("JPEG", "BMP") else src).save(buf, fmt, **({"quality": 90} if fmt == "JPEG" else {}))
        (OUT / f"intake_{ext}.{ext}").write_bytes(buf.getvalue())
        img = load_upload(buf.getvalue(), max_bytes=1 << 30, max_pixels=1 << 30)
        rgba = img.image.tobytes()
        cases[ext] = {"format": img.source_format, "width": img.width, "height": img.height, "sha256": hashlib.sha256(rgba).hexdigest(), "rgba_sum": sum(rgba)}
    # EXIF orientation 6 (rotate 90° clockwise to display): a 40x20 JPEG that must come out 20x40
    buf = io.BytesIO()
    exif = Image.Exif(); exif[0x0112] = 6
    Image.new("RGB", (40, 20), (200, 30, 30)).save(buf, "JPEG", exif=exif.tobytes())
    (OUT / "intake_exif6.jpg").write_bytes(buf.getvalue())
    cases["exif6"] = {"width": 20, "height": 40}
    write("intake", cases)
```

- [ ] **Step 2: Write the failing tests**

```rust
// crates/studi0trace-core/tests/intake.rs
mod common;
use studi0trace_core::intake::{load, Limits};

#[test]
fn lossless_formats_decode_exactly_as_pillow_does() {
    let g = common::fixture_json("intake.json");
    for ext in ["png", "gif", "webp", "bmp"] {
        let img = load(&common::fixture_bytes(&format!("intake_{ext}.{ext}")), Limits::default()).unwrap();
        assert_eq!(img.format, g[ext]["format"], "{ext}");
        assert_eq!((img.width as u64, img.height as u64), (g[ext]["width"].as_u64().unwrap(), g[ext]["height"].as_u64().unwrap()));
        let sum: u64 = img.rgba.iter().map(|b| *b as u64).sum();
        assert_eq!(sum, g[ext]["rgba_sum"].as_u64().unwrap(), "{ext}: pixels differ from Pillow's");
    }
}

#[test]
fn jpeg_decodes_to_within_two_levels_of_pillow() {
    let g = common::fixture_json("intake.json");
    let img = load(&common::fixture_bytes("intake_jpg.jpg"), Limits::default()).unwrap();
    let sum: u64 = img.rgba.iter().map(|b| *b as u64).sum();
    let want = g["jpg"]["rgba_sum"].as_u64().unwrap();
    assert!((sum as f64 - want as f64).abs() / (img.rgba.len() as f64) < 2.0);
}

#[test]
fn exif_orientation_is_applied() {
    let img = load(&common::fixture_bytes("intake_exif6.jpg"), Limits::default()).unwrap();
    assert_eq!((img.width, img.height), (20, 40));
}

#[test]
fn limits_and_garbage_give_the_pythons_codes() {
    let png = common::fixture_bytes("intake_png.png");
    assert_eq!(load(&png, Limits { max_bytes: 10, max_pixels: 1 << 40 }).unwrap_err().code, "too_large");
    assert_eq!(load(&png, Limits { max_bytes: 1 << 30, max_pixels: 100 }).unwrap_err().code, "too_many_pixels");
    assert_eq!(load(b"not an image", Limits::default()).unwrap_err().code, "unsupported_format");
    assert_eq!(load(&png[..png.len() / 2], Limits::default()).unwrap_err().code, "corrupt_image");
}
```

- [ ] **Step 3: Implement intake.rs**

Order of checks follows `backend/studi0trace/imaging/intake.py:load_upload`: size, then format detection, then pixel count (from the header, before decoding), then decode, then EXIF orientation, then RGBA8.

```rust
// crates/studi0trace-core/src/intake.rs
//! Bytes a person dropped in -> an RGBA8 image, oriented as a viewer shows it.
//! Ported from `studi0trace/imaging/intake.py`; the error codes are its codes.
use image::{ImageDecoder, ImageFormat, ImageReader};
use std::io::Cursor;

#[derive(Debug, Clone)]
pub struct Image { pub rgba: Vec<u8>, pub width: u32, pub height: u32, pub format: String }

#[derive(Debug, Clone)]
pub struct IntakeError { pub code: &'static str, pub message: String }

#[derive(Debug, Clone, Copy)]
pub struct Limits { pub max_bytes: usize, pub max_pixels: u64 }

impl Default for Limits {
    // backend/studi0trace/settings.py: max_upload_bytes, max_image_pixels
    fn default() -> Self { Limits { max_bytes: 20 * 1024 * 1024, max_pixels: 40_000_000 } }
}

fn e(code: &'static str, message: impl Into<String>) -> IntakeError { IntakeError { code, message: message.into() } }

pub fn load(bytes: &[u8], limits: Limits) -> Result<Image, IntakeError> {
    if bytes.len() > limits.max_bytes {
        return Err(e("too_large", format!("File exceeds the {} MB limit", limits.max_bytes / (1024 * 1024))));
    }
    let reader = ImageReader::new(Cursor::new(bytes)).with_guessed_format().map_err(|_| e("unsupported_format", "File is not a recognised image"))?;
    let format = match reader.format() {
        Some(ImageFormat::Png) => "PNG", Some(ImageFormat::Jpeg) => "JPEG", Some(ImageFormat::Gif) => "GIF",
        Some(ImageFormat::WebP) => "WEBP", Some(ImageFormat::Bmp) => "BMP",
        Some(other) => return Err(e("unsupported_format", format!("Unsupported image format: {other:?}"))),
        None => return Err(e("unsupported_format", "File is not a recognised image")),
    };
    let mut decoder = reader.into_decoder().map_err(|_| e("corrupt_image", "Image data is corrupt or truncated"))?;
    let (w, h) = decoder.dimensions();
    if (w as u64) * (h as u64) > limits.max_pixels {
        return Err(e("too_many_pixels", format!("Image exceeds the {} megapixel limit", limits.max_pixels / 1_000_000)));
    }
    let orientation = decoder.orientation().unwrap_or(image::metadata::Orientation::NoTransforms);
    let mut img = image::DynamicImage::from_decoder(decoder).map_err(|_| e("corrupt_image", "Image data is corrupt or truncated"))?;
    img.apply_orientation(orientation);
    let rgba = img.to_rgba8();
    Ok(Image { width: rgba.width(), height: rgba.height(), rgba: rgba.into_raw(), format: format.into() })
}
```

If the lossless test fails on GIF or WEBP, compare which pixels differ against Pillow's decode of the same file (palette transparency and premultiplication are the usual causes) and fix the conversion, not the fixture.

- [ ] **Step 4: Run the tests**

Run: `cargo test -p studi0trace-core --test intake`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add crates/studi0trace-core backend/tools/export_core_fixtures.py
git commit -m "core: image intake with EXIF orientation and the Python's error codes"
```

---

### Task 5: SVG finishing and stats

**Files:**
- Create: `crates/studi0trace-core/src/svg.rs`, `crates/studi0trace-core/tests/svg.rs`, fixture `svg.json`
- Modify: lib.rs, the exporter (`svg`)

**Interfaces:**
- Produces: `svg::normalize_dimensions(svg: &str, width: u32, height: u32) -> String`; `svg::Stats { paths, nodes, bytes, gradients, unique_fills }` (all `u64`, serde); `svg::stats(svg: &str) -> Stats`.

- [ ] **Step 1: Export cases**

```python
@exporter("svg")
def _svg():
    from studi0trace.imaging.svg import normalize_dimensions, svg_stats
    from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
    from studi0trace.imaging.intake import load_upload
    cases = []
    samples = ['<svg width="10pt" height="5pt" viewBox="0 0 1 1"><path d="M0 0L1 1"/></svg>',
               '<svg xmlns="http://www.w3.org/2000/svg"/>']
    for rel in ("real/logo/vexel-wordmark-512.png", "real/logo/studi0mail-icon-512.png", "synthetic/shadow/glow-128.png"):
        png = (ROOT / "backend/bench/corpus" / rel).read_bytes()
        samples.append(VexelEngine().trace(load_upload(png, max_bytes=1 << 30, max_pixels=1 << 30), VexelParams()).svg)
    for s in samples:
        cases.append({"svg": s, "normalized": normalize_dimensions(s, 64, 32), "stats": svg_stats(s).as_dict()})
    write("svg", cases)
```

Check the synthetic path exists (`ls backend/bench/corpus/synthetic/shadow`) and use a `-128` item that is there.

- [ ] **Step 2: Write the failing test**

```rust
// crates/studi0trace-core/tests/svg.rs
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
```

- [ ] **Step 3: Implement svg.rs**

```rust
// crates/studi0trace-core/src/svg.rs
//! viewBox normalisation and lightweight stats, ported from `imaging/svg.py`.
use regex::Regex;
use serde::Serialize;
use std::collections::BTreeSet;
use std::sync::OnceLock;

fn re(cell: &'static OnceLock<Regex>, pat: &str) -> &'static Regex { cell.get_or_init(|| Regex::new(pat).unwrap()) }
static ROOT: OnceLock<Regex> = OnceLock::new();
static WIDTH: OnceLock<Regex> = OnceLock::new();
static HEIGHT: OnceLock<Regex> = OnceLock::new();
static VIEWBOX: OnceLock<Regex> = OnceLock::new();
static PATH_TAG: OnceLock<Regex> = OnceLock::new();
static D_ATTR: OnceLock<Regex> = OnceLock::new();
static NUMBER: OnceLock<Regex> = OnceLock::new();
static GRADIENT: OnceLock<Regex> = OnceLock::new();
static FILL_ATTR: OnceLock<Regex> = OnceLock::new();
static FILL_STYLE: OnceLock<Regex> = OnceLock::new();

#[derive(Debug, Clone, Copy, Serialize, PartialEq)]
pub struct Stats { pub paths: u64, pub nodes: u64, pub bytes: u64, pub gradients: u64, pub unique_fills: u64 }

pub fn normalize_dimensions(svg: &str, width: u32, height: u32) -> String {
    let Some(m) = re(&ROOT, r"(?is)<svg\b[^>]*>").find(svg) else { return svg.to_string() };
    let mut root = m.as_str().to_string();
    for (cell, pat) in [(&WIDTH, r#"(?i)\s+width="[^"]*""#), (&HEIGHT, r#"(?i)\s+height="[^"]*""#), (&VIEWBOX, r#"(?i)\s+viewBox="[^"]*""#)] {
        root = re(cell, pat).replace_all(&root, "").into_owned();
    }
    let closing = if root.trim_end().ends_with("/>") { "/>" } else { ">" };
    let body = root[..root.len() - closing.len()].trim_end();
    format!("{}{} viewBox=\"0 0 {width} {height}\"{closing}{}", &svg[..m.start()], body, &svg[m.end()..])
}

pub fn stats(svg: &str) -> Stats {
    let number = re(&NUMBER, r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?");
    let nodes: u64 = re(&D_ATTR, r#"(?i)\bd="([^"]*)""#).captures_iter(svg).map(|c| number.find_iter(&c[1]).count() as u64 / 2).sum();
    let mut fills: BTreeSet<String> = re(&FILL_ATTR, r#"(?i)\bfill="([^"]*)""#).captures_iter(svg).map(|c| c[1].trim().to_lowercase()).collect();
    fills.extend(re(&FILL_STYLE, r#"(?i)fill\s*:\s*([^;"']+)"#).captures_iter(svg).map(|c| c[1].trim().to_lowercase()));
    fills.remove("none");
    fills.remove("");
    Stats {
        paths: re(&PATH_TAG, r"(?i)<(?:path|circle|ellipse|rect|polygon|polyline|line)\b").find_iter(svg).count() as u64,
        nodes,
        bytes: svg.len() as u64,
        gradients: re(&GRADIENT, r"(?i)<(?:linear|radial)Gradient\b").find_iter(svg).count() as u64,
        unique_fills: fills.len() as u64,
    }
}
```

- [ ] **Step 4: Run the test**

Run: `cargo test -p studi0trace-core --test svg`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crates/studi0trace-core backend/tools/export_core_fixtures.py
git commit -m "core: SVG viewBox normalisation and stats, ported from imaging/svg.py"
```

---

### Task 6: Colour fidelity (Lab and CIEDE2000)

**Files:**
- Create: `crates/studi0trace-core/src/color.rs`, `crates/studi0trace-core/tests/color.rs`, fixture `color.json`
- Modify: lib.rs, the exporter (`color`)

**Interfaces:**
- Produces: `color::rgb_on_white(rgba: &[u8]) -> Vec<u8>` (RGB8, `quality.to_rgb_on_white`: float32 maths, `+ 0.5`, clip); `color::lab(rgb: &[u8]) -> Vec<[f64; 3]>` (skimage `rgb2lab`, D65 2°); `color::ciede2000(a: [f64; 3], b: [f64; 3]) -> f64`; `color::delta_e(a_rgb: &[u8], b_rgb: &[u8]) -> (f64, f64)` (mean, 95th percentile, numpy's linear interpolation).

- [ ] **Step 1: Export reference values from skimage**

```python
@exporter("color")
def _color():
    import numpy as np
    from skimage.color import deltaE_ciede2000, rgb2lab
    from studi0trace.imaging.quality import to_rgb_on_white, delta_e
    rng = np.random.default_rng(7)
    rgb = rng.integers(0, 256, (64, 3), dtype=np.uint8)
    rgb2 = np.clip(rgb.astype(int) + rng.integers(-40, 41, (64, 3)), 0, 255).astype(np.uint8)
    lab1 = rgb2lab(rgb[None].astype(np.float64) / 255.0)[0]
    lab2 = rgb2lab(rgb2[None].astype(np.float64) / 255.0)[0]
    rgba = np.concatenate([rgb, rng.integers(0, 256, (64, 1), dtype=np.uint8)], axis=1)
    mean, p95 = delta_e(rgb[None], rgb2[None])
    write("color", {"rgb": rgb.tolist(), "rgb2": rgb2.tolist(), "lab": lab1.tolist(), "lab2": lab2.tolist(),
                    "de": deltaE_ciede2000(lab1, lab2).tolist(), "rgba": rgba.tolist(),
                    "on_white": to_rgb_on_white(rgba[None])[0].tolist(), "mean": mean, "p95": p95})
```

- [ ] **Step 2: Write the failing test**

```rust
// crates/studi0trace-core/tests/color.rs
mod common;
use studi0trace_core::color;

fn flat(v: &serde_json::Value) -> Vec<u8> { v.as_array().unwrap().iter().flat_map(|p| p.as_array().unwrap().iter().map(|c| c.as_u64().unwrap() as u8)).collect() }

#[test]
fn lab_and_ciede2000_match_skimage() {
    let g = common::fixture_json("color.json");
    let (a, b) = (flat(&g["rgb"]), flat(&g["rgb2"]));
    let (la, lb) = (color::lab(&a), color::lab(&b));
    for i in 0..la.len() {
        for c in 0..3 { assert!((la[i][c] - g["lab"][i][c].as_f64().unwrap()).abs() < 1e-9, "lab {i}"); }
        assert!((color::ciede2000(la[i], lb[i]) - g["de"][i].as_f64().unwrap()).abs() < 1e-9, "de {i}");
    }
    let (mean, p95) = color::delta_e(&a, &b);
    assert!((mean - g["mean"].as_f64().unwrap()).abs() < 1e-9 && (p95 - g["p95"].as_f64().unwrap()).abs() < 1e-9);
}

#[test]
fn compositing_on_white_matches() {
    let g = common::fixture_json("color.json");
    assert_eq!(color::rgb_on_white(&flat(&g["rgba"])), flat(&g["on_white"]));
}
```

- [ ] **Step 3: Implement color.rs**

```rust
// crates/studi0trace-core/src/color.rs
//! Colour fidelity: compositing on white, sRGB -> CIELAB (D65, 2°) and
//! CIEDE2000, each as scikit-image computes it (`skimage.color.rgb2lab`,
//! `deltaE_ciede2000` with kL = kC = kH = 1).
use std::f64::consts::PI;

pub fn rgb_on_white(rgba: &[u8]) -> Vec<u8> {
    rgba.chunks_exact(4).flat_map(|p| {
        let a = p[3] as f32 / 255.0;
        (0..3).map(move |c| ((p[c] as f32) * a + 255.0 * (1.0 - a) + 0.5).clamp(0.0, 255.0) as u8)
    }).collect()
}

fn lin(c: f64) -> f64 { if c > 0.04045 { ((c + 0.055) / 1.055).powf(2.4) } else { c / 12.92 } }
fn f(t: f64) -> f64 { if t > 0.008856 { t.cbrt() } else { 7.787 * t + 16.0 / 116.0 } }

pub fn lab(rgb: &[u8]) -> Vec<[f64; 3]> {
    const M: [[f64; 3]; 3] = [[0.412453, 0.357580, 0.180423], [0.212671, 0.715160, 0.072169], [0.019334, 0.119193, 0.950227]];
    const WHITE: [f64; 3] = [0.95047, 1.0, 1.08883];
    rgb.chunks_exact(3).map(|p| {
        let v = [lin(p[0] as f64 / 255.0), lin(p[1] as f64 / 255.0), lin(p[2] as f64 / 255.0)];
        let xyz: Vec<f64> = (0..3).map(|r| (M[r][0] * v[0] + M[r][1] * v[1] + M[r][2] * v[2]) / WHITE[r]).collect();
        let (fx, fy, fz) = (f(xyz[0]), f(xyz[1]), f(xyz[2]));
        [116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)]
    }).collect()
}

fn polar_2pi(x: f64, y: f64) -> (f64, f64) { let t = y.atan2(x); (x.hypot(y), if t < 0.0 { t + 2.0 * PI } else { t }) }

pub fn ciede2000(l1: [f64; 3], l2: [f64; 3]) -> f64 {
    let (lab1, a1, b1) = (l1[0], l1[1], l1[2]);
    let (lab2, a2, b2) = (l2[0], l2[1], l2[2]);
    let cbar = 0.5 * (a1.hypot(b1) + a2.hypot(b2));
    let c7 = cbar.powi(7);
    let g = 0.5 * (1.0 - (c7 / (c7 + 25f64.powi(7))).sqrt());
    let scale = 1.0 + g;
    let (c1, h1) = polar_2pi(a1 * scale, b1);
    let (c2, h2) = polar_2pi(a2 * scale, b2);
    let lbar = 0.5 * (lab1 + lab2);
    let tmp = (lbar - 50.0).powi(2);
    let sl = 1.0 + 0.015 * tmp / (20.0 + tmp).sqrt();
    let l_term = (lab2 - lab1) / sl;
    let cbar = 0.5 * (c1 + c2);
    let sc = 1.0 + 0.045 * cbar;
    let c_term = (c2 - c1) / sc;
    let h_diff = h2 - h1;
    let h_sum = h1 + h2;
    let cc = c1 * c2;
    let mut dh = h_diff;
    if h_diff > PI { dh -= 2.0 * PI } else if h_diff < -PI { dh += 2.0 * PI }
    if cc == 0.0 { dh = 0.0 }
    let dh_term = 2.0 * cc.sqrt() * (dh / 2.0).sin();
    let mut hbar = h_sum;
    if cc != 0.0 && h_diff.abs() > PI { if h_sum < 2.0 * PI { hbar += 2.0 * PI } else { hbar -= 2.0 * PI } }
    if cc == 0.0 { hbar *= 2.0 }
    hbar *= 0.5;
    let t = 1.0 - 0.17 * (hbar - 30f64.to_radians()).cos() + 0.24 * (2.0 * hbar).cos() + 0.32 * (3.0 * hbar + 6f64.to_radians()).cos() - 0.20 * (4.0 * hbar - 63f64.to_radians()).cos();
    let sh = 1.0 + 0.015 * cbar * t;
    let h_term = dh_term / sh;
    let c7 = cbar.powi(7);
    let rc = 2.0 * (c7 / (c7 + 25f64.powi(7))).sqrt();
    let dtheta = 30f64.to_radians() * (-((hbar.to_degrees() - 275.0) / 25.0).powi(2)).exp();
    let r_term = -(2.0 * dtheta).sin() * rc * c_term * h_term;
    (l_term * l_term + c_term * c_term + h_term * h_term + r_term).max(0.0).sqrt()
}

/// numpy's default (linear) percentile of an unsorted slice.
pub fn percentile(values: &[f64], q: f64) -> f64 {
    let mut v = values.to_vec();
    v.sort_by(|a, b| a.total_cmp(b));
    let pos = q / 100.0 * (v.len() - 1) as f64;
    let (lo, frac) = (pos.floor() as usize, pos - pos.floor());
    if lo + 1 < v.len() { v[lo] + (v[lo + 1] - v[lo]) * frac } else { v[lo] }
}

pub fn delta_e(a_rgb: &[u8], b_rgb: &[u8]) -> (f64, f64) {
    let (la, lb) = (lab(a_rgb), lab(b_rgb));
    let de: Vec<f64> = la.iter().zip(&lb).map(|(x, y)| ciede2000(*x, *y)).collect();
    (de.iter().sum::<f64>() / de.len() as f64, percentile(&de, 95.0))
}
```

If a Lab value is off beyond 1e-9, check skimage's installed `color/colorconv.py` (`xyz_from_rgb`, `_illuminants['D65']['2']`, the `xyz2lab` linear branch) and match it; the mean uses numpy's pairwise summation, so compare the mean at 1e-9 relative if the plain sum drifts.

- [ ] **Step 4: Run the tests**

Run: `cargo test -p studi0trace-core --test color`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add crates/studi0trace-core backend/tools/export_core_fixtures.py
git commit -m "core: compositing, CIELAB and CIEDE2000 as scikit-image computes them"
```

---

### Task 7: Edges and edge F1

**Files:**
- Create: `crates/studi0trace-core/src/edges.rs`, `crates/studi0trace-core/tests/edges.rs`, fixture `edges.json`
- Modify: lib.rs, the exporter (`edges`)

**Interfaces:**
- Consumes: `vexel_rs::core::filters` (the engine already twins scipy's `gaussian_filter`; reuse it where its mode matches skimage canny's).
- Produces: `edges::canny(gray: &[f64], h: usize, w: usize, sigma: f64) -> Vec<bool>` (skimage `feature.canny` with its default thresholds and mask); `edges::edges(rgb: &[u8], h, w) -> Vec<bool>` (`quality._edges`: Rec. 601 luma in float32 / 255, sigma 1); `edges::dilate_disk(mask: &[bool], h, w, r: usize) -> Vec<bool>` (skimage `dilation(mask, disk(r))`); `edges::f1(ea, eb, ea_wide: Option<&[bool]>, h, w, tolerance_px) -> f64` (`quality._edge_f1`).

- [ ] **Step 1: Export masks**

```python
@exporter("edges")
def _edges():
    import numpy as np
    from PIL import Image
    from studi0trace.imaging.quality import _edges, edge_f1, to_rgb_on_white
    cases = []
    for rel in ("real/logo/vexel-wordmark-512.png", "synthetic/logo/venn-128.png", "real/logo/logomark-128.png"):
        rgba = np.asarray(Image.open(ROOT / "backend/bench/corpus" / rel).convert("RGBA"))
        rgb = to_rgb_on_white(rgba)
        e = _edges(rgb)
        shifted = np.roll(rgb, 1, axis=1)
        cases.append({"item": rel, "edges": np.flatnonzero(e).tolist(), "h": rgb.shape[0], "w": rgb.shape[1],
                      "f1_shifted": edge_f1(rgb, shifted)})
    write("edges", cases)
```

- [ ] **Step 2: Write the failing test**

```rust
// crates/studi0trace-core/tests/edges.rs
mod common;
use studi0trace_core::{color, edges, intake};

#[test]
fn canny_matches_skimage_and_f1_matches_the_python() {
    for case in common::fixture_json("edges.json").as_array().unwrap() {
        let bytes = std::fs::read(common::backend(&format!("bench/corpus/{}", case["item"].as_str().unwrap()))).unwrap();
        let img = intake::load(&bytes, Default::default()).unwrap();
        let (h, w) = (img.height as usize, img.width as usize);
        let rgb = color::rgb_on_white(&img.rgba);
        let got: Vec<usize> = edges::edges(&rgb, h, w).iter().enumerate().filter(|(_, e)| **e).map(|(i, _)| i).collect();
        let want: Vec<usize> = case["edges"].as_array().unwrap().iter().map(|v| v.as_u64().unwrap() as usize).collect();
        let common_px = got.iter().filter(|i| want.binary_search(i).is_ok()).count();
        assert!(common_px as f64 >= 0.995 * want.len() as f64 && got.len() as f64 <= 1.005 * want.len() as f64, "{}", case["item"]);
        let shifted: Vec<u8> = (0..h).flat_map(|r| { let row = &rgb[r * w * 3..(r + 1) * w * 3]; let mut out = row[(w - 1) * 3..].to_vec(); out.extend_from_slice(&row[..(w - 1) * 3]); out }).collect();
        let f1 = edges::f1(&edges::edges(&rgb, h, w), &edges::edges(&shifted, h, w), None, h, w, 2);
        assert!((f1 - case["f1_shifted"].as_f64().unwrap()).abs() < 0.005, "{}", case["item"]);
    }
}
```

- [ ] **Step 3: Implement edges.rs.** Port skimage's Canny from the installed source (`.venv/lib/python3.12/site-packages/skimage/feature/_canny.py`, function `canny` and `_preprocess`): the Gaussian smoothing with the eroded-mask normalisation it does, Sobel (`ndi.sobel` with its `mode`), the magnitude, the four-direction non-maximum suppression with interpolation, and hysteresis by connected components with the defaults `low_threshold = 0.1`, `high_threshold = 0.2` for float input. Port `disk(r)` as the set of offsets with `dx² + dy² <= r²`. Port `_edge_f1` as written in `quality.py` (lines 175-189 at commit `2e53411`). The luma is computed in `f32` exactly as `quality.luminance` (`0.299 r + 0.587 g + 0.114 b`), then divided by 255 in `f64`.

- [ ] **Step 4: Run the test**

Run: `cargo test -p studi0trace-core --test edges`
Expected: PASS. Differences of a handful of pixels at hysteresis ties are allowed by the 0.5 % band; anything larger is a porting error.

- [ ] **Step 5: Commit**

```bash
git add crates/studi0trace-core backend/tools/export_core_fixtures.py
git commit -m "core: skimage-compatible Canny edges and the scorecard's edge F1"
```

---

### Task 8: Rendering with resvg

**Files:**
- Create: `crates/studi0trace-core/src/render.rs`, `crates/studi0trace-core/tests/render.rs`, fixtures `render_*.png`, `render.json`
- Modify: `crates/studi0trace-core/Cargo.toml` (add `resvg`), lib.rs, the exporter (`render`)

**Interfaces:**
- Produces: `render::render(svg: &str, width: u32, height: u32, crisp: bool) -> Result<Vec<u8>, String>` returning RGBA8 (straight alpha, as `quality.render` returns after Pillow's `convert("RGBA")`).

- [ ] **Step 1: Pin resvg.** `resvg-py` 0.5.0 (what the bench renders with) declares `resvg = { version = "0.48.0", features = ["raster-images", "text"] }`. Add `resvg = { version = "=0.48.0", default-features = false }` to the core: traced SVGs carry no text or raster images, so neither feature changes a pixel, and leaving them out keeps fonts out of the WebAssembly build. If a render test ever disagrees on an SVG that does use text, add the `text` feature with an empty font database rather than system fonts.

- [ ] **Step 2: Export renders**

```python
@exporter("render")
def _render():
    import numpy as np
    from PIL import Image
    from studi0trace.imaging.quality import render
    from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
    from studi0trace.imaging.intake import load_upload
    names = []
    for rel in ("real/logo/vexel-wordmark-512.png", "synthetic/shadow/glow-128.png"):
        png = (ROOT / "backend/bench/corpus" / rel).read_bytes()
        img = load_upload(png, max_bytes=1 << 30, max_pixels=1 << 30)
        svg = VexelEngine().trace(img, VexelParams()).svg
        stem = Path(rel).stem
        (OUT / f"render_{stem}.svg").write_text(svg, encoding="utf-8")
        for crisp in (False, True):
            out = render(svg, img.width * 2, img.height * 2, crisp=crisp)
            Image.fromarray(out).save(OUT / f"render_{stem}_{'crisp' if crisp else 'aa'}.png")
        names.append({"stem": stem, "width": img.width * 2, "height": img.height * 2})
    write("render", names)
```

- [ ] **Step 3: Write the failing test**

```rust
// crates/studi0trace-core/tests/render.rs
mod common;
use studi0trace_core::{intake, render};

#[test]
fn renders_match_resvg_py_pixel_for_pixel() {
    for case in common::fixture_json("render.json").as_array().unwrap() {
        let stem = case["stem"].as_str().unwrap();
        let (w, h) = (case["width"].as_u64().unwrap() as u32, case["height"].as_u64().unwrap() as u32);
        let svg = String::from_utf8(common::fixture_bytes(&format!("render_{stem}.svg"))).unwrap();
        for (crisp, tag) in [(false, "aa"), (true, "crisp")] {
            let got = render::render(&svg, w, h, crisp).unwrap();
            let want = intake::load(&common::fixture_bytes(&format!("render_{stem}_{tag}.png")), Default::default()).unwrap();
            let worst = got.iter().zip(&want.rgba).map(|(a, b)| (*a as i32 - *b as i32).abs()).max().unwrap();
            assert!(worst <= 1, "{stem} {tag}: worst channel difference {worst}");
        }
    }
}
```

- [ ] **Step 4: Implement render.rs.** Parse with `usvg::Tree::from_str` (no font database), render into a `tiny_skia::Pixmap` of `width × height` with the transform that scales the SVG's size to the target the way `resvg_py.svg_to_bytes(width, height)` does (non-uniform scale is not applied by resvg; `quality.render` then resizes with Pillow — replicate: if the rendered size differs from the target, resize with nearest for crisp and Lanczos otherwise; the fixtures only exercise the exact-size path). Crisp: set `shape_rendering = CrispEdges` on every path (usvg exposes it per path; set the tree option or walk the tree). Convert the premultiplied pixmap to straight alpha the way Pillow's PNG round trip does (`c * 255 / a`, rounded).

- [ ] **Step 5: Run the test**

Run: `cargo test -p studi0trace-core --test render --release`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add crates/studi0trace-core backend/tools/export_core_fixtures.py
git commit -m "core: resvg rendering, anti-aliased and crisp, pinned to the renderer the bench uses"
```

---

### Task 9: The SVG drawing parser

**Files:**
- Create: `crates/studi0trace-core/src/drawing.rs`, `crates/studi0trace-core/tests/drawing.rs`, fixture `drawing.json`
- Modify: lib.rs, the exporter (`drawing`)

**Interfaces:**
- Produces: `drawing::Contour { pts: Vec<[f64; 2]>, closed: bool, element: usize, fill: Option<String>, stroke: Option<String>, stroke_width: f64, opacity: f64, segments: usize }` and `drawing::Drawing { contours: Vec<Contour>, elements: usize, scale: f64 }`, ported from `quality.py`'s `Contour` and `Drawing` (lines 419-437 at `2e53411`); `drawing::parse(svg: &str, size: Option<(u32, u32)>) -> Drawing` (port of `parse`, lines 487-614, with `_matrix`, `_apply`, `_cubic`, `_quad`, `_arc`, `path_polylines`, `_rect`, `_ellipse`, `_f`, `_segments_in`, `_paint`, `_unit`, `_opacity`). Read those Python functions before writing Rust; keep every field the Python keeps, with the same names.

- [ ] **Step 1: Export parse summaries**

```python
@exporter("drawing")
def _drawing():
    from studi0trace.imaging import quality
    cases = []
    for svg_file in sorted(OUT.glob("render_*.svg")) + sorted((ROOT / "backend/bench/heldout").glob("**/*.svg"))[:6]:
        svg = svg_file.read_text(encoding="utf-8")
        d = quality.parse(svg)
        cases.append({"file": svg_file.relative_to(ROOT).as_posix(), "elements": d.elements,
                      "contours": [{"n": len(c.pts), "closed": bool(c.closed), "element": c.element,
                                    "sum": [float(c.pts[:, 0].sum()), float(c.pts[:, 1].sum())], "segments": c.segments}
                                   for c in d.contours]})
    write("drawing", cases)
```

Run after Task 8's exporter so the `render_*.svg` files exist. Check the attribute names (`element`, `segments`) against `class Contour` and adjust the exporter to what the class really holds.

- [ ] **Step 2: Write the failing test**

```rust
// crates/studi0trace-core/tests/drawing.rs
mod common;
use studi0trace_core::drawing;

#[test]
fn every_contour_is_sampled_as_the_python_samples_it() {
    for case in common::fixture_json("drawing.json").as_array().unwrap() {
        let svg = std::fs::read_to_string(common::backend(&format!("../{}", case["file"].as_str().unwrap()))).unwrap();
        let d = drawing::parse(&svg, None);
        assert_eq!(d.elements as u64, case["elements"].as_u64().unwrap(), "{}", case["file"]);
        let want = case["contours"].as_array().unwrap();
        assert_eq!(d.contours.len(), want.len(), "{}", case["file"]);
        for (c, w) in d.contours.iter().zip(want) {
            assert_eq!((c.pts.len() as u64, c.closed), (w["n"].as_u64().unwrap(), w["closed"].as_bool().unwrap()));
            let sx: f64 = c.pts.iter().map(|p| p[0]).sum();
            let sy: f64 = c.pts.iter().map(|p| p[1]).sum();
            assert!((sx - w["sum"][0].as_f64().unwrap()).abs() < 1e-6 * (1.0 + sx.abs()) && (sy - w["sum"][1].as_f64().unwrap()).abs() < 1e-6 * (1.0 + sy.abs()));
        }
    }
}
```

- [ ] **Step 3: Implement drawing.rs** as a port of the functions listed above, using `roxmltree` for the XML (add `roxmltree = "0.20"`; `ElementTree` semantics the Python relies on: namespace-qualified tags, attribute inheritance down `<g>`, `<use>` resolving `href`/`xlink:href` into `<defs>`). Keep the sampling step counts the Python uses in `_cubic`, `_quad` and `_arc`.

- [ ] **Step 4: Run the test**

Run: `cargo test -p studi0trace-core --test drawing`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crates/studi0trace-core backend/tools/export_core_fixtures.py
git commit -m "core: the scorecard's SVG parser, ported from quality.parse"
```

---

### Task 10: Holes

**Files:**
- Create: `crates/studi0trace-core/src/holes.rs`, `crates/studi0trace-core/tests/holes.rs`, fixture `holes.json`
- Modify: lib.rs, the exporter (`holes`)

**Interfaces:**
- Consumes: `render::render`, and the engine's `vexel_rs::core::labels::label_mask` (connected components) and `vexel_rs::core::morphology` (3×3 erosion with a border value; the junction port added a square erosion).
- Produces: `holes::Opaque` (the source's eroded opaque mask, `quality.Reference.opaque`); `holes::opaque(src_rgba: &[u8], h, w) -> Vec<bool>`; `holes::holes(svg: &str, src_rgba: &[u8], h: usize, w: usize, scale: u32, opaque: Option<&[bool]>) -> serde_json::Map<String, Value>` with exactly the keys `quality.holes` returns (lines 930-968 at `2e53411`: `hole_subpx`, `hole_px`, `hole_clusters`, `pinholes`, and the `_holes_at` locations).

- [ ] **Step 1: Export**

```python
@exporter("holes")
def _holes():
    import numpy as np
    from PIL import Image
    from studi0trace.imaging import quality
    sources = {"vexel-wordmark-512": "real/logo/vexel-wordmark-512.png", "glow-128": "synthetic/shadow/glow-128.png"}
    cases = []
    for stem, rel in sources.items():
        svg = (OUT / f"render_{stem}.svg").read_text(encoding="utf-8")
        rgba = np.asarray(Image.open(ROOT / "backend/bench/corpus" / rel).convert("RGBA"))
        for scale in (4, 2):
            card = quality.holes(svg, rgba, scale)
            cases.append({"stem": stem, "source": f"bench/corpus/{rel}", "scale": scale,
                          "card": {k: (float(v) if not isinstance(v, (int, bool)) else v) for k, v in card.items() if not k.startswith("_")}})
    write("holes", cases)
```

- [ ] **Step 2: Write the failing test**

```rust
// crates/studi0trace-core/tests/holes.rs
mod common;
use studi0trace_core::{holes, intake};

#[test]
fn hole_counts_match_the_python() {
    for case in common::fixture_json("holes.json").as_array().unwrap() {
        let src = intake::load(&std::fs::read(common::backend(case["source"].as_str().unwrap())).unwrap(), Default::default()).unwrap();
        let svg = String::from_utf8(common::fixture_bytes(&format!("render_{}.svg", case["stem"].as_str().unwrap()))).unwrap();
        let got = holes::holes(&svg, &src.rgba, src.height as usize, src.width as usize, case["scale"].as_u64().unwrap() as u32, None);
        for (k, v) in case["card"].as_object().unwrap() {
            let (a, b) = (got[k].as_f64().unwrap(), v.as_f64().unwrap());
            assert!((a - b).abs() <= 1e-9 * (1.0 + b.abs()), "{} x{} {k}: {a} vs {b}", case["stem"], case["scale"]);
        }
    }
}
```
- [ ] **Step 3: Implement** as a port of `quality.holes` and the opaque mask in `Reference.__init__` (erosion of `alpha >= 0.99` by a 3×3 square with `border_value=0`); clusters are 8-connected.
- [ ] **Step 4: Run the test**: `cargo test -p studi0trace-core --test holes --release`. Expected: PASS.
- [ ] **Step 5: Commit**: `git commit -m "core: hole and pinhole counts, ported from quality.holes"`.

---

### Task 11: The geometry card

**Files:**
- Create: `crates/studi0trace-core/src/geometry.rs`, `crates/studi0trace-core/tests/geometry.rs`, fixture `geometry.json`
- Modify: lib.rs, the exporter (`geometry`)

**Interfaces:**
- Consumes: `drawing::parse`, `render::render` (the crisp id map).
- Produces: `geometry::card(svg: &str, size: Option<(u32, u32)>, visibility: bool, id_scale: u32) -> serde_json::Map<String, Value>` with the keys `quality.geometry_card` returns (lines 969-1086 at `2e53411`), and the helpers it needs as private functions: `id_map`, `lookup`, `visible_samples`, `resample`, `wrap`, `turns`, `cancelled`, `flips`, `dilate`, `inflections`, `CornerInfo`, `corners`, `rect_like`, `area` (lines 615-929). Every module-level constant in `quality.py` lines 82-109 becomes a `pub const` with the same name, value and comment.

- [ ] **Step 1: Export**

```python
@exporter("geometry")
def _geometry():
    from studi0trace.imaging import quality
    from studi0trace.engines.presets import fixed_presets
    from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
    from studi0trace.imaging.intake import load_upload
    png = (ROOT / "backend/bench/corpus/real/logo/vexel-wordmark-512.png").read_bytes()
    img = load_upload(png, max_bytes=1 << 30, max_pixels=1 << 30)
    svgs = {f"wordmark-{p.id}": VexelEngine().trace(img, VexelParams(**p.params)).svg for p in fixed_presets()}
    for f in sorted((ROOT / "backend/bench/heldout").glob("**/*.svg"))[:6]:
        svgs[f"heldout-{f.stem}"] = f.read_text(encoding="utf-8")
    cases = []
    for name, svg in svgs.items():
        (OUT / f"geometry_{name}.svg").write_text(svg, encoding="utf-8")
        for vis in ((True, False) if name in ("wordmark-balanced", "wordmark-flat") else (True,)):
            card = quality.geometry_card(svg, (512, 512), visibility=vis)
            cases.append({"name": name, "visibility": vis,
                          "card": {k: (float(v) if not isinstance(v, (int, bool)) else v) for k, v in card.items() if not k.startswith("_")}})
    write("geometry", cases)
```

Check the held-out SVGs' own size before passing `(512, 512)`: if their viewBox is not 512, pass `None` and let the parser read the size, as `quality.geometry_card` allows.

- [ ] **Step 2: Write the failing test**

```rust
// crates/studi0trace-core/tests/geometry.rs
mod common;
use studi0trace_core::geometry;

#[test]
fn geometry_cards_match_the_python() {
    for case in common::fixture_json("geometry.json").as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let svg = String::from_utf8(common::fixture_bytes(&format!("geometry_{name}.svg"))).unwrap();
        let got = geometry::card(&svg, Some((512, 512)), case["visibility"].as_bool().unwrap(), 2);
        for (k, v) in case["card"].as_object().unwrap() {
            let (a, b) = (got[k].as_f64().unwrap(), v.as_f64().unwrap());
            assert!((a - b).abs() <= 1e-9 * (1.0 + b.abs()), "{name} {k}: {a} vs {b}");
        }
    }
}
```
- [ ] **Step 3: Implement**, one Python function at a time in file order, each with a unit test of its own against values printed from the Python for one wordmark contour (add those to the fixture as `helpers`), then `card`. numpy semantics to replicate: `np.unwrap` (discontinuity π, period 2π), `np.cumsum`, `np.roll`, boolean fancy indexing order, `np.percentile` (use `color::percentile`).
- [ ] **Step 4: Run the test**: `cargo test -p studi0trace-core --test geometry --release`. Expected: PASS.
- [ ] **Step 5: Commit**: `git commit -m "core: the geometry card (wobble, inflections, rectangles), ported from quality.geometry_card"`.

---

### Task 12: Scorecard, artifact index and assessment

**Files:**
- Create: `crates/studi0trace-core/src/scorecard.rs`, `crates/studi0trace-core/tests/scorecard.rs`, fixture `scorecard.json`
- Modify: lib.rs, the exporter (`scorecard`)

**Interfaces:**
- Consumes: `holes`, `geometry`, `color`, `edges`, `render`.
- Produces: `scorecard::Reference::new(src_rgba: &[u8], h: usize, w: usize) -> Reference` (lab, edges, wide edges, opaque, computed once); `Reference::fidelity(&self, out_rgba: &[u8]) -> Map` (`delta_e_mean`, `delta_e_p95`, `edge_f1`); `scorecard::scorecard(svg, src_rgba, h, w, hole_scale, id_scale, opaque) -> Map`; `scorecard::artifact_index(&Map) -> f64`; `scorecard::is_clean(&Map) -> bool`; `scorecard::assess(svg: &str, r: &Reference) -> Map` (hole scale 4 up to 640×640, else 2, as `quality.assess`).

- [ ] **Step 1: Export**: `quality.assess(svg, Reference(rgba))` for the wordmark traced with each of the four Auto candidates and for three held-out items at Balanced.
- [ ] **Step 2: Write the failing test**

```rust
// crates/studi0trace-core/tests/scorecard.rs
mod common;
use studi0trace_core::{intake, scorecard};

#[test]
fn assessments_match_the_python() {
    for case in common::fixture_json("scorecard.json").as_array().unwrap() {
        let src = intake::load(&std::fs::read(common::backend(case["source"].as_str().unwrap())).unwrap(), Default::default()).unwrap();
        let r = scorecard::Reference::new(&src.rgba, src.height as usize, src.width as usize);
        let got = scorecard::assess(case["svg"].as_str().unwrap(), &r);
        for (k, v) in case["card"].as_object().unwrap() {
            let (a, b) = (got[k].as_f64().unwrap(), v.as_f64().unwrap());
            let tol = if k == "edge_f1" { 0.005 } else { 1e-6 * (1.0 + b.abs()) };
            assert!((a - b).abs() <= tol, "{} {k}: {a} vs {b}", case["name"]);
        }
        assert_eq!(scorecard::is_clean(&got), case["clean"].as_bool().unwrap());
    }
}
```

- [ ] **Step 3: Implement** as a port of `quality.py` lines 195-218 (`Reference`) and 1087-1135 (`scorecard`, `artifact_index`, `is_clean`, `assess`); `artifact_index` and `is_clean` are short enough to copy term for term.
- [ ] **Step 4: Run the test**: `cargo test -p studi0trace-core --test scorecard --release`. Expected: PASS.
- [ ] **Step 5: Commit**: `git commit -m "core: the artifact scorecard and fidelity assessment, equal to the Python's"`.

---

### Task 13: Auto

**Files:**
- Create: `crates/studi0trace-core/src/auto.rs`, `crates/studi0trace-core/tests/auto.rs`
- Modify: lib.rs

**Interfaces:**
- Consumes: `presets::auto_candidates`, `params::parse`, `scorecard::{Reference, assess, is_clean}`, `vexel_rs::engine::trace_rgba`, `svg::{normalize_dimensions, stats}`.
- Produces: `auto::Scored { id: String, delta_e: f64, edge_f1: f64, artifact_index: f64, elements: u64 }`; `auto::choose(scored: &[Scored]) -> (Option<usize>, &'static str)`; `auto::issues(card: &Map) -> Vec<String>`; `auto::summary(card: &Map) -> Value` (the `CandidateScores` shape); `auto::run(img: &intake::Image) -> AutoOutcome` where `AutoOutcome { pick: Option<String>, reason: String, candidates: Vec<Candidate> }` and `Candidate { preset, label, svg: Option<String>, elapsed_ms: Option<f64>, stats: Option<svg::Stats>, parameters: Option<Map>, scores: Option<Value>, error: Option<ErrorBody> }`, serialising to the API's `AutoResult` (with `engine: "vexel"`).

- [ ] **Step 1: Port the tests.** Translate each of the seven tests in `backend/tests/test_auto.py` (`test_picks_the_lowest_artifact_index_within_the_fidelity_band` through `test_issues_name_what_a_designer_would_circle`) into `tests/auto.rs`, keeping their names and numbers. Read the Python file and copy each assertion.

- [ ] **Step 2: Run them to see them fail**: `cargo test -p studi0trace-core --test auto`. Expected: compile error.

- [ ] **Step 3: Implement the rule**

```rust
// crates/studi0trace-core/src/auto.rs
//! Auto: trace with every candidate preset, score each against the source,
//! keep the cleanest of those as faithful as the best. Ported from
//! `studi0trace/auto.py`; the reasons finish "Auto chose <preset> — …".
pub const DE_SLACK: f64 = 0.15;
pub const DE_SHARE: f64 = 0.30;
pub const EDGE_SLACK: f64 = 0.02;

#[derive(Debug, Clone)]
pub struct Scored { pub id: String, pub delta_e: f64, pub edge_f1: f64, pub artifact_index: f64, pub elements: u64 }

pub fn de_limit(best: f64) -> f64 { best + DE_SLACK.max(DE_SHARE * best) }

fn round1(x: f64) -> f64 { (x * 10.0).round() / 10.0 }

fn key(s: &Scored, i: usize) -> (i64, u64, usize) { ((round1(s.artifact_index) * 10.0).round() as i64, s.elements, i) }

pub fn faithful(scored: &[Scored]) -> Vec<usize> {
    if scored.is_empty() { return vec![]; }
    let limit = de_limit(scored.iter().map(|s| s.delta_e).fold(f64::INFINITY, f64::min));
    let ok: Vec<usize> = (0..scored.len()).filter(|i| scored[*i].delta_e <= limit).collect();
    let best_edge = ok.iter().map(|i| scored[*i].edge_f1).fold(f64::NEG_INFINITY, f64::max);
    ok.into_iter().filter(|i| scored[*i].edge_f1 >= best_edge - EDGE_SLACK).collect()
}

pub fn choose(scored: &[Scored]) -> (Option<usize>, &'static str) {
    if scored.is_empty() { return (None, "no candidate could be scored"); }
    let ok = faithful(scored);
    let pick = *ok.iter().min_by_key(|i| key(&scored[**i], **i)).unwrap();
    let most_faithful = (0..scored.len()).min_by(|a, b| scored[*a].delta_e.total_cmp(&scored[*b].delta_e).then(a.cmp(b))).unwrap();
    let cleanest = (0..scored.len()).min_by_key(|i| key(&scored[*i], *i)).unwrap();
    if scored.len() == 1 { return (Some(pick), "the only candidate that traced"); }
    if ok.len() == 1 { return (Some(pick), "the only one this faithful to the image"); }
    if pick == cleanest && pick == most_faithful { return (Some(pick), "the most faithful, and the cleanest"); }
    if ok.iter().any(|i| *i != pick && round1(scored[*i].artifact_index) == round1(scored[pick].artifact_index)) {
        return (Some(pick), "as clean at the same fidelity, with fewer shapes");
    }
    if pick == cleanest { return (Some(pick), "the cleanest at the same fidelity"); }
    if pick == most_faithful { return (Some(pick), "the most faithful; the cleaner ones lose detail"); }
    (Some(pick), "the cleanest of the most faithful")
}
```

Python's `round(x, 1)` rounds half to even; `f64::round` rounds half away from zero. Replace `round1` with a half-to-even implementation and add a test for `0.25 -> 0.2` and `0.35 -> 0.4` before relying on it. Port `issues` and `summary` from `auto.py` lines 93-126 in the same file (plural rules as written there).

- [ ] **Step 4: Implement `run`.** Decode nothing (the caller passes an `intake::Image`); build one `scorecard::Reference`; trace the four candidates in parallel with `rayon::prelude::*` (`par_iter` over `presets::auto_candidates()`), each timed with `std::time::Instant` and finished with `svg::normalize_dimensions`; assess each; a candidate whose trace panics (`std::panic::catch_unwind`) gets `error: {code: "engine_failed", message}` and is left out of `choose`, as `routes._run_auto` does.

- [ ] **Step 5: Add an end-to-end test**: trace the wordmark with `auto::run` and assert the pick is `"balanced"` and every candidate's scores match `scorecard.json`'s entries for that candidate (export them in Task 12 under names `auto_<preset>`).

- [ ] **Step 6: Run the tests**: `cargo test -p studi0trace-core --test auto --release`. Expected: all pass.

- [ ] **Step 7: Commit**: `git commit -m "core: Auto, its rule, reasons and issues, ported from auto.py"`.

---

### Task 14: The Core facade and its JSON

**Files:**
- Create: `crates/studi0trace-core/src/api.rs`, `crates/studi0trace-core/tests/api.rs`, `crates/studi0trace-core/examples/trace.rs`, fixture `api.json`
- Modify: lib.rs, the exporter (`api`)

**Interfaces:**
- Consumes: every module above.
- Produces (what plans 2 and 3 call):

```rust
pub struct Core { images: std::sync::Mutex<std::collections::HashMap<String, std::sync::Arc<intake::Image>>> }
impl Core {
    pub fn new() -> Core;
    /// GET /health: {status: "ok", version, engines: ["vexel"], vexel: "rust"}
    pub fn health(&self) -> serde_json::Value;
    /// GET /engines: [{id: "vexel", label, description, primary: true, params: params::schema(), defaults: params::defaults()}]
    pub fn engines(&self) -> serde_json::Value;
    /// GET /presets
    pub fn presets(&self) -> serde_json::Value;
    /// POST /uploads: decode, keep, and return {image_id, width, height, format}
    pub fn upload(&self, bytes: &[u8]) -> Result<serde_json::Value, ErrorBody>;
    /// POST /vectorize for engine "vexel": {success, image_id, width, height, results: {vexel: EngineResult}, parameters_used, auto}
    pub fn vectorize(&self, image_id: &str, parameters: &serde_json::Value, auto: bool) -> Result<serde_json::Value, ErrorBody>;
}
#[derive(serde::Serialize, Debug, Clone)] pub struct ErrorBody { pub code: String, pub message: String }
```

- [ ] **Step 1: Export API responses.** With the FastAPI `TestClient` (`from fastapi.testclient import TestClient; from studi0trace.main import app`): `GET /health`, `GET /engines` (keep only the `vexel` entry), `GET /presets`, `POST /uploads` of the wordmark, `POST /vectorize` with `engines=vexel` and default parameters, and with `auto=true`. Store the responses with `svg` fields replaced by their SHA-256 and `elapsed_ms` removed.
- [ ] **Step 2: Write the failing test**: call the same five methods on `Core`, apply the same replacement, and compare JSON values (`version` excepted, which is the crate's).
- [ ] **Step 3: Implement api.rs.** `image_id` is the SHA-256 hex of the uploaded bytes (stable, so re-uploading the same file reuses the entry); an unknown id returns `ErrorBody { code: "image_expired", … }` as the Python does. `vectorize` maps `params::ParamError` to `validation_error` with the message `"<field>: <message>"`, as `api.ts`'s `toApiError` expects.
- [ ] **Step 4: Write the example**

```rust
// crates/studi0trace-core/examples/trace.rs
//! cargo run -p studi0trace-core --release --example trace -- IMAGE.png [preset|auto] > out.svg
fn main() {
    let args: Vec<String> = std::env::args().collect();
    let bytes = std::fs::read(&args[1]).expect("read image");
    let mode = args.get(2).map(String::as_str).unwrap_or("auto");
    let core = studi0trace_core::api::Core::new();
    let up = core.upload(&bytes).expect("decode");
    let id = up["image_id"].as_str().unwrap();
    let params = match mode { "auto" => serde_json::json!({}), p => serde_json::Value::Object(studi0trace_core::presets::by_id(p).expect("preset").params) };
    let res = core.vectorize(id, &params, mode == "auto").expect("trace");
    if let Some(a) = res["auto"]["vexel"].as_object() { eprintln!("Auto chose {} — {}", a["pick"], a["reason"]); }
    print!("{}", res["results"]["vexel"]["svg"].as_str().unwrap());
}
```

- [ ] **Step 5: Run the tests and the example**

Run: `cargo test -p studi0trace-core --release` then `cargo run -p studi0trace-core --release --example trace -- backend/bench/corpus/real/logo/vexel-wordmark-512.png auto > /tmp/wordmark.svg` — writing to `/tmp` is fine for a manual check; delete it after.
Expected: all tests pass; stderr reads `Auto chose "balanced" — …`; the SVG's SHA-256 equals `api.json`'s Auto pick.

- [ ] **Step 6: Commit**: `git commit -m "core: the Core facade, returning the API's JSON, and a trace example"`.

---

### Task 15: The scorecard parity stage and docs

**Files:**
- Create: `crates/studi0trace-core/src/python.rs`
- Modify: `crates/studi0trace-core/src/lib.rs` (`#[cfg(feature = "python")] mod python;`), `backend/tools/diffcheck.py` (a `scorecard` stage), `CLAUDE.md`, `README.md`

**Interfaces:**
- Consumes: `scorecard::{Reference, assess}`.
- Produces: the Python module `studi0trace_core` with `assess(svg: str, rgba: bytes, width: int, height: int) -> dict`, built with `.venv/bin/python -m maturin develop --release -m crates/studi0trace-core/Cargo.toml --features python`.

- [ ] **Step 1: Write the binding**

```rust
// crates/studi0trace-core/src/python.rs
use pyo3::prelude::*;
use pyo3::types::PyDict;

#[pyfunction]
fn assess<'py>(py: Python<'py>, svg: &str, rgba: Vec<u8>, width: usize, height: usize) -> PyResult<Bound<'py, PyDict>> {
    let card = py.allow_threads(|| {
        let r = crate::scorecard::Reference::new(&rgba, height, width);
        crate::scorecard::assess(svg, &r)
    });
    let out = PyDict::new_bound(py);
    for (k, v) in card { out.set_item(k, v.as_f64().unwrap_or(f64::NAN))?; }
    Ok(out)
}

#[pymodule]
fn studi0trace_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(assess, m)?)?;
    Ok(())
}
```

- [ ] **Step 2: Add the diffcheck stage.** In `tools/diffcheck.py`, following the pattern of the existing stages: for each corpus item, trace with the Python reference at defaults, score the SVG with `studi0trace.imaging.quality.assess` and with `studi0trace_core.assess`, and compare every key (counts exactly, floats to 1e-6, `edge_f1` to 0.005). Add its row to the tolerance table with the reason for the `edge_f1` band (hysteresis ties in Canny).

- [ ] **Step 3: Run it**: `cd backend && .venv/bin/python -m tools.diffcheck scorecard`. Expected: `0 failing (stage, item) pairs`.

- [ ] **Step 4: Document.** In `CLAUDE.md`'s layout section add `crates/studi0trace-core` (what it is, that `tools/export_core_fixtures.py` regenerates its fixtures, that `diffcheck scorecard` holds it to the Python) and the workspace commands (`cargo test --workspace --release`). In `README.md`'s Tests section add `cargo test --workspace --release`.

- [ ] **Step 5: Run everything**

Run: `cargo test --workspace --release`, `cd backend && .venv/bin/python -m pytest -q`, `cd frontend && npm run test:run`.
Expected: all green.

- [ ] **Step 6: Commit**: `git commit -m "core: the scorecard's parity stage in diffcheck; workspace documented"`.

---

## Self-review

- **Coverage:** intake (T4), parameters and schema (T2), presets (T3), SVG finishing (T5), scorecard (T6–T12), Auto (T13), the facade and the API's JSON (T14), parity with the Python (every task's fixtures, T15). Potrace and VTracer are not ported: the core describes one engine.
- **Types used across tasks:** `intake::Image`, `params::parse -> VexelParams`, `presets::Preset`, `svg::Stats`, `scorecard::Reference`, `auto::{Scored, run}`, `api::{Core, ErrorBody}`, consistent between the tasks that produce and consume them.
- **Known gaps left to later plans:** rayon as an optional feature for WebAssembly (plan 3); JPEG decoding differs from Pillow by design (T4 bounds it).
