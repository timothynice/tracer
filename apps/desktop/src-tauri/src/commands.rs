//! Every command the UI invokes. Anything that blocks (a panel, a file, a trace) is `async`, which Tauri runs
//! off the main thread; a sync command runs on it.
use crate::error::CommandError;
use crate::intake;
use crate::store::Opened;
use crate::AppState;
use serde::Serialize;
use serde_json::Value;
use std::path::PathBuf;
use tauri::ipc::{InvokeBody, Request, Response};
use tauri::State;
use tauri_plugin_dialog::DialogExt;

/// One file's outcome: `{"ok": {...}}` or `{"failed": {"name", "path", "error"}}`.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum Outcome {
    Ok(Opened),
    Failed { name: String, path: Option<String>, error: CommandError },
}

pub const IMAGE_EXTENSIONS: &[&str] = &["png", "jpg", "jpeg", "gif", "webp", "bmp", "heic", "heif", "tif", "tiff"];

pub(crate) fn open_one<R: tauri::Runtime>(app: &tauri::AppHandle<R>, state: &AppState, path: PathBuf, downscale: bool) -> Outcome {
    match intake::open_path(&state.core, &path, downscale) {
        Ok(image) => {
            crate::settings::note_recent(app, &path.display().to_string());
            Outcome::Ok(state.images.insert(image))
        }
        Err(error) => {
            // a recent file that no longer opens (moved, deleted, unreadable, not an image) leaves Open Recent; one
            // refused for its size stays, since Downscale opens it
            if !matches!(error.code(), Some("too_many_pixels" | "too_large")) {
                crate::settings::forget_recent(app, &path.display().to_string());
            }
            Outcome::Failed { name: intake::file_name(&path), path: Some(path.display().to_string()), error }
        }
    }
}

#[tauri::command]
pub fn engines(state: State<'_, AppState>) -> Value {
    state.core.engines()
}

#[tauri::command]
pub fn presets(state: State<'_, AppState>) -> Value {
    state.core.presets()
}

#[tauri::command]
pub async fn open_paths(app: tauri::AppHandle, state: State<'_, AppState>, paths: Vec<String>, downscale: bool) -> Result<Vec<Outcome>, CommandError> {
    Ok(paths.into_iter().map(|p| open_one(&app, &state, PathBuf::from(p), downscale)).collect())
}

#[tauri::command]
pub async fn pick_images(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<Vec<Outcome>, CommandError> {
    let picked = app.dialog().file().set_title("Open Images").add_filter("Images", IMAGE_EXTENSIONS).blocking_pick_files().unwrap_or_default();
    Ok(picked.into_iter().filter_map(|f| f.into_path().ok()).map(|p| open_one(&app, &state, p, false)).collect())
}

#[tauri::command]
pub async fn open_bytes(state: State<'_, AppState>, request: Request<'_>) -> Result<Outcome, CommandError> {
    let InvokeBody::Raw(bytes) = request.body() else {
        return Err(CommandError::bad_request("open_bytes takes the file's bytes"));
    };
    let name = request.headers().get("x-name").and_then(|v| v.to_str().ok()).map(intake::decode_header_name).unwrap_or_else(|| "Untitled".into());
    Ok(match intake::open_bytes(&state.core, &name, bytes.clone()) {
        Ok(image) => Outcome::Ok(state.images.insert(image)),
        Err(error) => Outcome::Failed { name, path: None, error },
    })
}

#[tauri::command]
pub async fn read_image(state: State<'_, AppState>, id: String) -> Result<Response, CommandError> {
    let bytes = state.images.bytes(&id).ok_or_else(CommandError::expired)?;
    Ok(Response::new(bytes.as_ref().clone()))
}

#[tauri::command]
pub fn close_image(state: State<'_, AppState>, id: String) -> bool {
    close(&state.images, &state.redraws, &id)
}

use crate::queue::JobSpec;
use tauri::Emitter;

#[tauri::command]
pub async fn vectorize(app: tauri::AppHandle, state: State<'_, AppState>, image_id: String, parameters: Value, auto: bool, job: String) -> Result<Value, CommandError> {
    let bytes = state.images.bytes(&image_id).ok_or_else(CommandError::expired)?;
    let (emitter, job_id) = (app.clone(), job.clone());
    let rx = state.queue.submit(JobSpec { id: job, image_id, bytes, parameters, auto, test: None }, move || {
        let _ = emitter.emit("trace-phase", serde_json::json!({ "job": job_id, "phase": "tracing" }));
    });
    tauri::async_runtime::spawn_blocking(move || rx.recv().unwrap_or_else(|_| Err(CommandError::cancelled())))
        .await
        .unwrap_or_else(|e| Err(CommandError::crashed(e)))
}

#[tauri::command]
pub fn cancel_trace(state: State<'_, AppState>, job: String) -> bool {
    state.queue.cancel(&job)
}

use crate::export;
use std::path::Path;
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_opener::OpenerExt;

fn header(request: &Request<'_>, key: &str) -> Option<String> {
    request.headers().get(key).and_then(|v| v.to_str().ok()).map(intake::decode_header_name)
}

fn panel_path(fp: tauri_plugin_dialog::FilePath) -> Result<PathBuf, CommandError> {
    fp.into_path().map_err(|e| CommandError::bad_request(format!("not a file path: {e}")))
}

#[tauri::command]
pub async fn export_file(app: tauri::AppHandle, state: State<'_, AppState>, request: Request<'_>) -> Result<Option<String>, CommandError> {
    let InvokeBody::Raw(bytes) = request.body() else {
        return Err(CommandError::bad_request("export_file takes the file's bytes"));
    };
    let ext = match header(&request, "x-kind").as_deref() {
        Some("png") => "png",
        Some("pdf") => "pdf",
        _ => "svg",
    };
    // a PDF arrives as the SVG's text and is converted here, before anything is written
    let converted;
    let bytes: &[u8] = if ext == "pdf" {
        converted = export::to_pdf(bytes)?;
        &converted
    } else {
        bytes
    };
    let name = export::safe_name(&header(&request, "x-name").unwrap_or_default(), ext);
    let original = header(&request, "x-image").and_then(|id| state.images.get(&id)).and_then(|i| i.path);
    let destination = export::Destination::parse(&header(&request, "x-destination").unwrap_or_default());
    let reveal = header(&request, "x-reveal").as_deref() == Some("1");
    let done = |path: &Path| {
        if reveal {
            let _ = app.opener().reveal_item_in_dir(path);
        }
        Ok(Some(path.display().to_string()))
    };
    let mut fell_back = false;
    if let Some(path) = export::beside(original.as_deref(), destination, &name) {
        match export::save(&path, bytes, original.as_deref()) {
            Ok(()) => return done(&path),
            // a read-only volume or a folder that is gone: the same export through the panel instead
            Err(e) if e.elsewhere => {
                eprintln!("studi0trace: export beside the original failed ({}); asking where", e.error.message());
                fell_back = true;
            }
            Err(e) => return Err(e.error),
        }
    }
    let mut panel = app
        .dialog()
        .file()
        // the reason the panel came up when the user asked for Beside, visible before they could cancel
        .set_title(if fell_back {
            "Studi0Trace cannot save next to the original. Choose where to save."
        } else if ext == "png" {
            "Export PNG"
        } else if ext == "pdf" {
            "Export PDF"
        } else {
            "Export SVG"
        })
        .set_file_name(export::panel_name(&name, original.as_deref()))
        .add_filter(
            match ext {
                "png" => "PNG image",
                "pdf" => "PDF document",
                _ => "SVG image",
            },
            &[ext],
        );
    if let Some(dir) = original.as_deref().and_then(Path::parent).filter(|d| d.is_dir()) {
        panel = panel.set_directory(dir);
    }
    let Some(chosen) = panel.blocking_save_file() else {
        return Ok(None);
    };
    let path = panel_path(chosen)?;
    export::save(&path, bytes, original.as_deref()).map_err(|e| e.error)?;
    done(&path)
}

#[tauri::command]
pub async fn export_all(app: tauri::AppHandle, items: Vec<export::Item>, reveal: bool) -> Result<Option<export::Written>, CommandError> {
    let Some(dir) = app.dialog().file().set_title("Export All").blocking_pick_folder() else {
        return Ok(None);
    };
    let answer = export::write_all(&panel_path(dir)?, &items);
    if reveal {
        if let Some(first) = answer.written.first() {
            let _ = app.opener().reveal_item_in_dir(first);
        }
    }
    Ok(Some(answer))
}

#[tauri::command]
pub async fn reveal(app: tauri::AppHandle, path: String) -> Result<(), CommandError> {
    let p = PathBuf::from(&path);
    if !p.exists() {
        return Err(CommandError::io(&p, &std::io::Error::from(std::io::ErrorKind::NotFound)));
    }
    app.opener().reveal_item_in_dir(&p).map_err(|e| CommandError::new(500, "io_error", e.to_string()))
}

#[tauri::command]
pub fn copy_text(app: tauri::AppHandle, text: String) -> Result<(), CommandError> {
    app.clipboard().write_text(text).map_err(|e| CommandError::new(500, "io_error", e.to_string()))
}

use crate::menu::MenuState;
use crate::settings::{self, Settings};
use std::sync::Mutex;

#[tauri::command]
pub fn load_settings(app: tauri::AppHandle) -> Settings {
    settings::load(&app)
}

#[tauri::command]
pub async fn save_settings(app: tauri::AppHandle, settings: Settings) -> Result<Settings, CommandError> {
    settings::update(&app, |stored| stored.merged(settings))
}

pub const CLEAR_TITLE: &str = "Clear all images?";
pub const CLEAR: &str = "Clear All";

/// What Clear All would lose, as the alert's message; None when nothing would be (it then clears without asking).
pub fn clear_prompt(unexported: u32) -> Option<String> {
    match unexported {
        0 => None,
        1 => Some("1 traced image has not been exported.".to_string()),
        n => Some(format!("{n} traced images have not been exported.")),
    }
}

/// Clear All's question, for the menu item and the sidebar's button alike: true to clear. A sheet on the main
/// window, as Quit's is; the answer comes back once it is chosen.
#[tauri::command]
pub async fn confirm_clear(app: tauri::AppHandle, unexported: u32) -> bool {
    use tauri::Manager;
    use tauri_plugin_dialog::{MessageDialogButtons, MessageDialogKind};
    let Some(message) = clear_prompt(unexported) else { return true };
    let mut dialog = app
        .dialog()
        .message(message)
        .title(CLEAR_TITLE)
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(CLEAR.into(), crate::quit::CANCEL.into()));
    if let Some(main) = app.get_webview_window(crate::opens::MAIN) {
        crate::quit::bring_up(&app, &main);
        dialog = dialog.parent(&main);
    }
    dialog.blocking_show()
}

#[tauri::command]
pub fn set_menu_state(app: tauri::AppHandle, state: MenuState) {
    crate::menu::apply_state(&app, &state);
}

#[tauri::command]
pub fn take_pending_opens(window: tauri::WebviewWindow, opens: State<'_, Mutex<crate::opens::Opens>>) -> Vec<String> {
    opens.lock().unwrap_or_else(std::sync::PoisonError::into_inner).take_for(window.label())
}

pub(crate) fn show_settings_window<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> tauri::Result<()> {
    use tauri::Manager;
    if let Some(w) = app.get_webview_window("settings") {
        w.show()?;
        return w.set_focus();
    }
    tauri::WebviewWindowBuilder::new(app, "settings", tauri::WebviewUrl::App("index.html".into()))
        .title("Settings")
        // the AI redraw group made the sheet taller
        .inner_size(520.0, 780.0)
        .resizable(false)
        .minimizable(false)
        .maximizable(false)
        .theme(crate::settings::theme_for(&settings::load(app).appearance))
        .build()?;
    Ok(())
}

#[tauri::command]
pub fn open_settings_window(app: tauri::AppHandle) -> Result<(), CommandError> {
    show_settings_window(&app).map_err(|e| CommandError::new(500, "io_error", e.to_string()))
}

#[cfg(test)]
mod clear_tests {
    use super::*;

    #[test]
    fn clear_all_asks_only_when_a_vector_would_be_lost() {
        assert_eq!(clear_prompt(0), None);
        assert_eq!(clear_prompt(1).as_deref(), Some("1 traced image has not been exported."));
        assert_eq!(clear_prompt(4).as_deref(), Some("4 traced images have not been exported."));
        assert_eq!((CLEAR_TITLE, CLEAR), ("Clear all images?", "Clear All"));
    }
}

use crate::keychain;
use crate::redraw::{self, drift::Drift, openai, rough::Roughness, Model, Options, Pending, Phase, Quality, Redraws, Source};
use crate::store::{original_id, Images, OpenImage};
use std::sync::Arc;

/// `redraw_image`'s answer: the redraw (opened as any pasted image is, with its own id) and how far it moved.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RedrawAnswer {
    pub redraw: Opened,
    pub drift: Drift,
}

/// `accept_redraw`'s answer: the image, now drawn from the redraw, and the entry that keeps its original.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Accepted {
    pub image: Opened,
    pub original: Opened,
}

/// Lets a redraw's own store entry go, never the image's own or the entry that keeps its original.
fn let_go(images: &Images, image_id: &str, redraw_id: &str) {
    if redraw_id != image_id && redraw_id != original_id(image_id) {
        images.remove(redraw_id);
    }
}

/// An image closed: its redraw is abandoned, and one awaiting a decision let go of with its store entry;
/// `remove` takes the image's original too.
pub(crate) fn close(images: &Images, redraws: &Redraws, id: &str) -> bool {
    if let Some(waiting) = redraws.forget(id) {
        let_go(images, id, &waiting);
    }
    images.remove(id)
}

/// A redraw that may go ahead: the image as it was opened, the key, and the generation reserved for it.
pub(crate) struct Started {
    pub source: OpenImage,
    pub key: String,
    pub generation: u64,
}

/// What a redraw starts from, checked before anything goes anywhere: the image as it was opened, then the key. An
/// unknown image is `image_expired` and nothing is reserved or asked; no key is `no_key`, and a keychain that will
/// not hand the key over is `keychain`, both before any network call. The generation is reserved *before* the
/// keychain is read, so a cancel or a close during the read (a whole macOS prompt, on an unsigned rebuild) finds
/// the redraw and ends it: when the read returns and the generation is no longer current, the answer is `cancelled`
/// and nothing has been built or sent. The keychain is read off the runtime's threads.
pub(crate) async fn redraw_start(images: &Images, redraws: &Redraws, read_key: impl FnOnce() -> Result<Option<String>, CommandError> + Send + 'static, id: &str) -> Result<Started, CommandError> {
    let source = images.source_of(id).ok_or_else(CommandError::expired)?;
    let (generation, displaced) = redraws.reserve(id);
    if let Some(old) = displaced {
        let_go(images, id, &old);
    }
    let key = match redraw::blocking(read_key).await {
        Ok(Some(key)) => key,
        Ok(None) => {
            redraws.fail(id, generation);
            return Err(redraw::error("no_key"));
        }
        Err(e) => {
            redraws.fail(id, generation);
            return Err(e);
        }
    };
    if !redraws.is_current(id, generation) || images.source_of(id).is_none() {
        return Err(redraw::error("cancelled"));
    }
    Ok(Started { source, key, generation })
}

/// How a redraw's task ended without an answer: a panic is a crash and says so; anything else is the abort of a
/// cancel, a newer request or a closed image.
pub(crate) fn join_failure(e: tauri::Error) -> CommandError {
    match e {
        tauri::Error::JoinError(j) if j.is_panic() => CommandError::crashed("the redraw stopped unexpectedly"),
        _ => redraw::error("cancelled"),
    }
}

/// The terminal phase of a redraw that ended: `done` for an answer, `failed` for a failure that ended its own
/// generation, nothing for a job a cancel, a newer request or a close had already ended (it answers `cancelled`
/// and the webview already knows).
pub(crate) fn terminal_phase(redraws: &Redraws, image_id: &str, generation: u64, answered: bool) -> Option<Phase> {
    if answered {
        Some(Phase::Done)
    } else if redraws.fail(image_id, generation) {
        Some(Phase::Failed)
    } else {
        None
    }
}

/// Use redraw: the redraw awaiting a decision becomes the image's source; the original is kept. Whatever the
/// outcome, the pending redraw's own entry is gone.
pub(crate) fn accept(images: &Images, redraws: &Redraws, id: &str) -> Result<Accepted, CommandError> {
    let pending = redraws.take_pending(id).ok_or_else(|| CommandError::new(409, "no_redraw", "There is no redraw waiting for this image."))?;
    match images.accept_redraw(id, &pending.redraw_id) {
        Some((image, original)) => Ok(Accepted { image, original }),
        None => {
            let_go(images, id, &pending.redraw_id);
            Err(CommandError::expired())
        }
    }
}

/// Discard: the redraw awaiting a decision is let go of; the image is as it was.
pub(crate) fn discard(images: &Images, redraws: &Redraws, id: &str) -> bool {
    match redraws.take_pending(id) {
        Some(pending) => {
            let_go(images, id, &pending.redraw_id);
            true
        }
        None => false,
    }
}

/// Whether an OpenAI key is stored; the key itself never reaches the webview.
#[tauri::command]
pub async fn redraw_key_status() -> bool {
    redraw::blocking(|| Ok(keychain::has_key())).await.unwrap_or(false)
}

#[tauri::command]
pub async fn set_redraw_key(app: tauri::AppHandle, key: String) -> Result<(), CommandError> {
    use tauri::Emitter;
    redraw::blocking(move || keychain::set_key(&key)).await?;
    let _ = app.emit("redraw-key", true);
    Ok(())
}

#[tauri::command]
pub async fn delete_redraw_key(app: tauri::AppHandle) -> Result<(), CommandError> {
    use tauri::Emitter;
    redraw::blocking(keychain::delete_key).await?;
    let _ = app.emit("redraw-key", false);
    Ok(())
}

/// Whether the image looks rough (the inspector's hint).
#[tauri::command]
pub async fn image_roughness(state: State<'_, AppState>, id: String) -> Result<Roughness, CommandError> {
    let bytes = state.images.bytes(&id).ok_or_else(CommandError::expired)?;
    redraw::blocking(move || {
        let source = Source::decode(&bytes)?;
        Ok(redraw::rough::assess(&source.rgba, source.width, source.height))
    })
    .await
}

/// Redraws the image with AI, always from the image as it was opened. The answer waits in `redraws` for Use
/// redraw, Try again or Discard; nothing about the image changes here. A new request for the image abandons the
/// one running, and `cancel_redraw` abandons it too: a reply that arrives after either is let go of.
#[tauri::command]
pub async fn redraw_image(app: tauri::AppHandle, state: State<'_, AppState>, id: String) -> Result<RedrawAnswer, CommandError> {
    use tauri::{Emitter, Manager};
    // the image and the key are checked first: with no key nothing is read, built or sent
    let Started { source, key, generation } = redraw_start(&state.images, &state.redraws, keychain::read_key, &id).await?;
    let chosen = settings::load(&app);
    let options = Options { model: Model::parse(&chosen.redraw_model), quality: Quality::parse(&chosen.redraw_quality) };
    let api = match openai::Api::new(&openai::base_url(), &key) {
        Ok(api) => api,
        Err(e) => {
            state.redraws.fail(&id, generation);
            return Err(e);
        }
    };
    let (emitter, image_id) = (app.clone(), id.clone());
    let phase: Arc<dyn Fn(Phase) + Send + Sync> = Arc::new(move |p| {
        let _ = emitter.emit("redraw-phase", serde_json::json!({ "id": image_id, "phase": p }));
    });
    let (handle, task_id, task_phase) = (app.clone(), id.clone(), phase.clone());
    let task = tauri::async_runtime::spawn(async move {
        let bytes = source.bytes.clone();
        let decoded = Arc::new(redraw::blocking(move || Source::decode(&bytes)).await?);
        let done = redraw::redraw(&api, decoded, options, task_phase).await?;
        let (opener, name, png) = (handle.clone(), source.name.clone(), done.png);
        // a reply the intake will not take is not a usable image, whatever its own words
        let opened = redraw::blocking(move || intake::open_bytes(&opener.state::<AppState>().core, &name, png).map_err(|_| redraw::error("bad_reply"))).await?;
        let state = handle.state::<AppState>();
        let redraw_id = opened.id.clone();
        // an id some image already has would share its entry, and letting the redraw go would let the image go
        let shown = state.images.insert_new(opened).ok_or_else(|| redraw::error("bad_reply"))?;
        match state.redraws.finish(&task_id, generation, Pending { redraw_id: redraw_id.clone(), drift: done.drift }) {
            Ok(()) => Ok(RedrawAnswer { redraw: shown, drift: done.drift }),
            Err(_) => {
                state.images.remove(&redraw_id);
                Err(redraw::error("cancelled"))
            }
        }
    });
    state.redraws.arm(&id, generation, redraw::abort_handle(&task));
    let answer = task.await.unwrap_or_else(|e| Err(join_failure(e)));
    if let Some(last) = terminal_phase(&state.redraws, &id, generation, answer.is_ok()) {
        phase(last);
    }
    answer
}

#[tauri::command]
pub fn cancel_redraw(state: State<'_, AppState>, id: String) -> bool {
    state.redraws.cancel(&id)
}

/// Use redraw: the image's source becomes its redraw; the original is kept for Show Original and Revert.
#[tauri::command]
pub fn accept_redraw(state: State<'_, AppState>, id: String) -> Result<Accepted, CommandError> {
    accept(&state.images, &state.redraws, &id)
}

/// Discard: the redraw awaiting a decision is let go of; the image is as it was.
#[tauri::command]
pub fn discard_redraw(state: State<'_, AppState>, id: String) -> bool {
    discard(&state.images, &state.redraws, &id)
}

/// Revert to Original: the image's source is its original again.
#[tauri::command]
pub fn revert_redraw(state: State<'_, AppState>, id: String) -> Result<Opened, CommandError> {
    state.images.revert(&id).ok_or_else(|| CommandError::new(409, "not_redrawn", "This image is not drawn from a redraw."))
}

#[cfg(test)]
mod redraw_tests {
    use super::*;
    use std::path::PathBuf;

    fn opened(id: &str) -> Opened {
        Opened { id: id.into(), name: "a.png".into(), path: None, width: 8, height: 4, format: "PNG".into() }
    }

    fn entry(id: &str, bytes: &[u8]) -> OpenImage {
        OpenImage { id: id.into(), name: "logo.png".into(), path: Some(PathBuf::from("/pics/logo.png")), width: 8, height: 4, format: "PNG".into(), bytes: Arc::new(bytes.to_vec()), original: None }
    }

    fn pending(id: &str) -> Pending {
        Pending { redraw_id: id.into(), drift: Drift { edge_f1: 1.0, delta_e: 0.0, verdict: redraw::drift::Verdict::Close } }
    }

    /// `a` open, and a redraw `r` of it finished and waiting for a decision.
    fn waiting() -> (Images, Redraws) {
        let (images, redraws) = (Images::default(), Redraws::default());
        images.insert(entry("a", &[1]));
        let (g, _) = redraws.reserve("a");
        images.insert(entry("r", &[2]));
        redraws.finish("a", g, pending("r")).unwrap();
        (images, redraws)
    }

    #[test]
    fn the_answers_are_the_webviews_json() {
        let drift = Drift { edge_f1: 0.97, delta_e: 1.25, verdict: redraw::drift::Verdict::Close };
        let v = serde_json::to_value(RedrawAnswer { redraw: opened("r"), drift }).unwrap();
        assert_eq!(v, serde_json::json!({"redraw": {"id": "r", "name": "a.png", "path": null, "width": 8, "height": 4, "format": "PNG"}, "drift": {"edgeF1": 0.97, "deltaE": 1.25, "verdict": "close"}}));
        let v = serde_json::to_value(Accepted { image: opened("a"), original: opened("a-original") }).unwrap();
        assert_eq!((v["image"]["id"].as_str(), v["original"]["id"].as_str()), (Some("a"), Some("a-original")));
    }

    fn run<T>(f: impl std::future::Future<Output = T>) -> T {
        tauri::async_runtime::block_on(f)
    }

    #[test]
    fn no_key_is_answered_before_anything_else_is_done() {
        let (images, redraws) = (Images::default(), Redraws::default());
        images.insert(entry("a", &[1]));
        let e = run(redraw_start(&images, &redraws, || Ok(None), "a")).err().unwrap();
        assert_eq!((e.code(), e.status), (Some("no_key"), 401));
        assert!(!redraws.is_running("a"), "a refused start leaves no job behind");
        let asked = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let flag = asked.clone();
        let e = run(redraw_start(&images, &redraws, move || { flag.store(true, std::sync::atomic::Ordering::SeqCst); Ok(None) }, "gone")).err().unwrap();
        assert_eq!(e.code(), Some("image_expired"), "an unknown image is named first");
        assert!(!asked.load(std::sync::atomic::Ordering::SeqCst), "and the keychain is not asked");
        let started = run(redraw_start(&images, &redraws, || Ok(Some("sk-test".into())), "a")).unwrap();
        assert_eq!((started.source.id.as_str(), started.key.as_str()), ("a", "sk-test"));
        assert!(redraws.is_current("a", started.generation) && redraws.is_running("a"));
    }

    #[test]
    fn a_keychain_that_refuses_is_its_own_error_and_not_no_key() {
        let (images, redraws) = (Images::default(), Redraws::default());
        images.insert(entry("a", &[1]));
        let e = run(redraw_start(&images, &redraws, || Err(CommandError::new(500, "keychain", "denied")), "a")).err().unwrap();
        assert_eq!((e.code(), e.status), (Some("keychain"), 500));
        assert!(!redraws.is_running("a"));
    }

    /// The keychain read can be a whole macOS prompt. A cancel or a close during it must find the redraw, and the
    /// answer must be `cancelled` with no request: the read here blocks until the test has cancelled.
    #[test]
    fn a_cancel_or_close_during_the_key_read_stops_the_redraw_before_anything_is_sent() {
        for how in ["cancel", "close", "newer"] {
            let (images, redraws) = (Arc::new(Images::default()), Arc::new(Redraws::default()));
            images.insert(entry("a", &[1]));
            let (reading_tx, reading_rx) = std::sync::mpsc::channel::<()>();
            let (release_tx, release_rx) = std::sync::mpsc::channel::<()>();
            let (i, r) = (images.clone(), redraws.clone());
            let start = std::thread::spawn(move || {
                run(redraw_start(&i, &r, move || {
                    reading_tx.send(()).unwrap();
                    release_rx.recv().unwrap();
                    Ok(Some("sk-test".into()))
                }, "a"))
            });
            reading_rx.recv().unwrap();
            // the redraw is already there to be found while the keychain is being read
            assert!(redraws.is_running("a"), "{how}");
            match how {
                "cancel" => assert!(redraws.cancel("a"), "a cancel finds the redraw"),
                "close" => assert!(close(&images, &redraws, "a")),
                _ => {
                    redraws.reserve("a");
                }
            }
            release_tx.send(()).unwrap();
            let e = start.join().unwrap().err().unwrap();
            assert_eq!(e.code(), Some("cancelled"), "{how}");
        }
    }

    #[test]
    fn a_cancelled_job_emits_no_terminal_phase_and_a_failure_exactly_one() {
        let redraws = Redraws::default();
        // a real failure: the current generation ends once
        let (g, _) = redraws.reserve("a");
        assert_eq!(terminal_phase(&redraws, "a", g, false), Some(Phase::Failed));
        assert_eq!(terminal_phase(&redraws, "a", g, false), None);
        // an answer is done
        let (g, _) = redraws.reserve("a");
        assert_eq!(terminal_phase(&redraws, "a", g, true), Some(Phase::Done));
        // cancelled, superseded or closed: silent
        let (g, _) = redraws.reserve("a");
        assert!(redraws.cancel("a"));
        assert_eq!(terminal_phase(&redraws, "a", g, false), None);
        let (old, _) = redraws.reserve("a");
        let (new, _) = redraws.reserve("a");
        assert_eq!(terminal_phase(&redraws, "a", old, false), None);
        assert_eq!(terminal_phase(&redraws, "a", new, false), Some(Phase::Failed));
        let (g, _) = redraws.reserve("a");
        redraws.forget("a");
        assert_eq!(terminal_phase(&redraws, "a", g, false), None);
    }

    #[test]
    fn a_panicking_task_is_a_crash_and_an_aborted_one_is_cancelled() {
        let panicked = run(async { tauri::async_runtime::spawn(async { panic!("the redraw task's own test panic") }).await });
        let e = join_failure(panicked.unwrap_err());
        assert_eq!((e.code(), e.status), (Some("engine_crashed"), 500));
        let aborted = run(async {
            let task = tauri::async_runtime::spawn(std::future::pending::<()>());
            task.inner().abort();
            task.await
        });
        assert_eq!(join_failure(aborted.unwrap_err()).code(), Some("cancelled"));
    }

    #[test]
    fn a_redraw_starts_from_the_original_of_a_redrawn_image() {
        let (images, redraws) = waiting();
        accept(&images, &redraws, "a").unwrap();
        let started = run(redraw_start(&images, &redraws, || Ok(Some("k".into())), "a")).unwrap();
        assert_eq!((started.source.id.as_str(), started.source.bytes.as_slice()), ("a-original", &[1][..]));
    }

    #[test]
    fn closing_an_image_with_a_decision_pending_leaks_nothing() {
        let (images, redraws) = waiting();
        assert_eq!(images.len(), 2);
        assert!(close(&images, &redraws, "a"));
        assert!(images.is_empty(), "the redraw's own entry went with the image");
        assert!(redraws.take_pending("a").is_none());
        assert!(!close(&images, &redraws, "a"));
    }

    #[test]
    fn closing_an_image_mid_redraw_abandons_it_and_a_late_reply_is_let_go_of() {
        let (images, redraws) = (Images::default(), Redraws::default());
        images.insert(entry("a", &[1]));
        let (g, _) = redraws.reserve("a");
        let aborted = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let flag = aborted.clone();
        redraws.arm("a", g, Box::new(move || flag.store(true, std::sync::atomic::Ordering::SeqCst)));
        assert!(close(&images, &redraws, "a"));
        assert!(aborted.load(std::sync::atomic::Ordering::SeqCst));
        assert!(!redraws.is_running("a"));
        // the reply that still arrives has nowhere to wait: the task lets its own entry go
        assert!(redraws.finish("a", g, pending("late")).is_err());
        assert!(images.is_empty());
    }

    #[test]
    fn closing_a_redrawn_image_lets_its_original_go_too() {
        let (images, redraws) = waiting();
        accept(&images, &redraws, "a").unwrap();
        assert_eq!(images.len(), 2);
        assert!(close(&images, &redraws, "a"));
        assert!(images.is_empty());
    }

    #[test]
    fn accept_swaps_once_and_then_has_nothing_waiting() {
        let (images, redraws) = waiting();
        let done = accept(&images, &redraws, "a").unwrap();
        assert_eq!((done.image.id.as_str(), done.original.id.as_str()), ("a", "a-original"));
        assert_eq!(images.bytes("a").unwrap().as_slice(), &[2]);
        assert!(images.get("r").is_none());
        let e = accept(&images, &redraws, "a").unwrap_err();
        assert_eq!((e.status, e.code()), (409, Some("no_redraw")));
    }

    #[test]
    fn a_pending_redraw_that_cannot_be_used_is_still_let_go_of() {
        let (images, redraws) = (Images::default(), Redraws::default());
        images.insert(entry("a", &[1]));
        let (g, _) = redraws.reserve("a");
        redraws.finish("a", g, pending("a")).unwrap(); // its id is the image's own: never swapped, never removed
        let e = accept(&images, &redraws, "a").unwrap_err();
        assert_eq!(e.code(), Some("image_expired"));
        assert_eq!(images.bytes("a").unwrap().as_slice(), &[1]);
        // discarding such a redraw does not close the image it shares an id with
        let g = redraws.reserve("a").0;
        redraws.finish("a", g, pending("a")).unwrap();
        assert!(discard(&images, &redraws, "a"));
        assert!(images.get("a").is_some());
        let g = redraws.reserve("a").0;
        redraws.finish("a", g, pending("a")).unwrap();
        assert!(close(&images, &redraws, "a"));
        assert!(images.is_empty());
    }

    #[test]
    fn discard_lets_the_waiting_redraw_go_and_leaves_the_image() {
        let (images, redraws) = waiting();
        assert!(discard(&images, &redraws, "a"));
        assert!(images.get("r").is_none());
        assert_eq!(images.bytes("a").unwrap().as_slice(), &[1]);
        assert!(!discard(&images, &redraws, "a"));
    }

    #[test]
    fn a_new_request_lets_the_waiting_redraw_go() {
        let (images, redraws) = waiting();
        let (_, displaced) = redraws.reserve("a");
        let_go(&images, "a", &displaced.unwrap());
        assert!(images.get("r").is_none());
        assert!(images.get("a").is_some());
    }
}
