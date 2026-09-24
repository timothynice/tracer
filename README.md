# Studi0Trace

Raster → SVG tracing with pluggable engines, and **Vexel Bench**, a fidelity
scoreboard that says which trace is more true to the source image.

```
backend/   FastAPI service + engines + bench     (Python ≥ 3.12)
backend/vexel-rs/   Vexel's pipeline in Rust, built as an extension module
frontend/  Studi0Trace web app: React 18 + TS + Tailwind on the Studi0 design system (Node ≥ 20)
```

Engines: **Potrace** (1-bit outlines), **VTracer** (colour layers), and
**Vexel** — Studi0's own fidelity-first engine.

### Vexel

Vexel is built for logos, flat art and illustrations with gradients and soft
shadows. Instead of quantising colours and tracing bands, it:

1. finds discontinuities as *ridges* of the colour gradient (Canny-style
   non-maximum suppression), so steep smooth ramps stay whole and thin strokes
   keep their seeds;
2. merges regions by how well a **solid / linear / radial** colour model
   explains the union — closed-form least squares from moment statistics, so a
   red→blue gradient is one region and two flat tiles 6 ΔE apart are not;
3. reconstructs each region's fill as a solid, a multi-stop linear gradient or
   a radial gradient (stop-opacity for alpha ramps);
4. rescues thin features swallowed by a neighbour via per-region residuals,
   then joins gradient fragments that one real fill explains;
5. orders shapes by enclosure (painter's algorithm, seamless stacking);
6. builds the boundary **once, as a planar graph** — arcs between the junctions
   where three or more regions meet — so the edge two regions share is placed,
   fitted and emitted a single time and handed to both. Neighbours cannot
   describe it differently, so there is no hairline between them for the
   backdrop to show through, at any tolerance. A region that tapers to a point
   is carried past where the labels give out: below a pixel wide there is no
   pixel to hold it, so the stretch beyond is read as a mixture of three fills
   and the sliver handed back. Its tip is then a **cusp** — all three arcs share
   one tangent — so the boundary that carries on stays a single sweeping curve
   rather than taking a corner where the artwork has none;
7. places those arcs at **sub-pixel** positions inferred from anti-aliasing
   coverage, sharpens corners and junctions, keeps the outline G1 where a
   boundary runs on through a junction, fits circles/ellipses/rects and
   **rounded rectangles** (`<rect rx>`) as primitives and a run that is one
   circle as an **`A` arc**, fits every run between breaks **lines first** — straight runs
   from the residuals about their own line, cubics between them, kept when
   that costs no more segments than a curve — so a straight edge is emitted
   straight rather than as a cubic that bows, then makes lines that are meant
   to be **parallel, perpendicular or on an axis exactly so** across the whole
   boundary graph (`vexel/regularity.py`), traces a **small input with thin
   features at twice its size** and scales the drawing back (`vexel/upsample.py`),
   makes a **mirror- or rotationally
   symmetric** mark exactly so (`vexel/symmetry.py`), writes a **repeated shape
   once** and paints its copies with `<use>` (`vexel/reuse.py`), and
   otherwise G1 cubic Béziers — and, with **Render refinement** on, renders each
   edge's two shapes and nudges the curve until the pixels match the source;
8. recovers thin lines as **stroked centreline paths** (`fill="none"`,
   measured `stroke-width`, cap style read from the source) instead of
   filled slivers;
9. recognises **drop shadows, glows and inner shadows** as what they are — a
   blurred, offset, scaled copy of a shape's own alpha — recovers
   `(dx, dy, σ, colour, opacity)` and emits the SVG `<filter>` that regenerates
   them, instead of slicing the falloff into bands with lumpy iso-contour
   edges.

On the bench (synthetic corpus plus real Studi0 logos; mean CIEDE2000 ΔE,
lower is better):

| class | Potrace | VTracer | **Vexel** |
|---|---|---|---|
| logo | 11.4 | 1.20 | **0.36** |
| flat | 20.4 | 0.98 | **0.64** |
| gradient | 23.2 | 10.40 | **0.91** |
| shadow | 14.8 | 3.24 | **0.42** |

Vexel has the lowest ΔE on **68 of 72** corpus items, and gets there with far
less geometry: 9.9 paths and 11.7 KB per image on average against VTracer's 34.0
paths and 18.0 KB.

`seam_ppm` is the other number to watch: parts per million of the artwork that
the emitted shapes cover less than the source does. It is what the shared
boundary is for, and it does not show up in ΔE — a hairline between two shapes
is a handful of pixels in a frame, and sweeping `curve_tolerance` from 0.1 to
2.0 used to take the sub-pixel hole count on a 512 px logo from 397 to 19502
while moving `score` by 0.007.

### Vexel is Rust

The pipeline lives in `backend/vexel-rs/` and is built as an extension module.
It averages **0.21 s an image** against the Python implementation's 2.1 s —
**10.4× over the corpus**, and 15.8× on the logo class, where the two 768 px
real logos are. The Python pipeline is still in `engines/vexel/*.py`: it is the
reference the Rust one was ported from, the fallback when the extension is not
built, and the oracle `tools/diffcheck.py` compares every stage against.

Set `VEXEL_BACKEND=python` to force the reference, `rust` to require the
extension. The default is the extension when it imports.

Design, measurements and the two defects the port turned up —
`skimage.morphology.medial_axis` is seeded from the OS and so not reproducible,
and two corpus items were being scored on a lucky random sample — are in
`docs/superpowers/specs/2026-09-20-vexel-rust-port-design.md`.

Reproduce it yourself — the corpus is generated, not shipped:

```bash
cd backend
python -m bench generate            # rebuild the corpus from bench/synth.py
python -m bench run                 # every registered engine
```

Design and research notes: `docs/superpowers/specs/2026-09-17-vexel-engine-design.md`,
and `2026-09-20-vexel-rust-port-design.md` for the Rust implementation.

## Run it

Backend (needs the `potrace` binary: `brew install potrace` / `apt install
potrace`, and a Rust toolchain for Vexel: <https://rustup.rs>):

```bash
cd backend
uv venv .venv && uv pip install -p .venv/bin/python -e '.[dev]'
.venv/bin/python -m maturin develop --release -m vexel-rs/Cargo.toml   # Vexel
.venv/bin/uvicorn studi0trace.main:app --reload        # http://localhost:8000
```

Without the last step everything still works — Vexel falls back to its Python
implementation and traces about ten times slower.

Frontend:

```bash
cd frontend
npm ci
npm run dev                                            # http://localhost:5173
```

`frontend/.env.local` points the app at `http://localhost:8000`.

## Tests

```bash
cd backend           && .venv/bin/python -m pytest
cd backend/vexel-rs  && cargo test
cd frontend          && npm run test:run
```

The backend suite includes a concurrency test (two 0.4 s traces must finish in
< 0.7 s) and asserts CORS headers on every error path, including 500s. It runs
against whichever Vexel backend is installed; `VEXEL_BACKEND=python pytest`
exercises the other one.

`tools/diffcheck.py` compares the two Vexel implementations stage by stage over
the whole corpus — the partition's labels to the last float32 bit, the fills by
what they paint:

```bash
cd backend && .venv/bin/python -m tools.diffcheck            # every stage
.venv/bin/python -m tools.diffcheck labels0 --filter 128     # one stage, some items
```

## API

| Route | Purpose |
|---|---|
| `GET /health` | `{status, version, engines}` |
| `GET /engines` | Each engine's `id`, `label`, `description`, JSON Schema `params` and `defaults`. The UI renders every control from this — adding an engine or a parameter needs no frontend change. |
| `GET /presets` | Auto first, then named parameter bundles: `id`, `label`, `engine`, `description`, `detail` (what it measurably costs, from the bench), `params`, `kind` (`auto` or `preset`) and `auto_candidate`. A preset layers over the engine's **defaults**, never over current values. Auto has no params; it is asked for with `auto=true`. The `detail` lines are measured, never hand-written: `VEXEL_BACKEND=rust RAYON_NUM_THREADS=1 .venv/bin/python -m bench.presets_eval --out DIR --fast --workers 3 --write-details` rewrites `studi0trace/engines/preset_details.json` from a run over the whole corpus. |
| `POST /uploads` | multipart `file` → `{image_id, width, height, format}`. Validated once and kept server-side (LRU, sliding 30 min TTL) so re-tracing while tuning doesn't re-send the file. |
| `POST /vectorize` | multipart: `image_id` **or** `file`, `parameters` (JSON keyed by engine id), `engines` (comma list; default all). Returns `results.{engine}.{svg, elapsed_ms, stats | error}`, `image_id`, `width`, `height`. Expired id → 404 `image_expired`; the client re-uploads and retries once. With `auto=true`, each selected engine that has Auto candidates (Vexel: Balanced, Logo & icon, Detailed, Simplified) is traced once per candidate, concurrently, and each trace is scored against the source (`studi0trace/imaging/quality.py`: ΔE, edge F1, the artifact scorecard); `auto.{engine}` then holds every candidate's `svg`, `stats`, `parameters` and `scores`, the `pick` and a `reason`, and `results.{engine}` is the pick. The rule (`studi0trace/auto.py`): the lowest artifact index among candidates within ΔE +max(0.15, 30 %) and edge F1 −0.02 of the best, ties to fewer shapes. A failing candidate is reported and left out; `auto=true` with no engine that has candidates → 400 `auto_unavailable`. |

Uploads are sniffed with Pillow (client `Content-Type` is ignored), limited by
`MAX_UPLOAD_BYTES` (20 MB) and `MAX_IMAGE_PIXELS` (40 MP), and normalised to
RGBA — transparency reaches every engine. Errors carry a stable `code`.
Env: `ALLOWED_ORIGINS` (comma list), `MAX_UPLOAD_CACHE_BYTES` (256 MB),
`UPLOAD_TTL_SECONDS` (1800). The upload cache is per process; with several
workers the client's re-upload fallback keeps things correct, just slower.

## Frontend

Single workspace: drop / paste / browse an image (or pick a sample), then tune.
Every new image starts on **Auto**: the preset list says which preset Auto chose
and why, and each candidate's row shows that image's own result (thumbnail, ΔE,
shape count, clean or the issues found). Every candidate's trace comes back with
the Auto run, so clicking one shows it at once; moving a control or picking a
preset leaves Auto.
Controls are generated from `GET /engines` (`ui.control`, `ui.group`, `ui.label`,
`ui.step`, `ui.unit` hints on each Pydantic field). Parameter changes are
debounced 250 ms, superseded requests are aborted, and the previous result stays
on screen while the next one loads. Views: split (draggable divider), side by
side, overlay (opacity), vector; shared zoom/pan keeps raster and SVG
pixel-aligned. Download SVG or PNG (1×/2×/4×, rendered client-side), copy SVG,
and "Compare all engines" for a stats table across engines. System/light/dark
theme, persisted. No service worker.

Tests: Vitest + Testing Library + MSW (`npm run test:run`). A custom Vitest
environment (`src/test/env.ts`) keeps Node's fetch globals under jsdom so MSW
sees real multipart bodies and AbortSignals.

## The engine seam

```python
class Engine(Protocol):
    id: str; label: str; description: str
    Params: type[pydantic.BaseModel]          # bounds, defaults, UI hints, schema
    def trace(self, image: TraceInput, params: BaseModel) -> TraceResult
```

`trace()` is synchronous and CPU-bound; the API runs it in a worker thread.
`TraceInput.image` is RGBA. Return through `engines.base.finish()` so the SVG
gets a source-sized `viewBox` and stats. Register with
`registry.register(MyEngine())` — see `engines/potrace.py` for a 90-line example.

## Vexel Bench

```bash
cd backend
.venv/bin/python -m bench generate                       # synthetic corpus → bench/corpus
.venv/bin/python -m bench run --engines potrace,vtracer  # → bench/reports/<ts>/index.html
.venv/bin/python -m bench compare bench/baselines/vtracer.json bench/reports/<ts>/results.json
.venv/bin/python -m bench sweep --engine vtracer --param color_precision=3:8
```

Every engine's SVG is rasterised back with resvg and scored against the source:
SSIM, CIEDE2000 ΔE (mean/p95), edge F1, alpha error, a **banding index** for
gradient stair-stepping, plus path/node/byte counts and time. Classes:
`logo`, `flat`, `gradient`, `shadow`. Composite weights live in
`bench/config.py` and are echoed into every `results.json`.

## Deploy

Two independent services, described in `render.yaml` and deployed from `main`:

```
┌─ tracer-frontend ──────────┐        ┌─ tracer-backend ─────────────┐
│ Render static site          │        │ Render web service (Docker)  │
│ frontend/dist, built by Vite│  HTTPS │ backend/Dockerfile + uvicorn │
│ VITE_API_URL baked in       │ ─────▶ │ ALLOWED_ORIGINS gates CORS   │
└─────────────────────────────┘        └──────────────────────────────┘
```

The frontend is **static files only** — there is no server-side rendering and
no Node process in production. `VITE_API_URL` is substituted at build time, so
the backend URL is baked into the bundle: change it and you must rebuild, not
just restart.

The backend is a single container: `backend/Dockerfile` builds the Vexel crate
in its own stage, installs potrace and the two wheels, and runs uvicorn. It
fails the build rather than the first request if the extension did not land — a
silent fall back to the Python pipeline would only show up as every trace taking
ten times as long. It is stateless apart from an in-memory
upload cache (LRU with a sliding TTL), so it can be restarted or scaled without
coordination — but uploads do not survive a restart, and a second instance will
not see the first one's `image_id`. The client already handles that: an expired
id returns 404 `image_expired` and it re-uploads once.

### Pointing a domain at it

Both services take custom domains on Render's free tier; only the certificate
and DNS change, no code:

1. Render → each service → **Settings → Custom Domains → Add**, e.g.
   `studi0trace.com` for the frontend and `api.studi0trace.com` for the backend.
2. Add the CNAME records Render shows you at your registrar. Certificates are
   issued automatically.
3. Add the new frontend origin to the backend's `ALLOWED_ORIGINS`, and set the
   frontend's `VITE_API_URL` to the new API domain — **then redeploy the
   frontend**, since that value is compiled in.

### Free tier, honestly

The backend sleeps after inactivity, so the first request after a quiet period
pays 30–50 s of cold start. The shared CPU also costs more than it used to:
Vexel is parallel now, so a 512 px trace that takes ~0.2 s on a laptop's twelve
threads gets most of the way back to a second on a single shared core. Taking
the backend off the free instance type is the one change that fixes both;
nothing else about the deployment needs to move.

## Design docs

`docs/superpowers/specs/` holds the approved designs; `docs/superpowers/plans/`
the implementation plans.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: a tracing quality
change is not an improvement until `python -m bench compare` says so, and
baselines move only deliberately.

## Licence

MIT — see [LICENSE](LICENSE).
