# Vexel Geometry Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Vexel's emitted geometry match the artwork the way the best paid vectorizers do: straight edges emitted straight, junctions and corners placed exactly and without flare, and every step proven by geometric metrics against the corpus's vector truth.

**Architecture:** The boundary graph (`vexel/topology.py`) already places sub-pixel arcs to 0.05 px. The work is downstream of placement: measure geometry against ground truth first (bench), then fix node placement, approach-window trimming, the smooth-continuation test and the line-first fit, then the longer-term regularity, primitive, symmetry, render-refine and learned-corner stages. Every engine change lands in the Python reference *and* the Rust port in the same commit, proven equal by `tools/diffcheck.py`, and is gated by `bench compare`.

**Tech Stack:** Python 3.12 (numpy, scipy, scikit-image, resvg-py, Pillow), Rust extension via maturin/PyO3 (`backend/vexel-rs`), pytest, cargo test. Spec: `docs/superpowers/specs/2026-09-22-vexel-geometry-quality-research.md`.

## Global Constraints

- Both implementations change together. A stage changed in `studi0trace/engines/vexel/*.py` is changed identically in `vexel-rs/src/*.rs` in the same commit. `cd backend && .venv/bin/python -m tools.diffcheck arcs` must report `arcs` within 0.05 px RMS with matching shapes, and `VEXEL_BACKEND=python` and `VEXEL_BACKEND=rust` must emit byte-identical SVG for `bench/corpus/real/logo/vexel-wordmark-512.png`.
- Every quality change runs `python -m bench run --engines vexel` then `python -m bench compare bench/baselines/vexel.json bench/reports/<ts>/results.json --metric <m>` for `score`, `seam_ppm`, `outline_px`, `junction_px`, `line_debt_px`. No task is done while any of them regresses beyond tolerance. Baselines move only with `--update-baseline` and a commit message that quotes the deltas.
- Engines stay synchronous; parameters live on `VexelParams` with bounds, default, description and `json_schema_extra={"ui": ...}`; no hardcoded controls in the UI.
- New behaviour ships with a test (pytest for Python, `#[cfg(test)]` for Rust).
- Python env: `backend/.venv`. Rust build: `.venv/bin/python -m maturin develop --release -m vexel-rs/Cargo.toml`. Run pytest from `backend/`: `.venv/bin/python -m pytest -q`.
- Never use a one-sided coloured border as a UI highlight (no UI in this plan, kept for completeness).
- Placement noise model, measured in the spec: vertex RMS 0.035–0.074 px, p95 ≤ 0.15 px, max ≤ 0.23 px, at every edge angle. Thresholds below are set from it and are re-measured under the degradation variants in Task 2 before being frozen.

## Order of work

| # | Task | Kind | Gate |
|---|---|---|---|
| 1 | Geometry metrics in the bench | measure | tests; metrics appear in results.json and report |
| 2 | Geometry corpus templates and degradation variants; baseline refresh | measure | baselines carry the new metrics |
| 3 | Node from incident lines at any angle | fix | wedge tip error 1.4 → < 0.3 px; junction_px down |
| 4 | Trim the approach window at every node | fix | wordmark flare p50 0.52 → < 0.15 px; 45° square back to 4 lines |
| 5 | Smooth continuation decided by a fit, not the corner threshold | fix | 59° meeting is a corner; small circle through a T stays smooth |
| 6 | Lines first, corners from lines, collinear merge | fix | 5/25/38/45/50° squares → 4 lines, also under JPEG q75 and downsample |
| 7 | Segment-minimising, bump-free spline fit | fix | line_debt 0 on straight, nodes down on curves at equal outline_px |
| 8 | Regularity on the graph: direction clustering, axis/parallel/perpendicular snap | long term | tilted-squares corners exact; no seam regression |
| 9 | Primitives: rounded rects and circular arcs | long term | rounded square → `<rect rx>`; ring segments → `A` |
| 10 | Symmetry: mirror/rotational symmetrisation, repeated shapes as `<use>` | long term | symmetric logos symmetric to 0.05 px; bytes down |
| 11 | Render-and-compare refinement pass (opt-in) | long term | outline_px down on real logos with no node increase |
| 12 | Learned corner/smooth classifier from corpus truth | long term | corner F1 on held-out synthetic ≥ 0.98; replaces the 60° rule |
| 13 | Spikes with go/no-go: sub-pixel deblurring for ≤ 128 px, glyph fitting for wordmarks, semantic layer prior | strategic | measured protocol, decision recorded |

## Results log

Filled in as tasks land; every row quotes `bench compare` output.

| after task | outline_px logo | junction_px logo | line_debt_px logo | score logo | seam_ppm logo | wordmark bytes |
|---|---|---|---|---|---|---|
| baseline (pre-change engine, per-pixel seam) | 0.695 | 0.744 | 771 | 0.9548 | 14125 | 10762 |
| Tasks 3-6 | 0.264 | 0.721 | 338 | 0.9555 | 20424 | 7326 |
| Task 7 | 0.264 | 0.721 | 338 | 0.9555 | 20482 | 7326 |
| Task 8 | 0.264 | 0.721 | 338 | 0.9554 | 20482 | 7326 |
| Tasks 9-10 | 0.260 | 0.720 | 315 | 0.9555 | 20155 | 7341 |
| parity fixes + perf (2026-09-23) | 0.241 | 0.611 | 285 | 0.9573 | 17569 | 7341 |

**Follow-up (2026-09-23).** The two parity sessions' fixes landed (tie-breaks the two languages did not share:
rim splits, medial-axis thinning order, alpha-weighted rescue residual, split-point ties). The end-to-end diffcheck
stages they added still differ on 6 items for labels and ~20 for arcs; traced on card-512, the label maps agree
through `labels_clear` and part at `rescue`, where the residual's 1.0 threshold sits on a shadow band whose fill the
two fitters place a colour level apart (the documented fills tolerance), and the arc differences follow from
placement that agrees to 0.05 px, not to the bit — a `line_runs` decision flips on the last bits. Neither is a port
error; making them bit-identical would mean bit-identical linear algebra across LAPACK and Rust. They are now
`--all` diagnostics in diffcheck, and the default run passes everywhere. Rust's symmetry stage was profiled on
logomark-512 (4.4 of 5.2 s in a widening grid search) and bounded: 0.80 s. `refine_render` imported resvg-py at
module load while resvg-py was a bench-only dependency, which failed the Docker image's import guard; the import is
lazy and resvg-py is a runtime dependency. Specs written: `2026-09-23-vexel-small-input-upsampling.md`,
`2026-09-23-vexel-refine-rust-twin.md`.

**Follow-up (2026-09-23, later).** Both specs built. Small-input upsampling (`upsample.py`/`upsample.rs`, default
`auto`): selected by a region under 2.2 px wide in the direct trace's label map, Lanczos-3 2× with shared literal
weights (byte-identical, `diffcheck upsample`), drawn back in `<g transform="scale(0.5)">`; thin-mark-128 outline
2.24 → 0.26 px. Rust refiner (`refine_render.rs`, tiny-skia): same moves as the Python, filtered shapes left out of
the crop in both, cross-engine parity held to 0.005 px of outline error on the heart by `tests/test_vexel_refine.py`;
`trace` no longer routes `refine=True` to Python.

The Tasks 3-5 row was measured under the old seam metric (outline 0.384, seam 19665) and is superseded.
`seam_ppm` on logo is up because two sub-pixel-scale items dominate it: `thin-mark-128` (Rust does not recover its
ring as a stroke where Python does, a pre-existing parity gap in the stroke stage that diffcheck does not cover) and
`studi0mail-logo-dark` (38 px tall; one cubic now spans a stem side within `curve_tolerance` where the old engine spent
six, and the metric's 0.1 px slack counts the 0.2 px it sits inside). Excluding thin-mark, logo seam moved 7290 -> 7466 ppm.

---

### Task 1: Geometry metrics in the bench

**Files:**
- Create: `backend/bench/geometry.py`
- Modify: `backend/bench/metrics.py` (`all_metrics`, add `truth_svg` argument and the new keys)
- Modify: `backend/bench/runner.py:48-73` (`score_item` passes the truth SVG text)
- Modify: `backend/bench/report.py:10-16` (`SUMMARY_COLS` gains `outline_px`, `junction_px`, `line_debt_px`)
- Test: `backend/tests/test_bench_geometry.py`

**Interfaces:**
- Produces: `bench.geometry.outline_error(truth_svg: str, out_svg: str, width: int, height: int, scale: int = 8) -> dict` with keys `outline_px` (symmetric Chamfer mean, source px), `outline_p99_px`, `junction_px` (mean within 6 px of truth junctions, `None` when the truth has none); `bench.geometry.line_debt(svg: str) -> dict` with `line_debt_px` (total chord length of cubics that bow ≤ 0.2 px over ≥ 3 px), `line_debt_segments`, `nodes_per_100px` (segments per 100 px of outline). `all_metrics(..., truth_svg: str | None = None)` merges them; keys are `None` for real items without truth.

- [x] **Step 1: Write the failing tests**

```python
# backend/tests/test_bench_geometry.py
import numpy as np
from bench.geometry import outline_error, line_debt

NS = 'xmlns="http://www.w3.org/2000/svg"'

def svg(body):
    return f'<svg {NS} viewBox="0 0 128 128"><rect width="128" height="128" fill="#fff"/>{body}</svg>'

def test_identical_geometry_has_zero_outline_error():
    truth = svg('<polygon points="20,20 100,30 90,110 30,100" fill="#36c"/>')
    m = outline_error(truth, truth, 128, 128)
    assert m["outline_px"] < 0.02 and m["outline_p99_px"] < 0.13

def test_a_shifted_edge_is_measured_in_source_pixels():
    truth = svg('<polygon points="20,20 100,20 100,100 20,100" fill="#36c"/>')
    out = svg('<polygon points="20,20 100,20 100,100.75 20,100.75" fill="#36c"/>')  # bottom edge moved 0.75 px
    m = outline_error(truth, out, 128, 128)
    assert 0.6 < m["outline_p99_px"] < 0.9
    assert 0.1 < m["outline_px"] < 0.3  # one of four edges moved

def test_junction_error_is_read_where_three_colours_meet():
    truth = svg('<polygon points="0,128 128,0 128,128" fill="#3b8ee8"/>'
                '<polygon points="40,88 128,0 100,0" fill="#bfe0ff"/>')
    blunt = svg('<polygon points="0,128 128,0 128,128" fill="#3b8ee8"/>'
                '<polygon points="46,82 128,0 100,0" fill="#bfe0ff"/>')  # tip cut 6 px short
    exact = outline_error(truth, truth, 128, 128)
    cut = outline_error(truth, blunt, 128, 128)
    assert exact["junction_px"] is not None and exact["junction_px"] < 0.05
    assert cut["junction_px"] > 0.5
    assert cut["junction_px"] > cut["outline_px"]  # the defect is local, the metric says where

def test_line_debt_counts_cubics_that_should_have_been_lines():
    straight_as_cubic = svg('<path d="M10 10C40 10 70 10 100 10L100 100L10 100Z" fill="#36c"/>')
    m = line_debt(straight_as_cubic)
    assert m["line_debt_segments"] == 1 and 89 < m["line_debt_px"] < 91
    curved = svg('<path d="M10 10C40 40 70 40 100 10L100 100L10 100Z" fill="#36c"/>')
    assert line_debt(curved)["line_debt_segments"] == 0

def test_line_debt_is_none_for_relative_commands():
    assert line_debt(svg('<path d="m10 10c30 0 60 0 90 0z" fill="#36c"/>'))["line_debt_px"] is None
```

- [x] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_bench_geometry.py -q`
Expected: FAIL with `ModuleNotFoundError: bench.geometry`

- [x] **Step 3: Implement `bench/geometry.py`**

```python
"""Geometry metrics against vector truth, at 1/8 px.

The colour metrics cannot see a tip cut 6 px short or a straight edge drawn as
a bowing cubic: mean ΔE moved by 0.009 when such a tip was fixed. These render
truth and output at `scale`× and compare their edge sets directly.
"""
from __future__ import annotations

import re
import numpy as np
from scipy import ndimage

from bench.raster import rasterize, to_rgb_on_white

EDGE_STEP = 12.0        # 8-bit RGB distance between neighbouring pixels that makes an edge
JUNCTION_REACH = 6.0    # source px around a truth junction that count as "at the junction"
LINE_BOW = 0.2          # a cubic bowing less than this over ≥ LINE_MIN px should have been a line
LINE_MIN = 3.0


def _edges(rgb: np.ndarray) -> np.ndarray:
    f = rgb.astype(np.float32)
    dx = np.zeros(rgb.shape[:2], bool); dy = np.zeros(rgb.shape[:2], bool)
    dx[:, :-1] = np.linalg.norm(f[:, 1:] - f[:, :-1], axis=2) > EDGE_STEP
    dy[:-1, :] = np.linalg.norm(f[1:] - f[:-1], axis=2) > EDGE_STEP
    return dx | dy


def _junction_mask(rgb: np.ndarray, edges: np.ndarray, scale: int) -> np.ndarray:
    """Edge pixels whose neighbourhood holds three or more flat colours."""
    q = (rgb.astype(np.int32) >> 3)                     # 32 levels: anti-aliasing mixtures collapse
    code = (q[..., 0] << 10) | (q[..., 1] << 5) | q[..., 2]
    r = scale                                            # one source pixel radius
    out = np.zeros(edges.shape, bool)
    ys, xs = np.nonzero(edges)
    for y, x in zip(ys[::max(1, len(ys) // 20000)], xs[::max(1, len(xs) // 20000)]):
        win = code[max(0, y - r): y + r + 1, max(0, x - r): x + r + 1].ravel()
        vals, counts = np.unique(win, return_counts=True)
        if int((counts >= 4).sum()) >= 3:
            out[y, x] = True
    return out


def outline_error(truth_svg: str, out_svg: str, width: int, height: int, scale: int = 8) -> dict:
    t = to_rgb_on_white(rasterize(truth_svg, width * scale, height * scale))
    o = to_rgb_on_white(rasterize(out_svg, width * scale, height * scale))
    te, oe = _edges(t), _edges(o)
    if not te.any() or not oe.any():
        return {"outline_px": None, "outline_p99_px": None, "junction_px": None}
    d_to_out = ndimage.distance_transform_edt(~oe)      # distance from every pixel to an output edge
    d_to_truth = ndimage.distance_transform_edt(~te)
    forward = d_to_out[te] / scale
    backward = d_to_truth[oe] / scale
    both = np.concatenate([forward, backward])
    junction = _junction_mask(t, te, scale)
    junction_px = None
    if junction.any():
        near = ndimage.distance_transform_edt(~junction) <= JUNCTION_REACH * scale
        sel = np.concatenate([near[te], near[oe]])
        if sel.any():
            junction_px = float(both[sel].mean())
    return {
        "outline_px": float(both.mean()),
        "outline_p99_px": float(np.percentile(both, 99)),
        "junction_px": junction_px,
    }


_NUM = re.compile(r"-?\d*\.?\d+(?:e-?\d+)?")


def _segments(d: str):
    """Absolute M/L/C/Z only; returns None on any other command (relative emitters)."""
    out, cur, start = [], None, None
    for cmd, body in re.findall(r"([A-Za-z])([^A-Za-z]*)", d):
        nums = [float(x) for x in _NUM.findall(body)]
        if cmd == "M":
            cur = start = np.array(nums[:2])
        elif cmd == "L":
            for k in range(0, len(nums) - 1, 2):
                p = np.array(nums[k:k + 2]); out.append(("L", cur, p)); cur = p
        elif cmd == "C":
            for k in range(0, len(nums) - 5, 6):
                p = np.array(nums[k + 4:k + 6]); out.append(("C", cur, np.array(nums[k:k + 2]), np.array(nums[k + 2:k + 4]), p)); cur = p
        elif cmd == "Z":
            cur = start
        else:
            return None
    return out


def _bow(seg) -> float:
    p0, c1, c2, p1 = seg[1:]
    t = np.linspace(0, 1, 17)[:, None]
    q = (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * p1
    d = p1 - p0; n = np.linalg.norm(d)
    if n < 1e-9:
        return float(np.linalg.norm(q - p0, axis=1).max())
    return float(np.abs((q - p0) @ np.array([-d[1], d[0]]) / n).max())


def line_debt(svg: str) -> dict:
    debt_px, debt_n, segs_n, length = 0.0, 0, 0, 0.0
    for d in re.findall(r'<path[^>]*\sd="([^"]*)"', svg):
        segs = _segments(d)
        if segs is None:
            return {"line_debt_px": None, "line_debt_segments": None, "nodes_per_100px": None}
        for s in segs:
            chord = float(np.linalg.norm(s[-1] - s[1]))
            segs_n += 1; length += chord
            if s[0] == "C" and chord >= LINE_MIN and _bow(s) <= LINE_BOW:
                debt_px += chord; debt_n += 1
    return {"line_debt_px": debt_px, "line_debt_segments": debt_n,
            "nodes_per_100px": (100.0 * segs_n / length) if length > 0 else None}
```

Then in `bench/metrics.py` add the argument and merge:

```python
def all_metrics(src_rgba, out_rgba, svg, elapsed_ms, truth_paths=None, weights=DEFAULT_WEIGHTS, truth_svg: str | None = None) -> dict:
    ...
    geo = {"outline_px": None, "outline_p99_px": None, "junction_px": None}
    if truth_svg:
        geo = outline_error(truth_svg, svg, src_rgba.shape[1], src_rgba.shape[0])
    raw = {..., **geo, **line_debt(svg), ...}
```

In `runner.score_item`, pass `truth_svg=item.truth_svg.read_text(encoding="utf-8") if item.truth_svg and item.truth_svg.exists() else None`. In `report.SUMMARY_COLS` append `("outline_px", "Outline px", "{:.3f}"), ("junction_px", "Junction px", "{:.3f}"), ("line_debt_px", "Line debt", "{:.0f}")`. `summarize` in `runner.py` averages numeric keys and must skip `None` (check it does; if it uses `np.mean` over a list, filter `None` first).

- [x] **Step 4: Run the tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_bench_geometry.py tests/test_bench*.py -q`
Expected: PASS. Then `python -m bench run --engines vexel --limit 4` (if `--limit` exists; else the full run) and confirm `results.json` items carry `outline_px`.

- [x] **Step 5: Commit**

```bash
git add backend/bench/geometry.py backend/bench/metrics.py backend/bench/runner.py backend/bench/report.py backend/tests/test_bench_geometry.py
git commit -m "bench: geometry metrics against vector truth - outline, junction and line-debt"
```

---

### Task 2: Geometry corpus templates, degradation variants, baseline refresh

**Files:**
- Modify: `backend/bench/synth.py` (two templates in `TEMPLATES["logo"]`; a downsampled variant in `generate`)
- Modify: `backend/bench/baselines/{vexel,vtracer,potrace}.json` via `--update-baseline`
- Modify: `docs/superpowers/plans/2026-09-22-vexel-geometry-quality.md` (results log row)

**Interfaces:**
- Produces: corpus items `logo/tilted-squares-{512,128}`, `logo/wedge-fan-{512,128}`, and for every 512 px template `<name>-512-ds` tagged `degraded:downsample`.


**As built (2026-09-22):** `junction_px` is the 90th percentile inside the
junction zone, not the mean, and junctions are counted from flat interior
colours only (anti-aliased bands were passing for a third region). The wedge
fan uses fixed high-contrast colours and three separate triangles; the squares
are 100 px so none touch. The bench smoke tests count corpus items instead of
hardcoding them. Commits `486590f`, `628eb1b`, `bf0cf8a`.
- [x] **Step 1: Add the templates**

```python
def logo_tilted_squares(rng):
    cols = _pick(rng, PALETTE, 6)
    body = ""
    for (cx, cy), ang, c in zip(((96, 96), (256, 96), (416, 96), (96, 300), (256, 300), (416, 300)),
                                (5, 25, 38, 45, 50, 12), cols):
        body += f'<polygon points="{_poly(_regular(cx, cy, 60 * 2 ** 0.5, 4, math.radians(45 + ang)))}" fill="{c}"/>'
    return _svg(body, background="#FFFFFF")


def logo_wedge_fan(rng):
    blue, pale, ink = _pick(rng, PALETTE, 3)
    body = f'<polygon points="0,512 512,0 512,512" fill="{blue}"/>'
    for x, opening in ((100, 12), (220, 18), (340, 25)):      # tips on the 45° edge
        y = 512 - x
        top = y / math.tan(math.radians(45 + opening))
        body += f'<polygon points="{x},{y} 512,0 {x + top:.2f},0" fill="{pale}"/>'
    for x, ang in ((60, 59), (140, 75)):                       # stems meeting the edge at 59° and 75°
        y = 512 - x
        dx, dy = math.cos(math.radians(ang)), -math.sin(math.radians(ang))
        body += f'<polygon points="{x - 4},{y} {x + 4},{y} {x + 4 + 300 * dx:.2f},{y + 300 * dy:.2f} {x - 4 + 300 * dx:.2f},{y + 300 * dy:.2f}" fill="{ink}"/>'
    return _svg(body, background="#FFFFFF")
```

Check `_regular`'s rotation argument convention (radians vs degrees) in `synth.py:47` and match it. Register both in `TEMPLATES["logo"]`.

- [x] **Step 2: Add the downsampled variant in `generate`**

```python
def render_downsampled(svg: str, size: int) -> bytes:
    hi = Image.open(io.BytesIO(bytes(resvg_py.svg_to_bytes(svg_string=svg, width=2 * size, height=2 * size)))).convert("RGBA")
    lo = hi.resize((size, size), Image.Resampling.BILINEAR)
    buf = io.BytesIO(); lo.save(buf, "PNG", optimize=True); return buf.getvalue()
```

In the size loop, when `size == 512`, also write `f"{name}-{size}-ds.png"` and append an `Item(id=f"{cls}/{name}-{size}-ds", ..., tags=["synthetic", f"size:{size}", "degraded:downsample"])`.

- [x] **Step 3: Regenerate, run all engines, refresh baselines**

```bash
cd backend && .venv/bin/python -m bench generate
.venv/bin/python -m bench run --engines potrace,vtracer,vexel --update-baseline
.venv/bin/python -m pytest -q
```

Record the logo-class `outline_px`, `junction_px`, `line_debt_px`, `score`, `seam_ppm` and the wordmark byte count in the results log above. Also run the Task-3 to Task-6 acceptance probes once as a "before" (see each task's Step 1) and note the numbers.

- [x] **Step 4: Commit**

```bash
git add backend/bench/synth.py backend/bench/corpus backend/bench/baselines docs/superpowers/plans/2026-09-22-vexel-geometry-quality.md
git commit -m "bench: geometry templates and a downsampled variant; baselines carry outline/junction/line-debt"
```

---

### Task 3: Node from incident lines at any angle

**Files:**
- Modify: `backend/studi0trace/engines/vexel/topology.py:503-516` (`_approach` grows along a straight run), `:770-892` (`_junctions` node estimate)
- Modify: `backend/vexel-rs/src/topology.rs:866-1042` (`approach`, `junctions`)
- Test: `backend/tests/test_vexel_topology.py`, `#[cfg(test)]` in `topology.rs`

**Interfaces:**
- Consumes: `_approach(pts, from_start, reach, trim) -> (point, direction) | None`, `_line_through(points) -> (centre, unit_dir)` from `curves.py`.
- Produces: `_approach(pts, from_start, reach, trim, grow_to=APPROACH_MAX) -> tuple[np.ndarray, np.ndarray, float] | None` returning `(point, direction, rms)`; `_node_estimate(lines: list[tuple], mean: np.ndarray, limit: float) -> np.ndarray`; module constants `APPROACH_MAX = 12.0`, `APPROACH_RMS = 0.08`, `PLACEMENT_SIGMA = 0.06`, `NODE_UNCERTAINTY = 0.6`, `TIP_LIMIT = 4.0`. `Arc` gains `tip0: bool = False`, `tip1: bool = False` set where `_wedge` found a tip at that end.


**As built:** besides the plan, one-pixel arcs neither vote nor keep two nodes
of one corner apart (`SHORT_ARC` grouping, kept only when one point serves
every node), a shape cut by the canvas edge is not a wedge tip, nodes on the
canvas edge are held on it (`_on_border`), and vertices placed on handed-back
sliver pixels are carried on the arc (`Arc.sliver`) and excluded from approach
lines. Tip error 1.40 → 0.33 px; the remaining third of a pixel is line
direction noise at a 15° crossing. Commit `835af18` (with Tasks 4 and 5).
- [x] **Step 1: Write the failing test (a 15° wedge tip lands on the true tip)**

```python
def synthetic_wedge(size=256, tip=(75.0, 181.0), opening=15.0):
    """Blue below x+y=size, a pale wedge with its tip on that edge, rendered by resvg."""
    import resvg_py
    top = tip[1] / math.tan(math.radians(45 + opening))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
           f'<rect width="{size}" height="{size}" fill="#fff"/>'
           f'<polygon points="0,{size} {size},0 {size},{size}" fill="#3b8ee8"/>'
           f'<polygon points="{tip[0]},{tip[1]} {size},0 {tip[0] + top:.3f},0" fill="#bfe0ff"/></svg>')
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)), np.array(tip)


def test_a_shallow_wedge_tip_is_placed_where_its_two_sides_cross():
    png, tip = synthetic_wedge()
    svg = trace(png)
    d = re.search(r'<path[^>]*fill="#bfe0ff"[^>]*\sd="([^"]*)"', svg).group(1)
    xy = np.array([[float(a), float(b)] for a, b in re.findall(r"(-?\d+\.?\d*) (-?\d+\.?\d*)", d)])
    nearest = np.linalg.norm(xy - tip, axis=1).min()
    assert nearest < 0.3, f"tip landed {nearest:.2f} px from the true tip"
```

(`trace` is the module's existing helper. Before the change this reports ≈ 1.4 px.)

- [x] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_vexel_topology.py -k shallow_wedge -q` → FAIL, `tip landed 1.4x px`.

- [x] **Step 3: Implement**

`_approach` grows the window while the run stays straight:

```python
APPROACH_MAX = 12.0
APPROACH_RMS = 0.08

def _approach(pts, from_start, reach, trim, grow_to=APPROACH_MAX):
    q = pts if from_start else pts[::-1]
    d = np.linalg.norm(q - q[0], axis=1)
    best = None
    for far in (reach, *np.arange(reach + 2.0, grow_to + 1e-9, 2.0)):
        sel = (d >= trim) & (d <= far)
        if int(sel.sum()) < 2:
            continue
        centre, direction = _line_through(q[sel])
        off = (q[sel] - centre) @ np.array([-direction[1], direction[0]])
        rms = float(np.sqrt(np.mean(off * off)))
        if best is not None and rms > APPROACH_RMS:
            break
        best = (centre, direction, rms)
    if best is None:
        sel = (d > 0) & (d <= 2.0 * reach)
        if int(sel.sum()) < 2:
            return None
        centre, direction = _line_through(q[sel])
        best = (centre, direction, 0.0)
    return best
```

`_node_estimate` replaces the trust fade:

```python
PLACEMENT_SIGMA = 0.06
NODE_UNCERTAINTY = 0.6

def _node_estimate(lines, mean, limit):
    usable = [v for v in lines if v is not None]
    if len(usable) < 2:
        return mean
    acc = np.zeros((2, 2)); rhs = np.zeros(2)
    for point, direction, _rms in usable:
        normal = np.eye(2) - np.outer(direction, direction)
        acc += normal; rhs += normal @ point
    half = (acc[0, 0] + acc[1, 1]) / 2.0
    spread = np.hypot((acc[0, 0] - acc[1, 1]) / 2.0, acc[0, 1])
    lam_min = half - spread
    if lam_min <= 1e-9:
        return mean
    if PLACEMENT_SIGMA / math.sqrt(lam_min) > NODE_UNCERTAINTY:
        return mean            # the lines are too nearly parallel to say where they cross
    guess = np.linalg.solve(acc, rhs)
    move = guess - mean
    away = float(np.linalg.norm(move))
    if away > limit:
        move *= limit / away
    return mean + move
```

In `_junctions`: compute `lines` with the new `_approach`; `target = _node_estimate(lines.values(), mean, limit)`; compute `away`; call `_wedge`; **if a tip is found, recompute `target` from the two wedge sides' lines only with `limit=TIP_LIMIT` (4.0)** and mark `arcs[i].tip0/tip1 = True` for the two side arcs at that end. Arithmetic in plain sums, in a fixed order, so the Rust port lands on the same bits.

- [x] **Step 4: Port to Rust**

In `topology.rs`, `approach` returns `Option<(P, P, f64)>` with the same growth loop and the same `line_through`; add `node_estimate(lines: &[Option<(P, P, f64)>], mean: P, limit: f64) -> P` with the same eigenvalue arithmetic (`half`, `spread`, `lam_min`, the `PLACEMENT_SIGMA / lam_min.sqrt()` test, the clamp); `Arc` gains `tip0`/`tip1`. Add a `#[test]` that two lines at 15° through a known point recover it within 1e-9 and that two lines at 5° return `mean`.

- [x] **Step 5: Verify both implementations**

```bash
.venv/bin/python -m maturin develop --release -m vexel-rs/Cargo.toml
(cd vexel-rs && cargo test -q)
.venv/bin/python -m pytest tests/test_vexel_topology.py tests/test_vexel_curves.py -q
.venv/bin/python -m tools.diffcheck arcs wedges
for b in python rust; do VEXEL_BACKEND=$b .venv/bin/python -c "
from studi0trace.engines.vexel.engine import VexelParams, VexelEngine
from studi0trace.imaging.intake import load_upload
print(VexelEngine().trace(load_upload(open('bench/corpus/real/logo/vexel-wordmark-512.png','rb').read(), max_bytes=1<<30, max_pixels=1<<30), VexelParams()).svg)" > /tmp/wm-$b.svg; done; cmp /tmp/wm-python.svg /tmp/wm-rust.svg && echo identical
.venv/bin/python -m bench run --engines vexel
.venv/bin/python -m bench compare bench/baselines/vexel.json bench/reports/<ts>/results.json --metric junction_px
```

Expected: tests pass, diffcheck within tolerance, identical wordmark, `junction_px` improved on `logo`, no `score`/`seam_ppm` regression. `--update-baseline` and commit with the deltas in the message.

- [x] **Step 6: Commit**

```bash
git add backend/studi0trace/engines/vexel/topology.py backend/vexel-rs/src/topology.rs backend/tests/test_vexel_topology.py backend/bench/baselines/vexel.json
git commit -m "fix(vexel): place a junction where its arcs cross, at any angle"
```

---

### Task 4: Trim the approach window at every node

**Files:**
- Modify: `topology.py:1070-1097` (`_fit_arc`), `:1153-1166` (`_sharpen_piece`), `_bled` callers
- Modify: `topology.rs:1158-1220` (`sharpen_piece`, `fit_arc`)
- Test: `tests/test_vexel_topology.py`, `topology.rs` tests

**Interfaces:**
- Consumes: `Arc.tip0/tip1` from Task 3.
- Produces: `_sharpen_piece(pts, lo, hi, corners, node_trim: float, tip_trim: float, tips: tuple[bool, bool])`; constants `NODE_TRIM = 1.5`, `TIP_TRIM = 4.0`.


**As built:** trims are per end (`Arc.trim0/trim1`), widened by the node's
move and capped at `TRIM_SHARE` (30 %) of the arc so thin strokes and small
discs keep their vertices — without the cap thin-mark's seam doubled and a
disc became a 37-gon. Wedge sides are pinned to their own direction, not the
through axis. Bled copies are fitted as interior plus two explicit jogs.
- [x] **Step 1: Write the failing tests**

```python
def test_a_wedge_side_runs_straight_into_its_tip():
    """The bulge in the 750% screenshot: vertices next to a node are three-fill
    mixtures and were fitted faithfully. Compare the last 6 px of each side with
    the true line; the flare must be gone."""
    png, tip = synthetic_wedge()
    svg = trace(png)
    pts = sample_path(svg, fill="#bfe0ff", per_segment=200)          # helper: sample the path densely
    for direction in (math.radians(-45 - 15), math.radians(-45)):    # steep side, then the shared edge
        u = np.array([math.cos(direction), math.sin(direction)])
        rel = pts - tip; s = rel @ u; d = rel @ np.array([-u[1], u[0]])
        own = (s > 0.3) & (s < 6.0) & (np.abs(d) < 1.5)
        assert own.any() and np.abs(d[own]).max() < 0.15, f"flare {np.abs(d[own]).max():.2f} px"


def test_a_tilted_square_stays_four_lines():
    for ang in (38, 45, 50):
        svg = trace(tilted_square_png(ang))                            # helper: one 120 px square at ang on white
        d = re.search(r'<path[^>]*\sd="([^"]*)"', svg).group(1)
        assert d.count("C") == 0 and d.count("L") == 3, f"{ang}°: {d[:80]}"
```

- [x] **Step 2: Run to verify they fail** (flare ≈ 0.25–0.5 px; 45° emits cubics).

- [x] **Step 3: Implement**

```python
NODE_TRIM = 1.5
TIP_TRIM = 4.0

def _sharpen_piece(pts, lo, hi, corners, node_trim=NODE_TRIM, tip_trim=TIP_TRIM, tips=(False, False), trim=0.8):
    piece = pts[lo:hi + 1]
    if len(piece) < 4:
        return piece
    inner = piece[1:-1]
    keep = np.ones(len(inner), bool)
    start_trim = (tip_trim if tips[0] else node_trim) if lo == 0 else (trim if lo in corners else 0.0)
    end_trim = (tip_trim if tips[1] else node_trim) if hi == len(pts) - 1 else (trim if hi in corners else 0.0)
    if start_trim > 0:
        keep &= np.linalg.norm(inner - piece[0], axis=1) >= start_trim
    if end_trim > 0:
        keep &= np.linalg.norm(inner - piece[-1], axis=1) >= end_trim
    return np.vstack([piece[:1], inner[keep], piece[-1:]])
```

`_fit_arc` passes `tips=(arc.tip0, arc.tip1)`. The end vertices themselves always stay: they are the shared node. Port `sharpen_piece` identically. The bled copy inherits the flags through `_bled`.

- [x] **Step 4: Verify** as Task 3 Step 5, plus `--metric line_debt_px` and `--metric outline_px`. Expected: tests pass; `logo` `line_debt_px` and `junction_px` down; wordmark flare probe (spec's `flare.py` method) p50 < 0.15 px.

- [x] **Step 5: Commit** `fix(vexel): do not believe the outline inside a node's approach window`

---

### Task 5: Smooth continuation decided by a fit, not the corner threshold

**Files:**
- Modify: `topology.py:770-892` (`_junctions`: the shared-tangent decision)
- Modify: `topology.rs:895-1042` (`junctions`)
- Test: `tests/test_vexel_topology.py`

**Interfaces:**
- Produces: `_smooth_through(pa: np.ndarray, pb: np.ndarray, tol: float, span: float = SMOOTH_SPAN) -> bool` with `SMOOTH_SPAN = 10.0`; `_junctions` no longer reads `corner_threshold` for this decision (the parameter still drives `_open_corners`/`find_corners`).


**As built:** `_smooth_through` fits one primitive through 10 px of each arc
(node in the middle, vertices inside the trim and on slivers left out) at 0.75
of the tolerance, behind a 90° pre-filter. The pie fixture's 40° pair no
longer hooks; a 15 px disc over a split background stays a circle.
- [x] **Step 1: Write the failing tests**

```python
def test_two_arcs_meeting_at_59_degrees_are_a_corner():
    """A stem meeting an edge at 59° was forced G1 because 59 ≤ corner_threshold."""
    svg = trace(stem_on_edge_png(angle=59))                           # helper: 8 px stem meeting a 45° edge
    pts = sample_path(svg, fill=STEM_FILL, per_segment=100)
    # the stem's side must be straight up to the node: no sample more than 0.15 px off the true side
    assert side_deviation(pts, stem_side_line(59)) < 0.15


def test_a_small_circle_crossed_by_a_boundary_stays_smooth():
    """A 15 px circle over a two-colour background: the two arcs of the circle
    meet at nodes with a 60° chord turn yet are one smooth curve."""
    svg = trace(circle_on_split_png(r=15))
    d = re.search(r'<path[^>]*fill="%s"[^>]*\sd="([^"]*)"' % CIRCLE_FILL, svg).group(1)
    assert max_tangent_kink_deg(d) < 5.0
```

(`stem_on_edge_png`, `circle_on_split_png`, `sample_path`, `side_deviation`, `max_tangent_kink_deg` are small resvg/regex helpers written in the test module; `max_tangent_kink_deg` compares the outgoing tangent of each segment with the incoming tangent of the next.)

- [x] **Step 2: Run to verify they fail** (first fails with a hook of ≈ 0.8 px; second passes today and guards the change).

- [x] **Step 3: Implement**

```python
SMOOTH_SPAN = 10.0

def _smooth_through(pa, pb, tol, span=SMOOTH_SPAN):
    """Are two arcs leaving one node a single smooth curve? Fit one primitive
    through `span` px of each, node in the middle; smooth if one line or one
    cubic does it within `tol`. A 59° kink cannot be one cubic; a 15 px circle can."""
    def head(p):
        cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))])
        return p[cum <= span]
    a, b = head(pa), head(pb)
    if len(a) < 3 or len(b) < 3:
        return False
    joined = np.vstack([a[::-1], b[1:]])
    segs = fit_open(joined, 0.75 * tol)
    return len(segs) == 1
```

In `_junctions`, replace `if not tangents and best is not None and best[0] <= corner_threshold:` with: for the closest pair `(ka, kb)`, `if not tangents and _smooth_through(pts_from_node(ka), pts_from_node(kb), tol):` where `pts_from_node` returns the arc's points ordered from the node outward, and `tol` is `params.tol` threaded into `_junctions` (signature gains `tol: float`). Port to Rust with `fit_open` from `curves.rs`.

- [x] **Step 4: Verify** as Task 3 Step 5; compare `junction_px`, `outline_px`, `score`. Commit `fix(vexel): a boundary continues through a junction only where one curve fits both sides`.

---

### Task 6: Lines first, corners from lines, collinear merge

**Files:**
- Modify: `curves.py` (add `line_runs`, `fit_pieces`; delete `straight_runs`, `MIN_LINE`, `STRAIGHT_SAG`; `fit_contour_segments` and `split_pieces` use lines)
- Modify: `topology.py:1070-1097` (`_fit_arc` uses `fit_pieces`)
- Modify: `curves.rs:154-223` (delete `straight_runs`), add `line_runs`, `fit_pieces`; `topology.rs` `fit_arc`
- Test: `tests/test_vexel_curves.py` (replace `test_a_rounded_square_keeps_its_sides_straight`'s dependence on `straight_runs`), `tests/test_vexel_topology.py`

**Interfaces:**
- Produces:
  - `curves.line_runs(pts: np.ndarray, closed: bool = False) -> list[tuple[int, int, np.ndarray, np.ndarray]]` — `(i, j, centre, direction)` runs where the TLS residual RMS ≤ `LINE_RMS = 0.08`, p98 ≤ `LINE_P98 = 0.25`, chord ≥ `LINE_MIN = 4.0` px; runs never overlap; greedy from the start, retried from `i + 1` when a run is too short.
  - `curves.fit_pieces(pts, tol, corners: list[int], t_start, t_end, node_ends: tuple[bool, bool]) -> list[Segment]` — emits each line run as one `Line` (endpoints = projections of the run's first/last vertex onto the TLS line; an arc end that falls inside a run is kept exactly at the node); consecutive lines meeting at > `MERGE_DEG = 2.0` degrees are joined at their intersection, otherwise merged into one line; a curve gap shorter than `GAP_MIN = 1.5` px between two lines is dropped and the lines intersected; every other gap is fitted with `fit_cubics` with tangents pinned to the adjoining line directions (or the node tangents at arc ends).
- Consumes: `fit_cubics`, `_line_through`, `_intersect`, `find_corners`, `_open_corners`.

**As built (2026-09-22):** `line_runs` (TLS residual RMS <= 0.10, p98 <= 0.30, min 8.1 px, plus a
parabola-fit sag test <= 0.10 px and shedding of end vertices off the line), `lines_first` (runs as
lines, gaps as cubics, chords of a curve demoted when they turn <= 20 degrees against a neighbour
within 12 px, corner gaps intersected, jogs kept unless within 0.5 px of the line), `fit_stretch`
(lines-first kept when its cost, lines at 0.5, is no higher than the curve fit), `fit_closed` (loop
opened mid-run), `corners_from_runs` (corners placed at the crossing of the adjacent runs),
`merge_lines`. Interior arc corners are sharpened from their approach lines. Every threshold on a
distance between placed vertices sits off a multiple of a half, because lattice-placed vertices are
exact multiples apart and a tie is decided differently by numpy and Rust. Node lines are weighted
by their real uncertainty. Task 8's plan for collinear merging is folded in here.
- [x] **Step 1: Write the failing tests**

```python
def test_line_runs_finds_a_straight_edge_despite_end_noise():
    pts = dense_polygon([(0, 0), (100, 3)], step=0.7)                # a 3° edge, 143 vertices
    rng = np.random.default_rng(1); pts = pts + rng.normal(0, 0.05, pts.shape)
    pts[0] += (0.0, 0.35); pts[-1] += (0.0, -0.3)                    # ends off the line, as sharpened corners are
    runs = line_runs(pts)
    assert len(runs) == 1 and runs[0][0] <= 1 and runs[0][1] >= len(pts) - 2

def test_a_tilted_square_under_jpeg_is_four_lines():
    for ang in (5, 25, 38, 45, 50):
        svg = trace(jpeg(tilted_square_png(ang), quality=75))
        d = re.search(r'<path[^>]*\sd="([^"]*)"', svg).group(1)
        assert d.count("C") == 0 and d.count("L") == 3, f"{ang}°: {d[:100]}"

def test_a_downsampled_square_edge_is_one_line_not_eleven():
    svg = trace(downsampled(tilted_square_png(5)))
    assert re.search(r'<path[^>]*\sd="([^"]*)"', svg).group(1).count("L") == 3

def test_a_circle_is_not_chopped_into_lines():                        # keep from the old suite, now against line_runs
    assert line_runs(circle_poly(64, 64, 30)) == []
```

- [x] **Step 2: Run to verify they fail.**

- [x] **Step 3: Implement `line_runs` and `fit_pieces`**

```python
LINE_RMS = 0.08
LINE_P98 = 0.25
LINE_MIN = 4.0
MERGE_DEG = 2.0
GAP_MIN = 1.5

def _tls(pts):
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    d = vt[0]
    off = (pts - c) @ np.array([-d[1], d[0]])
    return c, d, float(np.sqrt(np.mean(off * off))), float(np.percentile(np.abs(off), 98))

def line_runs(pts, closed=False):
    n = len(pts)
    if n < 3:
        return []
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    runs, i = [], 0
    while i < n - 2:
        j, best = i + 2, None
        while j < n:
            c, d, rms, p98 = _tls(pts[i:j + 1])
            if rms > LINE_RMS or p98 > LINE_P98:
                break
            best = (j, c, d); j += 1
        if best is not None and cum[best[0]] - cum[i] >= LINE_MIN:
            runs.append((i, best[0], best[1], best[2])); i = best[0]
        else:
            i += 1
    return runs
```

The `_tls` in a growing loop is O(n²) on long arcs; cache prefix sums of x, y, x², xy, y² so the covariance of `pts[i:j+1]` is O(1) and the p98 check is done only when the RMS passes (compute residuals for the candidate). Write it that way from the start and mirror the same prefix-sum arithmetic in Rust so both land on the same floats.

`fit_pieces` walks the arc: breaks = sorted set of `{0, n-1}` ∪ corners ∪ run boundaries. For each line run emit `Line(project(pts[i]), project(pts[j]))`; at arc ends inside a run use the node vertex itself. Between two lines: if the angle between directions ≤ `MERGE_DEG`, merge (one line from the first start to the second end, re-projected on the joint TLS line); else if the gap chord < `GAP_MIN`, set both lines' shared endpoint to `_intersect`; else fit the gap with `fit_cubics(gap, t1=line_a.direction, t2=-line_b.direction, tol)`. Corners from `_open_corners`/`find_corners` inside a curve gap keep their old sharpening (`split_pieces` logic). `fit_contour_segments` (closed contours) calls `fit_pieces` with the polyline rolled to start at a run boundary or corner. Delete `straight_runs`, `MIN_LINE`, `STRAIGHT_SAG` and their Rust twins; update `test_a_rounded_square_keeps_its_sides_straight` to assert on emitted `L` count instead.

- [x] **Step 4: Port to Rust** (`line_runs` with the same prefix sums, `fit_pieces`, `fit_arc`, `fit_contour_segments`). `cargo test` gets the 3° edge and circle tests.

- [x] **Step 5: Verify** as Task 3 Step 5 with `--metric line_debt_px` (expect ≈ 0 on `logo`), `outline_px`, `score`, `seam_ppm`, and byte count (expect down). Commit `feat(vexel): lines first - straightness from residuals, corners from the lines`.

---

### Task 7: Segment-minimising, bump-free spline fit

**Files:**
- Modify: `curves.py:523-575` (`fit_c2`), `curves.rs:947-1005` (`fit_c2`)
- Test: `tests/test_vexel_curves.py`

**Interfaces:**
- Produces: `fit_c2(points, t1, t2, tol) -> list[Cubic] | None` unchanged in signature; new constants `EQUALISE_ROUNDS = 4`, `BUMP_RATIO = 0.85`.

- [x] **Step 1: Write the failing tests**

```python
def test_spline_error_is_spread_evenly_across_spans():
    pts = noisy_s_curve(n=300, sigma=0.05)                            # helper in the module
    cubics = fit_c2(pts, *end_tangents(pts), tol=0.4)
    errs = [max_error_of_span(c, pts) for c in cubics]
    assert max(errs) < 0.4 and max(errs) - min(errs) < 0.12

def test_no_span_has_a_bump():
    cubics = fit_c2(noisy_s_curve(300, 0.05), *end_tangents(pts), tol=0.4)
    for c in cubics:
        chord = np.linalg.norm(c.p1 - c.p0)
        assert np.linalg.norm(c.c1 - c.p0) < BUMP_RATIO * chord and np.linalg.norm(c.c2 - c.p1) < BUMP_RATIO * chord
```

- [x] **Step 2: Run to verify they fail.**

- [x] **Step 3: Implement.** After `fit_c2` reaches a knot set within `tol`, run `EQUALISE_ROUNDS` of knot re-placement: compute each span's max error `e_k`; move each interior knot toward the neighbour span with the larger error by `0.25 * (e_right - e_left) / (e_right + e_left) * span_width`; refit; keep the knots only if all spans stay within `tol` and `max(e) - min(e)` shrank. Then check every span's control-arm ratio; if any exceeds `BUMP_RATIO`, insert a knot at that span's midpoint and refit once; if still bumpy return `None` (the split-and-recurse fitter answers). Port identically.

- [x] **Step 4: Verify** with `--metric nodes_per_100px`, `outline_px`, `score`. Commit `feat(vexel): spread the spline's error evenly and refuse bumpy spans`.

---

**As built.** `_finish_spline` runs after `fit_c2` finds a knot set inside `tol`: `EQUALISE_ROUNDS = 4` rounds of
`_span_errors` → knot moves of `0.25·(e_right−e_left)/(e_right+e_left)·width` → `_solve_spans`, kept only while every
span stays inside `tol` and the spread `max−min` shrinks. `_bumpy` flags a span whose control arm exceeds
`BUMP_RATIO = 0.85` of its chord; it earns one knot at its middle, and a second bump declines the spline. Rust twin in
`curves.rs` (`span_errors`, `solve_spans`, `bumpy`, `finish_spline`). Tests: spline error spread (max < 0.45, spread <
0.16 over a noisy S-curve) and no-bump. Bench against the Task 6 baseline: score unchanged, outline_px unchanged on
flat/logo/shadow, line_debt_px down on flat/gradient/shadow, nodes_per_100px down on flat, seam_ppm −12 on flat and
+0.3 % on logo (thin-mark-512, the stroke item). Parity: diffcheck arcs/wedges clean over 96 items; the wordmark differs
in 2 of 868 numbers by 0.01 (accepted, last-decimal). Baseline moved to this engine (a319f8e).

### Task 8: Regularity on the graph

**Files:**
- Create: `backend/studi0trace/engines/vexel/regularity.py`, `backend/vexel-rs/src/regularity.rs` (register in `lib.rs`)
- Modify: `topology.build` (call `regularize(arcs)` after `_junctions`, before fitting), `engine.rs` twin
- Test: `backend/tests/test_vexel_regularity.py`

**Interfaces:**
- Produces: `regularity.regularize(arcs: list[Arc], snap_axis_deg: float) -> None` (mutates `Arc.pts` end vertices and inserts `Arc.line: tuple[centre, direction] | None` for arcs that are one line run). Constants `CLUSTER_DEG = 1.0`, `CLUSTER_MIN_PX = 40.0`, `END_MOVE_MAX = 0.15`.
- Consumes: `curves.line_runs`.

- [x] **Step 1: Write the failing test**

```python
def test_parallel_edges_of_one_logo_come_out_exactly_parallel():
    svg = trace(tilted_square_png(38))
    dirs = line_directions(svg)                                      # helper: angle mod 180 of every L
    pairs = [(a, b) for a in dirs for b in dirs if abs(((a - b) + 90) % 180 - 90) < 0.6]
    assert all(abs(((a - b) + 90) % 180 - 90) < 1e-6 for a, b in pairs), "parallel edges differ by a fraction of a degree"
```

- [x] **Step 2: Run to verify it fails** (today parallel edges differ by 0.1–0.5°).

- [x] **Step 3: Implement.** For every open arc whose `line_runs` covers ≥ 95 % of its length record `(angle mod 180, length)`. Cluster angles within `CLUSTER_DEG`; for clusters with total length ≥ `CLUSTER_MIN_PX` set the target angle to the length-weighted mean, snapped to 0/90 when within `snap_axis_deg`, and to exactly `other ± 90` when another qualifying cluster sits within `CLUSTER_DEG` of perpendicular. For each member arc rotate the line about its midpoint to the target angle; the requested end moves are `|Δθ| · L / 2`; if either exceeds `END_MOVE_MAX` skip the arc. Otherwise set `Arc.line` and move the end vertices; every arc incident to a moved node takes the same new position (nodes are `n0`/`n1` indices, so collect moves per node, average them, and apply once). `fit_pieces` emits an arc with `Arc.line` as one `Line` between its ends. Port identically.

- [x] **Step 4: Verify** (`seam_ppm` must not move; `outline_px` down on `tilted-squares`). Commit `feat(vexel): parallel, perpendicular and axis regularity across the boundary graph`.

---

**As built.** The stage runs on the *fitted segments* rather than on `Arc.pts` (the plan's `Arc.line` route): after
every arc is fitted, `regularity.regularize([(arc.segments, arc.closed) …], snap_axis_deg)` clusters every `Line`'s
direction (`CLUSTER_DEG = 0.5`), snaps clusters carrying `CLUSTER_MIN_PX = 40` to the length-weighted circular mean
(exactly 0/90 within `snap_axis_deg`, exactly `other + 90` within `PERP_DEG = 0.5` of a heavier cluster), and turns
each line about its node end or its midpoint — but only when neither end has to move more than `END_MOVE_MAX = 0.15`
px, the placement's own uncertainty: a line that would have to move further is not really in the cluster and stays
where its pixels are. Joints are re-made: two lines meet at their new crossing when that is within `END_MOVE_MAX` of
the plain projection, otherwise the old joint projects onto the new line; a cubic neighbour's arm moves with its end
so its tangent survives. A line with a node at both ends is left alone; nodes never move. A first cut with 1.0° /
1.5 px cost 0.0009 score and +858 seam_ppm on logo (overlap-512-ds, silverpeak-badge, thin-mark); with the 0.15 px
cap the bench is neutral on every gate (score, seam_ppm, junction_px, line_debt_px, nodes_per_100px all within
noise; logo bytes +26) and the exactness shows only in geometry: raster metrics cannot see a 0.1° correction.
Collinear merging had already landed in Task 6 (`merge_lines`). Rust twin `regularity.rs`, called from `build_opt`.
Finding while testing: on a clean render the fit already had parallel edges exact to the SVG's two-decimal output, so
the test uses JPEG'd tilted squares, where perpendicular pairs were 0.10° off at 5° and are now within one output
rounding step (0.0048° over 120 px). Parity: tilted-squares byte-identical between backends; wordmark 3/868 and
logomark 6/2203 numbers differ by 0.01 (last-decimal). wedge-fan-512 differs in structure between backends, but did so
at Task 6 and Task 7 too (Rust 4298 bytes then, 4308 now; Python 4003) — a pre-existing upstream/fit parity gap,
spawned as its own task.

### Task 9: Primitives: rounded rectangles and circular arcs

**Files:**
- Modify: `curves.py` (add `CircArc` segment, `try_rounded_rect`, `fit_arc_run`; `path_d`, `reverse_segments`, `shape_svg` handle them), `curves.rs` twins
- Modify: `frontend/src/components/inspector/*` — verify the anchor overlay parses `A` (it draws real geometry per `git log`); if not, add arc → cubic conversion in `frontend/src/lib/`
- Test: `tests/test_vexel_curves.py`, `frontend/src/**/*.test.tsx` for the inspector parse

**Interfaces:**
- Produces: `@dataclass class CircArc: p0, p1, r: float, large: bool, sweep: bool` in `Segment`; `try_rounded_rect(poly, params) -> RoundedRect | None` (`RoundedRect(x, y, w, h, rx)` emitted as `<rect ... rx>`); in `fit_pieces` a curve gap whose Kåsa circle fit has 95th-percentile deviation ≤ `tol` becomes one `CircArc`.

- [x] **Step 1: Write the failing tests**

```python
def test_rounded_square_becomes_a_rect_with_rx():
    svg = trace(rounded_square_png(size=120, rx=18))
    m = re.search(r'<rect [^>]*rx="([\d.]+)"', svg)
    assert m and abs(float(m.group(1)) - 18) < 0.3

def test_a_ring_segment_is_emitted_as_an_arc():
    svg = trace(ring_sector_png(r_outer=60, r_inner=40, sweep_deg=120))
    assert svg.count("A") >= 2 and svg.count("C") <= 2
```

- [x] **Step 2: Run to verify they fail.**

- [x] **Step 3: Implement.** `try_rounded_rect`: a closed ring with exactly four line runs, axis-aligned within `snap_axis_deg`, whose four gaps each fit a circle within `tol` with radii equal within `tol` and centres inset by `r` from the line intersections. `fit_arc_run`: in `fit_pieces`, before `fit_cubics` on a gap ≥ 6 px, try `fit_circle(gap)`; accept if dev ≤ `tol` and the gap subtends ≥ 10°; set `large = subtended > 180°`, `sweep` from the sign of the cross product. `path_d` writes `A r r 0 {large:d} {sweep:d} x y`. `reverse_segments` flips `sweep` and swaps `p0/p1`. Update `bench/geometry._segments` to sample `A` (convert to points by the centre-angle formula) and the frontend inspector likewise. Port identically.

- [x] **Step 4: Verify** (`bytes` down on `logo/ring`, `outline_px` unchanged; `npm run test:run` and `npm run build` pass). Commit `feat(vexel): rounded rectangles and circular arcs as primitives`.

---

**As built.** `RoundedRect(x, y, w, h, rx)` → `<rect … rx>`; `try_rounded_rect(poly, params)` is tried in `fit_shape`
after circle/ellipse/rect when the contour has no corners: the polygon is rolled to start at its farthest vertex from
the centroid (mid-corner), `line_runs` must give exactly four axis-aligned runs alternating H/V, and each gap must lie
inside `tol` (95th percentile) of a circle tangent to its two sides. The radius is read per vertex from its inside
distances `u, v` to those sides as `(u+v)+sqrt(2uv)` (the median over vertices with `min(u,v) > max(tol, 0.5)`), not
from a Kåsa fit, which the straight vertices the runs shed at their ends biased by +0.5 px; radii agree within
`max(2·tol, 5 %)`. `CircArc(p0, p1, r, large, sweep)` joins `Segment`; `fit_arc_run` (Kåsa circle, p95 ≤ tol, max ≤
2·tol, ≥ 10° over ≥ 6 px, pinned tangents within 3°) is tried on every stretch in `fit_stretch` (an arc costs 1.0 like
a cubic and wins the tie), on every gap between line runs in `lines_first`, and a corner-free closed loop that is one
circle becomes two half arcs in `fit_closed` (a ring's contours). `path_d` writes `A r r 0 large sweep x y`,
`reverse_segments` flips `sweep`, `arc_centre`/`arc_points` draw it as a renderer would. Two things the first bench
run taught: (1) an arc's ends are fixed by its neighbours, so the circle that gets drawn is the one *through the ends*,
not the free Kåsa fit — forcing the fit's radius through ends 0.4 px off displaced whole circles (cutout-512 outline
0.026 → 1.00); `circle_through` now finds the best centre on the chord's bisector by golden section and the arc is
accepted only if that circle holds the points (cutout-512 now 0.013), and a corner-free closed loop projects its start
vertex onto the circle; (2) the corner between a straight side and a circular arc used to be the crossing of two local
lines, 0.2 px off radially, and a 6 px chord of a 40 px circle passes the straight-run test, so `corners_from_runs`
now treats a piece that is one circle (`_whole_circle`, dev ≤ 0.15) as the circle at both ends and places a line–circle
corner where the line cuts the circle (`_line_circle`; `_circle_near` reads the circle from the first 30 px otherwise).
Rust twins throughout
(`Segment::Arc`, `Shape::RoundedRect`, `try_rounded_rect`, `fit_arc_run`). Consumers taught `A`: `bench/geometry`
(`_segments`, `_arc_points`, `line_debt` counts a flat arc as debt like a flat cubic), the test helpers
(`path_points`, `sample_path`, `path_anchors`, `line_directions`), and the frontend's `pathAnchors` already skipped the
five arc parameters — a test now pins that. Tests: `tests/test_vexel_primitives.py` (rounded square → `rx` 18 ± 0.3
clean and JPEG; geometry read-back and impostors; ring sector → exactly two `A`, radii 40/60 ± 0.3; arc fit reads
radius/sweep/large, refuses a 6° tangent mismatch, an 8° sliver and an ellipse). `npm run test:run`/`build` pass.
Parity: ring-512, wedge-fan-128, tilted-squares, overlap, stripes, cutout, hex-nest identical; wordmark 3/865 and
logomark 5/2190 numbers by 0.01. Pre-existing backend gaps found while checking (all present at Task 8 too):
sticker-512 eyes are `<ellipse>` in Python and a path in Rust (`fit_ellipse` acceptance), venn-512 differs in
spline knot placement (Task 7's equalisation flips a branch on last-bit solver differences), silverpeak-badge-768
differs in structure, thin-mark strokes (known).

### Task 10: Symmetry and repeated shapes

**Files:**
- Create: `backend/studi0trace/engines/vexel/symmetry.py`, `backend/vexel-rs/src/symmetry.rs`
- Modify: `topology.build` (call `symmetrize(arcs, rings_by_shape)` after regularity, before fitting), `engine.py`/`engine.rs` assembly (`<defs>`/`<use>`)
- Test: `backend/tests/test_vexel_symmetry.py`

**Interfaces:**
- Produces: `symmetry.mirror_axes(poly: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]` (candidate axes: centroid with principal directions and their 45° rotations); `symmetry.symmetrize(poly, axis) -> np.ndarray` (each vertex averaged with its nearest reflected neighbour via `scipy.spatial.cKDTree`, applied only when the mean reflected distance ≤ `SYM_MEAN = 0.10` px and max ≤ `SYM_MAX = 0.30`); `symmetry.rotational_order(poly) -> int` (2..8 by the same test); `symmetry.duplicates(shapes) -> list[list[int]]` (same fill, outline Chamfer ≤ 0.10 px after translation) for `<use>`.

- [x] **Step 1: Write the failing tests**

```python
def test_a_mirror_symmetric_mark_is_emitted_symmetric():
    svg = trace(heart_png())                                           # helper: mirror-symmetric path rendered at 512
    left, right = split_path_at_axis(svg)                              # helper: sample and reflect
    assert chamfer(left, right) < 0.05

def test_repeated_dots_become_use_elements():
    svg = trace(dot_grid_png(rows=3, cols=3, r=12))
    assert svg.count("<use ") == 8 and svg.count("<defs>") == 1
```

- [x] **Step 2: Run to verify they fail.**

- [x] **Step 3: Implement** as specified in the interface; symmetrisation moves shared arc vertices, so both regions on an arc move together (the ring of the symmetric shape is the union of its arcs; vertices are replaced in place in `Arc.pts`). `<use>`: emit the first shape as `<path id="s1">` inside `<defs>` and every duplicate as `<use href="#s1" x=dx y=dy>` with the duplicate's own fill attribute when fills differ only by name; keep painter's order. Port identically.

- [x] **Step 4: Verify** (`bytes` down on `logo/hex-nest`, `flat/mosaic`; `outline_px` unchanged or better). Commit `feat(vexel): mirror and rotational symmetry, repeated shapes as <use>`.

---

**As built.** `symmetry.py`: `mirror_axes` (PCA directions, their 45° turns, every 15°, snapped to the canvas axes
within `AXIS_SNAP_DEG = 1.5`), `reflect`/`rotate`, `symmetrize(poly, axis)`, `rotational_order` (8…2),
`symmetrize_rotational`, `ring_symmetries(poly) → (poly | None, axes)`; the fit test is mean ≤ `SYM_MEAN = 0.10`
and 99th percentile ≤ `SYM_MAX = 0.30` (absolute cap `SYM_CAP = 1.0`) — the plain max was decided by one cusp vertex
handed back with a sliver. `topology._symmetrize` runs after placement and before `_junctions` on every single-ring
label that does not touch the frame (frame vertices are exact and must not be averaged), writes the symmetrised
vertices back into the shared arcs, and records `Arc.mirror` for a ring that is one closed arc. Symmetrising the
vertices alone left the heart's emitted outline 0.12 px asymmetric (the fit is not a symmetric operation and the notch
cusp sat 0.48 px off the axis), so `_fit_mirrored` fits one half between the two axis crossings — a smooth crossing
pins the tangent perpendicular to the axis, a corner crossing (turn > `corner_threshold`) is sharpened as the approach
line's crossing with the axis — reflects the segments (`_reflect_segment`, arcs flip `sweep`) and merges the collinear
line pairs at smooth crossings; the mirror axis is chosen by a canonical rank (exact canvas axis first, then angle) so
both implementations pick the same one. Heart: mirror chamfer 0.34 → 0.0528 → < 0.05 once the near-vertical PCA
axis snaps to exactly vertical. The first bench showed triangle-bar's apex 0.93 px low (outline 0.005 → 0.105): a
corner crossing placed from a 3 px approach sits on anti-aliasing mixtures pulled inward, so `_axis_corner_from_run`
now crosses the adjacent straight run with the axis (as `corners_from_runs` does), and the apex lands at (256, 60)
exactly. Parity: triangle-bar, tilted-squares, hex-nest, cutout byte-identical; mosaic-512 differs in `<use>` count
because two stub corners differ between backends, a gap already present at Task 8 (3676 vs 3684 bytes then). `reuse.py`: `emit(pending, precision)` groups shapes that are the same primitive to
`USE_TOL = 0.10` or paths with the same segment signature whose sampled outlines agree to 0.10 px both ways after
translation; the first copy's geometry goes into `<defs>` with only an `id`, every copy (the first included, so the
test counts 9 uses for a 3×3 grid, not the plan's 8) is `<use href="#uN" x y fill…>` in paint order. Rust twins:
`symmetry.rs` (exact nearest-vertex lookup through a widening grid, numpy's linear percentile), `topology.rs`
`symmetrize_boundary`/`fit_mirrored`, `reuse.rs`. The frontend's `svgdoc.ts` resolves `<use>` (tag, anchors, outline
translated, own fill; hiding a copy removes its `<use>`, not the definition). Tests: `tests/test_vexel_symmetry.py`
(heart emitted symmetric, square/hexagon/pentagon/ellipse orders and axes, a blob has none, dot grid → 9 `<use>`,
definition unpainted), Rust unit tests, `svgdoc.test.ts`.

### Task 11: Render-and-compare refinement (opt-in)

**Files:**
- Create: `backend/studi0trace/engines/vexel/refine_render.py`, `backend/vexel-rs/src/refine_render.rs` (dependency `tiny-skia` in `vexel-rs/Cargo.toml`)
- Modify: `engine.py` (`VexelParams.refine: bool = False`, toggle, group "Curves", label "Render refinement"), `engine.rs`
- Modify: `tools/diffcheck.py` (new stage `refined` with `("rms", 0.05, 0.0)` tolerance)
- Test: `backend/tests/test_vexel_refine.py`

**Interfaces:**
- Produces: `refine_render.refine(arcs: list[Arc], fills, rank, rgb, alpha, iterations: int = 3, step: float = 0.1) -> None`: for each open arc, rasterise the two adjacent shapes' current geometry in a crop 3 px around the arc at 4× (Python: resvg on a crop SVG; Rust: tiny-skia), box-filter to 1×, compare with the source in the band, and coordinate-descend the normal offset of each interior control point (lines stay lines: only their two ends move, jointly for all arcs at a node), accepting a move only when the band error falls.

- [x] **Step 1: Write the failing test**

```python
def test_refinement_removes_a_planted_half_pixel_offset():
    png, tip = synthetic_wedge()
    svg_plain = trace(png, refine=False); svg_ref = trace(png, refine=True)
    truth = wedge_truth_svg()
    assert outline_error(truth, svg_ref, 256, 256)["outline_px"] < 0.8 * outline_error(truth, svg_plain, 256, 256)["outline_px"]
```

- [x] **Step 2: Run to verify it fails** (`refine` is not a parameter).

- [x] **Step 3: Implement**, Python first as the spec, then Rust with `tiny-skia`; the diffcheck stage compares refined arc vertices to 0.05 px RMS.

- [x] **Step 4: Verify** on the corpus with `refine=true` as a sweep (`python -m bench sweep --engine vexel --param refine=false:true`): `outline_px` down on `logo` and real items, `elapsed_ms` within 3×. Default stays off until the sweep says otherwise. Commit `feat(vexel): opt-in render-and-compare refinement of the boundary graph`.

---

**Spike before building (2026-09-22).** A scratch prototype nudged every coordinate pair of the traced SVG text by
±0.1 px in x and y, re-rendered a 24 px crop at 4× (resvg, crop origin integer-aligned — a fractional viewBox origin
shifts the sampling grid against the source and the optimiser then chases the misalignment, making tilted-squares
*worse*, 0.0086 → 0.0599), and kept a nudge when the crop's mean colour error fell. Three passes. Against vector
truth: tilted-squares-512 0.0086 → 0.0045, wedge-fan-512 0.0816 → 0.0620, overlap-512 0.0260 → 0.0133, blobs-512
0.0746 → 0.0566, triangle-bar-512 0.0055 → 0.0101 (worse: its bar's corners are stubs from sliver labels, and pixel
agreement there does not mean edge agreement). 4–22 s per image in Python through the text. Go, with the plan's
constraints made hard: only interior control points and joints move, along the local normal, nodes and frame
vertices never; a move needs a clear gain over a noise floor; the crop holds only the two shapes on either side of
the arc; opt-in behind `refine`.

**As built.** `VexelParams.refine` (toggle, group Curves, "Render refinement", default off). The engine's assembly
was refactored into records (labels painted, whole-shape primitive or rings, paint) fitted once, so a candidate move
only regenerates the markup of the two shapes beside the arc from `bnd.segments` — the first cut re-ran the whole
assembly (primitive fits included) per candidate and took over ten minutes on a 256 px wedge; now the overhead is
5–30 %. `refine_render.refine(arcs, neighbours, src)`: three passes; nodes first (moved ±0.1 px in x and y with every
arc that meets them and the arm next to each end; nodes on the frame or against the outside never, nor wedge tips — pixels barely change along a 15° tip's bisector and the first run moved one 0.13 px off), then each cubic
arm and each interior joint along its normal (a joint carries its arms so the curve stays G1; circular arcs are left
exact). The crop is a 16 px window, integer-aligned, rendered by resvg at 4×, averaged back and compared over white
with the source inside a 2 px band of the arc (`cKDTree` on `arc.pts`); a move is kept when the band error falls by
more than `NOISE = 0.02` grey levels. Measured against vector truth: synthetic wedge 0.0320 → 0.0275, tilted-squares
0.0086 → 0.0065, overlap 0.0260 → 0.0182, wedge-fan 0.0816 → 0.0622, blobs 0.0746 → 0.0581, triangle-bar 0.0055 →
0.0055 (exact stays exact). The plan's `tiny-skia` Rust twin is **not built**: refinement needs a renderer, the gain
is a few hundredths of a pixel on an opt-in switch, and a second rasteriser is a large dependency for it; instead
`VexelEngine.trace` routes `refine=True` to the Python pipeline whatever backend is selected, the Rust
`VexelParams` accepts the flag, and CLAUDE.md names this as the one deliberate divergence. The diffcheck `refined`
stage therefore does not exist either. Default stays off.

### Task 12: Learned corner/smooth classifier from corpus truth

**Files:**
- Create: `backend/bench/truth.py` (sample truth corners from truth SVGs: `polygon` vertices, `rect` corners, `path` M/L joins and tangent-discontinuous C joins; circles/ellipses yield none)
- Create: `backend/tools/train_corners.py` (features + logistic regression by gradient descent in numpy; writes `backend/studi0trace/engines/vexel/corner_model.py` and `vexel-rs/src/corner_model.rs` with the weights)
- Modify: `curves.find_corners`, `topology._open_corners` (score = sigmoid(w·features + b + (60 − corner_threshold)/20); corner when > 0.5), Rust twins
- Test: `backend/tests/test_corner_model.py`

**Interfaces:**
- Produces: `corner_model.FEATURES = ("turn2", "turn4", "turn8", "run_before", "run_after", "contrast", "degree")`, `corner_model.WEIGHTS`, `corner_model.BIAS`, `corner_model.score(features: np.ndarray) -> np.ndarray`. `bench.truth.corners(truth_svg: str) -> np.ndarray` (N, 2).

- [ ] **Step 1: Write the failing tests**

```python
def test_truth_corners_of_a_polygon_are_its_vertices():
    c = corners(svg('<polygon points="10,10 90,20 80,90" fill="#000"/>'))
    assert sorted(map(tuple, c)) == [(10, 10), (80, 90), (90, 20)]

def test_model_beats_the_angle_threshold_on_held_out_items():
    f1_model, f1_rule = evaluate_on_corpus(split_seed=3)             # in tools/train_corners.py
    assert f1_model >= 0.98 and f1_model > f1_rule
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement**: features at every arc vertex from the Python pipeline (a hook in `topology.build` behind `collect=` for the trainer), labels = within 1.0 px of a truth corner; 70/30 split by item; train; export weights; wire the score into both corner detectors keeping `corner_threshold` as a bias shift so the user control still means something. Port the weights and the sigmoid.

- [ ] **Step 4: Verify** (`outline_px`, `junction_px`, `nodes_per_100px` on `logo`/`flat`; no `seam_ppm` change). Commit `feat(vexel): corners decided by a classifier trained on the corpus's vector truth`.

---

**Spike and decision: no-go for now.** `bench/truth.py` was built as the plan says (`corners(truth_svg)`: polygon
vertices, square rects' corners, a path's joins turning more than 20° with M/L/H/V/C/S/Q/A/Z absolute or relative,
none for rounded rects, circles, tangent arc joins or blurred shapes; plus `emitted_corners(svg)` following `<use>`
to its definition, and `corner_match` → precision/recall/F1 within 1 px; tests in `tests/test_bench_truth.py`).
Measured over the 16 synthetic items whose truth has corners, the current rule finds **95 % of the truth's corners**
(mean recall 0.947; the misses are polygon vertices hidden under later shapes in `overlap`, which the truth lists
and the image never shows). "Precision" as this metric defines it is 0.30, but what it counts as false are the
junction nodes where shapes meet and the traced outlines of shadow bands and of the canvas region — real corners of
the composited image that a per-shape truth does not list — so the metric cannot be a bench gate without a
visible-intersection truth, and it is not wired in. A classifier can only lower false corners on smooth curves or
raise recall on soft corners, and neither shows as a problem on this corpus (`junction_px`, `outline_px` moved by
Tasks 3–10, not by corner decisions). Revisit if a real-logo truth set shows corner misses.

### Task 13: Spikes with go/no-go

**Files:**
- Create: `docs/superpowers/specs/2026-XX-XX-vexel-spikes.md` with the three protocols and their measured results

- [x] **Sub-pixel deblurring for ≤ 128 px inputs.** Protocol: take the 128 px synthetic items; upsample 2× with (a) bilinear, (b) a small trained upscaler on the synthetic pairs (512 → 128 → 256 truth) if the UBC model is not runnable; trace the 256 px result and scale the SVG by 0.5; compare `outline_px`, `junction_px`, ΔE with the 128 px trace. Go if `outline_px` falls ≥ 30 % on the 128 px class without a `score` regression.
- [~] **Glyph fitting for wordmarks.** Protocol: on `vexel-wordmark-512.png` and two more wordmarks imported via `bench import`, detect text lines with a lightweight OCR (e.g. `tesseract` via CLI if installed), fit glyph outlines from a font candidate set with an affine solve, and measure `outline_px` on the text region versus the traced text. Go if ≤ 0.1 px and bytes fall ≥ 3×.
- [~] **Semantic layer prior.** Protocol: on `flat/overlap` and `logo/venn`, ask whether the merge stage's decisions change when regions are first grouped by a SAM-style segmentation; measure `paths` versus truth `paths` and `outline_px`. Go if truth path counts are matched on ≥ 80 % of overlap items with no `outline_px` regression.

Each spike ends with a written result and a decision; a "go" becomes its own spec and plan.

---

**As built.** `docs/superpowers/specs/2026-09-22-vexel-spikes.md`. Spike 1 run over 18 items: 2× Lanczos cuts mean
`outline_px` 38 % but only on soft/thin/low-contrast inputs and hurts every sharp one — conditional go, needs a
selection rule (its own spec). Spikes 2 and 3 cannot run here (no OCR/fonts, no segmentation model): protocols
written, decisions deferred with the reasons in the spec.

## Self-review

- Spec coverage: recommendations 1–10 of the research doc map to Tasks 1–13 (1→1,2; 2→3; 3→4; 4→5; 5→6; 6→7; 7→8; 8→11; 9→12; 10→9,13). Degradation robustness is Task 2's variants plus Task 6's JPEG/downsample tests.
- Names used across tasks: `synthetic_wedge`, `tilted_square_png`, `sample_path`, `trace` (test helpers, defined in Task 3/4 tests and reused), `line_runs`, `fit_pieces`, `Arc.tip0/tip1`, `Arc.line`, `outline_px`/`junction_px`/`line_debt_px` — consistent above.
- Every engine task carries the Rust port and the diffcheck/wordmark identity check in its verify step.
