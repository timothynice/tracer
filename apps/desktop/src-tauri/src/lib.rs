//! Studi0Trace for Mac: the Tauri shell around `studi0trace_core::api::Core`.
pub mod commands;
pub mod error;
pub mod intake;
pub mod queue;
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
        .manage(AppState { core: Core::new(), images: store::Images::default(),
            queue: queue::TraceQueue::new(std::env::current_exe().expect("the app knows where it is")),
        })
        .invoke_handler(tauri::generate_handler![
            commands::health,
            commands::engines,
            commands::presets,
            commands::open_paths,
            commands::pick_images,
            commands::open_bytes,
            commands::read_image,
            commands::close_image,
            commands::vectorize,
            commands::cancel_trace,
        ])
        .build(tauri::generate_context!())
        .expect("Studi0Trace failed to start")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                use tauri::Manager;
                app.state::<AppState>().queue.shutdown();
            }
        });
}
