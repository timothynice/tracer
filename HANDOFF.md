# WP1 thin regions and strokes — handoff

Branch `claude/q3-thin-regions` off 8f09955. Worktree `.claude/worktrees/agent-abcb7c55071980f21`.
Env built (`backend/.venv`, Rust extension installed; rebuild with
`.venv/bin/maturin develop --release -m vexel-rs/Cargo.toml` after any Rust change). Scratch:
`$SCRATCH/wp1/` (probe dumps; helper scripts `uncovered.py`, `stages.py`, `fitprobe.py`,
`alphasurvey.py`, `snapsurvey.py` (→ `snaps.jsonl`), `upsurvey.py`, `refine_exp.py`, `toggle.py`
(bench metrics per item with each change switched back), `inpdiff.py`, `settlediff.py`, `cmpbench.py`
(per-item results.json compare), `cmprec.py` (records.jsonl compare), `subset.py`, `heldout-sub/`).

## Root causes, all fixed in both engines

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
   field a gradient the size of a real edge. Fix (final, after two false starts): the field is
   inpainted from the nearest visible pixel's *settled* colour — `settle_rim`, the alpha-weighted
   5×5 mean round a pixel below `INPAINT_ALPHA = 32` (`RIM_REACH = 2`) — and visible pixels keep the
   colour they have. A hard alpha floor (sources ≥ 32 only) fixed thin-mark but moved two shadows'
   faint halos to the caster's colour (transparent-bg-128/512 −0.012/−0.007 score) and split
   alpha-fade-512; settling the visible low-alpha pixels themselves did the same to the halo.
   `toggle.py` on the seven items involved: field-only settling restores transparent-bg exactly and
   improves alpha-fade-512 (0.956 → 0.964), alpha-fade-128 (0.830 → 0.946), thin-mark-128
   (0.939 → 0.966), cutout-512-ds (0.952 → 0.998), thin-mark-512-ds (0.774 → 0.979).
   Rust `prepare::settle_rim`, `inpaint_transparent(rgb, h, w, alpha8, round_f32)` (float32 rounding
   of the inpainted colour in `prepare`, as the Python keeps rgb in float32; none in the upsample).

B2. **thin-mark-128 fragmented at 2×** — `upsample2x` resamples channels straight, so the black under
   transparency was mixed into every edge at 2× (source-pixel colour spread 29 levels vs 1). Fix: the
   field is inpainted (same function) before the resample, both engines. The upsample rule still
   holds (`upsurvey.py`: on all 4 corpus items it fires on, the 2× outline is better, 0.103 vs
   0.494 px); only the 2 px thin-bars fixture is now better direct, because strokes are sub-pixel
   (its test now asserts both < 0.25 px and 2× < 0.5× direct on thin-mark-128).

C. **stroke fidelity knife-edge / wobble** — `stroke_fidelity` rasterised the centreline to pixels and
   the centreline was the raw medial axis (half a pixel off on a 2 px line by the tie-break). Fix:
   `_refine_centreline` (bilinear coverage centroid across the stroke, ±(w/2+1) px at 0.25 px, 2
   passes; `refine_exp.py`: rings 1–3 px at 4 sub-pixel offsets → centreline 0.06–0.08 px RMS, fidelity
   0.02–0.05; nearest-pixel samples 0.11 / 0.03–0.09; more passes or reach change nothing) and an
   exact point-to-polyline distance with box coverage in `stroke_fidelity` (`_polyline_distance`).
   Rust `strokes::refine_centreline`, `sample_bilinear`, `polyline_distance`; parity 1e-9 on the ring.

D. **wheelchair pinholes (3)**: not strokes — region 36 (an 8 px neck under region 30) has both its
   junction nodes placed at the same point in the middle of its top edge (121.16, 427.4) and
   (121.17, 427.5): the canvas–30 arcs end as wedge tips (`tip1=1`, sliver 16 px) because region 30's
   bottom edge (with 36) is collinear with its canvas edge, so `_extend_wedges`/`_junctions` read a
   flat-bottomed region as a wedge and pushed both tips to the centre; the neck's outline is then a
   figure-8. A junction/wedge fix (a wedge needs an angle between its two edges), not done here.
   spiral-notepad's 6 pinholes went to 0 with B.

## Tests added / changed
- tests/test_vexel_prepare.py::test_inpainting_ignores_the_colour_of_nearly_transparent_pixels
- tests/test_vexel_strokes.py::test_a_ring_off_the_pixel_lattice_is_a_faithful_stroke[1.0|2.0],
  ::test_the_rust_stroke_stage_agrees_on_the_refined_centreline
- tests/test_vexel_topology.py::test_an_axis_snap_never_moves_a_line_end_further_than_the_placement_knows,
  ::test_a_slightly_slanted_bar_keeps_its_sides_where_its_pixels_are[python|rust] (skewed rounded bar
  with a bevel strip: 223 px uncovered before, 1 after)
- tests/test_vexel_upsample.py::test_the_upsample_mixes_ink_with_ink_not_with_the_colour_under_transparency;
  thin-bars test rewritten (see B2)
- tests/test_vexel_curves.py snap test: 0.2/0.15 px rises snap, a 0.6 px rise stays

## Verification so far
- diffcheck `strokes skeleton segments arcs`: 0 failing pairs over 96 items (before the final B).
- Full pytest green; cargo test green (before the final B). Targeted tests green after it.
- Bench (hard-floor version, `bench1`): logo +0.0078, gradient +0.0037, flat −0.0001, shadow −0.0009;
  per item thin-mark-512-ds 0.774 → 0.979 (art 58.7 → 0.42, outline 1.23 → 0.023, f1 1.0, banding 0).
- Held-out subset (`h2h-sub`, hard-floor version): u2049-512 pinholes 2 → 0, outline 0.44 → 0.18,
  dE 0.35 → 0.12, seam 12935 → 4109; -ds alike; spiral-notepad pinholes 6 → 0; wheelchair 3 → 3.

## In flight (restart if killed)
- bench `wp1/bench2` (final code), h2h subset `wp1/h2h-sub2`, diffcheck `rgb features grad upsample
  labels0` → `wp1/diffcheck-prep.log`. Then: `bench compare` + `cmpbench.py` per item; re-run
  diffcheck `strokes skeleton segments arcs` and the rest (`fills edge_mix wedges local_fills`,
  `placed nodes posterize rects under`) in background chunks (whole-run background jobs got killed
  at ~25 min); full pytest + cargo test; commit; final report.
