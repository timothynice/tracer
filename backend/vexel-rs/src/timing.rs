//! Stage timing, printed when `VEXEL_TIMING` is set.
//!
//! The pipeline is fifteen stages deep and the expensive one moves with the
//! image, so "the trace took 900 ms" is not actionable on its own.

use std::sync::OnceLock;
use std::time::Instant;

fn enabled() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("VEXEL_TIMING").is_some())
}

pub struct Timer {
    start: Instant,
    last: Instant,
}

impl Timer {
    pub fn new() -> Self {
        let now = Instant::now();
        Timer { start: now, last: now }
    }

    pub fn lap(&mut self, name: &str) {
        if !enabled() {
            return;
        }
        let now = Instant::now();
        eprintln!("  {:>9.2} ms  {}", (now - self.last).as_secs_f64() * 1000.0, name);
        self.last = now;
    }

    pub fn total(&self, name: &str) {
        if !enabled() {
            return;
        }
        eprintln!("  {:>9.2} ms  {} (total)", (Instant::now() - self.start).as_secs_f64() * 1000.0, name);
    }
}

impl Default for Timer {
    fn default() -> Self {
        Self::new()
    }
}
