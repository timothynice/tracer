# studi0trace-core

Everything the Python server does around the Vexel engine, in Rust: image intake,
the engine's parameters and their JSON schema, the presets, SVG finishing and
stats, the artifact scorecard and fidelity assessment, Auto, and one facade,
`api::Core`, that answers the API's five requests with the API's JSON. The
desktop app and the web build call it; the Python in `backend/studi0trace` is the
reference each module was ported from, and stays the definition of what the core
does until the Python server is retired. Rust >= 1.88 (`slice::as_chunks`).

The engine itself is `backend/vexel-rs`, which this crate depends on. The root
`Cargo.toml` is the workspace of the two.

## The facade

```rust
let core = studi0trace_core::api::Core::new();        // Core::with_limits(..) to change the caps
core.health();                                        // GET  /health
core.engines();                                       // GET  /engines   (only Vexel)
core.presets();                                       // GET  /presets
let up = core.upload(&bytes)?;                        // POST /uploads   -> {image_id, width, height, format}
let out = core.vectorize(id, &parameters, auto)?;     // POST /vectorize
```

Every call returns a `serde_json::Value` shaped and ordered as the FastAPI
response is (field names, key order, error `code`s). A refusal is an `ApiError`:
`status` is the HTTP status FastAPI would have sent and `response_body()` the
body (`{"detail": ...}`). `Core` is `Send + Sync`; hold it in an `Arc`.

Try it without writing any:

```bash
cargo run -p studi0trace-core --release --example trace -- IMAGE [PRESET|auto] > out.svg
```

`PRESET` is a preset id (`balanced`, `logo`, `detailed`, `dense`, `flat`,
`cutfile`); `auto` (the default) traces every candidate, scores them and keeps the
cleanest of the most faithful, and says on stderr what it chose and why.

## Tests and fixtures

```bash
cargo test --workspace --release          # the engine and the core
cargo build -p studi0trace-core --release # no features: no pyo3, no fonts, no C
```

The core is held to the Python by golden fixtures in `tests/fixtures`, which
`backend/tools/export_core_fixtures.py` writes from the Python reference:

```bash
cd backend && .venv/bin/python -m tools.export_core_fixtures                   # everything
cd backend && .venv/bin/python -m tools.export_core_fixtures --only presets,api
```

Re-export after changing the Python module a fixture is made from, and after
`bench.presets_eval --write-details` (the core embeds `presets.json` and
`preset_details.json`). Review the diff of the fixtures as you would of code.

**Platform.** The fixtures were exported on macOS arm64. Results that go through
libm (`atan2`, `sin`, `cos`: the SVG parser's arcs, the geometry card, and the SVG
text of a trace, which the engine's own floats decide) are compared to the bit
there (`tests/common::exact()`), and to a tolerance on any other target, where a
libm may round the last bit the other way. `STUDI0TRACE_FORCE_TOLERANT=1` runs the
tolerant comparison on any machine, including the arm64 Mac, so that branch is not
untested until someone builds elsewhere.

**Against the Python, end to end.** `backend/tools/diffcheck.py scorecard` scores
a finished trace of every corpus item with `imaging/quality.assess` and with this
crate's `scorecard::assess` and compares every key: the same keys in the same
order, the same types, every count equal, every float to 1e-9 relative (bit-equal
on macOS arm64 except `delta_e_mean` and `delta_e_p95`, a few ulps of CIELAB that
numpy reaches through BLAS). The core reaches Python through the optional
`python` feature, a module named `studi0trace_core` that sits beside `vexel_rs`:

```bash
cd backend && VIRTUAL_ENV=$PWD/.venv .venv/bin/python -m maturin develop --release \
    -m ../crates/studi0trace-core/Cargo.toml --features python
.venv/bin/python -m tools.diffcheck scorecard
```

The feature is off by default; `backend/tests/test_core_scorecard.py` is skipped
without the module.

## Known, intentional differences from the Python server

The core answers like the server except where it was decided not to:

- **16-bit greyscale PNG** keeps each sample's high byte, as every other 16-bit
  PNG does. Pillow reads it as `I;16` and clips to white (every sample above 255
  of 65535 comes out 255). The Python is the one that is wrong, and it is not
  fixed.
- **MPO files.** Pillow names a multi-frame MPO `"MPO"` and the Python refuses it
  as `unsupported_format`; the core accepts any file that starts like a JPEG.
- **JPEG pixels** differ from Pillow's by a small IDCT difference (a mean of about
  0.07 of a level on the fixture, a maximum of 3). A JPEG upload can therefore
  trace slightly differently, and Auto can pick a different preset, than on the
  Python server. Every other format decodes byte for byte.
- **Parameters are validated in Pydantic's strict mode.** A number as a string
  (`"6"`), `true` as a number, and `"yes"` or `1` as a boolean are a 422 where
  the server's lax mode converts them. The frontend sends none of these.
- **Upload store.** It has the server's cap (256 MiB, here counting each image as
  its pixels, `4 * width * height`) and its LRU order but no TTL, and ids are the first 128 bits of the file's SHA-256 (32 hex digits),
  not random `uuid4`s: the same file is the same entry, and an id is not a
  security boundary.
- **Refusals only the core has**: more than 2^24 resampled outline points
  (`geometry::MAX_SAMPLES`), more than `drawing::MAX_POINTS` (2^24) points in a
  drawing, and elements nested deeper than 988 levels, where the Python would
  try (and exhaust memory, or recurse until it raises).
- **Non-finite floats** are `null` in the core's JSON (`serde_json` has no NaN or
  infinity), and the Python's JSON writer treats them in its own way. None of the
  scorecard's keys was non-finite over the corpus.
- **Float text** differs for a float outside the ordinary range (the Python's
  `3.4e-05`, the core's `0.000034`): the same double, and no parameter the UI sends
  reaches it.
- **Not ported:** the Potrace and VTracer engines (`GET /engines` lists Vexel
  only; the core describes one engine) and the Python `quality.LOWER_IS_BETTER`
  set, which only the bench reads.
- **The scorecard is bit-exact only on macOS arm64**; elsewhere it agrees to 1e-9
  relative, and its integers (every count) agree exactly.
- **`engine_crashed`, not `engine_failed`**, is the code of a trace that panics,
  as in the Python, where it is the code of any exception that is not an
  `EngineError` (which Vexel never raises).

## What a shell must do

`Core` is the route's engine half. A shell (the Tauri app, a web worker) is its
HTTP half:

- Parse the request and unwrap `parameters["vexel"]`: the frontend sends
  `parameters={"vexel": {...}}`, `Core::vectorize` takes the inner value. Malformed
  JSON, or JSON that is not an object, is a 400 `bad_parameters`.
- Read the `auto` form flag and pass it as the third argument.
- Resolve `engines`: an empty list means every engine (Vexel); any name but `vexel`
  is a 400 `unknown_engine`.
- A request with a `file` and no `image_id` is `upload` and then `vectorize`; one
  with neither is `no_image` (the core answers an empty `image_id` with it).
- Expire uploads. The core keeps a byte cap and no clock; "the user has gone away"
  is the shell's to decide, by dropping the `Core` or building a smaller one.
- Parse JSON number text correctly rounded (`serde_json`'s `float_roundtrip`, or
  a browser's `JSON.parse`): the default `serde_json` parser can be an ulp off on a
  decimal of 17 digits, and the core takes the parsed `f64` as it is.
- On `wasm32-unknown-unknown`, give `auto::trace_finished` a clock: `Instant::now()`
  traps there, an abort that no `engine_crashed` can report.
- Answer a refusal with `ApiError::status` and `ApiError::response_body()`.
