# Studi0Trace — Foundation & Vexel Bench (Projects A + B)

**Date:** 2026-09-16
**Status:** Approved for implementation (Section 1 reviewed with Tim; Sections 2–3 written under his instruction to proceed autonomously)

## Context

Tracer is a raster→SVG web app: FastAPI backend wrapping Potrace and VTracer, Vue 3 SPA frontend, deployed on Render. It is being grown into **Studi0Trace**, whose centrepiece will be **Vexel** — a from-scratch tracing engine whose objective is fidelity to the source image, targeting **logos/icons/marks, flat illustrations, and illustrations with complex gradients and shadowing**. Vexel is prototyped in Python, with a later Rust port planned.

The whole effort is four projects: **A** foundation + engine seam, **B** Vexel Bench (fidelity evaluation), **C** Studi0Trace frontend on the Studi0 design system, **D** Vexel itself. This spec covers A and B. C and D get their own specs.

### Problems in the current code this spec addresses

- `subprocess.run` and `vtracer.convert_image_to_svg_py` are called synchronously inside `async def` handlers — one request stalls the whole server.
- `main.py` is a 529-line monolith; ~110 lines are triple-redundant CORS enforcement.
- Parameter validation is hand-rolled twice; the frontend hardcodes every control.
- VTracer path converts to RGB, destroying transparency (bad for logos).
- `content_type` is trusted from the client; no decompression-bomb guard.
- 9,691 of 9,767 tracked files are `node_modules`; `dist`, `__pycache__`, `.DS_Store`, `vtracer.zip` and ~12 ad-hoc scripts are committed. No `.gitignore`.
- There is no way to measure whether one trace is better than another.

## Section 1 — Project A: Foundation + Engine Seam

### Layout

`vectorizer-app/` is flattened into the repo root (it is the only thing in the repo). History preserved with `git mv`.

```
tracer/
  backend/
    studi0trace/
      __init__.py
      main.py             # create_app(): CORS, exception handler, mount routes
      settings.py         # Settings from env: ALLOWED_ORIGINS, MAX_UPLOAD_BYTES, MAX_IMAGE_PIXELS
      api/
        routes.py         # GET /health  GET /engines  POST /vectorize
        schemas.py        # response models
      engines/
        base.py           # Engine protocol, TraceInput, TraceResult, EngineError
        registry.py       # get(id), all(), register()
        potrace.py        # PotraceEngine + PotraceParams
        vtracer.py        # VTracerEngine + VTracerParams
      imaging/
        intake.py         # load_upload(bytes) -> TraceInput (sniff, guard, RGBA)
        svg.py            # normalize_dimensions(svg, w, h); svg_stats(svg)
    bench/                # Project B (Section 2)
    tests/
    pyproject.toml        # project metadata, deps, optional [bench] and [dev] extras
    Dockerfile
  frontend/               # existing Vue app, unchanged except where noted
  render.yaml             # paths updated
  .gitignore
  README.md               # replaces CLAUDE.md sprawl + DEPLOYMENT_FIX.md + keep-alive.md
  CLAUDE.md               # short: points at README, records conventions
  docs/superpowers/specs/
```

### Engine interface (`engines/base.py`)

```python
@dataclass(frozen=True)
class TraceInput:
    image: PIL.Image.Image     # always RGBA
    width: int
    height: int
    source_bytes: bytes        # original upload, for engines that want it
    source_format: str         # "PNG" | "JPEG" | "GIF" | "WEBP" ...

@dataclass(frozen=True)
class TraceResult:
    svg: str                   # viewBox-normalised, no width/height attrs
    elapsed_ms: float
    stats: SvgStats            # paths, nodes, bytes, gradients, unique_fills

class Engine(Protocol):
    id: str                    # "potrace" | "vtracer" | later "vexel"
    label: str
    description: str
    Params: type[pydantic.BaseModel]
    def trace(self, image: TraceInput, params: pydantic.BaseModel) -> TraceResult: ...

class EngineError(Exception): ...   # engine-level failure with a user-safe message
```

Rules:
- `trace()` is **synchronous and CPU-bound**. Callers that need concurrency run it off the event loop (the API uses `anyio.to_thread.run_sync`). Engines never touch asyncio.
- Each `Params` model is the single source of truth for names, types, bounds, defaults and human labels (`Field(description=...)`, plus `json_schema_extra={"ui": {...}}` for control hints such as `slider`, `select`, `toggle`, `step`, `unit`). Its JSON Schema is what `GET /engines` returns.
- Engines receive RGBA. Potrace flattens onto white then thresholds to 1-bit (as today, plus a **threshold** param — the biggest missing quality knob for B&W tracing). VTracer receives RGBA PNG bytes with alpha intact.
- Engines return SVG already passed through `imaging.svg.normalize_dimensions`.
- The registry is a plain module-level dict populated at import; Vexel registers itself the same way.

### Potrace engine

Params (Pydantic): `threshold: int = 128 (0–255)`, `invert: bool = False`, `turdsize: int = 2 (0–100)`, `turnpolicy: Literal[...] = "minority"`, `alphamax: float = 1.0 (0–1.3334)`, `opticurve: bool = True`, `opttolerance: float = 0.2 (0–1)`. The current `alphamax` bound of 2.0 is wrong (Potrace's max is 4/3); corrected.

Implementation ports the existing subprocess flow; temp files via `tempfile.TemporaryDirectory`; `--flat` is not used (keep groups); non-zero exit → `EngineError` including stderr.

### VTracer engine

Params: `colormode: Literal["color","binary"] = "color"`, `hierarchical: Literal["stacked","cutout"] = "stacked"`, `mode: Literal["spline","polygon","none"] = "spline"`, `filter_speckle: int = 4 (0–128)`, `color_precision: int = 6 (1–8)`, `layer_difference: int = 16 (0–255)`, `corner_threshold: int = 60 (0–180)`, `length_threshold: float = 4.0 (3.5–10)`, `max_iterations: int = 10 (1–100)`, `splice_threshold: int = 45 (0–180)`, `path_precision: int = 3 (1–10)`. Ranges follow the VTracer documentation, not the current guesses. Implementation writes RGBA PNG to a temp dir and calls `vtracer.convert_image_to_svg_py`; the debugging scaffolding around file permissions is dropped (it was a Render/JPEG artefact, cured by always writing PNG).

### Image intake (`imaging/intake.py`)

`load_upload(data: bytes, *, max_bytes, max_pixels) -> TraceInput`:
1. Size guard (`MAX_UPLOAD_BYTES`, default 20 MB) → `IntakeError("too_large")`.
2. Format sniffed with `PIL.Image.open` + `img.format`; allowlist PNG, JPEG, GIF, WEBP, BMP. Client `content_type` is ignored.
3. `PIL.Image.MAX_IMAGE_PIXELS` set from settings (default 40 MP) and `DecompressionBombError` mapped to `IntakeError("too_many_pixels")`.
4. Animated GIF → first frame. Convert to RGBA. EXIF orientation applied for JPEG.

### API

- `GET /health` → `{"status":"ok","engines":[...ids]}`.
- `GET /engines` → `[{id, label, description, params: <JSON Schema>, defaults: {...}}]`.
- `POST /vectorize` (multipart): `file`, `parameters` (JSON, keyed by engine id), `engines` (comma list, optional), `selected_method` (legacy alias for a single engine). Default runs all engines. Each engine runs in the threadpool; engines run concurrently with each other via `anyio` task group.
  Response:
  ```json
  {
    "success": true,
    "original_image": "data:image/png;base64,...",
    "width": 512, "height": 512,
    "results": {
      "potrace": {"svg": "...", "elapsed_ms": 41.2, "stats": {...}},
      "vtracer": {"error": {"code": "engine_failed", "message": "..."}}
    },
    "vectorized": {"potrace": "<svg…>", "vtracer": "Error: …"},   // legacy, removed in Project C
    "parameters_used": {...}
  }
  ```
  Parameter validation errors → 422 with Pydantic's error list. Intake errors → 400 with `{"detail": {"code", "message"}}`.
- CORS: standard `CORSMiddleware` from `ALLOWED_ORIGINS`. One `Exception` handler returns JSON 500; a test asserts 4xx/5xx responses carry `Access-Control-Allow-Origin`.

### Frontend (compat only)

`App.vue` keeps working unchanged against the legacy `vectorized` field. The only edit: the `.env`/build path updates that come with flattening. Full rewrite is Project C.

### Hygiene

- Untrack and ignore: `node_modules/`, `dist/`, `__pycache__/`, `.DS_Store`, `*.pyc`, `venv/`, `.venv/`, `bench/reports/`.
- Delete: `test_backend.html`, `test_backend.py`, `DEPLOYMENT_FIX.md`, `keep-alive.md`, `vectorizer-app/debug_frontend_api.html`, `debug_turnpolicy.py`, `simple_fillcolor_test.py`, `test_ambiguous_turnpolicy.py`, `test_color_modes.py`, `test_current_app.py`, `test_parameter_api.py`, `test_turnpolicy.py`, `test_vtracer_integration.py`, `backend/{api_parameter_test,comprehensive_parameter_test,debug_parameters,quick_parameter_test,test_parameter_bug_fixes,test_parameter_validation,visual_parameter_validation}.py`, `backend/parameter_validation_report.md`, `backend/test_vtracer_output.svg`, `backend/vtracer.zip`, `backend/requirements*.txt` (superseded by `pyproject.toml`), `backend/run_tests.py`, `run_all_tests.sh`, `vectorizer-app/CLAUDE.md`, `vectorizer-app/TESTING.md`.
- `opencv-python` and `aiofiles` are dropped from deps (unused).
- Dockerfile: `python:3.12-slim`, `apt install potrace`, `pip install .` — the Pillow rebuild dance is unnecessary with wheels.

### Out of scope for A

Hosting/cold-start strategy (cost decision, raised in C), job queue, frontend features, SVG post-processing (SVGO etc. — belongs with Vexel/D once the bench can measure its effect).

## Section 2 — Project B: Vexel Bench

### Purpose

Answer "is this trace more true to the image than that one?" with numbers, for any `Engine`, over a corpus that represents Vexel's targets. It is the scoreboard Vexel is built against, the regression gate for the later Rust port ("scores identically, faster"), and an immediate tool for tuning Potrace/VTracer defaults empirically.

### Layout

```
backend/bench/
  __init__.py
  __main__.py          # python -m bench <command>
  cli.py               # argparse: generate | run | compare | sweep | report
  corpus.py            # Corpus/Item loading from manifest
  synth.py             # deterministic synthetic corpus generator
  raster.py            # rasterize(svg, w, h) -> RGBA ndarray via resvg-py
  metrics.py           # every metric as a pure function on ndarrays / svg text
  runner.py            # run engines over corpus -> Results
  report.py            # results.json -> index.html
  config.py            # composite weights, class lists
  corpus/
    manifest.yaml
    synthetic/<class>/<name>.{svg,png}    # generated, committed (small)
    real/<class>/<name>.png               # hand-collected, optional truth .svg
  baselines/
    potrace.json
    vtracer.json
  reports/             # gitignored
```

### Corpus

Classes: `logo`, `flat`, `gradient`, `shadow`. Each item: `id`, `class`, `png` path, optional `truth_svg`, `tags`.

**Synthetic generator (`synth.py`)** renders template SVGs through resvg at 512×512 (and a 128×128 variant of each for speed tests) with a fixed seed, so ground truth is exact and the corpus is licence-free and reproducible:
- `logo`: 2–4 solid shapes (circles, rounded rects, polygons, a letterform path) in 2–3 brand colours, sharp corners, transparent background, some with 1-px-thin features.
- `flat`: 6–12 overlapping flat regions, adjacent colours with low contrast, small details.
- `gradient`: linear and radial multi-stop gradients across shapes, some with 2 stops, some 4+; one full-frame ramp.
- `shadow`: shapes with `feGaussianBlur` drop shadows at several radii and alpha; shapes over a gradient background.
Initial corpus: 6 items per class = 24 items × 2 sizes = 48 PNGs. A `real/` directory accepts hand-added images (a `manifest.yaml` entry each); no ground truth required.

### Rasterization

`raster.rasterize(svg: str, width, height) -> np.ndarray[H,W,4] uint8` via `resvg_py.svg_to_bytes`. The engine's SVG has only a viewBox, so we render at the source's pixel size. Both source and output are then compared as RGBA.

### Metrics (`metrics.py`) — all pure functions

Colour metrics are computed over white-composited RGB; alpha compared separately.

| Metric | Definition | Direction |
|---|---|---|
| `ssim` | `skimage.metrics.structural_similarity`, RGB, `channel_axis=-1` | ↑ 0–1 |
| `delta_e_mean`, `delta_e_p95` | CIEDE2000 per pixel via `skimage.color.deltaE_ciede2000` on Lab | ↓ |
| `edge_f1` | Canny on luminance of both (σ=1.0). Match within 2 px (dilate). Precision, recall, F1 | ↑ 0–1 |
| `alpha_mae` | mean |α_src − α_out| / 255 | ↓ 0–1 |
| `banding_index` | Mask **M** = source pixels whose 7×7 local luminance std is in (0.5, 6) — smooth but not flat, i.e. gradients. `banding = mean_M( max(0, \|∇²L_out\| − \|∇²L_src\|) )` where ∇² is the Laplacian. Zero when the output ramps as smoothly as the source; grows with stair-stepping. Also report `smooth_fraction = \|M\|/HW` so a tiny mask is visible. | ↓ |
| `path_count`, `node_count`, `byte_size`, `gradient_count`, `unique_fill_count` | from `imaging.svg.svg_stats` (regex/XML parse; node_count = count of path commands) | ↓ (context) |
| `path_ratio` | `path_count / truth_path_count` when truth exists | → 1 |
| `elapsed_ms` | from `TraceResult` | ↓ |

**Composite** (config-driven, reported alongside, never instead of, raw metrics):
```
fidelity   = 0.35*ssim + 0.35*(1 − clamp(delta_e_mean/20, 0, 1)) + 0.30*edge_f1
smoothness = 1 − clamp(banding_index / 8, 0, 1)
economy    = clamp(1 − log10(max(path_count,1)) / 4, 0, 1)      # 1 path→1.0, 10k paths→0
score      = 0.60*fidelity + 0.25*smoothness + 0.15*economy    # per item
```
Class aggregates are means; the headline is the per-class table, not one number. Weights live in `config.py` and are echoed into every `results.json`.

### Runner & CLI

- `python -m bench generate` — (re)build the synthetic corpus deterministically.
- `python -m bench run --engines potrace,vtracer [--classes logo,gradient] [--params '{"vtracer": {...}}'] [--label name]` → `reports/<UTC ts>-<label>/results.json` + `index.html`. Engines are called through the registry in-process; per item per engine failures are recorded, not fatal.
- `python -m bench compare <a.json> <b.json>` — per-class and per-item deltas; exit code 1 if any class `score` regresses by more than `--tolerance` (default 0.01). This is the CI gate.
- `python -m bench sweep --engine vtracer --param color_precision=3:8 [--param ...]` — grid over parameter values, reports the best per class. Uses the same runner.
- `python -m bench report <results.json>` — regenerate HTML.
- `bench/baselines/*.json` are `results.json` files committed for the two current engines; `run --update-baseline` refreshes them.

### HTML report

Static single file. Per-class summary table (all metrics), then per item: source / each engine's render / ΔE heatmap thumbnails, metrics row, expandable raw SVG size. Images inline as base64 PNG (reports are gitignored). Styled with the Studi0 tokens (Poppins, HSL variables, `.dark` support) — minimal, no framework.

### Section 3 — Testing, verification, rollout

**Tests (pytest, `backend/tests/`)**
- `test_intake.py`: real PNG bytes with wrong content-type accepted; PNG magic + garbage rejected; >max_bytes rejected; decompression bomb rejected; RGBA preserved; animated GIF → first frame; EXIF-rotated JPEG orientation applied.
- `test_svg.py`: `normalize_dimensions` strips width/height, sets viewBox, idempotent; `svg_stats` counts paths/nodes/gradients on hand-written SVGs.
- `test_engines.py`: each registered engine traces a generated fixture (black square on transparent; a two-colour gradient) → parses as XML, root is `<svg>`, viewBox equals source size, no width/height attributes, `stats.paths ≥ 1`. Potrace `threshold`/`invert` change output. Param models reject out-of-range values.
- `test_api.py`: `/engines` schema shape; `/vectorize` happy path both engines; `selected_method` legacy alias; 422 on bad params; 400 on bad image; CORS header present on 400/422/500; **concurrency**: a stub engine that sleeps 0.4 s registered for the test — two simultaneous requests finish in < 0.7 s wall (proves off-loop execution).
- `test_bench_metrics.py`: identical images → ssim 1, ΔE 0, edge_f1 1, banding 0; inverted image → ssim low, ΔE high; smooth ramp vs 8-step posterised ramp → banding_index of posterised ≫ smooth; `path_count` etc. on fixtures.
- `test_bench_runner.py`: `generate` produces the manifest count; `run` over a 2-item subset with both engines yields results.json with expected keys and an index.html; `compare` exit codes.

**Manual verification gates**
1. Vue frontend (unchanged) runs against the new backend in the in-app browser: upload PNG and JPG, switch methods, drag sliders, download — before and after screenshots.
2. `docker build` of the new Dockerfile succeeds and `/health` responds in the container.
3. Bench baseline run for both engines over the full synthetic corpus completes; results are sanity-checked (VTracer should beat Potrace on `gradient` ΔE; Potrace should have far fewer paths on `logo`).

**Rollout order** (each step committed, tests green):
1. Hygiene: `.gitignore`, untrack `node_modules`/`dist`/`__pycache__`, delete listed files.
2. `git mv vectorizer-app/{backend,frontend} .`, update `render.yaml`, `.env` paths; confirm `npm run build` still works.
3. Package skeleton + `pyproject.toml`; `imaging/` with tests.
4. `engines/` (base, potrace, vtracer, registry) with tests; delete `main.py` monolith.
5. `api/` + `main.py` app factory with tests incl. concurrency and CORS.
6. Frontend compat verification in browser; Dockerfile; README/CLAUDE.md.
7. Bench: raster + metrics with tests → synth + corpus → runner + CLI → report → baselines committed.

## Decisions log

- **Rasterizer: resvg-py** (pure wheel, verified rendering gradients correctly on Python 3.13 locally). cairosvg rejected: needs system Cairo on the dyld path on macOS.
- **Python 3.12 in Docker, 3.13 locally**; `pyproject` requires `>=3.12`.
- **Pydantic params over hand-rolled validators**: one definition drives validation, defaults, JSON Schema, and (in C) the rendered controls.
- **Threadpool, not job queue**: cures the stall; queue deferred until a trace routinely exceeds ~10 s.
- **Legacy `vectorized` field kept** until Project C removes the Vue app.
- **Studi0 design token note for C:** `studi0mail`'s `.sidebar nav a.active` uses `box-shadow: inset 3px 0` — a partial-edge accent. Tim's global rule forbids that pattern; Studi0Trace will use the approved alternatives (dot / ring / tint / type shift) while keeping the tokens.
