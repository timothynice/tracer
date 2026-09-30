//! Studi0Trace's core: everything between the bytes a person drops in and the
//! SVG they save, around the Vexel engine. The Python in `backend/studi0trace`
//! is the reference each module was ported from.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub mod params;
pub mod presets;
