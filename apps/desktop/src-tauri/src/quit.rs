//! Closing the main window and quitting ask first when work would be lost: a traced vector not exported or
//! copied since it was traced, or a trace still running (a slip from ⌘E to ⌘W must not throw away a two-minute
//! Auto). Every road out passes here: File ▸ Close Window and the close button (`CloseRequested` on the main
//! window), Studi0Trace ▸ Quit (our own item, not the predefined one), `RunEvent::ExitRequested`, and the Dock's
//! Quit, logout and an Apple Event `quit`, which AppKit sends to `applicationShouldTerminate:` and which tao does
//! not answer, so `install_terminate_hook` adds the answer to its delegate.
//!
//! The question is a native alert from tauri-plugin-dialog, shown without blocking the main thread; its answer
//! arrives on a thread of its own.
use crate::menu::{self, MenuState};
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{AppHandle, Manager, Runtime};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

pub const TITLE: &str = "Quit Studi0Trace?";
pub const QUIT: &str = "Quit";
pub const CANCEL: &str = "Cancel";

/// Set once leaving is decided (nothing to lose, or Quit was chosen): every later road out passes unasked.
static LEAVING: AtomicBool = AtomicBool::new(false);
/// The question is on screen: a second ⌘Q does not ask again.
static ASKING: AtomicBool = AtomicBool::new(false);

/// What leaving now would lose, as the alert's message; None when nothing would be.
pub fn prompt(s: &MenuState) -> Option<String> {
    let mut parts = Vec::new();
    match s.unexported {
        0 => {}
        1 => parts.push("1 traced image has not been exported.".to_string()),
        n => parts.push(format!("{n} traced images have not been exported.")),
    }
    if s.any_tracing {
        parts.push("A trace is still running.".to_string());
    }
    (!parts.is_empty()).then(|| parts.join(" "))
}

/// The message to ask with before leaving, or None to leave at once.
pub fn hold<R: Runtime>(app: &AppHandle<R>) -> Option<String> {
    if LEAVING.load(Ordering::SeqCst) {
        return None;
    }
    prompt(&menu::current(app))
}

/// Leaving is decided; nothing asks again.
pub fn leaving() {
    LEAVING.store(true, Ordering::SeqCst);
}

/// Quit: at once when nothing would be lost, else after the question.
pub fn request<R: Runtime>(app: &AppHandle<R>) {
    match hold(app) {
        Some(message) => ask(app, message),
        None => {
            leaving();
            app.exit(0);
        }
    }
}

/// Ask whether to quit; Quit leaves, Cancel stays. Shown as a sheet on the main window when there is one.
pub fn ask<R: Runtime>(app: &AppHandle<R>, message: String) {
    if ASKING.swap(true, Ordering::SeqCst) {
        return;
    }
    let mut dialog = app
        .dialog()
        .message(message)
        .title(TITLE)
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(QUIT.into(), CANCEL.into()));
    if let Some(main) = app.get_webview_window(crate::opens::MAIN) {
        dialog = dialog.parent(&main);
    }
    let app = app.clone();
    dialog.show(move |quit| {
        ASKING.store(false, Ordering::SeqCst);
        if quit {
            leaving();
            app.exit(0);
        }
    });
}

/// Answer AppKit's `applicationShouldTerminate:` on tao's app delegate: the Dock's Quit, logout and an Apple Event
/// `quit` reach it and never `RunEvent::ExitRequested`. With work to lose the answer is "cancel" and the question
/// is asked (Quit then leaves through `app.exit`, which does not pass here again); with nothing, "now", so a logout
/// is not held up.
#[cfg(target_os = "macos")]
pub fn install_terminate_hook(app: &AppHandle<tauri::Wry>) {
    use objc2::runtime::{AnyClass, AnyObject, Imp, Sel};
    use std::sync::OnceLock;

    static APP: OnceLock<AppHandle<tauri::Wry>> = OnceLock::new();
    const TERMINATE_CANCEL: usize = 0;
    const TERMINATE_NOW: usize = 1;

    extern "C-unwind" fn should_terminate(_this: &AnyObject, _cmd: Sel, _sender: *mut AnyObject) -> usize {
        let Some(app) = APP.get() else { return TERMINATE_NOW };
        match hold(app) {
            Some(message) => {
                ask(app, message);
                TERMINATE_CANCEL
            }
            None => {
                leaving();
                TERMINATE_NOW
            }
        }
    }

    let _ = APP.set(app.clone());
    let Some(class) = AnyClass::get(c"TaoAppDelegateParent") else { return };
    // SAFETY: the function has the selector's signature (NSApplicationTerminateReply, an NSUInteger, from self,
    // _cmd and the NSApplication), and "Q@:@" is its type encoding. Adding a method the class does not have
    // replaces nothing of tao's.
    unsafe {
        let imp: Imp = std::mem::transmute::<extern "C-unwind" fn(&AnyObject, Sel, *mut AnyObject) -> usize, Imp>(should_terminate);
        objc2::ffi::class_addMethod(class as *const AnyClass as *mut AnyClass, objc2::sel!(applicationShouldTerminate:), imp, c"Q@:@".as_ptr());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::menu::MenuState;

    #[test]
    fn nothing_to_lose_quits_at_once() {
        assert_eq!(prompt(&MenuState::default()), None);
        // the selected image's job alone is not the measure: any job is
        assert_eq!(prompt(&MenuState { tracing: true, ..MenuState::default() }), None);
    }

    #[test]
    fn unexported_vectors_and_a_running_trace_ask_first() {
        let one = MenuState { unexported: 1, ..MenuState::default() };
        assert_eq!(prompt(&one).as_deref(), Some("1 traced image has not been exported."));
        let three = MenuState { unexported: 3, ..MenuState::default() };
        assert_eq!(prompt(&three).as_deref(), Some("3 traced images have not been exported."));
        let busy = MenuState { any_tracing: true, ..MenuState::default() };
        assert_eq!(prompt(&busy).as_deref(), Some("A trace is still running."));
        let both = MenuState { unexported: 2, any_tracing: true, ..MenuState::default() };
        assert_eq!(prompt(&both).as_deref(), Some("2 traced images have not been exported. A trace is still running."));
        assert_eq!((TITLE, QUIT, CANCEL), ("Quit Studi0Trace?", "Quit", "Cancel"));
    }
}
