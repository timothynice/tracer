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
