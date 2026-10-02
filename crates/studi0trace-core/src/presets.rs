//! The named parameter bundles and their measured one-line claims. Ported from
//! `backend/studi0trace/engines/presets.py`, which `tests/presets.rs` holds this
//! list to (the JSON of `GET /presets`, key for key).
//!
//! The bundles are `backend/studi0trace/engines/presets.json`, one file the
//! Python reads and this embeds, so the two cannot list different presets. The
//! lines are `preset_details.json`, written by `bench.presets_eval
//! --write-details`; a preset without one says it is not measured yet.
use std::sync::OnceLock;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

const BUNDLES: &str = include_str!("../../../backend/studi0trace/engines/presets.json");
const DETAILS: &str = include_str!("../../../backend/studi0trace/engines/preset_details.json");
const UNMEASURED: &str = "not measured yet";

/// A preset as `GET /presets` lists it. The fields are in the order the Pydantic
/// model declares them, which is the order FastAPI writes them in.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Preset {
    pub id: String,
    pub label: String,
    pub engine: String,
    /// What it is for, with Auto's `{candidates}` already filled.
    pub description: String,
    /// What it measurably costs or buys, from the bench corpus.
    pub detail: String,
    /// Thumbnail filename under `/presets/`.
    pub sample: String,
    /// Layered over the engine's defaults, never over current values. Keys stay
    /// in the order the bundle file has them.
    pub params: Map<String, Value>,
    /// `"auto"` traces with every candidate and picks per image; `"preset"` is a
    /// fixed bundle.
    pub kind: String,
    /// Whether Auto tries this preset.
    pub auto_candidate: bool,
}

/// An entry of presets.json: `kind` only on Auto, `auto_candidate` only on the
/// four Auto tries, `params` on every entry (Auto's are empty).
#[derive(Deserialize)]
struct Bundle {
    id: String,
    label: String,
    #[serde(default)]
    kind: Option<String>,
    #[serde(default)]
    auto_candidate: bool,
    description: String,
    sample: String,
    params: Map<String, Value>,
}

/// "A", "A and B", "A, B and C".
fn and(words: &[String]) -> String {
    match words {
        [] => String::new(),
        [only] => only.clone(),
        [init @ .., last] => format!("{} and {}", init.join(", "), last),
    }
}

fn build() -> Vec<Preset> {
    let bundles: Vec<Bundle> = serde_json::from_str(BUNDLES).expect("presets.json is the list of bundles");
    // Like the Python, a details file that will not read leaves every preset unmeasured.
    let details: Value = serde_json::from_str(DETAILS).unwrap_or(Value::Null);
    let lines = details.get("lines");
    let candidates: Vec<String> = bundles.iter().filter(|b| b.auto_candidate).map(|b| b.label.clone()).collect();
    let listed = and(&candidates);
    bundles
        .into_iter()
        .map(|b| Preset {
            detail: lines.and_then(|l| l.get(&b.id)).and_then(Value::as_str).unwrap_or(UNMEASURED).to_string(),
            description: b.description.replace("{candidates}", &listed),
            kind: b.kind.unwrap_or_else(|| "preset".into()),
            engine: "vexel".into(),
            id: b.id,
            label: b.label,
            sample: b.sample,
            params: b.params,
            auto_candidate: b.auto_candidate,
        })
        .collect()
}

fn list() -> &'static [Preset] {
    static LIST: OnceLock<Vec<Preset>> = OnceLock::new();
    LIST.get_or_init(build)
}

/// Every preset, Auto first, as the API lists them.
pub fn all() -> Vec<Preset> {
    list().to_vec()
}

/// What Auto traces with, in preference order (a tie goes to the earlier).
pub fn auto_candidates() -> Vec<Preset> {
    list().iter().filter(|p| p.auto_candidate).cloned().collect()
}

pub fn by_id(id: &str) -> Option<Preset> {
    list().iter().find(|p| p.id == id).cloned()
}
