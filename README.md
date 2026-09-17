# Studi0Trace

Raster → SVG tracing with pluggable engines, and **Vexel Bench**, a fidelity
scoreboard that says which trace is more true to the source image.

```
backend/   FastAPI service + engines + bench     (Python ≥ 3.12)
frontend/  Vue 3 single-page app                 (Node ≥ 18)
```

Engines today: **Potrace** (1-bit outlines) and **VTracer** (colour layers).
**Vexel**, our own fidelity-first engine, plugs into the same seam.

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
| `GET /engines` | Each engine's `id`, `label`, `description`, JSON Schema `params` and `defaults`. UIs render controls from this. |
| `POST /vectorize` | multipart: `file`, `parameters` (JSON keyed by engine id), `engines` (comma list; default all). Returns `results.{engine}.{svg, elapsed_ms, stats | error}` plus `original_image`, `width`, `height`. |

Uploads are sniffed with Pillow (client `Content-Type` is ignored), limited by
`MAX_UPLOAD_BYTES` (20 MB) and `MAX_IMAGE_PIXELS` (40 MP), and normalised to
RGBA — transparency reaches every engine. `ALLOWED_ORIGINS` is a comma list.

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

`render.yaml` defines two Render services: the backend from
`backend/Dockerfile`, the frontend as a static site. `docker build backend`
produces a self-contained image with potrace installed.

## Design docs

`docs/superpowers/specs/` holds the approved designs; `docs/superpowers/plans/`
the implementation plans.
