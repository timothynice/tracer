//! One binary, two roles: the app, and (with `--trace-worker`) the child process a trace runs in. The worker
//! branch comes before anything of Tauri's or AppKit's is touched, so a worker never shows in the Dock.
fn main() {
    if std::env::args().nth(1).as_deref() == Some(studi0trace_desktop::worker::FLAG) {
        std::process::exit(studi0trace_desktop::worker::main());
    }
    studi0trace_desktop::run();
}
