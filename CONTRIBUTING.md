# Contributing

Thanks for looking. This is a small project with one strong opinion, described
below — everything else is negotiable.

## The one rule

**A tracing quality change is not an improvement until the bench says so.**

```bash
cd backend
.venv/bin/python -m bench run --engines vexel --label my-change
.venv/bin/python -m bench compare bench/baselines/vexel.json bench/reports/<stamp>-my-change/results.json
```

If a class regresses, tighten the change — do not move the baseline. Baselines
move only when a change is understood, deliberate and explained in the commit
message. Quoting a number in a comment, a preset description or the README
means that number was measured on the current corpus with the current build.

## Setup

```bash
cd backend && uv venv && uv pip install -e '.[dev]'   # Python ≥ 3.12
cd backend && .venv/bin/python -m maturin develop --release -m vexel-rs/Cargo.toml
cd backend && VIRTUAL_ENV=$PWD/.venv .venv/bin/python -m maturin develop --release \
    -m ../crates/studi0trace-core/Cargo.toml --features python
cd frontend && npm ci                                  # Node ≥ 20
```

The two `maturin` lines build Rust (needs a toolchain from <https://rustup.rs>,
1.88 or newer for the second). The first is Vexel's pipeline: skip it and
everything still works — Vexel falls back to its Python implementation and
traces about ten times slower. The second is `studi0trace_core`, the Rust port of
everything around the engine, as a Python module beside `vexel_rs`; only
`tools.diffcheck scorecard` and `tests/test_core_scorecard.py` use it. Without it
the default `tools.diffcheck` run stops at once (see below) and that test file is
skipped.

Run both servers:

```bash
backend/.venv/bin/python -m uvicorn studi0trace.main:app --app-dir backend --reload
npm --prefix frontend run dev
```

## Before you open a pull request

```bash
cd backend           && .venv/bin/python -m pytest
cargo test --workspace --release        # at the repo root: the engine and the core
RUSTDOCFLAGS="-D warnings" cargo doc -p studi0trace-core --no-deps   # the core's docs build clean
cd frontend          && npm run test:run && npm run build
```

`cargo test --workspace --release` is the Rust line, not `cd backend/vexel-rs &&
cargo test`: inside the workspace that runs the engine alone. The core's golden
fixtures (`crates/studi0trace-core/tests/fixtures`) embed the SVG the engine
writes (`api.json`, `auto.json`, `auto_*.svg`), so a change to the engine that
alters its output breaks the core's tests on macOS arm64, where they compare the
bytes. Re-export them before you push (the next section).

If you change a dependency of the Rust workspace (`cargo update`, a version in any `Cargo.toml`), the Docker
image's own lockfile moves with it: `cd backend && .venv/bin/python -m tools.sync_vexel_lock` copies the
engine's closure out of the root `Cargo.lock` (the pytest `test_vexel_lock_matches_workspace.py` fails with this
command when it has fallen behind), and the image builds with `--locked`. Likewise `[profile.release]` in the
root `Cargo.toml` is carried by `CARGO_PROFILE_RELEASE_*` in `backend/Dockerfile`; change both.

New behaviour ships with a test. The concurrency and CORS-on-error tests in
`backend/tests/test_api.py` are regression guards for real production
incidents — if one of them fails, something is actually broken.

## Vexel has two implementations

`backend/vexel-rs/` is the Rust pipeline and is what runs;
`backend/studi0trace/engines/vexel/*.py` is the reference it was ported from and
the fallback when the extension is not built. **They are one algorithm.** A fix
to a stage in one needs the same fix in the other, and

```bash
cd backend && .venv/bin/python -m tools.diffcheck
```

is what proves it landed: it runs each stage in both over the whole corpus and
reports where they disagree — the partition's labels to the last float32 bit,
the fills by what they paint. Where the two are allowed to differ, the tolerance
table at the top of that file says so and says why. Its `scorecard` stage holds
the Rust core's scorecard to the Python's and needs the second `maturin` line
above: without `studi0trace_core` it exits at once with that command, and naming
stages (`tools.diffcheck labels0 rects`) runs only those.

A change to either that alters what the engine writes has a third step, because
the Rust core (`crates/studi0trace-core`) holds the engine's SVG to the byte on
macOS arm64. With the Rust `vexel_rs` built and installed from your checkout
(`maturin develop --release -m vexel-rs/Cargo.toml`, the first line above), on
macOS arm64:

```bash
cd backend && .venv/bin/python -m tools.export_core_fixtures --only api,auto
cargo test --workspace --release
```

`api` and `auto` are the two fixtures the core's tests compare a live trace with
(`tests/api.rs`, `tests/auto.rs`); they fail until they are re-exported. The
other exporters that trace (`svg,render,drawing,holes,geometry,scorecard`) store
the trace as an input, so their tests keep passing; re-export them too
(`.venv/bin/python -m tools.export_core_fixtures` is everything) so the fixtures
stand for what the engine writes now, and review the diff as you would code.
Off macOS arm64 the SVG bytes are not compared (the crate's README says what is),
and the exporter refuses to run there.

Run the suite against both:

```bash
cd backend && .venv/bin/python -m pytest && VEXEL_BACKEND=python .venv/bin/python -m pytest
```

`VEXEL_TIMING=1` makes the Rust engine print a per-stage breakdown to stderr.
The expensive stage moves with the image, so "the trace took 900 ms" is not
actionable on its own:

```bash
cd backend && VEXEL_TIMING=1 .venv/bin/python -c "
from PIL import Image; import numpy as np, vexel_rs
from studi0trace.engines.vexel.engine import VexelParams
a = np.asarray(Image.open('bench/corpus/real/logo/vexel-logo-768.png').convert('RGBA'), np.uint8)
vexel_rs.trace(a.tobytes(), a.shape[1], a.shape[0], VexelParams().model_dump())"
```

## Conventions worth knowing

- **Engines are synchronous.** Only `api/` touches asyncio/anyio.
- **Every engine parameter lives in its Pydantic `Params` model**, with bounds,
  a default, a description and `json_schema_extra={"ui": {...}}`. The UI is
  generated from that schema, so adding a parameter needs no frontend change —
  and hardcoding a control in the frontend defeats the point.
- **Errors that reach a client carry a stable `code`.** See `IntakeError` and
  `ErrorBody`.
- **No one-sided coloured borders as highlights** in the UI. Use the brand dot,
  a `ring-1 ring-primary/20`, a muted tint, or a weight shift.

## Adding an engine

Implement the `Engine` protocol in `backend/studi0trace/engines/base.py`, then
register it in `registry.load_builtin()`. It needs an `id`, a `label`, a
`description`, a `Params` model and a `trace()` that returns through
`engines.base.finish()`. Set `primary = True` if it should appear in the app's
own UI; leave it off and the engine is still callable through the API and
usable in the bench, which is where engine comparisons belong.
