# Foundation + Vexel Bench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Tracer monolith into the `studi0trace` package with a formal `Engine` seam, a non-blocking API, clean repo hygiene, and a `bench` package that scores any engine's fidelity over a synthetic corpus.

**Architecture:** FastAPI app factory → `api/routes.py` → `engines.registry` → `Engine.trace()` run in `anyio` threads. `imaging/` owns decode and SVG normalization. `bench/` imports the same registry, rasterizes SVG with resvg-py, computes pure-function metrics, writes JSON + HTML reports, and pins baselines.

**Tech Stack:** Python ≥3.12, FastAPI, Pydantic v2, Pillow, vtracer, potrace CLI, numpy, scikit-image, resvg-py, pytest, httpx. Frontend untouched (Vue 3 / Vite 4).

Spec: `docs/superpowers/specs/2026-09-16-foundation-and-vexel-bench-design.md`

## Global Constraints

- Package name `studi0trace`; bench package `bench`; both under `backend/`.
- `Engine.trace()` is synchronous; only `api/` may touch asyncio/anyio.
- Every engine `Params` is a Pydantic model; JSON Schema from it is the API contract for `GET /engines`.
- Engines receive RGBA and return viewBox-only SVG (no width/height attrs).
- Legacy response field `vectorized: {engine: svg | "Error: ..."}` must keep working for the current Vue app.
- Limits from env: `ALLOWED_ORIGINS` (default `http://localhost:5173`), `MAX_UPLOAD_BYTES` (20 MB), `MAX_IMAGE_PIXELS` (40 MP).
- Potrace `alphamax` max is 1.3334, not 2.0. VTracer ranges per its docs (see spec).
- Rasterizer is resvg-py. No cairo.
- Commit after every task; tests green before each commit.
- Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`

---

## File Structure

```
backend/
  pyproject.toml                    # Task 3
  studi0trace/__init__.py           # Task 3
  studi0trace/settings.py           # Task 3
  studi0trace/imaging/__init__.py   # Task 3
  studi0trace/imaging/intake.py     # Task 3  load_upload()
  studi0trace/imaging/svg.py        # Task 4  normalize_dimensions(), svg_stats()
  studi0trace/engines/__init__.py   # Task 5
  studi0trace/engines/base.py       # Task 5  TraceInput, TraceResult, SvgStats, Engine, EngineError
  studi0trace/engines/registry.py   # Task 5
  studi0trace/engines/potrace.py    # Task 6
  studi0trace/engines/vtracer.py    # Task 7
  studi0trace/api/__init__.py       # Task 8
  studi0trace/api/schemas.py        # Task 8
  studi0trace/api/routes.py         # Task 8
  studi0trace/main.py               # Task 8  create_app(), app
  Dockerfile                        # Task 9
  tests/conftest.py                 # Task 3 (fixtures: png bytes makers)
  tests/test_intake.py              # Task 3
  tests/test_svg.py                 # Task 4
  tests/test_engines.py             # Tasks 5–7
  tests/test_api.py                 # Task 8
  bench/__init__.py, __main__.py, cli.py            # Task 13
  bench/raster.py                   # Task 10
  bench/metrics.py                  # Task 10
  bench/config.py                   # Task 10
  bench/synth.py, corpus.py         # Task 11
  bench/runner.py                   # Task 12
  bench/report.py                   # Task 12
  bench/corpus/manifest.yaml + synthetic/   # Task 11 (generated, committed)
  bench/baselines/{potrace,vtracer}.json    # Task 14
  tests/test_bench_metrics.py       # Task 10
  tests/test_bench_runner.py        # Task 12
frontend/                            # Task 2 (moved)
render.yaml, .gitignore, README.md, CLAUDE.md   # Tasks 1, 2, 9
```

---

### Task 1: Repo hygiene

**Files:** Create `.gitignore`; delete files listed in spec §Hygiene; untrack `node_modules`, `dist`, `__pycache__`, `.DS_Store`.

- [ ] Write `.gitignore` (node_modules/, dist/, __pycache__/, *.pyc, .DS_Store, venv/, .venv/, backend/bench/reports/, .env.*.local, coverage/, .pytest_cache/, htmlcov/).
- [ ] `git rm -r --cached` the junk; `git rm` the listed ad-hoc files and docs.
- [ ] Verify: `git ls-files | wc -l` drops from 9767 to < 120; `git ls-files | grep -c node_modules` is 0.
- [ ] Commit: `chore: add .gitignore, untrack node_modules/dist, remove ad-hoc scripts`.

### Task 2: Flatten `vectorizer-app/` to repo root

**Files:** `git mv vectorizer-app/backend backend`, `git mv vectorizer-app/frontend frontend`, `git mv vectorizer-app/render.yaml render.yaml`; edit `render.yaml` paths (`./backend/Dockerfile`, `dockerContext: ./backend`, `buildCommand: cd frontend && …`, `staticPublishPath: frontend/dist`).

- [ ] Move; fix `render.yaml`; delete empty `vectorizer-app/`.
- [ ] Verify: `cd frontend && npm ci && npm run build` succeeds (dist is ignored).
- [ ] Commit: `chore: flatten vectorizer-app into repo root`.

### Task 3: Package skeleton, settings, image intake

**Files:** `backend/pyproject.toml`, `studi0trace/__init__.py`, `settings.py`, `imaging/intake.py`, `tests/conftest.py`, `tests/test_intake.py`.

**Produces:**
```python
# settings.py
class Settings(BaseModel):
    allowed_origins: list[str]; max_upload_bytes: int; max_image_pixels: int
def get_settings() -> Settings   # reads env, cached
# imaging/intake.py
class IntakeError(Exception): code: str; message: str
def load_upload(data: bytes, *, max_bytes: int, max_pixels: int) -> TraceInput
```
`TraceInput` lives in `engines/base.py` (Task 5) — Task 3 defines it there early with only the dataclass, so `intake` can import it.

- [ ] `pyproject.toml`: name `studi0trace`, `requires-python >=3.12`, deps `fastapi, uvicorn[standard], python-multipart, pillow, vtracer, pydantic>=2, anyio`; extras `bench = [numpy, scikit-image, resvg-py, pyyaml]`, `dev = [pytest, httpx, pytest-cov] + bench`; `[tool.pytest.ini_options] testpaths=["tests"]`; setuptools packages `studi0trace*`, `bench*`.
- [ ] `uv venv backend/.venv && uv pip install -e 'backend[dev]'`.
- [ ] Failing tests (`test_intake.py`): `make_png(w,h,mode,color)` fixture in conftest.
  - `test_accepts_png_regardless_of_content_type` → `load_upload(png).image.mode == "RGBA"`, `.source_format == "PNG"`.
  - `test_rejects_garbage_with_png_magic` → `IntakeError.code == "unsupported_format"`.
  - `test_rejects_oversize` → `code == "too_large"`.
  - `test_rejects_decompression_bomb` (2000×2000 with max_pixels=1_000_000) → `code == "too_many_pixels"`.
  - `test_preserves_alpha` (RGBA png with alpha 0 corner) → pixel alpha 0 survives.
  - `test_animated_gif_first_frame` → 2-frame GIF, result matches frame 0.
  - `test_jpeg_exif_orientation` → JPEG with orientation 6 tag, 30×10 → result 10×30.
- [ ] Implement `intake.py`; run tests → green.
- [ ] Commit: `feat(imaging): add image intake with sniffing, guards, RGBA normalization`.

### Task 4: SVG utilities

**Files:** `imaging/svg.py`, `tests/test_svg.py`.

**Produces:**
```python
def normalize_dimensions(svg: str, width: int, height: int) -> str
@dataclass(frozen=True) class SvgStats: paths:int; nodes:int; bytes:int; gradients:int; unique_fills:int
def svg_stats(svg: str) -> SvgStats
```
(`SvgStats` defined here; `engines/base.py` re-exports it.)

- [ ] Tests: strips width/height; sets viewBox when missing; replaces existing viewBox; idempotent; preserves the rest byte-for-byte. `svg_stats`: hand-written SVG with 3 paths (one `d="M0 0L1 1C…Z"` → nodes count = commands), 1 linearGradient + 1 radialGradient, fills `#fff`, `#fff`, `red` → `unique_fills == 2`. Regex-based; `<path>` in `<defs>` still counts (documented).
- [ ] Implement; green; commit `feat(imaging): svg dimension normalization and stats`.

### Task 5: Engine base + registry

**Files:** `engines/base.py`, `engines/registry.py`, `tests/test_engines.py` (registry part).

**Produces:**
```python
@dataclass(frozen=True) class TraceInput: image: Image.Image; width:int; height:int; source_bytes: bytes; source_format: str
@dataclass(frozen=True) class TraceResult: svg: str; elapsed_ms: float; stats: SvgStats
class EngineError(Exception): ...
class Engine(Protocol): id:str; label:str; description:str; Params: type[BaseModel]; def trace(self, image: TraceInput, params: BaseModel) -> TraceResult
def finish(svg_raw: str, image: TraceInput, started: float) -> TraceResult   # normalize + stats + timing helper
# registry.py
def register(engine: Engine) -> Engine; def get(engine_id: str) -> Engine (KeyError→ raises UnknownEngine); def all() -> list[Engine]; def ids() -> list[str]
def describe(engine: Engine) -> dict   # {id,label,description,params: Params.model_json_schema(), defaults: Params().model_dump()}
```
- [ ] Tests: register a `FakeEngine` (returns a fixed SVG) → `get`, `all`, `ids`, `describe()["defaults"]`, unknown id raises.
- [ ] Implement; green; commit `feat(engines): Engine protocol, TraceInput/Result, registry`.

### Task 6: Potrace engine

**Files:** `engines/potrace.py`, `tests/test_engines.py`.

Params: `threshold:int=128 (0–255)`, `invert:bool=False`, `turdsize:int=2 (0–100)`, `turnpolicy: Literal["black","white","left","right","minority","majority","random"]="minority"`, `alphamax: float=1.0 (0–1.3334)`, `opticurve: bool=True`, `opttolerance: float=0.2 (0–1)`. `json_schema_extra={"ui": {"control": "slider"}}` etc.
Flow: RGBA → composite onto white → `L` → `point(lambda v: 255 if v > threshold else 0)` (invert flips) → `1` mode BMP in `TemporaryDirectory` → `potrace -s --svg -o out.svg --turdsize --turnpolicy --alphamax [--longcurve] [--opttolerance]` → read → `finish()`. Missing binary → `EngineError("potrace binary not found")`.

- [ ] Tests (skip if `shutil.which("potrace")` is None): black square on transparent → valid XML `<svg>` root, viewBox `0 0 W H`, no width/height attrs, `stats.paths >= 1`; `invert=True` output differs; `threshold=250` on a mid-grey square yields paths, `threshold=10` yields none (`stats.paths == 0`); `PotraceParams(alphamax=2.0)` raises `ValidationError`; registered id `potrace`.
- [ ] Implement; green; commit `feat(engines): Potrace engine with Pydantic params and threshold control`.

### Task 7: VTracer engine

**Files:** `engines/vtracer.py`, `tests/test_engines.py`.

Params per spec ranges. Flow: RGBA PNG → temp dir → `vtracer.convert_image_to_svg_py(in, out, colormode=..., hierarchical=..., mode=..., filter_speckle=..., color_precision=..., layer_difference=..., corner_threshold=..., length_threshold=..., max_iterations=..., splice_threshold=..., path_precision=...)` → read → `finish()`.

- [ ] Tests: red/blue two-colour image → valid SVG, viewBox correct, `unique_fills >= 2`; transparent-background logo keeps alpha (rasterize output with resvg → corner pixel alpha == 0 — this is the alpha regression test); `VTracerParams(color_precision=9)` raises; `colormode="binary"` → `unique_fills <= 2`.
- [ ] Implement; green; commit `feat(engines): VTracer engine with alpha-preserving intake`.

### Task 8: API + app factory

**Files:** `api/schemas.py`, `api/routes.py`, `main.py`, `tests/test_api.py`. Delete old `backend/main.py`, `backend/tests/{conftest,test_*}.py` legacy suite, `pytest.ini`.

**Produces:** `create_app(settings: Settings | None = None) -> FastAPI`; module-level `app = create_app()`.
Routes: `GET /health`, `GET /engines`, `POST /vectorize` per spec. Engine runs: `async with anyio.create_task_group()` each engine → `anyio.to_thread.run_sync(engine.trace, image, params)`; exceptions captured to `{"error": {"code": "engine_failed", "message": str(e)}}`. Param parsing: `engine.Params.model_validate(params.get(engine.id, {}))` → `ValidationError` → 422 `{"detail": e.errors()}`. `IntakeError` → 400.

- [ ] Tests (httpx `TestClient`): `/health` lists both ids; `/engines` items have `params.properties.threshold`; `/vectorize` PNG → both results have `svg` and legacy `vectorized[id]` startswith `<svg`; `selected_method=potrace` → only potrace; `engines=vtracer`; bad param → 422 with `Access-Control-Allow-Origin` header (send `Origin: http://localhost:5173`); garbage bytes → 400 with CORS header; **concurrency**: register `SleepEngine` (`time.sleep(0.4)`), two threads posting simultaneously → wall < 0.7 s; unregister after.
- [ ] Implement; green; delete legacy files; run Vue app against it in the in-app browser (upload PNG + JPG, switch method, drag a slider, download). Fix `.env.local` path if needed.
- [ ] Commit: `feat(api): non-blocking FastAPI app with /engines schema and legacy-compatible /vectorize`.

### Task 9: Dockerfile, README, CLAUDE.md

- [ ] Dockerfile: `python:3.12-slim`, `apt-get install -y --no-install-recommends potrace`, `COPY pyproject.toml studi0trace ./`, `pip install .`, `CMD uvicorn studi0trace.main:app --host 0.0.0.0 --port 8000`. `docker build` if Docker is available (else note skipped).
- [ ] README.md: what it is, run backend/frontend, tests, bench usage, engine interface in 10 lines, deployment. CLAUDE.md (root) → 30 lines pointing at README + conventions; delete stale sections.
- [ ] Commit `docs: README and slim CLAUDE.md; Dockerfile for studi0trace package`.

### Task 10: Bench raster + metrics

**Files:** `bench/__init__.py`, `bench/raster.py`, `bench/metrics.py`, `bench/config.py`, `tests/test_bench_metrics.py`.

**Produces:**
```python
def rasterize(svg: str, width: int, height: int) -> np.ndarray  # (H,W,4) uint8
def to_rgb_on_white(rgba: np.ndarray) -> np.ndarray
def ssim(a_rgb, b_rgb) -> float
def delta_e(a_rgb, b_rgb) -> tuple[float, float]           # mean, p95
def edge_f1(a_rgb, b_rgb, tolerance_px: int = 2) -> float
def alpha_mae(a_rgba, b_rgba) -> float
def banding_index(src_rgb, out_rgb) -> tuple[float, float]  # (index, smooth_fraction)
def composite(raw: dict, weights: Weights) -> dict           # fidelity, smoothness, economy, score
def all_metrics(src_rgba, out_rgba, svg: str, elapsed_ms: float, truth_paths: int | None) -> dict
```
- [ ] Tests: identical → ssim≈1, ΔE≈0, edge_f1==1, alpha 0, banding 0; inverted → ssim<0.5, ΔE mean>30; smooth horizontal ramp vs 8-step posterised ramp → `banding_index(post) > 5 * banding_index(smooth)`, smooth_fraction > 0.8; flat image → smooth_fraction ≈ 0; rasterize gradient SVG → left red, right blue; composite bounds 0–1.
- [ ] Implement; green; commit `feat(bench): resvg rasterizer and fidelity metrics incl. banding index`.

### Task 11: Synthetic corpus generator

**Files:** `bench/synth.py`, `bench/corpus.py`, `bench/corpus/manifest.yaml`, `bench/corpus/synthetic/**`.

**Produces:**
```python
@dataclass class Item: id:str; cls:str; png: Path; truth_svg: Path|None; tags: list[str]
def load_corpus(root: Path, classes: list[str]|None=None) -> list[Item]
def generate(root: Path, seed: int = 1234, sizes=(512,128)) -> list[Item]   # writes svg+png+manifest
```
Templates per class as in spec (6 each). Deterministic via `random.Random(seed)`. `truth_svg` recorded for every synthetic item; `truth_paths` counted with `svg_stats`.
- [ ] Tests: `generate(tmp)` → 48 PNGs, manifest has 48 entries, classes each 12; running twice yields identical bytes; `load_corpus(classes=["logo"])` → 12.
- [ ] Implement; run `python -m bench generate` into `bench/corpus`; commit corpus (check size < 3 MB). Commit `feat(bench): deterministic synthetic corpus (logo/flat/gradient/shadow)`.

### Task 12: Runner + report

**Files:** `bench/runner.py`, `bench/report.py`, `tests/test_bench_runner.py`.

**Produces:**
```python
def run(items: list[Item], engine_ids: list[str], params: dict[str, dict], label: str, out_dir: Path) -> Path  # results.json path
def load_results(p: Path) -> dict
def compare(a: dict, b: dict, tolerance: float) -> tuple[list[str], bool]   # lines, regressed
def write_html(results: dict, out: Path) -> None
```
`results.json`: `{label, created_utc, weights, engines: {id: {params}}, items: [{id, cls, engine, metrics{…}, error?}], summary: {engine: {cls: {metric: mean}}}}` plus per-item base64 thumbnails stored only in HTML.
- [ ] Tests: run 2 items × 2 engines into tmp → results.json keys, summary present, index.html exists and contains item ids; `compare(same, same)` → no regression; degrade one score by 0.05 → regressed.
- [ ] Implement; commit `feat(bench): runner, JSON results, HTML report, compare`.

### Task 13: CLI

**Files:** `bench/cli.py`, `bench/__main__.py`.
Commands per spec: `generate`, `run --engines --classes --params --label --update-baseline`, `compare a b --tolerance`, `sweep --engine --param name=lo:hi[:step] …`, `report results.json`.
- [ ] Smoke test via subprocess in `test_bench_runner.py`: `python -m bench run --engines potrace --classes logo --label smoke --out tmp` exits 0.
- [ ] Commit `feat(bench): CLI`.

### Task 14: Baselines + sanity

- [ ] `python -m bench run --engines potrace,vtracer --label baseline --update-baseline` → `bench/baselines/*.json`. Check: VTracer `gradient` ΔE mean < Potrace's; Potrace `logo` path_count < VTracer's. Record observations in `docs/superpowers/specs/…` "Baseline observations" appendix.
- [ ] Commit `chore(bench): initial baselines for potrace and vtracer`.

## Self-review

- Spec coverage: intake (3), svg (4), seam/registry (5), engines (6–7), API/CORS/concurrency/legacy (8), Docker/docs (9), hygiene/flatten (1–2), raster/metrics/composite (10), corpus (11), runner/report/compare (12), CLI/sweep (13), baselines (14). Frontend compat check in Task 8. ✔
- Names consistent: `load_upload`, `normalize_dimensions`, `svg_stats`, `SvgStats`, `TraceInput/TraceResult`, `registry.get/all/ids/describe/register`, `rasterize`, `banding_index`, `run/compare/write_html`. ✔
