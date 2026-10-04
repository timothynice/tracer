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
    state.images.remove(&id)
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
    let ext = if header(&request, "x-kind").as_deref() == Some("png") { "png" } else { "svg" };
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
        } else {
            "Export SVG"
        })
        .set_file_name(export::panel_name(&name, original.as_deref()))
        .add_filter(if ext == "png" { "PNG image" } else { "SVG image" }, &[ext]);
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
        .inner_size(520.0, 480.0)
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
