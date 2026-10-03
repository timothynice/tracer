//! The app's settings, in `settings.json` (tauri-plugin-store, in the app's data folder). Every save is sent to
//! every window as `settings-changed`. The recent-files list is kept here too, but only opening files and Clear
//! Menu change it: the UI cannot hand back a list of its own.
use crate::error::CommandError;
use serde::{Deserialize, Serialize};
use std::sync::{Mutex, PoisonError};
use tauri::{AppHandle, Emitter, Runtime};
use tauri_plugin_store::StoreExt;

pub const MAX_RECENT: usize = 10;
const FILE: &str = "settings.json";
const KEY: &str = "settings";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct Settings {
    pub appearance: String,
    pub export_to: String,
    pub reveal_after_export: bool,
    pub trace_on_open: bool,
    pub live_update: bool,
    pub recent: Vec<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Settings { appearance: "system".into(), export_to: "ask".into(), reveal_after_export: false, trace_on_open: false, live_update: true, recent: Vec::new() }
    }
}

impl Settings {
    pub fn with_recent(mut self, path: &str) -> Settings {
        self.recent.retain(|p| p != path);
        self.recent.insert(0, path.to_string());
        self.recent.truncate(MAX_RECENT);
        self
    }

    /// The UI's settings, with the recent list kept as it is here.
    pub fn merged(self, from_ui: Settings) -> Settings {
        Settings { recent: self.recent, ..from_ui }
    }
}

pub fn load<R: Runtime>(app: &AppHandle<R>) -> Settings {
    app.store(FILE).ok().and_then(|s| s.get(KEY)).and_then(|v| serde_json::from_value(v).ok()).unwrap_or_default()
}

/// Held around every load → change → save, so two writers (a save from the UI, a file opened in a batch, Clear
/// Menu) cannot read the same file and save over each other's change, or persist out of order. Nothing that
/// holds it waits on the main thread: `save` hands the Open Recent rebuild to it without waiting.
static WRITE: Mutex<()> = Mutex::new(());

/// Reads the settings, changes them with `change` and saves the result, as one step.
pub fn update<R: Runtime>(app: &AppHandle<R>, change: impl FnOnce(Settings) -> Settings) -> Result<Settings, CommandError> {
    let _held = WRITE.lock().unwrap_or_else(PoisonError::into_inner);
    let next = change(load(app));
    save(app, &next)?;
    Ok(next)
}

fn save<R: Runtime>(app: &AppHandle<R>, settings: &Settings) -> Result<(), CommandError> {
    let store = app.store(FILE).map_err(|e| CommandError::new(500, "io_error", e.to_string()))?;
    store.set(KEY, serde_json::to_value(settings).expect("settings are JSON"));
    store.save().map_err(|e| CommandError::new(500, "io_error", e.to_string()))?;
    let _ = app.emit("settings-changed", settings);
    crate::menu::set_recent(app, &settings.recent);
    Ok(())
}

pub fn note_recent<R: Runtime>(app: &AppHandle<R>, path: &str) {
    let _ = update(app, |s| s.with_recent(path));
}

pub fn clear_recent<R: Runtime>(app: &AppHandle<R>) {
    let _ = update(app, |s| Settings { recent: Vec::new(), ..s });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_and_camel_case() {
        let v = serde_json::to_value(Settings::default()).unwrap();
        assert_eq!(v, serde_json::json!({"appearance": "system", "exportTo": "ask", "revealAfterExport": false, "traceOnOpen": false, "liveUpdate": true, "recent": []}));
        // a file from an older version, missing keys, still reads
        let old: Settings = serde_json::from_value(serde_json::json!({"appearance": "dark"})).unwrap();
        assert_eq!((old.appearance.as_str(), old.live_update), ("dark", true));
    }

    #[test]
    fn recent_files_come_first_once_and_ten_at_most() {
        let mut s = Settings::default();
        for i in 0..12 {
            s = s.with_recent(&format!("/p/{i}.png"));
        }
        assert_eq!(s.recent.len(), MAX_RECENT);
        assert_eq!(s.recent[0], "/p/11.png");
        s = s.with_recent("/p/5.png");
        assert_eq!(s.recent[0], "/p/5.png");
        assert_eq!(s.recent.iter().filter(|p| *p == "/p/5.png").count(), 1);
    }

    #[test]
    fn the_ui_cannot_rewrite_the_recent_list() {
        let stored = Settings::default().with_recent("/p/a.png");
        let from_ui = Settings { appearance: "light".into(), recent: vec!["/etc/passwd".into()], ..Settings::default() };
        let merged = stored.merged(from_ui);
        assert_eq!((merged.appearance.as_str(), merged.recent), ("light", vec!["/p/a.png".to_string()]));
    }
}
