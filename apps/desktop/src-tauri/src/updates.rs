//! Updates, from the GitHub release's `latest.json` (`plugins.updater` in tauri.conf.json), signed with the app's
//! own minisign key. Driven from Rust only: Studi0Trace ▸ Check for Updates… (Manual) and a quiet check at launch
//! when the setting allows it (Automatic). The webview has no updater permission.
//!
//! Every answer is a native alert from tauri-plugin-dialog, shown without blocking the main thread, as a sheet on
//! the main window (brought up first, as `quit::ask` does: a sheet on a minimised window would hang unseen).
use std::sync::{Mutex, MutexGuard, PoisonError};
use tauri::{AppHandle, Manager, Runtime};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::{Update, UpdaterExt};

pub const INSTALL: &str = "Install and Relaunch";
pub const LATER: &str = "Later";
pub const UP_TO_DATE: &str = "Studi0Trace is up to date";
pub const CHECK_FAILED: &str = "Could not check for updates";
pub const INSTALL_FAILED: &str = "The update could not be installed.";
pub const RELAUNCH_TITLE: &str = "Relaunch Studi0Trace?";
pub const RELAUNCH: &str = "Relaunch";
/// The most of a release's notes the alert shows, in characters.
pub const NOTES_MAX: usize = 600;

/// Who asked: the menu item (every answer is shown) or the launch (only an update is).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum How {
    Manual,
    Automatic,
}

/// What a check found.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    Available,
    UpToDate,
    Error,
}

/// True when the answer is not shown: an automatic check says nothing unless there is an update.
pub fn quiet(how: How, outcome: Outcome) -> bool {
    how == How::Automatic && outcome != Outcome::Available
}

/// The alert's message for an update: the version running, then the release's notes, cut to `NOTES_MAX`
/// characters.
pub fn release_notes(current: &str, body: Option<&str>) -> String {
    let mut out = format!("You have {current}.");
    let body = body.map(str::trim).unwrap_or_default();
    if !body.is_empty() {
        out.push_str("\n\n");
        match body.char_indices().nth(NOTES_MAX) {
            Some((cut, _)) => {
                out.push_str(&body[..cut]);
                out.push('…');
            }
            None => out.push_str(body),
        }
    }
    out
}

/// The check running and how its answer is shown; None when none runs. It stays set while the answer is on
/// screen and while an update downloads, so a second request cannot stack a second alert or a second download.
static RUNNING: Mutex<Option<How>> = Mutex::new(None);

/// Starts a check unless one runs. A Manual request while an Automatic check runs is not lost: that check's
/// answer is shown.
fn begin(running: &mut Option<How>, how: How) -> bool {
    match running {
        Some(shown) => {
            if how == How::Manual {
                *shown = How::Manual;
            }
            false
        }
        None => {
            *running = Some(how);
            true
        }
    }
}

fn running() -> MutexGuard<'static, Option<How>> {
    RUNNING.lock().unwrap_or_else(PoisonError::into_inner)
}

/// The check is over (its answer dismissed, or quiet).
fn done() {
    *running() = None;
}

/// Check for an update; `how` decides what is shown.
pub fn check<R: Runtime>(app: &AppHandle<R>, how: How) {
    if !begin(&mut running(), how) {
        return;
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let found = match app.updater() {
            Ok(updater) => updater.check().await,
            Err(e) => Err(e),
        };
        // read now, not at the start: a Manual request may have joined this check while it ran
        let how = running().unwrap_or(How::Automatic);
        match found {
            Ok(Some(update)) => offer(&app, update),
            Ok(None) if !quiet(how, Outcome::UpToDate) => {
                let current = app.package_info().version.to_string();
                tell(&app, MessageDialogKind::Info, UP_TO_DATE, format!("You have the newest version, {current}."));
            }
            Err(e) => {
                eprintln!("studi0trace: could not check for updates: {e}");
                if quiet(how, Outcome::Error) {
                    done();
                } else {
                    tell(&app, MessageDialogKind::Error, CHECK_FAILED, e.to_string());
                }
            }
            Ok(None) => done(),
        }
    });
}

/// A sheet on the main window when there is one, the window brought up first.
fn alert<R: Runtime>(app: &AppHandle<R>, kind: MessageDialogKind, title: &str, message: String) -> tauri_plugin_dialog::MessageDialogBuilder<R> {
    let mut dialog = app.dialog().message(message).title(title).kind(kind);
    if let Some(main) = app.get_webview_window(crate::opens::MAIN) {
        crate::quit::bring_up(app, &main);
        dialog = dialog.parent(&main);
    }
    dialog
}

/// An answer with only OK; the check ends when it is dismissed.
fn tell<R: Runtime>(app: &AppHandle<R>, kind: MessageDialogKind, title: &str, message: String) {
    alert(app, kind, title, message).buttons(MessageDialogButtons::Ok).show(|_| done());
}

/// Offer the update: Install and Relaunch downloads and installs it, then relaunches; Later leaves it.
fn offer<R: Runtime>(app: &AppHandle<R>, update: Update) {
    let title = format!("Studi0Trace {} is available", update.version);
    let message = release_notes(&update.current_version, update.body.as_deref());
    let handle = app.clone();
    alert(app, MessageDialogKind::Info, &title, message).buttons(MessageDialogButtons::OkCancelCustom(INSTALL.into(), LATER.into())).show(move |install| {
        if !install {
            done();
            return;
        }
        tauri::async_runtime::spawn(async move {
            match update.download_and_install(|_, _| {}, || {}).await {
                Ok(()) => relaunch(&handle),
                Err(e) => {
                    eprintln!("studi0trace: the update could not be installed: {e}");
                    tell(&handle, MessageDialogKind::Error, INSTALL_FAILED, e.to_string());
                }
            }
        });
    });
}

/// The update is on disk: relaunch into it. A relaunch is a quit, so it asks first when work would be lost
/// (`quit::hold`); Later keeps the window as it is, and the new version opens next time.
fn relaunch<R: Runtime>(app: &AppHandle<R>) {
    let Some(lost) = crate::quit::hold(app) else {
        crate::quit::leaving();
        app.request_restart();
        return;
    };
    let message = format!("The update is installed and opens with Studi0Trace's next launch. {lost}");
    let handle = app.clone();
    alert(app, MessageDialogKind::Warning, RELAUNCH_TITLE, message).buttons(MessageDialogButtons::OkCancelCustom(RELAUNCH.into(), LATER.into())).show(move |now| {
        if now {
            crate::quit::leaving();
            handle.request_restart();
        } else {
            done();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn notes_say_the_version_running_and_the_release_body() {
        assert_eq!(release_notes("0.3.0", None), "You have 0.3.0.");
        assert_eq!(release_notes("0.3.0", Some("")), "You have 0.3.0.");
        assert_eq!(release_notes("0.3.0", Some("  \n ")), "You have 0.3.0.");
        assert_eq!(release_notes("0.3.0", Some("\nFaster traces.\n")), "You have 0.3.0.\n\nFaster traces.");
    }

    #[test]
    fn long_notes_are_cut_to_600_characters() {
        let notes = release_notes("0.3.0", Some(&"a".repeat(1000)));
        let body = notes.strip_prefix("You have 0.3.0.\n\n").unwrap();
        assert_eq!(body, format!("{}…", "a".repeat(NOTES_MAX)));
        assert_eq!(NOTES_MAX, 600);
        // exactly 600 is not cut
        let exact = "b".repeat(600);
        assert_eq!(release_notes("1", Some(&exact)), format!("You have 1.\n\n{exact}"));
    }

    #[test]
    fn notes_are_cut_between_characters_not_inside_one() {
        // 3 bytes each: a byte cut at 600 would land inside the 201st
        let notes = release_notes("0.3.0", Some(&"é漢".repeat(400)));
        let body = notes.strip_prefix("You have 0.3.0.\n\n").unwrap();
        assert_eq!(body.chars().count(), NOTES_MAX + 1);
        assert!(body.ends_with('…'));
        assert_eq!(body.trim_end_matches('…'), "é漢".repeat(300));
    }

    #[test]
    fn only_an_update_interrupts_a_launch() {
        assert!(quiet(How::Automatic, Outcome::UpToDate));
        assert!(quiet(How::Automatic, Outcome::Error));
        assert!(!quiet(How::Automatic, Outcome::Available));
        for outcome in [Outcome::Available, Outcome::UpToDate, Outcome::Error] {
            assert!(!quiet(How::Manual, outcome), "{outcome:?}");
        }
    }

    #[test]
    fn one_check_at_a_time_and_a_manual_request_is_answered() {
        let mut state = None;
        assert!(begin(&mut state, How::Automatic));
        assert_eq!(state, Some(How::Automatic));
        // a second launch check is dropped
        assert!(!begin(&mut state, How::Automatic));
        assert_eq!(state, Some(How::Automatic));
        // the menu while it runs joins it: its answer will be shown
        assert!(!begin(&mut state, How::Manual));
        assert_eq!(state, Some(How::Manual));
        assert!(!begin(&mut state, How::Automatic));
        assert_eq!(state, Some(How::Manual));
        let mut idle = None;
        assert!(begin(&mut idle, How::Manual));
        assert_eq!(idle, Some(How::Manual));
        assert_eq!((INSTALL, LATER, UP_TO_DATE, CHECK_FAILED), ("Install and Relaunch", "Later", "Studi0Trace is up to date", "Could not check for updates"));
    }
}
