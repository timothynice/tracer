# Wave-Lockup Focus Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A loop that tells us in seconds whether a change makes one hard real-world asset (a wave mark + wordmark lockup) trace closer to its source at 100 %, and in a couple of minutes whether it costs anything on the 104-item corpus or the 120-item held-out set.

**Architecture:** Three speeds. The **inner loop** (`bench.focus`, ~5 s) traces the one asset, scores it whole and per named region with a view-blurred ΔE that approximates what a person sees at 100 %, the artifact scorecard's defects counted per region, and writes a contact sheet plus a flip page; every run is diffed against the last run and the pinned baseline. The **middle loop** (`tools/qloop.sh sentinels`, ~30 s) runs ~12 corpus/held-out items that exercise the same stages, through a new per-item gate (`bench.gate`). The **outer loop** (`tools/qloop.sh full`, a few minutes) runs the whole corpus and held-out set through the same gate, then pytest, `cargo test` and `diffcheck` for parity. Fixes are prototyped in Python (no rebuild), ported to Rust, and only Rust results gate.

**Tech Stack:** Python 3.12 (`backend/.venv`, uv), numpy/scipy/skimage/Pillow, resvg via `studi0trace.imaging.quality`, Rust `vexel_rs` + `studi0trace_core` via maturin, pytest, cargo.

## Global Constraints

- **The repository is public** (`timothynice/tracer`, MIT). The source asset is a third-party company logo: it, its traces, sheets and crops **never enter git**. They live in `backend/bench/focus/wave-lockup/`, which is gitignored. Tracked files call it `wave-lockup`, never by the company's name.
- Any tracing quality change is judged on the bench against references taken **before** the change (CLAUDE.md). Nothing is called an improvement on the focus asset alone.
- Python and Rust change together (CLAUDE.md "Change one and you change both"); `tools/diffcheck.py` proves it. A change to what the engine writes means re-exporting the core fixtures (`--only scorecard,api,auto`, plus `svg,render,drawing,holes,geometry`) and `cargo test --workspace --release`.
- Thresholds are chosen from a survey over every corpus and held-out item, never from the focus asset alone, and sit off multiples of 0.5 px (lattice ties).
- Every new pipeline tie gets a named tie and one rule in both engines (CLAUDE.md parity bullet).
- Never rebuild `vexel_rs` while a Rust-backed bench run is in progress.
- Durable records go under `backend/bench/reports/keep-2026-10-05-wave-lockup/` (gitignored, survives the scratchpad pruning), never only in the scratchpad.
- Agents commit WIP early and append to the Results log at the end of this file, so a continuation can resume after a usage-limit stop.

---

## Context: the asset and what it stresses

`source.png`: 1208 × 308, opaque RGBA, 10 682 distinct colours. Read at 3× before planning:

| Region (source px, x0 y0 x1 y1) | What is there | Hypothesised failure (verify in Task 5) |
|---|---|---|
| `backdrop` whole frame minus ink | off-white 254–255 with faint warm/cool blotches (generator noise) | spurious backdrop regions; halo rings read as slivers |
| `wave` 0 60 440 250 | two tapered ribbons, multi-stop gradients with specular streaks (navy→blue→navy, blue→cyan→green) | gradient banding at 1×, ribbon split into pieces, wrong stop placement |
| `gap` 120 110 400 200 | white channel 6–10 px wide between the ribbons, tapering to points at both ends | channel pinched/broken, backdrop not continuous, tips blunt |
| `tips` the four ribbon ends | acute soft wedges | truncated wedge, nub, hairpin |
| `light` 440 30 760 190 | thin geometric sans, ~6 px stems, soft edges, i-dot, g/h counters | stroke-vs-fill misclassification, stem width drift, wobble on round bowls |
| `river` 760 30 1180 190 | bold sans, straight stems, R bowl, v diagonals | flared/rounded junctions, bowed straights |
| `caps` 440 240 1180 300 | tracked light-blue caps, ~20 px cap height, ~3.5 px stems, O/C/A/B counters | sub-pixel strokes, closed counters, colour drift toward white at the rim |

The regions above are the starting `focus.yaml`; Task 4 confirms them on the baseline sheet.

## Definition of done

0. *(Amended at the Task 4 checkpoint, 2026-10-05.)* Tim: match **shapes and true colours**; halos, sharpening rims and streak noise are artifacts. The numeric bar is set with Tim **after the first fixes**, on Task 4b's `edge_off_frac` / `fill_de` measures; item 1 below is superseded until then.
1. **Focus asset, per region** (Rust, the preset Auto picks, render at 1×): `visible_frac` ≤ 1 % and `de_p99` ≤ 5.0 (view-blurred CIEDE2000, defined in Task 1: roughly, no stretch of outline more than 0.2 px from the source and no fill visibly off); whole image `pinholes` = 0 and `slivers` = 0. These are provisional and are re-confirmed with Tim at the Task 4 checkpoint once the baseline numbers exist.
2. **Tim signs off on `flip.html` at 1×**: flipping source and trace shows no defect he can point to.
3. **No regression**: `bench.gate` passes on corpus and held-out against the Task 4 references (tolerances in Task 2), or every flagged item is listed in the Results log with a reason Tim accepted.
4. Parity: default `diffcheck` stages pass; `pytest`, `cargo test --workspace --release` pass.
5. Task 7's license-clean synthetic stand-in is in the corpus, so the next change cannot quietly undo this work.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/bench/focus.py` (create) | inner loop: trace one asset, view-ΔE per region, defects per region, sheet, flip page, history diff, `pin` |
| `backend/bench/gate.py` (create) | per-item regression gate between two `results.json` files |
| `backend/bench/sentinels.txt` (create) | the middle loop's item ids, one per line, `#` comments |
| `backend/tools/qloop.sh` (create) | `focus` / `sentinels` / `full` / `ref` entry points with the right env and paths |
| `backend/tests/test_bench_focus.py` (create) | focus metrics and region accounting on a synthetic image |
| `backend/tests/test_bench_gate.py` (create) | gate tolerances and exit code |
| `.gitignore` (modify) | ignore `backend/bench/focus/*/` |
| `backend/bench/synth.py` (modify, Task 7) | the `wave-lockup` synthetic stand-in with vector truth |

---

### Task 0: Worktree environment and the asset in place

**Files:**
- Modify: `.gitignore`
- Create (untracked): `backend/bench/focus/wave-lockup/source.png`, `backend/bench/focus/wave-lockup/focus.yaml`

- [ ] **Step 1: Build the venv and both extensions**

```bash
cd backend && uv sync && uv pip install maturin
.venv/bin/python -m maturin develop --release -m vexel-rs/Cargo.toml
VIRTUAL_ENV=$PWD/.venv .venv/bin/python -m maturin develop --release -m ../crates/studi0trace-core/Cargo.toml --features python
.venv/bin/python -c "from studi0trace.engines.vexel.engine import backend; print(backend())"
```
Expected: last line prints `rust`.

- [ ] **Step 2: Ignore focus assets**

Append to `.gitignore`:
```
# Focus-loop assets: third-party images and their traces stay local (bench/focus.py)
backend/bench/focus/*/
```

- [ ] **Step 3: Place the asset**

```bash
mkdir -p backend/bench/focus/wave-lockup
cp ~/Desktop/testLogo.png backend/bench/focus/wave-lockup/source.png
shasum -a 256 backend/bench/focus/wave-lockup/source.png
```
Expected: `8f786caf62a65b110b2365d6ca2d1f8f442def924261d985939cd3a34b089333` (Tim's original, 213 654 bytes; pixel-identical to the copy the plan was written from).

Write `backend/bench/focus/wave-lockup/focus.yaml`:
```yaml
# Regions in source pixels: [x0, y0, x1, y1]
regions:
  wave:  [0, 60, 440, 250]
  gap:   [120, 110, 400, 200]
  light: [440, 30, 760, 190]
  river: [760, 30, 1180, 190]
  caps:  [440, 240, 1180, 300]
preset: balanced          # replaced in Task 4 by the preset Auto picks
params: {}
```

- [ ] **Step 4: Verify nothing leaks and commit**

```bash
git status --porcelain   # must show only .gitignore
git add .gitignore && git commit -m "bench: focus-loop assets stay out of git"
```

---

### Task 1: Focus metrics — view-ΔE and defects per region

**Files:**
- Create: `backend/bench/focus.py`
- Test: `backend/tests/test_bench_focus.py`

**Interfaces:**
- Produces:
  - `VIEW_SIGMA: float = 0.8`, `VISIBLE_DE: float = 5.0`
  - `view_de(src_rgb: np.ndarray, out_rgb: np.ndarray, sigma: float = VIEW_SIGMA) -> np.ndarray` — (H, W) float64
  - `region_stats(de: np.ndarray, box: tuple[int, int, int, int]) -> dict[str, float]` — keys `de_mean`, `de_p99`, `visible_frac`
  - `region_defects(card: dict, box) -> dict[str, float]` — keys `pinholes`, `slivers`, `inflections`, `wobble`; `card` is `scorecard(..., detail=True)`
  - `assess(svg: str, src_rgba: np.ndarray, regions: dict[str, list[int]], elapsed_ms: float) -> dict` — `{"whole": {...all_metrics...}, "regions": {name: {**region_stats, **region_defects}}}`

Why blur before ΔE: at 100 % a trace whose edge sits a tenth of a pixel off the source's anti-aliasing differs by tens of ΔE in one pixel column and is invisible; a Gaussian on both images removes that and keeps a missing counter, a band or a wobble. The constants were calibrated while planning on a 20 px orange disc against copies of itself grown by d px: with σ 0.8 and ΔE > 5 the visible fraction is 0 at d = 0.15, 0.3 % at 0.2, 2.8 % at 0.25 and 8 % at 0.5 (σ 0.6 / ΔE 3 flagged 1.9 % at d = 0.1). So an outline within ~0.2 px of the source is invisible to this metric and a quarter pixel is not — about the engine's own outline accuracy (`outline_px` ≈ 0.2 on the logo class). Edge-heavy regions (the caps) carry more outline per pixel, which is why the done-thresholds are confirmed on the baseline at Task 4.

- [ ] **Step 1: Write the failing tests**

```python
"""bench.focus: what the inner loop measures."""
import numpy as np

from bench import focus


def _disc(r: float, w: int = 64) -> np.ndarray:
    yy, xx = np.mgrid[0:w, 0:w] + 0.5
    cover = np.clip(r - np.hypot(xx - w / 2, yy - w / 2) + 0.5, 0, 1)
    rgb = 255 - (cover[..., None] * np.array([235, 155, 55])).astype(np.uint8)
    return rgb.astype(np.uint8)


def test_subpixel_edge_shift_is_not_visible():
    de = focus.view_de(_disc(20.0), _disc(20.15))
    assert focus.region_stats(de, (0, 0, 64, 64))["visible_frac"] < 0.005


def test_half_pixel_edge_shift_is_visible():
    de = focus.view_de(_disc(20.0), _disc(20.5))
    assert focus.region_stats(de, (0, 0, 64, 64))["visible_frac"] > 0.03


def test_missing_shape_is_visible_only_in_its_region():
    src = _disc(20.0)
    out = np.full_like(src, 255)
    out[:, 32:] = src[:, 32:]          # left half of the disc gone
    de = focus.view_de(src, out)
    left = focus.region_stats(de, (0, 0, 32, 64))
    right = focus.region_stats(de, (34, 0, 64, 64))
    assert left["visible_frac"] > 0.2
    assert right["visible_frac"] == 0.0


def test_region_defects_count_only_inside_the_box():
    card = {
        "_clusters": [{"x": 5, "y": 5, "pinhole": True}, {"x": 50, "y": 5, "pinhole": True},
                      {"x": 6, "y": 6, "pinhole": False}],
        "_slivers_at": [(10, 10, 3.0, 0.4)],
        "_flips_at": [(70, 70)],
        "_wobble_at": [(4, 4, 2.5), (5, 5, 1.0)],
    }
    got = focus.region_defects(card, (0, 0, 32, 32))
    assert got == {"pinholes": 1, "slivers": 1, "inflections": 0, "wobble": 3.5}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_bench_focus.py -v`
Expected: FAIL, `ImportError: cannot import name 'focus'`.

- [ ] **Step 3: Implement the metrics half of `bench/focus.py`**

```python
"""Focus loop: one asset, traced and judged as a person sees it at 100 %, in seconds.

    .venv/bin/python -m bench.focus run bench/focus/wave-lockup
    .venv/bin/python -m bench.focus run bench/focus/wave-lockup --backend python --params '{"refine": true}'
    .venv/bin/python -m bench.focus run bench/focus/wave-lockup --auto
    .venv/bin/python -m bench.focus pin bench/focus/wave-lockup bench/focus/wave-lockup/runs/<run>

An asset directory holds `source.png` and `focus.yaml` (named regions in source
pixels, the preset and params every run uses). Asset directories are gitignored:
a focus asset may be someone else's artwork. Each run writes
runs/<ts>-<label>/{trace.svg, focus.json, sheet.png, flip.html} and prints its
deltas against the previous run and the pinned baseline (baseline.json).
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter

from bench import metrics
from bench.artifacts import scorecard
from bench.raster import rasterize, to_rgb_on_white
from studi0trace.imaging.quality import delta_e_map

VIEW_SIGMA = 0.8  # px: what viewing at 100 % blurs away (edge offsets up to ~0.2 px)
VISIBLE_DE = 5.0  # CIEDE2000 above which flipping between source and trace shows


def view_de(src_rgb: np.ndarray, out_rgb: np.ndarray, sigma: float = VIEW_SIGMA) -> np.ndarray:
    """CIEDE2000 per pixel after a Gaussian of `sigma` on both images."""
    def blur(a: np.ndarray) -> np.ndarray:
        return gaussian_filter(a.astype(np.float64), sigma=(sigma, sigma, 0))
    return delta_e_map(blur(src_rgb), blur(out_rgb))


def _inside(x: float, y: float, box) -> bool:
    x0, y0, x1, y1 = box
    return x0 <= x < x1 and y0 <= y < y1


def region_stats(de: np.ndarray, box) -> dict[str, float]:
    x0, y0, x1, y1 = box
    d = de[y0:y1, x0:x1]
    return {"de_mean": float(d.mean()), "de_p99": float(np.percentile(d, 99)),
            "visible_frac": float((d > VISIBLE_DE).mean())}


def region_defects(card: dict, box) -> dict[str, float]:
    """The scorecard's located defects that fall inside `box`."""
    return {
        "pinholes": sum(1 for c in card["_clusters"] if c["pinhole"] and _inside(c["x"], c["y"], box)),
        "slivers": sum(1 for s in card["_slivers_at"] if _inside(s[0], s[1], box)),
        "inflections": sum(1 for x, y in card["_flips_at"] if _inside(x, y, box)),
        "wobble": float(sum(v for x, y, v in card["_wobble_at"] if _inside(x, y, box))),
    }


def assess(svg: str, src_rgba: np.ndarray, regions: dict[str, list[int]], elapsed_ms: float) -> dict:
    h, w = src_rgba.shape[:2]
    out_rgba = rasterize(svg, w, h)
    whole = metrics.all_metrics(src_rgba, out_rgba, svg, elapsed_ms)
    de = view_de(to_rgb_on_white(src_rgba), to_rgb_on_white(out_rgba))
    whole.update(region_stats(de, (0, 0, w, h)))
    card = scorecard(svg, src_rgba, detail=True)
    per = {name: {**region_stats(de, box), **region_defects(card, box)} for name, box in regions.items()}
    return {"whole": whole, "regions": per, "_de": de, "_out": out_rgba}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_bench_focus.py -v`
Expected: 4 passed. If either edge-shift test fails, report the measured `visible_frac` and stop: the constants are wrong, not the test.

- [ ] **Step 5: Commit**

```bash
git add backend/bench/focus.py backend/tests/test_bench_focus.py
git commit -m "bench.focus: view-blurred ΔE and scorecard defects per named region"
```

---

### Task 2: Per-item regression gate

**Files:**
- Create: `backend/bench/gate.py`
- Test: `backend/tests/test_bench_gate.py`

**Interfaces:**
- Produces:
  - `TOLERANCES: dict[str, tuple[int, float]]` — metric → (direction, allowed move); direction `+1` higher is better, `-1` lower is better
  - `gate(ref: dict, cand: dict, engine: str = "vexel") -> tuple[list[str], list[str], list[str]]` — (regressions, improvements, missing), each a printable line per item/metric
  - CLI: `python -m bench.gate REF_results.json CAND_results.json` → exit 1 when any regression or an item errored/missing

Why: `bench compare` gates on class means; one item can get much worse while its class mean holds (the memory notes this cost a latent bug once). The gate looks at every item. Rust is deterministic, so unchanged code gives identical metrics and these tolerances only absorb real but negligible moves.

- [ ] **Step 1: Write the failing tests**

```python
"""bench.gate: per-item regressions between two runs."""
import json

from bench import gate


def _run(**per_item):
    return {"items": [{"id": i, "cls": "logo", "engine": "vexel", "metrics": m} for i, m in per_item.items()]}


BASE = {"score": 0.95, "delta_e_mean": 0.30, "artifact_index": 1.0, "pinholes": 0, "slivers": 0,
        "thin_strokes": 0, "seam_ppm": 1000.0, "outline_px": 0.20, "wobble_deg_100px": 10.0}


def test_identical_runs_pass():
    regs, imps, missing = gate.gate(_run(a=BASE), _run(a=BASE))
    assert (regs, imps, missing) == ([], [], [])


def test_one_item_regression_is_caught_even_if_another_improves():
    worse = {**BASE, "score": 0.94}
    better = {**BASE, "score": 0.97}
    regs, imps, _ = gate.gate(_run(a=BASE, b=BASE), _run(a=worse, b=better))
    assert len(regs) == 1 and regs[0].startswith("a ") and "score" in regs[0]
    assert len(imps) == 1 and imps[0].startswith("b ")


def test_counts_regress_on_any_increase():
    regs, _, _ = gate.gate(_run(a=BASE), _run(a={**BASE, "slivers": 1}))
    assert any("slivers" in r for r in regs)


def test_missing_truth_metric_is_skipped_not_failed():
    no_truth = {**BASE, "outline_px": None}
    regs, _, _ = gate.gate(_run(a=no_truth), _run(a=no_truth))
    assert regs == []


def test_errored_item_is_missing(tmp_path):
    ref = _run(a=BASE)
    cand = {"items": [{"id": "a", "cls": "logo", "engine": "vexel", "error": "boom"}]}
    (tmp_path / "r.json").write_text(json.dumps(ref))
    (tmp_path / "c.json").write_text(json.dumps(cand))
    assert gate.main([str(tmp_path / "r.json"), str(tmp_path / "c.json")]) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_bench_gate.py -v`
Expected: FAIL, `ImportError: cannot import name 'gate'`.

- [ ] **Step 3: Implement `bench/gate.py`**

```python
"""Per-item regression gate: `python -m bench.gate REF/results.json CAND/results.json`.

`bench compare` judges class means, where one item can get much worse while
its class holds. This looks at every item and every metric below, and exits 1
on any regression, or on an item the candidate errored on or lost.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# metric: (direction, allowed move). +1 higher is better, -1 lower is better.
TOLERANCES: dict[str, tuple[int, float]] = {
    "score": (+1, 0.003),
    "delta_e_mean": (-1, 0.03),
    "artifact_index": (-1, 0.25),
    "pinholes": (-1, 0.0),
    "slivers": (-1, 0.0),
    "thin_strokes": (-1, 0.0),
    "seam_ppm": (-1, 50.0),
    "outline_px": (-1, 0.01),
    "junction_px": (-1, 0.02),
    "wobble_deg_100px": (-1, 2.0),
}


def _by_id(run: dict, engine: str) -> dict[str, dict]:
    return {r["id"]: r for r in run["items"] if r["engine"] == engine}


def gate(ref: dict, cand: dict, engine: str = "vexel") -> tuple[list[str], list[str], list[str]]:
    a, b = _by_id(ref, engine), _by_id(cand, engine)
    regs: list[str] = []
    imps: list[str] = []
    missing = [f"{i} missing or errored" for i in sorted(a) if i not in b or "error" in b[i]]
    for i in sorted(set(a) & set(b)):
        if "error" in a[i] or "error" in b[i]:
            continue
        ma, mb = a[i]["metrics"], b[i]["metrics"]
        for k, (sign, tol) in TOLERANCES.items():
            va, vb = ma.get(k), mb.get(k)
            if va is None or vb is None:
                continue
            gain = sign * (vb - va)
            line = f"{i} {k}: {va:.4f} → {vb:.4f}"
            if gain < -tol:
                regs.append(line)
            elif gain > tol:
                imps.append(line)
    return regs, imps, missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.gate")
    ap.add_argument("ref")
    ap.add_argument("cand")
    ap.add_argument("--engine", default="vexel")
    args = ap.parse_args(argv)
    regs, imps, missing = gate(json.loads(Path(args.ref).read_text()), json.loads(Path(args.cand).read_text()),
                               args.engine)
    for title, lines in (("REGRESSED", regs), ("MISSING", missing), ("improved", imps)):
        if lines:
            print(f"\n{title} ({len(lines)})")
            print("\n".join(f"  {ln}" for ln in lines))
    bad = bool(regs or missing)
    print("\nGATE FAIL" if bad else "\ngate ok")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_bench_gate.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/bench/gate.py backend/tests/test_bench_gate.py
git commit -m "bench.gate: per-item regression gate between two runs"
```

---

### Task 3: Focus runner — sheet, flip page, history; sentinels; `qloop.sh`

**Files:**
- Modify: `backend/bench/focus.py` (append the runner and CLI)
- Create: `backend/bench/sentinels.txt`, `backend/tools/qloop.sh`
- Test: `backend/tests/test_bench_focus.py` (append)

**Interfaces:**
- Consumes: `assess`, `view_de` (Task 1); `bench.gate.main` (Task 2); `bench.auto.auto_trace(png_bytes) -> (pick, reason, [(preset, svg, scores, ms)])`; `studi0trace.engines.presets.all_presets() -> list[Preset]` (`.id`, `.params`)
- Produces:
  - `run(asset: Path, backend: str = "rust", params: dict | None = None, label: str = "run", auto: bool = False) -> Path` — the run directory
  - `diff(prev: dict, now: dict) -> list[str]` — printable delta lines
  - `pin(asset: Path, run_dir: Path) -> None` — copies `focus.json` to `asset/baseline.json`
  - `qloop.sh focus [focus args]`, `qloop.sh sentinels`, `qloop.sh full`, `qloop.sh ref` (Task 4 uses `ref`)

- [ ] **Step 1: Write the failing test** (append to `tests/test_bench_focus.py`)

```python
def test_run_writes_a_complete_run_dir(tmp_path):
    from PIL import Image

    asset = tmp_path / "disc"
    asset.mkdir()
    rgb = _disc(20.0)
    Image.fromarray(np.dstack([rgb, np.full(rgb.shape[:2], 255, np.uint8)]), "RGBA").save(asset / "source.png")
    (asset / "focus.yaml").write_text("regions:\n  left: [0, 0, 32, 64]\npreset: balanced\nparams: {}\n")
    run_dir = focus.run(asset, backend="rust", label="t")
    for name in ("trace.svg", "focus.json", "sheet.png", "flip.html"):
        assert (run_dir / name).exists(), name
    import json
    data = json.loads((run_dir / "focus.json").read_text())
    assert set(data["regions"]) == {"left"} and data["whole"]["delta_e_mean"] < 2.0
    focus.pin(asset, run_dir)
    assert (asset / "baseline.json").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_bench_focus.py::test_run_writes_a_complete_run_dir -v`
Expected: FAIL, `AttributeError: module 'bench.focus' has no attribute 'run'`.

- [ ] **Step 3: Append the runner and CLI to `bench/focus.py`**

Add to the imports at the top: `argparse, json, os, shutil, sys, time`, `from datetime import datetime`, `from pathlib import Path`, `import yaml`, `from PIL import Image, ImageDraw`, `from bench.raster import load_png`, `from bench.runner import _heatmap`.

```python
ZOOM = 4  # crop magnification on the sheet
# focus.json keys diffed between runs: (key, direction) with +1 higher is better
WHOLE_KEYS = (("score", +1), ("delta_e_mean", -1), ("visible_frac", -1), ("de_p99", -1), ("artifact_index", -1),
              ("pinholes", -1), ("slivers", -1), ("wobble_deg_100px", -1), ("paths", -1), ("bytes", -1),
              ("elapsed_ms", -1))
REGION_KEYS = (("visible_frac", -1), ("de_p99", -1), ("de_mean", -1), ("pinholes", -1), ("slivers", -1),
               ("inflections", -1), ("wobble", -1))


def _trace(asset: Path, cfg: dict, backend: str, params: dict | None, auto: bool) -> tuple[str, float, str]:
    """(svg, trace ms, what ran) with VEXEL_BACKEND set for this call only."""
    from studi0trace.engines import registry
    from studi0trace.engines.presets import all_presets
    from studi0trace.imaging.intake import load_upload

    old = os.environ.get("VEXEL_BACKEND")
    os.environ["VEXEL_BACKEND"] = backend
    try:
        data = (asset / "source.png").read_bytes()
        if auto:
            from bench.auto import auto_trace

            pick, reason, rows = auto_trace(data)
            preset, svg, _s, ms = next(r for r in rows if r[0].id == pick)
            return svg, ms, f"auto→{pick} ({reason})"
        registry.load_builtin()
        engine = registry.get("vexel")
        base = next(p.params for p in all_presets() if p.id == cfg.get("preset", "balanced"))
        merged = {**base, **cfg.get("params", {}), **(params or {})}
        image = load_upload(data, max_bytes=1 << 30, max_pixels=1 << 30)
        t = time.perf_counter()
        svg = engine.trace(image, engine.Params.model_validate(merged)).svg
        return svg, 1000 * (time.perf_counter() - t), f"{cfg.get('preset', 'balanced')} {merged}"
    finally:
        if old is None:
            os.environ.pop("VEXEL_BACKEND", None)
        else:
            os.environ["VEXEL_BACKEND"] = old


def _sheet(src: np.ndarray, out: np.ndarray, de: np.ndarray, regions: dict, path: Path) -> None:
    """Rows: whole source / trace / view-ΔE at 1×, then each region source | trace | ΔE at ZOOM×."""
    heat = _heatmap(de, scale=10.0)
    tiles = [Image.fromarray(a, "RGBA") for a in (src, out, heat)]
    for name, (x0, y0, x1, y1) in regions.items():
        row = [Image.fromarray(a[y0:y1, x0:x1], "RGBA").resize(((x1 - x0) * ZOOM, (y1 - y0) * ZOOM),
                                                               Image.Resampling.NEAREST) for a in (src, out, heat)]
        strip = Image.new("RGBA", (sum(t.width for t in row) + 16, row[0].height + 18), "white")
        ImageDraw.Draw(strip).text((4, 2), name, fill="black")
        x = 0
        for t in row:
            strip.paste(t, (x, 18))
            x += t.width + 8
        tiles.append(strip)
    sheet = Image.new("RGBA", (max(t.width for t in tiles), sum(t.height + 8 for t in tiles)), "white")
    y = 0
    for t in tiles:
        sheet.paste(t, (0, y))
        y += t.height + 8
    sheet.save(path)


FLIP = """<!doctype html><meta charset="utf-8"><title>focus flip</title>
<style>body{{margin:16px;font:14px system-ui;background:#fff}}#v{{position:relative;display:inline-block}}
#v img{{display:block;image-rendering:pixelated}}#t{{position:absolute;left:0;top:0}}</style>
<p>{what} — <b>space</b> flips source/trace, <b>1 2 4</b> zoom. Showing: <span id="s">trace</span></p>
<div id="v"><img id="o" src="../../source.png"><img id="t" src="trace.svg"></div>
<script>const t=document.getElementById('t'),o=document.getElementById('o'),s=document.getElementById('s');
const W={w},H={h};function z(k){{for(const i of[o,t]){{i.style.width=W*k+'px';i.style.height=H*k+'px'}}}}z(1);
addEventListener('keydown',e=>{{if(e.key===' '){{e.preventDefault();t.hidden=!t.hidden;s.textContent=t.hidden?'source':'trace'}}
if('124'.includes(e.key))z(+e.key)}});</script>"""


def diff(prev: dict, now: dict) -> list[str]:
    def line(scope: str, k: str, sign: int, a: float, b: float) -> str:
        mark = "" if a == b else ("  better" if sign * (b - a) > 0 else "  WORSE")
        return f"  {scope:<8} {k:<18} {a:>10.4f} → {b:>10.4f}{mark}"

    out = [line("whole", k, s, prev["whole"][k], now["whole"][k]) for k, s in WHOLE_KEYS
           if k in prev["whole"] and k in now["whole"]]
    for name, stats in now["regions"].items():
        old = prev["regions"].get(name)
        if old:
            out += [line(name, k, s, old[k], stats[k]) for k, s in REGION_KEYS]
    return out


def run(asset: Path, backend: str = "rust", params: dict | None = None, label: str = "run",
        auto: bool = False) -> Path:
    asset = Path(asset)
    cfg = yaml.safe_load((asset / "focus.yaml").read_text())
    src = load_png(asset / "source.png")
    svg, ms, what = _trace(asset, cfg, backend, params, auto)
    result = assess(svg, src, cfg["regions"], ms)
    runs = asset / "runs"
    prev_dirs = sorted(d for d in runs.glob("*") if (d / "focus.json").exists()) if runs.exists() else []
    run_dir = runs / f"{datetime.now():%Y%m%d-%H%M%S}-{backend}-{label}"
    run_dir.mkdir(parents=True)
    (run_dir / "trace.svg").write_text(svg, encoding="utf-8")
    record = {"what": what, "backend": backend, "whole": result["whole"], "regions": result["regions"]}
    (run_dir / "focus.json").write_text(json.dumps(record, indent=1, default=float))
    _sheet(src, result["_out"], result["_de"], cfg["regions"], run_dir / "sheet.png")
    h, w = src.shape[:2]
    (run_dir / "flip.html").write_text(FLIP.format(what=what, w=w, h=h), encoding="utf-8")
    print(f"{what}\ntrace {ms:.0f} ms → {run_dir}")
    for title, ref in (("vs previous", prev_dirs[-1] / "focus.json" if prev_dirs else None),
                       ("vs baseline", asset / "baseline.json")):
        if ref is not None and ref.exists():
            print(f"\n{title} ({ref.parent.name if title == 'vs previous' else 'pinned'})")
            print("\n".join(diff(json.loads(ref.read_text()), record)))
    return run_dir


def pin(asset: Path, run_dir: Path) -> None:
    shutil.copy(Path(run_dir) / "focus.json", Path(asset) / "baseline.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.focus")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("asset")
    r.add_argument("--backend", choices=("rust", "python"), default="rust")
    r.add_argument("--params", help="JSON layered over focus.yaml's preset and params")
    r.add_argument("--label", default="run")
    r.add_argument("--auto", action="store_true", help="trace as Auto does and keep its pick")
    p = sub.add_parser("pin")
    p.add_argument("asset")
    p.add_argument("run_dir")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        run(Path(args.asset), args.backend, json.loads(args.params) if args.params else None, args.label, args.auto)
    else:
        pin(Path(args.asset), Path(args.run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the test**

Run: `cd backend && .venv/bin/python -m pytest tests/test_bench_focus.py -v`
Expected: 5 passed.

- [ ] **Step 5: Write `backend/bench/sentinels.txt`**

One line per item, chosen because it exercises a stage this asset stresses:
```
# Middle loop (tools/qloop.sh sentinels). Each line: corpus|heldout <id>  # stage it guards
corpus logo/vexel-wordmark-512        # wordmark, sharpening halos, rounded rects
corpus logo/studi0mail-logo-light     # wordmark on a light backdrop
corpus logo/thin-mark-512             # strokes vs fills, stroke_fidelity gate
corpus logo/wedge-fan-512             # acute wedge tips
corpus logo/hex-nest-512-ds           # soft (downsampled) edges
corpus logo/venn-512-q75              # JPEG noise floor, overlaps
corpus flat/low-contrast-512          # crisp small steps must survive
corpus gradient/linear-4stop-512      # multi-stop linear ramp
corpus gradient/multi-shape-512-q75   # gradients under noise
corpus shadow/card-512-q75            # shadow bands on an opaque backdrop
heldout fluent-color/nail-polish-512  # focal radial, highlight with pointed ends
heldout fluent-color/speech-balloon-512 # faint-alpha halo
heldout noto/u2049-512                # glyph outlines
```

- [ ] **Step 6: Write `backend/tools/qloop.sh`** and `chmod +x` it

```bash
#!/usr/bin/env bash
# The three speeds of the focus loop. Run from backend/.
#   tools/qloop.sh focus [bench.focus run args]   ~5 s: the one asset (default bench/focus/wave-lockup)
#   tools/qloop.sh sentinels                      ~30 s: bench/sentinels.txt, gated per item
#   tools/qloop.sh full                           minutes: corpus + held-out, gated per item
#   tools/qloop.sh ref                            freeze the references the gates compare against
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
KEEP=bench/reports/keep-2026-10-05-wave-lockup
ASSET=${ASSET:-bench/focus/wave-lockup}
export VEXEL_BACKEND=${VEXEL_BACKEND:-rust} RAYON_NUM_THREADS=${RAYON_NUM_THREADS:-2}
W=${WORKERS:-6}

ids() { grep -v '^#' bench/sentinels.txt | awk -v c="$1" '$1==c {print $2}' | paste -sd, -; }

bench_run() {  # corpus-dir ids-or-empty out-dir
  local extra=(); [ -n "$2" ] && extra=(--ids "$2")
  $PY -m bench run --engines vexel --corpus "$1" "${extra[@]}" --no-media --workers "$W" --out "$3" >/dev/null
}

case "${1:-}" in
  focus) shift; exec $PY -m bench.focus run "$ASSET" "$@" ;;
  ref)
    bench_run bench/corpus "" "$KEEP/ref-corpus"
    bench_run bench/heldout "" "$KEEP/ref-heldout"
    echo "references: $KEEP/ref-{corpus,heldout}/results.json" ;;
  sentinels|full)
    tag=$(date +%H%M%S); rc=0
    for set in corpus heldout; do
      sel=""; [ "$1" = sentinels ] && sel=$(ids $set)
      bench_run "bench/$set" "$sel" "$KEEP/$1-$tag-$set"
      echo "== $set"; $PY -m bench.gate "$KEEP/ref-$set/results.json" "$KEEP/$1-$tag-$set/results.json" || rc=1
    done
    exit $rc ;;
  *) sed -n '2,6p' "$0"; exit 2 ;;
esac
```

Note: with `--ids`, the reference has more items than the candidate, so `gate` must compare only ids present in the candidate for a sentinel run. Change `missing` in `bench/gate.py` to count an id as missing only when it is in the candidate with an `error`, or absent from the candidate while the candidate's item count equals the reference's:

```python
    subset = len(b) < len(a)
    missing = [f"{i} missing or errored" for i in sorted(a)
               if (i in b and "error" in b[i]) or (i not in b and not subset)]
```
Add a test to `tests/test_bench_gate.py`:
```python
def test_subset_run_only_checks_its_own_items():
    regs, _, missing = gate.gate(_run(a=BASE, b=BASE), _run(a=BASE))
    assert regs == [] and missing == []
```

- [ ] **Step 7: Run all focus/gate tests and a real focus run**

```bash
cd backend && .venv/bin/python -m pytest tests/test_bench_focus.py tests/test_bench_gate.py -v
tools/qloop.sh focus --label smoke
```
Expected: all pass; the focus run prints a trace time and a run dir. Open `sheet.png` and `flip.html` and check the regions land on the parts named in `focus.yaml`. Record the wall time of `tools/qloop.sh focus` in the Results log.

- [ ] **Step 8: Commit**

```bash
git add backend/bench/focus.py backend/bench/gate.py backend/bench/sentinels.txt backend/tools/qloop.sh backend/tests/test_bench_focus.py backend/tests/test_bench_gate.py
git commit -m "bench: focus runner (sheet, flip page, history), sentinel set and tools/qloop.sh"
```

---

### Task 4: Freeze the references and pin the baseline — CHECKPOINT with Tim

**Files:** none tracked. Writes `bench/reports/keep-2026-10-05-wave-lockup/ref-{corpus,heldout}/`, `bench/focus/wave-lockup/baseline.json`.

- [ ] **Step 1: Confirm the tree is the merge base** — `git log -1 --format=%H` equals `29801cf` or the commit after Tasks 0–3 (no engine change yet). The references must be the engine as it is before any fix.

- [ ] **Step 2: Freeze corpus + held-out references**

```bash
cd backend && time tools/qloop.sh ref
```
Record wall time and the per-class means (`python -m bench report` is not needed; read `summary` from each `results.json`).

- [ ] **Step 3: Check the references against the committed baseline**

```bash
.venv/bin/python -m bench.gate bench/baselines/vexel.json bench/reports/keep-2026-10-05-wave-lockup/ref-corpus/results.json
```
Expected: `gate ok` with no regressions. If anything moved, the committed baseline is stale or the environment differs (resvg version, extension not rebuilt); stop and report before going on — every later comparison stands on this.

- [ ] **Step 4: Find which preset Auto picks, then pin the baseline**

```bash
tools/qloop.sh focus --auto --label auto-baseline
```
Set `preset:` in `focus.yaml` to the printed pick, then:
```bash
tools/qloop.sh focus --label baseline
.venv/bin/python -m bench.focus pin bench/focus/wave-lockup bench/focus/wave-lockup/runs/<the baseline run>
cp -r bench/focus/wave-lockup/runs/<the baseline run> bench/reports/keep-2026-10-05-wave-lockup/focus-baseline
```

- [ ] **Step 5: Checkpoint.** Send Tim `sheet.png` and the per-region numbers. Ask him to (a) confirm or redraw the regions, (b) confirm the Definition of done thresholds against these numbers, (c) point at anything on `flip.html` at 1× that bothers him that no region catches. Record his answers in the Results log. Do not start Task 5 without them.

---

### Task 4b: Shape and fill measures that forgive the source's artifacts

Added after the Task 4 checkpoint. Tim's answer: the trace matches **shapes and true colours**; halos, sharpening rims (dark edge, light core) and the ribbons' streak noise are generator artifacts, not targets. View-ΔE cannot tell those apart from real defects (at baseline the wordmark's whole error sits within 2 px of an edge), so the focus loop gains two measures per region, which become its primary numbers. `visible_frac`/`de_p99` stay as secondary readings.

- **Edge fit**: Canny edges of both images' luminance at σ `EDGE_SIGMA` 1.5, which merges a 1–2 px halo or rim into its edge; every edge pixel of either image is scored by its distance to the other image's nearest edge. `edge_off_frac` is the share at `EDGE_OFF` 2 px or more, `edge_p99_px` the 99th percentile. The misses are clustered and located, so a run prints *where* the shapes differ.
- **Fill colour**: CIEDE2000 between the two images on pixels at least `CORE_PX` 3 px inside an edge in both, each image blurred over that core alone by σ `FILL_SIGMA` 4 (normalised convolution). `fill_de_mean`, `fill_de_p95`.

Calibrated while planning on the baseline: the misses at 2 px are the ribbon tips (≈ (430, 105), (88, 118), (358, 176)) and the small caps; `light`/`river` score `edge_off_frac` ≤ 0.0005; `fill_de_mean` is 0.4–0.6 on the wordmark and 1.2 / 2.9 on wave / gap, where the streaks are (residual by decision). Synthetic checks: a 1 px light halo outside a square plus a 1 px dark rim inside scores 0 on both; a 6 px corner cut scores `edge_off_frac` 0.04 with one miss cluster at the corner; a ΔE 5.5 fill shift scores `fill_de_p95` 5.5 with `edge_off_frac` 0. Pixel-level Canny does not see a sub-pixel corner rounding (the thin word's round stroke corners score within 1 px); Task 5 judges those on the 8× crops.

**Files:**
- Modify: `backend/bench/focus.py`
- Test: `backend/tests/test_bench_focus.py`

**Interfaces:**
- Consumes: `assess`, `run`, `diff`, `WHOLE_KEYS`, `REGION_KEYS` (Tasks 1, 3).
- Produces:
  - `EDGE_SIGMA = 1.5`, `EDGE_OFF = 2.0`, `CORE_PX = 3.0`, `FILL_SIGMA = 4.0`
  - `shape_edges(rgb: np.ndarray) -> np.ndarray` (bool (H, W))
  - `edge_fit(es, eo, box) -> {"edge_off_frac", "edge_p99_px"}`
  - `fill_de(src_rgb, out_rgb, es, eo, box) -> {"fill_de_mean", "fill_de_p95"}`
  - `edge_misses(es, eo) -> list[tuple[float, float, int]]` — (x, y, pixels) per cluster, largest first
  - `assess` adds the four keys to `whole` and to every region, and returns `"misses"` (the 20 largest clusters, each with the region it falls in or `"-"`); `run` stores `misses` in `focus.json` and prints the 8 largest after the diff.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_bench_focus.py`)

```python
def _square(col=(30, 40, 80), w=64, lo=16, hi=48) -> np.ndarray:
    a = np.full((w, w, 3), 255, np.uint8)
    a[lo:hi, lo:hi] = col
    return a


BOX = (0, 0, 64, 64)


def test_halo_and_sharpening_rim_are_forgiven():
    src = _square()
    src[15, 15:49] = src[48, 15:49] = 200          # light halo just outside
    src[15:49, 15] = src[15:49, 48] = 200
    src[16, 16:48] = src[47, 16:48] = 5            # dark rim just inside
    src[16:48, 16] = src[16:48, 47] = 5
    out = _square()
    es, eo = focus.shape_edges(src), focus.shape_edges(out)
    assert focus.edge_fit(es, eo, BOX)["edge_off_frac"] == 0.0
    assert focus.fill_de(src, out, es, eo, BOX)["fill_de_mean"] < 0.5


def test_cut_corner_is_an_edge_miss_where_it_is():
    out = _square().copy()
    for i in range(6):
        out[16 + i, 16:22 - i] = 255
    es, eo = focus.shape_edges(_square()), focus.shape_edges(out)
    assert focus.edge_fit(es, eo, BOX)["edge_off_frac"] > 0.02
    x, y, n = focus.edge_misses(es, eo)[0]
    assert abs(x - 18) < 3 and abs(y - 18) < 3 and n > 0


def test_wrong_fill_is_a_fill_error_not_an_edge_error():
    src, out = _square(col=(30, 40, 80)), _square(col=(45, 55, 105))
    es, eo = focus.shape_edges(src), focus.shape_edges(out)
    assert focus.edge_fit(es, eo, BOX)["edge_off_frac"] == 0.0
    assert focus.fill_de(src, out, es, eo, BOX)["fill_de_p95"] > 3.0
```

Extend `test_run_writes_a_complete_run_dir` to assert `{"edge_off_frac", "edge_p99_px", "fill_de_mean", "fill_de_p95"} <= set(data["regions"]["left"])` and `"misses" in data`.

- [ ] **Step 2: Run to verify they fail** — `cd backend && .venv/bin/python -m pytest tests/test_bench_focus.py -v`; expected: the three new tests fail with `AttributeError` (no `shape_edges`), the extended run test with a missing key.

- [ ] **Step 3: Implement** (in `bench/focus.py`, beside the Task 1 metrics; add `distance_transform_edt, label, binary_dilation, center_of_mass` to the scipy import, `from skimage.feature import canny`, and `luminance` to the quality import)

```python
EDGE_SIGMA = 1.5  # Canny scale: merges a 1–2 px halo or sharpening rim into the edge it rings
EDGE_OFF = 2.0    # px from the other image's nearest edge at which an edge pixel is a miss
CORE_PX = 3.0     # fills are compared this far inside every edge, clear of halos and rims
FILL_SIGMA = 4.0  # px: blur over the core, so streak noise in the source does not count as a wrong fill


def shape_edges(rgb: np.ndarray) -> np.ndarray:
    return canny(luminance(rgb) / 255.0, sigma=EDGE_SIGMA)


def edge_fit(es: np.ndarray, eo: np.ndarray, box) -> dict[str, float]:
    """Both images' edge pixels in `box`, scored by distance to the other image's nearest edge."""
    x0, y0, x1, y1 = box
    to_out = distance_transform_edt(~eo)[y0:y1, x0:x1][es[y0:y1, x0:x1]]
    to_src = distance_transform_edt(~es)[y0:y1, x0:x1][eo[y0:y1, x0:x1]]
    d = np.concatenate([to_out, to_src])
    if d.size == 0:
        return {"edge_off_frac": 0.0, "edge_p99_px": 0.0}
    return {"edge_off_frac": float((d >= EDGE_OFF).mean()), "edge_p99_px": float(np.percentile(d, 99))}


def fill_de(src_rgb: np.ndarray, out_rgb: np.ndarray, es: np.ndarray, eo: np.ndarray, box) -> dict[str, float]:
    """CIEDE2000 over the pixels CORE_PX inside every edge of both images, each blurred over that core only."""
    core = (distance_transform_edt(~es) >= CORE_PX) & (distance_transform_edt(~eo) >= CORE_PX)
    x0, y0, x1, y1 = box
    inside = core[y0:y1, x0:x1]
    if not inside.any():
        return {"fill_de_mean": 0.0, "fill_de_p95": 0.0}
    weight = np.maximum(gaussian_filter(core.astype(np.float64), FILL_SIGMA), 1e-9)[..., None]

    def blur(a: np.ndarray) -> np.ndarray:
        return np.dstack([gaussian_filter(a[..., c] * core, FILL_SIGMA) for c in range(3)]) / weight

    d = delta_e_map(blur(src_rgb.astype(np.float64)), blur(out_rgb.astype(np.float64)))[y0:y1, x0:x1][inside]
    return {"fill_de_mean": float(d.mean()), "fill_de_p95": float(np.percentile(d, 95))}


def edge_misses(es: np.ndarray, eo: np.ndarray) -> list[tuple[float, float, int]]:
    """Where the shapes differ: clusters of edge pixels EDGE_OFF or more from the other image's edges."""
    miss = (es & (distance_transform_edt(~eo) >= EDGE_OFF)) | (eo & (distance_transform_edt(~es) >= EDGE_OFF))
    labels, n = label(binary_dilation(miss, iterations=2))
    found = []
    for i in range(1, n + 1):
        m = (labels == i) & miss
        y, x = center_of_mass(m)
        found.append((float(x), float(y), int(m.sum())))
    return sorted(found, key=lambda t: -t[2])
```

In `assess`, after `de` is computed: `src_rgb, out_rgb = to_rgb_on_white(src_rgba), to_rgb_on_white(out_rgba)`, `es, eo = shape_edges(src_rgb), shape_edges(out_rgb)`; merge `edge_fit(es, eo, box)` and `fill_de(src_rgb, out_rgb, es, eo, box)` into `whole` (box = the frame) and into every region's dict; add `"misses": [{"x": x, "y": y, "px": n, "region": <first region whose box holds (x, y), else "-">} for x, y, n in edge_misses(es, eo)[:20]]`. In `run`, write `misses` into `focus.json` and print, after the diffs, `misses (x, y, px, region):` and the 8 largest. Add `("edge_off_frac", -1), ("fill_de_mean", -1)` to `WHOLE_KEYS` after `("score", +1)`, and put `("edge_off_frac", -1), ("edge_p99_px", -1), ("fill_de_mean", -1), ("fill_de_p95", -1)` first in `REGION_KEYS`.

- [ ] **Step 4: Run the tests** — all of `tests/test_bench_focus.py` and `tests/test_bench_gate.py` pass.

- [ ] **Step 5: Re-pin the baseline with the new keys** — the engine has not changed, so this is the same trace measured more ways:
```bash
cd backend && tools/qloop.sh focus --label baseline2
.venv/bin/python -m bench.focus pin bench/focus/wave-lockup bench/focus/wave-lockup/runs/<the baseline2 run>
cp -r bench/focus/wave-lockup/runs/<the baseline2 run> bench/reports/keep-2026-10-05-wave-lockup/focus-baseline2
```
Record the per-region `edge_off_frac`, `edge_p99_px`, `fill_de_mean`, `fill_de_p95` and the printed misses in the Results log.

- [ ] **Step 6: Commit** — `git add backend/bench/focus.py backend/tests/test_bench_focus.py && git commit -m "bench.focus: edge fit and core fill colour per region, forgiving halos, rims and streak noise"`

---

### Task 5: Diagnose — a ranked defect ledger

**Files:** Results log at the end of this plan (tracked; describe defects by region and stage, no images). Crops and dumps stay in `bench/reports/keep-2026-10-05-wave-lockup/diag/`.

- [ ] **Step 1: Dump the stages on the baseline trace**

```bash
cd backend && mkdir -p bench/reports/keep-2026-10-05-wave-lockup/diag/rust
VEXEL_DUMP=bench/reports/keep-2026-10-05-wave-lockup/diag/rust tools/qloop.sh focus --label dump
.venv/bin/python -m bench artifacts bench/focus/wave-lockup/runs/<dump run>/trace.svg bench/focus/wave-lockup/source.png --where
```

- [ ] **Step 2: For each region, find the first stage where it goes wrong.** Colour `labels_merge`, `labels_rescue`, `labels_refine` and `labels_to_topology` (`h w` header, then one row of labels per line) as a random-palette PNG cropped to the region, next to the source crop. A defect is a **partition** defect if the label map is already wrong (a ribbon in pieces, the gap not one backdrop region, a counter missing, halo rings as regions), a **fill** defect if the labels are right but the colour/gradient is (banding, wrong stops), a **geometry** defect if both are right and the outline is not (blunt tip, wobble, flare, stroke width), a **stroke** defect if `strokes.txt` shows a group stroked that should be filled or the reverse.

- [ ] **Step 3: Write the ledger** in the Results log: one row per defect — id (D1, D2…), region, stage, what is wrong at 1×, the region metric it moves, and which sentinel guards the same stage. Rank by visible area at 1× (`visible_frac` × region area), largest first. Check each hypothesis in the Context table off as confirmed or refuted.

- [ ] **Step 4: Commit the ledger** — `git add docs/superpowers/plans/2026-10-05-wave-lockup-focus-loop.md && git commit -m "plan: wave-lockup defect ledger"`

---

### Task 6: The fix cycle (repeat per ledger row, in rank order)

Each defect is its own pass through these steps and its own commit(s). A defect whose fix fails step 7 or 8 twice is written up in the ledger as blocked with what was learned, and the next row is taken.

- [ ] **Step 1: Name the hypothesis** in the ledger row: the stage, the constant or rule at fault, and the predicted move in the region metric.

- [ ] **Step 2: Reproduce it without the asset.** Write a failing test in the stage's existing test file (`tests/test_vexel_<stage>.py`) on a synthetic image that shows the same defect: a tapered ribbon with a 7 px white channel, a 6 px stroke with a 1.5 px blur, 20 px caps on 254/255 noise — whichever the row needs, built with numpy or `bench.synth.render_png` from an SVG string. The test asserts the measurable fact (the gap is one region; the tip is within 0.5 px of the truth; the stem is filled, not stroked). Run it; it must fail for the hypothesised reason.

- [ ] **Step 3: Prototype the fix in Python** (`engines/vexel/<stage>.py`) and iterate with:
```bash
tools/qloop.sh focus --backend python --label d<N>-try<k>
```
Seconds per try, no rebuild. Read the deltas against the previous run and the baseline. Keep a try only if the target region improves and no other region gets worse.

- [ ] **Step 4: Survey before fixing a threshold.** If the fix introduces or moves a constant, compute the quantity it thresholds over every corpus and held-out item (one script under `bench/reports/keep-2026-10-05-wave-lockup/survey/`, kept) and pick the value from the gap in that distribution, off a multiple of 0.5 px. Write the distribution's relevant numbers into the ledger row.

- [ ] **Step 5: Sentinels in Python** — `VEXEL_BACKEND=python tools/qloop.sh sentinels`. The reference is Rust; a Python candidate differs from it within diffcheck's allowed tolerances, so read regressions here as a signal, not a verdict. Anything clearly worse sends you back to step 3.

- [ ] **Step 6: Port to Rust** (`vexel-rs/src/<stage>.rs`), name any new tie in both engines, add the stage's comparison to `tools/diffcheck.py` if it has none, then:
```bash
cd backend && .venv/bin/python -m maturin develop --release -m vexel-rs/Cargo.toml
.venv/bin/python -m tools.diffcheck <stage>
tools/qloop.sh focus --label d<N>-rust
```
Expected: diffcheck passes; the Rust focus numbers match the Python try within the stage's tolerance.

- [ ] **Step 7: Full gate on Rust**
```bash
tools/qloop.sh full
```
Expected: `gate ok` on both sets. A flagged item is opened (`bench artifacts … --where`, its sheet) and either fixed, or listed in the ledger row with the reason and shown to Tim for acceptance. Not silently accepted.

- [ ] **Step 8: Tests, parity, fixtures**
```bash
cd backend && .venv/bin/python -m pytest -q && .venv/bin/python -m tools.diffcheck
.venv/bin/python -m tools.export_core_fixtures --only scorecard,api,auto,svg,render,drawing,holes,geometry
cd .. && cargo test --workspace --release
```
Expected: all pass (re-export only when the SVG the engine writes changed).

- [ ] **Step 9: Commit and log.** One commit, both engines, test, fixtures. Message names the stage and the defect in the codebase's style, e.g. `vexel: a white channel between two ribbons stays one backdrop region (both engines)`. Append to the ledger row: region metrics before → after, the full gate's improved/regressed counts, the commit hash. Update CLAUDE.md's conventions only if the fix establishes a rule a later change could break (as the existing bullets do).

- [ ] **Step 10: Every third defect, Auto check.** `tools/qloop.sh focus --auto --label auto-d<N>`: confirm Auto still picks the preset in `focus.yaml`; if it changed, update `focus.yaml` and re-pin, and note why.

---

### Task 7: Lock it in — a license-clean stand-in in the corpus

The real asset cannot ship in the public corpus, so the corpus gets a synthetic item that reproduces its hard parts with vector truth (which also unlocks `outline_px`/`junction_px` for it).

**Files:**
- Modify: `backend/bench/synth.py`, `backend/bench/corpus/manifest.yaml` (by `bench generate`)
- Modify: `backend/bench/baselines/vexel.json`, `backend/studi0trace/engines/preset_details.json`, core fixtures

- [ ] **Step 1: Add `wave-lockup` to `bench/synth.py`** in the logo class, following the existing generators' pattern: an SVG of two tapered ribbons (cubic outlines) with a 7 px white channel between them, each filled by a 4-stop `linearGradient` (navy `#14244a` → `#3b7fd9` → `#14244a`; `#3a9bd8` → `#5cc6e8` → `#5fc77a`), a row of thin-stroke geometric letterforms built from rects and circles (stem 6 px at 512 wide), a row of small caps (O, C, A, B, L shapes, stem 3.5 px), on `#fefefe`. Rendered at 512 and 1024 wide, plus the `-ds` degradation, as the generator does for other items. Add the blotch noise of the source (±1 level, 40 px Gaussian) to the backdrop in the PNG, not the truth.

- [ ] **Step 2: Test it** in `tests/test_bench_corpus.py`: the generated item exists, has a truth SVG, and its PNG's backdrop is not a single colour.

- [ ] **Step 3: Generate, confirm the stand-in shows the same defects the ledger fixed** by running it at HEAD and in a temporary worktree (`git worktree add`) at the Task 4 reference commit with only this generator change applied. The stand-in is only a guard if HEAD beats the merge base on it in the regions the ledger names.

- [ ] **Step 4: Deliberately move the baselines** (only now — CONTRIBUTING: "baselines move only deliberately"):
```bash
cd backend && .venv/bin/python -m bench generate
VEXEL_BACKEND=rust .venv/bin/python -m bench run --engines vexel --no-media --workers 6 --update-baseline
VEXEL_BACKEND=rust RAYON_NUM_THREADS=1 .venv/bin/python -m bench.presets_eval --out bench/reports/keep-2026-10-05-wave-lockup/presets --fast --workers 3 --write-details
.venv/bin/python -m tools.export_core_fixtures --only scorecard,api,auto,svg,render,drawing,holes,geometry
cd .. && cargo test --workspace --release
```

- [ ] **Step 5: Final checkpoint with Tim** on `flip.html` of the final focus run (Definition of done 2), the full gate summary against the Task 4 references, and the ledger. Then commit:
```bash
git add backend/bench/synth.py backend/bench/corpus backend/bench/baselines/vexel.json backend/studi0trace/engines/preset_details.json crates/studi0trace-core/tests/fixtures backend/tests/test_bench_corpus.py docs/superpowers/plans/2026-10-05-wave-lockup-focus-loop.md
git commit -m "bench: wave-lockup stand-in in the corpus; baseline and preset lines after the wave-lockup pass"
```

---

## Results log

(Append as tasks complete: environment facts, wall times, reference numbers, Tim's checkpoint answers, the defect ledger, and per-defect before → after.)

### 2026-10-05 — Tasks 0–4 (steps 1–4)

- Environment: `uv sync` does not install the dev extra; use `VIRTUAL_ENV=$PWD/.venv uv pip install -e ".[dev]"` after it. `uv sync` writes an untracked `backend/uv.lock`: stage by name, never `git add -A`. Builds: vexel_rs 42 s, studi0trace_core 45 s.
- Regions corrected on an overlay (the planned boxes clipped the g descender, split the t, missed the caps' tops): `gap [85,110,370,185]`, `light [440,30,805,215]`, `river [805,30,1180,190]`, `caps [440,222,1180,272]`; `wave` unchanged.
- Loop speeds: `qloop.sh focus` 6.4 s wall (trace 3.9–4.7 s); `--auto` 8.4 s; `qloop.sh ref` (corpus + held-out) 6 min 39 s with 6 workers.
- References (HEAD 59a352a, engine = 29801cf): gate vs `bench/baselines/vexel.json` ok, 0 regressions, 0 improvements. Corpus score flat 0.9562 / gradient 0.9802 / logo 0.9685 / shadow 0.9668; held-out fluent-color 0.9366 / fluent-flat 0.9561 / noto 0.9452.
- Auto picks `logo` (ΔE 1.533, ART 44.4, 43 paths) over balanced (ART 113.4), detailed (2 slivers), dense.
- Focus baseline (logo preset): whole ΔE 1.53, view-ΔE p99 10.5, visible 5.8 %, 0 pinholes, 0 slivers, wobble 21.7°/100 px.

| region | de_mean | de_p99 | visible_frac | inflections | wobble |
|---|---|---|---|---|---|
| wave | 1.83 | 11.4 | 12.5 % | 23 | 0 |
| gap | 3.70 | 12.4 | 29.4 % | 8 | 0 |
| light | 1.34 | 12.9 | 6.5 % | 4 | 319 |
| river | 1.54 | 12.9 | 6.1 % | 10 | 52 |
| caps | 1.75 | 9.9 | 8.4 % | 2 | 2780 |

- Where the error is: in the wordmark all of it lies within 2 px of an edge; in the wave and gap half is interior (the ribbons' specular streaks drawn as smooth ramps). Blurring the trace to the source's softness lowers visible_frac by at most a fifth, so softness is not the main cause. Seen at 8×: the thin word's stems are drawn as round-capped, round-joined strokes where the source has square ends and corners; the small caps come out heavier with lumpy outlines and malformed M vertices; some caps carry stray shading (an O drawn as a lit sphere, dark spots in M/U); a speck sits off the dark ribbon's left tip.

### 2026-10-05 — Task 4 checkpoint (Tim)

- Fidelity: match shapes + true colours; halos, sharpening rims and the ribbons' streak noise are artifacts (not "also the streaks", not "pixel-close"). → Task 4b.
- Done bar: set after the first fixes, not before.
- Regions: keep the corrected boxes.
