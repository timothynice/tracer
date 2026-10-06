"""Per-item regression gate: `python -m bench.gate REF/results.json CAND/results.json`.

`bench compare` judges class means, where one item can get much worse while
its class holds. This looks at every item and every metric below, and exits 1
on any regression, or on an item the candidate errored on or lost.
"""
from __future__ import annotations

import argparse
import json
import math
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
    subset = len(b) < len(a)  # a sentinel run (--ids) checks only the items it ran
    missing = [f"{i} missing or errored" for i in sorted(a)
               if (i in b and "error" in b[i]) or (i not in b and not subset)]
    for i in sorted(set(a) & set(b)):
        if "error" in a[i] or "error" in b[i]:
            continue
        ma, mb = a[i]["metrics"], b[i]["metrics"]
        for k, (sign, tol) in TOLERANCES.items():
            va, vb = ma.get(k), mb.get(k)
            # Both None is OK (no truth); vanished or non-finite is regression
            if va is None and vb is None:
                continue
            if va is not None and (vb is None or not math.isfinite(vb)):
                line = f"{i} {k}: {va:.4f} → {vb}"
                regs.append(line)
                continue
            if va is None or vb is None or not math.isfinite(va) or not math.isfinite(vb):
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
    ref_data = json.loads(Path(args.ref).read_text())
    cand_data = json.loads(Path(args.cand).read_text())
    # Check for no items with the specified engine
    if not _by_id(ref_data, args.engine):
        print(f"no {args.engine} items in {args.ref}", file=sys.stderr)
        return 2
    if not _by_id(cand_data, args.engine):
        print(f"no {args.engine} items in {args.cand}", file=sys.stderr)
        return 2
    regs, imps, missing = gate(ref_data, cand_data, args.engine)
    for title, lines in (("REGRESSED", regs), ("MISSING", missing), ("improved", imps)):
        if lines:
            print(f"\n{title} ({len(lines)})")
            print("\n".join(f"  {ln}" for ln in lines))
    bad = bool(regs or missing)
    print("\nGATE FAIL" if bad else "\ngate ok")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
