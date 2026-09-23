# Vexel: a Rust twin for render-and-compare refinement

**Status:** built 2026-09-23 (`vexel-rs/src/refine_render.rs`, dependency `tiny-skia 0.11`). `VexelEngine.trace`
no longer routes `refine=True` to Python. Departures from the design below: shapes under a blur filter are left out
of the crop in *both* engines (tiny-skia has no filters) instead of skipping their arcs; parity is held by an
end-to-end test (Rust and Python refined traces within 0.005 px of outline error on the heart) rather than a
`refined` diffcheck stage, because the two rasterisers differ in the last grey level and a control-point tolerance
would have to be loose enough to be meaningless.

## Why

Refinement (Task 11) asks the renderer where the placement model guessed: outline error falls 14–30 % on curved
and polygonal marks. In Python it costs 5–30 % of a trace, but the Python trace itself is 3–14× slower than Rust,
so the served engine cannot offer the toggle at Rust speed, and the two engines cannot be held to parity on it.

## Design

1. **Rasteriser**: `tiny-skia` (the crate resvg itself draws with, so anti-aliasing matches the Python side's
   resvg to the pixel for solid fills). Paths: `Line` → `line_to`, `Cubic` → `cubic_to`, `Arc` → four-cubic
   approximation per 90° (error ≤ 2.7e-4·r, far below the band's 0.02 grey-level noise floor). Fill rule evenodd
   for multi-contour shapes, matching `shape_svg`.
2. **Fills**: solid colours drawn directly; linear and radial gradients through tiny-skia's shaders with the same
   stops and `userSpaceOnUse` geometry the SVG carries. Filters (shadows) are not rendered — arcs whose two shapes
   carry a filter are skipped, in both engines, so the decision set stays identical.
3. **Crop and band**: the same integer-aligned 16 px window at 4×, box-averaged to 1×, composited over white, and
   the same 2 px band from the arc's vertices (a grid search with the `symmetry::Grid` bounded lookup).
4. **Moves**: identical order and identical candidates to `refine_render.py` — nodes first (x then y, ±0.1 px,
   frame and wedge-tip nodes never), then cubic arms and joints along their normals — with the same `NOISE`.
5. **Parity**: `tools/diffcheck.py` gains a `refined` stage that feeds both engines the Python's fitted graph and
   compares the refined control points to 0.05 px RMS (the tolerance the plan named), plus a pixel-level check
   that both rasterisers agree on a crop to ≤ 1 grey level.
6. **Wiring**: `VexelEngine.trace` stops routing `refine=True` to Python once the stage lands; the CLAUDE.md
   sentence about the deliberate divergence is removed in the same commit.

## Acceptance

- `refine=True` under `VEXEL_BACKEND=rust` reproduces the Python improvements within 0.005 px `outline_px` on
  heart, overlap, wedge-fan, blobs and tilted-squares, and leaves triangle-bar and the wedge unchanged.
- Overhead ≤ 30 % of the Rust trace. `cargo test` covers the path conversion and the crop compare.
- Dependency cost measured: image size and build time of `backend/Dockerfile` before and after.
