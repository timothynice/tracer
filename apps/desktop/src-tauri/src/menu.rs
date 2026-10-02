//! The menu bar. Each item the UI acts on is sent to it as a `menu` event with the item's id; File ▸ Open…,
//! Open Recent, Settings and Help are handled here. The UI reports what is possible (`set_menu_state`) and the
//! items follow.
use crate::{opens, settings};
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::Mutex;
use tauri::menu::{AboutMetadata, CheckMenuItem, CheckMenuItemBuilder, Menu, MenuItem, MenuItemBuilder, PredefinedMenuItem, Submenu, SubmenuBuilder};
use tauri::{AppHandle, Emitter, Manager, Runtime};

pub struct Entry {
    pub id: &'static str,
    pub label: &'static str,
    pub accel: Option<&'static str>,
    pub check: bool,
}

const fn e(id: &'static str, label: &'static str, accel: Option<&'static str>) -> Entry {
    Entry { id, label, accel, check: false }
}
const fn c(id: &'static str, label: &'static str, accel: Option<&'static str>) -> Entry {
    Entry { id, label, accel, check: true }
}

/// Every item of ours (the predefined ones, About, Hide, Copy and so on, are added in `build`).
pub const MENU: &[Entry] = &[
    e("settings", "Settings…", Some("CmdOrCtrl+,")),
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
    c("mode-split", "Split", Some("CmdOrCtrl+1")),
    c("mode-side", "Side by Side", Some("CmdOrCtrl+2")),
    c("mode-overlay", "Overlay", Some("CmdOrCtrl+3")),
    c("mode-vector", "Vector Only", Some("CmdOrCtrl+4")),
    c("toggle-sidebar", "Show Sidebar", Some("Ctrl+Super+S")),
    c("toggle-inspector", "Show Inspector", Some("Alt+Super+I")),
    e("generate", "Generate Vector", Some("CmdOrCtrl+Enter")),
    e("cancel", "Cancel Trace", Some("CmdOrCtrl+.")),
    e("remove", "Remove Image", Some("CmdOrCtrl+Backspace")),
    e("clear", "Clear All", None),
    e("clear-recent", "Clear Menu", None),
    e("help", "Studi0Trace Help", None),
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
    pub tracing: bool,
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

/// The items `plan` touches, and the Open Recent submenu, kept to change later.
pub struct Handles<R: Runtime> {
    items: HashMap<&'static str, MenuItem<R>>,
    checks: HashMap<&'static str, CheckMenuItem<R>>,
    recent: Submenu<R>,
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
        .separator()
        .item(&item("settings")?)
        .separator()
        .services()
        .separator()
        .hide()
        .hide_others()
        .show_all()
        .separator()
        .quit()
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
        .fullscreen()
        .build()?;
    let image = SubmenuBuilder::new(app, "Image")
        .item(&item("generate")?)
        .item(&item("cancel")?)
        .separator()
        .item(&item("remove")?)
        .item(&item("clear")?)
        .build()?;
    let window = SubmenuBuilder::new(app, "Window").minimize().maximize().build()?;
    let help = SubmenuBuilder::new(app, "Help").item(&item("help")?).build()?;
    let menu = Menu::with_items(app, &[&app_menu, &file, &edit, &view, &image, &window, &help])?;
    let handles = Handles { items, checks, recent: recent_menu };
    fill_recent(app, &handles.recent, recent)?;
    Ok((menu, handles))
}

fn fill_recent<R: Runtime>(app: &AppHandle<R>, submenu: &Submenu<R>, recent: &[String]) -> tauri::Result<()> {
    while !submenu.items()?.is_empty() {
        submenu.remove_at(0)?;
    }
    for (i, path) in recent.iter().enumerate() {
        let label = std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| path.clone());
        submenu.append(&MenuItemBuilder::with_id(format!("recent-{i}"), label).build(app)?)?;
    }
    if !recent.is_empty() {
        submenu.append(&PredefinedMenuItem::separator(app)?)?;
    }
    submenu.append(&MenuItemBuilder::with_id("clear-recent", entry("clear-recent").label).enabled(!recent.is_empty()).build(app)?)?;
    Ok(())
}

pub fn set_recent<R: Runtime>(app: &AppHandle<R>, recent: &[String]) {
    if let Some(h) = app.try_state::<Mutex<Handles<R>>>() {
        let h = h.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        let _ = fill_recent(app, &h.recent, recent);
    }
}

pub fn apply_state<R: Runtime>(app: &AppHandle<R>, state: &MenuState) {
    let Some(h) = app.try_state::<Mutex<Handles<R>>>() else { return };
    let h = h.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
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
        "clear-recent" => settings::clear_recent(app),
        "settings" => {
            let _ = crate::commands::show_settings_window(app);
        }
        "help" => {
            use tauri_plugin_opener::OpenerExt;
            let _ = app.opener().open_url(HELP_URL, None::<&str>);
        }
        recent if recent.starts_with("recent-") => {
            let at: usize = recent["recent-".len()..].parse().unwrap_or(usize::MAX);
            if let Some(path) = settings::load(app).recent.get(at).cloned() {
                opens::deliver(app, vec![path]);
            }
        }
        other => {
            let _ = app.emit_to("main", "menu", serde_json::json!({ "id": other }));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

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
        let s = MenuState { has_items: true, has_image: true, has_vector: true, any_vector: true, has_path: true, tracing: false, mode: "side".into(), sidebar: true, inspector: false };
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
}
