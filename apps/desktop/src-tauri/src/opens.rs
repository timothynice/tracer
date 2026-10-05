//! Paths to open that arrive from outside the page: Finder (Open With, a drop on the Dock icon, which can come
//! before the page is listening), File ▸ Open…, Open Recent, and drops on the window. Until the UI first asks
//! (`take_pending_opens`), they wait; after that each batch is sent as `open-paths`.
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, Runtime};

#[derive(Debug, Default)]
pub struct Opens {
    ready: bool,
    pending: Vec<String>,
}

impl Opens {
    /// Some(paths) to send now, or None when they are kept for later.
    pub fn offer(&mut self, paths: Vec<String>) -> Option<Vec<String>> {
        if self.ready {
            Some(paths)
        } else {
            self.pending.extend(paths);
            None
        }
    }

    /// What arrived before the UI was listening; from now on paths are sent as they come.
    pub fn take(&mut self) -> Vec<String> {
        self.ready = true;
        std::mem::take(&mut self.pending)
    }
}

/// The window the UI that opens files lives in; drops on, and requests from, any other window are not its.
pub const MAIN: &str = "main";

impl Opens {
    /// `take` for a window: only the main window's first ask means the UI is listening.
    pub fn take_for(&mut self, window: &str) -> Vec<String> {
        if window == MAIN {
            self.take()
        } else {
            Vec::new()
        }
    }
}

/// What a drop or Finder hands over, with each folder replaced by the image files directly inside it (one level,
/// `IMAGE_EXTENSIONS`, no hidden files, sorted by name). Anything else is left for the open to judge.
pub fn expand(paths: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    for path in paths {
        let p = std::path::Path::new(path);
        if !p.is_dir() {
            out.push(path.clone());
            continue;
        }
        let Ok(entries) = std::fs::read_dir(p) else { continue };
        let mut images: Vec<(String, String)> = entries
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|f| f.is_file())
            .filter_map(|f| {
                let name = f.file_name()?.to_string_lossy().into_owned();
                let ext = f.extension()?.to_string_lossy().to_lowercase();
                (!name.starts_with('.') && crate::commands::IMAGE_EXTENSIONS.contains(&ext.as_str())).then(|| (name.to_lowercase(), f.display().to_string()))
            })
            .collect();
        images.sort();
        out.extend(images.into_iter().map(|(_, f)| f));
    }
    out
}

/// The failed open a drop with nothing in it becomes, in the shape of an `open_paths` failure.
pub fn nothing_to_open() -> serde_json::Value {
    let error = crate::error::CommandError::new(400, "nothing_to_open", "Nothing to open: drop image files or a folder of them.");
    serde_json::json!({ "name": "Dropped items", "path": null, "error": error })
}

/// Paths dropped on the main window: folders opened, and a drop that yields nothing reported as a failed open
/// (a drop from Safari carries no file paths at all).
pub fn deliver_drop<R: Runtime>(app: &AppHandle<R>, paths: &[String]) {
    let paths = expand(paths);
    if paths.is_empty() {
        let _ = app.emit_to(MAIN, "open-failures", [nothing_to_open()]);
        return;
    }
    deliver(app, paths);
}

pub fn deliver<R: Runtime>(app: &AppHandle<R>, paths: Vec<String>) {
    if paths.is_empty() {
        return;
    }
    let state = app.state::<Mutex<Opens>>();
    let now = state.lock().unwrap_or_else(std::sync::PoisonError::into_inner).offer(paths);
    if let Some(paths) = now {
        let _ = app.emit_to(MAIN, "open-paths", paths);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths_wait_until_the_ui_asks_for_them() {
        let mut o = Opens::default();
        assert_eq!(o.offer(vec!["/a.png".into()]), None);
        assert_eq!(o.offer(vec!["/b.png".into()]), None);
        assert_eq!(o.take(), vec!["/a.png".to_string(), "/b.png".to_string()]);
        assert_eq!(o.offer(vec!["/c.png".into()]), Some(vec!["/c.png".to_string()]));
        assert!(o.take().is_empty());
    }

    #[test]
    fn a_dropped_folder_opens_the_images_directly_inside_it() {
        let dir = std::env::temp_dir().join(format!("studi0trace-drop-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("inner")).unwrap();
        for name in ["b.PNG", "a.jpg", "notes.txt", ".hidden.png", "inner/c.png", "C.heic"] {
            std::fs::write(dir.join(name), b"x").unwrap();
        }
        let empty = dir.join("inner/empty");
        std::fs::create_dir_all(&empty).unwrap();
        let file = dir.join("notes.txt").display().to_string();
        let got = expand(&[dir.display().to_string(), file.clone()]);
        let want: Vec<String> = ["a.jpg", "b.PNG", "C.heic"].iter().map(|n| dir.join(n).display().to_string()).chain([file]).collect();
        assert_eq!(got, want);
        assert!(expand(&[empty.display().to_string()]).is_empty());
        // a path that is not there is left for the open to report
        assert_eq!(expand(&["/nowhere/x.png".into()]), ["/nowhere/x.png"]);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_drop_with_nothing_to_open_says_so_as_a_failed_open() {
        let f = nothing_to_open();
        assert_eq!(f["name"], "Dropped items");
        assert!(f["path"].is_null());
        assert_eq!(f["error"]["body"]["detail"]["code"], "nothing_to_open");
        assert_eq!(f["error"]["body"]["detail"]["message"], "Nothing to open: drop image files or a folder of them.");
    }

    #[test]
    fn only_the_main_window_can_ask_for_them() {
        let mut o = Opens::default();
        o.offer(vec!["/a.png".into()]);
        assert!(o.take_for("settings").is_empty());
        // still waiting: the settings window did not make the UI ready
        assert_eq!(o.offer(vec!["/b.png".into()]), None);
        assert_eq!(o.take_for(MAIN), vec!["/a.png".to_string(), "/b.png".to_string()]);
        assert_eq!(o.offer(vec!["/c.png".into()]), Some(vec!["/c.png".to_string()]));
    }
}
