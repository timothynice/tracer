# Studi0Trace

Raster → SVG tracing with pluggable engines, and **Vexel Bench**, a fidelity
scoreboard that says which trace is more true to the source image.

```
backend/   FastAPI service + engines + bench     (Python ≥ 3.12)
frontend/  Studi0Trace web app: React 18 + TS + Tailwind on the Studi0 design system (Node ≥ 20)
```

Engines: **Potrace** (1-bit outlines), **VTracer** (colour layers), and
**Vexel** — Studi0's own fidelity-first engine.

### Vexel

Vexel (`backend/studi0trace/engines/vexel/`) is built for logos, flat art and
illustrations with gradients and soft shadows. Instead of quantising colours
and tracing bands, it:

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
6. places outlines at **sub-pixel** positions inferred from anti-aliasing
   coverage, sharpens corners, fits circles/ellipses/rects as primitives and
   otherwise G1 cubic Béziers;
7. recovers thin lines as **stroked centreline paths** (`fill="none"`,
   measured `stroke-width`, cap style read from the source) instead of
   filled slivers;
8. recognises **drop shadows, glows and inner shadows** as what they are — a
   blurred, offset, scaled copy of a shape's own alpha — recovers
   `(dx, dy, σ, colour, opacity)` and emits the SVG `<filter>` that regenerates
   them, instead of slicing the falloff into bands with lumpy iso-contour
   edges.

On the bench (synthetic corpus plus real Studi0 logos; mean CIEDE2000 ΔE,
lower is better):

| class | Potrace | VTracer | **Vexel** |
|---|---|---|---|
| logo | 11.7 | 1.11 | **0.33** |
| flat | 20.4 | 0.97 | **0.68** |
| gradient | 23.2 | 10.40 | **0.89** |
| shadow | 14.8 | 3.24 | **0.44** |

Vexel has the lowest ΔE on **65 of 69** corpus items, and gets there with far
less geometry: 6.0 paths and 5.3 KB per image on average against VTracer's 23.3
paths and 12.4 KB. It is pure Python, averaging ~0.9 s an image.

Reproduce it yourself — the corpus is generated, not shipped:

```bash
cd backend
python -m bench generate            # rebuild the corpus from bench/synth.py
python -m bench run                 # every registered engine
```

Design and research notes: `docs/superpowers/specs/2026-09-17-vexel-engine-design.md`.

## Run it

Backend (needs the `potrace` binary: `brew install potrace` / `apt install potrace`):

```bash
cd backend
uv venv .venv && uv pip install -p .venv/bin/python -e '.[dev]'
.venv/bin/uvicorn studi0trace.main:app --reload        # http://localhost:8000
```

Frontend:

```bash
cd frontend
npm ci
npm run dev                                            # http://localhost:5173
```

`frontend/.env.local` points the app at `http://localhost:8000`.

## Tests

```bash
cd backend && .venv/bin/python -m pytest
cd frontend && npm run test:run
```

The backend suite includes a concurrency test (two 0.4 s traces must finish in
< 0.7 s) and asserts CORS headers on every error path, including 500s.

## API

| Route | Purpose |
|---|---|
| `GET /health` | `{status, version, engines}` |
| `GET /engines` | Each engine's `id`, `label`, `description`, JSON Schema `params` and `defaults`. The UI renders every control from this — adding an engine or a parameter needs no frontend change. |
| `GET /presets` | Named parameter bundles: `id`, `label`, `engine`, `description`, `detail` (what it measurably costs, from the bench) and `params`. A preset layers over the engine's **defaults**, never over current values. |
| `POST /uploads` | multipart `file` → `{image_id, width, height, format}`. Validated once and kept server-side (LRU, sliding 30 min TTL) so re-tracing while tuning doesn't re-send the file. |
| `POST /vectorize` | multipart: `image_id` **or** `file`, `parameters` (JSON keyed by engine id), `engines` (comma list; default all). Returns `results.{engine}.{svg, elapsed_ms, stats | error}`, `image_id`, `width`, `height`. Expired id → 404 `image_expired`; the client re-uploads and retries once. |

Uploads are sniffed with Pillow (client `Content-Type` is ignored), limited by
`MAX_UPLOAD_BYTES` (20 MB) and `MAX_IMAGE_PIXELS` (40 MP), and normalised to
RGBA — transparency reaches every engine. Errors carry a stable `code`.
Env: `ALLOWED_ORIGINS` (comma list), `MAX_UPLOAD_CACHE_BYTES` (256 MB),
`UPLOAD_TTL_SECONDS` (1800). The upload cache is per process; with several
workers the client's re-upload fallback keeps things correct, just slower.

## Frontend

Single workspace: drop / paste / browse an image (or pick a sample), then tune.
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

The backend is a single container: `backend/Dockerfile` installs potrace,
builds the wheel and runs uvicorn. It is stateless apart from an in-memory
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
pays 30–50 s of cold start, and the shared CPU makes a 512 px trace take
seconds rather than the ~0.9 s it takes locally. Taking the backend off the
free instance type is the single change that fixes both; nothing else about the
deployment needs to move.

## Design docs

`docs/superpowers/specs/` holds the approved designs; `docs/superpowers/plans/`
the implementation plans.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: a tracing quality
change is not an improvement until `python -m bench compare` says so, and
baselines move only deliberately.

## Licence

MIT — see [LICENSE](LICENSE).
