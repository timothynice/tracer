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

pub(crate) fn open_one(state: &AppState, path: PathBuf, downscale: bool) -> Outcome {
    match intake::open_path(&state.core, &path, downscale) {
        Ok(image) => Outcome::Ok(state.images.insert(image)),
        Err(error) => Outcome::Failed {
            name: intake::file_name(&path),
            path: Some(path.display().to_string()),
            error,
        },
    }
}

#[tauri::command]
pub fn health(state: State<'_, AppState>) -> Value {
    state.core.health()
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
pub async fn open_paths(state: State<'_, AppState>, paths: Vec<String>, downscale: bool) -> Result<Vec<Outcome>, CommandError> {
    Ok(paths.into_iter().map(|p| open_one(&state, PathBuf::from(p), downscale)).collect())
}

#[tauri::command]
pub async fn pick_images(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<Vec<Outcome>, CommandError> {
    let picked = app.dialog().file().set_title("Open Images").add_filter("Images", IMAGE_EXTENSIONS).blocking_pick_files().unwrap_or_default();
    Ok(picked.into_iter().filter_map(|f| f.into_path().ok()).map(|p| open_one(&state, p, false)).collect())
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
