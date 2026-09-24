"""Auto on one image, from the command line: every candidate's card and the pick.

    python -m bench.auto IMAGE.png [--candidates balanced,logo,detailed,dense]

The rule and the candidates are the service's own (`studi0trace.auto`,
`studi0trace.engines.presets`); this only runs them in threads (the Rust
engine releases the GIL) and prints what each candidate scored.
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor

from studi0trace import auto
from studi0trace.engines.presets import auto_candidates


def auto_trace(png_bytes: bytes, names: list[str] | None = None, workers: int = 4):
    """(pick, reason, [(preset, svg, scores, trace_ms)]) for one image."""
    from studi0trace.engines import registry
    from studi0trace.imaging.intake import load_upload

    registry.load_builtin()
    engine = registry.get("vexel")
    image = load_upload(png_bytes, max_bytes=1 << 30, max_pixels=1 << 30)
    presets = [p for p in auto_candidates() if names is None or p.id in names]
    ref = auto.reference(image)

    def run(preset):
        t = time.perf_counter()
        res = engine.trace(image, engine.Params.model_validate(preset.params))
        ms = 1000 * (time.perf_counter() - t)
        return preset, res.svg, auto.assess(res.svg, ref), ms

    with ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(run, presets))
    pick, reason = auto.choose([auto.Scored(p.id, s["delta_e_mean"], s["edge_f1"], s["artifact_index"], int(s["elements"]))
                                for p, _svg, s, _ms in rows])
    return pick, reason, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bench.auto")
    ap.add_argument("image")
    ap.add_argument("--candidates", help="comma list of preset ids (default: every Auto candidate)")
    args = ap.parse_args(argv)
    t = time.perf_counter()
    pick, reason, rows = auto_trace(open(args.image, "rb").read(), args.candidates.split(",") if args.candidates else None)
    wall = time.perf_counter() - t
    for p, _svg, c, ms in rows:
        print(f"{p.id:<10} dE {c['delta_e_mean']:.3f} edgeF1 {c['edge_f1']:.3f} ART {c['artifact_index']:6.1f} "
              f"pinholes {c['pinholes']} slivers {c['slivers'] + c['degenerate'] + c['thin_strokes']} "
              f"wobble {c['wobble_deg_100px']:.0f} infl {c['inflections']} uneven-rects {c['radius_inconsistent'] + c['rect_bowed']} "
              f"paths {c['elements']}  trace {ms:.0f} ms  {', '.join(auto.issues(c)) or 'clean'}")
    label = next((p.label for p, *_ in rows if pick is not None and p.id == pick.id), "nothing")
    print(f"Auto chose {label} — {reason}   ({wall:.1f} s wall for {len(rows)} candidates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
