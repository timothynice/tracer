//! Stage timing, printed when `VEXEL_TIMING` is set.
//!
//! The pipeline is fifteen stages deep and the expensive one moves with the
//! image, so "the trace took 900 ms" is not actionable on its own.
//!
//! The clock is read only when `VEXEL_TIMING` is set: `Instant::now()` panics on
//! `wasm32-unknown-unknown`, and every trace makes a `Timer`.

use std::sync::OnceLock;
use std::time::Instant;

fn enabled() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var_os("VEXEL_TIMING").is_some())
}

/// `None` while timing is off: nothing is read from the clock.
pub struct Timer(Option<Laps>);

struct Laps {
    start: Instant,
    last: Instant,
}

impl Timer {
    pub fn new() -> Self {
        Timer(enabled().then(|| {
            let now = Instant::now();
            Laps { start: now, last: now }
        }))
    }

    pub fn lap(&mut self, name: &str) {
        let Some(laps) = &mut self.0 else { return };
        let now = Instant::now();
        eprintln!("  {:>9.2} ms  {}", (now - laps.last).as_secs_f64() * 1000.0, name);
        laps.last = now;
    }

    pub fn total(&self, name: &str) {
        let Some(laps) = &self.0 else { return };
        eprintln!("  {:>9.2} ms  {} (total)", (Instant::now() - laps.start).as_secs_f64() * 1000.0, name);
    }
}

impl Default for Timer {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_timer_reads_no_clock_while_timing_is_off() {
        if enabled() {
            return; // VEXEL_TIMING is set for this run: the timer is meant to read the clock
        }
        let mut t = Timer::new();
        t.lap("stage");
        t.total("trace");
        assert!(t.0.is_none());
    }
}
