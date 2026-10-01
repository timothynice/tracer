//! `api` against the FastAPI app (`api/routes.py`, `main.py`): the five calls the frontend makes and the
//! failures the routes raise, answered by the real app in `api.json` and by `Core` here, and compared as
//! JSON STRINGS (so a key out of its place fails, not only a value) after both sides are normalised the
//! same way: each `svg` by its SHA-256, `elapsed_ms` as null, `image_id` as a placeholder (the Python's is
//! random, the core's is a content hash; both are 32 lowercase hex digits, which is checked apart) and the
//! health's `version` (the crate's own). Then what the Python server does not show a single request of:
//! the upload store (the byte cap, the order it evicts in, the same bytes twice), a `Core` shared by
//! threads, and a trace that panics.
mod common;

use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::Cursor;
use std::sync::mpsc;
use std::sync::Arc;
use std::time::Duration;
use studi0trace_core::api::{ApiError, Core, ErrorBody};
use studi0trace_core::intake::{IntakeError, Limits};
use studi0trace_core::{auto, params, presets, VERSION};

const EXPIRED: &str = "Upload expired or unknown; upload it again";

// ---------------------------------------------------------------- the golden

/// The bytes a case uploads: a corpus image (named relative to `backend/`) or bytes the fixture carries.
fn bytes_of(golden: &Value, source: &str) -> Vec<u8> {
    let entry = &golden["sources"][source];
    if let Some(file) = entry["file"].as_str() {
        return std::fs::read(common::backend(file)).unwrap_or_else(|e| panic!("{file}: {e}"));
    }
    base64(entry["b64"].as_str().unwrap_or_else(|| panic!("{source}: no such source")))
}

fn base64(text: &str) -> Vec<u8> {
    let value = |c: u8| match c {
        b'A'..=b'Z' => c - b'A',
        b'a'..=b'z' => c - b'a' + 26,
        b'0'..=b'9' => c - b'0' + 52,
        b'+' => 62,
        b'/' => 63,
        other => panic!("not base64: {other}"),
    };
    let mut out = Vec::new();
    let (mut acc, mut bits) = (0u32, 0);
    for c in text.bytes().filter(|&c| c != b'=') {
        acc = (acc << 6) | u32::from(value(c));
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    out
}

fn is_image_id(id: &str) -> bool {
    id.len() == 32 && id.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

/// The normalisation both sides get: see the top of this file.
fn normalise(v: &mut Value) {
    match v {
        Value::Object(m) => {
            for (k, child) in m.iter_mut() {
                match (k.as_str(), &*child) {
                    ("svg", Value::String(s)) => *child = Value::String(common::sha256_hex(s.as_bytes())),
                    ("elapsed_ms", _) => *child = Value::Null,
                    ("image_id", Value::String(id)) => {
                        assert!(is_image_id(id), "image_id {id:?} is not 32 lowercase hex digits");
                        *child = json!("<image_id>");
                    }
                    _ => normalise(child),
                }
            }
        }
        Value::Array(items) => items.iter_mut().for_each(normalise),
        _ => {}
    }
}

/// What the engine's floats decide (`libm` rounds an ulp differently on another platform than the one the fixtures
/// were exported on, as in `common::exact`): the SVG, the counts read off it, Auto's scores, pick and reason, and
/// the parameters of the pick. Blanked on both sides there; the keys, their order and everything else stay compared.
fn blank(v: &mut Value, auto: bool) {
    match v {
        Value::Object(m) => {
            for (k, child) in m.iter_mut() {
                match (k.as_str(), &*child) {
                    ("svg", Value::String(_)) => *child = json!("<svg>"),
                    ("stats" | "scores", Value::Object(_)) => child.as_object_mut().unwrap().values_mut().for_each(|x| *x = Value::Null),
                    ("pick" | "reason", Value::String(_)) => *child = json!("<auto>"),
                    ("parameters_used", _) if auto => *child = json!("<the pick's>"),
                    _ => blank(child, auto),
                }
            }
        }
        Value::Array(items) => items.iter_mut().for_each(|i| blank(i, auto)),
        _ => {}
    }
}

/// Every `elapsed_ms` that is not null, whatever it is nested in.
fn times(v: &Value, out: &mut Vec<f64>) {
    match v {
        Value::Object(m) => {
            for (k, child) in m {
                match (k.as_str(), child) {
                    ("elapsed_ms", Value::Number(n)) => out.push(n.as_f64().unwrap()),
                    _ => times(child, out),
                }
            }
        }
        Value::Array(items) => items.iter().for_each(|i| times(i, out)),
        _ => {}
    }
}

/// What the app answers: the status, and the body (`{"detail": …}` for a failure).
fn failed(e: &ApiError) -> (u16, Value) {
    (e.status, e.response_body())
}

fn limits_of(case: &Value) -> Limits {
    match case.get("limits") {
        Some(l) => Limits { max_bytes: l["max_bytes"].as_u64().unwrap() as usize, max_pixels: l["max_pixels"].as_u64().unwrap() },
        None => Limits::default(),
    }
}

/// Where a context between two strings differs, for a message a person can read.
fn first_difference(got: &str, want: &str) -> String {
    let at = got.bytes().zip(want.bytes()).position(|(a, b)| a != b).unwrap_or(got.len().min(want.len()));
    let around = |s: &str| {
        let from = at.saturating_sub(60);
        let to = (at + 100).min(s.len());
        s.get(from..to).unwrap_or("(not on a character boundary)").to_string()
    };
    format!("at byte {at}:\n  got  …{}…\n  want …{}…", around(got), around(want))
}

#[test]
fn every_call_answers_as_the_fastapi_app_does_to_the_letter() {
    let golden = common::fixture_json("api.json");
    let cases = golden["cases"].as_array().unwrap();
    assert!(cases.len() >= 45, "the fixture is thin: {} cases", cases.len());
    let mut cores: HashMap<String, Core> = HashMap::new();
    let mut ids: HashMap<String, String> = HashMap::new();
    let mut checked = HashMap::<&str, usize>::new();
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let limits = limits_of(case);
        let core = cores.entry(format!("{limits:?}")).or_insert_with(|| Core::with_limits(limits, 256 << 20));
        let (status, mut body): (u16, Value) = match case["call"].as_str().unwrap() {
            "health" => {
                let mut h = core.health();
                assert_eq!(h["version"], json!(VERSION), "{name}");
                h["version"] = json!("<version>");
                (200, h)
            }
            "engines" => (200, core.engines()),
            "presets" => (200, core.presets()),
            "upload" => {
                let bytes = bytes_of(&golden, case["source"].as_str().unwrap());
                match core.upload(&bytes) {
                    Ok(v) => (200, v),
                    Err(e) => failed(&e),
                }
            }
            "vectorize" => {
                let id = match case["image"].as_str() {
                    Some(source) => ids
                        .entry(source.to_string())
                        .or_insert_with(|| core.upload(&bytes_of(&golden, source)).unwrap()["image_id"].as_str().unwrap().to_string())
                        .clone(),
                    None => case["image_id"].as_str().unwrap().to_string(),
                };
                match core.vectorize(&id, &case["parameters"], case["auto"].as_bool().unwrap()) {
                    Ok(v) => {
                        assert_eq!(v["image_id"], json!(id), "{name}: the response names the upload it traced");
                        (200, v)
                    }
                    Err(e) => failed(&e),
                }
            }
            other => panic!("{name}: no such call {other}"),
        };
        assert_eq!(status, case["status"].as_u64().unwrap() as u16, "{name}: status");
        // a wall time is a positive number wherever there is a trace, and the fixture holds only that it is there
        let mut t = Vec::new();
        times(&body, &mut t);
        assert!(t.iter().all(|&ms| ms > 0.0), "{name}: elapsed_ms {t:?}");
        normalise(&mut body);
        let mut want = case["body"].clone();
        if !common::exact() {
            let auto = case["auto"].as_bool() == Some(true);
            blank(&mut body, auto);
            blank(&mut want, auto);
        }
        let (got, want) = (serde_json::to_string(&body).unwrap(), serde_json::to_string(&want).unwrap());
        assert!(got == want, "{name}: the body differs {}\n(an `svg` field is compared by its SHA-256 here; {})", first_difference(&got, &want), common::REEXPORT);
        *checked.entry(case["call"].as_str().unwrap()).or_default() += 1;
    }
    // every kind of call was made, and by enough cases to mean something
    assert_eq!((checked["health"], checked["engines"], checked["presets"]), (1, 1, 1));
    assert!(checked["upload"] >= 8 && checked["vectorize"] >= 30, "{checked:?}");
}

#[test]
fn the_tolerant_comparison_blanks_what_the_engines_floats_decide_and_nothing_else() {
    let mut body = json!({
        "success": true, "image_id": "<image_id>", "width": 8,
        "results": {"vexel": {"svg": "ab", "elapsed_ms": null, "stats": {"paths": 3, "bytes": 90}, "error": null}},
        "parameters_used": {"vexel": {"detail": 6.0}},
        "auto": {"vexel": {"engine": "vexel", "pick": "dense", "reason": "why", "candidates": [
            {"preset": "dense", "svg": "cd", "scores": {"delta_e": 0.2, "clean": true}, "parameters": {"detail": 4.0}, "error": null}]}},
    });
    blank(&mut body, true);
    assert_eq!(
        body,
        json!({
            "success": true, "image_id": "<image_id>", "width": 8,
            "results": {"vexel": {"svg": "<svg>", "elapsed_ms": null, "stats": {"paths": null, "bytes": null}, "error": null}},
            "parameters_used": "<the pick's>",
            "auto": {"vexel": {"engine": "vexel", "pick": "<auto>", "reason": "<auto>", "candidates": [
                {"preset": "dense", "svg": "<svg>", "scores": {"delta_e": null, "clean": null}, "parameters": {"detail": 4.0}, "error": null}]}},
        })
    );
    // a plain trace's parameters are the request's own, and stay compared; a null pick (nothing traced) is a null
    let mut plain = json!({"parameters_used": {"vexel": {"detail": 6.0}}, "auto": {"vexel": {"pick": null}}});
    blank(&mut plain, false);
    assert_eq!(plain, json!({"parameters_used": {"vexel": {"detail": 6.0}}, "auto": {"vexel": {"pick": null}}}));
}

#[test]
fn the_golden_covers_what_the_brief_lists() {
    // the fixture is the contract: it must hold each kind of answer, or the loop above would pass over a gap
    let golden = common::fixture_json("api.json");
    let by_name: HashMap<&str, &Value> = golden["cases"].as_array().unwrap().iter().map(|c| (c["name"].as_str().unwrap(), c)).collect();
    let status = |n: &str| by_name[n]["status"].as_u64().unwrap();
    let code = |n: &str| by_name[n]["body"]["detail"]["code"].as_str().unwrap().to_string();
    for n in ["upload_wordmark", "upload_mark128", "default_wordmark", "changed_wordmark", "auto_wordmark", "refined_wordmark"] {
        assert_eq!(status(n), 200, "{n}");
    }
    assert_eq!((code("upload_garbage"), code("upload_corrupt_png"), code("upload_too_large"), code("upload_too_many_pixels")), (
        "unsupported_format".to_string(),
        "corrupt_image".to_string(),
        "too_large".to_string(),
        "too_many_pixels".to_string()
    ));
    assert_eq!((status("expired"), code("expired")), (404, "image_expired".to_string()));
    assert_eq!((status("no_image"), code("no_image")), (400, "no_image".to_string()));
    assert!(by_name.keys().filter(|n| n.starts_with("refused_")).count() >= 15);
    assert!(by_name.keys().filter(|n| n.starts_with("falsy_")).count() >= 5);
    // Auto is held to all four candidates: a route that promoted the first would pass on a golden of balanced picks
    let picks: std::collections::BTreeSet<&str> =
        by_name.values().filter_map(|c| c["body"]["auto"]["vexel"]["pick"].as_str()).collect();
    assert_eq!(picks.into_iter().collect::<Vec<_>>(), ["balanced", "dense", "detailed", "logo"]);
    assert!(is_image_id(&"a".repeat(32)) && !is_image_id(&"a".repeat(31)) && !is_image_id(&"A".repeat(32)));
    assert_eq!(golden["python_engines"], json!(["vexel", "potrace", "vtracer"]), "the Python lists three engines; the core describes one");
}

#[test]
fn a_wall_time_is_a_positive_number_wherever_there_is_a_trace() {
    let core = Core::new();
    let id = upload_png(&core, 24, 1);
    let v = core.vectorize(&id, &json!({}), false).unwrap();
    assert!(v["results"]["vexel"]["elapsed_ms"].as_f64().unwrap() > 0.0);
    let v = core.vectorize(&id, &json!({}), true).unwrap();
    assert!(v["results"]["vexel"]["elapsed_ms"].as_f64().unwrap() > 0.0);
    for c in v["auto"]["vexel"]["candidates"].as_array().unwrap() {
        assert!(c["elapsed_ms"].as_f64().unwrap() > 0.0);
    }
}

// ---------------------------------------------------------------- small images

/// A PNG of `size`² pixels no other `seed` makes: a gradient over a flat field, so it traces.
fn png(size: u32, seed: u8) -> Vec<u8> {
    let img = image::RgbaImage::from_fn(size, size, |x, y| {
        if (x / 4 + y / 4) % 2 == 0 {
            image::Rgba([seed.wrapping_mul(37), (x * 255 / size) as u8, (y * 255 / size) as u8, 255])
        } else {
            image::Rgba([240, 240, 240, 255])
        }
    });
    let mut out = Cursor::new(Vec::new());
    image::DynamicImage::ImageRgba8(img).write_to(&mut out, image::ImageFormat::Png).unwrap();
    out.into_inner()
}

fn upload_png(core: &Core, size: u32, seed: u8) -> String {
    core.upload(&png(size, seed)).unwrap()["image_id"].as_str().unwrap().to_string()
}

fn expired(core: &Core, id: &str) -> bool {
    match core.vectorize(id, &json!({}), false) {
        Err(e) => {
            assert_eq!((e.status, e.body.code.as_str(), e.body.message.as_str()), (404, "image_expired", EXPIRED));
            true
        }
        Ok(_) => false,
    }
}

// ---------------------------------------------------------------- the ids and the store

#[test]
fn an_id_is_32_lowercase_hex_digits_and_the_same_bytes_are_the_same_id() {
    let core = Core::new();
    let bytes = png(16, 1);
    let first = core.upload(&bytes).unwrap();
    let again = core.upload(&bytes).unwrap();
    assert_eq!(first, again, "the same file is the same entry");
    let id = first["image_id"].as_str().unwrap();
    assert!(is_image_id(id), "{id}");
    assert_eq!((core.cached_images(), core.cached_bytes()), (1, 16 * 16 * 4), "one entry, counted once");
    let other = upload_png(&core, 16, 2);
    assert_ne!(other, id);
    assert_eq!(core.cached_images(), 2);
    // the id is a function of the bytes, not of the Core
    assert_eq!(Core::new().upload(&bytes).unwrap()["image_id"], first["image_id"]);
}

#[test]
fn upload_answers_with_the_image_id_width_height_and_format_in_that_order() {
    let core = Core::new();
    let v = core.upload(&png(20, 1)).unwrap();
    let keys: Vec<&str> = v.as_object().unwrap().keys().map(String::as_str).collect();
    assert_eq!(keys, ["image_id", "width", "height", "format"]);
    assert_eq!((&v["width"], &v["height"], &v["format"]), (&json!(20), &json!(20), &json!("PNG")));
}

#[test]
fn the_oldest_upload_is_the_first_to_go_when_the_byte_cap_is_passed() {
    // an entry is its pixels, 16 * 16 * 4 = 1024 bytes: three fit
    let core = Core::with_limits(Limits::default(), 3 * 1024);
    let ids: Vec<String> = (0..5).map(|seed| upload_png(&core, 16, seed)).collect();
    assert_eq!((core.cached_images(), core.cached_bytes()), (3, 3 * 1024));
    assert!(expired(&core, &ids[0]) && expired(&core, &ids[1]), "the two oldest are gone");
    assert!(!expired(&core, &ids[2]) && !expired(&core, &ids[3]) && !expired(&core, &ids[4]), "the three newest stay");
}

#[test]
fn what_was_used_last_is_what_stays_as_the_pythons_cache_moves_a_hit_to_the_end() {
    let core = Core::with_limits(Limits::default(), 3 * 1024);
    let (a, b, c) = (upload_png(&core, 16, 1), upload_png(&core, 16, 2), upload_png(&core, 16, 3));
    assert!(!expired(&core, &a), "tracing `a` makes it the newest");
    let d = upload_png(&core, 16, 4);
    assert!(expired(&core, &b), "so `b` is the oldest, and goes");
    assert!(!expired(&core, &a) && !expired(&core, &c) && !expired(&core, &d));
    // uploading a file that is already there is a use of it too
    assert_eq!(core.upload(&png(16, 1)).unwrap()["image_id"], json!(a));
    let e = upload_png(&core, 16, 5);
    assert!(expired(&core, &c), "`a` was refreshed by the re-upload, `c` is the oldest");
    assert!(!expired(&core, &a) && !expired(&core, &e));
}

#[test]
fn an_image_larger_than_the_whole_cap_is_still_kept_and_alone() {
    // the Python empties the cache and keeps the new entry whatever its size
    let core = Core::with_limits(Limits::default(), 1000);
    let small = upload_png(&core, 8, 1);
    assert!(!expired(&core, &small));
    let big = upload_png(&core, 16, 2);
    assert_eq!(core.cached_images(), 1);
    assert!(expired(&core, &small) && !expired(&core, &big));
}

#[test]
fn a_refused_upload_is_not_kept() {
    let core = Core::with_limits(Limits { max_bytes: 100, max_pixels: 40_000_000 }, 1 << 20);
    assert!(core.upload(&png(16, 1)).is_err());
    assert_eq!((core.cached_images(), core.cached_bytes()), (0, 0));
}

#[test]
fn the_defaults_are_the_servers_settings() {
    // settings.py: max_upload_cache_bytes
    assert_eq!(studi0trace_core::api::MAX_UPLOAD_CACHE_BYTES, 256 * 1024 * 1024);
    let core = Core::new();
    assert_eq!((core.cached_images(), core.cached_bytes()), (0, 0));
}

// ---------------------------------------------------------------- the answers' shape

fn keys(v: &Value) -> Vec<&str> {
    v.as_object().unwrap().keys().map(String::as_str).collect()
}

#[test]
fn a_vectorize_response_has_the_apis_fields_in_the_apis_order() {
    let core = Core::new();
    let id = upload_png(&core, 24, 1);
    let v = core.vectorize(&id, &json!({}), false).unwrap();
    assert_eq!(keys(&v), ["success", "image_id", "width", "height", "results", "parameters_used", "auto"]);
    assert_eq!(v["success"], json!(true));
    assert_eq!(keys(&v["results"]), ["vexel"]);
    assert_eq!(keys(&v["results"]["vexel"]), ["svg", "elapsed_ms", "stats", "error"]);
    assert_eq!(keys(&v["results"]["vexel"]["stats"]), ["paths", "nodes", "bytes", "gradients", "unique_fills"]);
    assert_eq!((&v["results"]["vexel"]["error"], &v["auto"]), (&Value::Null, &Value::Null));
    assert_eq!(keys(&v["parameters_used"]), ["vexel"]);
    assert_eq!(Value::Object(params::defaults()), v["parameters_used"]["vexel"]);
    assert!(v["results"]["vexel"]["svg"].as_str().unwrap().contains("viewBox=\"0 0 24 24\""));
    let auto = core.vectorize(&id, &json!({}), true).unwrap();
    assert_eq!(keys(&auto), ["success", "image_id", "width", "height", "results", "parameters_used", "auto"]);
    assert_eq!(keys(&auto["auto"]), ["vexel"]);
    assert_eq!(keys(&auto["auto"]["vexel"]), ["engine", "pick", "reason", "candidates"]);
}

#[test]
fn health_is_ok_with_the_crates_version_and_the_one_engine() {
    let h = Core::new().health();
    assert_eq!(h, json!({"status": "ok", "version": VERSION, "engines": ["vexel"], "vexel": "rust"}));
    assert_eq!(keys(&h), ["status", "version", "engines", "vexel"]);
}

#[test]
fn the_engine_list_is_the_vexel_entry_with_the_schema_the_ui_builds_its_controls_from() {
    let e = Core::new().engines();
    let list = e.as_array().unwrap();
    assert_eq!(list.len(), 1);
    assert_eq!(keys(&list[0]), ["id", "label", "description", "primary", "params", "defaults"]);
    assert_eq!((&list[0]["id"], &list[0]["label"], &list[0]["primary"]), (&json!("vexel"), &json!("Vexel"), &json!(true)));
    assert_eq!(list[0]["params"], params::schema());
    assert_eq!(list[0]["defaults"], Value::Object(params::defaults()));
}

#[test]
fn the_presets_are_the_list_get_presets_sends() {
    let p = Core::new().presets();
    assert_eq!(p, serde_json::to_value(presets::all()).unwrap());
    assert_eq!(p[0]["id"], json!("auto"));
}

// ---------------------------------------------------------------- the failures

#[test]
fn a_missing_empty_or_unknown_image_is_the_routes_400_and_404() {
    let core = Core::new();
    let e = core.vectorize("", &json!({}), false).unwrap_err();
    assert_eq!((e.status, e.body.code.as_str(), e.body.message.as_str()), (400, "no_image", "Send either `file` or `image_id`"));
    for id in ["0".repeat(32), "not-an-id".to_string(), "../etc/passwd".to_string()] {
        for auto in [false, true] {
            let e = core.vectorize(&id, &json!({}), auto).unwrap_err();
            assert_eq!((e.status, e.body.code.as_str(), e.body.message.as_str()), (404, "image_expired", EXPIRED));
        }
    }
}

#[test]
fn the_parameters_are_checked_before_the_upload_is_looked_up_as_the_route_does() {
    let core = Core::new();
    let e = core.vectorize(&"0".repeat(32), &json!({"detail": 0.5}), false).unwrap_err();
    assert_eq!(e.status, 422);
    let e = core.vectorize("", &json!({"detail": 0.5}), true).unwrap_err();
    assert_eq!(e.status, 422, "and before the missing image");
}

#[test]
fn every_intake_refusal_is_a_400_with_the_intakes_code_and_words() {
    let tiny = Core::with_limits(Limits { max_bytes: 40, max_pixels: 40_000_000 }, 1 << 20);
    let e = tiny.upload(&png(16, 1)).unwrap_err();
    assert_eq!((e.status, e.body.code.as_str(), e.body.message.as_str()), (400, "too_large", "File exceeds the 0 MB limit"));
    let flat = Core::with_limits(Limits { max_bytes: 1 << 20, max_pixels: 100 }, 1 << 20);
    let e = flat.upload(&png(16, 1)).unwrap_err();
    assert_eq!((e.status, e.body.code.as_str()), (400, "too_many_pixels"));
    let e = Core::new().upload(b"definitely not an image").unwrap_err();
    assert_eq!((e.status, e.body.code.as_str(), e.body.message.as_str()), (400, "unsupported_format", "File is not a recognised image"));
    let mut truncated = png(32, 1);
    truncated.truncate(truncated.len() * 6 / 10);
    let e = Core::new().upload(&truncated).unwrap_err();
    assert_eq!((e.status, e.body.code.as_str()), (400, "corrupt_image"));
}

#[test]
fn an_api_error_serialises_as_the_detail_fastapi_sends_and_reads_as_a_line() {
    let core = Core::new();
    let e = core.vectorize("", &json!({}), false).unwrap_err();
    assert_eq!(serde_json::to_value(&e).unwrap(), json!({"code": "no_image", "message": "Send either `file` or `image_id`"}));
    assert_eq!(e.response_body(), json!({"detail": {"code": "no_image", "message": "Send either `file` or `image_id`"}}));
    assert_eq!(e.to_string(), "400 no_image: Send either `file` or `image_id`");
    let _: &dyn std::error::Error = &e;

    let id = upload_png(&core, 16, 1);
    let e = core.vectorize(&id, &json!({"detail": 0.5, "colour": 1}), false).unwrap_err();
    assert_eq!(e.status, 422);
    let want = json!([
        {"type": "greater_than_equal", "loc": ["vexel", "detail"], "msg": "Input should be greater than or equal to 1", "input": 0.5, "ctx": {"ge": 1.0}},
        {"type": "extra_forbidden", "loc": ["vexel", "colour"], "msg": "Extra inputs are not permitted", "input": 1},
    ]);
    assert_eq!(serde_json::to_string(&e).unwrap(), serde_json::to_string(&want).unwrap());
    assert_eq!(e.response_body(), json!({"detail": want}));
    // what `toApiError` of the frontend makes of that list, for a shell that has no use for the list
    assert_eq!(e.body, ErrorBody { code: "validation_error".into(), message: "vexel.detail: Input should be greater than or equal to 1".into() });
    assert_eq!(e.to_string(), "422 validation_error: vexel.detail: Input should be greater than or equal to 1");
}

#[test]
fn a_parameters_value_that_is_not_an_object_is_refused_at_vexel_itself() {
    let core = Core::new();
    let id = upload_png(&core, 16, 1);
    let e = core.vectorize(&id, &json!("abc"), false).unwrap_err();
    assert_eq!(
        e.response_body(),
        json!({"detail": [{"type": "model_type", "loc": ["vexel"], "msg": "Input should be a valid dictionary or instance of VexelParams",
                           "input": "abc", "ctx": {"class_name": "VexelParams"}}]})
    );
    assert_eq!(e.body.message, "vexel: Input should be a valid dictionary or instance of VexelParams");
}

#[test]
fn whatever_is_falsy_is_the_defaults_and_nothing_else_is() {
    // `all_params.get(engine.id) or {}`
    let core = Core::new();
    let id = upload_png(&core, 16, 1);
    let want = core.vectorize(&id, &json!({}), false).unwrap();
    let svg = |v: &Value| v["results"]["vexel"]["svg"].clone();
    for falsy in [json!(null), json!(false), json!(0), json!(0.0), json!(-0.0), json!(""), json!([]), json!({})] {
        let got = core.vectorize(&id, &falsy, false).unwrap_or_else(|e| panic!("{falsy}: {e}"));
        assert_eq!(svg(&got), svg(&want), "{falsy}");
        assert_eq!(got["parameters_used"], want["parameters_used"], "{falsy}");
    }
    for truthy in [json!(true), json!(1), json!(0.5), json!("a"), json!([0]), json!([[]])] {
        let e = core.vectorize(&id, &truthy, false).unwrap_err();
        assert_eq!((e.status, e.violations[0].kind), (422, "model_type"), "{truthy}");
    }
}

#[test]
fn auto_validates_the_parameters_it_does_not_use_and_reports_the_picks_own() {
    let core = Core::new();
    let id = upload_png(&core, 24, 3);
    let e = core.vectorize(&id, &json!({"detail": 99}), true).unwrap_err();
    assert_eq!(e.status, 422);
    let v = core.vectorize(&id, &json!({"detail": 12.5}), true).unwrap();
    let pick = v["auto"]["vexel"]["pick"].as_str().unwrap();
    let chosen = v["auto"]["vexel"]["candidates"].as_array().unwrap().iter().find(|c| c["preset"] == json!(pick)).unwrap();
    assert_eq!(v["parameters_used"]["vexel"], chosen["parameters"], "the pick's parameters, not the request's");
    assert_ne!(v["parameters_used"]["vexel"]["detail"], json!(12.5));
    assert_eq!(v["results"]["vexel"]["svg"], chosen["svg"]);
    assert_eq!(v["results"]["vexel"]["stats"], chosen["stats"]);
    assert_eq!(v["results"]["vexel"]["elapsed_ms"], chosen["elapsed_ms"]);
    assert_eq!(v["results"]["vexel"]["error"], Value::Null);
}

#[test]
fn the_parameters_used_are_every_parameter_in_the_models_order_and_kind() {
    let core = Core::new();
    let id = upload_png(&core, 16, 1);
    let v = core.vectorize(&id, &json!({"detail": 10, "min_region": 16.0, "strokes": false}), false).unwrap();
    let used = &v["parameters_used"]["vexel"];
    assert_eq!(keys(used), params::fields().iter().map(|f| f.name).collect::<Vec<_>>());
    assert!(used["detail"].is_f64() && used["min_region"].is_u64(), "{used}");
    assert_eq!((&used["detail"], &used["min_region"], &used["strokes"]), (&json!(10.0), &json!(16), &json!(false)));
}

// ---------------------------------------------------------------- a trace that panics

fn real(img: &studi0trace_core::intake::Image, p: &vexel_rs::engine::VexelParams) -> String {
    vexel_rs::engine::trace_rgba(&img.rgba, img.height as usize, img.width as usize, p)
}

/// A core whose trace panics for an image 7 pixels wide, as the engine does for one it cannot take.
fn core_with_a_bad_width() -> Core {
    Core::with_tracer(Limits::default(), 1 << 28, |img, p| {
        if img.width == 7 {
            panic!("kaboom at {}x{}", img.width, img.height);
        }
        real(img, p)
    })
}

#[test]
fn a_panic_in_the_engine_is_that_engines_error_and_the_core_carries_on() {
    let core = core_with_a_bad_width();
    let bad = upload_png(&core, 7, 1);
    let good = upload_png(&core, 16, 2);
    let v = core.vectorize(&bad, &json!({"detail": 9}), false).unwrap();
    assert_eq!(v["success"], json!(true), "the request succeeds; the engine's entry says what went wrong");
    let r = &v["results"]["vexel"];
    assert_eq!((&r["svg"], &r["elapsed_ms"], &r["stats"]), (&Value::Null, &Value::Null, &Value::Null));
    assert_eq!(r["error"]["code"], json!("engine_crashed"));
    assert!(r["error"]["message"].as_str().unwrap().contains("kaboom at 7x7"), "{r}");
    assert_eq!(keys(r), ["svg", "elapsed_ms", "stats", "error"]);
    assert_eq!(v["parameters_used"]["vexel"]["detail"], json!(9.0), "what was asked for, as the route reports it");
    // the store still answers: the lock was not held by the trace, and a panic did not poison it
    assert!(core.vectorize(&good, &json!({}), false).unwrap()["results"]["vexel"]["svg"].is_string());
    assert!(core.upload(&png(8, 9)).is_ok());
    assert_eq!(core.cached_images(), 3);
}

#[test]
fn every_candidate_failing_is_the_first_ones_error_and_no_pick_and_the_requests_own_parameters() {
    let core = core_with_a_bad_width();
    let bad = upload_png(&core, 7, 1);
    let v = core.vectorize(&bad, &json!({"detail": 9}), true).unwrap();
    let auto = &v["auto"]["vexel"];
    assert_eq!((&auto["pick"], &auto["reason"]), (&Value::Null, &json!("every candidate failed")));
    let first = &auto["candidates"][0]["error"];
    assert_eq!(first["code"], json!("engine_crashed"));
    assert_eq!(&v["results"]["vexel"]["error"], first, "the engine's error is the first candidate's");
    assert_eq!(v["results"]["vexel"]["svg"], Value::Null);
    // nothing was chosen, so the route leaves `parameters_used` as validated
    assert_eq!(v["parameters_used"]["vexel"]["detail"], json!(9.0));
}

/// Whether the store answers a thread of its own while `trace` runs: the lock must not be held by whoever traces.
#[test]
fn the_store_is_not_locked_while_an_image_is_traced_plain_or_auto() {
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::OnceLock;
    let slot: Arc<OnceLock<Arc<Core>>> = Arc::new(OnceLock::new());
    let answered = Arc::new(AtomicUsize::new(0));
    let (probe, count) = (slot.clone(), answered.clone());
    let core = Arc::new(Core::with_tracer(Limits::default(), 1 << 28, move |img, p| {
        let core = probe.get().expect("the core is in its slot").clone();
        let (tx, rx) = mpsc::channel();
        // `cached_images` takes the store's lock: a thread that cannot get it would wait for this trace to end
        std::thread::spawn(move || tx.send((core.cached_images(), core.upload(&png(6, 99)).is_ok())).unwrap());
        let (_, uploaded) = rx.recv_timeout(Duration::from_secs(30)).expect("the store is locked while the image is traced");
        assert!(uploaded);
        count.fetch_add(1, Ordering::SeqCst);
        real(img, p)
    }));
    assert!(slot.set(core.clone()).is_ok());
    let id = upload_png(&core, 16, 1);
    core.vectorize(&id, &json!({}), false).unwrap();
    assert_eq!(answered.load(Ordering::SeqCst), 1);
    core.vectorize(&id, &json!({}), true).unwrap();
    assert_eq!(answered.load(Ordering::SeqCst), 1 + presets::auto_candidates().len(), "every candidate was traced with the store free");
}

// ---------------------------------------------------------------- shared by threads

#[test]
fn core_is_send_and_sync_and_an_arc_of_it_is_what_a_shell_shares() {
    fn shareable<T: Send + Sync + 'static>() {}
    shareable::<Core>();
    shareable::<Arc<Core>>();
    shareable::<ApiError>();
    shareable::<ErrorBody>();
}

#[test]
fn four_threads_trace_two_images_at_once_and_get_what_one_thread_gets() {
    let core = Arc::new(Core::new());
    let images = [
        std::fs::read(common::backend("bench/corpus/real/logo/studi0trace-mark-128.png")).unwrap(),
        std::fs::read(common::backend("bench/corpus/synthetic/logo/venn-128.png")).unwrap(),
    ];
    let ids: Vec<String> = images.iter().map(|b| core.upload(b).unwrap()["image_id"].as_str().unwrap().to_string()).collect();
    let svg = |v: &Value| v["results"]["vexel"]["svg"].as_str().unwrap().to_string();
    let alone: Vec<(String, String)> = ids
        .iter()
        .map(|id| (svg(&core.vectorize(id, &json!({}), false).unwrap()), svg(&core.vectorize(id, &json!({}), true).unwrap())))
        .collect();
    assert_ne!(alone[0].0, alone[1].0);

    let (tx, rx) = mpsc::channel();
    for thread in 0..4 {
        let (core, ids, tx, images) = (core.clone(), ids.clone(), tx.clone(), images.clone());
        std::thread::spawn(move || {
            let mut got = Vec::new();
            for round in 0..2 {
                // each thread starts on a different image, and one of them also uploads and runs Auto while the others trace
                for k in 0..2 {
                    let which = (thread + round + k) % 2;
                    if thread == 3 {
                        core.upload(&images[which]).unwrap();
                    }
                    let plain = core.vectorize(&ids[which], &json!({}), false).unwrap();
                    got.push((which, false, plain["results"]["vexel"]["svg"].as_str().unwrap().to_string()));
                    if thread == 3 && round == 0 {
                        let auto = core.vectorize(&ids[which], &json!({}), true).unwrap();
                        got.push((which, true, auto["results"]["vexel"]["svg"].as_str().unwrap().to_string()));
                    }
                }
            }
            tx.send((thread, got)).unwrap();
        });
    }
    drop(tx);
    let mut finished = 0;
    while finished < 4 {
        let (thread, got) = rx.recv_timeout(Duration::from_secs(600)).expect("a thread did not finish: a deadlock?");
        assert!(!got.is_empty());
        for (which, auto, svg) in got {
            let want = if auto { &alone[which].1 } else { &alone[which].0 };
            assert!(&svg == want, "thread {thread}, image {which}, auto {auto}: not what one thread gets");
        }
        finished += 1;
    }
    assert_eq!(core.cached_images(), 2);
}

#[test]
fn threads_that_upload_the_same_file_share_one_entry() {
    let core = Arc::new(Core::new());
    let bytes = Arc::new(png(32, 7));
    let handles: Vec<_> = (0..8)
        .map(|_| {
            let (core, bytes) = (core.clone(), bytes.clone());
            std::thread::spawn(move || core.upload(&bytes).unwrap()["image_id"].as_str().unwrap().to_string())
        })
        .collect();
    let ids: Vec<String> = handles.into_iter().map(|h| h.join().unwrap()).collect();
    assert!(ids.windows(2).all(|w| w[0] == w[1]));
    assert_eq!((core.cached_images(), core.cached_bytes()), (1, 32 * 32 * 4));
}

// ---------------------------------------------------------------- the errors of the other modules

#[test]
fn the_other_modules_errors_are_errors_with_their_words() {
    let e: &dyn std::error::Error = &IntakeError { code: "too_large", message: "File exceeds the 0 MB limit".into() };
    assert_eq!(e.to_string(), "File exceeds the 0 MB limit");
    let e = auto::AutoError { code: "auto_unavailable", message: "no candidates".into() };
    assert!(std::error::Error::source(&e).is_none());
    assert_eq!(e.to_string(), "no candidates");
    let api: ApiError = e.into();
    assert_eq!((api.status, api.body.code.as_str()), (400, "auto_unavailable"));
    let api: ApiError = auto::AutoError { code: "invalid_image", message: "x".into() }.into();
    assert_eq!(api.status, 500);
    let api: ApiError = IntakeError { code: "corrupt_image", message: "m".into() }.into();
    assert_eq!((api.status, api.to_string()), (400, "400 corrupt_image: m".to_string()));
}

#[test]
fn the_error_body_is_the_two_fields_the_frontend_reads() {
    let b = ErrorBody { code: "c".into(), message: "m".into() };
    assert_eq!(serde_json::to_string(&b).unwrap(), r#"{"code":"c","message":"m"}"#);
    assert_eq!(b.to_string(), "c: m");
}
