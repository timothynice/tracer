"""Read a `bench.presets_eval` run and answer: which preset is artifact-prone on
which class, and which one parameter of it does the damage.

    python -m bench.presets_report DIR [--items] [--write-details]

The ablation table reads, for every preset P and each of its parameters k,
how much of P's artifact count goes away when k alone is put back to the
default (P−k): the parameter with the largest drop is the one causing it.
The forward table reads what that parameter does on its own over the
defaults (+k). Only items present in every compared configuration count.

`auto` is not a traced configuration: for each item it is whichever
candidate's record `studi0trace.auto.choose` picks. `--write-details` writes
the preset list's measured `detail` lines (`detail_lines`) into
`studi0trace/engines/preset_details.json`.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from studi0trace.imaging.quality import is_clean

CLASSES = ("ALL", "logo", "flat", "gradient", "shadow")
REAL = "real"
KEYS = ("score", "delta_e_mean", "seam_ppm", "pinholes", "hole_clusters", "slivers", "degenerate", "thin_strokes",
        "wobble_deg_100px", "inflections", "radius_inconsistent", "rect_bowed", "paths", "artifact_index")
SHORT = {"delta_e_mean": "dE", "seam_ppm": "seam", "hole_clusters": "holeC", "degenerate": "degen",
         "thin_strokes": "thinS", "wobble_deg_100px": "wobble", "inflections": "infl", "radius_inconsistent": "radius",
         "rect_bowed": "bowed", "artifact_index": "ART", "pinholes": "pinh", "slivers": "sliv"}


FLAGS = {  # the four kinds of visible defect; clean is none of them (quality.is_clean)
    "hole": lambda m: m["pinholes"] >= 1,
    "sliver": lambda m: m["slivers"] + m["degenerate"] + m["thin_strokes"] >= 1,
    "wobble": lambda m: m["wobble_deg_100px"] >= 25.0,
    "rect": lambda m: m["radius_inconsistent"] + m["rect_bowed"] + m.get("rect_skewed", 0) >= 1,
}


def auto_records(records: list[dict]) -> list[dict]:
    """One `auto` record per item: the record of the candidate Auto picks from
    the candidates' own records, with `pick` naming it. Items missing any
    candidate are left out, so Auto is never scored on a partial choice."""
    from studi0trace.auto import Scored, choose
    from studi0trace.engines.presets import auto_candidates

    cands = [p.id for p in auto_candidates()]
    by: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in records:
        if "error" not in r and r["config"] in cands:
            by[r["id"]][r["config"]] = r
    out = []
    for item, rs in by.items():
        if len(rs) != len(cands):
            continue
        pick, _why = choose([Scored(c, rs[c]["metrics"]["delta_e_mean"], rs[c]["metrics"]["edge_f1"],
                                    rs[c]["metrics"]["artifact_index"], int(rs[c]["metrics"]["elements"])) for c in cands])
        out.append({**rs[pick.id], "config": "auto", "pick": pick.id})
    return out


def detail_lines(records: list[dict]) -> tuple[dict[str, str], dict]:
    """The `detail` line of every preset, from records over the corpus: mean
    ΔE, the median number of shapes, and the share of images traced clean
    (`quality.is_clean`: no pinhole, sliver, wobbly edge or uneven rect)."""
    from studi0trace.engines.presets import all_presets

    by: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if "error" not in r:
            by[r["config"]].append(r)
    lines: dict[str, str] = {}
    numbers: dict[str, dict] = {}
    for p in all_presets():
        rs = by.get(p.id)
        if not rs:
            continue
        ms = [r["metrics"] for r in rs]
        de = float(np.mean([m["delta_e_mean"] for m in ms]))
        shapes = int(round(float(np.median([m["elements"] for m in ms]))))
        clean = 100.0 * sum(is_clean(m) for m in ms) / len(ms)
        lines[p.id] = f"ΔE {de:.2f} · {shapes} shapes · clean on {clean:.0f}% of {len(ms)} test images"
        numbers[p.id] = {"delta_e_mean": round(de, 4), "shapes_median": shapes, "clean_pct": round(clean, 1),
                         "artifact_index_mean": round(float(np.mean([m["artifact_index"] for m in ms])), 2),
                         "artifact_index_median": round(float(np.median([m["artifact_index"] for m in ms])), 2),
                         "items": len(ms)}
        if p.id == "auto":
            picks = Counter(r["pick"] for r in rs)
            numbers[p.id]["picks"] = {k: round(100.0 * v / len(rs), 1) for k, v in picks.most_common()}
    return lines, numbers


def write_details(records: list[dict], corpus_items: int, path: Path | None = None) -> Path:
    """Write the measured detail lines where `studi0trace.engines.presets` reads them."""
    from studi0trace.engines import presets as presets_mod
    from studi0trace.engines.vexel.engine import backend

    lines, numbers = detail_lines(records)
    missing = [p.id for p in presets_mod.all_presets() if p.id not in lines]
    if missing:
        raise SystemExit(f"no records for {', '.join(missing)}: run every preset over the corpus first")
    short = [k for k, v in numbers.items() if v["items"] < corpus_items]
    if short:
        print(f"warning: {', '.join(short)} measured on fewer than the {corpus_items} corpus items (errors?)")
    path = path or presets_mod.DETAILS_FILE
    doc = {
        "about": "Measured by `python -m bench.presets_eval --write-details`; do not edit by hand.",
        "measured": time.strftime("%Y-%m-%d"),
        "vexel_backend": os.environ.get("VEXEL_BACKEND") or backend(),
        "corpus_items": corpus_items,
        "lines": lines,
        "numbers": numbers,
    }
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {path}")
    for k, v in lines.items():
        print(f"  {k:<10} {v}")
    if "auto" in numbers:
        print("  auto picks: " + ", ".join(f"{k} {v:.0f}%" for k, v in numbers["auto"]["picks"].items()))
    return path


def flag_table(by, configs, items, classes=CLASSES + ("real",)):
    """Share of items (%) showing each kind of visible defect, and the share clean of all four."""
    print(f"\n{'config':<24}{'class':<9}{'n':>4}" + "".join(f"{k:>8}" for k in FLAGS) + f"{'CLEAN':>8}{'medART':>8}")
    for c in classes:
        for cfg in configs:
            rs = [by[cfg][i] for i in items if i in by[cfg] and c in cls_of(by[cfg][i])]
            if not rs:
                continue
            ms = [r["metrics"] for r in rs]
            hits = {k: 100.0 * sum(f(m) for m in ms) / len(ms) for k, f in FLAGS.items()}
            clean = 100.0 * sum(is_clean(m) for m in ms) / len(ms)
            med = float(np.median([m["artifact_index"] for m in ms]))
            print(f"{cfg:<24}{c:<9}{len(ms):>4}" + "".join(f"{hits[k]:>8.0f}" for k in FLAGS) + f"{clean:>8.0f}{med:>8.1f}")
        print()


def load(d: Path) -> dict[str, dict[str, dict]]:
    by: dict[str, dict[str, dict]] = defaultdict(dict)
    for line in (d / "records.jsonl").read_text().splitlines():
        r = json.loads(line)
        if "error" in r:
            continue
        by[r["config"]][r["id"]] = r
    return by


def cls_of(r: dict) -> list[str]:
    return ["ALL", r["cls"]] + ([REAL] if r["id"] in REAL_IDS else [])


REAL_IDS: set[str] = set()


def mean_table(by, configs, items, keys=KEYS):
    out = {}
    for cfg in configs:
        acc = defaultdict(lambda: defaultdict(list))
        for i in items:
            r = by[cfg].get(i)
            if r is None:
                continue
            for c in cls_of(r):
                for k in keys:
                    v = r["metrics"].get(k)
                    if v is not None:
                        acc[c][k].append(v)
        out[cfg] = {c: {k: float(np.mean(v)) for k, v in ks.items()} for c, ks in acc.items()}
    return out


def fmt(v, k):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return f"{'-':>8}"
    return f"{v:>8.3f}" if k in ("score", "delta_e_mean") else f"{v:>8.1f}"


def print_means(t, configs, classes=CLASSES + (REAL,)):
    for c in classes:
        print(f"\n{'config':<24}{'class':<9}" + "".join(f"{SHORT.get(k, k):>8}" for k in KEYS))
        for cfg in configs:
            s = t.get(cfg, {}).get(c)
            if s:
                print(f"{cfg:<24}{c:<9}" + "".join(fmt(s.get(k), k) for k in KEYS))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bench.presets_report")
    ap.add_argument("dir")
    ap.add_argument("--items", action="store_true", help="per-item worst offenders")
    ap.add_argument("--write-details", action="store_true", help="write the presets' measured detail lines")
    args = ap.parse_args(argv)
    d = Path(args.dir)
    by = load(d)
    for r in auto_records([r for cfg in by.values() for r in cfg.values()]):
        by["auto"][r["id"]] = r
    import yaml
    manifest = yaml.safe_load((Path(__file__).resolve().parent / "corpus" / "manifest.yaml").read_text())["items"]
    REAL_IDS.update(e["id"] for e in manifest if "real" in (e.get("tags") or []))
    from bench.presets_eval import configs as all_configs

    cfgs = all_configs(True, True)
    presets = ["auto"] + [c[0] for c in cfgs if c[0] == c[1] and "-" not in c[0] and not c[0].startswith("+")]
    presets = [p for p in presets if p in by]
    common = set.intersection(*(set(by[p]) for p in presets)) if presets else set()
    print(f"presets over {len(common)} common items")
    t = mean_table(by, presets, common)
    print_means(t, presets)
    print("\n\nVISIBLE-DEFECT RATE (% of items): pinhole / sliver / wobble >= 25 deg per 100 px / uneven rect")
    flag_table(by, presets, common)

    if "auto" in by:
        picks = Counter(r["pick"] for r in by["auto"].values())
        print("\nAUTO picks: " + ", ".join(f"{k} {v}" for k, v in picks.most_common()))
    if args.write_details:
        write_details([r for cfg in by.values() for r in cfg.values()], len(manifest))

    ablations = [c for c in cfgs if c[0] != c[1] and c[0] in by]
    if ablations:
        print("\n\nABLATION: artifact_index (and pinholes / slivers+degen+thin / wobble) of the preset, and with one parameter back at default")
        for p in presets:
            kids = [c for c in ablations if c[1] == p and not c[0].startswith("+")]
            if not kids:
                continue
            items = set(by[p]).intersection(*(set(by[k[0]]) for k in kids))
            tt = mean_table(by, [p] + [k[0] for k in kids], items)
            for c in CLASSES + (REAL,):
                base = tt[p].get(c)
                if not base:
                    continue
                row = [f"{p:<10} {c:<8} ART {base['artifact_index']:7.1f}  dE {base['delta_e_mean']:.3f} |"]
                for k in kids:
                    s = tt[k[0]].get(c, {})
                    name = k[0][len(p) + 1:]
                    row.append(f" -{name}: ART {s.get('artifact_index', float('nan')):6.1f} dE {s.get('delta_e_mean', float('nan')):.3f}"
                               f" pin {s.get('pinholes', float('nan')):4.1f} sl {s.get('slivers', 0) + s.get('degenerate', 0) + s.get('thin_strokes', 0):4.1f}"
                               f" wob {s.get('wobble_deg_100px', float('nan')):5.1f} |")
                print("".join(row))
        print("\n\nCULPRITS: ART(P) - ART(P-k) per parameter k (positive: k adds that many artifact points; the")
        print("largest is the parameter to blame), with dE(P) - dE(P-k) (positive: k costs fidelity), paired items only")
        for p in presets:
            kids = [c for c in ablations if c[1] == p and not c[0].startswith("+")]
            if not kids:
                continue
            items = set(by[p]).intersection(*(set(by[k[0]]) for k in kids))
            tt = mean_table(by, [p] + [k[0] for k in kids], items)
            for c in CLASSES + (REAL,):
                base = tt[p].get(c)
                if not base:
                    continue
                parts = []
                for k in kids:
                    s2 = tt[k[0]].get(c, {})
                    parts.append((base["artifact_index"] - s2.get("artifact_index", np.nan), base["delta_e_mean"] - s2.get("delta_e_mean", np.nan), k[0][len(p) + 1:]))
                worst = max(parts, key=lambda t: t[0])
                print(f"{p:<9}{c:<9} ART {base['artifact_index']:6.1f} dE {base['delta_e_mean']:.3f} | "
                      + "  ".join(f"{n}: {a:+6.1f} ({d:+.3f})" for a, d, n in parts)
                      + (f"   <- {worst[2]}" if worst[0] > 1.0 else "   <- none"))
        fwd = [c for c in ablations if c[0].startswith("+")]
        if fwd and "balanced" in by:
            items = set(by["balanced"]).intersection(*(set(by[k[0]]) for k in fwd))
            tt = mean_table(by, ["balanced"] + [k[0] for k in fwd], items)
            print(f"\n\nFORWARD: the defaults with one preset parameter applied ({len(items)} items)")
            print_means(tt, ["balanced"] + [k[0] for k in fwd])
    if args.items:
        print("\n\nWORST ITEMS per preset by artifact_index")
        for p in presets:
            rows = sorted(((r["metrics"]["artifact_index"], i) for i, r in by[p].items()), reverse=True)[:8]
            print(f"{p:<10}", ", ".join(f"{i} {a:.0f}" for a, i in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
