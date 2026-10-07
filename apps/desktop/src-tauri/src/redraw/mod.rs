//! AI redraw: an opt-in redraw of a rough image by OpenAI's image model, made with the user's own API key and
//! checked against the original before anything is traced from it (spec
//! `docs/superpowers/specs/2026-10-06-ai-redraw-design.md`). With `keychain.rs`, the only code in the app that
//! touches the network or the key. One responsibility per file: `geometry` (padding, sizes, the crop back),
//! `rough` (the inspector's hint), `drift` (how far a redraw moved the image), `openai` (the request and its
//! reply); this file holds the error words, the pipeline and the jobs in flight.
use crate::error::CommandError;
use image::ExtendedColorType;
use serde::Serialize;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use studi0trace_core::api::ApiError;
use studi0trace_core::color::rgb_on_white;
use studi0trace_core::intake::{self, Limits};
use studi0trace_core::resample::{resize_rgba, Filter};

pub mod drift;
pub mod geometry;
pub mod openai;
pub mod rough;

/// An image whose longest side is under this many pixels looks rough.
pub const ROUGH_SIDE: u32 = 600;
/// The long side of the size `gpt-image-2` is asked for.
pub const REQUEST_SIDE: u32 = 2048;

/// The image model asked (Settings ▸ AI redraw ▸ Model).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Model {
    GptImage2,
    GptImage15,
}

impl Model {
    /// The setting's value; anything else is the default, `gpt-image-2`.
    pub fn parse(id: &str) -> Model {
        match id {
            "gpt-image-1.5" => Model::GptImage15,
            _ => Model::GptImage2,
        }
    }

    pub fn id(self) -> &'static str {
        match self {
            Model::GptImage2 => "gpt-image-2",
            Model::GptImage15 => "gpt-image-1.5",
        }
    }
}

/// Settings ▸ AI redraw ▸ Quality.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Quality {
    Medium,
    High,
}

impl Quality {
    /// The setting's value; anything else is the default, `medium`.
    pub fn parse(id: &str) -> Quality {
        match id {
            "high" => Quality::High,
            _ => Quality::Medium,
        }
    }

    pub fn id(self) -> &'static str {
        match self {
            Quality::Medium => "medium",
            Quality::High => "high",
        }
    }
}

/// A redraw's failure, in words for a person: never the key, and never OpenAI's own message (a 401's quotes part
/// of it). Every one leaves the image exactly as it was.
pub fn error(code: &'static str) -> CommandError {
    let (status, message) = match code {
        "no_key" => (401, "Add your OpenAI API key to use AI redraw."),
        "invalid_key" => (401, "OpenAI did not accept your API key. Replace it in Settings ▸ AI redraw."),
        "quota" => (429, "Your OpenAI account is out of credit or rate limited."),
        "not_allowed" => (403, "OpenAI did not allow this key to create images. Your organization may need to be verified for image models at platform.openai.com."),
        "refused" => (422, "OpenAI declined to redraw this image under its content policy."),
        "timeout" => (504, "OpenAI did not answer within 2 minutes. Try again."),
        "offline" => (503, "Studi0Trace could not reach OpenAI. Check your internet connection."),
        "bad_reply" => (502, "OpenAI's reply held no usable image. Try again."),
        "too_large" => (413, "This image is too large to send to OpenAI (50 MB at most)."),
        "cancelled" => (499, "The redraw was cancelled"),
        _ => (500, "The redraw failed."),
    };
    CommandError::new(status, code, message)
}

/// Where a redraw is (the `redraw-phase` event's `phase`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    Uploading,
    Drawing,
    Checking,
    Done,
    Failed,
}

/// What the settings ask of OpenAI.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Options {
    pub model: Model,
    pub quality: Quality,
}

/// A decoded image, RGBA.
#[derive(Debug, Clone)]
pub struct Source {
    pub rgba: Vec<u8>,
    pub width: u32,
    pub height: u32,
}

impl Source {
    /// An open image's bytes, decoded by the core's intake (its own refusal if they will not decode).
    pub fn decode(bytes: &[u8]) -> Result<Source, CommandError> {
        let image = intake::load(bytes, Limits::default()).map_err(|e| CommandError::from(ApiError::from(e)))?;
        Ok(Source { rgba: image.rgba, width: image.width, height: image.height })
    }
}

/// A reply may be larger than an upload may be (up to 3840 px a side).
const REPLY_LIMITS: Limits = Limits { max_bytes: 64 * 1024 * 1024, max_pixels: 40_000_000, max_side: None };

/// A finished redraw: an opaque PNG in the original's framing, and its drift from the original.
#[derive(Debug, Clone)]
pub struct Redrawn {
    pub png: Vec<u8>,
    pub width: u32,
    pub height: u32,
    pub drift: drift::Drift,
}

fn encode_png(pixels: &[u8], w: u32, h: u32, colour: ExtendedColorType) -> Result<Vec<u8>, CommandError> {
    use image::ImageEncoder;
    let mut out = Vec::new();
    image::codecs::png::PngEncoder::new(&mut out).write_image(pixels, w, h, colour).map_err(|e| CommandError::crashed(format!("the PNG could not be written: {e}")))?;
    Ok(out)
}

/// What is sent: the source on white (a transparent one loses its alpha, which is not restored), padded with its
/// border colour to the plan's canvas, as PNG.
pub fn prepare(source: &Source, model: Model) -> Result<(geometry::Plan, Vec<u8>), CommandError> {
    let rgb = rgb_on_white(&source.rgba);
    let plan = geometry::plan(model, source.width, source.height);
    let colour = geometry::border_colour(&rgb, source.width, source.height);
    let padded = geometry::pad_rgb(&rgb, source.width, source.height, plan.pad, colour);
    let png = encode_png(&padded, plan.pad.width, plan.pad.height, ExtendedColorType::Rgb8)?;
    if png.len() > openai::MAX_REQUEST_BYTES {
        return Err(error("too_large"));
    }
    Ok((plan, png))
}

/// The reply in the original's framing: the original's rectangle found in it, resized once (Lanczos) to the final
/// size, its drift measured, encoded as an opaque PNG.
pub fn finish(source: &Source, plan: geometry::Plan, reply_png: &[u8]) -> Result<Redrawn, CommandError> {
    let reply = intake::load(reply_png, REPLY_LIMITS).map_err(|_| error("bad_reply"))?;
    let rect = geometry::crop_back(plan.pad, source.width, source.height, reply.width, reply.height);
    let cropped = geometry::crop_rgba(&reply.rgba, reply.width, rect);
    let (fw, fh) = geometry::final_size(source.width, source.height);
    let out = resize_rgba(&cropped, rect.width, rect.height, fw, fh, Filter::Lanczos).map_err(|_| error("bad_reply"))?;
    let drift = drift::measure(&source.rgba, source.width, source.height, &out, fw, fh)?;
    let png = encode_png(&rgb_on_white(&out), fw, fh, ExtendedColorType::Rgb8)?;
    Ok(Redrawn { png, width: fw, height: fh, drift })
}

/// `f` on the blocking pool (the pixels and the drift are not work for the async runtime's threads).
pub async fn blocking<T: Send + 'static>(f: impl FnOnce() -> Result<T, CommandError> + Send + 'static) -> Result<T, CommandError> {
    tauri::async_runtime::spawn_blocking(f).await.unwrap_or_else(|e| Err(CommandError::crashed(e)))
}

/// The whole redraw of `source`: prepared, sent, cropped back and measured, with each phase reported.
pub async fn redraw(api: &openai::Api, source: Arc<Source>, options: Options, phase: Arc<dyn Fn(Phase) + Send + Sync>) -> Result<Redrawn, CommandError> {
    phase(Phase::Uploading);
    let src = source.clone();
    let (plan, png) = blocking(move || prepare(&src, options.model)).await?;
    let sent = phase.clone();
    let request = openai::EditRequest { png, width: plan.request.0, height: plan.request.1, model: options.model, quality: options.quality };
    let reply = api.edit(request, move || sent(Phase::Drawing)).await?;
    phase(Phase::Checking);
    blocking(move || finish(&source, plan, &reply)).await
}

/// How to abandon a redraw's task.
pub type Abort = Box<dyn Fn() + Send + Sync>;

/// A finished redraw waiting for Use redraw, Try again or Discard: its own image's id and its drift.
#[derive(Debug, Clone, PartialEq)]
pub struct Pending {
    pub redraw_id: String,
    pub drift: drift::Drift,
}

#[derive(Default)]
struct Slot {
    /// The generation of the redraw running for this image.
    current: Option<u64>,
    abort: Option<Abort>,
    pending: Option<Pending>,
}

/// The redraws in flight (one per image: a new request for an image abandons the old) and the ones awaiting a
/// decision. Every abort is a tokio abort: it never blocks, so it is called under the lock.
#[derive(Default)]
pub struct Redraws {
    slots: Mutex<HashMap<String, Slot>>,
    next: AtomicU64,
}

impl Redraws {
    fn lock(&self) -> MutexGuard<'_, HashMap<String, Slot>> {
        self.slots.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// A new redraw of `image_id`: the one running is abandoned, and the one awaiting a decision is dropped (its
    /// image id is handed back for the caller to let go of). Answers this redraw's generation.
    pub fn reserve(&self, image_id: &str) -> (u64, Option<String>) {
        let generation = self.next.fetch_add(1, Ordering::Relaxed) + 1;
        let mut slots = self.lock();
        let slot = slots.entry(image_id.to_string()).or_default();
        if let Some(abort) = slot.abort.take() {
            abort();
        }
        slot.current = Some(generation);
        (generation, slot.pending.take().map(|p| p.redraw_id))
    }

    /// How to abandon `generation`'s task, kept while it is the current one; called at once when it no longer is
    /// (a cancel that came first).
    pub fn arm(&self, image_id: &str, generation: u64, abort: Abort) {
        let mut slots = self.lock();
        match slots.get_mut(image_id).filter(|s| s.current == Some(generation)) {
            Some(slot) => slot.abort = Some(abort),
            None => abort(),
        }
    }

    /// `generation` finished: its redraw waits for a decision if it is still the current one; else it is handed
    /// back, to be let go of (a reply that arrived after its cancel).
    pub fn finish(&self, image_id: &str, generation: u64, pending: Pending) -> Result<(), Pending> {
        let mut slots = self.lock();
        match slots.get_mut(image_id).filter(|s| s.current == Some(generation)) {
            Some(slot) => {
                slot.current = None;
                slot.abort = None;
                slot.pending = Some(pending);
                Ok(())
            }
            None => Err(pending),
        }
    }

    /// `generation` ended without a redraw.
    pub fn fail(&self, image_id: &str, generation: u64) {
        if let Some(slot) = self.lock().get_mut(image_id).filter(|s| s.current == Some(generation)) {
            slot.current = None;
            slot.abort = None;
        }
    }

    /// Cancel: abandons the redraw running for `image_id`; true when one was.
    pub fn cancel(&self, image_id: &str) -> bool {
        let mut slots = self.lock();
        let Some(slot) = slots.get_mut(image_id) else { return false };
        if let Some(abort) = slot.abort.take() {
            abort();
        }
        slot.current.take().is_some()
    }

    pub fn take_pending(&self, image_id: &str) -> Option<Pending> {
        self.lock().get_mut(image_id)?.pending.take()
    }

    pub fn is_running(&self, image_id: &str) -> bool {
        self.lock().get(image_id).is_some_and(|s| s.current.is_some())
    }

    /// The image was closed: its redraw is abandoned, and the id of one awaiting a decision handed back.
    pub fn forget(&self, image_id: &str) -> Option<String> {
        let slot = self.lock().remove(image_id)?;
        if let Some(abort) = slot.abort {
            abort();
        }
        slot.pending.map(|p| p.redraw_id)
    }
}

/// The [`Abort`] of a task on Tauri's runtime.
pub fn abort_handle<T: Send + 'static>(task: &tauri::async_runtime::JoinHandle<T>) -> Abort {
    let handle = task.inner().abort_handle();
    Box::new(move || handle.abort())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn counter() -> (Arc<AtomicUsize>, Abort) {
        let n = Arc::new(AtomicUsize::new(0));
        let m = n.clone();
        (n, Box::new(move || {
            m.fetch_add(1, Ordering::SeqCst);
        }))
    }

    fn pending(id: &str) -> Pending {
        Pending { redraw_id: id.into(), drift: drift::Drift { edge_f1: 1.0, delta_e: 0.0, verdict: drift::Verdict::Close } }
    }

    #[test]
    fn a_finished_redraw_waits_for_its_decision() {
        let r = Redraws::default();
        let (g, displaced) = r.reserve("a");
        assert_eq!(displaced, None);
        let (aborted, abort) = counter();
        r.arm("a", g, abort);
        assert!(r.is_running("a"));
        assert_eq!(r.finish("a", g, pending("r1")), Ok(()));
        assert!(!r.is_running("a"));
        assert_eq!(aborted.load(Ordering::SeqCst), 0);
        assert_eq!(r.take_pending("a"), Some(pending("r1")));
        assert_eq!(r.take_pending("a"), None);
    }

    #[test]
    fn a_new_request_abandons_the_old_and_drops_its_waiting_redraw() {
        let r = Redraws::default();
        let (first, _) = r.reserve("a");
        let (aborted, abort) = counter();
        r.arm("a", first, abort);
        let (second, displaced) = r.reserve("a");
        assert_eq!((aborted.load(Ordering::SeqCst), displaced), (1, None));
        assert_eq!(r.finish("a", first, pending("late")), Err(pending("late")));
        r.fail("a", first); // the old one's end is not the new one's
        assert!(r.is_running("a"));
        assert_eq!(r.finish("a", second, pending("r2")), Ok(()));
        let (_, displaced) = r.reserve("a");
        assert_eq!(displaced.as_deref(), Some("r2"));
    }

    #[test]
    fn cancel_abandons_the_running_redraw_and_refuses_its_reply() {
        let r = Redraws::default();
        assert!(!r.cancel("a"));
        let (g, _) = r.reserve("a");
        let (aborted, abort) = counter();
        r.arm("a", g, abort);
        assert!(r.cancel("a"));
        assert_eq!(aborted.load(Ordering::SeqCst), 1);
        assert_eq!(r.finish("a", g, pending("late")), Err(pending("late")));
        assert_eq!(r.take_pending("a"), None);
    }

    #[test]
    fn a_task_armed_after_its_cancel_is_abandoned_at_once() {
        let r = Redraws::default();
        let (g, _) = r.reserve("a");
        assert!(r.cancel("a"));
        let (aborted, abort) = counter();
        r.arm("a", g, abort);
        assert_eq!(aborted.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn closing_an_image_abandons_its_redraw_and_hands_back_the_waiting_one() {
        let r = Redraws::default();
        let (g, _) = r.reserve("a");
        r.finish("a", g, pending("r1")).unwrap();
        let (g, _) = r.reserve("b");
        let (aborted, abort) = counter();
        r.arm("b", g, abort);
        assert_eq!(r.forget("a").as_deref(), Some("r1"));
        assert_eq!(r.forget("b"), None);
        assert_eq!(aborted.load(Ordering::SeqCst), 1);
        assert_eq!(r.forget("c"), None);
    }

    #[test]
    fn models_and_qualities_read_their_setting_and_default_safely() {
        assert_eq!(Model::parse("gpt-image-1.5"), Model::GptImage15);
        assert_eq!(Model::parse("gpt-image-2"), Model::GptImage2);
        assert_eq!(Model::parse("dall-e-3"), Model::GptImage2);
        assert_eq!((Model::GptImage2.id(), Model::GptImage15.id()), ("gpt-image-2", "gpt-image-1.5"));
        assert_eq!(Quality::parse("high"), Quality::High);
        assert_eq!(Quality::parse(""), Quality::Medium);
        assert_eq!((Quality::Medium.id(), Quality::High.id()), ("medium", "high"));
        assert_eq!((ROUGH_SIDE, REQUEST_SIDE), (600, 2048));
    }

    #[test]
    fn every_failure_has_its_code_status_and_plain_words() {
        let table = [
            ("no_key", 401),
            ("invalid_key", 401),
            ("quota", 429),
            ("not_allowed", 403),
            ("refused", 422),
            ("timeout", 504),
            ("offline", 503),
            ("bad_reply", 502),
            ("too_large", 413),
            ("cancelled", 499),
        ];
        for (code, status) in table {
            let e = error(code);
            assert_eq!((e.code(), e.status), (Some(code), status), "{code}");
            assert!(!e.message().is_empty() && !e.message().contains("sk-"), "{code}");
        }
        assert!(error("quota").message().to_lowercase().contains("your openai account is out of credit or rate limited"));
    }

    /// A 400 x 100 RGBA source: a blue bar (x 50..350, y 30..70) on `ground`.
    fn bar_source(ground: [u8; 4]) -> Source {
        let rgba = (0..400u32 * 100)
            .flat_map(|i| -> [u8; 4] {
                let (x, y) = (i % 400, i / 400);
                if (50..350).contains(&x) && (30..70).contains(&y) {
                    [0, 80, 200, 255]
                } else {
                    ground
                }
            })
            .collect();
        Source { rgba, width: 400, height: 100 }
    }

    fn rgb_png(w: u32, h: u32, colour: impl Fn(u32, u32) -> [u8; 3]) -> Vec<u8> {
        let mut out = std::io::Cursor::new(Vec::new());
        image::RgbImage::from_fn(w, h, |x, y| image::Rgb(colour(x, y))).write_to(&mut out, image::ImageFormat::Png).unwrap();
        out.into_inner()
    }

    #[test]
    fn a_transparent_source_is_sent_on_white_padded_with_its_border_colour() {
        let (plan, png) = prepare(&bar_source([0, 0, 0, 0]), Model::GptImage2).unwrap();
        assert_eq!(plan.pad, geometry::Pad { left: 0, top: 17, width: 400, height: 134 });
        assert_eq!(plan.request, (2048, 688));
        let sent = image::load_from_memory(&png).unwrap().to_rgb8();
        assert_eq!(sent.dimensions(), (400, 134));
        assert_eq!(sent.get_pixel(0, 0).0, [255, 255, 255]); // padding: the border colour, white
        assert_eq!(sent.get_pixel(5, 20).0, [255, 255, 255]); // the transparent ground, on white
        assert_eq!(sent.get_pixel(200, 17 + 50).0, [0, 80, 200]);
    }

    #[test]
    fn a_reply_comes_back_in_the_original_framing_at_the_largest_size() {
        let source = bar_source([255, 255, 255, 255]);
        let (plan, _) = prepare(&source, Model::GptImage2).unwrap();
        // what a faithful model would draw at 2048 x 688: the padded source, scaled
        let reply = rgb_png(2048, 688, |x, y| {
            let (px, py) = ((f64::from(x) + 0.5) * 400.0 / 2048.0, (f64::from(y) + 0.5) * 134.0 / 688.0 - 17.0);
            if (50.0..350.0).contains(&px) && (30.0..70.0).contains(&py) {
                [0, 80, 200]
            } else {
                [255, 255, 255]
            }
        });
        let done = finish(&source, plan, &reply).unwrap();
        assert_eq!((done.width, done.height), (2048, 512));
        assert_eq!(image::load_from_memory(&done.png).unwrap().to_rgb8().dimensions(), (2048, 512));
        assert_eq!(done.drift.verdict, drift::Verdict::Close, "{:?}", done.drift);
    }

    #[test]
    fn a_reply_that_is_not_an_image_is_a_bad_reply() {
        let source = bar_source([255, 255, 255, 255]);
        let (plan, _) = prepare(&source, Model::GptImage2).unwrap();
        assert_eq!(finish(&source, plan, b"not a png").err().and_then(|e| e.code().map(str::to_string)).as_deref(), Some("bad_reply"));
    }

    #[test]
    fn phases_are_the_events_words() {
        let words: Vec<_> = [Phase::Uploading, Phase::Drawing, Phase::Checking, Phase::Done, Phase::Failed].iter().map(|p| serde_json::to_value(p).unwrap()).collect();
        assert_eq!(words, ["uploading", "drawing", "checking", "done", "failed"]);
    }
}
