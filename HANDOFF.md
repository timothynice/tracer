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

5. **man-feeding-baby-512 (Auto = detailed, 19 slivers) and cherries-512-ds (Auto = logo, 14
   slivers) are the rescue-thread mechanism**: a band rescued along the host's edge with a third
   region starts 2 px in (`boundary_band` excludes candidates) and grows 1 px back, leaving a 1-px
   thread of the host between the feature and the third region; on a diagonal it is 8-connected
   only and topology makes a sliver per pixel (`aaabccccbb` in the baby's labels_to_topology at
   x148-172 y58-88; `cccccccbaa` in the cherries at x203-250 y112-158).
6. **Fragment survey** (`$SCRATCH/q3/wp2/fragsurvey.py`, Rust, 4 Auto presets × 224 images =
   896 traces, `frags-before.log`): four-connected pieces of a label below min_region: 0 at
   labels_merge and labels_clear, 11 376 at labels_rescue, 8 240 surviving to labels_to_topology in
   203 of 896 traces. The rescue is the only fragment factory.
7. **Alpha-noise survey** (`alphasurvey.py`, corpus + heldout): median ΔE between a pixel at alpha a
   and its neighbours of higher alpha ≈ 45/a: 46 (a=1), 18 (2), 11 (4), 6 (8), 3.4 (16), 1.5 (32).
   `seed_mask` needs grad < 8 to seed, so below alpha 8 a halo is all ridge and no seed.

## Done (commit a3933db = Python side + tests; Rust port in progress)
- `prepare.COLOUR_ALPHA_FLOOR = 8`: colour under alpha < 8/255 is inpainted from the nearest pixel
  at or above it (alpha kept). Rust `prepare.rs` twin takes the file's u8 alpha.
- `rescue.absorb_shards`: after promotion, every four-connected piece of a host label below
  min_region joins the feature it shares the most four-edges with (ties → lower label); a shard
  touching no feature joins its most-shared neighbour. Integer rule, no watershed (a watershed
  flood zigzagged a thread where the two sides tie). Rust `rescue.rs` twin, same rule.
- Tests (all failed before the fix, pass on Python now): `test_vexel_prepare.py::
  test_colour_under_faint_alpha_is_inpainted_from_the_nearest_pixel_that_shows`,
  `test_vexel_rescue.py::test_carving_a_feature_leaves_the_host_in_no_shards`,
  `test_vexel_engine.py::test_a_faint_noise_halo_leaves_the_shape_whole_and_opaque` (default and
  dense-like params; scene `conftest.noisy_halo_disc`), `test_vexel_backends.py::
  test_both_backends_read_a_faint_noise_halo_the_same_way`.

## After the fix (Rust, probe with Auto)
- speech-balloon-512: Auto = logo, art 9.23 (was 229; VTracer 52), slivers 0, pinholes 0, f1 1.000.
  -ds: logo, art 4.00, slivers 0. balanced 22.6 / 6.1.
- man-feeding-baby-512: Auto = detailed, slivers 0 (was 19), art 80.3 (was 114; VTracer 51) — what
  is left is wobble 261°/100px along x127-136 y110-150 and 13 inflections (`panels2/`), a different
  mechanism (not yet looked at).
- cherries-512-ds: slivers 0, art 3.5, but ΔE 0.97 → 2.29: the two cherries now trace as identical
  circles and `reuse` wrote the second as `<use x="240" fill="url(#g3)">` — resvg resolves a
  userSpaceOnUse gradient in the clone's translated space, so the copy got the wrong stretch of its
  gradient (right disc colour error 8 → 34 levels). Latent bug exposed. Fixed in `reuse.py`/`reuse.rs`:
  a shape painted by a gradient (`url(#` in its attrs) is never a `<use>`, like one under a filter.
  Test `test_vexel_symmetry.py::test_a_copy_painted_by_a_gradient_is_written_in_full` (fails at HEAD).
- diffcheck (first run, before the reuse fix): rgb, features, labels0, wedges all ok; arcs/under running.

## Next
- Rebuild Rust after diffcheck finishes; pytest; `tools/diffcheck.py rgb features labels0 wedges arcs under`.
- Sweep COLOUR_ALPHA_FLOOR ∈ {4, 8, 16} on the corpus bench if 8 leaves regressions.
- `python -m bench run --engines vexel --no-media --workers 3` + compare vs bench/baselines/vexel.json;
  per-item check; heldout items with vexel-auto (`panel3.py`); re-run fragsurvey after.
