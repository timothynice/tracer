"""`python -m bench <command>` — generate | run | compare | sweep | report."""
from __future__ import annotations

import argparse
import itertools
import json
import shutil
import sys
from pathlib import Path

from bench.config import CLASSES

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus"
BASELINES = HERE / "baselines"
REPORTS = HERE / "reports"


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


def cmd_generate(args) -> int:
    from bench.synth import generate

    items = generate(Path(args.corpus), seed=args.seed)
    print(f"generated {len(items)} items under {args.corpus}")
    return 0


def cmd_run(args) -> int:
    from bench.corpus import load_corpus
    from bench.runner import run

    items = load_corpus(Path(args.corpus), classes=_csv(args.classes) or None, ids=_csv(args.ids) or None)
    if not items:
        print("no corpus items matched", file=sys.stderr)
        return 2
    params = json.loads(args.params) if args.params else {}
    engines = _csv(args.engines)
    out_dir = Path(args.out) if args.out else None
    results_path = run(items, engines, params=params, label=args.label, out_dir=out_dir, media=not args.no_media)
    results = json.loads(results_path.read_text())
    _print_summary(results)
    print(f"\nresults: {results_path}\nreport:  {results_path.with_name('index.html')}")
    if args.update_baseline:
        BASELINES.mkdir(exist_ok=True)
        for eid in engines:
            single = {**results, "engines": {eid: results["engines"][eid]},
                      "items": [r for r in results["items"] if r["engine"] == eid],
                      "summary": {eid: results["summary"].get(eid, {})}}
            target = BASELINES / f"{eid}.json"
            target.write_text(json.dumps(single, indent=1))
            print(f"baseline updated: {target}")
    return 0


def _print_summary(results: dict) -> None:
    cols = ("score", "fidelity", "ssim", "delta_e_mean", "edge_f1", "banding_index", "paths", "elapsed_ms")
    print(f"\n{'engine':>10} {'class':<9}" + "".join(f"{c:>14}" for c in cols))
    for engine, by_cls in sorted(results["summary"].items()):
        for cls in list(CLASSES) + sorted(set(by_cls) - set(CLASSES)):
            s = by_cls.get(cls)
            if not s:
                continue
            row = "".join(f"{s[c]:>14.3f}" if isinstance(s.get(c), float) else f"{str(s.get(c, '-')):>14}" for c in cols)
            err = f"  ({s['errors']} errors)" if s.get("errors") else ""
            print(f"{engine:>10} {cls:<9}{row}{err}")


def cmd_compare(args) -> int:
    from bench.runner import compare, load_results

    lines, regressed = compare(load_results(Path(args.baseline)), load_results(Path(args.candidate)), args.tolerance, args.metric)
    print("\n".join(lines))
    print("\nREGRESSED" if regressed else "\nok")
    return 1 if regressed else 0


def _parse_grid(spec: str) -> tuple[str, list]:
    """name=lo:hi[:step] or name=a,b,c"""
    name, _, values = spec.partition("=")
    if ":" in values:
        parts = [float(p) for p in values.split(":")]
        lo, hi = parts[0], parts[1]
        step = parts[2] if len(parts) > 2 else 1.0
        grid = []
        v = lo
        while v <= hi + 1e-9:
            grid.append(int(v) if float(v).is_integer() and step.is_integer() else round(v, 6))
            v += step
        return name, grid
    return name, [_coerce(v) for v in values.split(",")]


def _coerce(v: str):
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return {"true": True, "false": False}.get(v.lower(), v)


def cmd_sweep(args) -> int:
    from bench.corpus import load_corpus
    from bench.runner import run

    items = load_corpus(Path(args.corpus), classes=_csv(args.classes) or None)
    grids = dict(_parse_grid(s) for s in args.param)
    names = list(grids)
    combos = list(itertools.product(*(grids[n] for n in names)))
    root = REPORTS / f"sweep-{args.engine}-{args.label}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    rows = []
    print(f"sweeping {len(combos)} combinations of {names} for {args.engine} over {len(items)} items")
    for i, combo in enumerate(combos):
        params = dict(zip(names, combo))
        out_dir = root / f"{i:03d}"
        results_path = run(items, [args.engine], params={args.engine: params}, label=f"sweep-{i}", out_dir=out_dir, media=False)
        summary = json.loads(results_path.read_text())["summary"][args.engine]
        rows.append({"params": params, "summary": summary, "dir": str(out_dir)})
        overall = sum(s["score"] for s in summary.values() if "score" in s) / max(1, len(summary))
        print(f"  {i:03d} {params}  mean score {overall:.4f}")
    (root / "sweep.json").write_text(json.dumps({"engine": args.engine, "grid": grids, "rows": rows}, indent=1))

    print("\nbest per class:")
    classes = sorted({c for r in rows for c in r["summary"]})
    for cls in classes:
        best = max(rows, key=lambda r: r["summary"].get(cls, {}).get("score", float("-inf")))
        print(f"  {cls:<9} score {best['summary'][cls]['score']:.4f}  {best['params']}")
    print(f"\nsweep results: {root / 'sweep.json'}")
    return 0


def cmd_report(args) -> int:
    from bench.report import write_html
    from bench.runner import load_results

    results_path = Path(args.results)
    media_path = results_path.with_name("media.json")
    media = json.loads(media_path.read_text()) if media_path.exists() else None
    out = results_path.with_name("index.html")
    write_html(load_results(results_path), out, media)
    print(f"report: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench", description="Vexel Bench: fidelity evaluation for tracing engines")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="(re)build the synthetic corpus")
    g.add_argument("--corpus", default=str(CORPUS))
    g.add_argument("--seed", type=int, default=1234)
    g.set_defaults(fn=cmd_generate)

    r = sub.add_parser("run", help="score engines over the corpus")
    r.add_argument("--engines", default="potrace,vtracer")
    r.add_argument("--classes", help="comma list, default all")
    r.add_argument("--ids", help="comma list of item ids")
    r.add_argument("--params", help='JSON keyed by engine id, e.g. \'{"vtracer": {"color_precision": 8}}\'')
    r.add_argument("--label", default="run")
    r.add_argument("--out", help="output directory (default bench/reports/<ts>-<label>)")
    r.add_argument("--corpus", default=str(CORPUS))
    r.add_argument("--no-media", action="store_true", help="skip thumbnails (faster)")
    r.add_argument("--update-baseline", action="store_true", help="write bench/baselines/<engine>.json")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("compare", help="diff two results.json files")
    c.add_argument("baseline")
    c.add_argument("candidate")
    c.add_argument("--tolerance", type=float, default=0.01)
    c.add_argument("--metric", default="score")
    c.set_defaults(fn=cmd_compare)

    s = sub.add_parser("sweep", help="grid-search parameters for one engine")
    s.add_argument("--engine", required=True)
    s.add_argument("--param", action="append", required=True, help="name=lo:hi[:step] or name=a,b,c (repeatable)")
    s.add_argument("--classes")
    s.add_argument("--label", default="sweep")
    s.add_argument("--corpus", default=str(CORPUS))
    s.set_defaults(fn=cmd_sweep)

    rp = sub.add_parser("report", help="regenerate index.html from a results.json")
    rp.add_argument("results")
    rp.set_defaults(fn=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
