# Vexel geometry quality: where the bar is, where we are, what to do next

Research and evaluation note, 2026-09-22. Prompted by two visible defects in
Vexel's output: an outline that flares where it meets another shape (the
junction bulge in the 750 % screenshot), and edges that should be straight
coming out as gently wobbling cubics.

Two versions of the engine were measured: the branch this worktree is on
(`claude/trace-engine-quality-b09adc` at `e41540f`, "red") and the eight
commits ahead of it on `claude/rust-engine-quality-5b0571` ("green"), which
added the shared boundary graph (`vexel/topology.py`), wedge extension and
cusps, straight-run cutting and the C2 spline fitter. The green branch is what
the screenshot shows as the better line. **This worktree does not have those
commits; merge them before touching the geometry stage.**

## Verdict

- The **sub-pixel placement is already industry grade.** Against exact vector
  truth the placed outline vertices sit within 0.035–0.074 px RMS of the true
  edge at every angle from 0° to 60°, 95th percentile ≤ 0.15 px. That is as
  good as any published method claims.
- **Everything downstream of placement is what loses the quality**: how
  junction nodes are placed, which vertices near a node are believed, how
  "straight" is decided, and how curves are fitted. Both defects the screenshot
  shows trace to specific rules in that stage, listed below with numbers.
- **The bench cannot see any of it.** On a synthetic wedge whose tip the old
  engine cut 6.7 px short, mean ΔE moved from 0.018 to 0.009 when the tip was
  fixed, and edge F1 went the *wrong* way (0.9997 → 0.9849). The corpus carries
  exact vector ground truth and no metric uses it. Until geometry is measured
  against that truth, every geometry change is tuned by eye.
- **AI has not changed fidelity tracing; it has changed the decisions around
  it.** The end-to-end generators (StarVector, OmniSVG, Adobe's Concept to
  Vector, Recraft) reinterpret; none is a faithful tracer. The tools that lead
  on fidelity (Vectorizer.AI, and the UBC/Adobe PolyFit line of work) use
  learning for *perceptual* decisions — is this a corner, are these one
  shape, where is the edge under the blur — inside a classical geometric
  pipeline. Vexel is the same kind of system; the gap is in geometric
  finishing and in what is measured, not in the architecture.

## What was measured

All inputs are 512 px, rendered with resvg from SVG the test script generates,
so the truth is exact. Both engines ran through the Python reference pipeline
(`VEXEL_BACKEND=python`; the branches report Python/Rust byte parity). Scripts
and outputs are in this session's scratchpad (`exp.py`, `probe.py`,
`sweep.py`, `chord.py`, `flare.py`, `viz.py`); the method is described well
enough here to re-implement as a bench stage, which is recommendation 1.

### A 15° wedge closing onto a 45° edge (the screenshot's situation)

White background, blue half-plane below `x + y = 512`, light-blue triangle
with its tip at (150, 362) and a 15° opening. Deviation is measured per side
against the true line, with samples assigned to the side they belong to.

| | red (this worktree) | green (sibling branch) |
|---|---|---|
| traced tip vs true tip | **6.7 px short** (154.5, 357.0) | 1.4 px short (151.0, 361.0) |
| steep side, worst deviation within 60 px of tip | 0.95 px | 0.25 px (only in the last 3 px) |
| shared side with the blue, deviation | ±0.6 px bulges | constant 0.34 px offset, 0.34 px kink into the node over 3 px |
| segments for the wedge | 24 cubics, 0 lines | 15 segments, 4 lines |
| bench: mean ΔE / edge F1 / SSIM | 0.018 / 0.9997 / 0.9994 | 0.009 / 0.9849 / 0.9997 |

The green branch fixed most of it. What remains at the tip is small on this
clean synthetic case and larger on the real wordmark below.

### The real wordmark (`bench/corpus/real/logo/vexel-wordmark-512.png`)

For every arc end that is pinned at a junction node and whose run 6–30 px back
from the node is straight, the straight continuation was extended into the node
and the last 6 px of the arc compared with it. That is exactly the black line
in the screenshot.

- 4 such ends. Flare of the fitted curve: median **0.52 px**, max **0.82 px**.
  At 750 % zoom 0.5 px is 4 screen pixels, which matches the screenshot.
- Vertex flare equals curve flare at every one of them. **The placed vertices
  near the node already carry the bulge; the fitter reproduces it faithfully.**
  This is a placement-near-junctions problem, not a curve-fitting problem.
- At the worst node the tangent the arc was pinned to was **59.2° off the
  direction the arc actually travels**. See root cause 3.

### Straight edges: 120 px squares rotated 0°–60°, clean, JPEG q75, and 2× bilinear downsample

Placement first, then what was emitted.

| angle | placed vertices vs true edge (RMS / p95 / max) | red emits | green emits |
|---|---|---|---|
| 0° | 0 / 0 / 0 | 4 L | 4 L |
| 5° | 0.074 / 0.147 / 0.173 | 15 C | 12 C |
| 10°, 15°, 20°, 30°, 35°, 40°, 60° | 0.04–0.06 / ≤ 0.12 / ≤ 0.14 | 4 L | 4 L |
| 25° | 0.053 / 0.112 / 0.125 | 4 L | 1 L + 9 C |
| 38° | 0.045 / 0.08 / 0.09 (one outlier 0.37) | 2 L + 8 C, 0.56 px off | **17 C, 1.52 px off** |
| 45° | 0.035 / 0.035 / 0.035 (exactly collinear) | 4 L | **22 C + 1 L, 1.17 px off** |
| 50° | 0.046 / 0.084 / 0.11 | 4 L | 16 segments |

- JPEG q75: every square, both engines, becomes 10–25 cubics with 0.2–0.6 px
  (red) or 0.2–1.3 px (green) of wobble.
- 2× bilinear downsample (a different anti-aliasing kernel, closer to a real
  screenshot): green emits the 5° and 12° squares as **11 collinear line
  segments each**; nothing merges collinear lines.

The placement column is the important one: the outline is straight to a tenth
of a pixel at every angle, and the emitted geometry is not. The line-versus-
curve decision is what fails.

## Root causes, in the code

Paths are on the sibling branch unless noted.

1. **Junction nodes are not moved at shallow angles.** `topology._junctions`
   places a node at the least-squares intersection of the incident arcs'
   approach lines, faded by `trust = clip((λ_min − 0.15) / 0.2)` where λ_min is
   the smaller eigenvalue of the summed line normals. For two arcs meeting at
   angle θ that is `1 − cos θ`: **trust is 0 below 32° and 1 only above
   ~50°**. A 15° wedge tip is therefore never corrected and sits at the mean
   of the two chamfered arc ends, 1.4 px short of the true tip; the move is
   also clamped to `limit = 2.0` px. The fade was added to keep the two
   implementations from disagreeing by a whole pixel at a threshold, which is
   a real concern, but it switches the correction off precisely where the
   user sees the flare. Two accurate lines crossing at 15° pin the point to
   about σ / sin(θ/2) ≈ 0.4 px along the bisector for σ = 0.05 px; that is far
   better than not moving at all.

2. **Vertices within a few pixels of a node are believed as placed.** Pixels
   near a junction mix three fills; the two-fill projection in
   `topology._crossing` misplaces them, and `_extend_wedges` hands back sliver
   pixels that are worse still. `_junctions` moves only the *end* vertex to
   the node. `_sharpen_piece` trims chamfer vertices only next to interior
   corners found by `_open_corners`, **never next to the arc's own ends**, so
   the contaminated run between the clean part of the arc and the node stays
   and the fit follows it. That is the 0.5 px bulge on the wordmark and the
   45°/38° regression: on a perfectly collinear 45° edge the untrimmed
   chamfer vertices next to each corner node break the chord test, the piece
   falls to the spline fitter, and the corner comes out rounded by more than a
   pixel. The old `curves.split_pieces` trimmed 0.8 px at every corner and
   intersected lines fitted 0.8–3 px back; the arc-based path lost that at
   nodes.

3. **One threshold decides both "is this a corner" and "do these two arcs
   continue smoothly".** In `_junctions`, when no wedge is found, the pair of
   arcs with the smallest turn gets a shared tangent if that turn is
   `<= corner_threshold` (default 60°). A 59° meeting is therefore forced G1,
   and the arc must swing 59° inside its first few pixels: the 0.82 px hook on
   the wordmark, and a 1.09 px flare where the synthetic wedge's 60° side meets
   the canvas border. A smooth continuation should be decided by whether one
   curve fits both arcs within tolerance, or by a much tighter angle (≤ 15°),
   not by the corner threshold.

4. **Straightness is decided from a chord between two pre-placed corners.**
   `curves.fit_open` emits a line only if every vertex is within `tol`
   (0.4 px) of the chord from the piece's first vertex to its last. Those end
   vertices are the sharpened corners from a 0.8–3 px approach window, and on
   the 5° and 25° squares they are **0.2–0.4 px off the true edge**. The chord
   tilts, the interior vertices (which are within 0.17 px of the true edge)
   deviate 0.46 px from it, the test fails, and a straight edge becomes three
   cubics. The order is backwards: fit the line to the run, judge straightness
   by the residuals, then put the corner at the intersection of the lines.

5. **`curves.straight_runs` cannot rescue it.** It is a greedy scan from
   vertex 0 that ends a run at the first vertex more than `STRAIGHT_SAG =
   0.10` px off the run's chord, keeps runs ≥ 18 px, and returns nothing when
   the whole arc is flat (`flat.all()`), so a straight arc gets no help. The
   sag bound was set from a p90 of 0.053 px measured on one synthetic image;
   the sweep above shows p95 of 0.10–0.15 px at shallow angles, so runs break
   on noise. A max-based test on 500 vertices is a test of the single worst
   vertex. The `closed` parameter is unused.

6. **The fitter stops at "max error < tol", which bakes in wobble up to tol.**
   `fit_c2` adds spline spans at the worst point until every vertex is inside
   0.4 px. Nothing rewards a straighter or lower-energy curve, so with noisy
   vertices the curve follows the noise to within tolerance. Levien's fitter
   minimises segment count for a target error under an arc-length L2 /
   Fréchet norm and penalises "bumpy" segments; Potrace fits the minimal
   polygon first. Either is structurally wobble-free on a straight run.

7. Smaller things seen along the way: no collinear-line merge (11 lines for
   one edge after downsampling); a lattice-aligned 45° edge whose pixels sit
   at exactly 0.5 coverage flips the crossing interval and lands 0.34 px off
   in both engines (`_crossing` picks the interval by proximity to the label
   edge); JPEG q75 defeats the line test everywhere.

## What the best tools do that Vexel does not (yet)

| Technique | Who | Vexel status |
|---|---|---|
| Boundary as a shared planar graph; every edge fitted once | Vectorizer.AI ("Vector Graph"), PolyFit | **Done** on the sibling branch |
| Junction/corner placed from the incident edges, never from the raster chamfer | Vectorizer.AI ("we analyze, model, and optimize every corner"), PolyFit | Partial: trust fade disables it below 32° |
| Straight/smooth decided by fitting, corners derived from the fits | Potrace (optimal polygon), PolyFit (polygon first), Hoshyari 2018 | Missing: chord test between pre-placed corners |
| Corner vs smooth as a learned perceptual decision, not an angle threshold | Hoshyari 2018 (learned discontinuity metric), PolyFit (learned polygon→primitive map) | Missing: single 60° threshold does both jobs |
| Segment-count-minimising fit with L2/Fréchet error and bump penalty | Levien (kurbo `fit_to_bezpath`, `simplify`) | Missing: max-error greedy split; listed as v1 in the 09-17 spec |
| Regularity: axis alignment, parallelism, equal lengths, symmetry | Vectorizer.AI ("symmetry modelling"), PolyFit | Only axis snap of lines |
| Primitives beyond circle/ellipse/rect: rounded rects, stars, circular and elliptical arcs | Vectorizer.AI | Missing |
| Verify the output by re-rendering, refine geometry against the pixels | DiffVG/LIVE (heavy), Render-in-the-Loop (2026), AnchorFlow's render-guided correction (2026) | Missing: geometry is never checked against the source after fitting |
| Learned sub-pixel deblurring before tracing low-res art | UBC/Adobe 2023 | Missing (relevant for ≤ 128 px inputs) |
| Robustness to unknown rasterisation (JPEG, resampling) via a degradation model | VectorArk (CVPR 2026) | Corpus has JPEG q75 only; line test fails under it |

## Did AI change how tracing is done?

Three things happened, and only one of them is relevant to Vexel's goal.

- **End-to-end SVG generation.** StarVector (CVPR 2025) and OmniSVG (2025)
  treat vectorisation as code generation from a vision-language model; they
  produce compact, editable SVG for icons and logotypes and are explicitly not
  faithful (they hallucinate detail and miss exact colour). Adobe's *Concept
  to Vector* is documented as producing "a stylized version of the image" and
  is a Firefly reinterpretation, not a trace. Recraft's vectoriser is neural
  and strong on illustrations, weak on exactness. None competes on the
  "true to the source" axis Vexel is built for.
- **Layer and structure recovery.** Layered Image Vectorization via Semantic
  Simplification (CVPR 2025), LayerTracer, LayerPeeler (SIGGRAPH Asia 2025),
  AmodalSVG (2026) and SAMVG use diffusion or SAM to decide *which pixels are
  one shape* and *what is behind what*, then fit geometry classically or with
  DiffVG. This is the genuinely new idea and it is where Vexel's model-aware
  merging could eventually gain a semantic prior for occluded shapes.
- **Learning inside the geometry stage.** Hoshyari 2018 learned a
  perceptual "is this a corner" metric from human annotations; PolyFit
  learned which primitive configuration humans expect for a polygon patch;
  the 2023 sub-pixel deblurring work learned a 2× blur-free prediction and then
  used discrete optimisation. Vectorizer.AI says the same in its own words:
  deep networks *and* classical algorithms, fifteen years in. AnchorFlow (May
  2026) predicts where anchors should go and corrects by rendering; VectorArk
  (CVPR 2026) learns a rounded-polygon representation with a degradation model
  so it survives real-world inputs.

The practical reading for Vexel: keep the classical pipeline, and use learning
where a rule is currently a hand-picked threshold. The corpus already provides
exact labels for corners, straight runs, junction positions and node counts,
so a corner/smooth classifier and a straightness test can be trained and
validated without a single human annotation.

## Recommendations, in order

Each has the measurement that proves it, because the current bench proves none
of them.

1. **A geometry stage in the bench, scored against the vector truth.** For
   every synthetic item: Chamfer and Hausdorff distance between the traced and
   true outlines sampled at 0.1 px; the same restricted to 6 px around true
   junctions and corners ("junction error"); fraction of true straight-edge
   length emitted as `L` or as a cubic within 0.05 px of straight
   ("straightness"); corner angle error; node count relative to truth. Add
   degradation variants beyond JPEG q75: bilinear and Lanczos resampling from
   2× and 0.5×, gamma-blended anti-aliasing, chroma-subsampled JPEG. The
   prototype scripts from this session do most of this already. Nothing below
   should merge without moving these numbers.

2. **Place every node from the incident lines, at any angle.** Replace the
   trust fade in `_junctions` with the least-squares intersection plus an
   uncertainty estimate; accept the move when the along-bisector uncertainty
   is under ~0.5 px, which two accurate lines at 15° satisfy. For wedge tips,
   the tip *is* that intersection; the mix-share walk then only has to say
   which pixels belong to the sliver. Expected: tip error 1.4 px → < 0.2 px.

3. **Do not believe vertices inside the approach window of a node.** Trim
   `reach`-scale (2–4 px) of vertices next to every node in `_sharpen_piece`,
   fit the clean run, and let the curve run from the clean run straight into
   the node. This is what the old `split_pieces` did at corners and is the
   single change that removes the 0.5 px bulge the screenshot shows.
   Expected: wordmark flare p50 0.52 → < 0.15 px; 45° square back to 4 lines.

4. **Separate "corner" from "smooth continuation".** At a node, two arcs
   share a tangent only if a single cubic fits their combined approach within
   tolerance, or their meeting angle is under ~15°; everything else is a
   corner. Retire the use of `corner_threshold` for this.

5. **Fit lines first; judge straightness by residuals; derive corners from
   lines.** Total-least-squares line per candidate run; accept as straight
   when RMS residual < 0.08 px and p98 < 0.25 px (from the sweep's noise
   model, to be re-measured under the degradation variants); place each corner
   at the intersection of the two accepted lines; merge collinear neighbours.
   Then `straight_runs` and the chord test can go. Expected: 5°/25°/38°/45°/50°
   squares → 4 lines; JPEG q75 squares → 4 lines.

6. **Replace max-error splitting with a segment-count-minimising fit** (Levien:
   arc-length L2 or Fréchet error, greedy-then-tighten for the minimum
   segment count, bump penalty on control-arm ratio > 0.85). Keep the C2
   spline where a run is genuinely curved. This removes wobble-to-tolerance
   on every curve, not only the straight ones.

7. **Regularity pass**: parallel and perpendicular snapping between lines of
   one shape and across shapes, equal-radius snapping of arcs, mirror and
   rotational symmetry detection within a shape (logos are full of it), and
   `<use>` for repeated shapes. Vectorizer.AI lists exactly these.

8. **A render-and-compare refinement**, as an optional final pass: rasterise
   each shape locally at 4×, compare with the source under the fitted fills,
   and nudge node positions and control arms to reduce the error under a
   straightness/G2 prior. It is the one step that would catch every residual
   the vertex-fitting stages cannot see, and it is a few milliseconds per arc
   in Rust with an analytic coverage rasteriser, not a DiffVG run.

9. **Learned corner/smooth classifier**, trained on the synthetic corpus's
   exact corners, once 1–6 have flattened the easy errors. Features: turning
   angle at 2/4/8 px, run lengths either side, local contrast, junction
   degree. Hoshyari 2018 shows humans do not use a fixed angle, and neither
   should the engine.

10. Strategic, larger: rounded-rect/arc primitives; a sub-pixel deblurring
    network for ≤ 128 px inputs; text detection with glyph fitting for
    wordmarks; a semantic layer prior for occluded shapes.

## Sources

- Vectorizer.AI feature descriptions (Vector Graph, Full Shape Fitting, Clean Corners, Sub-Pixel Precision, Symmetry Modelling): https://vectorizer.ai/
- Dominici et al., *PolyFit: Perception-aligned vectorization of raster clip-art via intermediate polygonal fitting*, SIGGRAPH 2020: https://www.cs.ubc.ca/labs/imager/tr/2020/ClipArtVectorization/ , https://dl.acm.org/doi/10.1145/3386569.3392401
- Hoshyari et al., *Perception-Driven Semi-Structured Boundary Vectorization*, SIGGRAPH 2018: https://www.cs.ubc.ca/labs/imager/tr/2018/PerceptionDrivenVectorization/
- Yang et al., *Subpixel Deblurring of Anti-Aliased Raster Clip-Art*, Eurographics 2023: https://www.cs.ubc.ca/labs/imager/tr/2022/SubpixelDeblurring/
- Levien, *Simplifying Bézier paths* (2023) and *Fitting cubic Bézier curves* (2021): https://raphlinus.github.io/curves/2023/04/18/bezpath-simplify.html , https://raphlinus.github.io/curves/2021/03/11/bezier-fitting.html
- Selinger, *Potrace: a polygon-based tracing algorithm*, 2003: https://potrace.sourceforge.net/potrace.pdf
- Rodriguez et al., *StarVector*, CVPR 2025: https://openaccess.thecvf.com/content/CVPR2025/papers/Rodriguez_StarVector_Generating_Scalable_Vector_Graphics_Code_from_Images_and_Text_CVPR_2025_paper.pdf
- *OmniSVG*, 2025: https://omnisvg.github.io/
- Wang et al., *Layered Image Vectorization via Semantic Simplification*, CVPR 2025: https://arxiv.org/abs/2406.05404
- *LayerTracer* (2025): https://www.researchgate.net/publication/388657987_LayerTracer_Cognitive-Aligned_Layered_SVG_Synthesis_via_Diffusion_Transformer
- *AmodalSVG* (2026): https://arxiv.org/pdf/2604.10940 ; *SAMVG*: https://www.researchgate.net/publication/379817400_SAMVG_A_Multi-Stage_Image_Vectorization_Model_with_the_Segment-Anything_Model
- *AnchorFlow: Editable SVG Reconstruction via Sparse Anchor Point Fields* (May 2026): https://arxiv.org/abs/2605.19551
- *VectorArk: Learning Practical Image Vectorization with Rounded Polygon Representation*, CVPR 2026: https://arxiv.org/abs/2605.24398
- *Render-in-the-Loop: Vector Graphics Generation via Visual Self-Feedback* (2026): https://arxiv.org/pdf/2604.20730
- Adobe, *Generate vector artwork from raster images* (Concept to Vector): https://helpx.adobe.com/illustrator/desktop/use-generative-ai/generate-vector-artwork-from-images.html
- Recraft, *Best image-to-vector software*: https://www.recraft.ai/blog/best-software-image-vector
