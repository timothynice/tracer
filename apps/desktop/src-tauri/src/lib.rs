//! Studi0Trace for Mac: the Tauri shell around `studi0trace_core::api::Core`.
pub mod commands;
pub mod error;
pub mod intake;
pub mod store;
pub mod worker;

use studi0trace_core::api::Core;

/// What every command shares.
pub struct AppState {
    pub core: Core,
    pub images: store::Images,
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_opener::init())
        .manage(AppState { core: Core::new(), images: store::Images::default() })
        .invoke_handler(tauri::generate_handler![
            commands::health,
            commands::engines,
            commands::presets,
            commands::open_paths,
            commands::pick_images,
            commands::open_bytes,
            commands::read_image,
            commands::close_image,
        ])
        .run(tauri::generate_context!())
        .expect("Studi0Trace failed to start");
}
