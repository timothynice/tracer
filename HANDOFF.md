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

- man-feeding-baby's remaining wobble (x127-136 y110-152): the source edge is aliased (alpha 0 →
  241..255 in one pixel, no partial coverage), so placement has nothing to read, the vertices stay on
  the pixel-edge midpoints and the fitter draws a two-point cubic per step. A fitter/placement
  question for hard-edged art (fit a line through a binary-coverage staircase), not this package's
  mechanism; left for a follow-up.
- The scratchpad `compare/` tree is empty here (no competitor records); VTracer numbers come from
  the brief. `$SCRATCH/q3/wp2/heldout5/` is a 5-item held-out subset (manifest + truth SVGs) for
  `bench/headtohead.py run --corpus … --configs vexel-auto`.
- Commits so far: b3959a5 notes, a3933db python fix + tests, 331c1a0 rust port, 33a4d6d reuse fix.

- Full pytest on the Python engine (VEXEL_BACKEND=python, backends file skipped): all passed.
- diffcheck first run (before the reuse fix, killed with the session at exit 144 half way through
  `arcs`): rgb, features, labels0, wedges all ok; arcs ok on every item reached (max |Δ| ≤ 1e-7).
- COLOUR_ALPHA_FLOOR sweep (`floorsweep.py`, Python engine, Balanced, the 65 corpus+heldout items with
  ≥ 200 px of alpha 1..31; sums): T=1 (old behaviour) ΔE 25.77 art 1780 slivers 4 pinholes 25 seam
  365k; T=4 22.19 / 1155 / 3 / 29 / 191k; T=8 21.43 / 1042 / 3 / 14 / 164k; T=16 21.07 / 1010 / 3 / 17
  / 136k. T=32 and the per-item deltas were lost with the session; `floorsweep2.log` re-runs 8/16/32.

## Round 2 (after the first full verification)
- diffcheck over all 96 corpus PNGs: rgb, features, labels0, wedges, arcs, under all 0 failing
  (run in `--filter` batches: the harness kills any background job at ~exit 144 after ~30 min).
  pytest full suite: Python backend all pass; Rust backend all pass; cargo test ok.
- First corpus bench (bench-after/, compare_items.py): totals ΔE 43.1→40.1, artifact 1337→1021,
  seam 433k→251k, outline 28.2→23.8, score 99.965→100.096, but regressions: shadow/inset-card-512(-ds)
  ΔE 0.054→0.363 F1 1→0.82 elements 2→4, inset-well, disc-512 ΔE, thin-mark-128 art 134→175 slivers
  3→7 elements 19→42, alpha-fade-512 art 1.3→6 elements 3→5, blobs-128, sticker-512-ds, logomark-128
  seam. Ablation (`ablate.py`): inset-card/inset-well/disc = the shard rule; thin-mark-128/alpha-fade
  = the alpha floor; nothing from the reuse fix.
- Cause 1 (shards): on inset-card the shards are the card's own outer AA row at the four rounded
  corners (1–4 px bits between the inset band and the backdrop). Given to the band, the band pokes
  through to the backdrop, is no longer enclosed by the card, and the inset-shadow model fails
  (filters 0, paths 4). At HEAD the shadow stage absorbed the bands before topology, reconnecting
  those bits. FIX: the absorb moved out of the rescue into `engine.absorb_shards`, run after the
  shadow stage (both engines): every 4-piece below min_region of a label that has a piece ≥
  min_region; each pixel to the nearest neighbouring region as `split_rim` does (distance, own
  colour, lower label), never its own label, never an invisible one while a visible one is as near;
  a label whose every piece is small (dotted line) is left alone. Test
  `test_vexel_engine.py::test_shards_of_a_host_join_their_surroundings_but_a_family_of_small_pieces_stays`;
  the rescue test now pins the thread/islands the rescue leaves.
- Cause 2 (floor): thin-mark-128 is ≤192 px → 2× Lanczos upsample of the RAW rgba, so the black
  under alpha 0 bleeds into the ringing's colour; unconditional inpainting of alpha<8 turned the <8
  ring pink while the 8–33 ring kept black-mixed colour → a dozen faint regions. alpha-fade-512's
  tail is a synthetic straight-alpha ramp whose faint pixels are real colour; nearest ≥8 pixel is the
  green disc → green tail pieces. FIX: inpaint only where the colour is unpremultiply noise, i.e. on
  the 255/a grid (every channel = round(k·255/a) ± GRID_TOL 1.0) — `prepare.unpremultiply_noise`,
  Rust `is_unpremultiply_noise`; off-grid faint colours are kept. Prepare test extended (off-grid
  kept, ±1 rounding counted); `conftest.noisy_halo_disc` now draws alpha-1 noise from {0,255} only.
- Rust rebuilt with both; Python changed-test set passes (7). Rust verification pending.

## Round 3 (round 2 verified: diffcheck 6 stages 0 failing over 96, Rust suite pass, but the bench
## regressed further: inset-card ΔE 0.486 F1 0.707, thin-mark-128 slivers 16, alpha-fade-512 art 10.7)
- inset-card: both engines agree (trace_labels exact, 2 paths + filter) and are wrong the same way:
  the inset bands are NOT absorbed by the shadow stage (kept, painted through the filter) and the
  card's shape is "card + what it encloses"; once the corner bits join the band the band touches the
  backdrop, the recomputed enclosure drops it, and the card is painted without its band ring. FIX:
  the shard pass keeps the enclosure computed before it (both engines) — the shards are the host's
  edge; moving their pixels is bookkeeping for the boundary build, not a change of what lies inside.
- alpha-fade-512: the synthetic corpus is rendered premultiplied, so its ramp tails are ON the grid
  (153,102,255,5), (170,85,255,3) … — the grid rule is right; the nearest-source inpainting is not
  (the nearest reliable pixel is the foreign disc). At alpha 1–2 no consistency check can work (the
  cell is ±64 or wider). FIX: the colour under faint on-grid noise is the alpha-weighted mean of the
  faint noise pixels within `NOISE_RADIUS` 3 (7×7 brings ±64 to ±9 levels ≈ 3.5 ΔE, under the seed
  threshold) — `prepare.smooth_faint_noise`/`_box_sum` with a fixed add order, Rust `box_sum` the
  same order, result cast through f32; alpha-0 inpainting unchanged (nearest α>0 pixel).
- thin-mark-128: (0,0,0) is on every grid, so the near-black 2× ringing was half-inpainted. The
  ringing carries black because `upsample2x` resamples the RAW rgba; FIX: the 2× input takes the
  prepared colour, floor(rgb+0.5) as u8 (both engines; tie-safe rounding).
- Tests: prepare test rewritten (checker noise → smooth mean, off-grid kept, alpha-0 nearest).
  Python changed-test set passes; Rust rebuilt (pending verification).

## Round 3 verification (commit 52cd362 + CLAUDE.md)
- Rust suite: all pass. diffcheck rgb/features/labels0/wedges: 0 failing over 96; arcs+under batches
  flat/gradient/shadow/real 0 failing (logo batch finishing at hand-off time).
- bench (bench-after3/): every class at or above baseline (flat +0.0002, gradient +0.0065, logo
  +0.0058, shadow +0.0003, `ok`). Totals ΔE 43.1→38.7, artifact 1337→922, slivers 18→15, pinholes
  5→5, seam 433k→274k, outline 28.2→20.2, edge_f1 101.55→101.97, score 99.965→100.328. No per-item
  score / edge_f1 / pinhole regression. Single metrics still above baseline: blobs-128 art 46→52
  (wobble 185→207), sticker-512-ds art 5.1→9.2 (inflections 4→8), alpha-fade-512 ΔE 0.217→0.246 (art
  1.3→0.67, seam 54→41), alpha-fade-512-ds art 2.1→4.8 (outline 6.3→1.8, F1 .937→1.0), thin-mark-128
  slivers 3→7 (ΔE .71→.37, art 134→50), thin-mark-512-ds slivers 0→3 / art 59→63 (ΔE .58→.34, score
  .77→.91), inset-well-512 art 0→0.67, radii-128 art 53→55 (F1 .914→.950), logomark-128 seam
  8k→15k (ΔE .46→.29, art 88→15), cutout-512-ds seam 54→144 (art 30→10), studi0clip ΔE .164→.193
  (art 100→2.2).
- held-out with vexel-auto (h2h5c/): see the report table.
- Remaining: man-feeding-baby's artifact index (81 vs VTracer 51) is aliased-edge wobble, a
  fitter/placement question; the balloon's seam_ppm 2400 (target < 500) is the outline's
  ~0.26 px inward bias along the whole rim (outline_px 0.264), not a hole.
- Sweep COLOUR_ALPHA_FLOOR ∈ {4, 8, 16} on the corpus bench if 8 leaves regressions.
- `python -m bench run --engines vexel --no-media --workers 3` + compare vs bench/baselines/vexel.json;
  per-item check; heldout items with vexel-auto (`panel3.py`); re-run fragsurvey after.
