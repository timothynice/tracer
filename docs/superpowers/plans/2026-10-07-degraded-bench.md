# Degraded Bench Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third bench set, `backend/bench/degraded/` — 13 vector-truth sources × 4 degradations (nn2x, sharpen, small, combo) = 52 items — wire it into the focus loop, and re-score the three blocked wave-lockup prototypes (D0, D1, D2) on it.

**Architecture:** One new module, `backend/bench/degraded.py`, holds the degradations as pure functions over RGBA arrays, an exact-size resvg renderer, the geometric wave-lockup stand-in, the source list and `generate()`; its output is a manifest + PNGs + truth SVGs that `bench run --corpus bench/degraded` reads like `bench/heldout`. `tools/qloop.sh` and `bench/sentinels.txt` gain the third set; `bench.gate` gains `--by-class` item counts; the evaluation runs each prototype branch from a temporary detached worktree on the shared `backend/.venv`, Python against Python.

**Tech Stack:** Python 3.12, numpy, scipy.ndimage, Pillow (`ImageFilter.UnsharpMask`), resvg_py 0.5, PyYAML (via `bench.corpus`), pytest, bash 3.2 (`tools/qloop.sh`).

## Global Constraints

Copied from the spec (`docs/superpowers/specs/2026-10-07-degraded-bench-design.md`); every task's requirements include these.

- **Set:** a third bench set `backend/bench/degraded/`, laid out like `bench/heldout/`: a `manifest.yaml` of items (id, class, png, width, height, tags, truth_svg), PNGs and truth SVGs under per-class folders, run with `python -m bench run --corpus bench/degraded`.
- **Unchanged:** the existing corpus, its baseline (`bench/baselines/vexel.json`), `preset_details.json` and the core fixtures do not change.
- **Sources (vector truth, license-clean):**

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

  The truth SVGs are copied into the set (with their licence lines for the held-out ones), so the set is self-contained.
- **wave-lockup stand-in** (synthetic, `viewBox 0 0 1208 308`, background `#fefefe`): two tapered ribbons on the right, flowing right to left, with soft-ended acute tips, drawn as cubic outlines with a 7 px white channel between them, each filled by a 4-stop `linearGradient` along the ribbon (dark: `#4a1430 → #d93b7f → #b02a5f → #4a1430`; light: `#d8913a → #e8c65c → #a0c04f → #7ac75f`); on the left, a row of 10 small shapes (3.5 px strokes, 20 px tall, in `#e0703a`, unevenly spaced, in this order: diamond, zigzag with three acute turns, double ring, chevron, triangle with a counter, plus sign, ring, short bar, three-quarter arc, square ring) sitting above a row of 5 thin shapes (6 px strokes, `#2b2b2b`: a ring, a wide shallow arc at least 2.5 times as wide as it is high, a sideways S-shaped wave, a horizontal bar with a separate dot centred below its end, a cross) and a row of 4 heavy shapes (16 px stems, `#2b2b2b`: a pair of bars of unequal height, a chevron with an acute inner notch, a bar into a round bowl, a right-angled corner opening up and to the left). No font, no letter, no word, and no palette or layout of any real mark. It is a geometric stand-in for the asset's hard parts (acute tips, a thin channel, thin strokes, heavy stems, tiny counters), never a copy of the asset.
- **Never name the company whose logo the focus asset is** — in code, comments, docs, commit messages or the Results log. The stand-in is geometric shapes only.
- **Degradations (each source × each class):**

  | class | operation | reproduces |
  |---|---|---|
  | `nn2x` | render at half the final size with resvg, nearest-neighbour 2× up; columns always pair at phase 0; rows pair at phase 0 for sources at even index in the source list and at phase 1 for odd index (render one extra native row, double, drop the first output row, so rows pair as (2k+1, 2k+2) like the asset's) | exact pixel doubling |
  | `sharpen` | render at the final size, then `ImageFilter.UnsharpMask(radius=1.5, percent=120, threshold=0)` on RGB (alpha kept) | dark rim inside, light halo outside |
  | `small` | render with the long side at 176 px | strokes under 2 px |
  | `combo` | render at half the final size, unsharp mask as `sharpen`, nearest-neighbour 2× (phase 1 rows), add ground noise: a ±1-level, σ 40 px Gaussian field on RGB of opaque pixels, seeded | the asset's whole signature |
- **Sizes:** final size long side 512 for square sources, 1208 × 308 for wave-lockup (half size 604 × 154).
- **Ids and tags:** ids `<class>/<source>-<size>` (e.g. `nn2x/venn-512`, `combo/wave-lockup-1208`); tags `degraded`, `degraded:<class>`, `source:<corpus|heldout|synthetic>`, `size:<long side>`.
- **Seeds and bytes:** everything is seeded (`random.Random(f"{seed}:{class}:{source}")`, default seed 1234) and re-encoded through Pillow like `synth.render_png`, so `python -m bench.degraded generate` writes byte-identical files on a second run.
- **Count:** 13 sources × 4 classes = 52 items.
- **Committed:** 52 PNGs (optimised, expected well under 3 MB), the copied truth SVGs and the manifest.
- **Git:** never stage `backend/bench/focus/` or `backend/bench/reports/` (both are gitignored; stage every file by name, never `git add -A` or `git add .`). `backend/uv.lock` is untracked noise: never stage it.
- **Commits** end with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Out of scope:** detectors and routed fixes (next plan); any engine change; moving the corpus baseline; held-out-style competitor runs on the new set.

### Decisions this plan takes where the spec is silent (binding for every task)

- **Non-square sources** (`logomark`, viewBox 278 × 263): the size of any render is `fit(svg, long_side)` = long side exactly, short side `floor(x + 0.5)`; nn2x and combo render at `fit(svg, long_side // 2)` and double it, so `logomark` is 512 × 484 in nn2x, sharpen and combo (sharpen's `fit(svg, 512)` is 512 × 484 too) and 176 × 167 in small. Every render stretches the viewBox onto exactly that canvas (`preserveAspectRatio="none"`, the root's own width/height dropped) so the doubling is exact. wave-lockup in `small` is 176 × 45, id `small/wave-lockup-176`.
- **Phase-1 rows** keep the geometry: the half-size render's grid is moved half a native row up and gets one extra row (`render(..., row_phase=1)`), and `nn2x(..., 1)` drops the first output row and keeps 2·h rows, so native row k covers output rows 2k−1 and 2k exactly where the truth has them (alignment checked by a test to 0.1 px).
- **combo's noise is added at native size** (σ 20 native px = 40 px at the final size) **before** the doubling: added after it, the ±1 rounding would split pairs and the item would no longer be an exact 2× replication (the spec's test says "combo is doubled"; the asset's own blotches are doubled too). Order: render half (phase 1) → unsharp → noise → nn2x.
- **Stand-in colours:** the thin and heavy shapes are `#2b2b2b`, the small shapes `#e0703a`; the shapes are abstract geometry, never read as a letter or a word.
- **Truth copies:** each class folder holds its own copy, `<class>/<source>.svg`; `LICENSES/` holds copies of the two held-out licence texts and a generated `SOURCES.md` lists every source, its origin and licence line.
- **`bench/degraded/` must never get an `__init__.py`:** it shares its name with `bench/degraded.py`, which wins the import only while the directory is not a package.

---

## File structure

| path | change | responsibility |
|---|---|---|
| `backend/bench/degraded.py` | create (Tasks 1–3) | exact-size render, the four degradations, the wave-lockup stand-in, `SOURCES`, `generate`, `write_sources`, CLI |
| `backend/tests/test_bench_degraded.py` | create (Tasks 1–4) | degradations, stand-in, byte-identical generate, row phases, sentinels resolve |
| `backend/bench/degraded/` | generated (Task 3) | `manifest.yaml`, `SOURCES.md`, `LICENSES/*.txt`, `{nn2x,sharpen,small,combo}/<source>.svg` + `<source>-<size>.png` |
| `backend/tools/qloop.sh` | modify (Task 4) | `ref [SET …]`, `sentinels` and `full` over corpus, heldout and degraded |
| `backend/bench/sentinels.txt` | modify (Task 4) | four `degraded` lines; header names the third set |
| `README.md`, `CLAUDE.md` | modify (Task 4) | the third set and why it exists |
| `backend/bench/gate.py` | modify (Task 5) | `by_class()` and `--by-class` |
| `backend/tests/test_bench_gate.py` | modify (Task 5) | `by_class` tests |
| `docs/superpowers/plans/2026-10-05-wave-lockup-focus-loop.md` | modify (Task 5) | Results log: ref-degraded means, per-prototype counts, routing recommendation |
| `backend/bench/reports/keep-2026-10-05-wave-lockup/{ref-degraded,degraded-eval/}` | local only, never staged | references, the evaluation script, its runs and gate outputs |

All commands run from `backend/` of this worktree (`/Users/TimNice/Development/tracer/.claude/worktrees/asset-trace-quality-plan-b879a2/backend`) unless a step says otherwise; the commit blocks `cd` to the worktree root first, since they stage root-relative paths.

---

### Task 1: The degradations as pure functions

**Files:**
- Create: `backend/bench/degraded.py`
- Test: `backend/tests/test_bench_degraded.py`

**Interfaces:**
- Consumes: `resvg_py.svg_to_bytes`, `PIL.ImageFilter.UnsharpMask`, `scipy.ndimage.gaussian_filter`, `bench.corpus.Item`, `bench.corpus.write_manifest` (imported now, used in Task 3).
- Produces (module `bench.degraded`):
  - `HERE: Path` (= `backend/bench`), `DEFAULT_OUT: Path` (= `HERE / "degraded"`), `CLASSES = ("nn2x", "sharpen", "small", "combo")`, `SMALL_SIDE = 176`, `UNSHARP: dict`, `NOISE_SIGMA = 40.0`
  - `viewbox(svg: str) -> tuple[float, float, float, float]`
  - `fit(svg: str, long_side: int) -> tuple[int, int]` — (width, height)
  - `render(svg: str, width: int, height: int, row_phase: int = 0) -> np.ndarray` — (height + row_phase, width, 4) uint8
  - `nn2x(native: np.ndarray, row_phase: int) -> np.ndarray`
  - `unsharp(rgba: np.ndarray) -> np.ndarray`
  - `ground_noise(rgba: np.ndarray, rng: random.Random, sigma: float) -> np.ndarray`
  - `degrade(cls: str, svg: str, long_side: int, row_phase: int, rng: random.Random) -> np.ndarray`
  - `encode_png(rgba: np.ndarray) -> bytes`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_bench_degraded.py`:

```python
"""bench.degraded: the degraded bench set (degradations, the wave-lockup stand-in, generate)."""
import random

import numpy as np
import pytest

from bench import degraded as dg

SQUARE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
          '<rect x="0" y="0" width="40" height="64" fill="#3366cc"/>'
          '<circle cx="48" cy="32" r="10" fill="#cc3333" fill-opacity="0.5"/></svg>')
WIDE = ('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="10" viewBox="0 0 100 50">'
        '<rect x="10" y="10" width="80" height="30" fill="#222"/></svg>')


def _ramp(h: int, w: int) -> np.ndarray:
    a = np.zeros((h, w, 4), np.uint8)
    a[..., 0] = (np.arange(h)[:, None] * 7 + np.arange(w)[None, :] * 3) % 256
    a[..., 1] = (np.arange(w)[None, :] * 11) % 256
    a[..., 2] = 40
    a[..., 3] = 255
    return a


def test_nn2x_phase_zero_doubles_every_pixel():
    n = _ramp(5, 4)
    out = dg.nn2x(n, 0)
    assert out.shape == (10, 8, 4)
    assert np.array_equal(out[0::2], out[1::2])
    assert np.array_equal(out[:, 0::2], out[:, 1::2])
    assert np.array_equal(out[::2, ::2], n)


def test_nn2x_phase_one_pairs_rows_from_the_second():
    n = _ramp(6, 4)  # one extra native row, as render(..., row_phase=1) gives
    out = dg.nn2x(n, 1)
    assert out.shape == (10, 8, 4)
    assert np.array_equal(out[1:9:2], out[2:10:2])  # rows (2k+1, 2k+2)
    assert not np.array_equal(out[0], out[1])
    assert np.array_equal(out[0], np.repeat(n[0], 2, axis=0))
    assert np.array_equal(out[9], np.repeat(n[5], 2, axis=0))
    assert np.array_equal(out[:, 0::2], out[:, 1::2])


def test_render_is_exact_and_phase_one_keeps_the_geometry():
    assert dg.fit(WIDE, 176) == (176, 88)
    img = dg.render(WIDE, 176, 88)
    assert img.shape == (88, 176, 4)  # the root's width/height are ignored
    half = dg.render(SQUARE, 32, 32, row_phase=1)
    assert half.shape == (33, 32, 4)
    full = dg.render(SQUARE, 64, 64)
    doubled = dg.nn2x(half, 1)
    # the circle's alpha centroid lands where the full-size render has it
    def centroid(a):
        w = a[..., 3].astype(float) * (a[..., 0] > a[..., 2])
        ys, xs = np.mgrid[:a.shape[0], :a.shape[1]]
        return (w * ys).sum() / w.sum(), (w * xs).sum() / w.sum()
    (y1, x1), (y2, x2) = centroid(doubled), centroid(full)
    assert abs(y1 - y2) < 0.1 and abs(x1 - x2) < 0.1


def test_unsharp_rims_dark_inside_and_halos_light_outside():
    a = np.full((32, 32, 4), 220, np.uint8)
    a[..., 3] = 255
    a[8:24, 8:24, :3] = 40
    a[0, 0, 3] = 7
    out = dg.unsharp(a)
    assert out[16, 8, 0] < 40 and out[16, 7, 0] > 220
    assert out[16, 16, 0] == 40 and out[16, 30, 0] == 220
    assert np.array_equal(out[..., 3], a[..., 3])


def test_small_has_the_long_side_176():
    out = dg.degrade("small", WIDE, 176, 0, random.Random(0))
    assert out.shape == (88, 176, 4)


def test_combo_is_doubled_at_phase_one_and_noisy_only_where_opaque():
    out = dg.degrade("combo", SQUARE, 128, 1, random.Random("t"))
    assert out.shape == (128, 128, 4)
    assert np.array_equal(out[1:127:2], out[2:128:2])
    assert np.array_equal(out[:, 0::2], out[:, 1::2])
    clean = dg.nn2x(dg.unsharp(dg.render(SQUARE, 64, 64, row_phase=1)), 1)
    assert np.array_equal(out[..., 3], clean[..., 3])
    diff = out[..., :3].astype(int) - clean[..., :3]
    opaque = clean[..., 3] == 255
    assert np.abs(diff).max() <= 1
    assert (diff[~opaque] == 0).all() and (diff[opaque] != 0).any()


def test_ground_noise_is_seeded():
    a = _ramp(40, 40)
    one = dg.ground_noise(a, random.Random("s"), 20.0)
    assert np.array_equal(one, dg.ground_noise(a, random.Random("s"), 20.0))
    assert not np.array_equal(one, a)


def test_unknown_class_is_refused():
    with pytest.raises(ValueError):
        dg.degrade("blur", SQUARE, 64, 0, random.Random(0))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_bench_degraded.py -q`
Expected: collection error, `ImportError: cannot import name 'degraded' from 'bench'`.

- [ ] **Step 3: Write the module**

Create `backend/bench/degraded.py` (the imports for Tasks 2–3 — `argparse`, `shutil`, `sys`, `dataclass`, `Item`, `write_manifest` — are in the header from the start):

```python
"""Degraded bench set: vector truth, damaged the way real uploads are.

Thirteen sources with vector truth (synthetic corpus templates, Studi0's own
marks, two held-out emoji and a geometric stand-in for a wave lockup) are
rendered with resvg and put through four degradations:

  nn2x     rendered at half size and doubled nearest-neighbour; columns pair at
           phase 0, rows at phase 0 (even source index) or 1 (odd index)
  sharpen  rendered at full size, then an unsharp mask: a dark rim inside every
           edge and a light halo outside it
  small    rendered with the long side at 176 px: strokes under 2 px
  combo    half size, unsharp mask, ±1-level ground noise, doubled with rows
           at phase 1: the whole signature of an upscaled, sharpened asset

`python -m bench.degraded generate` writes bench/degraded byte for byte the
same on every run; `python -m bench run --corpus bench/degraded` scores it.
The data directory bench/degraded/ must never get an __init__.py: this
module and that directory share a name, and the module wins only while the
directory is not a package.
"""
from __future__ import annotations

import argparse
import io
import math
import random
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import resvg_py
from PIL import Image, ImageFilter
from scipy import ndimage

from bench.corpus import Item, write_manifest

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "degraded"
CLASSES = ("nn2x", "sharpen", "small", "combo")
SMALL_SIDE = 176
UNSHARP = {"radius": 1.5, "percent": 120, "threshold": 0}
NOISE_SIGMA = 40.0  # px at the final size

# --- rendering --------------------------------------------------------------------------

_ROOT = re.compile(r"<svg\b[^>]*>")
_CANVAS_ATTR = re.compile(r"""\s(?:width|height|viewBox|preserveAspectRatio)\s*=\s*(?:"[^"]*"|'[^']*')""")


def viewbox(svg: str) -> tuple[float, float, float, float]:
    """The root element's viewBox as (min-x, min-y, width, height)."""
    tag = _ROOT.search(svg).group(0)
    m = re.search(r"""viewBox\s*=\s*["']([^"']*)["']""", tag)
    if m is None:
        raise ValueError("source SVG has no viewBox")
    x0, y0, vw, vh = (float(v) for v in re.split(r"[\s,]+", m.group(1).strip()))
    return x0, y0, vw, vh


def fit(svg: str, long_side: int) -> tuple[int, int]:
    """(width, height) with the long side `long_side`, the short side rounded half up."""
    _, _, vw, vh = viewbox(svg)
    k = long_side / max(vw, vh)
    return int(math.floor(vw * k + 0.5)), int(math.floor(vh * k + 0.5))


def render(svg: str, width: int, height: int, row_phase: int = 0) -> np.ndarray:
    """RGBA uint8, exactly `height` × `width`, the viewBox stretched onto the
    canvas (the root's width, height and preserveAspectRatio are replaced).

    row_phase 1 moves the pixel grid half a row up and adds a row (height + 1
    rows): `nn2x(render(svg, w, h, 1), 1)` then lands every native row on the
    two output rows it covers, with rows paired as (2k+1, 2k+2)."""
    x0, y0, vw, vh = viewbox(svg)
    rows = height
    if row_phase:
        step = vh / height
        y0, vh, rows = y0 - step / 2, vh + step, height + 1
    tag = _ROOT.search(svg)
    root = _CANVAS_ATTR.sub("", tag.group(0))[:-1].rstrip()
    root += (f' width="{width}" height="{rows}" viewBox="{x0!r} {y0!r} {vw!r} {vh!r}"'
             ' preserveAspectRatio="none">')
    png = resvg_py.svg_to_bytes(svg_string=svg[:tag.start()] + root + svg[tag.end():], skip_system_fonts=True)
    rgba = np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"), dtype=np.uint8)
    if rgba.shape[:2] != (rows, width):
        raise ValueError(f"resvg rendered {rgba.shape[1]}x{rgba.shape[0]}, not {width}x{rows}")
    return rgba


# --- degradations: pure functions over RGBA arrays -------------------------------------

def nn2x(native: np.ndarray, row_phase: int) -> np.ndarray:
    """Nearest-neighbour 2×. Columns pair at phase 0. Rows pair at phase 0, or
    at phase 1: then `native` carries one extra row (render(..., row_phase=1)),
    the first output row is dropped, rows (2k+1, 2k+2) are equal, row 0 and
    the last row stand alone, and the output is 2·(rows − 1) high."""
    up = np.repeat(np.repeat(native, 2, axis=0), 2, axis=1)
    if row_phase:
        up = up[1:1 + 2 * (native.shape[0] - 1)]
    return np.ascontiguousarray(up)


def unsharp(rgba: np.ndarray) -> np.ndarray:
    """Pillow's UnsharpMask(radius=1.5, percent=120, threshold=0) on RGB; alpha kept."""
    rgb = Image.fromarray(np.ascontiguousarray(rgba[..., :3]), "RGB").filter(ImageFilter.UnsharpMask(**UNSHARP))
    out = rgba.copy()
    out[..., :3] = np.asarray(rgb, dtype=np.uint8)
    return out


def ground_noise(rgba: np.ndarray, rng: random.Random, sigma: float) -> np.ndarray:
    """A ±1-level blotch field — white noise blurred by a Gaussian of `sigma`
    px, scaled to peak at 1 and rounded — added to R, G and B of the opaque
    pixels (alpha 255) only."""
    noise = np.random.default_rng(rng.getrandbits(64)).standard_normal(rgba.shape[:2])
    field = ndimage.gaussian_filter(noise, sigma, mode="reflect")
    peak = float(np.abs(field).max())
    delta = np.rint(field / peak).astype(np.int16) if peak > 0 else np.zeros(rgba.shape[:2], np.int16)
    out = rgba.copy()
    opaque = rgba[..., 3] == 255
    rgb = out[..., :3].astype(np.int16)
    rgb[opaque] += delta[opaque][:, None]
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    return out


def degrade(cls: str, svg: str, long_side: int, row_phase: int, rng: random.Random) -> np.ndarray:
    """`svg` through one class's degradation; `long_side` is the final long
    side (176 for small), `row_phase` the nn2x row phase (combo always uses 1)."""
    if cls == "nn2x":
        return nn2x(render(svg, *fit(svg, long_side // 2), row_phase=row_phase), row_phase)
    if cls == "sharpen":
        return unsharp(render(svg, *fit(svg, long_side)))
    if cls == "small":
        return render(svg, *fit(svg, long_side))
    if cls == "combo":
        native = unsharp(render(svg, *fit(svg, long_side // 2), row_phase=1))
        # The noise goes on at native size (σ halved) so that the doubling stays
        # exact: the asset's blotches are doubled with the rest of it.
        return nn2x(ground_noise(native, rng, NOISE_SIGMA / 2), 1)
    raise ValueError(f"unknown class {cls!r}")


def encode_png(rgba: np.ndarray) -> bytes:
    """PNG bytes through Pillow (optimize=True), like `synth.render_png`."""
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_bench_degraded.py -q`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/TimNice/Development/tracer/.claude/worktrees/asset-trace-quality-plan-b879a2
git add backend/bench/degraded.py backend/tests/test_bench_degraded.py
git commit -m "bench(degraded): exact-size render and the four degradations as pure functions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The wave-lockup stand-in

**Files:**
- Modify: `backend/bench/degraded.py` (the stand-in section after `encode_png`: `wave_lockup_svg`)
- Test: `backend/tests/test_bench_degraded.py`

The stand-in's geometry, palette, composition and shape list are the **wave-lockup stand-in** line of the Global Constraints above; the code is `wave_lockup_svg()` in `backend/bench/degraded.py` (Task 3 consumes it with `WAVE_W` = 1208). Its tests check the SVG's shape (no `<text>`/`<image>`, two 4-stop gradients), the three rows' colours and regions, a 6-7 px background gap at every column between the ribbon tips, and tips at most 2 px across.

- [ ] **Step 1: Run the stand-in tests**

Run: `.venv/bin/python -m pytest tests/test_bench_degraded.py -q -k wave_lockup`
Expected: pass.

- [ ] **Step 2: Render it and look at it**

Render `wave_lockup_svg()` at 1208 x 308 to a PNG under the scratchpad and confirm by eye that it reads as abstract shapes (no letter, no word): ribbons on the right, three rows of shapes on the left.

- [ ] **Step 3: Commit**

```bash
cd /Users/TimNice/Development/tracer/.claude/worktrees/asset-trace-quality-plan-b879a2
git add backend/bench/degraded.py backend/tests/test_bench_degraded.py
git commit -m "bench(degraded): the stand-in keeps the stresses and none of the look

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `generate`, the CLI, and the committed set

**Files:**
- Modify: `backend/bench/degraded.py` (append after `wave_lockup_svg`)
- Test: `backend/tests/test_bench_degraded.py` (append)
- Create (generated): `backend/bench/degraded/manifest.yaml`, `backend/bench/degraded/SOURCES.md`, `backend/bench/degraded/LICENSES/{Apache-2.0.txt,MIT-fluentui-emoji.txt}`, `backend/bench/degraded/{nn2x,sharpen,small,combo}/*.{svg,png}`

**Interfaces:**
- Consumes: `degrade`, `encode_png`, `SMALL_SIDE`, `CLASSES`, `HERE`, `DEFAULT_OUT` (Task 1); `WAVE_W`, `wave_lockup_svg` (Task 2); `bench.corpus.Item`, `write_manifest`, `load_corpus`.
- Produces:
  - `@dataclass(frozen=True) class Source: name: str; origin: str; path: str | None; long_side: int = 512; licence: str = OWN; licence_file: str | None = None`
  - `SOURCES: tuple[Source, ...]` — 13 entries in the spec's order (the index decides nn2x's row phase)
  - `source_svg(src: Source) -> str`
  - `write_sources(root: Path) -> Path`
  - `generate(root: Path = DEFAULT_OUT, seed: int = 1234) -> list[Item]`
  - `main(argv: list[str] | None = None) -> int`; `python -m bench.degraded generate [--out DIR] [--seed N]`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_bench_degraded.py`:

```python
def test_generate_writes_52_items_byte_identically(tmp_path):
    from bench.corpus import load_corpus

    items = dg.generate(tmp_path / "a", seed=1234)
    assert len(items) == len(dg.SOURCES) * len(dg.CLASSES) == 52
    dg.generate(tmp_path / "b", seed=1234)
    files_a = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(tmp_path / "b") for p in (tmp_path / "b").rglob("*") if p.is_file())
    assert files_a == files_b and len(files_a) == 52 + 52 + 1 + 1 + 2  # PNGs, truths, manifest, SOURCES.md, licences
    for rel in files_a:
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), rel

    loaded = load_corpus(tmp_path / "a")
    assert len(loaded) == 52
    assert all(i.truth_svg is not None and i.truth_svg.exists() and i.png.exists() for i in loaded)
    assert {i.cls for i in loaded} == set(dg.CLASSES)
    by_id = {i.id: i for i in loaded}
    combo = by_id["combo/wave-lockup-1208"]
    assert (combo.width, combo.height) == (1208, 308)
    assert combo.tags == ["degraded", "degraded:combo", "source:synthetic", "size:1208"]
    assert (by_id["small/venn-176"].width, by_id["small/venn-176"].height) == (176, 176)
    assert "source:heldout" in by_id["sharpen/u2049-512"].tags
    assert (by_id["nn2x/logomark-512"].width, by_id["nn2x/logomark-512"].height) == (512, 484)


def test_nn2x_row_phase_follows_the_source_index(tmp_path):
    from bench.raster import load_png

    dg.generate(tmp_path, seed=1234)

    def row_phase(a):
        for p in (0, 1):
            m = (a.shape[0] - p) // 2
            if np.array_equal(a[p:p + 2 * m:2], a[p + 1:p + 2 * m:2]):
                return p
        return None

    for index, src in enumerate(dg.SOURCES):
        nn = load_png(tmp_path / "nn2x" / f"{src.name}-{src.long_side}.png")
        combo = load_png(tmp_path / "combo" / f"{src.name}-{src.long_side}.png")
        assert row_phase(nn) == index % 2, src.name
        assert row_phase(combo) == 1, src.name
        assert row_phase(nn.transpose(1, 0, 2)) == 0 and row_phase(combo.transpose(1, 0, 2)) == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_bench_degraded.py -q -k "generate or row_phase"`
Expected: 2 FAIL with `AttributeError: module 'bench.degraded' has no attribute 'generate'` (or `'SOURCES'`).

- [ ] **Step 3: Append the set to the module**

Append to `backend/bench/degraded.py`:

```python
# --- the set ----------------------------------------------------------------------------

OWN = "Studi0 (this repository)"


@dataclass(frozen=True)
class Source:
    name: str
    origin: str                      # corpus | heldout | synthetic: the source:<origin> tag
    path: str | None                 # relative to backend/bench; None for the generated stand-in
    long_side: int = 512             # final long side of nn2x, sharpen and combo
    licence: str = OWN               # the SOURCES.md licence line
    licence_file: str | None = None  # file in bench/heldout/LICENSES copied beside the set


NOTO = "Apache-2.0, Google Noto Emoji (commit 06121655d0e8; see bench/heldout/SOURCES.md)"
FLUENT = "MIT, Copyright (c) Microsoft Corporation, Fluent Emoji (commit 1ffb34c752ec; see bench/heldout/SOURCES.md)"

SOURCES: tuple[Source, ...] = (
    Source("thin-mark", "corpus", "corpus/synthetic/logo/thin-mark.svg"),
    Source("wedge-fan", "corpus", "corpus/synthetic/logo/wedge-fan.svg"),
    Source("venn", "corpus", "corpus/synthetic/logo/venn.svg"),
    Source("hex-nest", "corpus", "corpus/synthetic/logo/hex-nest.svg"),
    Source("linear-4stop", "corpus", "corpus/synthetic/gradient/linear-4stop.svg"),
    Source("radial-disc", "corpus", "corpus/synthetic/gradient/radial-disc.svg"),
    Source("sticker", "corpus", "corpus/synthetic/flat/sticker.svg"),
    Source("card", "corpus", "corpus/synthetic/shadow/card.svg"),
    Source("logomark", "corpus", "corpus/real/logo/logomark.svg"),
    Source("studi0trace-mark", "corpus", "corpus/real/logo/studi0trace-mark.svg"),
    Source("u2049", "heldout", "heldout/noto/u2049.svg", licence=NOTO, licence_file="Apache-2.0.txt"),
    Source("nail-polish", "heldout", "heldout/fluent-color/nail-polish.svg", licence=FLUENT,
           licence_file="MIT-fluentui-emoji.txt"),
    Source("wave-lockup", "synthetic", None, long_side=WAVE_W),
)


def source_svg(src: Source) -> str:
    return wave_lockup_svg() if src.path is None else (HERE / src.path).read_text(encoding="utf-8")


def write_sources(root: Path) -> Path:
    """SOURCES.md: where every truth SVG comes from and the licence it is under."""
    lines = [
        "# Degraded bench set: sources and licences", "",
        "Written by `python -m bench.degraded generate` (`backend/bench/degraded.py`); do not edit by hand.",
        "Each `<class>/<source>.svg` is a byte-for-byte copy of the source below (the stand-in is",
        "generated), and each PNG beside it is a render of that SVG, damaged as its class says. The",
        "held-out sources and their renders are covered by the licence texts in `LICENSES/`.", "",
        "| source | origin | truth | licence |", "|---|---|---|---|",
    ]
    for s in SOURCES:
        where = f"`bench/{s.path}`" if s.path else "generated by `wave_lockup_svg()`"
        lines.append(f"| {s.name} | {s.origin} | {where} | {s.licence} |")
    path = root / "SOURCES.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate(root: Path = DEFAULT_OUT, seed: int = 1234) -> list[Item]:
    """Write every class × source under root and rewrite root/manifest.yaml."""
    root.mkdir(parents=True, exist_ok=True)
    items: list[Item] = []
    for cls in CLASSES:
        out_dir = root / cls
        out_dir.mkdir(parents=True, exist_ok=True)
        for index, src in enumerate(SOURCES):
            svg = source_svg(src)
            truth = out_dir / f"{src.name}.svg"
            if src.path is None:
                truth.write_text(svg, encoding="utf-8")
            else:
                shutil.copyfile(HERE / src.path, truth)
            size = SMALL_SIDE if cls == "small" else src.long_side
            row_phase = index % 2 if cls == "nn2x" else 1
            rgba = degrade(cls, svg, size, row_phase, random.Random(f"{seed}:{cls}:{src.name}"))
            png = out_dir / f"{src.name}-{size}.png"
            png.write_bytes(encode_png(rgba))
            items.append(Item(
                id=f"{cls}/{src.name}-{size}", cls=cls, png=png, width=rgba.shape[1], height=rgba.shape[0],
                truth_svg=truth, tags=["degraded", f"degraded:{cls}", f"source:{src.origin}", f"size:{size}"],
            ))
    licences = root / "LICENSES"
    licences.mkdir(exist_ok=True)
    for name in sorted({s.licence_file for s in SOURCES if s.licence_file}):
        shutil.copyfile(HERE / "heldout" / "LICENSES" / name, licences / name)
    write_sources(root)
    write_manifest(root, items)
    return items


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.degraded")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="(re)build the degraded set")
    g.add_argument("--out", default=str(DEFAULT_OUT))
    g.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args(argv)
    items = generate(Path(args.out), seed=args.seed)
    print(f"generated {len(items)} items under {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_bench_degraded.py -q`
Expected: `11 passed` (about 5 s).

- [ ] **Step 5: Generate the set and check it**

```bash
.venv/bin/python -m bench.degraded generate
du -sh bench/degraded; find bench/degraded -name '*.png' | wc -l; find bench/degraded -name '__init__.py' | wc -l
cat bench/degraded/*/*.png bench/degraded/manifest.yaml | shasum
.venv/bin/python -m bench.degraded generate >/dev/null
cat bench/degraded/*/*.png bench/degraded/manifest.yaml | shasum
```

Expected: `generated 52 items under …/backend/bench/degraded`; about `1.2M` (the PNGs are ≈ 0.9 MB, well under the spec's 3 MB); `52`; `0`; and the two `shasum` lines identical (byte-identical regeneration on the real tree). Open `bench/degraded/combo/wave-lockup-1208.png` with the Read tool: the stand-in with a faint light rim round every shape and stair-stepped edges.

- [ ] **Step 6: Confirm the existing sets did not move**

Run: `git status --porcelain bench/corpus bench/heldout bench/baselines studi0trace ../crates`
Expected: no output.

- [ ] **Step 7: Run every set's tests**

Run: `.venv/bin/python -m pytest tests/test_bench_degraded.py tests/test_bench_corpus.py -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
cd /Users/TimNice/Development/tracer/.claude/worktrees/asset-trace-quality-plan-b879a2
git add backend/bench/degraded.py backend/tests/test_bench_degraded.py backend/bench/degraded
git commit -m "bench(degraded): generate the 52-item degraded set (nn2x, sharpen, small, combo)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(`backend/bench/degraded` is a directory of generated files only; `git status` before the commit must show nothing else staged.)

---

### Task 4: Wire the set into the loop, freeze its reference, document it

**Files:**
- Modify: `backend/tools/qloop.sh` (whole file below)
- Modify: `backend/bench/sentinels.txt` (header line 1; four lines appended)
- Modify: `README.md` (Vexel Bench section, after the "Classes:" paragraph)
- Modify: `CLAUDE.md` (the `backend/bench/` layout bullet)
- Test: `backend/tests/test_bench_degraded.py` (append)

**Interfaces:**
- Consumes: `bench.degraded.HERE`, `bench.corpus.load_corpus`, the committed `bench/degraded/manifest.yaml`.
- Produces: `tools/qloop.sh ref [SET …]` (default `corpus heldout degraded`); `sentinels` and `full` gate all three sets against `$KEEP/ref-<set>/results.json`; `bench/reports/keep-2026-10-05-wave-lockup/ref-degraded/results.json` (Rust, current engine; local, never staged). Task 5 reads `ref-degraded`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_bench_degraded.py`:

```python


def test_every_sentinel_names_an_item():
    from bench.corpus import load_corpus

    lines = [ln for ln in (dg.HERE / "sentinels.txt").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert any(ln.split()[0] == "degraded" for ln in lines)
    known: dict[str, set[str]] = {}
    for ln in lines:
        name, item = ln.split()[:2]
        if name not in known:
            known[name] = {i.id for i in load_corpus(dg.HERE / name)}
        assert item in known[name], ln
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_bench_degraded.py -q -k sentinel`
Expected: FAIL on `assert any(ln.split()[0] == "degraded" …)`.

- [ ] **Step 3: Add the degraded sentinels**

In `backend/bench/sentinels.txt` replace line 1

```
# Middle loop (tools/qloop.sh sentinels). Each line: corpus|heldout <id>  # stage it guards
```

with

```
# Middle loop (tools/qloop.sh sentinels). Each line: corpus|heldout|degraded <id>  # stage it guards
```

and append:

```
degraded combo/wave-lockup-1208       # the asset's signature: doubled at phase 1, sharpened, ground noise
degraded nn2x/thin-mark-512           # exact 2× doubling of thin strokes
degraded small/venn-176               # strokes and slivers under 2 px
degraded sharpen/u2049-512            # unsharp rims and halos on glyph outlines
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_bench_degraded.py -q`
Expected: `12 passed`.

- [ ] **Step 5: Teach `qloop.sh` the third set**

Replace `backend/tools/qloop.sh` with:

```bash
#!/usr/bin/env bash
# The three speeds of the focus loop. Run from backend/.
#   tools/qloop.sh focus [bench.focus run args]   ~5 s: the one asset (default bench/focus/wave-lockup)
#   tools/qloop.sh sentinels                      ~30 s: bench/sentinels.txt, gated per item
#   tools/qloop.sh full                           minutes: corpus + held-out + degraded, gated per item
#   tools/qloop.sh ref [SET ...]                  freeze the references the gates compare against (default: all sets)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
KEEP=bench/reports/keep-2026-10-05-wave-lockup
ASSET=${ASSET:-bench/focus/wave-lockup}
SETS="corpus heldout degraded"  # bench/<set>, each gated against $KEEP/ref-<set>
export VEXEL_BACKEND=${VEXEL_BACKEND:-rust} RAYON_NUM_THREADS=${RAYON_NUM_THREADS:-2}
W=${WORKERS:-6}

# bench/sentinels.txt: the first column names the set (corpus | heldout | degraded), the second the item id
ids() { grep -v '^#' bench/sentinels.txt | awk -v c="$1" '$1==c {print $2}' | paste -sd, -; }

bench_run() {  # corpus-dir ids-or-empty out-dir
  local extra=(); [ -n "$2" ] && extra=(--ids "$2")
  # ${extra[@]+...}: an empty array is "unbound" under set -u on bash 3.2 (macOS)
  $PY -m bench run --engines vexel --corpus "$1" ${extra[@]+"${extra[@]}"} --no-media --workers "$W" --out "$3" >/dev/null
}

case "${1:-}" in
  focus) shift; exec $PY -m bench.focus run "$ASSET" "$@" ;;
  ref)
    shift
    for set in ${*:-$SETS}; do
      bench_run "bench/$set" "" "$KEEP/ref-$set"
      echo "reference: $KEEP/ref-$set/results.json"
    done ;;
  sentinels|full)
    tag=$(date +%H%M%S); rc=0
    for set in $SETS; do
      sel=""
      if [ "$1" = sentinels ]; then
        sel=$(ids $set)
        [ -n "$sel" ] || { echo "qloop: no $set ids in bench/sentinels.txt" >&2; exit 1; }
      fi
      bench_run "bench/$set" "$sel" "$KEEP/$1-$tag-$set"
      echo "== $set"; $PY -m bench.gate "$KEEP/ref-$set/results.json" "$KEEP/$1-$tag-$set/results.json" || rc=1
    done
    exit $rc ;;
  *) sed -n '2,6p' "$0"; exit 2 ;;
esac
```

Check it: `bash -n tools/qloop.sh && tools/qloop.sh; echo "exit $?"` — expected: the five usage lines (2–6) and `exit 2`. Then `grep -v '^#' bench/sentinels.txt | awk -v c=degraded '$1==c {print $2}' | paste -sd, -` — expected: `combo/wave-lockup-1208,nn2x/thin-mark-512,small/venn-176,sharpen/u2049-512`.

- [ ] **Step 6: Freeze `ref-degraded` at the current engine (Rust)**

First prove the venv's `vexel_rs` is the current engine, not a prototype (D0 was once built into it):

```bash
.venv/bin/python -c "import vexel_rs; assert not hasattr(vexel_rs, '_stage_undouble'), 'vexel_rs has D0 in it: rebuild with maturin develop --release -m vexel-rs/Cargo.toml'; print('vexel_rs ok')"
git diff --stat 87b00c1 -- studi0trace vexel-rs
```

Expected: `vexel_rs ok`, and no diff output (this branch has not touched the engine since 87b00c1). Then:

```bash
time tools/qloop.sh ref degraded
```

Expected: `reference: bench/reports/keep-2026-10-05-wave-lockup/ref-degraded/results.json`, in about a minute.

- [ ] **Step 7: Every item traced, and the per-class means**

```bash
.venv/bin/python - bench/reports/keep-2026-10-05-wave-lockup/ref-degraded/results.json <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
errs = [r["id"] for r in d["items"] if "error" in r]
print(f"{len(d['items'])} items, {len(errs)} errors {errs}")
keys = ("items", "score", "delta_e_mean", "outline_px", "junction_px", "artifact_index", "wobble_deg_100px")
print("| class | " + " | ".join(keys) + " |")
print("|---|" + "---|" * len(keys))
for cls, s in sorted(d["summary"]["vexel"].items()):
    print(f"| {cls} | " + " | ".join(f"{s[k]:.4f}" if isinstance(s.get(k), float) else str(s.get(k, "–")) for k in keys) + " |")
EOF
```

Expected: `52 items, 0 errors []` and a four-row table (combo, nn2x, sharpen, small). Save the table: Task 5 puts it in the Results log. If any item errored, stop: that is an engine failure on a degraded input, and it is reported in Task 5's Results log entry before anything else.

- [ ] **Step 8: The sentinels pass on all three sets**

Run: `time tools/qloop.sh sentinels`
Expected: `== corpus … gate ok`, `== heldout … gate ok`, `== degraded … gate ok`, exit 0 (the engine has not changed since `ref-corpus`/`ref-heldout` were frozen at 29801cf's engine, and Rust traces reproduce).

- [ ] **Step 9: Document the set**

In `README.md`, after the paragraph that ends "`bench/config.py` and are echoed into every `results.json`." (Vexel Bench section), insert a blank line and:

```markdown
Two more sets sit beside the corpus, each run with `--corpus`: `bench/heldout`
(open-source emoji never used while developing Vexel) and `bench/degraded`
(`python -m bench.degraded generate`): thirteen vector-truth sources rendered
with the damage real uploads carry — `nn2x` (an exact nearest-neighbour 2×
upscale), `sharpen` (unsharp-mask rims and halos), `small` (176 px, strokes
under 2 px) and `combo` (all of it plus ground noise). A fix aimed at a
degraded input is judged there on both sides, where it helps and where it
costs; `tools/qloop.sh full` gates all three sets per item.
```

In `CLAUDE.md` replace

```
- `backend/bench/` — Vexel Bench (`python -m bench …`); `bench/geometry.py` measures
  against vector truth, `bench/truth.py` reads the truth's corners
```

with

```
- `backend/bench/` — Vexel Bench (`python -m bench …`); `bench/geometry.py` measures
  against vector truth, `bench/truth.py` reads the truth's corners. Three sets, each
  gated per item by `tools/qloop.sh full`: `bench/corpus`, `bench/heldout` and
  `bench/degraded` (`python -m bench.degraded generate`: vector-truth sources doubled
  nearest-neighbour, sharpened, rendered at 176 px, or all three with ground noise —
  the damage real uploads carry, which the clean sets cannot show a fix's benefit on).
  `bench/degraded/` must never get an `__init__.py`: it shares its name with
  `bench/degraded.py`
```

- [ ] **Step 10: Commit**

```bash
cd /Users/TimNice/Development/tracer/.claude/worktrees/asset-trace-quality-plan-b879a2
git add backend/tools/qloop.sh backend/bench/sentinels.txt backend/tests/test_bench_degraded.py README.md CLAUDE.md
git commit -m "bench(degraded): qloop gates the third set; degraded sentinels; docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Re-score the blocked prototypes on the degraded set

**Files:**
- Modify: `backend/bench/gate.py` (add `by_class`, `--by-class`)
- Test: `backend/tests/test_bench_gate.py` (append)
- Modify: `docs/superpowers/plans/2026-10-05-wave-lockup-focus-loop.md` (append a Results log entry)
- Local only (never staged): `backend/bench/reports/keep-2026-10-05-wave-lockup/degraded-eval/{run.sh,d0-experiment.patch,<run>/,<run>.gate.txt}`

**Interfaces:**
- Consumes: `bench.gate.gate(ref, cand, engine) -> (regs, imps, missing)`, `bench.gate._by_id`; `ref-degraded/results.json` (Task 4); `bench/degraded/` (Task 3).
- Produces: `bench.gate.by_class(ref: dict, cand: dict, engine: str = "vexel") -> dict[str, dict[str, int]]` — per class `{"improved": n, "regressed": n, "same": n}` items; `python -m bench.gate REF CAND --by-class`.

The prototypes (all Python-only, all parked):

| run | branch (tip) | base | how it is switched on |
|---|---|---|---|
| `d0-lanczos` | `claude/wave-lockup-d0-wip` (87a0981) | ce70a09 | `undouble` with the exactly-once rule, as committed; with the patch below, `VEXEL_D0=lanczos` |
| `d0-bilinear` | same + the `VEXEL_D0` experiment patch below | ce70a09 | `VEXEL_D0=bilinear` |
| `d1-try9` | `claude/wave-lockup-d1-wip` (7a5014d) | 87b00c1 | defaults (`D1_POOL`, `D1_STRAND` on unless `=0`; `D1_SEEDFLOOR` off unless set) = try9 |
| `d2-late5` | `claude/wave-lockup-d2-wip` (1ae71b3) | ce70a09 | `VEXEL_NO_TIP_REGIONS=1` (the D7 tip regions off = late5, the best variant) |

`git diff --stat ce70a09..87b00c1 -- backend/` is empty, so every base runs the same engine and the same bench code as this branch's `backend/studi0trace`; one Python reference (`py-ref`, from this worktree) serves all four. How a worktree's code is used: `python -m bench` puts the **working directory** first on `sys.path`, ahead of `PYTHONPATH` and the venv, so each run is started **with its cwd at that worktree's `backend/`** (and `PYTHONPATH` set to the same directory, which the spawned `--workers` processes inherit), on this worktree's `.venv/bin/python` with `VEXEL_BACKEND=python`. A check before the runs asserts `studi0trace.__file__` lies in the intended worktree and the prototype's entry point exists, and `d0-*` must leave every `sharpen`/`small` item unchanged (D0 only fires on an exact 2× replication) — a cheap proof the right code ran.

- [ ] **Step 1: Write the failing gate tests**

Append to `backend/tests/test_bench_gate.py`:

```python
def _items(**per_item):
    return {"items": [{"id": i, "cls": i.split("/")[0], "engine": "vexel", "metrics": m} for i, m in per_item.items()]}


def test_by_class_counts_items_not_metrics():
    ref = _items(**{"nn2x/a": BASE, "nn2x/b": BASE, "nn2x/c": BASE, "small/d": BASE, "small/e": BASE})
    cand = _items(**{
        "nn2x/a": {**BASE, "score": 0.97, "artifact_index": 0.5},   # two metrics better: one improved item
        "nn2x/b": {**BASE, "score": 0.97, "slivers": 1},            # better and worse: regressed
        "nn2x/c": BASE,
        "small/d": {**BASE, "outline_px": 0.30},
        "small/e": BASE,
    })
    assert gate.by_class(ref, cand) == {
        "nn2x": {"improved": 1, "regressed": 1, "same": 1},
        "small": {"improved": 0, "regressed": 1, "same": 1},
    }


def test_by_class_counts_an_errored_item_as_regressed():
    ref = _items(**{"combo/a": BASE})
    cand = {"items": [{"id": "combo/a", "cls": "combo", "engine": "vexel", "error": "boom"}]}
    assert gate.by_class(ref, cand) == {"combo": {"improved": 0, "regressed": 1, "same": 0}}


def test_by_class_flag_prints_the_table(tmp_path, capsys):
    ref = _items(**{"nn2x/a": BASE})
    cand = _items(**{"nn2x/a": {**BASE, "score": 0.97}})
    (tmp_path / "r.json").write_text(json.dumps(ref))
    (tmp_path / "c.json").write_text(json.dumps(cand))
    assert gate.main([str(tmp_path / "r.json"), str(tmp_path / "c.json"), "--by-class"]) == 0
    out = capsys.readouterr().out
    assert "class" in out and "nn2x" in out and "        1         0     0" in out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_bench_gate.py -q -k by_class`
Expected: 3 FAIL (`AttributeError: module 'bench.gate' has no attribute 'by_class'`; the CLI test exits 2 on the unknown `--by-class`).

- [ ] **Step 3: Add `by_class` and the flag**

In `backend/bench/gate.py`, insert before `def main(`:

```python
def by_class(ref: dict, cand: dict, engine: str = "vexel") -> dict[str, dict[str, int]]:
    """Items per class that regressed (any metric, or errored/lost), improved
    (some metric and none regressed) or stayed the same, as `gate` judges them."""
    regs, imps, missing = gate(ref, cand, engine)
    bad = {ln.split(" ", 1)[0] for ln in regs + missing}
    good = {ln.split(" ", 1)[0] for ln in imps} - bad
    a, b = _by_id(ref, engine), _by_id(cand, engine)
    subset = len(b) < len(a)
    out: dict[str, dict[str, int]] = {}
    for i, rec in sorted(a.items()):
        if subset and i not in b:
            continue
        row = out.setdefault(rec.get("cls", "?"), {"improved": 0, "regressed": 0, "same": 0})
        row["regressed" if i in bad else "improved" if i in good else "same"] += 1
    return out
```

In `main`, after `ap.add_argument("--engine", default="vexel")` add:

```python
    ap.add_argument("--by-class", action="store_true", help="also count improved/regressed/same items per class")
```

and replace

```python
    bad = bool(regs or missing)
    print("\nGATE FAIL" if bad else "\ngate ok")
```

with

```python
    if args.by_class:
        print(f"\n{'class':<12}{'improved':>9}{'regressed':>10}{'same':>6}")
        for cls, row in sorted(by_class(ref_data, cand_data, args.engine).items()):
            print(f"{cls:<12}{row['improved']:>9}{row['regressed']:>10}{row['same']:>6}")
    bad = bool(regs or missing)
    print("\nGATE FAIL" if bad else "\ngate ok")
```

- [ ] **Step 4: Run the gate tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_bench_gate.py -q`
Expected: `12 passed` (9 existing + 3 new).

- [ ] **Step 5: Commit the gate change**

```bash
cd /Users/TimNice/Development/tracer/.claude/worktrees/asset-trace-quality-plan-b879a2
git add backend/bench/gate.py backend/tests/test_bench_gate.py
git commit -m "bench(gate): --by-class counts improved, regressed and unchanged items per class

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Write the D0 resampler patch**

The `VEXEL_D0` toggle of the D0 round-2 experiment was never committed (it lived in a session scratchpad); this is it, re-based onto 87a0981 and checked to apply there while this plan was written (Python only; it adds `undouble_bilinear`, `bilinear2x`, `native_of`, `double_svg`, and reads `VEXEL_D0=off|lanczos|bilinear|native`, default `lanczos`):

```bash
mkdir -p bench/reports/keep-2026-10-05-wave-lockup/degraded-eval
cat > bench/reports/keep-2026-10-05-wave-lockup/degraded-eval/d0-experiment.patch <<'PATCH'
diff --git a/backend/studi0trace/engines/vexel/engine.py b/backend/studi0trace/engines/vexel/engine.py
index 4226ec7..709314a 100644
--- a/backend/studi0trace/engines/vexel/engine.py
+++ b/backend/studi0trace/engines/vexel/engine.py
@@ -485,7 +485,20 @@ def trace_rgba(rgba: np.ndarray, p: VexelParams) -> str:
     height, width = rgba.shape[:2]
     # An exact 2× nearest-neighbour upscale is traced from its native pixels,
     # resampled smooth at its own size: its staircase is not edge evidence.
-    rgba = undouble(rgba)
+    import os as _os  # EXPERIMENT (D0 round 2, not for commit)
+    from studi0trace.engines.vexel import upsample as _up
+    _mode = _os.environ.get("VEXEL_D0", "lanczos")
+    if _mode == "off":
+        pass
+    elif _mode == "bilinear":
+        rgba = _up.undouble_bilinear(rgba)
+    elif _mode == "native" and p.upsample != "never-native":
+        _r = _up.native_of(rgba)
+        if _r is not None:
+            _n, _pr, _pc = _r
+            return _up.double_svg(trace_rgba(_n, p.model_copy(update={"upsample": "never-native"})), width, height, _pr, _pc)
+    else:
+        rgba = undouble(rgba)
     prep = prepare(rgba)
     grad = discontinuity(prep.features)
     labels0 = initial_labels(grad, prep.features, min_region=p.min_region, detail=p.detail)
diff --git a/backend/studi0trace/engines/vexel/upsample.py b/backend/studi0trace/engines/vexel/upsample.py
index 1ca746d..4a0a2e0 100644
--- a/backend/studi0trace/engines/vexel/upsample.py
+++ b/backend/studi0trace/engines/vexel/upsample.py
@@ -159,3 +159,66 @@ def halve(svg: str, width: int, height: int) -> str:
         return svg
     root = re.sub(r'viewBox="[^"]*"', f'viewBox="0 0 {width} {height}"', m.group(1), count=1)
     return f'{root}{m.group(2) or ""}<g transform="scale(0.5)">{m.group(3)}</g></svg>'
+
+
+# ---- EXPERIMENT (D0 round 2, not for commit) ----
+_B_EVEN = (0.0, 0.25, 0.75, 0.0, 0.0, 0.0)
+_B_ODD = (0.0, 0.0, 0.75, 0.25, 0.0, 0.0)
+
+
+def _xpass(a, we, wo):
+    n = a.shape[0]
+    out = np.empty((2 * n, *a.shape[1:]), dtype=np.float64)
+    idx = np.arange(n)
+    for parity, weights in ((0, we), (1, wo)):
+        acc = np.zeros_like(a)
+        for k, w in zip(range(-2, 4), weights):
+            acc = acc + w * a[np.clip(idx + k, 0, n - 1)]
+        out[parity::2] = acc
+    return out
+
+
+def bilinear2x(rgba):
+    a = rgba.astype(np.float64)
+    a[..., :3] = inpaint_transparent(a[..., :3], rgba[..., 3])
+    a = _xpass(a, _B_EVEN, _B_ODD)
+    a = _xpass(a.transpose(1, 0, 2), _B_EVEN, _B_ODD).transpose(1, 0, 2)
+    return np.clip(np.floor(a + 0.5), 0.0, 255.0).astype(np.uint8)
+
+
+def native_of(rgba):
+    """(native, pr, pc) for a once-doubled image, else None."""
+    rows = _doubling_phase(rgba)
+    cols = _doubling_phase(rgba.transpose(1, 0, 2))
+    if rows is None or cols is None:
+        return None
+    a = rgba
+    if rows:
+        a = np.concatenate([a[:1], a], axis=0)
+    if a.shape[0] % 2:
+        a = np.concatenate([a, a[-1:]], axis=0)
+    if cols:
+        a = np.concatenate([a[:, :1], a], axis=1)
+    if a.shape[1] % 2:
+        a = np.concatenate([a, a[:, -1:]], axis=1)
+    n = np.ascontiguousarray(a[::2, ::2])
+    if _doubling_phase(n) is not None and _doubling_phase(n.transpose(1, 0, 2)) is not None:
+        return None
+    return n, rows, cols
+
+
+def undouble_bilinear(rgba):
+    r = native_of(rgba)
+    if r is None:
+        return rgba
+    n, pr, pc = r
+    h, w = rgba.shape[:2]
+    return np.ascontiguousarray(bilinear2x(n)[pr:pr + h, pc:pc + w])
+
+
+def double_svg(svg, width, height, pr, pc):
+    import re
+
+    m = re.match(r"(<svg[^>]*>)(<defs>.*?</defs>)?(.*)</svg>$", svg, re.S)
+    root = re.sub(r'viewBox="[^"]*"', f'viewBox="0 0 {width} {height}"', m.group(1), count=1)
+    return f'{root}{m.group(2) or ""}<g transform="scale(2)"><g transform="translate({-pc / 2} {-pr / 2})">{m.group(3)}</g></g></svg>'
PATCH
```

- [ ] **Step 7: Write the evaluation script**

```bash
cat > bench/reports/keep-2026-10-05-wave-lockup/degraded-eval/run.sh <<'SH'
#!/usr/bin/env bash
# Re-score the blocked wave-lockup prototypes on bench/degraded, Python against Python.
# Lives in backend/bench/reports/keep-2026-10-05-wave-lockup/degraded-eval/ (never staged).
# Re-runnable: a run whose results.json exists is skipped; worktrees are reused.
set -euo pipefail
OUT=$(cd "$(dirname "$0")" && pwd)
HERE=$(cd "$OUT/../../../.." && pwd)                    # backend/ of this worktree
PY=$HERE/.venv/bin/python
KEEP=$HERE/bench/reports/keep-2026-10-05-wave-lockup
DEG=$HERE/bench/degraded
ROOT=$(dirname "$(git -C "$HERE" rev-parse --path-format=absolute --git-common-dir)")
W=${WORKERS:-6}
export VEXEL_BACKEND=python
unset VEXEL_D0 VEXEL_NO_TIP_REGIONS VEXEL_DUMP D1_POOL D1_STRAND D1_SEEDFLOOR D1_KNOWN D1_BIGTEST || true

wt() {  # name branch -> prints that worktree's backend dir
  local dir=$ROOT/.claude/worktrees/degraded-eval-$1
  [ -d "$dir" ] || git -C "$HERE" worktree add --detach "$dir" "$2" >/dev/null 2>&1
  echo "$dir/backend"
}
D0=$(wt d0 claude/wave-lockup-d0-wip)
D1=$(wt d1 claude/wave-lockup-d1-wip)
D2=$(wt d2 claude/wave-lockup-d2-wip)
# D0's resampler toggle (VEXEL_D0) was never committed; apply it once
if git -C "$D0/.." apply --check "$OUT/d0-experiment.patch" 2>/dev/null; then git -C "$D0/.." apply "$OUT/d0-experiment.patch"; fi

check() {  # backend-dir python-statements: the code that will run is the code we mean
  (cd "$1" && PYTHONPATH="$1" "$PY" -c "
import studi0trace
assert studi0trace.__file__.startswith('$1/'), studi0trace.__file__
$2
print('check ok', '$1')") || { echo "check failed in $1" >&2; exit 1; }
}
check "$HERE" "import studi0trace.engines.vexel.upsample as u; assert not hasattr(u, 'undouble')"
check "$D0" "import studi0trace.engines.vexel.upsample as u; assert hasattr(u, 'undouble') and hasattr(u, 'undouble_bilinear')"
check "$D1" "import studi0trace.engines.vexel.partition as p; assert hasattr(p, 'pool_fragments') and hasattr(p, 'seed_stranded')"
check "$D2" "import studi0trace.engines.vexel.tapers as t; assert hasattr(t, 'extend_tapers')"

run() {  # name backend-dir VAR=value ...: one bench run of the whole set from that tree
  local name=$1 dir=$2; shift 2
  if [ ! -f "$OUT/$name/results.json" ]; then
    (cd "$dir" && env "$@" PYTHONPATH="$dir" "$PY" -m bench run --engines vexel --corpus "$DEG" \
      --no-media --workers "$W" --out "$OUT/$name" >/dev/null)
  fi
  "$PY" - "$OUT/$name/results.json" "$name" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
errs = [f"{r['id']}: {r['error']}" for r in d["items"] if "error" in r]
print(f"{sys.argv[2]}: {len(d['items'])} items, {len(errs)} errors", *errs, sep="\n  ")
# A prototype's error is its result (the gate counts it as a regressed item); the reference's is not.
sys.exit(1 if len(d["items"]) != 52 or (errs and sys.argv[2] == "py-ref") else 0)
EOF
}
run py-ref "$HERE" VEXEL_BACKEND=python
run d0-lanczos "$D0" VEXEL_D0=lanczos
run d0-bilinear "$D0" VEXEL_D0=bilinear
run d1-try9 "$D1" VEXEL_BACKEND=python
run d2-late5 "$D2" VEXEL_NO_TIP_REGIONS=1

gate() {  # ref-results cand-results out-file
  (cd "$HERE" && "$PY" -m bench.gate "$1" "$2" --by-class) > "$3" || true
  tail -n 8 "$3"
}
for name in d0-lanczos d0-bilinear d1-try9 d2-late5; do
  echo; echo "=== $name vs py-ref (improved / regressed / same items)"
  gate "$OUT/py-ref/results.json" "$OUT/$name/results.json" "$OUT/$name.gate.txt"
done
echo; echo "=== Rust ref-degraded vs py-ref: the two engines' own difference, for scale"
gate "$OUT/py-ref/results.json" "$KEEP/ref-degraded/results.json" "$OUT/rust-vs-py.gate.txt"
SH
chmod +x bench/reports/keep-2026-10-05-wave-lockup/degraded-eval/run.sh
```

- [ ] **Step 8: Run it**

Run: `time bench/reports/keep-2026-10-05-wave-lockup/degraded-eval/run.sh 2>&1 | tee bench/reports/keep-2026-10-05-wave-lockup/degraded-eval/run.log`

Expected (about 5–10 minutes, five Python runs of 52 items on 6 workers): the four `check ok` lines; `py-ref: 52 items, 0 errors` and a line per prototype run (its errors, if any, listed under it); then, per prototype, a by-class table and `GATE FAIL` or `gate ok`, and the Rust-vs-Python table last. If a check fails, the script stops before any run: fix the worktree (it names the directory) and re-run — finished runs are kept and skipped. If `py-ref` has an error the script stops: the current engine failed on a degraded input under Python although Rust did not (Task 4 Step 7); record it in the Results log and do not read the prototype counts until it is understood. A prototype's error is part of its result: the gate lists it under MISSING and counts the item as regressed; quote it in the Results log.

Then confirm the sanity check by eye: in `d0-lanczos.gate.txt` and `d0-bilinear.gate.txt` the `sharpen` and `small` rows read `0 0 13` (improved, regressed, same). If they do not, D0 fired where nothing is doubled: the run did not use the intended code; stop and find out why before reading any number.

- [ ] **Step 9: Remove the worktrees**

```bash
for n in d0 d1 d2; do git worktree remove --force "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/.claude/worktrees/degraded-eval-$n"; done
git worktree list | grep -c degraded-eval
```

Expected: `0`. The branches are untouched (`git log -1 --format=%h claude/wave-lockup-d0-wip` is still `87a0981`, d1 `7a5014d`, d2 `1ae71b3`).

- [ ] **Step 10: Write the Results log entry**

Append to `docs/superpowers/plans/2026-10-05-wave-lockup-focus-loop.md` (after the last `###` entry of the Results log) an entry built from the outputs above — numbers copied from Task 4 Step 7 and `degraded-eval/*.gate.txt`, nothing estimated:

```markdown
### 2026-10-07 — Degraded bench set: the blocked fixes re-scored

Set: `bench/degraded` (spec `2026-10-07-degraded-bench-design.md`, plan `2026-10-07-degraded-bench.md`):
13 vector-truth sources × nn2x / sharpen / small / combo = 52 items. Reference `ref-degraded`
(Rust, engine = 87b00c1's) under `bench/reports/keep-2026-10-05-wave-lockup/`; every item traced
without error (or: the items that errored, and the error).

<the per-class means table from Task 4 Step 7>

Prototypes re-scored Python against Python (`degraded-eval/py-ref`, this branch's engine with
`VEXEL_BACKEND=python`); each from a detached worktree of its WIP branch, the D0 toggle from the
re-based experiment patch. Items per class, improved / regressed / same (`bench.gate --by-class`):

| prototype | targets | nn2x | sharpen | small | combo | degraded total | clean sets (already recorded) |
|---|---|---|---|---|---|---|---|
| D0 lanczos (87a0981) | nn2x, combo | i/r/s | i/r/s | i/r/s | i/r/s | i/r/s | 0 of 224 items changed (round-2 survey) |
| D0 bilinear (+patch) | nn2x, combo | … | … | … | … | … | not run on the clean sets (focus only) |
| D1 try9 (7a5014d) | small, combo | … | … | … | … | … | 26 regressions in 8 corpus items, 44 in ~17 held-out; 10 + 13 improved |
| D2 late5 (1ae71b3, tip regions off) | all | … | … | … | … | … | 6 regressions in 5 items, 7 improved |
| (Rust vs Python, same engine) | — | … | … | … | … | … | — (scale of the engines' own difference) |

For each prototype, the regressed lines that matter (item, metric, before → after) and where its
wins sit: <two to five lines per prototype from its `.gate.txt`>.

**Routing recommendation.** <one paragraph per fix, using the rule below>.
```

Fill every `…` and `<…>` from the outputs (the template's placeholders are for the measured numbers only — do not leave any in the file). Decide each prototype's recommendation by this rule, and say which clause decided it:

- **Candidate for routing** when, in its target classes taken together, improved items ≥ 2 × regressed items, and at least two thirds of all its improved degraded items are in its target classes. Name the detector it would need:
  - D0: none new — the exactly-once doubling test it already carries (`native_of`), plus the round-2 report's unimplemented second clause (the native image has at least one partial-coverage pixel), which clears the two crisp-art test fixtures; route bilinear or Lanczos by which variant's target-class counts are better.
  - D1: whichever split its wins follow — in `small` only: the small-input rule's measure (`upsample.thinnest_region` < 2.2 px on a direct trace); in `combo`/`nn2x`: D0's doubling detector (thin strokes at native resolution); in both: the two together.
  - D2: tips are cut short wherever an edge is soft or stair-stepped, so if its wins are spread over every class, there is no cheap detector — say so, and say the rule must instead be made safe on clean inputs (its clean-set regressions are the list to fix).
- **Not a candidate** otherwise; say whether it is because it does not help degraded inputs either (few improved anywhere) or because its costs on degraded inputs are as broad as on clean ones.

End the entry with one line: the order the next plan (detectors and routed fixes) should take the candidates in, most improved target-class items first.

- [ ] **Step 11: Commit the Results log**

```bash
cd /Users/TimNice/Development/tracer/.claude/worktrees/asset-trace-quality-plan-b879a2
git add docs/superpowers/plans/2026-10-05-wave-lockup-focus-loop.md
git commit -m "plan: degraded-set results for the blocked wave-lockup fixes; routing recommendation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git status --porcelain
```

Expected: `git status` shows nothing staged and nothing modified under `backend/bench/focus/` or `backend/bench/reports/` (they are ignored); at most `?? backend/uv.lock`.

- [ ] **Step 12: Full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (nothing in the engine changed; the new tests are 12 in `test_bench_degraded.py` and 3 in `test_bench_gate.py`).
