//! Auto: trace an image with every candidate preset, score each against the source, and keep
//! the cleanest of those as faithful as the best. Ported from `studi0trace/auto.py` (the rule,
//! [`issues`], [`summary`]) and from `api/routes._run_auto` (what [`run`] does with it), which
//! `tests/auto.rs` holds this to: 300 random sets of candidates the Python chose among, and
//! the whole of `auto=true` on seven images, SVG to the byte.
//!
//! The rule, in a line: among the candidates whose mean ΔE is within
//! `max(DE_SLACK, DE_SHARE × best)` of the most faithful one's and whose edge F1 is within
//! [`EDGE_SLACK`] of the best of those, take the one with the lowest artifact index; a tie goes
//! to fewer shapes, then to the earlier preset. [`choose`] says why in words that finish
//! "Auto chose *preset* — …".
//!
//! # Python's `round`, and why the artifact index is compared as it is
//!
//! The artifact index is compared after `round(x, 1)`, so a tie at one decimal is a tie. Python's
//! `round(x, n)` is the exact binary value rounded correctly to `n` decimals, ties to even:
//! `round(0.35, 1)` is `0.3` (the double nearest 0.35 is 0.34999999999999997), `round(0.45, 1)` is
//! `0.5`, `round(0.25, 1)` is `0.2`. [`py_round`] is that, and is what the rule, [`summary`] and
//! the tests use; `f64::round` (half away from zero on `x * 10`) differs on all three.
//!
//! # What the Python does that this does literally
//!
//! [`choose`] takes the first of equal minima (Python's `min`), compares keys as Python compares
//! tuples (so a NaN artifact index is never less than anything and nothing is less than it:
//! the first candidate stays), reads `order` as a dict (a later duplicate id overwrites, and the
//! tie-break uses the last index of an id), and tests `pick is cleanest` by identity, which
//! here is the same index. None of this is reachable from a render; it is the rule as written.
//! Where the Python would raise (every ΔE NaN, so no candidate is faithful) the port answers
//! "no candidate could be scored" instead of panicking.
use std::collections::HashMap;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::OnceLock;
use std::time::Instant;

use rayon::prelude::*;
use serde::{Serialize, Serializer};
use serde_json::{Map, Value};
use vexel_rs::engine::VexelParams;

use crate::intake::Image;
use crate::params;
use crate::presets::{self, Preset};
use crate::scorecard::{self, Reference};
use crate::svg::{self, Stats};

/// ΔE a candidate may give away to the most faithful one…
pub const DE_SLACK: f64 = 0.15;
/// …or this share of the best ΔE, whichever is larger.
pub const DE_SHARE: f64 = 0.30;
/// Edge F1 it may give away to the best of those.
pub const EDGE_SLACK: f64 = 0.02;

/// The engine the `engine` field of an [`AutoOutcome`] names.
const ENGINE: &str = "vexel";

// ---------------------------------------------------------------- Python's round

/// Python's `round(x, ndigits)` for a float: the exact value of `x` rounded correctly to
/// `ndigits` decimals (a tie goes to the even digit), read back as the nearest double. NaN and
/// the infinities are themselves, and a negative that rounds to nothing is `-0.0`.
///
/// Rust formats a float exactly (its decimal expansion is not truncated at 17 digits) and rounds
/// that to even, which is what CPython's `dtoa` does; the string is parsed back correctly rounded.
/// `tests/auto.rs` holds it to the Python's over a thousand numbers (decimals as typed, exact
/// ties, the doubles either side of them, both signs, tiny and huge), and it was compared with a
/// million more when it was written.
pub fn py_round(x: f64, ndigits: u32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{:.*}", ndigits as usize, x).parse().unwrap_or(x)
}

// ---------------------------------------------------------------- the rule

/// What [`choose`] needs to know about one candidate.
#[derive(Debug, Clone, PartialEq)]
pub struct Scored {
    pub id: String,
    pub delta_e: f64,
    pub edge_f1: f64,
    pub artifact_index: f64,
    pub elements: u64,
}

/// The ΔE a candidate may have and still be as faithful as one at `best`.
pub fn de_limit(best: f64) -> f64 {
    // Python's `max(DE_SLACK, DE_SHARE * best)`: the share only when it is greater
    let share = DE_SHARE * best;
    best + if share > DE_SLACK { share } else { DE_SLACK }
}

/// Python's `min(floats)`: the first, replaced by each later one that is less (NaN is never
/// less, and nothing is less than NaN).
fn py_min(mut values: impl Iterator<Item = f64>) -> Option<f64> {
    let mut best = values.next()?;
    for v in values {
        if v < best {
            best = v;
        }
    }
    Some(best)
}

/// Python's `max(floats)`.
fn py_max(mut values: impl Iterator<Item = f64>) -> Option<f64> {
    let mut best = values.next()?;
    for v in values {
        if v > best {
            best = v;
        }
    }
    Some(best)
}

/// A tuple key of Python: a float, an integer and a position.
#[derive(Clone, Copy)]
struct Key(f64, u64, usize);

impl Key {
    /// Python's `<` on tuples: at the first place the two differ (by `==`, which NaN fails),
    /// the elements compare.
    fn lt(&self, other: &Key) -> bool {
        if self.0 != other.0 {
            return self.0 < other.0;
        }
        if self.1 != other.1 {
            return self.1 < other.1;
        }
        self.2 < other.2
    }
}

/// Python's `min(items, key=key)`: the first of equal minima.
fn py_min_by(mut items: impl Iterator<Item = usize>, key: impl Fn(usize) -> Key) -> Option<usize> {
    let mut best = items.next()?;
    let mut best_key = key(best);
    for i in items {
        let k = key(i);
        if k.lt(&best_key) {
            best = i;
            best_key = k;
        }
    }
    Some(best)
}

/// The candidates as faithful as the best, as indices into `scored` in the order given.
pub fn faithful(scored: &[Scored]) -> Vec<usize> {
    let Some(best) = py_min(scored.iter().map(|s| s.delta_e)) else {
        return vec![];
    };
    let limit = de_limit(best);
    let ok: Vec<usize> = (0..scored.len()).filter(|&i| scored[i].delta_e <= limit).collect();
    // the Python's `max()` of nothing raises (a ΔE of NaN first leaves no one); here no one is faithful
    let Some(best_edge) = py_max(ok.iter().map(|&i| scored[i].edge_f1)) else {
        return vec![];
    };
    ok.into_iter().filter(|&i| scored[i].edge_f1 >= best_edge - EDGE_SLACK).collect()
}

/// The pick (an index into `scored`) and why, in words that finish "Auto chose *preset* — …".
pub fn choose(scored: &[Scored]) -> (Option<usize>, &'static str) {
    const NONE: &str = "no candidate could be scored";
    if scored.is_empty() {
        return (None, NONE);
    }
    // a dict: a later duplicate id overwrites the earlier index
    let order: HashMap<&str, usize> = scored.iter().enumerate().map(|(i, s)| (s.id.as_str(), i)).collect();
    let position = |i: usize| order.get(scored[i].id.as_str()).copied().unwrap_or(0);
    let key = |i: usize| Key(py_round(scored[i].artifact_index, 1), scored[i].elements, position(i));
    let ok = faithful(scored);
    let Some(pick) = py_min_by(ok.iter().copied(), key) else {
        return (None, NONE); // the Python raises: no candidate is faithful
    };
    let most_faithful = py_min_by(0..scored.len(), |i| Key(scored[i].delta_e, 0, position(i))).expect("scored is not empty");
    let cleanest = py_min_by(0..scored.len(), key).expect("scored is not empty");
    let others = ok.iter().copied().filter(|&i| i != pick);
    if scored.len() == 1 {
        return (Some(pick), "the only candidate that traced");
    }
    if ok.len() == 1 {
        return (Some(pick), "the only one this faithful to the image");
    }
    if pick == cleanest && pick == most_faithful {
        return (Some(pick), "the most faithful, and the cleanest");
    }
    let mine = py_round(scored[pick].artifact_index, 1);
    if others.into_iter().any(|i| py_round(scored[i].artifact_index, 1) == mine) {
        return (Some(pick), "as clean at the same fidelity, with fewer shapes");
    }
    if pick == cleanest {
        return (Some(pick), "the cleanest at the same fidelity");
    }
    if pick == most_faithful {
        return (Some(pick), "the most faithful; the cleaner ones lose detail");
    }
    (Some(pick), "the cleanest of the most faithful")
}

// ---------------------------------------------------------------- the words

/// A count of a card: an integer, or what is missing as none. (The Python raises `KeyError` on a
/// card with a term missing; a card from [`scorecard::assess`] has them all.)
fn count(card: &Map<String, Value>, key: &str) -> i64 {
    match card.get(key) {
        Some(v) => v.as_i64().or_else(|| v.as_f64().map(|f| f as i64)).unwrap_or(0),
        None => 0,
    }
}

fn float(card: &Map<String, Value>, key: &str) -> f64 {
    card.get(key).and_then(Value::as_f64).unwrap_or(f64::NAN)
}

fn plural(n: i64) -> &'static str {
    if n != 1 {
        "s"
    } else {
        ""
    }
}

/// What is visibly wrong with a trace, in a designer's words, worst first.
pub fn issues(card: &Map<String, Value>) -> Vec<String> {
    let mut out = Vec::new();
    let pinholes = count(card, "pinholes");
    if pinholes != 0 {
        out.push(format!("{pinholes} pinhole{}", plural(pinholes)));
    }
    let thin = count(card, "slivers") + count(card, "degenerate") + count(card, "thin_strokes");
    if thin != 0 {
        out.push(format!("{thin} sliver{}", plural(thin)));
    }
    if float(card, "wobble_deg_100px") >= 25.0 {
        out.push("wobbly edges".into());
    }
    // `card.get('rect_skewed', 0)`: older results do not have it
    let uneven = count(card, "radius_inconsistent") + count(card, "rect_bowed") + count(card, "rect_skewed");
    if uneven != 0 {
        out.push(format!("{uneven} uneven rectangle{}", plural(uneven)));
    }
    if count(card, "inflections") >= 3 {
        out.push("wavy curves".into());
    }
    out
}

/// The per-candidate scores the API returns (`CandidateScores`): the same keys in the same
/// order, counts as integers, the rest as floats rounded as Python rounds them.
pub fn summary(scores: &Map<String, Value>) -> Value {
    let mut m = Map::new();
    m.insert("delta_e".into(), py_round(float(scores, "delta_e_mean"), 4).into());
    m.insert("edge_f1".into(), py_round(float(scores, "edge_f1"), 4).into());
    m.insert("artifact_index".into(), py_round(float(scores, "artifact_index"), 2).into());
    m.insert("clean".into(), scorecard::is_clean(scores).into());
    m.insert("issues".into(), issues(scores).into());
    m.insert("shapes".into(), count(scores, "elements").into());
    m.insert("pinholes".into(), count(scores, "pinholes").into());
    m.insert("slivers".into(), (count(scores, "slivers") + count(scores, "degenerate") + count(scores, "thin_strokes")).into());
    m.insert("wobble".into(), py_round(float(scores, "wobble_deg_100px"), 1).into());
    m.insert("inflections".into(), count(scores, "inflections").into());
    m.insert(
        "uneven_rects".into(),
        (count(scores, "radius_inconsistent") + count(scores, "rect_bowed") + count(scores, "rect_skewed")).into(),
    );
    Value::Object(m)
}

// ---------------------------------------------------------------- the answer

/// `ErrorBody` of the API: a stable `code` and a message.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ErrorBody {
    pub code: String,
    pub message: String,
}

impl ErrorBody {
    pub fn new(code: &str, message: impl Into<String>) -> ErrorBody {
        ErrorBody { code: code.into(), message: message.into() }
    }

    /// What the route reports of an exception that is not an `EngineError`: a candidate that
    /// crashed (here: panicked) or whose parameters would not validate.
    pub(crate) fn crashed(message: impl Into<String>) -> ErrorBody {
        ErrorBody::new("engine_crashed", message)
    }
}

impl std::fmt::Display for ErrorBody {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

/// One preset Auto traced with (`AutoCandidate`): its trace, what it was traced with and its
/// scores. A field that is `None` is `null` in the JSON, as Pydantic writes it.
#[derive(Debug, Clone, Serialize)]
pub struct Candidate {
    pub preset: String,
    pub label: String,
    pub svg: Option<String>,
    /// Wall time of the trace, the viewBox and the stats (the scoring is not in it), unrounded,
    /// as `Engine.trace` stamps it.
    pub elapsed_ms: Option<f64>,
    pub stats: Option<Stats>,
    /// Every parameter the candidate was traced with (`model_dump()`): the preset's over the defaults.
    pub parameters: Option<Map<String, Value>>,
    /// [`summary`] of the card.
    pub scores: Option<Value>,
    pub error: Option<ErrorBody>,
    /// The full card [`scorecard::assess`] gave, which `scores` is summarised from. Not part of
    /// the API's shape.
    #[serde(skip)]
    pub card: Option<Map<String, Value>>,
}

impl Candidate {
    fn new(preset: &Preset) -> Candidate {
        Candidate {
            preset: preset.id.clone(),
            label: preset.label.clone(),
            svg: None,
            elapsed_ms: None,
            stats: None,
            parameters: None,
            scores: None,
            error: None,
            card: None,
        }
    }
}

/// What Auto tried and what it chose (`AutoResult`; it serialises as one, with `engine`).
#[derive(Debug, Clone)]
pub struct AutoOutcome {
    /// The chosen preset's id, or `None` when no candidate traced.
    pub pick: Option<String>,
    /// Why, in words that finish "Auto chose *label* — …".
    pub reason: String,
    /// Every candidate, in the order of preference, whichever finished first.
    pub candidates: Vec<Candidate>,
}

impl Serialize for AutoOutcome {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        #[derive(Serialize)]
        struct AutoResult<'a> {
            engine: &'static str,
            pick: &'a Option<String>,
            reason: &'a str,
            candidates: &'a [Candidate],
        }
        AutoResult { engine: ENGINE, pick: &self.pick, reason: &self.reason, candidates: &self.candidates }.serialize(s)
    }
}

impl AutoOutcome {
    /// The chosen candidate: also the engine's ordinary result (`results[engine]`).
    pub fn chosen(&self) -> Option<&Candidate> {
        let pick = self.pick.as_deref()?;
        self.candidates.iter().find(|c| c.preset == pick)
    }

    /// The engine's error when nothing was chosen (`results[engine].error`): the first candidate's,
    /// in the order of preference, or the route's own words when none says why.
    pub fn result_error(&self) -> Option<ErrorBody> {
        if self.chosen().is_some() {
            return None;
        }
        Some(self.candidates.iter().find_map(|c| c.error.clone()).unwrap_or_else(|| ErrorBody::new("engine_failed", "no Auto candidate traced")))
    }
}

/// Auto could not be run at all.
#[derive(Debug, Clone, PartialEq)]
pub struct AutoError {
    pub code: &'static str,
    pub message: String,
}

impl std::fmt::Display for AutoError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for AutoError {}

// ---------------------------------------------------------------- run

/// What traces an image with one preset's parameters. The real one is Vexel's; a test replaces it
/// to make a candidate panic or answer with something that does not render.
#[doc(hidden)]
pub type Tracer<'a> = dyn Fn(&Preset, &Image, &VexelParams) -> String + Sync + 'a;

/// What scores a candidate's SVG against the source's [`Reference`]. The real one is
/// [`scorecard::assess`] at its default hole scale; a test replaces it to make the scoring of
/// one candidate panic, which the renderer can do on an input the engine never writes.
#[doc(hidden)]
pub type Scorer<'a> = dyn Fn(&str, &Reference) -> Result<Map<String, Value>, scorecard::ScoreError> + Sync + 'a;

fn assess_default(svg: &str, reference: &Reference) -> Result<Map<String, Value>, scorecard::ScoreError> {
    scorecard::assess(svg, reference, None)
}

/// Vexel's pipeline on an image: the SVG as the engine writes it, before the viewBox is set.
pub(crate) fn trace_vexel(img: &Image, p: &VexelParams) -> String {
    vexel_rs::engine::trace_rgba(&img.rgba, img.height as usize, img.width as usize, p)
}

fn vexel(_: &Preset, img: &Image, p: &VexelParams) -> String {
    trace_vexel(img, p)
}

/// A panic's message.
fn message_of(payload: Box<dyn std::any::Any + Send>) -> String {
    match payload.downcast::<String>() {
        Ok(s) => *s,
        Err(payload) => payload.downcast_ref::<&str>().map_or_else(|| "a panic with no message".to_string(), |s| s.to_string()),
    }
}

/// `f()`, or the message of the panic it ended in: a candidate that crashes is its own error,
/// never the request's.
fn guard<T>(f: impl FnOnce() -> T) -> Result<T, String> {
    catch_unwind(AssertUnwindSafe(f)).map_err(message_of)
}

/// `Engine.trace` as the route calls it: trace, give the SVG the image's viewBox and count it
/// (`engines.base.finish`; timed together, in milliseconds, unrounded). A trace that panics is the
/// engine's `engine_crashed`, never the caller's: the route's `_failure` for an exception that is
/// not an `EngineError`, which Vexel never raises. [`run`] traces each candidate with it, and so
/// does `api::Core` a plain trace.
///
/// **The one clock in this crate** (the engine has its own, below). On
/// `wasm32-unknown-unknown` `Instant::now()` panics, and with `panic=abort` (that target's
/// default) the panic is a trap that ends the module: `catch_unwind` cannot catch it where
/// panics abort, so it is not `engine_crashed`. A web build has to hand this function a clock
/// (plan 3) before it traces, **and** the engine itself reads `Instant::now()` on every trace
/// (`vexel_rs::timing::Timer::new`, called first thing in `engine::trace_rgba` whether or not
/// `VEXEL_TIMING` is set), so plan 3 must also make that `Timer` lazy or give it a wasm clock;
/// nothing else in this crate reads the time.
pub(crate) fn trace_finished(
    img: &Image,
    params: &VexelParams,
    trace: impl FnOnce(&Image, &VexelParams) -> String,
) -> Result<(String, Stats, f64), ErrorBody> {
    guard(|| {
        let started = Instant::now();
        let raw = trace(img, params);
        let svg = svg::normalize_dimensions(&raw, img.width, img.height);
        let stats = svg::stats(&svg);
        (svg, stats, started.elapsed().as_secs_f64() * 1000.0)
    })
    .map_err(|m| ErrorBody::crashed(format!("panic: {m}")))
}

/// Trace, finish (`Engine.trace`: the trace, the viewBox, the stats; timed together) and, if it
/// traced, leave the scoring for later. `routes._run_auto`'s `run`, up to `ref_ready.wait()`.
fn trace_one(img: &Image, preset: &Preset, trace: &Tracer<'_>) -> Candidate {
    let mut c = Candidate::new(preset);
    let params = match params::parse(&Value::Object(preset.params.clone())) {
        Ok(p) => p,
        Err(e) => {
            c.error = Some(ErrorBody::crashed(format!("ValidationError: {}: {}", e.field, e.message)));
            return c;
        }
    };
    c.parameters = Some(params::dump(&params));
    match trace_finished(img, &params, |i, p| trace(preset, i, p)) {
        Ok((svg, stats, ms)) => (c.svg, c.stats, c.elapsed_ms) = (Some(svg), Some(stats), Some(ms)),
        Err(e) => c.error = Some(e),
    }
    c
}

/// Score a candidate that traced, if there is a reference to score it against. An SVG that will
/// not render is still a trace, so it stays, unscored.
fn score_one(mut c: Candidate, reference: Option<&Reference>, score: &Scorer<'_>) -> (Candidate, Option<Scored>) {
    let card = match (c.svg.as_deref(), reference) {
        (Some(svg), Some(r)) => match guard(|| score(svg, r)) {
            Ok(Ok(card)) => card,
            _ => return (c, None),
        },
        _ => return (c, None),
    };
    let scored = Scored {
        id: c.preset.clone(),
        delta_e: float(&card, "delta_e_mean"),
        edge_f1: float(&card, "edge_f1"),
        artifact_index: float(&card, "artifact_index"),
        elements: count(&card, "elements") as u64,
    };
    c.scores = Some(summary(&card));
    c.card = Some(card);
    (c, Some(scored))
}

/// Choose among the candidates that scored, and fall back as the route does.
fn decide(done: Vec<(Candidate, Option<Scored>)>) -> AutoOutcome {
    let (candidates, scored): (Vec<Candidate>, Vec<Option<Scored>>) = done.into_iter().unzip();
    let scored: Vec<Scored> = scored.into_iter().flatten().collect();
    let (pick, why) = choose(&scored);
    let mut reason = why.to_string();
    let mut chosen = pick.and_then(|i| candidates.iter().position(|c| c.preset == scored[i].id));
    if chosen.is_none() {
        // nothing could be scored: the first candidate that traced, in the order of preference
        chosen = candidates.iter().position(|c| c.svg.is_some());
        reason = if chosen.is_some() { "scoring was unavailable, so the first preset that traced" } else { "every candidate failed" }.to_string();
    }
    AutoOutcome { pick: chosen.map(|i| candidates[i].preset.clone()), reason, candidates }
}

/// The threads Auto's work runs on: a pool of rayon's with 8 MiB stacks (its global pool's
/// workers have 2 MiB), built on first use. Should the system refuse the threads, rayon's own.
fn pool() -> Option<&'static rayon::ThreadPool> {
    static POOL: OnceLock<Option<rayon::ThreadPool>> = OnceLock::new();
    POOL.get_or_init(|| rayon::ThreadPoolBuilder::new().stack_size(8 << 20).thread_name(|i| format!("studi0trace-auto-{i}")).build().ok()).as_ref()
}

/// Run `f` on [`pool`]. A caller that is already a worker of another pool waits for it here.
fn on_pool<R: Send>(f: impl FnOnce() -> R + Send) -> R {
    match pool() {
        Some(p) => p.install(f),
        None => f(),
    }
}

/// [`run`] with the candidates and the tracer given: how the failure paths are tested. Errors
/// only without candidates (the route's `auto_unavailable`). The image is taken as it is: one
/// whose pixels do not make a reference loses the scoring of every candidate, not their traces.
#[doc(hidden)]
pub fn run_with(img: &Image, candidates: &[Preset], trace: &Tracer<'_>) -> Result<AutoOutcome, AutoError> {
    run_with_scorer(img, candidates, trace, &assess_default)
}

/// `run_with` with the scoring given as well: how a panic in the renderer, which the engine's own
/// SVGs cannot cause, is tested.
#[doc(hidden)]
pub fn run_with_scorer(img: &Image, candidates: &[Preset], trace: &Tracer<'_>, score: &Scorer<'_>) -> Result<AutoOutcome, AutoError> {
    if candidates.is_empty() {
        return Err(AutoError { code: "auto_unavailable", message: "Auto has no candidates for the selected engines".into() });
    }
    let done = on_pool(|| {
        // the route builds the reference in a thread beside the traces
        let (reference, traced) = rayon::join(
            || guard(|| Reference::new(&img.rgba, img.height as usize, img.width as usize)),
            || candidates.par_iter().map(|p| trace_one(img, p, trace)).collect::<Vec<Candidate>>(),
        );
        // no reference (a source with no pixels, or a panic making it): no scores, still traces
        let reference = reference.ok().and_then(Result::ok);
        // `into_par_iter().collect()` keeps the order of the list, however the threads finish
        traced.into_par_iter().map(|c| score_one(c, reference.as_ref(), score)).collect::<Vec<_>>()
    });
    Ok(decide(done))
}

/// Auto: trace `img` with every candidate preset (`presets::auto_candidates()`, concurrently),
/// score each against the source, and choose. A candidate that fails is reported (its
/// [`Candidate::error`]) and left out of the choice; it never fails the run.
///
/// Errors only for an image whose pixels are not `width * height * 4` bytes of at least one pixel.
///
/// # Where it runs
///
/// On a pool of its own (threads named `studi0trace-auto-N`, 8 MiB stacks), not rayon's global
/// one: [`scorecard::assess`] renders with resvg, whose parser recurses with the depth of the SVG,
/// and a deep one aborts the process on the 2 MiB stack of a global worker (see [`scorecard`]).
/// The SVGs scored here are the engine's own, which nest a few levels, so no input can reach that
/// depth through `run`; the room is for the day a caller of `run_with` scores something else.
/// The pool costs nothing measurable: the 512 px wordmark takes 1.17 s on average on it and 1.20 s
/// on the global pool (release, 7 runs each, twice). The engine parallelises inside a trace, too;
/// a worker that calls `par_iter` uses the pool it belongs to, so the candidates and the engine's
/// own tasks share one thread per core and the cores are not oversubscribed.
///
/// A panic is caught per candidate with `catch_unwind`, which a build with `panic = "abort"`
/// turns into an abort of the process.
pub fn run(img: &Image) -> Result<AutoOutcome, AutoError> {
    let want = (img.width as usize).checked_mul(img.height as usize).and_then(|p| p.checked_mul(4));
    if want.is_none() || want == Some(0) || want != Some(img.rgba.len()) {
        return Err(AutoError {
            code: "invalid_image",
            message: format!("{} bytes of RGBA are not a {}x{} image", img.rgba.len(), img.width, img.height),
        });
    }
    run_with(img, &presets::auto_candidates(), &vexel)
}
