# Vexel v0 — fidelity-first tracing engine (Project D)

**Date:** 2026-09-17
**Status:** Written under Tim's instruction to proceed autonomously; bench-gated.
**Depends on:** A+B spec (engine seam, bench), C spec (UI is schema-driven, so Vexel needs no frontend work).

## Goal

A tracing engine whose output is *true to the source* for logos/icons, flat
illustrations, and illustrations with gradients and soft shadows — judged by
the Vexel Bench against Potrace and VTracer, with the explicit target of
beating VTracer on `gradient` and `shadow` (ΔE, banding) while matching or
beating it on `logo` and `flat` with equal or fewer paths.

Python (numpy / scipy / scikit-image) first; every stage is a pure function on
arrays so a Rust port can replace stages one at a time under the same bench.

## What the best tools do (research summary, sources in the appendix)

| Capability | Who does it | Vexel v0 |
|---|---|---|
| Boundaries placed at **sub-pixel** positions inferred from anti-aliasing values | Vectorizer.AI, Vector Magic, PolyFit, "Subpixel deblurring" (UBC 2023) | ✅ coverage field + marching squares |
| **Whole-shape fitting** (circles, ellipses, rounded rects) and explicit corner modelling | Vectorizer.AI | ✅ circle/ellipse/rect; rounded-rect later |
| Regions filled with the best of **solid / linear / radial gradient**, chosen per region with discontinuity-aware segmentation | Adobe (Chakraborty et al., EG 2025; radial-gradient patents) | ✅ model-aware merging + per-region fit |
| **Layered semi-transparent gradients** for shading | Photo2ClipArt (2017), Du et al. (SIGGRAPH 2023) | ⏭ v1 (alpha ramps handled as gradient stop-opacity in v0) |
| Perceptual regularity: axis-aligned lines, G1 joins, few primitives | PolyFit (SIGGRAPH 2020) | ✅ light version: snapping + G1 + segment merging |
| Optimal cubic fitting (area + moment matching, quartic solve) | Raph Levien (kurbo) | ⏭ v1; v0 uses Schneider least squares with corner splitting |
| Differentiable-rendering refinement (DiffVG/LIVE, segmentation-guided) | academic | ✗ too slow for interactive use; may become an optional "refine" pass later |

Potrace and VTracer's failure on our targets is structural: Potrace is 1-bit;
VTracer quantises colour then traces each quantised layer, which turns every
gradient into stair-steps (bench: `shadow` banding 8.7, `gradient` ΔE 11.4).

## Pipeline

All coordinates are pixel space; pixel (i, j) covers [j, j+1) × [i, i+1); its
centre is (j+0.5, i+0.5). Output viewBox is `0 0 W H` (via `finish()`).

### 1. Prepare
- Input RGBA uint8 → float32 `rgb` (0–255) and `alpha` (0–1). Where α = 0 the
  RGB is undefined: replace with the nearest opaque colour (distance transform
  indices) so gradients don't see garbage.
- Feature image `F = [L*, a*, b*, 100·α]` (Lab from sRGB) used for
  discontinuities and merge costs; fits and output use sRGB + α because SVG
  interpolates gradients in sRGB.

### 2. Discontinuity-bounded partition
- `grad = ‖∇F‖` via Scharr per channel, summed in quadrature; smoothed σ=0.7.
- Markers = connected components of `grad < g_low` (g_low = 1.5 ΔE-ish) with
  size ≥ `min_region` px. Watershed on `grad` from those markers → label map
  `L0` with boundaries on discontinuities. Regions smaller than `min_region`
  merge into their most similar neighbour immediately.

### 3. Model-aware region merging (the core)
Every region keeps **sufficient statistics** over its pixels: n, Σx, Σy, Σx²,
Σxy, Σy², Σx³… up to 4th-order position moments, and Σc, Σxc, Σyc, Σx²c,
Σxyc, Σy²c, Σc² for each colour channel c ∈ {R,G,B,A}. Merging two regions is
adding vectors. From these, three colour models are fitted in **closed form**:

- **solid**: c = a — SSE = Σc² − (Σc)²/n
- **planar** (≈ linear gradient): c = a + bx + cy — 3×3 normal equations
- **quadratic** (≈ radial gradient; a radial ramp in r² *is* quadratic in x,y):
  c = a + bx + cy + dx² + exy + fy² — 6×6 normal equations

Model cost = SSE (summed over channels, in 0–255² units) + `k · λ · n_eff`
where k = parameter count (4, 12, 24) and λ ≈ (`detail`/4)². The region cost
is the min over models.

Adjacency graph with edge weights; merge criterion for neighbours A, B:

```
Δ(A,B) = cost(A∪B) − cost(A) − cost(B) − γ            (γ = per-region penalty, favours fewer regions)
       + β · mean_boundary_gradient(A,B) · |boundary|  (don't merge across visible edges)
```

Greedy: pop the smallest Δ from a heap while Δ < 0 (all thresholds folded
into γ, β and λ, which derive from the single user parameter `detail` in ΔE-like
units). Stale heap entries are skipped via version counters. Result: label
map `L` with typically 5–60 regions for our targets.

Why this beats colour-distance merging: a red→blue gradient has huge colour
spread but is one region with near-zero planar SSE, so it merges into one
region; two flat colours 6 ΔE apart never merge because their combined solid
SSE jumps. Gradients stop stair-stepping *and* low-contrast flat neighbours
stay distinct — both current failure modes at once.

### 4. Fill reconstruction per final region
Fit in sRGB+α on the region's pixels (weighted by α for colour channels):

1. **solid**: mean colour; done if RMS error < `flat_tol` (≈ `detail`/2).
2. **linear**: per-channel planar gradient vectors g_c; direction d = principal
   axis of {g_c} (weighted SVD). t = p·d; fit a **piecewise-linear ramp** c(t)
   with 2 stops, adding a stop at the point of maximum residual until RMS <
   `flat_tol` or `max_stops` reached. Gradient line = [t_min, t_max] along d
   through the region centroid → SVG `x1,y1,x2,y2` in userSpaceOnUse.
3. **radial**: from the quadratic fit, centre = −(b, c)/(2·d̄) using the mean
   of the x² and y² coefficients (weighted across channels); refine with one
   Gauss-Newton step on the actual pixels; r = ‖p − centre‖; fit the same
   piecewise-linear ramp in r; `cx, cy, r = r_max`. If the x²/y² coefficients
   differ markedly the gradient is elliptical → emit `gradientTransform`
   scale (v0: only if the ratio is within 0.5–2; otherwise fall back).
4. Choose the model with lowest `RMS + k·penalty`; if `gradients=false`,
   solid only (with the region split beforehand? no — the merge stage already
   respected `gradients`: with gradients off only the solid model is offered).

Alpha: if the region's α varies (shadow on transparency), stops carry
`stop-opacity`; otherwise `fill-opacity` if α<1.

### 5. Depth ordering & stacking
- Region adjacency + "enclosure": B is enclosed by A if every boundary pixel of
  B's outer contour touches A or the image border.
- Painter's order: the region with the most border pixels (else largest) is
  the background; then a BFS by enclosure depth, siblings by area descending.
- **stacked** (default): each region's shape = its outer contour with holes
  *filled* (children paint over it) → no seams, editable layers, exactly how
  clean vector art is built.
- **cutout**: exact shapes with holes (`fill-rule="evenodd"`), no overlap.

### 6. Sub-pixel boundaries from anti-aliasing
For region A with neighbours N(A): build a coverage field M_A on the pixel
grid: 1 for interior pixels of A, 0 for pixels of other regions not adjacent
to A's boundary, and on the boundary ring (pixels within 1 px of the A/B
label change) the estimated coverage

```
α_A(p) = clamp( (c_p − f_B(p)) · (f_A(p) − f_B(p)) / ‖f_A(p) − f_B(p)‖² , 0, 1 )
```

where f_A(p), f_B(p) are the reconstructed fills evaluated at p (so it works
across gradients). Against transparency, α_A is the image alpha directly.
Contour = `find_contours(M_A, 0.5)` (marching squares, linear interpolation) →
closed polylines in pixel-centre coordinates (+0.5 to SVG space). This is
what places edges where the artist drew them instead of on pixel borders and
recovers features thinner than a pixel.

### 7. Curve fitting
Per closed polyline:
1. **Corners**: turning angle between chords of length s (s = 2 px and 4 px;
   both must agree) > `corner_threshold` → corner. Adjacent corner candidates
   collapse to the sharpest.
2. **Whole-shape fitting** (if `shape_fitting`): no corners → algebraic circle
   fit (Kåsa); accept if 95th-percentile radial deviation < `curve_tolerance`
   → `<circle>`; else `skimage.measure.EllipseModel` → `<ellipse>`. Exactly 4
   corners, sides straight within tolerance and axis-aligned within 1.5° →
   `<rect>`.
3. **Segments between corners**: straight if max chord deviation <
   `curve_tolerance` → `L`; otherwise Schneider least-squares cubic fitting
   with recursive split at max error > `curve_tolerance` (iterations: 4
   Newton reparametrisations), tangents estimated from ±3 samples and shared at
   smooth joins (G1).
4. **Regularity**: near-axis lines snapped; consecutive collinear lines merged;
   after fitting, adjacent cubics are re-merged when a single cubic fits both
   within tolerance (Levien's insight that greedy merging cuts node counts a lot).
5. Coordinates rounded to `path_precision` decimals.

### 8. Assembly
`<svg xmlns viewBox>` → `<defs>` (gradients with ids `g1…`, `gradientUnits=
"userSpaceOnUse"`) → elements in painter's order. Solid fills as `#rrggbb`,
`fill-opacity` when α<1. Output passes through `finish()` like every engine.

## Parameters (`VexelParams`)

| name | default | range | ui | meaning |
|---|---|---|---|---|
| `detail` | 8 | 1–40 | slider, Regions | ΔE-like merge threshold; lower = more regions |
| `min_region` | 6 | 1–200 px | slider, Regions | speckle floor |
| `gradients` | true | | toggle, Fills | allow linear/radial fills |
| `max_stops` | 4 | 2–8 | slider, Fills | stops per gradient |
| `layering` | stacked | stacked/cutout | select, Output | painter's order vs exact cut-outs |
| `corner_threshold` | 60 | 20–150 ° | slider, Curves | turning angle that makes a corner |
| `curve_tolerance` | 0.4 | 0.1–2 px | slider, Curves | max fit error |
| `shape_fitting` | true | | toggle, Curves | circles/ellipses/rects as primitives |
| `path_precision` | 2 | 0–4 | slider, Output | decimals |

## Modules

```
backend/studi0trace/engines/vexel/
  __init__.py       # registers VexelEngine
  engine.py         # VexelEngine, VexelParams, orchestration
  prepare.py        # rgba → rgb/alpha/lab, alpha inpainting
  partition.py      # discontinuity map, watershed initial labels
  stats.py          # RegionStats (moment accumulation, closed-form model fits)
  merge.py          # RAG + heap merging
  fills.py          # Solid/Linear/Radial dataclasses, reconstruction, evaluate(p)
  order.py          # enclosure tree, painter's order, hole filling
  boundary.py       # coverage fields, marching-squares contours
  curves.py         # corners, Schneider fitting, line/shape fitting, regularity
  svg.py            # element/gradient serialisation
```
Every module is array-in/array-out with no engine imports, so the Rust port
and the unit tests target them one at a time.

## Testing

Unit tests (`tests/test_vexel_*.py`) on synthetic arrays:
- `stats`: planar fit of an exact planar image has SSE≈0; solid SSE formula; merged stats == stats of union.
- `merge`: two flat regions 6 ΔE apart stay separate at `detail=8`; a rendered linear gradient over a flat background → exactly 2 regions.
- `fills`: linear gradient recovered with direction within 2° and stop colours within 2/255; radial centre within 1 px; solid chosen for flat.
- `boundary`: anti-aliased circle (rendered by resvg) → contour radius error < 0.15 px vs the truth radius; a 0.5-px line survives.
- `curves`: exact circle polyline → `<circle>`; square → 4 corners, `<rect>`; S-curve → ≤ 3 cubics within tolerance; near-axis lines snapped.
- `engine`: registered as `vexel`; traces the fixture images; SVG parses; params validated; `GET /engines` lists three engines.

Bench gate (the actual definition of done for v0):
- `python -m bench run --engines potrace,vtracer,vexel` on the full corpus.
- Targets: `gradient` and `shadow` ΔE mean and banding_index below VTracer's; `logo` and `flat` score ≥ VTracer's with `paths` ≤ 1.2× VTracer's; per-item runtime < 2 s at 512 px.
- Baseline committed as `bench/baselines/vexel.json`; observations appended to this spec.

## Baseline observations (v0, 2026-09-17)

Full corpus, defaults (`detail=6`), Python 3.13 on Tim's Mac:

| class | score V / VT | ΔE mean V / VT | edge F1 V / VT | banding V / VT | paths V / VT |
|---|---|---|---|---|---|
| logo | 0.946 / 0.961 | **0.43 / 0.89** | 0.971 / 0.988 | 0.72 / 0.03 | 3.6 / 2.8 |
| flat | 0.855 / 0.926 | 1.64 / 1.03 | 0.933 / 0.962 | 2.37 / 0.44 | 10.4 / 12.6 |
| gradient | 0.931 / 0.822 | **1.10 / 11.4** | 0.801 / 0.902 | 0.30 / 0.25 | 2.0 / 5.5 |
| shadow | 0.930 / 0.840 | **0.53 / 3.97** | 0.895 / 0.874 | 0.51 / 8.68 | 5.0 / 8.7 |

Vexel has lower ΔE than VTracer on 39 of 48 items; every 512 px item is under
1.7 s. Remaining losses and what they need:

- `flat/stripes`, `flat/mosaic`: neighbouring palette colours < `detail` apart
  merge by design; the composite score is flat across detail 5–8 (sweep), so the
  default stays 6. A perceptual "distinct colour" prior (e.g. treat a prominent
  ridge between two *large* flat regions as a hard boundary) is the v1 fix.
- `logo/thin-mark-128`: strokes narrower than a pixel become semi-transparent
  bands; a dedicated centreline/thin-stroke pass is needed (Vectorizer.AI's
  "features less than a pixel wide").
- `gradient/radial-focal`: SVG focal radial gradients (fx, fy) are not modelled;
  the region splits into two rings.
- `shadow/glow`: a Gaussian bump is not well approximated by the quadratic merge
  proxy, so the glow fragments into rings; a refit-merge pass on the final regions
  (try the *actual* multi-stop radial fit on each adjacent pair) would join them.
- Lessons that changed the design during implementation: absolute gradient is
  the wrong "smooth" test (steep ramps have large flat gradients) — ridges need
  non-maximum suppression *and* prominence (8-bit quantisation puts periodic
  bumps on ramps); a planar/quadratic model "explains" a hard step surprisingly
  well, so gradient-assisted merges must be vetoed across visible edges; fills
  must be fitted on interior pixels (anti-aliasing mixtures bias colour and make
  transparent regions look visible); the Nelder-Mead centre search needs a cheap
  smooth surrogate, not the full knot search.

## Out of scope for v0 (v1 candidates)
Semi-transparent layer decomposition (Photo2ClipArt-style shadows as a separate blurred layer / `feGaussianBlur`), symmetry detection, rounded-rect and star primitives, Levien optimal cubic fitting, elliptical gradients with rotation, differentiable refinement, Rust port.

## Appendix — sources
- Vectorizer.AI feature list: https://vectorizer.ai/ (full shape fitting, sub-pixel precision, corners, symmetry, adaptive simplification, 32-bit ARGB)
- Vector Magic comparisons: https://vectormagic.com/comparisons
- Chakraborty et al., *Image Vectorization via Gradient Reconstruction*, CGF/EG 2025: https://onlinelibrary.wiley.com/doi/10.1111/cgf.70055 ; Adobe patents *Reconstructing general radial gradients* (US 12307554) and *Reconstructing concentric radial gradients* (US 12340441)
- Favreau, Lafarge, Bousseau, *Photo2ClipArt*, SIGGRAPH Asia 2017: https://www-sop.inria.fr/reves/Basilic/2017/FLB17/
- Du et al., *Image vectorization and editing via linear gradient layer decomposition*, SIGGRAPH 2023: https://dl.acm.org/doi/10.1145/3592128
- Dominici et al., *PolyFit*, SIGGRAPH 2020: https://www.cs.ubc.ca/labs/imager/tr/2020/ClipArtVectorization/ (code: https://github.com/dedoardo/polyfit)
- Yang et al., *Subpixel Deblurring of Anti-Aliased Raster Clip-Art*, CGF 2023: https://www.cs.ubc.ca/labs/imager/tr/2022/SubpixelDeblurring/
- Ma et al., *LIVE: Towards Layer-wise Image Vectorization*, CVPR 2022: https://ma-xu.github.io/LIVE/ ; *Segmentation-guided Layer-wise Image Vectorization with Gradient Fills*: https://arxiv.org/abs/2408.15741
- *A Formalization of Image Vectorization by Region Merging*: https://arxiv.org/abs/2409.15940
- Levien, *Fitting cubic Béziers* and *Simplifying Bézier paths*: https://raphlinus.github.io/curves/2021/03/11/bezier-fitting.html , https://raphlinus.github.io/curves/2023/04/18/bezpath-simplify.html
- Selinger, *Potrace: a polygon-based tracing algorithm*, 2003: https://potrace.sourceforge.net/potrace.pdf
- Schneider, *An Algorithm for Automatically Fitting Digitized Curves*, Graphics Gems 1990 (Python port: https://github.com/volkerp/fitCurves)
