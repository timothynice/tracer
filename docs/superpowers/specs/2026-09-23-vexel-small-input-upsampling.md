# Vexel: upsampling small inputs before tracing (selection rule)

**Status:** built 2026-09-23 (`vexel/upsample.py`, `vexel-rs/src/upsample.rs`, `tests/test_vexel_upsample.py`).

**As built, where it departs from the design below.** Calibration over the 128 px corpus items showed that the
selection signal is *thin structure*, not softness: every item that gained had a region under 2.4 px wide
(2·area/perimeter on the direct trace's label map) and every item that lost had none under 7.6 px; boundary
density and transparency did not separate them (venn has semi-transparent regions and lost). So the rule is
`max(h, w) ≤ 192` and `thinnest_region < 2.2`, computed from the direct trace's own labels — renderer-free, so
both engines apply it identically, and the render-based guard in step 5 is not needed. Stripes-128 and
alpha-fade-128 gained in the spike without thin regions and are not selected. The upsample is a fixed-weight
Lanczos-3 at exactly 2× (two literal tap sets shared by both engines, byte-identical by `diffcheck upsample`). The
result keeps the original viewBox with the drawing in `<g transform="scale(0.5)">`, because `finish` normalises the
root viewBox to the image; gradients, filters and stroke widths follow the element's user space, and
`bench.geometry.root_scale` / the frontend parser honour the group.

**Measured (bench, never → auto, 128 px items the rule selects):** thin-mark outline 2.24 → 0.26 px and score
0.894 → 0.934; blobs 0.130 → 0.075 (score −0.003); overlap 0.117 → 0.060 (score +0.000); wedge-fan 0.208 → 0.181
(score −0.008); disc 0.281 → 0.109 (score −0.003). Files grow 2–3× and traces take 3–5× longer on those five
items only. Two variants were rejected by the same A/B: a 3.5 px threshold also selected sticker-128, whose gradient
splits into bands at 2× (banding 0.05 → 0.94, score −0.031) although its direct trace no longer needs help (0.147
px), and scaling `min_region`/`curve_tolerance` to source pixels for the inner pass cured neither the banding nor kept
the outline gain (blobs back to 0.119). The composite score is not the right gate for this rule — it moves a few
thousandths on fill statistics while the geometry improves by tenths of a pixel — so the gate is `outline_px` on the
selected items with `score` watched per item.

## Problem

At 128 px the anti-aliasing model that places edges has two pixels of evidence per edge, and soft, thin or
low-contrast inputs land 0.4–1.9 px off their truth (sticker 0.41, alpha-fade 1.86, thin-mark 1.73 `outline_px`).
Tracing a 2× Lanczos upsample instead and scaling the SVG by half cut those to 0.12, 0.41 and 0.76, a 38 % mean
gain over the 128 px class — while making every already-sharp item worse (cutout 0.013 → 0.121, hex-nest 0.027 →
0.068, linear-4stop 0.007 → 0.147), because the resampler's ringing and its own anti-aliasing are read as edge
position. Bilinear is worse than direct on average. So the upsample must be *selected*, never applied blind.

## Design

1. **Trigger only for small inputs**: longest side ≤ `UPSAMPLE_MAX_SIDE` (192 px), so the cost (4× pixels) is
   bounded and the class that benefits is the one that gets it.
2. **Trace direct first**, always. It is the fallback and the reference for the decision.
3. **Decide from the direct trace's own evidence of softness**, not from the image alone:
   - `seam_ppm` of the direct trace against its own source above `SOFT_SEAM_PPM` (coverage the fitted edges
     could not account for is the signature of a soft or thin input), or
   - the stroke stage found thin groups whose width is under 2 px, or
   - the label map has more than `SOFT_SPECK_SHARE` of its regions under `min_region` px before rescue.
   Sharp inputs (tilted-squares, cutout, hex-nest) fail all three; the items that gained pass at least one.
4. **When selected**: upsample 2× with Lanczos (PIL `Resampling.LANCZOS`; the Rust twin uses the same kernel,
   `a = 3`, and `tools/diffcheck.py` gets an `upsample` stage holding the two to the last float32 bit), trace at 2×,
   and write the SVG with the original `viewBox` so the scale is in the file, not in the coordinates.
5. **Keep whichever trace the bench's own gate prefers when both exist**: if the upsampled trace's `seam_ppm`
   against the *original* source (rendered back at 1×) is not lower than the direct trace's, keep the direct one.
   This is the guard the spike lacked and what protects the sharp items.
6. Parameter: `VexelParams.upsample: Literal["auto", "never", "always"] = "auto"`, group Regions, so the bench
   sweep can measure `never` against `auto` and the user can force either.

## Acceptance

- `python -m bench sweep --engine vexel --param upsample=never:auto` over the 128 px class: `outline_px` down
  ≥ 30 % on the items the spike named, no item's `score` down more than 0.002, sharp items byte-identical to
  `never` (the guard kept the direct trace).
- Tests: a soft fixture selects the upsample and improves; a sharp fixture is not selected; `always` on a sharp
  fixture is worse than `never` (documents why the rule exists); Python and Rust agree.
- Elapsed for a 128 px item within 5× of the direct trace (two traces plus a render).
