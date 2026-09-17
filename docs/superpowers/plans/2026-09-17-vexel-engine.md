# Vexel v0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `vexel` as a third registered engine that beats VTracer on gradient/shadow fidelity and matches it on logos/flat art, verified by the bench.

**Architecture:** Pure array-in/array-out modules under `studi0trace/engines/vexel/` (prepare → partition → stats/merge → fills → order → boundary → curves → svg), orchestrated by `VexelEngine.trace()`. Merging uses closed-form least squares from region moment statistics.

**Tech Stack:** numpy, scipy (ndimage, linalg, sparse not needed), scikit-image (color, segmentation.watershed, measure.find_contours/EllipseModel, filters.scharr), pytest, bench.

Spec: `docs/superpowers/specs/2026-09-17-vexel-engine-design.md`

## Global Constraints
- numpy/scipy/scikit-image become core deps (move from `[bench]` extras to `dependencies`).
- No module under `vexel/` imports FastAPI or the registry except `engine.py`/`__init__.py`.
- Pixel-centre convention: pixel (i, j) centre = (j+0.5, i+0.5); contours from `find_contours` are (row, col) → SVG (col+0.5, row+0.5).
- Colour fits in sRGB 0–255 + α 0–1; merge features in Lab.
- Every stage must handle a fully transparent image and a 1-region image without crashing.
- Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`

---

### Task 1: prepare + partition
Files: `vexel/__init__.py` (empty for now), `vexel/prepare.py`, `vexel/partition.py`, `tests/test_vexel_prepare.py`.
- `prepare(rgba: uint8[H,W,4]) -> Prepared(rgb f32[H,W,3], alpha f32[H,W], lab f32[H,W,3], features f32[H,W,4])` with alpha inpainting via `ndimage.distance_transform_edt(alpha==0, return_indices=True)`.
- `discontinuity(features) -> f32[H,W]`; `initial_labels(grad, min_region, g_low) -> int32[H,W]` (1-based labels, watershed with markers from low-gradient components; tiny regions absorbed into most similar neighbour by Lab mean).
- Tests: transparent corner gets inpainted RGB; two flat halves → exactly 2 labels; flat image → 1 label; gradient image → labels ≥ 1 and no label spans a hard edge.
- Commit `feat(vexel): prepare and discontinuity-bounded partition`.

### Task 2: stats + merge
Files: `vexel/stats.py`, `vexel/merge.py`, `tests/test_vexel_merge.py`.
- `RegionStats` as a numpy vector: moments M = Σ [1,x,y,x²,xy,y²,x³,x²y,xy²,y³,x⁴,x³y,x²y²,xy³,y⁴] (15) and per-channel C_c = Σ c·[1,x,y,x²,xy,y²] (6×4) and Σc² (4). `accumulate(labels, xs, ys, colours) -> dict[label, stats]` via `np.add.at`. `sse_solid/sse_planar/sse_quadratic(stats)` from normal equations (guard singular with lstsq/pinv). `cost(stats, lam, gradients)`.
- `merge_regions(labels, stats, features, params) -> labels` with adjacency from label pairs across horizontal/vertical neighbours, boundary gradient sums per pair, heap of Δ, union-find, relabel compact.
- Tests: exact planar image SSE_planar ≈ 0 while SSE_solid large; union stats equality; two flat halves 6 ΔE apart stay 2 regions at detail 8 and merge at detail 30; linear-gradient disc over flat bg → 2 regions; speckle of 3 px absorbed.
- Commit `feat(vexel): moment statistics and model-aware region merging`.

### Task 3: fills
Files: `vexel/fills.py`, `tests/test_vexel_fills.py`.
- `@dataclass Solid(rgba)`, `Linear(x1,y1,x2,y2, stops:[(t, rgba)])`, `Radial(cx,cy,r, stops)`, each with `evaluate(xs, ys) -> rgba[N,4]` and `to_svg(id) -> (defs, fill_attr)`.
- `fit_fill(xs, ys, rgba, params) -> Fill` per spec §4 (ramp fitting with knot insertion).
- Tests: recovered direction/colours/centre within tolerances; flat → Solid; alpha ramp → stop-opacity varies.
- Commit `feat(vexel): solid, linear and radial fill reconstruction`.

### Task 4: order + boundary
Files: `vexel/order.py`, `vexel/boundary.py`, `tests/test_vexel_boundary.py`.
- `paint_order(labels) -> list[label]`, `shape_mask(labels, label, stacked) -> bool[H,W]` (holes filled when stacked).
- `coverage_field(rgb, alpha, labels, label, fills) -> f32[H,W]`; `contours(field) -> list[np.ndarray (N,2) xy]` (outer + holes, closed, ≥ 4 points; discard tiny).
- Tests: enclosed disc ordered after its background; stacked mask of the background is full frame; resvg-rendered anti-aliased circle r=20.3 → mean radius error < 0.15 px; 1-px-wide stroke yields a closed contour.
- Commit `feat(vexel): painter ordering and sub-pixel coverage contours`.

### Task 5: curves
Files: `vexel/curves.py`, `tests/test_vexel_curves.py`.
- `find_corners(poly, threshold_deg) -> list[int]`; `fit_circle/fit_ellipse/fit_rect`; `fit_segment(points, tol) -> list[Cubic|Line]` (Schneider); `merge_cubics`; `snap_axis_lines`; `to_path_d(elements, precision)`; `fit_shape(poly, params) -> ShapeElem | PathElem`.
- Tests per spec.
- Commit `feat(vexel): corner detection, Bezier/line fitting, whole-shape fitting`.

### Task 6: svg + engine + registry + API
Files: `vexel/svg.py`, `vexel/engine.py`, `vexel/__init__.py`, `engines/registry.py` (load_builtin imports vexel), `pyproject.toml` deps, `tests/test_vexel_engine.py`, `tests/test_api.py` (three engines).
- Commit `feat(vexel): engine assembly and registration`.

### Task 7: bench gate and tuning
- `python -m bench run --engines potrace,vtracer,vexel --label vexel-v0`; iterate on defaults with `bench sweep --engine vexel --param detail=4:16:2 --param curve_tolerance=0.25,0.4,0.6`; commit `bench/baselines/vexel.json`; append observations to the spec; browser check in Studi0Trace (Vexel tab appears automatically).
- Commit `feat(vexel): v0 baseline`.
