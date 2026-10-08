# Degraded bench set — design

Status: approved by Tim 2026-10-07 ("Yes, build it autonomously"). Branch `claude/degraded-bench`.

## Why

Three engine fixes for the wave-lockup asset (D0 undouble, D2 tips, D1 caps seeding — records in
`docs/superpowers/plans/2026-10-05-wave-lockup-focus-loop.md`) each improved the asset and failed
the per-item gate on clean corpus and held-out images, with no survey gap for a threshold. The
asset's defects come from how it was made — an exact 2× nearest-neighbour upscale, sharpening
rims and halos, strokes under 2 px at native resolution — and no bench item is degraded that
way. The gate can therefore only see costs, never the benefit on degraded inputs, and a rule
cannot be designed against a distribution the bench does not contain. This set adds that
distribution, with vector truth, so later fixes can be routed to detected degraded inputs and
judged on both sides.

## What

A third bench set `backend/bench/degraded/`, laid out like `bench/heldout/`: a `manifest.yaml`
of items (id, class, png, width, height, tags, truth_svg), PNGs and truth SVGs under per-class
folders, run with `python -m bench run --corpus bench/degraded`. The existing corpus, its
baseline (`bench/baselines/vexel.json`), `preset_details.json` and the core fixtures do not
change.

### Sources (vector truth, license-clean)

| source | from |
|---|---|
| thin-mark, wedge-fan, venn, hex-nest | `bench/corpus/synthetic/logo/*.svg` |
| linear-4stop, radial-disc | `bench/corpus/synthetic/gradient/*.svg` |
| sticker | `bench/corpus/synthetic/flat/sticker.svg` |
| card | `bench/corpus/synthetic/shadow/card.svg` |
| logomark, studi0trace-mark | `bench/corpus/real/logo/*.svg` (Studi0's own marks) |
| u2049 | `bench/heldout/noto/u2049.svg` (licence in `bench/heldout/LICENSES`) |
| nail-polish | `bench/heldout/fluent-color/nail-polish.svg` (same) |
| wave-lockup | new, generated in `bench/degraded.py` (below) |

The truth SVGs are copied into the set (with their licence lines for the held-out ones), so
the set is self-contained.

**wave-lockup stand-in** (synthetic, `viewBox 0 0 1208 308`, background `#fefefe`): two tapered
ribbons on the right, flowing right to left, with soft-ended acute tips, drawn as cubic
outlines with a 7 px white channel between them, each filled by a 4-stop `linearGradient` along
the ribbon (dark: `#4a1430 → #d93b7f → #b02a5f → #4a1430`; light: `#d8913a → #e8c65c → #a0c04f
→ #7ac75f`); on the left, a row of 10 small shapes (3.5 px strokes, 20 px tall, in `#e0703a`,
unevenly spaced, in this order: diamond, zigzag with three acute turns, double ring, chevron,
triangle with a counter, plus sign, ring, short bar, three-quarter arc, square ring) sitting
above a row of 5 thin shapes (6 px strokes, `#2b2b2b`: a ring, a wide shallow arc at least 2.5
times as wide as it is high, a sideways S-shaped wave, a horizontal bar with a separate dot
centred below its end, a cross) and a row of 4 heavy shapes (16 px stems, `#2b2b2b`: a pair of
bars of unequal height, a chevron with an acute inner notch, a bar into a round bowl, a
right-angled corner opening up and to the left). No font, no letter, no word, and no palette or
layout of any real mark. It is a geometric stand-in for the asset's hard parts (acute tips, a
thin channel, thin strokes, heavy stems, tiny counters), never a copy of the asset.

### Degradations (each source × each class)

| class | operation | reproduces |
|---|---|---|
| `nn2x` | render at half the final size with resvg, nearest-neighbour 2× up; columns always pair at phase 0; rows pair at phase 0 for sources at even index in the source list and at phase 1 for odd index (render one extra native row, double, drop the first output row, so rows pair as (2k+1, 2k+2) like the asset's) | exact pixel doubling |
| `sharpen` | render at the final size, then `ImageFilter.UnsharpMask(radius=1.5, percent=120, threshold=0)` on RGB (alpha kept) | dark rim inside, light halo outside |
| `small` | render with the long side at 176 px | strokes under 2 px |
| `combo` | render at half the final size, unsharp mask as `sharpen`, nearest-neighbour 2× (phase 1 rows), add ground noise: a ±1-level, σ 40 px Gaussian field on RGB of opaque pixels, seeded | the asset's whole signature |

Final size: long side 512 for square sources, 1208 × 308 for wave-lockup (half size 604 × 154).
Ids `<class>/<source>-<size>` (e.g. `nn2x/venn-512`, `combo/wave-lockup-1208`); tags
`degraded`, `degraded:<class>`, `source:<corpus|heldout|synthetic>`, `size:<long side>`.
Everything is seeded (`random.Random(f"{seed}:{class}:{source}")`, default seed 1234) and
re-encoded through Pillow like `synth.render_png`, so `python -m bench.degraded generate`
writes byte-identical files on a second run. 13 sources × 4 classes = 52 items.

## Wiring

- `backend/bench/degraded.py` — `SOURCES`, `CLASSES`, the four degradations as pure functions
  over RGBA arrays, `wave_lockup_svg()`, `generate(root, seed)`; CLI
  `python -m bench.degraded generate [--out bench/degraded] [--seed 1234]`.
- `backend/bench/config.py` — nothing (classes come from the manifest, as for held-out).
- `backend/tools/qloop.sh` — `ref` and `full` also run `bench/degraded` (reference
  `ref-degraded`); `sentinels` gains a few `degraded` lines in `bench/sentinels.txt`
  (`combo/wave-lockup-1208`, `nn2x/thin-mark-512`, `small/venn-176`, `sharpen/u2049-512`).
- `README.md` bench section and `CLAUDE.md` layout line: the third set and why it exists.
- Generated files are committed (like the synthetic corpus): 52 PNGs (optimised, expected well
  under 3 MB), the copied truth SVGs and the manifest.

## Evaluation after building it

1. Freeze `ref-degraded` at the current engine (main 87b00c1's engine) under
   `bench/reports/keep-2026-10-05-wave-lockup/` and record per-class means in the focus-loop
   plan's Results log.
2. Re-score the three blocked prototypes on it, Python against Python (the prototypes are
   Python-only): `claude/wave-lockup-d0-wip` (bilinear and Lanczos undouble with the
   exactly-once rule), `claude/wave-lockup-d2-wip`, `claude/wave-lockup-d1-wip`. Per prototype:
   improved / regressed item counts on degraded, next to the clean-set counts already
   recorded, and whether the wins cluster in the classes the fix targets (`nn2x`/`combo` for
   D0, `small`/`combo` for D1, all for D2).
3. Write a recommendation in the Results log: which fixes are candidates for routing to a
   detected degradation, and what detector each would need. Building the detectors and routed
   fixes is the next plan.

## Testing

- `tests/test_bench_degraded.py`: each degradation on a tiny synthetic array (nn2x doubles
  exactly with the right phase; sharpen darkens inside and lightens outside an edge; small has
  the right long side; combo is doubled and noisy only where opaque); `wave_lockup_svg()`
  parses and renders at 1208 × 308; `generate` into `tmp_path` twice is byte-identical and
  writes a manifest `load_corpus` reads with 52 items, every one with a truth SVG.
- Every item traces without error in `bench run --corpus bench/degraded` (part of step 1).

## Out of scope

Detectors and routed fixes (next plan); any engine change; moving the corpus baseline;
held-out-style competitor runs on the new set.
