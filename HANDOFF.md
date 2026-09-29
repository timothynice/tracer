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

## Step 2 (planned): glow split under Detailed
`refine_merge` declines (2,4) on glow-512-ds because the boundary gradient 2.44 > edge_limit
0.6·3.5 = 2.1, although the union radial fits better than either part (1.81 vs 2.27/2.35). The
gradient is the glow's own slope, not a step: apply the partition's ridge test (NECK_RIDGE 1.25 over
NECK_REACH 3 px, more than half the pairs) so the veto holds only on a ridge. Both engines.

## Step 3 (planned): rescue leaves a strip
`rescue_features` excludes `boundary_band` from candidates, so a rescued band next to the outline
leaves a 2-px strip of its parent between itself and the canvas; on a diagonal every row makes a
(b,c/a,b) lattice vertex, a node, and the strip becomes dozens of 3-vertex arcs. Fix: hand the
parent's band pixels next to a rescued region to the nearer of the two (`split_rim`), both engines.
