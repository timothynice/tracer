//! The core as the app calls it: the five requests the frontend makes of the Python server
//! (`getHealth`, `getEngines`, `getPresets`, `uploadImage`, `vectorize`), answered with the
//! JSON the server sends, by a [`Core`] that a desktop shell or a web build holds in an `Arc`.
//! The Python (`api/routes.py`, `api/schemas.py`, `main.py`) is the reference, and
//! `tests/api.rs` compares the two as strings, key order included, over the answers of the
//! real app (`api.json`).
//!
//! | Python | here | status when it fails |
//! |---|---|---|
//! | `GET /health` | [`Core::health`] | |
//! | `GET /engines` | [`Core::engines`] (only Vexel: the core describes one engine) | |
//! | `GET /presets` | [`Core::presets`] | |
//! | `POST /uploads` | [`Core::upload`] | 400, with the intake's own code (`too_large`, `unsupported_format`, `too_many_pixels`, `corrupt_image`) |
//! | `POST /vectorize` `engines=vexel` | [`Core::vectorize`] | 422 (the parameters), 400 `no_image` (an empty `image_id`), 404 `image_expired` |
//!
//! Every intake refusal is a **400** in the Python (`routes._intake` raises `HTTPException(400, …)`
//! whatever the code): there is no 413 or 415. A shell answers its client with
//! [`ApiError::status`] and [`ApiError::response_body`], which is what FastAPI would have sent.
//!
//! # What `vectorize` takes
//!
//! `parameters` is the value of the `vexel` key of the request's `parameters` JSON (the route
//! reads `all_params.get(engine.id) or {}`): a falsy value (`null`, `false`, `0`, `""`, `[]`,
//! `{}`) is the defaults, and anything else that is not an object is refused at `vexel`
//! (`model_type`). The parameters are checked first, Auto or not, and before the upload is
//! looked up, as the route does: a bad value with an unknown image is a 422, not a 404. The
//! other failures of the route (`bad_parameters`, `unknown_engine`, `auto_unavailable`) are
//! about a request body to parse, an engine to pick and a preset list that cannot be empty;
//! with one engine, a `Value` and `presets.json` they cannot happen here.
//!
//! A trace that panics is that engine's `engine_crashed` entry in `results`, as an exception
//! in the Python is (the request still succeeds); one that fails to be Auto's is the candidate's
//! (see [`auto`]).
//!
//! # The upload store, and what it does not do
//!
//! `POST /uploads` keeps the decoded image and answers with an id, so a slider can trace the
//! same upload again and again. The Python keeps them in `imaging/cache.py`: an LRU, capped in
//! bytes, with a sliding TTL, under `uuid4().hex` ids. This is the same cache with two
//! differences, both for a program that has no server's reasons:
//!
//! - **The id is the first 128 bits of the SHA-256 of the file**, as 32 lowercase hex digits
//!   (the shape of the Python's `uuid4().hex`, which the frontend treats as opaque and its
//!   tests pin at 32 characters). Uploading the same file again is the same entry (the cost of
//!   a decode saved, and the bytes held once), it needs no source of randomness (a wasm build
//!   would need a JavaScript one), and an id means the same on every `Core`, which makes a
//!   test of it a test.
//! - **There is no TTL.** A TTL needs a clock, `std::time::Instant` panics on `wasm32`, and
//!   the core is not where a shell's idea of "the user has gone away" belongs: it can drop
//!   the `Core`, or call for a smaller cap. What stays is the byte cap, which is what bounds
//!   memory ([`MAX_UPLOAD_CACHE_BYTES`], 256 MiB as in `settings.py`): putting an image over
//!   it evicts the **least recently used** first (a `vectorize` or a repeated upload is a
//!   use, as the Python moves a hit to the end), and an image bigger than the whole cap is
//!   kept alone, as the Python keeps it. An entry counts as its pixels, `4 · width · height`:
//!   the Python also counts the file's bytes, which it holds on to and this does not.
//!
//! An evicted or unknown id is `image_expired`, which the frontend answers by uploading again.
//!
//! **An id is not a security boundary.** It is a hash of the file, so anyone who has the bytes
//! can compute it and an id says nothing about who uploaded the file: it names an entry, it does
//! not authorise a caller (the Python's random `uuid4` ids were no capability either, but they
//! could not be guessed from a file). A shell that serves more than one person must key its own
//! sessions, not rely on the id being secret.
//!
//! # Time, and `wasm32`
//!
//! The core's own clock is one: a trace is timed with `std::time::Instant`
//! (`auto::trace_finished`, which `elapsed_ms` is read from), and nothing else in this crate
//! reads the time. The engine's stage timer (`vexel_rs`'s `timing::Timer`) reads the clock
//! only when `VEXEL_TIMING` is set, which a web build does not set. On
//! `wasm32-unknown-unknown` `Instant::now()` **panics**, which a build with `panic=abort`
//! (that target's default) turns into a trap that ends the module: `catch_unwind` cannot catch
//! it where panics abort, so [`Core::vectorize`] cannot report it as an `engine_crashed`
//! entry. A web build therefore has to give `trace_finished` a clock before it can trace (plan
//! 3: a JavaScript `performance.now()` behind a parameter or a feature).
//!
//! # Threads
//!
//! [`Core`] is `Send + Sync`, and the store sits behind a `Mutex` that is held only to look an
//! id up or to put one in: never while an image is decoded (done before the lock is taken) or
//! traced (the image is an `Arc` taken out), so threads trace at once and a panic in a trace
//! cannot poison it. Should a lock be poisoned all the same it is taken over: the store's
//! operations are small and leave it consistent at every step.
use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use serde::{Serialize, Serializer};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use vexel_rs::engine::VexelParams;

pub use crate::auto::ErrorBody;
use crate::auto::{self, AutoError, AutoOutcome};
use crate::intake::{self, Image, IntakeError, Limits};
use crate::params::{self, ParamError};
use crate::presets::{self, Preset};
use crate::svg::Stats;
use crate::VERSION;

/// How many bytes of decoded uploads a [`Core`] keeps (`settings.py`: `max_upload_cache_bytes`).
pub const MAX_UPLOAD_CACHE_BYTES: usize = 256 * 1024 * 1024;

/// The one engine the core describes; the key of `results`, `parameters_used` and `auto`.
const ENGINE: &str = "vexel";
const LABEL: &str = "Vexel";
const DESCRIPTION: &str = "Studi0's fidelity-first engine: gradient-aware regions, sub-pixel edges, whole-shape fitting.";

// ---------------------------------------------------------------- the failures

/// One entry of the 422's list: what Pydantic writes of a parameter it refused, with the
/// engine's id in front of `loc` as the route puts it (`["vexel", "detail"]`).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Violation {
    /// Pydantic's error type: `greater_than_equal`, `extra_forbidden`, …
    #[serde(rename = "type")]
    pub kind: &'static str,
    pub loc: Vec<String>,
    pub msg: String,
    pub input: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ctx: Option<Map<String, Value>>,
}

impl From<&ParamError> for Violation {
    fn from(e: &ParamError) -> Violation {
        let mut loc = vec![ENGINE.to_string()];
        if !e.field.is_empty() {
            loc.push(e.field.clone());
        }
        Violation { kind: e.kind, loc, msg: e.message.clone(), input: e.input.clone(), ctx: e.ctx.clone() }
    }
}

/// A request the server would have refused: what a shell needs to answer it the way the server
/// does. It serialises as the `detail` of FastAPI's answer, [`ApiError::response_body`] is the
/// whole body, and [`ApiError::status`] the HTTP status.
///
/// For every failure but a 422, `detail` is `{code, message}` ([`ApiError::body`]). For a 422 it
/// is Pydantic's list ([`ApiError::violations`]), and `body` is what the frontend's `toApiError`
/// makes of that list: the code `validation_error` and `"<loc joined by .>: <msg>"` of the first
/// entry, for a shell that has no use for the list.
#[derive(Debug, Clone, PartialEq)]
pub struct ApiError {
    pub status: u16,
    pub body: ErrorBody,
    /// The 422's list; empty for every other failure.
    pub violations: Vec<Violation>,
}

impl ApiError {
    fn new(status: u16, code: &str, message: &str) -> ApiError {
        ApiError { status, body: ErrorBody::new(code, message), violations: Vec::new() }
    }

    /// The route's 422: `raise HTTPException(422, errors)`.
    fn invalid(errors: &[ParamError]) -> ApiError {
        let violations: Vec<Violation> = errors.iter().map(Violation::from).collect();
        let message = match violations.first() {
            Some(v) => format!("{}: {}", v.loc.join("."), v.msg),
            None => "Invalid parameters".to_string(),
        };
        ApiError { status: 422, body: ErrorBody::new("validation_error", message), violations }
    }

    fn no_image() -> ApiError {
        ApiError::new(400, "no_image", "Send either `file` or `image_id`")
    }

    fn expired() -> ApiError {
        ApiError::new(404, "image_expired", "Upload expired or unknown; upload it again")
    }

    /// The `detail` of the answer: `{code, message}`, or the 422's list.
    pub fn detail(&self) -> Value {
        serde_json::to_value(self).expect("an ApiError is JSON")
    }

    /// The body of the answer, `{"detail": …}`.
    pub fn response_body(&self) -> Value {
        json!({ "detail": self.detail() })
    }
}

impl Serialize for ApiError {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        if self.violations.is_empty() {
            self.body.serialize(s)
        } else {
            self.violations.serialize(s)
        }
    }
}

impl std::fmt::Display for ApiError {
    /// `404 image_expired: Upload expired or unknown; upload it again`.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} {}: {}", self.status, self.body.code, self.body.message)
    }
}

impl std::error::Error for ApiError {}

impl From<IntakeError> for ApiError {
    /// `routes._intake`: whatever the code, a 400.
    fn from(e: IntakeError) -> ApiError {
        ApiError::new(400, e.code, &e.message)
    }
}

impl From<AutoError> for ApiError {
    /// `auto_unavailable` is the route's 400. An image whose pixels are not its size is not a
    /// request's fault: the server's boundary would have answered 500.
    fn from(e: AutoError) -> ApiError {
        ApiError::new(if e.code == "auto_unavailable" { 400 } else { 500 }, e.code, &e.message)
    }
}

// ---------------------------------------------------------------- the store

struct Entry {
    image: Arc<Image>,
    size: usize,
    /// The tick of the last use: the least is the one to go.
    used: u64,
}

/// `imaging/cache.py` without the clock: an LRU of decoded images under a cap in bytes.
struct Store {
    items: HashMap<String, Entry>,
    bytes: usize,
    max_bytes: usize,
    clock: u64,
}

impl Store {
    fn new(max_bytes: usize) -> Store {
        Store { items: HashMap::new(), bytes: 0, max_bytes, clock: 0 }
    }

    /// The image, which is then the most recently used.
    fn get(&mut self, id: &str) -> Option<Arc<Image>> {
        self.clock += 1;
        let entry = self.items.get_mut(id)?;
        entry.used = self.clock;
        Some(entry.image.clone())
    }

    /// Keep `image` under `id`, evicting the least recently used until it fits (and all of them
    /// if it does not: it is kept alone). The same id again is a use of what is there.
    fn put(&mut self, id: String, image: Arc<Image>) {
        if self.get(&id).is_some() {
            return;
        }
        let size = image.rgba.len();
        while !self.items.is_empty() && self.bytes.saturating_add(size) > self.max_bytes {
            let oldest = self.items.iter().min_by_key(|(_, e)| e.used).map(|(id, _)| id.clone()).expect("the store is not empty");
            if let Some(gone) = self.items.remove(&oldest) {
                self.bytes -= gone.size;
            }
        }
        self.clock += 1;
        self.bytes += size;
        self.items.insert(id, Entry { image, size, used: self.clock });
    }
}

/// The id of an upload: the first 128 bits of the SHA-256 of its bytes, as 32 lowercase hex digits.
fn image_id(bytes: &[u8]) -> String {
    Sha256::digest(bytes)[..16].iter().map(|b| format!("{b:02x}")).collect()
}

// ---------------------------------------------------------------- the response

/// `EngineResult`: the fields are always there, `null` when they do not apply.
#[derive(Serialize)]
struct EngineResult {
    svg: Option<String>,
    elapsed_ms: Option<f64>,
    stats: Option<Stats>,
    error: Option<ErrorBody>,
}

/// `{"vexel": …}`: the dictionaries the response keys by engine.
#[derive(Serialize)]
struct ByEngine<T> {
    vexel: T,
}

/// `VectorizeResponse`.
#[derive(Serialize)]
struct Vectorized<'a> {
    success: bool,
    image_id: &'a str,
    width: u32,
    height: u32,
    results: ByEngine<EngineResult>,
    parameters_used: ByEngine<Map<String, Value>>,
    auto: Option<ByEngine<&'a AutoOutcome>>,
}

/// Python's truthiness of a JSON value: what `x or {}` replaces.
fn falsy(v: &Value) -> bool {
    match v {
        Value::Null => true,
        Value::Bool(b) => !b,
        Value::Number(n) => n.as_f64() == Some(0.0),
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        Value::Object(o) => o.is_empty(),
    }
}

// ---------------------------------------------------------------- the core

/// The core as a server would run it: the upload store and the five calls. See the module
/// documentation. Hold it in an `Arc` and share it between threads.
pub struct Core {
    limits: Limits,
    tracer: Box<dyn Fn(&Image, &VexelParams) -> String + Send + Sync>,
    store: Mutex<Store>,
}

impl Default for Core {
    fn default() -> Core {
        Core::new()
    }
}

impl Core {
    /// A core with the server's settings: 20 MiB, 2048 px a side and 40 megapixels an upload
    /// ([`Limits::default`]), [`MAX_UPLOAD_CACHE_BYTES`] of them kept.
    pub fn new() -> Core {
        Core::with_limits(Limits::default(), MAX_UPLOAD_CACHE_BYTES)
    }

    /// A core with the limits of an upload and the cap of the store given.
    ///
    /// **Mind `max_pixels` when `max_side` is `None`.** Auto scores a candidate by rendering its SVG at 2x the source's
    /// size (for the holes and for the id map), and [`crate::render::MAX_PIXELS`] (2^28) refuses
    /// a render of more: so a source of more than 2^26 pixels (about 67.1 MP) cannot be scored,
    /// and Auto does not fail on it, it degrades: every candidate keeps its SVG without scores
    /// and the pick is `"scoring was unavailable, so the first preset that traced"`. A plain
    /// trace (`auto` false) is not scored and is not affected. The default (2048 x 2048 at most,
    /// 4.2 MP) is well under it.
    /// This is not clamped or asserted, because a shell may want the larger cap for plain
    /// traces; it is documented here and in the crate's README. The engine's cost is the other
    /// limit, and a much lower one (the README's "What a shell must do").
    pub fn with_limits(limits: Limits, max_cache_bytes: usize) -> Core {
        Core::with_tracer(limits, max_cache_bytes, auto::trace_vexel)
    }

    /// A core that traces with `tracer` where it would trace with Vexel: how the failure paths
    /// are tested (a trace that panics), as `auto::run_with` is for Auto. It is given the image
    /// and the validated parameters, and returns the engine's SVG before the viewBox is set.
    #[doc(hidden)]
    pub fn with_tracer(limits: Limits, max_cache_bytes: usize, tracer: impl Fn(&Image, &VexelParams) -> String + Send + Sync + 'static) -> Core {
        Core { limits, tracer: Box::new(tracer), store: Mutex::new(Store::new(max_cache_bytes)) }
    }

    fn store(&self) -> MutexGuard<'_, Store> {
        self.store.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// How many uploads are kept.
    #[doc(hidden)]
    pub fn cached_images(&self) -> usize {
        self.store().items.len()
    }

    /// How many bytes of pixels they come to (the number the cap is held against).
    #[doc(hidden)]
    pub fn cached_bytes(&self) -> usize {
        self.store().bytes
    }

    /// `GET /health`: `{status, version, engines, vexel}`. `vexel` names the implementation that
    /// is serving, and it is always the Rust one.
    pub fn health(&self) -> Value {
        json!({"status": "ok", "version": VERSION, "engines": [ENGINE], "vexel": "rust"})
    }

    /// `GET /engines`: the engines, each with the schema and the defaults the UI builds its controls from.
    pub fn engines(&self) -> Value {
        json!([{
            "id": ENGINE,
            "label": LABEL,
            "description": DESCRIPTION,
            "primary": true,
            "params": params::schema(),
            "defaults": params::defaults(),
        }])
    }

    /// `GET /presets`: Auto first, then the named bundles.
    pub fn presets(&self) -> Value {
        let all: Vec<Preset> = presets::all();
        serde_json::to_value(all).expect("presets are JSON")
    }

    /// `POST /uploads`: decode and keep the image, and answer `{image_id, width, height, format}`.
    /// The same file again is the same entry.
    pub fn upload(&self, bytes: &[u8]) -> Result<Value, ApiError> {
        // an upload over the size limit is refused first, as the intake does, without hashing it
        let early = (bytes.len() <= self.limits.max_bytes).then(|| image_id(bytes));
        if let Some(id) = &early {
            if let Some(image) = self.store().get(id) {
                return Ok(uploaded(id, &image));
            }
        }
        let image = Arc::new(intake::load(bytes, self.limits)?);
        let id = early.unwrap_or_else(|| image_id(bytes));
        let answer = uploaded(&id, &image);
        self.store().put(id, image);
        Ok(answer)
    }

    /// `POST /vectorize` for the engine `vexel`, with the upload `image_id` and the engine's
    /// `parameters` (see the module documentation). With `auto`, every candidate preset is
    /// traced and scored and the pick is also `results.vexel`; `parameters` is then validated
    /// and otherwise unused, and `parameters_used` is the pick's.
    ///
    /// `parameters` are validated in Pydantic's **strict** mode: a number given as a string
    /// (`"6"`), `true` where a number is wanted, and `"yes"` or `1` where a boolean is are a 422
    /// here and are converted by the Python's lax mode (the frontend sends none of them). The
    /// route's other half belongs to the shell: it parses the request, takes the `vexel` key of
    /// the form's `parameters` JSON (malformed JSON, or JSON that is not an object, is a 400
    /// `bad_parameters`), reads the `auto` flag, answers an `engines` list that names anything but
    /// `vexel` with a 400 `unknown_engine`, and, for a request that carries a `file` rather than
    /// an `image_id`, calls [`Core::upload`] and then this.
    pub fn vectorize(&self, image_id: &str, parameters: &Value, auto: bool) -> Result<Value, ApiError> {
        // the route's order: the parameters, then the image
        let empty = Value::Object(Map::new());
        let asked = params::check(if falsy(parameters) { &empty } else { parameters }).map_err(|errors| ApiError::invalid(&errors))?;
        if image_id.is_empty() {
            return Err(ApiError::no_image());
        }
        // the lock is let go before anything is traced
        let image = self.store().get(image_id).ok_or_else(ApiError::expired)?;

        let mut used = params::dump(&asked);
        let (result, outcome) = if auto {
            let trace = |_: &Preset, img: &Image, p: &VexelParams| (self.tracer)(img, p);
            let outcome = auto::run_with(&image, &presets::auto_candidates(), &trace)?;
            let result = match outcome.chosen() {
                Some(c) => {
                    used = c.parameters.clone().unwrap_or_default();
                    EngineResult { svg: c.svg.clone(), elapsed_ms: c.elapsed_ms, stats: c.stats, error: None }
                }
                None => EngineResult { svg: None, elapsed_ms: None, stats: None, error: outcome.result_error() },
            };
            (result, Some(outcome))
        } else {
            let result = match auto::trace_finished(&image, &asked, |img, p| (self.tracer)(img, p)) {
                Ok((svg, stats, ms)) => EngineResult { svg: Some(svg), elapsed_ms: Some(ms), stats: Some(stats), error: None },
                Err(error) => EngineResult { svg: None, elapsed_ms: None, stats: None, error: Some(error) },
            };
            (result, None)
        };
        let response = Vectorized {
            success: true,
            image_id,
            width: image.width,
            height: image.height,
            results: ByEngine { vexel: result },
            parameters_used: ByEngine { vexel: used },
            auto: outcome.as_ref().map(|o| ByEngine { vexel: o }),
        };
        Ok(serde_json::to_value(response).expect("a response is JSON"))
    }
}

/// `UploadResponse`.
fn uploaded(id: &str, image: &Image) -> Value {
    json!({"image_id": id, "width": image.width, "height": image.height, "format": image.format})
}

#[cfg(test)]
mod tests {
    use super::*;

    fn png() -> Vec<u8> {
        let mut out = std::io::Cursor::new(Vec::new());
        image::DynamicImage::ImageRgba8(image::RgbaImage::from_pixel(8, 8, image::Rgba([10, 200, 30, 255])))
            .write_to(&mut out, image::ImageFormat::Png)
            .unwrap();
        out.into_inner()
    }

    #[test]
    fn a_poisoned_store_is_taken_over_not_trusted_to_be_broken() {
        let core = Arc::new(Core::new());
        let id = core.upload(&png()).unwrap()["image_id"].as_str().unwrap().to_string();
        let held = core.clone();
        // a thread dies holding the lock, as nothing in this module does
        let died = std::thread::spawn(move || {
            let _guard = held.store.lock().unwrap();
            panic!("a thread that held the store");
        })
        .join();
        assert!(died.is_err() && core.store.is_poisoned());
        assert_eq!(core.cached_images(), 1);
        assert!(core.vectorize(&id, &json!({}), false).is_ok(), "the upload made before is still there");
        assert!(core.upload(&png()).is_ok());
    }

    #[test]
    fn the_least_recently_used_goes_first_and_the_bytes_add_up() {
        let image = |n: usize| Arc::new(Image { rgba: vec![0; n], width: 1, height: n as u32 / 4, format: "PNG".into() });
        let mut s = Store::new(100);
        for (id, size) in [("a", 40), ("b", 40), ("c", 20)] {
            s.put(id.into(), image(size));
        }
        assert_eq!(s.bytes, 100);
        // the same id again is a use of what is there, counted once (two threads that uploaded one file at once)
        s.put("c".into(), image(20));
        assert_eq!((s.items.len(), s.bytes), (3, 100));
        assert!(s.get("a").is_some()); // a is now newer than b
        s.put("d".into(), image(40)); // 100 + 40 > 100: b goes, then 60 + 40 fits
        assert_eq!((s.get("b").is_none(), s.get("a").is_some(), s.get("c").is_some(), s.get("d").is_some(), s.bytes), (true, true, true, true, 100));
        s.put("e".into(), image(400)); // over the whole cap: the rest go, and it stays
        assert_eq!((s.items.len(), s.bytes), (1, 400));
    }
}
