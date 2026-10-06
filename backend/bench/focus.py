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

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter

from bench import metrics
from bench.artifacts import scorecard
from bench.raster import load_png, rasterize, to_rgb_on_white
from bench.runner import _heatmap
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
            out += [line(name, k, s, old[k], stats[k]) for k, s in REGION_KEYS if k in old and k in stats]
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
