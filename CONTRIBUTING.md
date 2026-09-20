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
cd frontend && npm ci                                  # Node ≥ 20
```

The middle line builds Vexel's Rust pipeline (needs a toolchain from
<https://rustup.rs>). Skip it and everything still works — Vexel falls back to
its Python implementation and traces about ten times slower.

Run both servers:

```bash
backend/.venv/bin/python -m uvicorn studi0trace.main:app --app-dir backend --reload
npm --prefix frontend run dev
```

## Before you open a pull request

```bash
cd backend           && .venv/bin/python -m pytest
cd backend/vexel-rs  && cargo test
cd frontend          && npm run test:run && npm run build
```

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
table at the top of that file says so and says why.

Run the suite against both:

```bash
cd backend && .venv/bin/python -m pytest && VEXEL_BACKEND=python .venv/bin/python -m pytest
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
