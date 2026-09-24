"""Prototype of an Auto preset: trace with a few candidate parameter sets and
keep the cleanest one that is as faithful as the best.

    python -m bench.auto IMAGE.png [--candidates balanced,clean,simplify]

The rule is lexicographic, so it can be explained to a user in one line:
among the candidates whose mean ΔE is within DE_SLACK of the most faithful
one's (and whose edge F1 is within EDGE_SLACK), take the one with the lowest
artifact index; ties go to fewer paths. "As faithful as the best, and the
cleanest of those."

Candidates are parameter sets, not presets: `balanced` is the defaults;
`clean` is Logo & icon with the curve tolerance back at the default (the 0.6
tolerance is what bows its straight edges); `simplify` is Photo & dense art.
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from bench.artifacts import scorecard
from bench.metrics import delta_e, edge_f1
from bench.raster import rasterize, to_rgb_on_white

DE_SLACK = 0.15
EDGE_SLACK = 0.02

CANDIDATES: dict[str, dict] = {
    "balanced": {},
    "clean": {"detail": 10.0, "min_region": 16, "corner_threshold": 70.0},
    "simplify": {"detail": 14.0, "min_region": 24, "strokes": False, "overlaps": False, "shadows": False},
}


@dataclass
class Scored:
    name: str
    params: dict
    svg: str
    delta_e: float
    edge_f1: float
    card: dict
    trace_ms: float


def score(name: str, params: dict, svg: str, src: np.ndarray, trace_ms: float) -> Scored:
    h, w = src.shape[:2]
    out = rasterize(svg, w, h)
    a, b = to_rgb_on_white(src), to_rgb_on_white(out)
    return Scored(name, params, svg, delta_e(a, b)[0], edge_f1(a, b), scorecard(svg, src), trace_ms)


def choose(scored: list[Scored]) -> Scored:
    best_de = min(s.delta_e for s in scored)
    ok = [s for s in scored if s.delta_e <= best_de + DE_SLACK]
    best_edge = max(s.edge_f1 for s in ok)
    ok = [s for s in ok if s.edge_f1 >= best_edge - EDGE_SLACK]
    return min(ok, key=lambda s: (round(s.card["artifact_index"], 1), s.card["elements"]))


def auto_trace(png_bytes: bytes, candidates: dict[str, dict] = CANDIDATES, workers: int = 3) -> tuple[Scored, list[Scored]]:
    from studi0trace.engines import registry
    from studi0trace.imaging.intake import load_upload

    registry.load_builtin()
    engine = registry.get("vexel")
    image = load_upload(png_bytes, max_bytes=1 << 30, max_pixels=1 << 30)
    src = np.asarray(image.image.convert("RGBA"))

    def run(item):
        name, params = item
        t = time.perf_counter()
        res = engine.trace(image, engine.Params.model_validate(params))
        return score(name, params, res.svg, src, 1000 * (time.perf_counter() - t))

    with ThreadPoolExecutor(workers) as pool:
        scored = list(pool.map(run, candidates.items()))
    return choose(scored), scored


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bench.auto")
    ap.add_argument("image")
    ap.add_argument("--candidates", default=",".join(CANDIDATES))
    args = ap.parse_args(argv)
    cands = {k: CANDIDATES[k] for k in args.candidates.split(",")}
    t = time.perf_counter()
    pick, scored = auto_trace(open(args.image, "rb").read(), cands)
    wall = time.perf_counter() - t
    for s in scored:
        c = s.card
        print(f"{s.name:<10} dE {s.delta_e:.3f} edgeF1 {s.edge_f1:.3f} ART {c['artifact_index']:6.1f} "
              f"pinholes {c['pinholes']} slivers {c['slivers'] + c['degenerate'] + c['thin_strokes']} "
              f"wobble {c['wobble_deg_100px']:.0f} infl {c['inflections']} uneven-rects {c['radius_inconsistent'] + c['rect_bowed']} "
              f"paths {c['elements']}  trace {s.trace_ms:.0f} ms")
    print(f"auto -> {pick.name}   ({wall:.1f} s wall for {len(scored)} candidates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
