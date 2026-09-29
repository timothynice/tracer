# HANDOFF — work package 2: slivers along soft bands, unpainted rim

Branch `claude/q3-slivers-rim` from 8f09955. Env: `backend/.venv` built, Rust extension built
(`.venv/bin/maturin develop --release -m vexel-rs/Cargo.toml`). Scratch tooling copied under
`$SCRATCH/q3/wp2/` (`stages.py DUMPDIR [box]` prints every dumped label map's ids/sizes and a crop;
`why.py IMAGE PRESET` runs the Python engine and prints why a label is not painted; `alpha.py IMAGE
DUMPDIR LABEL` prints a dumped region's alpha distribution and connectivity).

## Findings so far (speech-balloon-512, dense preset = Auto's pick)

1. **Root cause A — colour noise under near-zero alpha.** The balloon has a ~25 k px halo of alpha
   1–8/255 around it (fluent-color emoji, a faint shadow). Under alpha 1–2 the straight colour is
   unpremultiply quantisation noise (only 0/128/255 per channel: white, magenta, black, (128,128,255)).
   `prepare.inpaint_transparent` inpaints only alpha == 0, so `features` = [L*, a*, b*, 100·alpha]
   carry that noise: the discontinuity map inside the halo is chaos (ridges everywhere, no seeds),
   the canvas' inpainted colour along the halo is the same noise, and the watershed floods the halo
   from the body across the real edge (alpha 255→2, a ~33/px ridge, weaker than the 100/px noise
   steps). labels0/labels_merge: the body/canvas edge sits at the alpha 1→0 step. The body's fill
   is then a 4-channel linear gradient fading to opacity 0 at the top right (`g2`).
2. **Root cause B — the rescue carves the halo out as an "invisible" region.** The rescue residual
   (alpha-weighted colour + alpha difference) is > 1 across the halo and just inside the true edge, so
   `rescue_features` promotes halo + bevel highlight as one region 3 (4010 px: 3772 at alpha ≤ 2, 135
   opaque). Its interior-weighted alpha is 0.016 < INVISIBLE_ALPHA → `invisible` → never painted →
   the opaque highlight pixels (x336-359 y98-107) are the hole; the body's gradient fades to
   transparent there → seams.
3. **Root cause C — specks.** On the true edge the body's fit crosses the real colour: a diagonal
   line of pixels has residual 0.3–0.5 < threshold/2, so the rescue's growth leaves them as isolated
   single-pixel islands of label 2 inside region 3 (`cccbccc`). They are not diagonal chains but
   true islands; `_absorb_small` works per label id and never sees a disconnected fragment of a
   big label. Topology draws each as a sliver (69 slivers).
4. Other items: man-feeding-baby-512 has 28 px of alpha ≤ 8 — its 19 slivers are a different
   mechanism (panel logs in `$SCRATCH/q3/wp2/panel-*.log`). cherries-512(-ds) have a ~1.5 k px halo.

## Plan
- Fix A at the source in `prepare` (both engines): inpaint colour under alpha too low for the colour
  to mean anything (threshold from a survey of colour noise vs alpha level over corpus + heldout).
- Fix C generically: before topology, a four-connected fragment of a label below `min_region`
  enclosed by other regions joins its surroundings (both engines).
- Tests first (tests/test_vexel_*.py), diffcheck `labels0 wedges arcs under`, bench run + compare.
