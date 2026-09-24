# Vexel quality pass: artifacts a designer would never draw

2026-09-23. Prompted by the Vexel wordmark (`bench/corpus/real/logo/vexel-wordmark-512.png`)
traced with each preset in the deployed app, with the defects circled: dark ticks where
shapes meet, a grey band inside the "e", wobbly and nicked glyph edges under *Detailed*,
S-waves on the ribbon under *Logo & icon*, bumps at the V junction, and pixel squares with
mismatched corners. The ask was quality that matches the image "without crazy wobble or weird
curves", read the way a designer reads it — sweeping curves, parallel lines, one corner radius
around a shape, points that align — and presets a user can choose between without guessing.

## What the bench could not see

The composite `score` ranged 0.919–0.947 across the six presets on the wordmark while the
defects ran from a handful to hundreds. Every metric compared one flattened 1× render with the
source; a pinhole, a 0.4 px sliver or a pillow-shaped square moves ΔE by thousandths. And the
bench only ever ran the default parameters, so no preset had been measured.

**The artifact scorecard** (`bench/artifacts.py`, in every `python -m bench run`, and
`python -m bench artifacts OUT.svg SRC.png --where`) counts what a person sees:

| field | what it counts |
|---|---|
| `pinholes`, `hole_clusters`, `hole_px` | 4× render under-covering an opaque source (a pinhole has a sub-pixel under 0.5) |
| `slivers`, `degenerate`, `thin_strokes` | contours under 4 px² or 1 px thick; under 0.05 px²; strokes under 1 px |
| `wobble_deg_100px` | tangent variation at 0.25 px minus chord variation at 4 px: zero for lines, arcs, clean corners and long S-curves; nicks, hooks and staircases score |
| `inflections` | curvature sign changes at 6 px chords in smooth runs |
| `radius_inconsistent`, `rect_bowed`, `rect_skewed` | rectangles whose corner radii disagree, whose sides bow, or that are 1–6° off square |
| `artifact_index` | a weighted sum of the above |

The 35 vector-truth SVGs scored against their own renders all have `artifact_index` ≤ 2.3.
It also found a bench bug: `seam_index` rendered each top-level element alone and returned 0 for
any upsampled input (one root `<g>`); one render gives the same coverage and sees it.

## Where each defect came from

Each defect class was root-caused by an investigator that proved its mechanism by ablation
and prototyped the fix. Wordmark `artifact_index` per preset at HEAD:
balanced 18.7, logo 23.7, detailed 67.5, flat 210.7, dense 20.0, cutfile 21.1.

1. **Pinholes** — the bled copy an earlier shape draws under a later one was an offset of the
   placed *vertices*, not of the drawn curve; where the fit left the vertices by more than the
   1 px bleed (corners, loose fits, wedge tips) the copy crossed back over the edge. Plus: a
   wedge tip placed past a short arc's far node (the arc runs backwards, a hairpin); corners
   inside a node's approach window kept as breaks (a hook past the node); three shapes
   anti-aliasing their own corner of one point; hole rings drawn as a second copy of the
   inner shapes' outlines; strokes with nothing painted under them; overlap tops traced from a
   coverage field that tiled with nothing. Corpus pinholes 386 → 4.
2. **False gradients** — fills were fitted over the whole region, and the edge band (the
   anti-aliasing plus this source's *sharpening halo*: a dark undershoot at the edge, a light
   lobe 2–3 px inside) alone decided a small shape's end stops. Now the band belongs to the edge:
   stops and solids are fitted on the core (depth > 3 px), "good enough" is still judged over
   every pixel, and a gradient whose range over the core is under 2·tol is the solid (fitted to
   four rows, a ramp can follow the core's noise exactly and carry it across the band).
3. **Detailed's slivers** — at detail 3.5 the rescue threshold (26 levels) sits below the
   halo, so ringing lobes and AA bands were promoted as regions: 22 of Detailed's 41 wordmark
   regions had no interior pixel. `rescue.edge_mix`: a residual pixel whose colour lies on the
   line from its own fill to the nearest other region's fill (within 3 px, at most half way,
   within 10 % of the contrast) is that edge's rendering, and a component mostly made of them is
   not promoted. Detailed 41 → 17 shapes, 7 → 0 strokes.
4. **Bowed straight edges** — `fit_stretch` chose among lines-first, arc and curve by cost at
   the user's tolerance; at 0.6 px one cubic covers a straight run plus its neighbouring arc.
   Now a stretch that is lines-first at 0.4 px stays lines at any looser tolerance
   (`KIND_TOL`), and a two-point cubic is held inside its tangents. A negative result worth
   keeping: a fairness penalty inside `fit_c2` changes nothing measurable — the wobble is at
   junctions and in segmentation, not in the spline.
5. **The V junction** — one fitted gradient per region misses its own colour near an edge, so
   the placement's projection between the two fills jumped between intervals row to row
   (±0.6 px zig-zag). Placement now reads each region's fill plus its own smoothed residual
   (local fills, gated off where the two sides' local colours converge); a vertex with no
   crossing in reach follows its neighbours; a wedge tip is held at its handed-back sliver.
6. **Pixel squares** — `try_rounded_rect` needed three vertices inside each corner, so a
   2–4 px corner was never read; a corner read from 4–7 vertices moves ±0.5 px with its phase on
   the pixel grid; and blur rounds corners: r_read² ≈ r² + (1.86σ)² + 0.58. `rects.py` reads
   the blur across the model's own sides and takes it out, gives a shape one radius, clusters
   radii, sizes and edge levels across shapes, and turns a corner that meets a neighbour into a
   cusp of two mirrored quarter circles. The squares come out as `<rect rx>`.

Combined (Python, wordmark): balanced 4.6, logo 2.7, detailed 6.3, dense 6.4, cutfile 9.2,
zero pinholes in every preset. Flat is the exception (159): posterised bands are iso-contours
of the noisy pixels and are then misread as translucent overlaps.

## Presets

Measured over the corpus at HEAD (mean `artifact_index`, % of items with no visible defect):
balanced 41.9 / 44 %, logo 29.9 / 48 %, detailed 89.4 / 33 %, flat 94.9 / 27 %, dense 32.0 / 51 %,
cutfile 52.9 / 41 %. Each preset's defects trace to one parameter: Detailed's `detail` (+23.5)
and `curve_tolerance` (+19.5), Flat's `gradients=False` (+64.5), Cut file's `layering` (+16.8),
Logo's `curve_tolerance` (bowed edges). Balanced and Photo & dense art looked best because they
were the least artifact-prone, not because they were the most faithful.

A user cannot be expected to know this, so **Auto** traces the candidates concurrently and keeps
the cleanest one whose ΔE is within a slack of the most faithful. Simulated over the corpus it
beats every fixed preset on score, edge F1 and artifact index.

## Result

Both engines, merged (Rust, what the app runs). Wordmark `artifact_index`, the same
visible-only scorecard on both sides:

| preset | HEAD | now | pinholes (HEAD → now) |
|---|---|---|---|
| Balanced | 15.9 | 0.85 | 5 → 0 |
| Logo & icon | 19.3 | 1.02 | 6 → 0 |
| Detailed illustration | 63.9 | 0.91 | 6 → 0 |
| Simplified (was Photo & dense art) | 16.5 | 1.03 | 5 → 0 |
| Flat & poster | 180 | 2.93 | 41 → 0 |
| Cut file | 21.4 | 8.97 | 0 → 0 (shallow cut-out seams remain, by design) |

The pixel squares are `<rect rx>` with one radius; the "e" is solid with a solid white
counter; the V's arm and ribbon meet on one straight edge; Flat's bands are straight strips.

Corpus, default parameters, 104 items (HEAD SVGs re-scored with the same scorecard):
`artifact_index` 41.1 → 15.0, items with no visible defect 47 → 68, pinholes per item
3.74 → 0.05, wobble 135 → 51 °/100 px; bench `score` 0.9581 → 0.9607, ΔE 0.459 → 0.421,
outline error against the vector truth 0.447 → 0.357 px. No class loses score. Flat & poster
over the corpus: `score` 0.899 → 0.919, ΔE 1.44 → 0.81.

`tools/diffcheck.py`: 0 failing (stage, item) pairs over 96 items, no tolerance widened; new
stages `under`, `local_fills`, `placed`, `nodes`, `rects`, `posterize`. The Rust engine is
deterministic (0/104 items differ over repeated runs, Balanced and Detailed).

## Still open

- Busy illustrations (the Silverpeak badge): a forest of small trees merges into one dark
  shape and water reflections come out lumpy. HEAD did the same; it is a segmentation limit
  (fine texture below `detail`), not a geometry defect, and the next place to look.
- Flat & poster leaves a small notch where a band's level line ends on a rounded corner.
- The V node of the wordmark is two 3-way nodes 1.5 px apart (a sub-pixel nub); merging
  tip-to-tip wedges into one node needs the bleed to join copies across the collapsed arc.
- Kept costs: `shadow/inset-well-128` (its inner shadow sits inside the band a core fit
  ignores), `logo/venn-128` (graph-union overlap tops sit ~1 px low on the 2× input).

## Found along the way

- The Rust engine was nondeterministic on 12/104 corpus items at Balanced (18 at Detailed):
  `order.rs` sorted paint-order roots with ties left in HashMap order, and the shadow gate
  summed floats in a parallel reduce. Fixed; 0/104.
- The two engines disagreed wherever a choice tied to rounding: a resampled side of
  16.000000000000004 px got 18 vertices in Python and 17 in Rust; symmetry matched two
  equidistant vertices differently; a square's principal axes are undefined; ramp knots fit
  equally well; Rust's port of numpy's `choice` skipped its tail-shuffle road, so every
  region of 25k–1.25M px was fitted to different pixels (the Silverpeak backdrop ended 3 px
  off). Each is now a named tie with one rule in both engines.
- Fitting fills on the core gave a transparent canvas a radial that modelled a drop
  shadow's falloff; an unpainted region now fits solid, and a shadow on a transparent canvas
  is rebuilt as a filter. Edges on a transparent canvas are placed from premultiplied colour
  (hard edges had come out 0.3–0.45 px fat).

## Sources

Investigation reports and the research note (curve fairing, regularisation, edge localisation,
cleanliness metrics, with primary sources) were written to the session scratchpad; the
research ranking and its measured negative results are summarised above.
