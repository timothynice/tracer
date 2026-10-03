//! Studi0Trace for Mac: the Tauri shell around `studi0trace_core::api::Core`.
pub mod commands;
pub mod error;
pub mod export;
pub mod intake;
pub mod menu;
pub mod opens;
pub mod queue;
pub mod settings;
pub mod store;
pub mod worker;

use studi0trace_core::api::Core;

/// What every command shares.
pub struct AppState {
    pub core: Core,
    pub images: store::Images,
    pub queue: queue::TraceQueue,
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_opener::init())
        .manage(std::sync::Mutex::new(opens::Opens::default()))
        // The parent's core validates what is opened and answers engines and presets; the worker traces with a
        // core of its own, from the bytes in `images`. A cache of 0 bytes keeps only the last image decoded
        // (the core keeps the newest alone when nothing fits), not 256 MiB of pixels nobody traces here.
        .manage(AppState { core: Core::with_limits(studi0trace_core::intake::Limits::default(), 0), images: store::Images::default(),
            queue: queue::TraceQueue::new(std::env::current_exe().expect("the app knows where it is")),
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
            commands::take_pending_opens,
            commands::open_settings_window,
        ])
        .setup(|app| {
            use tauri::Manager;
            let (menu, handles) = menu::build(app.handle(), &settings::load(app.handle()).recent)?;
            app.set_menu(menu)?;
            app.manage(handles);
            // nothing is possible until the UI says so
            menu::apply_state(app.handle(), &menu::MenuState::default());
            Ok(())
        })
        .on_menu_event(|app, event| menu::on_menu(app, event.id().as_ref()))
        .on_window_event(|window, event| {
            if window.label() != opens::MAIN {
                return;
            }
            // the app is its main window: with it gone (Settings may still be open), Finder, the Dock, ⌘O and
            // Open Recent would deliver into nothing, so the app quits with it
            if let tauri::WindowEvent::Destroyed = event {
                use tauri::Manager;
                window.app_handle().exit(0);
                return;
            }
            if let tauri::WindowEvent::DragDrop(drop) = event {
                use tauri::{DragDropEvent, Emitter, Manager};
                match drop {
                    DragDropEvent::Enter { .. } => {
                        let _ = window.emit("drag-state", true);
                    }
                    DragDropEvent::Leave => {
                        let _ = window.emit("drag-state", false);
                    }
                    DragDropEvent::Drop { paths, .. } => {
                        let _ = window.emit("drag-state", false);
                        opens::deliver(window.app_handle(), paths.iter().map(|p| p.display().to_string()).collect());
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
                opens::deliver(app, urls.into_iter().filter_map(|u| u.to_file_path().ok()).map(|p| p.display().to_string()).collect());
            }
            tauri::RunEvent::Exit => {
                use tauri::Manager;
                app.state::<AppState>().queue.shutdown();
            }
            _ => {}
        });
}
