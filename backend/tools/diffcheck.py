"""Differential harness: run a pipeline stage in the Python engine and in the
Rust one and report where they disagree.

The bench only says the SVG got better or worse. This says *which stage* moved,
which is the difference between a five-minute fix and a day of bisecting colour
numbers. It is how the port was built, and it is how a change to either
implementation is checked against the other.

    .venv/bin/python -m tools.diffcheck                 # every stage, whole corpus
    .venv/bin/python -m tools.diffcheck labels0 --filter 128

Exact agreement is expected only for the stages that decide the *topology* —
the partition's labels, and the filters feeding it. Downstream of those the two
differ in the last bits (numpy's `lstsq` is SVD-based, this one is QR-based) and
the tolerances say so.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import vexel_rs  # noqa: E402
from studi0trace.engines.vexel.fills import FitParams, Linear, Radial, Solid, Stop, fit_fill  # noqa: E402
from studi0trace.engines.vexel.merge import MergeParams, merge_regions  # noqa: E402
from studi0trace.engines.vexel.partition import discontinuity, initial_labels  # noqa: E402
from studi0trace.engines.vexel.prepare import prepare  # noqa: E402
from studi0trace.engines.vexel.weights import interior_weights  # noqa: E402

CORPUS = pathlib.Path(__file__).resolve().parent.parent / "bench" / "corpus"

# Per stage: how the difference is judged, and the bar.
#
# `max` for the stages that decide the *topology* — one pixel over the line
# there moves a whole region. The label bar is not zero because the two
# implementations round the Lab conversion differently in the last float32 bit
# (numpy's matmul is BLAS's, and its result depends on the array's shape), and a
# handful of pixels sit exactly on the ridge test's knife edge.
#
# `rms` for the fills, which are compared by what they *paint* rather than by
# their parameters: a radial whose centre moved half a pixel but whose colours
# land in the same place is the same fill. Individual regions can differ by more
# where the radial centre search — Nelder-Mead on a rough objective — settles in
# a different local minimum; the bench is the arbiter of whether that matters,
# and it says the two are within a thousandth of a ΔE.
TOLERANCE = {
    "rgb": ("max", 1e-9, 0.0),
    "features": ("max", 1e-4, 0.0),
    "grad": ("max", 1e-4, 0.0),
    "labels0": ("max", 0.0, 0.002),
    "fills": ("rms", 1.0, 0.0),
}


def items(limit: int | None, pattern: str | None) -> list[pathlib.Path]:
    paths = sorted(CORPUS.rglob("*.png"))
    if pattern:
        paths = [p for p in paths if pattern in str(p)]
    return paths[:limit] if limit else paths


def load(path: pathlib.Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)


def _report(name: str, path: pathlib.Path, py: np.ndarray, rs: np.ndarray) -> bool:
    metric, atol, frac = TOLERANCE[name]
    if py.shape != rs.shape:
        print(f"  FAIL {name:9s} {path.name}: shape {py.shape} vs {rs.shape}")
        return False
    if py.dtype.kind in "iu":
        bad = int((py != rs).sum())
        ok = bad <= frac * py.size
        detail = "exact" if bad == 0 else f"{bad} / {py.size} pixels ({bad / py.size:.4%})"
    else:
        d = np.abs(py.astype(np.float64) - rs.astype(np.float64))
        err = float(np.sqrt((d * d).mean())) if metric == "rms" else float(d.max())
        ok = err <= atol
        detail = f"{metric} |Δ| = {err:.3e} (tolerance {atol:.0e}), max {d.max():.3e}"
    print(f"  {'ok  ' if ok else 'FAIL'} {name:9s} {path.name}: {detail}")
    return ok


STAGES: dict[str, callable] = {}


def stage(fn):
    STAGES[fn.__name__] = fn
    return fn


@stage
def rgb(path):
    a = load(path)
    h, w = a.shape[:2]
    return prepare(a).rgb.astype(np.float64), np.asarray(vexel_rs._stage_rgb(a.tobytes(), h, w)).reshape(h, w, 3)


@stage
def features(path):
    a = load(path)
    h, w = a.shape[:2]
    return prepare(a).features.astype(np.float64), np.asarray(vexel_rs._stage_features(a.tobytes(), h, w)).reshape(h, w, 4)


@stage
def grad(path):
    a = load(path)
    h, w = a.shape[:2]
    py = discontinuity(prepare(a).features).astype(np.float64)
    return py, np.asarray(vexel_rs._stage_grad(a.tobytes(), h, w)).reshape(h, w)


@stage
def labels0(path):
    a = load(path)
    h, w = a.shape[:2]
    prep = prepare(a)
    py = initial_labels(discontinuity(prep.features), prep.features, min_region=6)
    rs = np.asarray(vexel_rs._stage_labels0(a.tobytes(), h, w, 6), dtype=np.int32).reshape(h, w)
    return py.astype(np.int32), rs


def _rust_fill(kind: str, vals: list[float]):
    """Rebuild the Rust fill as a Python one so the two can be evaluated side
    by side."""
    if kind == "solid":
        return Solid(rgba=np.asarray(vals[:4], dtype=float))
    head, rest = (4, vals[4:]) if kind == "linear" else (3, vals[3:])
    stops = [Stop(offset=rest[i], rgba=np.asarray(rest[i + 1 : i + 5], dtype=float))
             for i in range(0, len(rest), 5)]
    if kind == "linear":
        return Linear(*vals[:4], stops=stops)
    return Radial(*vals[:3], stops=stops)


@stage
def fills(path):
    """What every merged region's fitted fill actually paints, sampled at the
    region's own pixels."""
    a = load(path)
    h, w = a.shape[:2]
    prep = prepare(a)
    g = discontinuity(prep.features)
    labels = merge_regions(initial_labels(g, prep.features, min_region=6), prep.features,
                           MergeParams(detail=6.0, gradients=True), g)
    ys, xs = np.mgrid[0:h, 0:w]
    xs = xs.astype(np.float64) + 0.5
    ys = ys.astype(np.float64) + 0.5
    rgba255 = np.concatenate([prep.rgb, (prep.alpha * 255.0)[..., None]], axis=-1)
    params = FitParams(gradients=True, max_stops=4, tol=3.0)
    py_out, rs_out = [], []
    for lab in (int(i) for i in np.unique(labels) if i):
        m = labels == lab
        wt = interior_weights(m)
        f = fit_fill(xs[m], ys[m], rgba255[m], params, weights=wt)
        kind, vals = vexel_rs._fit_fill(xs[m].tolist(), ys[m].tolist(), rgba255[m].ravel().tolist(),
                                        wt.tolist(), True, 4, 3.0)
        if f.kind != kind:
            print(f"  FAIL fills     {path.name}: region {lab} is {f.kind} in Python, {kind} in Rust")
            return np.zeros(1), np.full(1, 1e9)
        # sample at most a few thousand of the region's pixels; the fill is
        # smooth, so more tells us nothing
        px, py_ = xs[m], ys[m]
        step = max(1, px.size // 2000)
        px, py_ = px[::step], py_[::step]
        py_out.append(f.evaluate(px, py_))
        rs_out.append(_rust_fill(kind, vals).evaluate(px, py_))
    if not py_out:
        return np.zeros(1), np.zeros(1)
    return np.concatenate(py_out), np.concatenate(rs_out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stages", nargs="*", choices=list(STAGES), metavar="STAGE",
                    help=f"one or more of: {', '.join(STAGES)} (default: all)")
    ap.add_argument("--limit", type=int, default=None, help="only the first N corpus items")
    ap.add_argument("--filter", default=None, help="only items whose path contains this")
    args = ap.parse_args()

    paths = items(args.limit, args.filter)
    failures = 0
    for name in args.stages or list(STAGES):
        print(f"{name}:")
        for p in paths:
            py, rs = STAGES[name](p)
            if not _report(name, p, py, rs):
                failures += 1
    print(f"\n{failures} failing (stage, item) pairs over {len(paths)} items")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
