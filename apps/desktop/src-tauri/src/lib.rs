//! Studi0Trace for Mac: the Tauri shell around `studi0trace_core::api::Core`.
pub mod commands;
pub mod error;
pub mod export;
pub mod intake;
pub mod keychain;
pub mod menu;
pub mod opens;
pub mod queue;
pub mod quit;
pub mod redraw;
pub mod settings;
pub mod store;
pub mod updates;
pub mod worker;

use studi0trace_core::api::Core;

/// What every command shares.
pub struct AppState {
    pub core: Core,
    pub images: store::Images,
    pub queue: queue::TraceQueue,
    /// AI redraws in flight and awaiting a decision (redraw/mod.rs).
    pub redraws: redraw::Redraws,
}

pub fn run() {
    tauri::Builder::default()
        // Settings is a fixed-size window of its own: a remembered size (it was 520x400 once) would override the one
        // the code gives it, and the sheet would scroll again.
        .plugin(tauri_plugin_window_state::Builder::default().with_denylist(&["settings"]).build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_opener::init())
        // driven from Rust only (updates.rs): the webview has no updater permission
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(std::sync::Mutex::new(opens::Opens::default()))
        // The parent's core validates what is opened and answers engines and presets; the worker traces with a
        // core of its own, from the bytes in `images`. A cache of 0 bytes keeps only the last image decoded
        // (the core keeps the newest alone when nothing fits), not 256 MiB of pixels nobody traces here.
        .manage(AppState { core: Core::with_limits(studi0trace_core::intake::Limits::default(), 0), images: store::Images::default(),
            queue: queue::TraceQueue::new(std::env::current_exe().expect("the app knows where it is")),
            redraws: redraw::Redraws::default(),
        })
        .invoke_handler(tauri::generate_handler![
            commands::engines,
            commands::presets,
            commands::open_paths,
            commands::pick_images,
            commands::open_bytes,
            commands::read_image,
            commands::close_image,
            commands::vectorize,
            commands::cancel_trace,
            commands::export_file,
            commands::export_all,
            commands::reveal,
            commands::copy_text,
            commands::load_settings,
            commands::save_settings,
            commands::set_menu_state,
            commands::confirm_clear,
            commands::take_pending_opens,
            commands::open_settings_window,
            commands::redraw_key_status,
            commands::set_redraw_key,
            commands::delete_redraw_key,
            commands::image_roughness,
            commands::redraw_image,
            commands::cancel_redraw,
            commands::accept_redraw,
            commands::discard_redraw,
            commands::revert_redraw,
        ])
        .setup(|app| {
            use tauri::Manager;
            let (menu, handles) = menu::build(app.handle(), &settings::load(app.handle()).recent)?;
            app.set_menu(menu)?;
            app.manage(handles);
            // the windows follow the Appearance setting, not only the page (vibrancy, title bar, traffic lights)
            settings::apply_theme(app.handle(), &settings::load(app.handle()).appearance);
            // nothing is possible until the UI says so
            menu::apply_state(app.handle(), &menu::MenuState::default());
            #[cfg(target_os = "macos")]
            quit::install_terminate_hook(app.handle());
            // the windows of tauri.conf.json exist by now; the check runs on the async runtime (`updates::check`
            // spawns it) and says nothing unless there is an update
            if settings::load(app.handle()).check_for_updates {
                updates::check(app.handle(), updates::How::Automatic);
            }
            Ok(())
        })
        .on_menu_event(|app, event| menu::on_menu(app, event.id().as_ref()))
        .on_window_event(|window, event| {
            if window.label() != opens::MAIN {
                return;
            }
            use tauri::Manager;
            // closing the main window quits (below), so it asks first when that would lose work
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if let Some(message) = quit::hold(window.app_handle()) {
                    api.prevent_close();
                    quit::ask(window.app_handle(), message);
                }
                return;
            }
            // the app is its main window: with it gone (Settings may still be open), Finder, the Dock, ⌘O and
            // Open Recent would deliver into nothing, so the app quits with it
            if let tauri::WindowEvent::Destroyed = event {
                quit::leaving();
                window.app_handle().exit(0);
                return;
            }
            if let tauri::WindowEvent::DragDrop(drop) = event {
                use tauri::{DragDropEvent, Emitter};
                match drop {
                    DragDropEvent::Enter { .. } => {
                        let _ = window.emit("drag-state", true);
                    }
                    DragDropEvent::Leave => {
                        let _ = window.emit("drag-state", false);
                    }
                    DragDropEvent::Drop { paths, .. } => {
                        let _ = window.emit("drag-state", false);
                        opens::deliver_drop(window.app_handle(), &paths.iter().map(|p| p.display().to_string()).collect::<Vec<_>>());
                    }
                    _ => {}
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("Studi0Trace failed to start")
        .run(|app, event| match event {
            #[cfg(target_os = "macos")]
            tauri::RunEvent::Opened { urls } => {
                let paths: Vec<String> = urls.into_iter().filter_map(|u| u.to_file_path().ok()).map(|p| p.display().to_string()).collect();
                opens::deliver(app, opens::expand(&paths));
            }
            // any other way out (the main window gone, `app.exit`) asks first too; the Dock's Quit and logout do not
            // come this way but through `applicationShouldTerminate:` (quit.rs)
            tauri::RunEvent::ExitRequested { api, .. } => {
                if let Some(message) = quit::hold(app) {
                    api.prevent_exit();
                    quit::ask(app, message);
                }
            }
            tauri::RunEvent::Exit => {
                use tauri::Manager;
                app.state::<AppState>().queue.shutdown();
            }
            _ => {}
        });
}
