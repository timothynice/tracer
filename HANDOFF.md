# WP1 thin regions and strokes — handoff

Branch `claude/q3-thin-regions` off 8f09955. Worktree `.claude/worktrees/agent-abcb7c55071980f21`.
Env built (`backend/.venv`, Rust extension installed; rebuild with
`.venv/bin/maturin develop --release -m vexel-rs/Cargo.toml` after any Rust change). Scratch:
`$SCRATCH/wp1/` (probe dumps; helper scripts `uncovered.py`, `stages.py`, `fitprobe.py`,
`alphasurvey.py`, `snapsurvey.py` (→ `snaps.jsonl`), `upsurvey.py`, `refine_exp.py`, `toggle.py`
(bench metrics per item with each change switched back), `strokeab.py` (old vs new stroke stage per
item, `strokes_old.py` = 8f09955's module), `gatesurvey.py` (→ `gates.jsonl`) + `gateanalysis.py`,
`stroketol.py` (stroke fit tolerance vs seam), `seamwhere.py`, `inpdiff.py`, `settlediff.py`,
`cmpbench.py` (per-item results.json compare), `cmprec.py` (records.jsonl compare), `subset.py`,
`heldout-sub/`).

## Root causes, all fixed in both engines (commits 050a901 … 6508348)

A. **u2049 strips (464 px uncovered)** — `topology._snap_axis` / `curves.snap_axis_lines` snapped any
   line within `snap_axis_deg` (1.5°) to the axis with no bound on how far its ends move; the "!" bar's
   sides are drawn 1.3° off vertical (7 px drift over 330 px) and were turned 3.6 px at each end.
   Fix: `curves.SNAP_END_MOVE = 0.15` px (same bound as `regularity.END_MOVE_MAX`): a snap that
   would move an end further is not applied. Rust: `curves::SNAP_END_MOVE`, `topology::snap_axis`.
   Survey (`snapsurvey.py`, 4427 candidates over corpus+heldout): 73 % move < 0.02 px, 84 % < 0.05
   (median 0.0°, real axis lines); 0.05–0.15 are short lines (median 19–23 px) at 0.3–0.5°, what
   their placement noise allows; above 0.15 the lengths climb 29 → 41 → 63 → 211 → 323 px at
   0.8–1.3°: drawn off-axis, u2049 at the top.

B. **thin-mark-512-ds serrated triangle, ring in 39 fragments** — `prepare.inpaint_transparent` took
   the transparent field's colour from the nearest pixel with alpha > 0; in resampled assets alpha 1–15
   pixels carry un-premultiplication noise (±128/alpha levels; `alphasurvey.py`: median error
   68/44/30/11/4/3 levels for alpha 1–3/4–7/8–15/16–31/32–63/64–127), and its Voronoi seams gave the
   field a gradient the size of a real edge. Fix: the field is inpainted from the nearest visible
   pixel's *settled* colour — `settle_rim`, the alpha-weighted 5×5 mean round a pixel below
   `INPAINT_ALPHA = 32` (`RIM_REACH = 2`) — and visible pixels keep the colour they have. A hard alpha
   floor (sources ≥ 32 only) fixed thin-mark but moved two shadows' faint halos to the caster's colour
   (transparent-bg-128/512 −0.012/−0.007) and split alpha-fade-512; settling the visible low-alpha
   pixels themselves did the same to the halo. Rust `prepare::settle_rim`,
   `inpaint_transparent(rgb, h, w, alpha8, round_f32)`; the rgb stage is bit-exact.

B2. **thin-mark-128 fragmented at 2×** — `upsample2x` resamples channels straight, so the black under
   transparency was mixed into every edge at 2× (source-pixel colour spread 29 levels vs 1). Fix: the
   field is inpainted (same function) before the resample, both engines. The upsample rule still
   holds (`upsurvey.py`: on all 4 corpus items it fires on the 2× outline is better, 0.103 vs 0.494 px).

C. **stroke fidelity knife-edge / wobble** — `stroke_fidelity` rasterised the centreline to pixels and
   the centreline was the raw medial axis (half a pixel off on a 2 px line by the tie-break). Fix:
   `_refine_centreline` (bilinear coverage centroid across the stroke, ±(w/2+1) px at 0.25 px, 2
   passes; step count truncated — round(4.5) differs between the languages) and an exact
   point-to-polyline distance with box coverage in `stroke_fidelity` (`_polyline_distance`), whose
   band ends at an open stroke's ends (the cap is `_finish_ends`'s guess; a square-ended 3 px bar
   scored 0.12–0.14 for a round cap's ink it does not have). The exact measure runs at ~0.67× the old,
   so the gate `stroke_tolerance` default is 0.13 (`engine.py`, `engine.rs`): over corpus+heldout
   (`gates.jsonl`, first survey) every drawn line scored ≤ 0.118, the stems and blobs a stroke would
   mangle ≥ 0.141; benches at 0.13/0.15/0.2 (`bench-g13`, `bench-g15`, `bench3`): 0.13 best (flat
   −0.0001, logo +0.0079; studi0mail-logo-dark and blobs-128 back to baseline). The centreline is
   fitted at `STROKE_FIT_SHARE = 0.5` of the curve tolerance (`stroketol.py`: at 0.4 the cubics
   through the 150 px ring sagged 0.14 px inside it, seam 16651 ppm; at 0.2, 11399 with 24 segments
   for 16; at 0.1 the fit chases noise, 209 segments, artifact index 0.4 → 6.7). Rust
   `strokes::refine_centreline`, `sample_bilinear`, `polyline_distance`, `STROKE_FIT_SHARE`.

D. **wheelchair pinholes (3)**: not strokes — region 36 (an 8 px neck under region 30) has both its
   junction nodes placed at the same point in the middle of its top edge; the canvas–30 arcs end as
   wedge tips because region 30's bottom edge (with 36) is collinear with its canvas edge, so
   `_extend_wedges`/`_junctions` read a flat-bottomed region as a wedge and pushed both tips to the
   centre; the neck's outline is then a figure-8. A junction/wedge fix, not done here.

## Verification (final build 6508348)
- diffcheck: `rgb features grad upsample labels0` 0 failing / 96 (rgb bit-exact); `strokes skeleton`
  0 failing (`diffcheck-strokes4.log`); `segments arcs` 0 failing (`diffcheck-arcs2.log`). Full pytest
  green; cargo test green.
- Bench `bench4` vs baseline: flat −0.0002, gradient +0.0062, logo +0.0079, shadow +0.0000.
  No item down more than 0.002 (sticker-512-ds 0.967 → 0.965, art 5.1 → 9.2; alpha-fade-512-ds
  0.952 → 0.950, outline 6.3 → 2.3 but seam 0 → 599; sticker-128, multi-shape-512, radial-disc-512
  −0.001). Up: thin-mark-512-ds 0.774 → 0.980, thin-mark-512 0.962 → 0.970, thin-mark-128
  0.937 → 0.939, logomark-128 0.951 → 0.984, cutout-512-ds 0.952 → 0.998, alpha-fade-128
  0.830 → 0.946, alpha-fade-512 0.956 → 0.964, studi0clip-mark-light 0.964 → 0.967.
- Held-out subset (`h2h-sub4`, Auto): u2049-512 dE 0.350 → 0.124, f1 0.955 → 0.994, outline
  0.442 → 0.184, art 26.4 → 24.0, pinholes 2 → 0, seam 12935 → 4109; u2049-512-ds dE 0.358 → 0.155,
  outline 0.422 → 0.173, pinholes 2 → 0, seam 11127 → 1769; spiral-notepad-512 pinholes 6 → 1,
  art 47.4 → 40.2; wheelchair-512 pinholes 3 → 3, art 105 → 96.7.
- Gate re-survey with the end-zone measure (`gatesurvey.py` → `gates.jsonl`) was in flight to
  confirm 0.13 against the final measure; the bench at 0.13 above is the decision's evidence.
