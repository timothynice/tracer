# HANDOFF — work package 3: wobble on soft edges (branch `claude/q3-soft-edges`, base 8f09955)

## Environment
`backend/.venv` built (uv), Rust extension built with `maturin develop --release`. Scratch tooling
copies live in `$SCRATCH/q3s/` (softsurvey.py, run-survey*.sh, probes under *-probe/).

## Findings so far (2026-09-29)
Instrument: `$SCRATCH/q3s/softsurvey.py IMAGE PRESET` wraps the Python `topology._crossing` and
records, per placed chain, `drop3` = median over its vertices of (coverage one pixel before the
label edge − coverage one pixel after it), i.e. the coverage drop across 3 px (≈1.0 on a hard
anti-aliased edge, → 0 on a soft ramp), the fraction of vertices whose crossing was found, and the
scorecard's wobble sites attributed to the nearest chain. Results in `$SCRATCH/q3s/survey1.log`,
`survey2.jsonl` (per chain, with vertex noise about a local 9-vertex line).

Three distinct causes of `wobble_deg_100px`:
1. **Soft boundaries** (drop3 < ~0.3, found ≈ 0): `_crossing` finds no half-crossing within its
   four samples, every vertex falls to t = 0.5 (the lattice edge), and `_fit_arc` follows the label
   staircase at 0.4–0.6 px. heart-eyes chin 2|7 (drop3 0.05, 239 px), glow-512-ds core 2|4
   (0.22, 532 px, the whole item's wobble), over-gradient 1|4 (0.16, 398 px), u1f61b 2|12 (0.006),
   baby 2|25 / 2|26, wheelchair 2|42 / 2|45 / 1|26, spiral 1|4 / 1|6. The two fitted fills agree
   at the boundary, so colour holds no edge to read there.
2. **Junction storms on a thin strip**: a 2-px strip of region b between a and c on a diagonal
   makes a lattice vertex with pixels (b,c / a,b) at every row; `_chains` calls a vertex a node
   unless it is a pass-through of one pair, so each such vertex is a node, and the strip becomes
   dozens of 3–5-vertex arcs alternating (a,b)/(b,c), each fitted as a line (heart-eyes: 31 chains
   along the chin outline; over-gradient: 186 chains). Two such arcs even share one fitted segment.
3. Hard-outline wobble on baby/wheelchair/spiral in the attributed sums — visually the outlines are
   smooth at 4x; the visible defects are the soft-band fragments. Not pursued first.

## Plan
- Return the 3-px coverage drop from `_crossing`/`crossing` per vertex; per chain take the median
  (`SOFT_*` constants, both engines); where the edge is soft, smooth the placed vertices along the
  arc with a window set by the blur width the drop implies (σ_b ≈ 1.2/drop − 0.35 px), and carry
  the softness on `Arc` so `_fit_arc` fits at a tolerance widened the same way. Constants from
  survey2 (noise vs drop3 bins), not from one image.
- Glow: `labels_merge` splits 2|4; check whether a 3-stop radial explains the union (merge.py).
- Strip junctions: make the (b,c / a,b) checkerboard vertex a pass-through of both pairs when the
  shared label is the diagonal one — evaluate on corpus first.

## Step 1 (in progress): soft-edge smoothing, both engines
- `topology._crossing` (`crossing` in Rust) now also returns, per vertex, the coverage drop from the
  sample one pixel before the label edge to the one after it (NaN where either sample fell back).
- `_softness(drop)`: median drop over the chain -> blur sigma = SOFT_WIDTH/drop - SOFT_BIAS
  (1.2, 0.35: inverts erf(1.5/(σ√2)) within 8 % for σ 1..8), 0 below SOFT_SIGMA=1.0 (drop > 0.89),
  capped at SOFT_SIGMA_MAX=4.0; drop <= 0 -> the cap. Chains on a posterised level line: no smoothing.
- `_soften(pts, sigma, closed)`: Gaussian along the arc by vertex index, kernel to 3σ, open ends
  fixed and the kernel renormalised where it runs off an end; closed chains wrap. Applied in
  `_place` after `_unfold`/`_settle` (Rust `place` likewise).
- Test: `tests/test_vexel_soft_edges.py` (a σ=3/5 ramp cut at its quarter level: the placed
  vertices must lie on one line, be fitted as one Line, and sit between the label line and the
  half-crossing; a crisp control keeps sub-pixel placement). Failed before (RMS 0.47 px), passes after.
- Survey numbers behind the constants: `$SCRATCH/q3s/analyse_survey.py survey2.jsonl`.

## Step 2 (done, Python + Rust): glow split under Detailed
`refine_merge` declines (2,4) on glow-512-ds because the boundary gradient 2.44 > edge_limit
0.6·3.5 = 2.1, although the union radial fits better than either part (1.81 vs 2.27/2.35). The
gradient is the glow's own slope, not a step: apply the partition's ridge test (NECK_RIDGE 1.25 over
NECK_REACH 3 px, more than half the pairs) so the veto holds only on a ridge. Both engines.

## Step 3 (done, Python + Rust, Rust not yet rebuilt/diffchecked): rescue leaves a strip
`rescue_features` excludes `boundary_band` from candidates, so a rescued band next to the outline
leaves a 2-px strip of its parent between itself and the canvas; on a diagonal every row makes a
(b,c/a,b) lattice vertex, a node, and the strip becomes dozens of 3-vertex arcs. Fix: hand the
parent's band pixels next to a rescued region to the nearer of the two (`split_rim`), both engines.

## Results so far (Python engine, before -> after)
- heart-eyes-512 logo (Auto's pick): artifact 50.17 -> 4.49, dE 1.689 -> 1.681, f1 0.987; balanced 72.6 -> 14.9
- glow-512-ds detailed: artifact 54.76 -> 0.00, dE 0.382 -> 0.380, paths 6 -> 3
- over-gradient-512 balanced: artifact 45.9 -> 3.43
- u1f61b-512 balanced: artifact 24.1 -> 15.3
- wedge-fan-128: the stroke test's pinhole appeared with smoothing of hub stubs; fixed by the
  kernel-length rule (a chain shorter than 2*ceil(3σ)+1 vertices is not smoothed).
- Tests added: tests/test_vexel_soft_edges.py, test_vexel_refine_merge.py, test_vexel_reach_edge.py.
- diffcheck (placed nodes arcs segments) on the smoothing alone: 0 failing so far (run in progress
  when this was written; rerun everything after the Rust rebuild).

## Next
1. Wait for the diffcheck run to end, `maturin develop --release`, run `tools.diffcheck` (default
   stages) and `placed nodes arcs segments`: 0 failing pairs required.
2. `python -m bench run --engines vexel --no-media --workers 3 --label q3soft --out $SCRATCH/q3s/bench`
   then `bench compare bench/baselines/vexel.json .../results.json`; per-item check for outline_px / dE
   regressions > 0.02 and the wordmark.
3. Held-out: `VEXEL_BACKEND=rust RAYON_NUM_THREADS=1 python bench/headtohead.py run --corpus
   bench/heldout --out $SCRATCH/q3s/heldout --configs vexel-auto --workers 3`, then
   `$SCRATCH/q3/survey.py` for wobble wins vs VTracer (target >= 90 of 120).
4. Full pytest (both backends), cargo test, CLAUDE.md bullet, final report.

## Resumed 2026-09-29 (after a session stop; background jobs were lost)
- Corrections since the last results: `boundary_ridges` compares with the *lower* of the two side
  samples (a small region's far sample is its other edge: thin-mark-128's 14 px core had joined its
  backdrop across a step of 54); `reach_the_edge` reaches only the outline of a neighbour at least
  as large as the rescued region (a ten-pixel dot on a ring is not an outline). Tests for both.
- Fixed: thin-mark-128 (upsample never) lost its stroked ring with reach_the_edge on: three
  backdrop band pixels beside ring fragments 8/9 (reddish, so split_rim hands them over) are a
  one-pixel wart on a 1.5 px line and the stroke stage refused the ring. reach_the_edge now skips a thin rescued region
  (`strokes.is_thin`, both engines); the ring is stroked again in every variant (isolate_thin.py).
- Then: rebuild Rust, diffcheck (default + placed nodes arcs segments), bench run + compare, held-out
  Auto run + survey3.py, full pytest on both backends, cargo test, report.

## Report draft (numbers to fill from the pipeline: diffcheck2/3.log, bench/compare.log, heldout/records.jsonl)

Root causes
1. Soft boundary: across a ramp several pixels wide the placement's four coverage samples never
   cross a half, so every vertex of the chain falls to t = 0.5 (the lattice edge) and `_fit_arc`
   draws the label staircase at 0.4-0.6 px; where the fitted fills agree at the boundary there is
   nothing to read at all (heart-eyes chin 2|7: median 3-px drop 0.05, 0 % found).
2. `refine_merge`'s edge veto took a glow's own slope for a step: under Detailed (limit 0.6·3.5 =
   2.1) the boundary between the glow's core and halo reads 2.44 although the union radial fits
   better than either part (1.81 vs 2.27/2.35), so the core stayed a second shape with a soft,
   wobbling circular edge.
3. `rescue_features` leaves the parent's edge band out of a rescued component, so a band rescued
   beside the parent's outline stopped two pixels short of it and a strip of the parent ran on
   between them; on a diagonal every row makes a (b,c / a,b) lattice vertex, `_chains` calls each a
   node, and the outline became dozens of three-vertex arcs (heart-eyes: 31 such chains along the
   chin; over-gradient: 186).

Changes (Python and Rust, one commit each)
- topology `_crossing`/`crossing`: also returns the per-vertex coverage drop across 3 px (NaN where
  an outer sample fell back). `_softness`: median drop -> blur sigma = SOFT_WIDTH/drop - SOFT_BIAS
  (1.2, 0.35; inverts erf(1.5/(σ√2)) within 8 % for σ 1..8 px), 0 under SOFT_SIGMA = 1.0 (drop >
  0.89), capped at SOFT_SIGMA_MAX = 4.0 (a Gaussian of σ pulls a circle of radius R in by σ²/2R);
  drop <= 0 -> the cap. `_soften`: Gaussian along the arc by vertex index to 3σ, open ends fixed,
  kernel renormalised at the ends, closed chains wrap; never a chain shorter than the kernel
  (2·ceil(3σ)+1 vertices: a stub between the spokes of wedge-fan-128's hub pulled onto its chord
  left a pinhole), never a chain placed on a posterised level line. Constants from the survey of
  652 chains over 28 images ($SCRATCH/q3s/survey2.jsonl): vertex noise about a local line 0.08 px
  at drop >= 0.95, 0.12 at 0.8-0.9, 0.22 at 0.7-0.8, 0.4-0.55 below 0.7; wobble/100 px 78 -> 400
  -> 950 -> 1500-2200 along the same bins.
- merge `boundary_ridges` + refine: a pair steeper than edge_limit is vetoed only where the boundary
  is a ridge at more than half its pairs (NECK_RIDGE 1.25 over NECK_REACH 3, the partition's neck
  test) against the *lower* of the two side samples (a small region's far sample is its other
  edge: thin-mark-128's 14 px core joined its backdrop across a step of 54 with the higher).
- engine `reach_the_edge`: after the rescue refit, the parent's band pixels within two of a rescued
  region, on the outline side of it (nearer the parent's old boundary with a neighbour at least as
  large as the rescued region than the rescued pixels within two are; exact EDT, local min) go to
  the nearer of parent/rescued by `split_rim`; thin rescued regions (`strokes.is_thin`) are skipped.

Tests: tests/test_vexel_soft_edges.py (3), test_vexel_refine_merge.py (3), test_vexel_reach_edge.py (5).
CLAUDE.md: one bullet (soft edges; the ridge veto; reach_the_edge).
