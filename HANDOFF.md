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

## Done
- nothing committed to the engine yet.
