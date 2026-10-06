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
