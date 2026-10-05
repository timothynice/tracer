//! The menu bar. Each item the UI acts on is sent to it as a `menu` event with the item's id; File ▸ Open…,
//! Open Recent, Check for Updates…, Settings, Help and Quit are handled here. The UI reports what is possible
//! (`set_menu_state`) and the items follow.
use crate::{opens, settings};
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::{Mutex, PoisonError};
use std::time::{Duration, Instant};
use tauri::menu::{AboutMetadata, CheckMenuItem, CheckMenuItemBuilder, Menu, MenuItem, MenuItemBuilder, PredefinedMenuItem, Submenu, SubmenuBuilder};
use tauri::{AppHandle, Emitter, Manager, Runtime};

pub struct Entry {
    pub id: &'static str,
    pub label: &'static str,
    pub accel: Option<&'static str>,
}

const fn e(id: &'static str, label: &'static str, accel: Option<&'static str>) -> Entry {
    Entry { id, label, accel }
}

/// Every item of ours (the predefined ones, About, Hide, Copy and so on, are added in `build`).
pub const MENU: &[Entry] = &[
    e("check-updates", "Check for Updates…", None),
    e("settings", "Settings…", Some("CmdOrCtrl+,")),
    e("quit", "Quit Studi0Trace", Some("CmdOrCtrl+Q")),
    e("open", "Open…", Some("CmdOrCtrl+O")),
    e("export-svg", "Export SVG…", Some("CmdOrCtrl+E")),
    e("export-png-1", "1×…", None),
    e("export-png-2", "2×…", Some("Shift+CmdOrCtrl+E")),
    e("export-png-4", "4×…", None),
    e("export-all", "Export All…", Some("Alt+CmdOrCtrl+E")),
    e("reveal", "Show in Finder", Some("Alt+CmdOrCtrl+R")),
    e("copy-svg", "Copy SVG", Some("Shift+CmdOrCtrl+C")),
    e("zoom-in", "Zoom In", Some("CmdOrCtrl+=")),
    e("zoom-out", "Zoom Out", Some("CmdOrCtrl+-")),
    e("zoom-actual", "Actual Size", Some("CmdOrCtrl+0")),
    e("zoom-fit", "Zoom to Fit", Some("CmdOrCtrl+9")),
    e("mode-split", "Split", Some("CmdOrCtrl+1")),
    e("mode-side", "Side by Side", Some("CmdOrCtrl+2")),
    e("mode-overlay", "Overlay", Some("CmdOrCtrl+3")),
    e("mode-vector", "Vector Only", Some("CmdOrCtrl+4")),
    e("toggle-sidebar", "Show Sidebar", Some("Ctrl+Super+S")),
    e("toggle-inspector", "Show Inspector", Some("Alt+Super+I")),
    e("generate", "Generate Vector", Some("CmdOrCtrl+Enter")),
    e("cancel", "Cancel Trace", Some("CmdOrCtrl+.")),
    e("remove", "Remove Image", Some("CmdOrCtrl+Backspace")),
    e("clear", "Clear All", None),
    e("clear-recent", "Clear Menu", None),
    e("help", "Studi0Trace Help", None),
    e("acknowledgements", "Acknowledgements", None),
];

const HELP_URL: &str = "https://github.com/timothynice/tracer#readme";

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct MenuState {
    pub has_items: bool,
    pub has_image: bool,
    pub has_vector: bool,
    pub any_vector: bool,
    pub has_path: bool,
    /// The selected image is tracing.
    pub tracing: bool,
    /// Any image is tracing.
    pub any_tracing: bool,
    /// Images whose vector on screen has not been exported or copied since it was traced.
    pub unexported: u32,
    pub mode: String,
    pub sidebar: bool,
    pub inspector: bool,
}

/// For each item the state governs: (id, enabled, checked).
pub fn plan(s: &MenuState) -> Vec<(&'static str, bool, Option<bool>)> {
    let mut out = vec![
        ("export-svg", s.has_vector, None),
        ("export-png-1", s.has_vector, None),
        ("export-png-2", s.has_vector, None),
        ("export-png-4", s.has_vector, None),
        ("copy-svg", s.has_vector, None),
        ("export-all", s.any_vector, None),
        ("reveal", s.has_path, None),
        ("generate", s.has_image && !s.tracing, None),
        ("cancel", s.tracing, None),
        ("remove", s.has_image, None),
        ("clear", s.has_items, None),
        ("zoom-in", s.has_image, None),
        ("zoom-out", s.has_image, None),
        ("zoom-actual", s.has_image, None),
        ("zoom-fit", s.has_image, None),
        ("toggle-sidebar", true, Some(s.sidebar)),
        ("toggle-inspector", true, Some(s.inspector)),
    ];
    for (id, mode) in [("mode-split", "split"), ("mode-side", "side"), ("mode-overlay", "overlay"), ("mode-vector", "vector")] {
        out.push((id, s.has_image, Some(s.mode == mode)));
    }
    out
}

/// The check marks `state` asks for: every View mode and both toggles. muda flips a check item on every click,
/// so choosing the mode already shown would uncheck it, and the UI, whose mode did not change, would not send a
/// state to check it again: after a click on one of them, these are put back.
pub fn checks(state: &MenuState) -> Vec<(&'static str, bool)> {
    plan(state).into_iter().filter_map(|(id, _, checked)| checked.map(|on| (id, on))).collect()
}

/// A repeat of a command that removes images, closer than this to the one before, is a held key, not a choice.
pub const DEBOUNCE: Duration = Duration::from_millis(400);
pub const DEBOUNCED: &[&str] = &["remove", "clear"];

/// True when a command arriving `now`, after the last of its kind at `last`, is a key repeat to drop.
pub fn debounce(last: Option<Instant>, now: Instant) -> bool {
    last.is_some_and(|l| now.saturating_duration_since(l) < DEBOUNCE)
}

/// The commands that act on the image in the main window, which must not reach it while Settings is the key
/// window: everything the UI is sent, View's sidebar and inspector toggles included. Not Open, Settings, Help,
/// Quit, Open Recent and the app's own windows.
pub fn acts_on_document(id: &str) -> bool {
    !matches!(id, "open" | "settings" | "help" | "quit" | "check-updates" | "acknowledgements" | "clear-recent") && !id.starts_with("recent:")
}

/// Open Recent's labels: the file's name, and its folder's after a dash where two files share a name.
pub fn recent_labels(paths: &[String]) -> Vec<String> {
    let name = |p: &str| std::path::Path::new(p).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| p.to_string());
    let names: Vec<String> = paths.iter().map(|p| name(p)).collect();
    paths
        .iter()
        .zip(&names)
        .map(|(path, n)| {
            if names.iter().filter(|m| *m == n).count() < 2 {
                return n.clone();
            }
            let folder = std::path::Path::new(path).parent().and_then(|d| d.file_name()).map(|d| d.to_string_lossy().into_owned());
            folder.map_or_else(|| n.clone(), |f| format!("{n} — {f}"))
        })
        .collect()
}

/// The items `plan` touches, and the Open Recent submenu, kept to change later. The mutexes are held only to read
/// or replace what they hold, never across a hop to the main thread.
pub struct Handles<R: Runtime> {
    items: HashMap<&'static str, MenuItem<R>>,
    checks: HashMap<&'static str, CheckMenuItem<R>>,
    recent: Submenu<R>,
    /// The paths Open Recent was last built from: `recent:<i>` is the i-th of these.
    recent_paths: Mutex<Vec<String>>,
    /// The state last applied, to put the check marks back after a click, and to know what quitting would lose.
    shown: Mutex<MenuState>,
    /// When each debounced command last arrived.
    last: Mutex<HashMap<String, Instant>>,
}

/// The state the UI last reported.
pub fn current<R: Runtime>(app: &AppHandle<R>) -> MenuState {
    app.try_state::<Handles<R>>().map(|h| h.shown.lock().unwrap_or_else(PoisonError::into_inner).clone()).unwrap_or_default()
}

fn entry(id: &str) -> &'static Entry {
    MENU.iter().find(|x| x.id == id).expect("a known menu id")
}

pub fn build<R: Runtime>(app: &AppHandle<R>, recent: &[String]) -> tauri::Result<(Menu<R>, Handles<R>)> {
    let mut items = HashMap::new();
    let mut checks = HashMap::new();
    let mut item = |id: &'static str| -> tauri::Result<MenuItem<R>> {
        let x = entry(id);
        let mut b = MenuItemBuilder::with_id(x.id, x.label);
        if let Some(a) = x.accel {
            b = b.accelerator(a);
        }
        let built = b.build(app)?;
        items.insert(x.id, built.clone());
        Ok(built)
    };
    let mut check = |id: &'static str| -> tauri::Result<CheckMenuItem<R>> {
        let x = entry(id);
        let mut b = CheckMenuItemBuilder::with_id(x.id, x.label).checked(false);
        if let Some(a) = x.accel {
            b = b.accelerator(a);
        }
        let built = b.build(app)?;
        checks.insert(x.id, built.clone());
        Ok(built)
    };

    let about = AboutMetadata {
        name: Some("Studi0Trace".into()),
        version: Some(env!("CARGO_PKG_VERSION").into()),
        copyright: Some("Copyright © 2026 Timothy Nice. MIT licensed.".into()),
        ..Default::default()
    };
    let app_menu = SubmenuBuilder::new(app, "Studi0Trace")
        .about(Some(about))
        .item(&item("check-updates")?)
        .separator()
        .item(&item("settings")?)
        .separator()
        .services()
        .separator()
        .hide()
        .hide_others()
        .show_all()
        .separator()
        .item(&item("quit")?)
        .build()?;
    let recent_menu = SubmenuBuilder::with_id(app, "open-recent", "Open Recent").build()?;
    let png = SubmenuBuilder::new(app, "Export PNG").item(&item("export-png-1")?).item(&item("export-png-2")?).item(&item("export-png-4")?).build()?;
    let file = SubmenuBuilder::new(app, "File")
        .item(&item("open")?)
        .item(&recent_menu)
        .separator()
        .item(&item("export-svg")?)
        .item(&png)
        .item(&item("export-all")?)
        .separator()
        .item(&item("reveal")?)
        .separator()
        .close_window()
        .build()?;
    let edit = SubmenuBuilder::new(app, "Edit").undo().redo().separator().cut().copy().paste().select_all().separator().item(&item("copy-svg")?).build()?;
    let view = SubmenuBuilder::new(app, "View")
        .item(&item("zoom-in")?)
        .item(&item("zoom-out")?)
        .item(&item("zoom-actual")?)
        .item(&item("zoom-fit")?)
        .separator()
        .item(&check("mode-split")?)
        .item(&check("mode-side")?)
        .item(&check("mode-overlay")?)
        .item(&check("mode-vector")?)
        .separator()
        .item(&check("toggle-sidebar")?)
        .item(&check("toggle-inspector")?)
        .separator()
        .item(&PredefinedMenuItem::fullscreen(app, Some("Enter Full Screen"))?)
        .build()?;
    let image = SubmenuBuilder::new(app, "Image")
        .item(&item("generate")?)
        .item(&item("cancel")?)
        .separator()
        .item(&item("remove")?)
        .item(&item("clear")?)
        .build()?;
    let window = SubmenuBuilder::new(app, "Window").minimize().maximize().build()?;
    let help = SubmenuBuilder::new(app, "Help").item(&item("help")?).item(&item("acknowledgements")?).build()?;
    let menu = Menu::with_items(app, &[&app_menu, &file, &edit, &view, &image, &window, &help])?;
    fill_recent(app, &recent_menu, recent)?;
    let handles = Handles { items, checks, recent: recent_menu, recent_paths: Mutex::new(recent.to_vec()), shown: Mutex::new(MenuState::default()), last: Mutex::default() };
    Ok((menu, handles))
}

/// Fills Open Recent with `recent`, which the caller keeps as `Handles::recent_paths`.
fn fill_recent<R: Runtime>(app: &AppHandle<R>, submenu: &Submenu<R>, recent: &[String]) -> tauri::Result<()> {
    while !submenu.items()?.is_empty() {
        submenu.remove_at(0)?;
    }
    for (i, label) in recent_labels(recent).into_iter().enumerate() {
        submenu.append(&MenuItemBuilder::with_id(format!("recent:{i}"), label).build(app)?)?;
    }
    if !recent.is_empty() {
        submenu.append(&PredefinedMenuItem::separator(app)?)?;
    }
    submenu.append(&MenuItemBuilder::with_id("clear-recent", entry("clear-recent").label).enabled(!recent.is_empty()).build(app)?)?;
    Ok(())
}

/// Rebuilds Open Recent. The submenu is rebuilt on the main thread, in the order the requests came, so two
/// callers cannot interleave their removes and appends; `Handles` is never locked (a lock held across a hop to
/// the main thread deadlocks against a main-thread command waiting for the same lock).
pub fn set_recent<R: Runtime>(app: &AppHandle<R>, recent: &[String]) {
    let Some(h) = app.try_state::<Handles<R>>() else { return };
    let submenu = h.recent.clone();
    let recent = recent.to_vec();
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        let _ = fill_recent(&handle, &submenu, &recent);
        if let Some(h) = handle.try_state::<Handles<R>>() {
            *h.recent_paths.lock().unwrap_or_else(PoisonError::into_inner) = recent;
        }
    });
}

pub fn apply_state<R: Runtime>(app: &AppHandle<R>, state: &MenuState) {
    let Some(h) = app.try_state::<Handles<R>>() else { return };
    *h.shown.lock().unwrap_or_else(PoisonError::into_inner) = state.clone();
    for (id, enabled, checked) in plan(state) {
        if let Some(i) = h.items.get(id) {
            let _ = i.set_enabled(enabled);
        }
        if let Some(c) = h.checks.get(id) {
            let _ = c.set_enabled(enabled);
            if let Some(on) = checked {
                let _ = c.set_checked(on);
            }
        }
    }
}

/// A menu item was chosen.
pub fn on_menu<R: Runtime>(app: &AppHandle<R>, id: &str) {
    match id {
        "open" => {
            let app = app.clone();
            tauri::async_runtime::spawn_blocking(move || {
                use tauri_plugin_dialog::DialogExt;
                let picked = app.dialog().file().set_title("Open Images").add_filter("Images", crate::commands::IMAGE_EXTENSIONS).blocking_pick_files().unwrap_or_default();
                let paths = picked.into_iter().filter_map(|f| f.into_path().ok()).map(|p| p.display().to_string()).collect();
                opens::deliver(&app, paths);
            });
        }
        "quit" => crate::quit::request(app),
        "check-updates" => crate::updates::check(app, crate::updates::How::Manual),
        "clear-recent" => settings::clear_recent(app),
        "settings" => {
            let _ = crate::commands::show_settings_window(app);
        }
        "help" => {
            use tauri_plugin_opener::OpenerExt;
            let _ = app.opener().open_url(HELP_URL, None::<&str>);
        }
        "acknowledgements" => show_acknowledgements(app),
        recent if recent.starts_with("recent:") => open_recent(app, &recent["recent:".len()..]),
        other => {
            if !held_back(app, other) {
                let _ = app.emit_to("main", "menu", serde_json::json!({ "id": other }));
            }
            restore_checks(app, other);
        }
    }
}

/// The file of third-party notices the bundle carries (`bundle.resources`), under Resources.
pub const NOTICES_FILE: &str = "THIRD_PARTY_NOTICES.html";

/// Help ▸ Acknowledgements: the notices open in the default browser; a failure is a native alert, shown without
/// blocking the main thread.
fn show_acknowledgements<R: Runtime>(app: &AppHandle<R>) {
    use tauri::path::BaseDirectory;
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    use tauri_plugin_opener::OpenerExt;
    let opened = app.path().resolve(NOTICES_FILE, BaseDirectory::Resource).map_err(|e| e.to_string()).and_then(|path| app.opener().open_path(path.to_string_lossy(), None::<&str>).map_err(|e| e.to_string()));
    if let Err(why) = opened {
        app.dialog().message(format!("The third-party notices could not be opened. {why}")).title("Could not open Acknowledgements").kind(MessageDialogKind::Error).buttons(MessageDialogButtons::Ok).show(|_| {});
    }
}

/// The i-th file of the list Open Recent was built from. Nothing here asks the disk whether it is still there (a
/// stale network mount would stall the main thread): it is opened, and a failed open takes it off the list
/// (`commands::open_one`).
fn open_recent<R: Runtime>(app: &AppHandle<R>, at: &str) {
    let Some(h) = app.try_state::<Handles<R>>() else { return };
    let path = at.parse::<usize>().ok().and_then(|i| h.recent_paths.lock().unwrap_or_else(PoisonError::into_inner).get(i).cloned());
    if let Some(path) = path {
        opens::deliver(app, vec![path]);
    }
}

/// A command the main window must not get: one for the image while Settings is the key window, or a held key's
/// repeat of Remove Image or Clear All.
fn held_back<R: Runtime>(app: &AppHandle<R>, id: &str) -> bool {
    let repeat = DEBOUNCED.contains(&id)
        && app.try_state::<Handles<R>>().is_some_and(|h| {
            let now = Instant::now();
            let last = h.last.lock().unwrap_or_else(PoisonError::into_inner).insert(id.to_string(), now);
            debounce(last, now)
        });
    let settings_key = app.get_webview_window("settings").is_some_and(|w| w.is_focused().unwrap_or(false));
    repeat || (settings_key && acts_on_document(id))
}

/// After a click on a check item, its marks are the last state's again; a change the UI makes arrives as a new
/// state and is applied over them.
fn restore_checks<R: Runtime>(app: &AppHandle<R>, clicked: &str) {
    let Some(h) = app.try_state::<Handles<R>>() else { return };
    if !h.checks.contains_key(clicked) {
        return;
    }
    let shown = h.shown.lock().unwrap_or_else(PoisonError::into_inner).clone();
    for (id, on) in checks(&shown) {
        if let Some(c) = h.checks.get(id) {
            let _ = c.set_checked(on);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;
    use std::time::Duration;

    #[test]
    fn every_id_is_unique_and_every_accelerator_parses() {
        let mut seen = std::collections::HashSet::new();
        for e in MENU {
            assert!(seen.insert(e.id), "{} twice", e.id);
            if let Some(a) = e.accel {
                assert!(muda::accelerator::Accelerator::from_str(a).is_ok(), "{}: {a}", e.id);
            }
        }
    }

    #[test]
    fn the_state_decides_what_is_enabled_and_checked() {
        let none = plan(&MenuState::default());
        let get = |p: &[(&'static str, bool, Option<bool>)], id: &str| *p.iter().find(|x| x.0 == id).unwrap();
        assert_eq!(get(&none, "export-svg"), ("export-svg", false, None));
        assert_eq!(get(&none, "generate"), ("generate", false, None));
        let s = MenuState { has_items: true, has_image: true, has_vector: true, any_vector: true, has_path: true, mode: "side".into(), sidebar: true, ..MenuState::default() };
        let p = plan(&s);
        assert!(get(&p, "export-svg").1);
        assert!(get(&p, "reveal").1);
        assert!(get(&p, "generate").1);
        assert!(!get(&p, "cancel").1);
        assert_eq!(get(&p, "mode-side"), ("mode-side", true, Some(true)));
        assert_eq!(get(&p, "mode-split").2, Some(false));
        assert_eq!(get(&p, "toggle-sidebar").2, Some(true));
        assert_eq!(get(&p, "toggle-inspector").2, Some(false));
        let tracing = plan(&MenuState { tracing: true, ..s });
        assert_eq!((get(&tracing, "generate").1, get(&tracing, "cancel").1), (false, true));
    }

    #[test]
    fn acknowledgements_is_a_help_item_the_rust_side_handles() {
        let a = entry("acknowledgements");
        assert_eq!((a.label, a.accel), ("Acknowledgements", None));
        assert_eq!(NOTICES_FILE, "THIRD_PARTY_NOTICES.html");
    }

    #[test]
    fn check_for_updates_is_ours_and_asks_for_more() {
        let c = entry("check-updates");
        assert_eq!((c.label, c.accel), ("Check for Updates…", None));
    }

    #[test]
    fn quit_is_ours_and_reads_what_the_ui_reports() {
        let q = entry("quit");
        assert_eq!((q.label, q.accel), ("Quit Studi0Trace", Some("CmdOrCtrl+Q")));
        let s: MenuState = serde_json::from_value(serde_json::json!({"unexported": 2, "anyTracing": true, "tracing": false})).unwrap();
        assert_eq!((s.unexported, s.any_tracing, s.tracing), (2, true, false));
        assert_eq!(MenuState::default().unexported, 0);
    }

    #[test]
    fn a_held_shortcut_repeats_inside_the_window_and_is_dropped() {
        let t = Instant::now();
        assert!(!debounce(None, t));
        assert!(debounce(Some(t), t + Duration::from_millis(30)));
        assert!(debounce(Some(t), t + Duration::from_millis(399)));
        assert!(!debounce(Some(t), t + DEBOUNCE));
        assert!(!debounce(Some(t), t + Duration::from_secs(2)));
        assert_eq!(DEBOUNCE, Duration::from_millis(400));
        assert!(DEBOUNCED.contains(&"remove") && DEBOUNCED.contains(&"clear"));
    }

    #[test]
    fn only_the_commands_that_act_on_the_image_wait_for_the_main_window() {
        for id in ["remove", "clear", "generate", "cancel", "export-svg", "export-png-2", "export-all", "copy-svg", "reveal", "zoom-in", "zoom-fit", "mode-split", "mode-vector", "toggle-sidebar", "toggle-inspector"] {
            assert!(acts_on_document(id), "{id}");
        }
        for id in ["open", "settings", "help", "recent:0", "recent:9", "quit", "check-updates", "acknowledgements", "clear-recent"] {
            assert!(!acts_on_document(id), "{id}");
        }
    }

    #[test]
    fn two_recent_files_of_one_name_are_told_apart_by_their_folder() {
        let paths = ["/Users/t/Desktop/logo.png", "/Users/t/Work/logo.png", "/Users/t/Work/mark.png"].map(String::from);
        assert_eq!(recent_labels(&paths), ["logo.png — Desktop", "logo.png — Work", "mark.png"]);
        assert!(recent_labels(&[]).is_empty());
    }

    #[test]
    fn a_click_on_the_mode_shown_leaves_it_checked() {
        let s = MenuState { has_image: true, mode: "split".into(), sidebar: true, ..MenuState::default() };
        let marks: HashMap<_, _> = checks(&s).into_iter().collect();
        // every check item in the View menu gets a mark back, and only the mode shown is on
        let ids: std::collections::BTreeSet<_> = marks.keys().copied().collect();
        assert_eq!(ids, ["mode-overlay", "mode-side", "mode-split", "mode-vector", "toggle-inspector", "toggle-sidebar"].into_iter().collect());
        assert_eq!((marks["mode-split"], marks["mode-side"], marks["mode-overlay"], marks["mode-vector"]), (true, false, false, false));
        assert_eq!((marks["toggle-sidebar"], marks["toggle-inspector"]), (true, false));
    }
}
