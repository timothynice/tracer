"""Every preset over the corpus, with the artifact scorecard, and which parameter does it.

    python -m bench.presets_eval --out DIR [--ablate] [--forward] [--workers N] [--classes a,b] [--ids x,y]

For each configuration — a preset as shipped; with `--ablate`, the preset
with one of its parameters put back to the default (`logo-detail` is `logo`
with `detail` at its default); with `--forward`, the defaults with one
preset's parameter applied (`+detail=10`) — every corpus item is traced and
scored with the standard bench metrics plus the scorecard
(`bench.artifacts`). Writes DIR/records.jsonl (one line per item and
configuration), DIR/svgs/<config>/<item>.svg and DIR/summary.json
(means per configuration and class), and prints the table.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TABLE_KEYS = ("score", "delta_e_mean", "ssim", "edge_f1", "seam_ppm", "paths", "pinholes", "hole_clusters",
              "slivers", "degenerate", "thin_strokes", "wobble_deg_100px", "inflections", "radius_inconsistent",
              "rect_bowed", "artifact_index", "elapsed_ms")


def configs(ablate: bool, forward: bool) -> list[tuple[str, str, dict]]:
    """(name, preset it derives from, params)."""
    from studi0trace.engines.presets import all_presets

    out: list[tuple[str, str, dict]] = []
    presets = [p for p in all_presets() if p.engine == "vexel"]
    for p in presets:
        out.append((p.id, p.id, dict(p.params)))
    if ablate:
        for p in presets:
            for k in p.params:
                out.append((f"{p.id}-{k}", p.id, {kk: v for kk, v in p.params.items() if kk != k}))
    if forward:
        seen = set()
        for p in presets:
            for k, v in p.params.items():
                if (k, repr(v)) in seen:
                    continue
                seen.add((k, repr(v)))
                out.append((f"+{k}={v}", "balanced", {k: v}))
    return out


def _work(args: tuple) -> dict:
    name, params, item_entry, corpus_root, svg_dir, fast = args
    from bench.corpus import Item
    from bench.raster import load_png, rasterize
    from bench import metrics
    from studi0trace.engines import registry
    from studi0trace.imaging.intake import load_upload

    registry.load_builtin()
    if fast:  # the per-element 4x seam renders are most of the cost; the scorecard's holes see the same defect
        metrics.seam_index = lambda *_a, **_k: None
    item = Item.from_manifest(item_entry, Path(corpus_root))
    engine = registry.get("vexel")
    rec: dict = {"config": name, "id": item.id, "cls": item.cls}
    try:
        image = load_upload(item.png.read_bytes(), max_bytes=1 << 30, max_pixels=1 << 30)
        t = time.perf_counter()
        result = engine.trace(image, engine.Params.model_validate(params))
        src = load_png(item.png)
        out = rasterize(result.svg, item.width, item.height)
        truth = item.truth_svg.read_text(encoding="utf-8") if item.truth_svg and item.truth_svg.exists() and not fast else None
        m = metrics.all_metrics(src, out, result.svg, result.elapsed_ms, item.truth_paths, truth_svg=truth)
        rec["metrics"] = {k: (float(v) if isinstance(v, (int, float, np.floating, np.integer)) and v is not None else v)
                          for k, v in m.items()}
        rec["wall_s"] = time.perf_counter() - t
        if svg_dir:
            p = Path(svg_dir) / name / (item.id.replace("/", "__") + ".svg")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(result.svg, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def summarize(records: list[dict]) -> dict:
    acc: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for r in records:
        if "error" in r:
            continue
        for cls in (r["cls"], "ALL"):
            for k in TABLE_KEYS:
                v = r["metrics"].get(k)
                if v is not None:
                    acc[r["config"]][cls][k].append(float(v))
    out: dict = {}
    for cfg, by in acc.items():
        out[cfg] = {cls: {k: float(np.mean(v)) for k, v in ks.items()} | {"n": len(next(iter(ks.values())))}
                    for cls, ks in by.items()}
    return out


def print_table(summary: dict, keys=TABLE_KEYS, classes=("ALL", "logo", "flat", "gradient", "shadow")) -> None:
    short = {"delta_e_mean": "dE", "edge_f1": "edgeF1", "seam_ppm": "seam", "hole_clusters": "holeC",
             "degenerate": "degen", "thin_strokes": "thinS", "wobble_deg_100px": "wobble", "inflections": "infl",
             "radius_inconsistent": "radius", "rect_bowed": "bowed", "artifact_index": "ART", "elapsed_ms": "ms",
             "pinholes": "pinh", "slivers": "sliv"}
    head = f"{'config':<26}{'class':<9}" + "".join(f"{short.get(k, k):>9}" for k in keys)
    for cls in classes:
        print("\n" + head)
        for cfg, by in summary.items():
            s = by.get(cls)
            if not s:
                continue
            print(f"{cfg:<26}{cls:<9}" + "".join(f"{s.get(k, float('nan')):>9.3f}" if k in ("score", "ssim", "edge_f1", "delta_e_mean")
                                                   else f"{s.get(k, float('nan')):>9.1f}" for k in keys))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.presets_eval")
    ap.add_argument("--out", required=True)
    ap.add_argument("--corpus", default=str(HERE / "corpus"))
    ap.add_argument("--classes")
    ap.add_argument("--ids")
    ap.add_argument("--only", help="comma list of config names to run")
    ap.add_argument("--ablate", action="store_true")
    ap.add_argument("--forward", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-svgs", action="store_true")
    ap.add_argument("--fast", action="store_true", help="skip the 8x outline error against the vector truth and the per-element seam renders")
    args = ap.parse_args(argv)
    import yaml

    root = Path(args.corpus)
    entries = yaml.safe_load((root / "manifest.yaml").read_text())["items"]
    if args.classes:
        entries = [e for e in entries if e["class"] in args.classes.split(",")]
    if args.ids:
        entries = [e for e in entries if e["id"] in args.ids.split(",")]
    cfgs = configs(args.ablate, args.forward)
    if args.only:
        want = set(args.only.split(","))
        cfgs = [c for c in cfgs if c[0] in want]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    svg_dir = None if args.no_svgs else str(out / "svgs")
    done: set[tuple[str, str]] = set()
    rec_path = out / "records.jsonl"
    records: list[dict] = []
    if rec_path.exists():  # resume
        for line in rec_path.read_text().splitlines():
            r = json.loads(line)
            records.append(r)
            done.add((r["config"], r["id"]))
    tasks = [(name, params, e, str(root), svg_dir, args.fast) for name, _base, params in cfgs for e in
             sorted(entries, key=lambda e: -e["width"] * e["height"]) if (name, e["id"]) not in done]
    print(f"{len(cfgs)} configs x {len(entries)} items: {len(tasks)} to run on {args.workers} workers "
          f"(VEXEL_BACKEND={os.environ.get('VEXEL_BACKEND', '')})", flush=True)
    t0 = time.time()
    with rec_path.open("a") as fh, ProcessPoolExecutor(args.workers) as pool:
        futs = [pool.submit(_work, t) for t in tasks]
        for i, f in enumerate(as_completed(futs)):
            r = f.result()
            records.append(r)
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            if (i + 1) % 100 == 0:
                print(f"  {i + 1}/{len(tasks)}  {time.time() - t0:.0f}s", flush=True)
    summary = summarize(records)
    order = [c[0] for c in configs(True, True)]
    summary = {k: summary[k] for k in order if k in summary}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    errors = [r for r in records if "error" in r]
    if errors:
        print(f"{len(errors)} errors, e.g. {errors[0]['config']} {errors[0]['id']}: {errors[0]['error']}")
    print_table(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
