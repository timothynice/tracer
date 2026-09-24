"""Head-to-head: Vexel against Potrace, VTracer, AutoTrace and ImageTracer.js.

    RAYON_NUM_THREADS=1 IMAGETRACER_DIR=<npm dir> \\
      python -m bench.headtohead run --corpus bench/heldout --out OUT/heldout --workers 3
    python -m bench.headtohead run --corpus bench/corpus  --out OUT/corpus  --workers 3
    python -m bench.headtohead summarize OUT/heldout OUT/corpus --out OUT
    python -m bench.headtohead sheets OUT/summary.json OUT/heldout OUT/corpus --out OUT/sheets

`run` scores every configuration in `bench.adapters.CONFIGS` (each competitor's
defaults and its documented presets; Vexel's defaults and Auto) on every item,
with the standard bench metrics (`bench.metrics.all_metrics`: the composite
score, ΔE, SSIM, edge F1, outline error against the vector truth, the artifact
scorecard, size and time). An item is one task, so the truth's 8x render is
made once per item for all configurations. Records stream to records.jsonl
(the run resumes from it), and each configuration gets a results.json in the
runner's format under OUT/<config>/.

`summarize` picks each competitor's best configuration by mean composite
score over the *first* directory (the held-out corpus) and applies that choice
unchanged to the others; Vexel is never tuned. It writes summary.json (every
number the tables use) and tables.md (per engine, per class, per degradation,
win counts, pairwise). `sheets` renders comparison sheets and failure-mode
crops from summary.json, by a stated rule (see `cmd_sheets`).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RECORD_KEYS = ("score", "fidelity", "smoothness", "economy", "delta_e_mean", "delta_e_p95", "ssim", "edge_f1",
               "alpha_mae", "banding_index", "seam_ppm", "outline_px", "outline_p99_px", "junction_px",
               "artifact_index", "pinholes", "hole_clusters", "slivers", "degenerate", "thin_strokes",
               "wobble_deg_100px", "inflections", "radius_inconsistent", "rect_bowed", "rect_skewed",
               "paths", "elements", "nodes", "bytes", "elapsed_ms")
LOWER = {"delta_e_mean", "delta_e_p95", "alpha_mae", "banding_index", "seam_ppm", "outline_px", "outline_p99_px",
         "junction_px", "artifact_index", "pinholes", "hole_clusters", "slivers", "degenerate", "thin_strokes",
         "wobble_deg_100px", "inflections", "radius_inconsistent", "rect_bowed", "rect_skewed", "paths",
         "elements", "nodes", "bytes", "elapsed_ms"}


# ---------------------------------------------------------------- run

def _item_task(args: tuple) -> list[dict]:
    entry, root, names = args
    from bench.adapters import CONFIGS, load
    from bench.config import DEFAULT_WEIGHTS
    from bench.corpus import Item
    from bench.runner import score_item

    load()
    item = Item.from_manifest(entry, Path(root))
    out = []
    for name in names:
        eid, params = CONFIGS[name]
        t = time.perf_counter()
        rec = score_item(item, eid, params, DEFAULT_WEIGHTS, None)
        rec["config"], rec["engine_id"], rec["engine"] = name, eid, name
        rec["tags"] = item.tags
        rec["wall_s"] = time.perf_counter() - t
        if "metrics" in rec:
            rec["metrics"] = {k: (float(v) if isinstance(v, (int, float, np.floating, np.integer)) and not isinstance(v, bool)
                                  else v) for k, v in rec["metrics"].items()}
        out.append(rec)
    return out


def cmd_run(args) -> int:
    import yaml

    from bench.adapters import CONFIGS, load
    from bench.runner import summarize

    missing = load()
    root = Path(args.corpus)
    entries = yaml.safe_load((root / "manifest.yaml").read_text())["items"]
    names = list(CONFIGS) if not args.configs else [c.strip() for c in args.configs.split(",") if c.strip()]
    unknown = [n for n in names if n not in CONFIGS]
    if unknown:
        print(f"unknown configurations: {unknown}", file=sys.stderr)
        return 2
    skipped = [n for n in names if CONFIGS[n][0] in missing]
    for n in skipped:
        print(f"skipping {n}: {missing[CONFIGS[n][0]]}", file=sys.stderr)
    names = [n for n in names if n not in skipped]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rec_path = out / "records.jsonl"
    done: dict[str, set[str]] = defaultdict(set)
    records: list[dict] = []
    if rec_path.exists():
        for line in rec_path.read_text().splitlines():
            r = json.loads(line)
            if r["config"] in names:
                records.append(r)
                done[r["id"]].add(r["config"])
    tasks = []
    for e in sorted(entries, key=lambda e: -e["width"] * e["height"]):
        todo = [n for n in names if n not in done[e["id"]]]
        if todo:
            tasks.append((e, str(root), todo))
    print(f"{len(names)} configs x {len(entries)} items: {sum(len(t[2]) for t in tasks)} scorings in {len(tasks)} "
          f"item tasks on {args.workers} workers (RAYON_NUM_THREADS={os.environ.get('RAYON_NUM_THREADS', '')})",
          flush=True)
    t0 = time.time()
    with rec_path.open("a") as fh, ProcessPoolExecutor(args.workers) as pool:
        futs = [pool.submit(_item_task, t) for t in tasks]
        for i, f in enumerate(as_completed(futs)):
            for r in f.result():
                records.append(r)
                fh.write(json.dumps(r) + "\n")
            fh.flush()
            if (i + 1) % 10 == 0 or i + 1 == len(futs):
                print(f"  {i + 1}/{len(futs)} items  {time.time() - t0:.0f}s", flush=True)
    by_cfg: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_cfg[r["config"]].append(r)
    from studi0trace.engines import registry

    for name in names:
        eid, params = CONFIGS[name]
        recs = sorted(by_cfg[name], key=lambda r: r["id"])
        results = {
            "label": name, "corpus": str(root), "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "engines": {name: {"engine": eid, "params": registry.get(eid).Params.model_validate(params).model_dump()}},
            "env": {"RAYON_NUM_THREADS": os.environ.get("RAYON_NUM_THREADS"), "workers": args.workers},
            "items": recs, "summary": summarize(recs),
        }
        d = out / name
        d.mkdir(exist_ok=True)
        (d / "results.json").write_text(json.dumps(results, indent=1))
    errors = [r for r in records if "error" in r]
    print(f"wrote {len(names)} results.json under {out}; {len(errors)} errors"
          + (f", e.g. {errors[0]['config']} {errors[0]['id']}: {errors[0]['error']}" if errors else ""))
    return 0


# ---------------------------------------------------------------- summarize

def _load(d: Path) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = defaultdict(list)
    for line in (d / "records.jsonl").read_text().splitlines():
        r = json.loads(line)
        by[r["config"]].append(r)
    return by


def _stats(recs: list[dict], key: str) -> dict:
    vals = [r["metrics"][key] for r in recs if "metrics" in r and r["metrics"].get(key) is not None]
    if not vals:
        return {"mean": None, "median": None, "n": 0}
    a = np.asarray(vals, float)
    return {"mean": float(a.mean()), "median": float(np.median(a)), "n": int(a.size)}


def _variant(item_id: str) -> str:
    if item_id.endswith("-ds"):
        return "downsampled"
    if item_id.endswith("-q75"):
        return "jpeg-q75"
    return "clean"


def best_configs(by: dict[str, list[dict]]) -> dict[str, dict]:
    """engine id -> {"default", "best", "scores": {config: mean score}} over the given records."""
    from bench.adapters import CONFIGS, FAMILIES

    out = {}
    for eid, names in FAMILIES.items():
        names = [n for n in names if n in by]
        if not names:
            continue
        scores = {n: _stats(by[n], "score")["mean"] for n in names}
        errors = {n: sum("error" in r for r in by[n]) for n in names}
        best = max(names, key=lambda n: (scores[n] if scores[n] is not None and errors[n] == 0 else -1.0))
        out[eid] = {"default": eid if eid in names else names[0], "best": best, "scores": scores, "errors": errors,
                    "tunable": eid not in ("vexel", "vexel-auto", "potrace"),
                    "params": {n: CONFIGS[n][1] for n in names}}
    return out


def _wins(by: dict[str, list[dict]], lineup: list[str], key: str, tol: float) -> dict:
    """Per config: items where it alone is lowest on `key`, and items where it ties for lowest (within tol)."""
    ids = sorted(set.intersection(*[{r["id"] for r in by[c] if "metrics" in r and r["metrics"].get(key) is not None}
                                    for c in lineup]))
    val = {c: {r["id"]: r["metrics"][key] for r in by[c] if "metrics" in r} for c in lineup}
    wins = {c: 0 for c in lineup}
    ties = {c: 0 for c in lineup}
    for i in ids:
        lo = min(val[c][i] for c in lineup)
        at = [c for c in lineup if val[c][i] <= lo + tol]
        for c in at:
            if len(at) == 1:
                wins[c] += 1
            else:
                ties[c] += 1
    return {"items": len(ids), "wins": wins, "ties": ties}


TABLE = ("score", "delta_e_mean", "ssim", "edge_f1", "outline_px", "artifact_index", "pinholes", "slivers",
         "wobble_deg_100px", "inflections", "radius_inconsistent", "banding_index", "seam_ppm", "paths", "bytes",
         "elapsed_ms")
TOL = {"delta_e_mean": 0.005, "outline_px": 0.005, "artifact_index": 0.05}


def summarize_dirs(dirs: list[Path]) -> dict:
    loaded = {d.name: _load(d) for d in dirs}
    first = dirs[0].name
    choice = best_configs(loaded[first])
    report: dict = {"selected_on": first, "choice": choice, "corpora": {}}
    for name, by in loaded.items():
        reported = []
        for eid, c in choice.items():
            reported.append(c["default"])
            if c["tunable"] and c["best"] != c["default"]:
                reported.append(c["best"])
        reported = [c for c in reported if c in by]
        classes = sorted({r["cls"] for recs in by.values() for r in recs})
        corp: dict = {"items": len({r["id"] for recs in by.values() for r in recs}), "configs": {}}
        for cfg in sorted(by):
            recs = by[cfg]
            corp["configs"][cfg] = {
                "engine": recs[0]["engine_id"],
                "errors": sum("error" in r for r in recs),
                "all": {k: _stats(recs, k) for k in TABLE},
                "by_class": {cls: {k: _stats([r for r in recs if r["cls"] == cls], k) for k in TABLE} for cls in classes},
                "by_variant": {v: {k: _stats([r for r in recs if _variant(r["id"]) == v], k) for k in TABLE}
                               for v in ("clean", "downsampled", "jpeg-q75")},
                "clean_share": float(np.mean([_is_clean(r) for r in recs if "metrics" in r])) if recs else None,
            }
        corp["best_on_this_corpus"] = {eid: c["best"] for eid, c in best_configs(by).items()}
        comp = [choice[e]["best"] for e in ("potrace", "vtracer", "autotrace", "imagetracer") if e in choice]
        # Win counts twice: against every competitor, and against the colour tracers only, since
        # Potrace's 1-bit output is "clean" on the artifact index largely by drawing little.
        corp["wins"], corp["wins_colour"] = {}, {}
        for vx in ("vexel", "vexel-auto"):
            if vx not in by:
                continue
            lineup = [vx] + [c for c in comp if c in by]
            corp["wins"][vx] = {k: _wins(by, lineup, k, TOL[k]) for k in ("delta_e_mean", "outline_px", "artifact_index")}
            colour = [c for c in lineup if not c.startswith("potrace")]
            corp["wins_colour"][vx] = {k: _wins(by, colour, k, TOL[k]) for k in ("delta_e_mean", "outline_px", "artifact_index")}
        # Pairwise: Vexel (defaults / Auto) against every reported competitor configuration, item by item.
        corp["pairwise"] = {}
        for vx in ("vexel", "vexel-auto"):
            if vx not in by:
                continue
            mine = {r["id"]: r["metrics"] for r in by[vx] if "metrics" in r}
            corp["pairwise"][vx] = {}
            for cfg in [c for c in reported if c not in ("vexel", "vexel-auto")]:
                theirs = {r["id"]: r["metrics"] for r in by[cfg] if "metrics" in r}
                row = {}
                for k in ("score", "delta_e_mean", "outline_px", "artifact_index"):
                    tol = TOL.get(k, 0.0005)
                    sign = 1.0 if k == "score" else -1.0
                    ids = [i for i in mine if i in theirs and mine[i].get(k) is not None and theirs[i].get(k) is not None]
                    d = [sign * (mine[i][k] - theirs[i][k]) for i in ids]
                    row[k] = {"items": len(ids), "better": sum(x > tol for x in d), "tied": sum(abs(x) <= tol for x in d),
                              "worse": sum(x < -tol for x in d)}
                corp["pairwise"][vx][cfg] = row
        corp["reported"] = reported
        corp["classes"] = classes
        corp["per_item"] = {cfg: {r["id"]: ({k: r["metrics"].get(k) for k in RECORD_KEYS} | {"cls": r["cls"]}
                                            | ({"pick": r["notes"]["pick"]} if r.get("notes") else {}))
                                  if "metrics" in r else {"error": r["error"], "cls": r["cls"]}
                                  for r in by[cfg]} for cfg in reported}
        picks = defaultdict(int)
        for r in by.get("vexel-auto", []):
            if r.get("notes"):
                picks[r["notes"]["pick"]] += 1
        corp["auto_picks"] = dict(picks)
        report["corpora"][name] = corp
    return report


def _is_clean(r: dict) -> bool:
    from studi0trace.imaging.quality import is_clean

    return bool(is_clean(r["metrics"]))


def _fmt(v, key: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if key in ("paths", "pinholes", "slivers", "inflections", "radius_inconsistent"):
        return f"{v:.1f}" if abs(v) < 100 else f"{v:,.0f}"
    if key == "bytes":
        return f"{v / 1024:,.1f} KB"
    if key == "elapsed_ms":
        return f"{v:,.0f}"
    if key in ("score", "ssim", "edge_f1"):
        return f"{v:.3f}"
    if key == "seam_ppm":
        return f"{v:,.0f}"
    return f"{v:.2f}"


LABEL = {"score": "score", "delta_e_mean": "ΔE", "ssim": "SSIM", "edge_f1": "edge F1", "outline_px": "outline px",
         "artifact_index": "artifact idx", "pinholes": "pinholes", "slivers": "slivers", "wobble_deg_100px": "wobble",
         "inflections": "inflections", "radius_inconsistent": "uneven radii", "banding_index": "banding",
         "seam_ppm": "seam ppm", "paths": "paths", "bytes": "size", "elapsed_ms": "ms"}


def markdown(report: dict) -> str:
    L: list[str] = []
    ch = report["choice"]
    L.append(f"## Configurations and the best-preset choice (selected on `{report['selected_on']}`, mean composite score)\n")
    L.append("| engine | configuration | mean score (held-out) | chosen |")
    L.append("|---|---|---|---|")
    for eid, c in ch.items():
        for n, s in sorted(c["scores"].items(), key=lambda t: -(t[1] or -1)):
            tag = "default" if n == c["default"] else ""
            if n == c["best"] and c["tunable"]:
                tag = (tag + ", best" if tag else "best")
            L.append(f"| {eid} | `{n}` {json.dumps(c['params'][n]) if c['params'][n] else ''} | {_fmt(s, 'score')} "
                     f"{'(' + str(c['errors'][n]) + ' errors)' if c['errors'][n] else ''} | {tag} |")
    for name, corp in report["corpora"].items():
        L.append(f"\n## {name} ({corp['items']} items)\n")
        rep = corp["reported"]
        for stat in ("mean", "median"):
            L.append(f"\n### All items, {stat}\n")
            L.append("| config | " + " | ".join(LABEL[k] for k in TABLE) + " | clean share |")
            L.append("|---|" + "---|" * (len(TABLE) + 1))
            for cfg in rep:
                c = corp["configs"][cfg]
                share = f"{c['clean_share']:.0%}" if c["clean_share"] is not None else "–"
                L.append(f"| `{cfg}` | " + " | ".join(_fmt(c["all"][k][stat], k) for k in TABLE) + f" | {share} |")
        keys = ("score", "delta_e_mean", "outline_px", "artifact_index", "paths")
        for group, label in (("by_class", "class"), ("by_variant", "degradation")):
            groups = corp["classes"] if group == "by_class" else ("clean", "downsampled", "jpeg-q75")
            L.append(f"\n### Per {label} (mean / median)\n")
            L.append(f"| {label} | config | " + " | ".join(LABEL[k] for k in keys) + " |")
            L.append("|---|---|" + "---|" * len(keys))
            for g in groups:
                for cfg in rep:
                    s = corp["configs"][cfg][group].get(g)
                    if not s or s["score"]["n"] == 0:
                        continue
                    L.append(f"| {g} | `{cfg}` | " + " | ".join(f"{_fmt(s[k]['mean'], k)} / {_fmt(s[k]['median'], k)}"
                                                               for k in keys) + " |")
        for (vx, w), scope in [(kv, "every competitor") for kv in corp["wins"].items()] + \
                              [(kv, "the colour tracers (Potrace left out)") for kv in corp.get("wins_colour", {}).items()]:
            L.append(f"\n### Win counts: `{vx}` against {scope}, each at its best configuration\n")
            L.append("Lowest value on the item; a tie (within "
                     + ", ".join(f"{LABEL[k]} {t}" for k, t in TOL.items()) + ") is counted separately.\n")
            L.append("| metric | items | " + " | ".join(f"`{c}`" for c in w["delta_e_mean"]["wins"]) + " |")
            L.append("|---|---|" + "---|" * len(w["delta_e_mean"]["wins"]))
            for k, v in w.items():
                L.append(f"| lowest {LABEL[k]} | {v['items']} | "
                         + " | ".join(f"{v['wins'][c]}" + (f" (+{v['ties'][c]} tied)" if v["ties"][c] else "")
                                      for c in v["wins"]) + " |")
        for vx, rows in corp.get("pairwise", {}).items():
            L.append(f"\n### Pairwise: `{vx}` against each configuration, items where Vexel is better / tied / worse\n")
            L.append("| against | score | ΔE | outline px | artifact idx |")
            L.append("|---|---|---|---|---|")
            for cfg, row in rows.items():
                L.append(f"| `{cfg}` | " + " | ".join(f"{row[k]['better']} / {row[k]['tied']} / {row[k]['worse']}"
                                                       for k in ("score", "delta_e_mean", "outline_px", "artifact_index")) + " |")
        if corp["auto_picks"]:
            L.append("\nAuto's picks: " + ", ".join(f"{k} {v}" for k, v in sorted(corp["auto_picks"].items(), key=lambda t: -t[1])))
        diff = {e: b for e, b in corp["best_on_this_corpus"].items() if ch.get(e) and ch[e]["tunable"] and ch[e]["best"] != b}
        if name != report["selected_on"]:
            L.append("\nBest configuration on this corpus, where it differs from the held-out choice: "
                     + (", ".join(f"{e}: `{b}` (held-out chose `{ch[e]['best']}`)" for e, b in diff.items()) or "none"))
    return "\n".join(L) + "\n"


def cmd_summarize(args) -> int:
    report = summarize_dirs([Path(d) for d in args.dirs])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(report, indent=1))
    (out / "tables.md").write_text(markdown(report))
    print(f"wrote {out / 'summary.json'} and {out / 'tables.md'}")
    return 0


# ---------------------------------------------------------------- comparison sheets

SHEET_CROP = 96        # source px of the zoomed window on a comparison sheet
SHEET_ZOOM = 4
FAIL_CROP = 64         # and on a failure-mode crop
FAIL_ZOOM = 6
PANEL = SHEET_CROP * SHEET_ZOOM


def _corpus_root(run_dir: Path) -> Path:
    for p in sorted(run_dir.glob("*/results.json")):
        return Path(json.loads(p.read_text())["corpus"])
    raise SystemExit(f"no results.json under {run_dir}")


def _trace(item, cfg: str) -> str:
    from bench.adapters import CONFIGS, load
    from studi0trace.engines import registry
    from studi0trace.imaging.intake import load_upload

    load()
    eid, params = CONFIGS[cfg]
    engine = registry.get(eid)
    image = load_upload(item.png.read_bytes(), max_bytes=1 << 30, max_pixels=1 << 30)
    return engine.trace(image, engine.Params.model_validate(params)).svg


def _on(rgba: np.ndarray, backdrop: tuple[int, int, int]) -> np.ndarray:
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return (rgba[..., :3].astype(np.float32) * a + np.array(backdrop, np.float32) * (1 - a) + 0.5).astype(np.uint8)


def _window(score: np.ndarray, size: int, step: int = 8) -> tuple[int, int]:
    """Top-left (x, y) of the size x size window with the largest sum of `score`."""
    from scipy import ndimage

    h, w = score.shape
    box = ndimage.uniform_filter(score.astype(np.float64), size=size, mode="constant")
    best, at = -1.0, (0, 0)
    for y in range(size // 2, h - size // 2 + 1, step):
        for x in range(size // 2, w - size // 2 + 1, step):
            if box[y, x] > best:
                best, at = box[y, x], (x - size // 2, y - size // 2)
    return at


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _label(m: dict | None) -> str:
    if not m:
        return ""
    return (f"dE {m['delta_e_mean']:.2f}  outline {m['outline_px']:.2f}px  art {m['artifact_index']:.1f}  "
            f"{int(m['paths'])} paths" if m.get("outline_px") is not None else
            f"dE {m['delta_e_mean']:.2f}  art {m['artifact_index']:.1f}  {int(m['paths'])} paths")


def comparison_sheet(item, lineup: list[str], metrics: dict[str, dict], out: Path, note: str = "") -> dict:
    """source | each configuration: the whole image, then one window at SHEET_ZOOM x.

    The window is the SHEET_CROP px square where the configurations shown
    (Potrace excepted: it is 1-bit, and its colour error is everywhere) are
    furthest from the source on average: the per-pixel ΔE of each, summed."""
    from PIL import Image, ImageDraw

    from bench.metrics import delta_e_map
    from bench.raster import load_png, rasterize, to_rgb_on_white

    src = load_png(item.png)
    h, w = src.shape[:2]
    svgs = {c: _trace(item, c) for c in lineup}
    renders = {c: rasterize(svgs[c], w, h) for c in lineup}
    err = np.zeros((h, w))
    for c in lineup:
        if not c.startswith("potrace"):
            err += delta_e_map(to_rgb_on_white(src), to_rgb_on_white(renders[c]))
    x0, y0 = _window(err, SHEET_CROP)
    big = {c: rasterize(svgs[c], w * SHEET_ZOOM, h * SHEET_ZOOM) for c in lineup}
    src_big = np.asarray(Image.fromarray(src, "RGBA").resize((w * SHEET_ZOOM, h * SHEET_ZOOM), Image.Resampling.NEAREST))
    cols = ["source"] + lineup
    head, foot = 34, 44
    sheet = Image.new("RGB", (PANEL * len(cols), head + PANEL * 2 + foot), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    f, fs = _font(20), _font(15)
    for k, c in enumerate(cols):
        full = src if c == "source" else renders[c]
        zoom = src_big if c == "source" else big[c]
        whole = Image.fromarray(_on(full, (255, 255, 255))).resize((PANEL, PANEL), Image.Resampling.LANCZOS)
        crop = Image.fromarray(_on(zoom[y0 * SHEET_ZOOM:(y0 + SHEET_CROP) * SHEET_ZOOM,
                                        x0 * SHEET_ZOOM:(x0 + SHEET_CROP) * SHEET_ZOOM], (255, 255, 255)))
        sheet.paste(whole, (k * PANEL, head))
        sheet.paste(crop, (k * PANEL, head + PANEL))
        if c == "source":
            r = PANEL / w
            d.rectangle([x0 * r, head + y0 * r, (x0 + SHEET_CROP) * r, head + (y0 + SHEET_CROP) * r], outline=(230, 0, 90), width=2)
        d.text((k * PANEL + 8, 7), c, fill=(20, 20, 20), font=f)
        d.text((k * PANEL + 8, head + 2 * PANEL + 4), _label(metrics.get(c)) if c != "source" else item.id,
               fill=(60, 60, 60), font=fs)
        if c == "source" and note:
            d.text((k * PANEL + 8, head + 2 * PANEL + 23), note, fill=(150, 0, 60), font=fs)
        if k:
            d.line([(k * PANEL, 0), (k * PANEL, sheet.height)], fill=(200, 200, 200), width=1)
    d.line([(0, head + PANEL), (sheet.width, head + PANEL)], fill=(200, 200, 200), width=1)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, optimize=True)
    return {"path": str(out), "item": item.id, "crop": [x0, y0, SHEET_CROP, SHEET_CROP], "zoom": SHEET_ZOOM,
            "lineup": lineup}


FAILURES = {  # scorecard/metric key -> (what it looks like, backdrop the defect shows on)
    "pinholes": ("pinholes and seams: the backdrop showing through the artwork", (24, 24, 24)),
    "slivers": ("slivers: hairline shapes left between neighbours", (24, 24, 24)),
    "wobble_deg_100px": ("wobble: edges that turn back and forth at pixel scale", (255, 255, 255)),
    "inflections": ("wavy curves: sign changes of curvature along smooth outlines", (255, 255, 255)),
    "banding_index": ("posterised gradients: a smooth ramp cut into flat steps", (255, 255, 255)),
    "delta_e_mean": ("lost detail / wrong colour: the largest colour error", (255, 255, 255)),
}


def _defect_map(kind: str, svg: str, src: np.ndarray, out: np.ndarray) -> np.ndarray:
    from scipy import ndimage

    from bench.artifacts import scorecard
    from bench.metrics import delta_e_map, smooth_mask
    from bench.raster import luminance, to_rgb_on_white

    h, w = src.shape[:2]
    score = np.zeros((h, w))
    if kind in ("delta_e_mean",):
        return delta_e_map(to_rgb_on_white(src), to_rgb_on_white(out))
    if kind == "banding_index":
        a, b = to_rgb_on_white(src), to_rgb_on_white(out)
        ex = np.maximum(np.abs(ndimage.laplace(luminance(b))) - np.abs(ndimage.laplace(luminance(a))), 0)
        return ex * smooth_mask(a)
    card = scorecard(svg, src, detail=True)
    pts = {"pinholes": [(c["x"], c["y"], 1.0 + c["deficit_px"]) for c in card["_clusters"] if c["pinhole"]],
           "slivers": [(x, y, 1.0) for x, y, *_ in card["_slivers_at"]],
           "wobble_deg_100px": [(x, y, v) for x, y, v in card["_wobble_at"]],
           "inflections": [(x, y, 1.0) for x, y in card["_flips_at"]]}[kind]
    for x, y, v in pts:
        score[min(h - 1, max(0, int(y))), min(w - 1, max(0, int(x)))] += v
    return score


def failure_crop(item, cfg: str, ref: str, kind: str, metrics: dict[str, dict], out: Path) -> dict:
    """source | competitor | Vexel, FAIL_CROP px at FAIL_ZOOM x, on the window where `kind` is densest."""
    from PIL import Image, ImageDraw

    from bench.raster import load_png, rasterize

    src = load_png(item.png)
    h, w = src.shape[:2]
    svg = _trace(item, cfg)
    x0, y0 = _window(_defect_map(kind, svg, src, rasterize(svg, w, h)), FAIL_CROP, step=4)
    what, backdrop = FAILURES[kind]
    z = FAIL_ZOOM
    panels = [("source", np.asarray(Image.fromarray(src, "RGBA").resize((w * z, h * z), Image.Resampling.NEAREST))),
              (cfg, rasterize(svg, w * z, h * z)), (ref, rasterize(_trace(item, ref), w * z, h * z))]
    P = FAIL_CROP * z
    head, foot = 34, 44
    sheet = Image.new("RGB", (P * 3, head + P + foot), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    f, fs = _font(20), _font(15)
    for k, (name, img) in enumerate(panels):
        crop = img[y0 * z:(y0 + FAIL_CROP) * z, x0 * z:(x0 + FAIL_CROP) * z]
        sheet.paste(Image.fromarray(_on(crop, backdrop)), (k * P, head))
        d.text((k * P + 8, 7), name, fill=(20, 20, 20), font=f)
        d.text((k * P + 8, head + P + 4), _label(metrics.get(name)) if name != "source" else f"{item.id} @ ({x0},{y0})",
               fill=(60, 60, 60), font=fs)
    d.text((8, head + P + 23), what, fill=(150, 0, 60), font=fs)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, optimize=True)
    return {"path": str(out), "item": item.id, "config": cfg, "kind": kind, "what": what,
            "crop": [x0, y0, FAIL_CROP, FAIL_CROP], "zoom": z}


def cmd_sheets(args) -> int:
    import random

    from bench.corpus import load_corpus

    report = json.loads(Path(args.summary).read_text())
    ch = report["choice"]
    comp = [ch[e]["best"] for e in ("potrace", "vtracer", "autotrace", "imagetracer") if e in ch]
    lineup = ["vexel", "vexel-auto"] + comp
    out = Path(args.out)
    made: list[dict] = []
    rng = random.Random(args.seed)
    for run_dir in [Path(d) for d in args.dirs]:
        name = run_dir.name
        corp = report["corpora"][name]
        items = {i.id: i for i in load_corpus(_corpus_root(run_dir))}
        per = corp["per_item"]
        vx = {i: m for i, m in per["vexel"].items() if "score" in m}
        picks: list[tuple[str, str]] = []
        if name == report["selected_on"]:
            for cls in corp["classes"]:
                ids = sorted((i for i, m in vx.items() if m["cls"] == cls), key=lambda i: (vx[i]["score"], i))
                picks += [(ids[0], f"Vexel's worst in {cls}"), (ids[len(ids) // 2], f"Vexel's median in {cls}")]
            for key, why in (("outline_px", "Vexel's largest outline error"), ("artifact_index", "Vexel's largest artifact index")):
                cand = [i for i in vx if vx[i].get(key) is not None]
                picks.append((max(sorted(cand), key=lambda i: vx[i][key]), why))
            rest = sorted(set(vx) - {p for p, _ in picks})
            picks += [(i, "random") for i in rng.sample(rest, min(2, len(rest)))]
        else:
            ids = sorted(vx, key=lambda i: (vx[i]["score"], i))
            picks += [(ids[0], "Vexel's worst on the dev corpus")]
            picks += [(rng.choice(sorted(set(vx) - {ids[0]})), "random, dev corpus")]
        seen: set[str] = set()
        for iid, why in picks:
            if iid in seen:
                continue
            seen.add(iid)
            mets = {c: per[c].get(iid) for c in lineup if c in per}
            slug = iid.replace("/", "__")
            made.append(comparison_sheet(items[iid], lineup, mets, out / f"{name}__{slug}.png", note=why) | {"why": why,
                                                                                                             "corpus": name})
            print(made[-1]["path"], flush=True)
        if name != report["selected_on"]:
            continue
        # Failure modes: for each competitor, the two defects it has most in excess of Vexel's (mean over
        # the corpus, as a ratio), each shown on the item where it is largest, cropped where it is densest.
        vmeans = corp["configs"]["vexel"]["all"]
        for cfg in comp:
            cm = corp["configs"][cfg]["all"]
            excess = []
            for kind in FAILURES:
                c, v = cm[kind]["mean"], vmeans[kind]["mean"]
                if c is None or v is None or c <= v:
                    continue
                excess.append(((c + 1e-3) / (v + 1e-3), kind))
            for _ratio, kind in sorted(excess, reverse=True)[:2]:
                cand = {i: m for i, m in per[cfg].items() if m.get(kind) is not None}
                iid = max(sorted(cand), key=lambda i: cand[i][kind])
                mets = {cfg: per[cfg].get(iid), "vexel": per["vexel"].get(iid)}
                made.append(failure_crop(items[iid], cfg, "vexel", kind, mets,
                                         out / f"failure__{cfg}__{kind}.png") | {"corpus": name})
                print(made[-1]["path"], flush=True)
    (out / "sheets.json").write_text(json.dumps(made, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.headtohead", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--corpus", default=str(HERE / "heldout"))
    r.add_argument("--out", required=True)
    r.add_argument("--configs", help="comma list of bench.adapters.CONFIGS names (default all)")
    r.add_argument("--workers", type=int, default=3)
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("summarize")
    s.add_argument("dirs", nargs="+", help="run directories; the first is the one the best presets are chosen on")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_summarize)
    sh = sub.add_parser("sheets", help="comparison sheets and failure-mode crops (PNG)")
    sh.add_argument("summary", help="summary.json from `summarize`")
    sh.add_argument("dirs", nargs="+", help="the run directories summarize was given, same order")
    sh.add_argument("--out", required=True)
    sh.add_argument("--seed", type=int, default=20260924)
    sh.set_defaults(fn=cmd_sheets)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
