# studi0trace-core

Everything the Python server does around the Vexel engine, in Rust: image intake,
the engine's parameters and their JSON schema, the presets, SVG finishing and
stats, the artifact scorecard and fidelity assessment, Auto, and one facade,
`api::Core`, that answers the API's five requests with the API's JSON. The desktop
app (plan 2, `apps/desktop`) and the web build (plan 3, `crates/studi0trace-wasm`)
are meant to call it, and neither exists yet; the Python in `backend/studi0trace` is the
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

Re-export after changing the Python module a fixture is made from, after a change to
the engine that alters what it writes (the `api` and `auto` fixtures embed its SVG; see
`CONTRIBUTING.md`), and after `bench.presets_eval --write-details` (the core embeds
`presets.json` and `preset_details.json`). Review the diff of the fixtures as you would
of code.

**Regenerating, and where the fixtures came from.** The fixtures are exact only for the
environment that made them: macOS on arm64, the venv's numpy, scipy, scikit-image,
Pillow and resvg-py, and the Rust `vexel_rs` (built with `maturin develop --release -m
vexel-rs/Cargo.toml`, `VEXEL_BACKEND` unset). The exporter therefore

- refuses to run off macOS arm64, or with the Python engine, unless `--force`;
- writes `tests/fixtures/provenance.json` on every run: per exporter, the versions and
  platform it ran with, the engine backend, the commit of HEAD and whether the Python
  and engine sources differ from it (`--only` rewrites only the entries it runs);
- refuses a partial run (`--only`) beside records made in another environment, again
  unless `--force`: re-export everything (no `--only`) after upgrading a library.

`tests/provenance.rs` prints that file, and the messages of the tests that compare the
engine's output to a fixture point at it; if an exact comparison fails on a machine that
is not the one recorded there, that is the first thing to check.

**Platform.** The fixtures were exported on macOS arm64. Results that go through
libm (`atan2`, `sin`, `cos`: the SVG parser's arcs, the geometry card, the
scorecard's floats) are compared to the bit there (`tests/common::exact()`), and to
a tolerance on any other target, where a libm may round the last bit the other way.
The SVG a trace writes, and the counts read off it, are decided by the engine's own
floats; the two tests that hold them say what they give up off the fixtures'
platform:

- `tests/api.rs` blanks, on both sides, the SVG, the counts read off it (`stats`),
  Auto's scores, pick and reason, and an Auto response's `parameters_used`. Everything
  else is compared as a string, key order included: statuses, error bodies, ids,
  sizes, the structure of every response.
- `tests/auto.rs` scores the SVGs the Python scored (stored beside the fixture) and holds
  each full scorecard to the Python's (integers exactly, floats to
  `1e-9 * (1 + |want|)`), and applies the rule to the Python's scores for the pick and
  the reason, on every platform. The live trace it holds to the response's structure
  everywhere (four candidates in order, their parameters and keys, all scored); its SVG
  bytes, `stats`, scores, pick and reason only where `exact()`.
- `tests/scorecard.rs` holds every float to `1e-9 * (1 + |want|)` (relative above 1,
  absolute below) on every platform, and to the bit as well on macOS arm64 (but for
  CIEDE2000).

`STUDI0TRACE_FORCE_TOLERANT=1` runs the tolerant branch of all of these on any
machine, including the arm64 Mac, so that branch is exercised here and not only
when someone builds elsewhere.

Not every comparison is gated by `exact()`. Results that depend on libm but are
rounded to f32 or to fixed point before they are compared are held to the bit on
every platform, and are practically immune to a different libm: the Canny stages
(`tests/edges.rs`; the Gaussian taps are `f64::exp` stored as f32, and all 25 taps in
the fixture are correctly rounded), the Lanczos digests (`tests/resample.rs`,
`tests/render.rs`; the taps are `f64::sin` rounded to 22-bit fixed point) and the holes
digests. tiny-skia's one architecture-dependent operation, `recip_fast`, is used only
by the colour-burn and colour-dodge blend modes, which the engine never writes. If one of
these ever fails on another platform, that is where to look.

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

## Build, features and dependencies

- **`--features python` cannot link without maturin.** The `python` feature builds the
  `studi0trace_core` extension module (pyo3 with `extension-module`), which leaves the
  Python symbols to the interpreter; `cargo build` or `cargo test` with it fails at
  link time. Build it only through `maturin develop` (the command above). Never use
  `--all-features` (in CI or by hand): it turns the feature on.
- **`serde_json` has `preserve_order`.** The API's JSON is ordered (the UI lays its
  controls out in the order `properties` is written, and `tests/api.rs` compares key
  order), so every object the core builds keeps its keys in insertion order. Cargo
  unifies features across a dependency graph: a shell that depends on this crate gets
  `preserve_order` for all of its own `serde_json` use too.
- **Auto runs on a pool of its own**: rayon threads named `studi0trace-auto-N` with 8 MiB
  stacks (rayon's global workers have 2 MiB, too little for resvg on a deep SVG), built on
  first use and shared by every `Core` in the process. Where threads cannot be spawned
  (`wasm32-unknown-unknown` without shared memory) the pool is not built and Auto runs on
  the calling thread, on whatever stack it has (1 MiB on wasm by default); plan 3 must
  measure that.
- **The release profile** (`opt-level = 3`, `lto = "fat"`, `codegen-units = 1`) is in the
  root `Cargo.toml`, the workspace's. `backend/Dockerfile` builds the engine alone from
  the `backend/` context, which has no root manifest, so it carries the same profile in
  `CARGO_PROFILE_RELEASE_*` environment variables; change one, change the other.
- **Test seams** are public (the integration tests are another crate) but not part of the
  API, and `#[doc(hidden)]`: `Core::with_tracer`, `auto::run_with` and `auto::run_with_scorer`
  (with the `auto::Tracer` and `auto::Scorer` types) let a test replace the engine, and the
  scoring, so that a panic, a slow trace or an unrenderable SVG can be tried;
  `Core::cached_images` and `Core::cached_bytes` read the upload store.

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
  drawing, elements nested deeper than 988 levels, and a render of more than
  `render::MAX_PIXELS` (2^28 pixels, a 1 GiB buffer; it applies to the size asked
  for as well as the SVG's own), where the Python would try (and exhaust memory, or
  recurse until it raises).
- **Stack depth.** An SVG nested a few hundred levels deep takes resvg about 3.5 KB
  of stack a level to render, so one near the 988 limit overflows a 2 MiB thread
  (a rayon worker's, a test's), which is an abort and not an error. The SVGs the
  engine writes nest a few levels, so only scoring an SVG the core did not make
  can reach it; do that on a thread with room (`scorecard`'s module documentation).
  The Python binding runs on the caller's thread: a deep SVG is fine on Python's
  main thread and on its threads at the default stack size, and kills the
  interpreter on a thread made after `threading.stack_size(2 * 1024 * 1024)` (a
  980-deep SVG did).
- **An empty parameter name.** The 422 for an unknown parameter whose name is the
  empty string has `loc` `["vexel"]` where Pydantic's is `["vexel", ""]` (the facade
  drops an empty `loc` element; `Violation::from`). The frontend never sends one.
- **Non-finite floats** are `null` in the core's JSON (`serde_json` has no NaN or
  infinity). The Python server cannot send one: Starlette's `JSONResponse` raises
  `ValueError` ("Out of range float values are not JSON compliant") and the client
  gets a 500. None of the scorecard's keys was non-finite over the corpus.
- **Float text** differs for a float outside the ordinary range (the Python's
  `3.4e-05`, the core's `0.000034`): the same double, and no parameter the UI sends
  reaches it.
- **Not ported:** the Potrace and VTracer engines (`GET /engines` lists Vexel
  only; the core describes one engine) and the Python `quality.LOWER_IS_BETTER`
  set, which only the bench reads.
- **The scorecard is bit-exact only on macOS arm64**; elsewhere it agrees to
  `1e-9 * (1 + |want|)`, and its integers (every count) agree exactly.
- **Raster images in an SVG** (`<image>`) are left out of a render, where resvg-py
  draws them: the core builds resvg without its raster decoders, and replaces usvg's
  `href` resolver, which opens whatever file an `<image>` names, with one that finds
  nothing (a `data:` URL of a nested SVG is still resolved). A traced SVG carries none.
- **XML** is read by roxmltree where the Python uses ElementTree (expat). They part on
  an internal DTD's `<!ATTLIST>` defaults (expat applies them), on an undeclared entity
  beside an external DTD (expat reads nothing, roxmltree refuses the document) and on
  `xmlns=""` (an element so marked is left out of the drawing here and drawn there).
  None occurs in engine output (`drawing.rs`'s module documentation).
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
- Parse JSON number text correctly rounded, as a browser's `JSON.parse` does. The core
  takes the parsed `f64` as it is, and `serde_json`'s default parser can be an ulp off
  on a decimal of 17 digits; this crate depends on `serde_json` with `float_roundtrip`
  on (and `preserve_order`), and cargo unifies features, so a shell that parses with
  `serde_json` inherits exact parsing and needs to do nothing. A shell that parses some
  other way (a different JSON crate, hand-made text) must parse correctly rounded.
- On `wasm32-unknown-unknown`, give the trace a clock, in two places. `Instant::now()`
  panics there, and with `panic=abort` (that target's default) the panic ends the
  module, so `catch_unwind` cannot turn it into an `engine_crashed`. The core reads the
  clock once, in `auto::trace_finished`; **the engine reads it too, on every trace**
  (`vexel_rs`'s `timing::Timer::new` calls `Instant::now()` unconditionally, in
  `engine::trace_rgba` and in the partition, shadow and topology stages, whether or not
  `VEXEL_TIMING` is set). Plan 3 must make the engine's `Timer` lazy (read the clock only
  when `VEXEL_TIMING` is set) or give it a wasm clock, and hand `trace_finished` one.
- Answer a refusal with `ApiError::status` and `ApiError::response_body()`.
- **Cap the work, not only the upload, and keep it off the UI thread.** The engine's
  cost is far above what the intake limit suggests. Measured in the final review, on a
  2048 x 2048 (4.2 MP) upscaled badge: a plain Balanced trace took 97 s and peaked at
  7.5 GB of resident memory; Auto took 120 s and 10.8 GB (14.9 GB peak footprint). The
  40 MP intake limit admits ten times that, a wasm build has 4 GB of address space, and
  `vectorize` cannot be cancelled and reports no progress. Plans 2 and 3 must measure
  and set their own pixel cap for tracing (or downscale before tracing), run `vectorize`
  on a worker thread and never the UI's, and plan for cancellation and progress (a
  cooperative flag in the engine's stages, or a worker the shell can drop).
- `Core::with_limits` takes the intake's pixel cap: above 2^26 pixels (about 67.1 MP)
  Auto cannot score an upload (its renders at 2x would pass `render::MAX_PIXELS`), and
  degrades to "scoring was unavailable, so the first preset that traced" instead of
  failing. A plain trace is unaffected. Set the cap well under it (the engine's cost
  above is the real limit); it is not clamped, because a shell may want a higher one
  for plain traces.
