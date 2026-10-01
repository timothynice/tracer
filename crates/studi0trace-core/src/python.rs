//! The CPython binding, built only with `--features python` (a plain `cargo build` and `cargo
//! test` never see pyo3: with `extension-module` on the CPython symbols are left for the
//! interpreter to resolve, and a test binary cannot link).
//!
//! It exists for one reader, `backend/tools/diffcheck.py scorecard`, which holds
//! [`scorecard::assess`] to the Python's `quality.assess` over the whole corpus, and for the
//! regression test `backend/tests/test_core_scorecard.py`. Build it into the backend's venv with
//!
//! ```text
//! cd backend && VIRTUAL_ENV=$PWD/.venv .venv/bin/python -m maturin develop --release \
//!     -m ../crates/studi0trace-core/Cargo.toml --features python
//! ```
//!
//! The module is `studi0trace_core`, not `vexel_rs`: the two extensions live side by side.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use serde_json::{Map, Value};

use crate::scorecard::{self, Reference};

/// A JSON value as the Python object the Python server's own dict holds: an integer stays an
/// `int`, a float a `float`, `null` (what a non-finite float becomes in `serde_json`) `None`.
/// Keeping the types is the point: a count that came back as `3.0` would pass a float
/// comparison and still be a different answer.
fn object<'py>(py: Python<'py>, v: &Value) -> PyResult<Bound<'py, PyAny>> {
    Ok(match v {
        Value::Null => py.None().into_bound(py),
        Value::Bool(b) => b.into_py(py).into_bound(py),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i.into_py(py).into_bound(py)
            } else if let Some(u) = n.as_u64() {
                u.into_py(py).into_bound(py)
            } else {
                n.as_f64().unwrap_or(f64::NAN).into_py(py).into_bound(py)
            }
        }
        Value::String(s) => s.into_py(py).into_bound(py),
        Value::Array(a) => {
            let list = PyList::empty_bound(py);
            for x in a {
                list.append(object(py, x)?)?;
            }
            list.into_any()
        }
        Value::Object(m) => map(py, m)?.into_any(),
    })
}

/// An insertion-ordered map as a `dict` in the same order (a Python dict keeps it).
fn map<'py>(py: Python<'py>, m: &Map<String, Value>) -> PyResult<Bound<'py, PyDict>> {
    let out = PyDict::new_bound(py);
    for (k, v) in m {
        out.set_item(k, object(py, v)?)?;
    }
    Ok(out)
}

/// `quality.assess(svg, Reference(rgba))` for `rgba`, `width * height * 4` bytes: the fidelity
/// of `svg` against the source and the artifact scorecard, as one dict in the Python's key
/// order. The work runs with the GIL released. A source of the wrong size, an SVG that does not
/// render and the rest of [`scorecard::ScoreError`] are a `ValueError` with the error's text.
///
/// **Stack.** It runs on the calling thread, and an SVG nested near the 988-level limit needs
/// about 3.5 MiB of stack to render (`scorecard`'s module documentation). A stack overflow is an
/// abort, not an exception, so it ends the interpreter: a 980-deep SVG is fine on Python's main
/// thread and on its threads at the default stack size, and killed the process on a thread made
/// after `threading.stack_size(2 * 1024 * 1024)`. The SVGs the engine writes nest a few levels.
#[pyfunction]
fn assess<'py>(py: Python<'py>, svg: &str, rgba: &[u8], width: usize, height: usize) -> PyResult<Bound<'py, PyDict>> {
    let card = py
        .allow_threads(|| {
            let reference = Reference::new(rgba, height, width)?;
            scorecard::assess(svg, &reference, None)
        })
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    map(py, &card)
}

#[pymodule]
fn studi0trace_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(assess, m)?)?;
    m.add("__version__", crate::VERSION)?;
    Ok(())
}
