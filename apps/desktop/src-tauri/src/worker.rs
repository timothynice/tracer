//! The trace worker: this binary started with `--trace-worker`, one per trace. It reads one job from stdin
//! (a JSON line, then the file's bytes), traces it with a `Core` of its own, writes one JSON line to stdout and
//! exits. Killing it cancels the trace, its exit gives back the memory, and its crash is a failed trace, not a
//! closed window. It never touches AppKit, so it never shows in the Dock.
use crate::error::CommandError;
use serde::Deserialize;
use serde_json::{json, Value};
use std::io::{BufRead, Read, Write};
use std::time::Duration;
use studi0trace_core::api::Core;

pub const FLAG: &str = "--trace-worker";
/// With this set to "1", a job's `test` hook is obeyed; otherwise it is ignored.
pub const TEST_HOOKS: &str = "STUDI0TRACE_TEST_HOOKS";

#[derive(Debug, Deserialize)]
struct Header {
    parameters: Value,
    auto: bool,
    bytes: usize,
    #[serde(default)]
    test: Option<TestHook>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
enum TestHook {
    Sleep { ms: u64 },
    Die,
    Garbage,
}

/// The answer to one job, as the worker writes it.
pub fn answer(core: &Core, bytes: &[u8], parameters: &Value, auto: bool) -> Value {
    let traced = core.upload(bytes).and_then(|up| {
        let id = up["image_id"].as_str().unwrap_or_default().to_string();
        core.vectorize(&id, parameters, auto)
    });
    match traced {
        Ok(v) => json!({ "ok": v }),
        Err(e) => json!({ "err": CommandError::from(e) }),
    }
}

/// Exit when the app that started this worker is gone (it is then the child of launchd).
fn exit_with_parent() {
    let parent = std::os::unix::process::parent_id();
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(500));
        if std::os::unix::process::parent_id() != parent {
            std::process::exit(4);
        }
    });
}

pub fn main() -> i32 {
    exit_with_parent();
    let mut input = std::io::stdin().lock();
    let mut line = String::new();
    if input.read_line(&mut line).is_err() {
        eprintln!("trace worker: no job");
        return 2;
    }
    let header: Header = match serde_json::from_str(&line) {
        Ok(h) => h,
        Err(e) => {
            eprintln!("trace worker: a bad job header: {e}");
            return 2;
        }
    };
    let mut bytes = vec![0; header.bytes];
    if let Err(e) = input.read_exact(&mut bytes) {
        eprintln!("trace worker: a short job: {e}");
        return 2;
    }
    if std::env::var(TEST_HOOKS).as_deref() == Ok("1") {
        match header.test {
            Some(TestHook::Sleep { ms }) => std::thread::sleep(Duration::from_millis(ms)),
            Some(TestHook::Die) => std::process::exit(70),
            Some(TestHook::Garbage) => {
                println!("this is not an answer");
                return 0;
            }
            None => {}
        }
    }
    let out = answer(&Core::new(), &bytes, &header.parameters, header.auto);
    let mut stdout = std::io::stdout().lock();
    if writeln!(stdout, "{out}").and_then(|_| stdout.flush()).is_err() {
        return 3;
    }
    0
}
