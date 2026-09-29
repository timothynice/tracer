# WP1 thin regions and strokes — handoff

Branch `claude/q3-thin-regions` off 8f09955. Worktree `.claude/worktrees/agent-abcb7c55071980f21`.
Env built (`backend/.venv`, Rust extension installed). Scratch: `$SCRATCH/wp1/` (probe dumps, helper
scripts `uncovered.py`, `stages.py`, `fitprobe.py`).

## Root causes found (2026-09-29)

A. **u2049-512(-ds) strips (464 px uncovered)** — not a stroke problem. The "!" bar's outer sides are
   genuinely slanted 1.3° (source left edge x=55.9 at y=40 → 62.8 at y=340). `topology._snap_axis`
   (and `curves.snap_axis_lines`, `regularity.py`) snap any line within `snap_axis_deg` (1.5°) to the
   axis by turning it about its midpoint, with no bound on how far the ends move: here 3.6 px at each
   end against a 0.6 px tolerance. Proven with `fitprobe.py`: `fit_stretch` gives the line
   (55.95,30.5)→(63.22,353.5); `_fit_arc` returns x=59.59 vertical. Fix: a snap may move an end by at
   most MOVE_SHARE·tol (rects.py already has that rule); longer/steeper lines stay as fitted. Both engines.

B. **thin-mark-512-ds sawtooth triangle + fragmented ring** — a partition problem upstream of strokes.
   `prepare.inpaint_transparent` copies the colour of the *nearest pixel with alpha > 0*. In the
   downsampled (-ds) images pixels with alpha 1–15 carry un-premultiplication garbage (G=255 at alpha=1,
   colour error ≈ 128/alpha). Their Voronoi seams give the transparent field a gradient of 40–70
   (the ring's own ridge is ~79), so the watershed attaches transparent blobs to the triangle every
   5 rows (the staircase period) and swallows most of the 2 px ring into the canvas; the ring comes
   back as 39 rescued fragments in 14 groups. Fix: inpaint from pixels whose alpha is high enough for
   the colour to be trustworthy (threshold to be chosen from a survey), both engines.

C. **stroke fidelity knife-edge / wobble (thin-mark-128 ring 0.198 vs 0.2, -ds groups 0.17–0.23)**:
   `stroke_fidelity` rasterises the centreline to whole pixels and predicts coverage from an EDT of
   those pixels, so a line whose centre is half a pixel off the lattice is scored as if it were on
   it; the centreline itself is the medial axis of the binary mask (pixel-quantised, tie-break
   dependent). Fix: coverage-weighted sub-pixel centreline refinement (move each vertex along its
   normal to the coverage centroid of the cross-section) and an exact point-to-polyline distance with
   a box-coverage prediction in the fidelity. Both engines (`strokes.py`, `vexel-rs/src/strokes.rs`).

D. **spiral-notepad / wheelchair pinholes**: label maps are fine there; the arcs carry `sliver`
   (wedge extension) and the wheelchair's neck region 36 has both ends as wedge tips: a
   `_extend_wedges` / tip tiling issue, not strokes. Look after A–C.

## Status
- Nothing changed yet; investigation only. Next: failing tests for A, B, C; fixes in Python then Rust;
  diffcheck; bench.
