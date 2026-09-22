# Vexel spikes: go/no-go

Three ideas from the research report that were too uncertain to plan as tasks. Each has a protocol, a measured
result where it could be run in this environment, and a decision. A "go" becomes its own spec and plan.

## 1. Sub-pixel deblurring for ≤ 128 px inputs — **conditional go**

**Protocol.** Every 128 px synthetic item with a vector truth and no blur filter (18 items). Trace it directly;
upsample 2× (PIL bilinear, PIL Lanczos), trace the 256 px image, render the result back at 128 px (the SVG's
viewBox does the scaling); compare `outline_px` against the truth. A trained upscaler was not tried: the
classical resamplers already separate the cases.

**Result** (`outline_px`, lower is better; scratch script `spike_upsample.py`):

| item | direct | 2× bilinear | 2× Lanczos |
|---|---|---|---|
| blobs-128 | 0.1300 | 0.1870 | 0.0754 |
| low-contrast-128 | 0.0150 | 0.0104 | 0.0060 |
| mosaic-128 | 0.0106 | 0.0013 | 0.0363 |
| overlap-128 | 0.1171 | 0.1089 | 0.0707 |
| sticker-128 | 0.4110 | 0.1231 | 0.1177 |
| stripes-128 | 0.1571 | 0.7046 | 0.0315 |
| alpha-fade-128 | 1.8626 | 2.1238 | 0.4060 |
| linear-4stop-128 | 0.0072 | 0.0066 | 0.1470 |
| multi-shape-128 | 0.1485 | 0.1345 | 0.1733 |
| radial-disc-128 | 0.0194 | 0.2293 | 0.1412 |
| cutout-128 | 0.0132 | 0.0194 | 0.1207 |
| hex-nest-128 | 0.0271 | 0.3605 | 0.0675 |
| ring-128 | 0.0106 | 0.0368 | 0.0312 |
| thin-mark-128 | 1.7299 | 0.5148 | 0.7589 |
| tilted-squares-128 | 0.0119 | 0.0255 | 0.0235 |
| triangle-bar-128 | 0.0611 | 0.0178 | 0.0654 |
| venn-128 | 0.4224 | 0.8752 | 0.8965 |
| wedge-fan-128 | 0.2084 | 0.2312 | 0.1453 |

MEAN direct 0.2980  bilinear 0.3173 (+6%)  lanczos 0.1841 (-38%)  over 18 items

**Reading.** The mean falls 38 % with Lanczos, but the gain is concentrated where the input is soft, thin or
low-contrast (alpha-fade 1.86 → 0.41, sticker 0.41 → 0.12, stripes 0.157 → 0.032, thin-mark 1.73 → 0.76, blobs
0.130 → 0.075), and the same upsample *hurts* every item that was already sharp (cutout 0.013 → 0.121, hex-nest
0.027 → 0.068, radial-disc 0.019 → 0.141, linear-4stop 0.007 → 0.147, venn 0.42 → 0.90): the resampler's ringing
and its own anti-aliasing model are read as edge position. Bilinear is worse than direct on average.

**Decision.** Go, but not as a switch: a follow-up spec needs a *selection rule* — upsample only when the image
is small and the direct trace reports its input as soft (a high `seam_ppm`/coverage deficit along its own edges,
or a stroke-recovery band under 2 px), and keep the direct trace otherwise — and a bench sweep over the 128 px
class with `score` as the guard. Estimated effort: one task; the plumbing (`render_downsampled` in `bench/synth.py`
already produces the degraded pairs) exists.

## 2. Glyph fitting for wordmarks — **deferred, not run**

**Protocol.** On `vexel-wordmark-512.png` and two wordmarks imported via `bench import`: detect text lines with a
lightweight OCR (tesseract via CLI), fit glyph outlines from a font candidate set with an affine solve per line,
and measure `outline_px` on the text region against the traced text. Go if ≤ 0.1 px and bytes fall ≥ 3×.

**Why not run.** No OCR engine or font candidate set is available in this environment, and the wordmark's truth
is a raster, so `outline_px` cannot be measured for it. The corpus has one real wordmark and no vector truth for
text; the protocol needs two more imported with their fonts known. Decision when those exist.

## 3. Semantic layer prior — **deferred, not run**

**Protocol.** On `flat/overlap` and `logo/venn`, group regions by a SAM-style segmentation before the merge stage
and measure `paths` against the truth's path count and `outline_px`. Go if truth path counts are matched on ≥ 80 %
of overlap items with no `outline_px` regression.

**Why not run.** No segmentation model is available here. Note that Tasks 9–10 already moved the two items the
protocol names: venn's boundary is exact circles and overlap's corners sit on their lines; the remaining gap on
venn (`outline_px` 0.43) is the overlap decomposition's semi-transparent tops, a fills question the prior would not
answer by itself.
