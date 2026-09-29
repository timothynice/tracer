# WP1 thin regions and strokes — handoff

Branch `claude/q3-thin-regions` off 8f09955. Worktree `.claude/worktrees/agent-abcb7c55071980f21`.
Env built (`backend/.venv`, Rust extension installed; rebuild with
`.venv/bin/maturin develop --release -m vexel-rs/Cargo.toml` after any Rust change). Scratch:
`$SCRATCH/wp1/` (probe dumps, helper scripts `uncovered.py`, `stages.py`, `fitprobe.py`,
`alphasurvey.py`, `snapsurvey.py`, `upsurvey.py`, `refine_exp.py`).

## Root causes (all four fixed in both engines, commits 050a901 + 75d9c95)

A. **u2049 strips (464 px uncovered)** — `topology._snap_axis` / `curves.snap_axis_lines` snapped any
   line within `snap_axis_deg` (1.5°) to the axis with no bound on how far its ends move; the "!" bar's
   sides are drawn 1.3° off vertical (7 px drift over 330 px) and were turned 3.6 px at each end.
   Fix: `curves.SNAP_END_MOVE = 0.15` px (same bound as `regularity.END_MOVE_MAX`): a snap that
   would move an end further is not applied. Rust: `curves::SNAP_END_MOVE`, `topology::snap_axis`.
   Survey of every snap candidate over corpus+heldout running (`snapsurvey.py` → `snaps.jsonl`);
   check its distribution of end moves before finalising the constant.

B. **thin-mark-512-ds serrated triangle, ring in 39 fragments** — `prepare.inpaint_transparent` took
   colour from the nearest pixel with alpha > 0; in resampled assets alpha 1–15 pixels carry
   un-premultiplication noise (±128/alpha levels; survey `alphasurvey.py`: median error 68/44/30/11/4/3
   levels for alpha 1–3/4–7/8–15/16–31/32–63/64–127), and its Voronoi seams gave the transparent field
   a gradient the size of a real edge. Fix: `INPAINT_ALPHA = 32` (8-bit): every pixel below it takes the
   colour of the nearest pixel at or above it (fallback: any alpha > 0 when nothing reaches 32).
   Rust `prepare::INPAINT_ALPHA`, `inpaint_transparent(rgb, h, w, alpha8)`.

B2. **thin-mark-128 fragmented at 2× (found by B)** — `upsample2x` resamples channels straight, so the
   black under transparency was mixed into every edge at 2× (source-pixel colour spread 29 levels vs 1);
   with B's larger inpainting cells that cut the transparent field into 336 regions. Fix: the RGB below
   INPAINT_ALPHA is inpainted before the resample, both engines (`upsample.py`, `upsample.rs`).
   The upsample rule itself still holds: on all 4 corpus items it fires on, 2× outline is better
   (`upsurvey.py`: mean 0.103 vs 0.494 px); only the 2 px thin-bars fixture is now better direct,
   because strokes are now sub-pixel (test rewritten to use thin-mark-128).

C. **stroke fidelity knife-edge / wobble** — `stroke_fidelity` rasterised the centreline to pixels and
   the centreline was the raw medial axis (half a pixel off on a 2 px line by the tie-break). Fix:
   `_refine_centreline` (bilinear coverage centroid across the stroke, ±(w/2+1) px at 0.25 px, 2
   passes; `refine_exp.py`: rings 1–3 px at 4 sub-pixel offsets → centreline 0.06–0.08 px RMS, fidelity
   0.02–0.05, nearest-pixel samples 0.11/0.03–0.09, more passes/reach change nothing) and an exact
   point-to-polyline distance with box coverage in `stroke_fidelity` (`_polyline_distance`). Rust:
   `strokes::refine_centreline`, `sample_bilinear`, `polyline_distance`. Parity to 1e-9 on the ring test.

D. **spiral-notepad / wheelchair pinholes**: wedge-extension / tip tiling, not strokes. Not touched yet;
   re-check after the bench (B may have changed them).

## Tests added
- tests/test_vexel_prepare.py::test_inpainting_ignores_the_colour_of_nearly_transparent_pixels
- tests/test_vexel_strokes.py::test_a_ring_off_the_pixel_lattice_is_a_faithful_stroke[1.0|2.0],
  ::test_the_rust_stroke_stage_agrees_on_the_refined_centreline
- tests/test_vexel_topology.py::test_an_axis_snap_never_moves_a_line_end_further_than_the_placement_knows,
  ::test_a_slightly_slanted_bar_keeps_its_sides_where_its_pixels_are[python|rust] (skewed rounded bar
  with a bevel strip: 223 px uncovered before, 1 after)
- tests/test_vexel_upsample.py::test_the_upsample_mixes_ink_with_ink_not_with_the_colour_under_transparency;
  the thin-bars test now asserts both traces < 0.25 px and 2× < 0.5× direct on thin-mark-128
- tests/test_vexel_curves.py snap test updated to the bound (0.2/0.15 px rises snap, 0.6 px stays)

## Numbers so far (Auto / probe.py, Rust)
- thin-mark-512-ds: balanced art 58.7 → 0.42, dE 0.58 → 0.18, f1 0.855 → 1.0, paths 39 → 3; Auto now
  picks dense (art 0.30, dE 0.156)
- thin-mark-512: balanced art 12.6 → 0.37 (inflections 12 → 0)
- thin-mark-128: Auto pick dense dE 0.48 art 28 → logo dE 0.36 art 18.9 (balanced 134 → 23.7)
- u2049-512-ds: logo dE 0.358 → 0.168, f1 0.955 → 0.994, pinholes 2 → 0, art 15.4 → 13.7
- u2049-512: logo art 24.0, pinholes 0, f1 0.994

## In flight / next
- background: full pytest (b6188tqq7), diffcheck default stages (b3cea6jb4 → wp1/diffcheck2.log),
  snap survey (bzo59a5vh).
- then: bench run `python -m bench run --engines vexel --no-media --workers 3 --label wp1 --out DIR`
  and `bench compare bench/baselines/vexel.json DIR/results.json`; per-item check of logo/flat classes;
  held-out items via headtohead on a corpus copy (u2049, spiral-notepad, wheelchair, thin marks);
  seam_ppm for thin-mark-128/512; spiral/wheelchair pinholes (D); CLAUDE.md bullet; final report.
