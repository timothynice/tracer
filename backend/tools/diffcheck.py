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
from studi0trace.engines.vexel.weights import interior  # noqa: E402
from studi0trace.engines.vexel import topology  # noqa: E402
from studi0trace.engines.vexel.curves import CurveParams  # noqa: E402
from studi0trace.engines.vexel.engine import VexelParams, trace_rgba  # noqa: E402
from studi0trace.engines.vexel.strokes import is_thin, medial_axis  # noqa: E402
from studi0trace.engines.vexel.boundary import thin_coverage  # noqa: E402
from studi0trace.engines.vexel.engine import _group_thin  # noqa: E402
from studi0trace.engines.vexel.strokes import Stroke, is_thin, stroke_fidelity, stroke_geometry  # noqa: E402

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
    # `rms` for the boundary graph, for the same reason as the fills: the two
    # are compared by the curve they describe, not vertex by vertex. Which arcs
    # exist, where each is cut and how many vertices each has must match exactly
    # — a shape mismatch fails outright — and over the corpus every vertex then
    # agrees to 0.03 px RMS. What is left is inherited: a vertex sits where the
    # coverage of one fill against another passes a half, and on a soft edge
    # that crossing slides a long way for the colour level the two fill fitters
    # are allowed to differ by. Feeding the Rust fills to the Python placement
    # drops the disagreeing values on the worst item from 7103 to 78. The
    # reported `max` is printed beside the RMS, so a single badly placed
    # junction is still visible to a reader.
    "arcs": ("rms", 0.05, 0.0),
    # The local fills' correction grids, given one label map and one set of
    # fills: an erosion, a box and two Gaussian filters in float64, which the
    # Rust sums with independent accumulators — a few ulps of a colour level.
    "local_fills": ("max", 1e-9, 0.0),
    # The placement alone, given one (extended) label map and the Python's
    # fills: what differs is those ulps and the Python's alpha, scaled to 255 in
    # float32 where the Rust scales it in double (a few 1e-7 px, and there
    # before the local fills), carried through a projection and a crossing
    # interpolation. A decision they tipped (a coverage level at exactly a
    # half) would move a vertex by a pixel and fail this outright.
    "placed": ("max", 1e-6, 0.0),
    # `placed` carried through symmetry and the junctions, given one label map
    # and one set of fills: the same ulps, through line fits and node solves.
    "nodes": ("max", 1e-6, 0.0),
    "upsample": ("max", 0.0, 0.0),
    # The wedge extension hands whole pixels back to a region that the partition
    # cut off, on a threshold over a three-way colour mix — and that mix is read
    # from the fitted fills, which the two implementations agree on only to about
    # a colour level. A pixel whose share sits on the threshold can therefore go
    # either way, so this is allowed the same kind of slack as `labels0`: a
    # handful of pixels in a frame. `arcs` is then given one extended map so that
    # what it compares is the graph, not this.
    "wedges": ("max", 0.0, 0.002),
    # Fed one label map, one set of fills and one residual, the two decide
    # alike to the pixel: the test is a handful of products per neighbour and
    # an "any neighbour explains it", which no visiting order can change.
    "edge_mix": ("max", 0.0, 0.0),
    # The skeleton decides whether a thin region is a stroke, and skimage's
    # would decide it differently on every run; both engines now thin in the
    # same deterministic order, so the two are one skeleton, pixel for pixel.
    "skeleton": ("max", 0.0, 0.0),
    # The stages above are each fed one input. `trace` runs the two engines
    # end to end and compares what each actually handed the boundary build —
    # the label map after every stage of its own (the rescue, the refine merge,
    # the thin-rim absorb) — and the graph it fitted: every arc's vertices and
    # every visible fitted segment. The labels are allowed what `labels0` is:
    # the rim split breaks a distance tie on the pixel's colour against two
    # fills that the two fitters agree on only to a colour level. The segments
    # are compared as `arcs` are (a shape mismatch fails outright, the
    # coordinates by RMS), because a segment's control points move with its
    # vertices. The bled copies are not compared: never seen, fitted loosely
    # from the same vertices, and their line-or-curve calls sit on knife edges.
    # The fit, on its own: every arc the Python placed and junction-placed is
    # handed to both fitters as it stands (vertices, pinned tangents, trims,
    # sliver flags, mirror axis). Which segments come out — line, cubic or
    # circular arc, and how many — must match exactly; the numbers then agree
    # to the last bits the two least-squares solvers leave.
    "segments": ("rms", 1e-3, 0.0),
    "trace_labels": ("max", 0.0, 0.002),
    "trace_arcs": ("rms", 0.05, 0.0),
    # Stroke recovery, per thin group: which regions are thin, how they group,
    # the centreline's vertices and width, and the fidelity that decides whether
    # the group is stroked at all. Both sides are handed the Python's label map
    # and coverage field, so what is compared is `strokes.py` against
    # `strokes.rs`. The centreline is the medial axis, and both thin in the same
    # order (`core/skeleton.rs`, `strokes.medial_axis`), so the skeletons are
    # one skeleton and everything after it is arithmetic: over the corpus the
    # vertices, widths and fidelities agree to 1e-7. A group that is stroked on
    # one side and not the other, or whose polylines differ in length, fails
    # outright.
    "strokes": ("max", 1e-6, 0.0),
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
def upsample(path):
    """The 2x Lanczos upsample both engines trace small inputs through, to the byte."""
    from studi0trace.engines.vexel.upsample import upsample2x

    a = load(path)
    h, w = a.shape[:2]
    py = upsample2x(a).astype(np.int32)
    rs = np.asarray(vexel_rs._stage_upsample(a.tobytes(), h, w), dtype=np.int32).reshape(2 * h, 2 * w, 4)
    return py, rs


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
        wt, core = interior(m)
        f = fit_fill(xs[m], ys[m], rgba255[m], params, weights=wt, core=core)
        kind, vals = vexel_rs._fit_fill(xs[m].tolist(), ys[m].tolist(), rgba255[m].ravel().tolist(),
                                        wt.tolist(), True, 4, 3.0, core.tolist())
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


def _fills_for(prep, labels):
    """Every region's fitted fill, the way the engine fits them."""
    h, w = labels.shape
    ys, xs = np.mgrid[0:h, 0:w]
    xs = xs.astype(np.float64) + 0.5
    ys = ys.astype(np.float64) + 0.5
    rgba255 = np.concatenate([prep.rgb, (prep.alpha * 255.0)[..., None]], axis=-1)
    params = FitParams(gradients=True, max_stops=4, tol=3.0)
    out = {}
    for lab in (int(i) for i in np.unique(labels) if i):
        m = labels == lab
        wt, core = interior(m)
        out[lab] = fit_fill(xs[m], ys[m], rgba255[m], params, weights=wt, core=core)
    return out


def _prepared(path):
    """Everything the boundary stages need: labels, fills and the prepared image."""
    a = load(path)
    prep = prepare(a)
    g = discontinuity(prep.features)
    labels = merge_regions(initial_labels(g, prep.features, min_region=6), prep.features,
                           MergeParams(detail=6.0, gradients=True), g)
    return a, prep, labels, _fills_for(prep, labels)


@stage
def edge_mix(path):
    """Which pixels the rescue treats as an edge's anti-aliasing or ringing
    rather than a feature, given one label map, one set of fills and the
    residual the engine thresholds (at the Detailed preset's detail, where the
    rescue reaches furthest into the ringing)."""
    from studi0trace.engines.vexel.rescue import edge_mix as py_edge_mix

    a, prep, labels, fills = _prepared(path)
    h, w = a.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w]
    xs = xs.astype(np.float64) + 0.5
    ys = ys.astype(np.float64) + 0.5
    rgba255 = np.concatenate([prep.rgb, (prep.alpha * 255.0)[..., None]], axis=-1)
    pred = np.zeros((h, w, 4))
    residual = np.zeros((h, w))
    for lab, f in fills.items():
        m = labels == lab
        pred[m] = f.evaluate(xs[m], ys[m])
        d = rgba255[m] - pred[m]
        cover = prep.alpha[m]
        residual[m] = np.sqrt(cover * cover * (d[:, :3] ** 2).sum(axis=1) + d[:, 3] ** 2) / (7.5 * 3.5)
    at = residual > 1.0
    py = py_edge_mix(labels, rgba255, pred, prep.alpha, at).astype(np.int32)
    rs = np.asarray(vexel_rs._stage_edge_mix(labels.astype(np.int32).ravel().tolist(), h, w,
                                             rgba255.ravel().tolist(), pred.ravel().tolist(),
                                             prep.alpha.astype(np.float64).ravel().tolist(), at.ravel().tolist()),
                    dtype=np.int32).reshape(h, w)
    return py, rs


@stage
def wedges(path):
    """The label map after a region cut off at a point is handed back the pixels
    its ink still runs through."""
    a, prep, labels, fills = _prepared(path)
    h, w = a.shape[:2]
    padded = np.pad(labels.astype(np.int64), 1, constant_values=0)
    py, _ = topology._extend_wedges(padded, prep.rgb, prep.alpha,
                                 lambda lab, qx, qy: fills[lab].evaluate(qx, qy),
                                 CurveParams(corner_threshold=60.0, tol=0.4, shape_fitting=True))
    rs = np.asarray(vexel_rs._stage_wedges(a.tobytes(), h, w, labels.astype(np.int32).ravel().tolist()),
                    dtype=np.int32).reshape(h + 2, w + 2)
    return py.astype(np.int32), rs


def _fill_args(fills: dict) -> tuple[list[int], list[str], list[list[float]]]:
    """The Python's fills as the Rust stage hooks take them (`_fills_from`)."""
    labs, kinds, vals = [], [], []
    for lab in sorted(fills):
        f = fills[lab]
        if isinstance(f, Solid):
            v = [float(x) for x in f.rgba]
        else:
            head = [f.x1, f.y1, f.x2, f.y2] if isinstance(f, Linear) else [f.cx, f.cy, f.r]
            v = [float(x) for x in head] + [float(x) for s in f.stops for x in (s.offset, *s.rgba)]
        labs.append(int(lab))
        kinds.append(f.kind)
        vals.append(v)
    return labs, kinds, vals


@stage
def local_fills(path):
    """Every region's local colour correction (`topology._local_corrections`,
    Rust `LocalFills`): the fitted fill's own residual over the region's pure
    pixels, Gaussian-smoothed and divided by the smoothed support. Both are
    given one label map and the Python's fills, so this compares the erosion,
    the boxes, the opaque test and the two Gaussian filters alone."""
    a, prep, labels, fills = _prepared(path)
    h, w = a.shape[:2]
    corr = topology._local_corrections(labels, prep.rgb, lambda lab, qx, qy: fills[lab].evaluate(qx, qy))
    py = []
    for lab in sorted(corr):
        r0, c0, img = corr[lab]
        py += [float(lab), float(r0), float(c0), float(img.shape[0]), float(img.shape[1]), *img.ravel().tolist()]
    rs = vexel_rs._stage_local_fills(a.tobytes(), h, w, labels.astype(np.int32).ravel().tolist(), *_fill_args(fills))
    return np.array(py, dtype=np.float64), np.array(rs, dtype=np.float64)


@stage
def placed(path):
    """Where the placement puts every vertex, before any node is placed: the
    coverage against the local fills (with its contrast gate), the crossing
    search and the lone-vertex rule. Both are given one label map (extended
    once, as `arcs` is) and the Python's fills, so the only differences left
    are the last bits of the two Gaussian filters and of the fills' arithmetic
    — and any decision one of those bits tips."""
    a, prep, labels, fills = _prepared(path)
    h, w = a.shape[:2]
    padded = np.pad(labels.astype(np.int64), 1, constant_values=0)
    extended, _ = topology._extend_wedges(padded, prep.rgb, prep.alpha,
                                       lambda lab, qx, qy: fills[lab].evaluate(qx, qy),
                                       CurveParams(corner_threshold=60.0, tol=0.4, shape_fitting=True))
    labels = extended[1:-1, 1:-1]
    fills = _fills_for(prep, labels)
    fill_at = lambda lab, qx, qy: fills[lab].evaluate(qx, qy)  # noqa: E731
    padded = np.pad(labels.astype(np.int64), 1, constant_values=0)
    chains = topology._chains(padded)
    out = topology._place(chains, padded, prep.rgb, prep.alpha, fill_at, set(),
                          local=topology._local_fills(labels, prep.rgb, prep.alpha, fill_at))

    def key(row):
        return tuple(round(v, 6) for v in row)

    rows = sorted(([float(ch["pair"][0]), float(ch["pair"][1]), float(len(pts)), *pts.ravel().tolist()]
                   for ch, (pts, _step, _crowded) in zip(chains, out)), key=key)
    py = np.array([v for row in rows for v in row], dtype=np.float64)
    flat = np.asarray(vexel_rs._stage_place(a.tobytes(), h, w, labels.astype(np.int32).ravel().tolist(), *_fill_args(fills), False, False),
                      dtype=np.float64)
    rs_rows, i = [], 0
    while i + 2 < len(flat):
        n = int(flat[i + 2])
        rs_rows.append(flat[i:i + 3 + 2 * n].tolist())
        i += 3 + 2 * n
    rs = np.array([v for row in sorted(rs_rows, key=key) for v in row], dtype=np.float64)
    if py.shape != rs.shape:
        print(f"  FAIL placed    {path.name}: {len(rows)} arcs / {py.size} values in Python, {len(rs_rows)} arcs / {rs.size} in Rust")
        return np.zeros(1), np.full(1, 1e9)
    return py, rs


def _arc_rows(flat) -> list[list[float]]:
    """Split a Rust stage hook's flat (pair, n, points...) answer into rows."""
    rows, i = [], 0
    while i + 2 < len(flat):
        n = int(flat[i + 2])
        rows.append(list(flat[i:i + 3 + 2 * n]))
        i += 3 + 2 * n
    return rows


@stage
def nodes(path):
    """Where the boundary graph puts every vertex once the nodes are placed:
    the wedge extension, the placement, symmetry and the junctions (the tip
    hold, the revert guard) and rectangles, given one label map and the
    Python's fills — `placed` carried through everything that moves a vertex,
    so a difference here is a rule, not a fill. The Rust passes it only with
    the pinholes port's tip-revert guard and the rects port in; until then it
    names the arcs where they bite."""
    a, prep, labels, fills = _prepared(path)
    h, w = a.shape[:2]
    bnd = topology.build(labels, prep.rgb, prep.alpha, lambda lab, qx, qy: fills[lab].evaluate(qx, qy),
                         CurveParams(corner_threshold=60.0, tol=0.4, shape_fitting=True))

    def key(row):
        return tuple(round(v, 6) for v in row)

    py_rows = sorted(([float(arc.pair[0]), float(arc.pair[1]), float(len(arc.pts)), *arc.pts.ravel().tolist()]
                      for arc in bnd.arcs), key=key)
    flat = vexel_rs._stage_place(a.tobytes(), h, w, labels.astype(np.int32).ravel().tolist(), *_fill_args(fills), True, True)
    rs_rows = sorted(_arc_rows(flat), key=key)
    py = np.array([v for row in py_rows for v in row], dtype=np.float64)
    rs = np.array([v for row in rs_rows for v in row], dtype=np.float64)
    if py.shape != rs.shape:
        print(f"  FAIL nodes     {path.name}: {len(py_rows)} arcs / {py.size} values in Python, {len(rs_rows)} arcs / {rs.size} in Rust")
        return np.zeros(1), np.full(1, 1e9)
    return py, rs


@stage
def arcs(path):
    """Where the shared boundary graph puts every arc.

    The whole output's topology hangs off this: which edges exist, where each is
    cut, and where each of its vertices sits. Two regions are handed one fitted
    arc, so an arc that differs between the implementations is two shapes that
    differ, and `bench` would only say the SVG moved.

    Both sides are given the *same* label map, as the `fills` stage is: `labels0`
    is allowed a slack of a few pixels, and one pixel moving redraws the graph
    around it, which would drown this stage's own signal.
    """
    a, prep, labels, fills = _prepared(path)
    h, w = a.shape[:2]
    # One extended map for both, so this stage compares the graph and not the
    # pixel-level call `wedges` already covers.
    padded = np.pad(labels.astype(np.int64), 1, constant_values=0)
    extended, _ = topology._extend_wedges(padded, prep.rgb, prep.alpha,
                                       lambda lab, qx, qy: fills[lab].evaluate(qx, qy),
                                       CurveParams(corner_threshold=60.0, tol=0.4, shape_fitting=True))
    labels = extended[1:-1, 1:-1]
    # Refitted on the extended map, because that is what the Rust hook is handed
    # and this stage is about the graph, not about which labels the fills saw.
    fills = _fills_for(prep, labels)
    bnd = topology.build(labels, prep.rgb, prep.alpha,
                         lambda lab, qx, qy: fills[lab].evaluate(qx, qy),
                         CurveParams(corner_threshold=60.0, tol=0.4, shape_fitting=True),
                         extend=False)
    def key(row):
        # Two arcs with the same pair and vertex count sort by their coordinates,
        # and a coordinate that differs in the last bit between the two
        # implementations (75.99999999999999 against 76.00000000000001) would
        # swap them and compare one arc against another.
        return tuple(round(v, 6) for v in row)

    rows = sorted(
        ([float(arc.pair[0]), float(arc.pair[1]), float(len(arc.pts)), *arc.pts.ravel().tolist()] for arc in bnd.arcs),
        key=key,
    )
    py = np.array([v for row in rows for v in row], dtype=np.float64)
    flat = np.asarray(vexel_rs._stage_arcs(a.tobytes(), h, w, labels.astype(np.int32).ravel().tolist(), True, False), dtype=np.float64)
    rs_rows, i = [], 0
    while i + 2 < len(flat):
        n = int(flat[i + 2])
        rs_rows.append(flat[i:i + 3 + 2 * n].tolist())
        i += 3 + 2 * n
    rs = np.array([v for row in sorted(rs_rows, key=key) for v in row], dtype=np.float64)
    if py.shape != rs.shape:
        print(f"  FAIL arcs      {path.name}: {len(rows)} arcs / {py.size} values in Python, {len(rs_rows)} arcs / {rs.size} in Rust")
        return np.zeros(1), np.full(1, 1e9)
    return py, rs


@stage
def skeleton(path):
    """The medial axis of every thin region of the merged map, as the stroke
    stage takes it: cropped to the region's box with a one-pixel margin."""
    a, prep, labels, _fills = _prepared(path)
    h, w = a.shape[:2]
    py_out, rs_out = [], []
    for lab in (int(i) for i in np.unique(labels) if i):
        m = labels == lab
        if not is_thin(m):
            continue
        rows, cols = np.nonzero(m)
        crop = np.pad(m[rows.min():rows.max() + 1, cols.min():cols.max() + 1], 1)
        py_out.append(medial_axis(crop).ravel())
        rs_out.append(np.asarray(vexel_rs._medial_axis(crop.astype(np.uint8).ravel().tolist(), *crop.shape), dtype=bool))
    if not py_out:
        return np.zeros(1, np.int32), np.zeros(1, np.int32)
    return np.concatenate(py_out).astype(np.int32), np.concatenate(rs_out).astype(np.int32)


@stage
def segments(path):
    """What each fitter makes of the same placed arcs: the Python builds the
    graph on one map, and every arc's state after the junctions are placed goes
    to `topology._fit_arc` and to the Rust `fit_arc` alike."""
    from studi0trace.engines.vexel.curves import CircArc, Cubic, Line

    a, prep, labels, fills = _prepared(path)
    params = CurveParams(corner_threshold=60.0, tol=0.4, shape_fitting=True)
    bnd = topology.build(labels, prep.rgb, prep.alpha,
                         lambda lab, qx, qy: fills[lab].evaluate(qx, qy), params)

    def py_seg(s):
        if isinstance(s, Line):
            return "L", [*s.p0, *s.p1]
        if isinstance(s, Cubic):
            return "C", [*s.p0, *s.c1, *s.c2, *s.p1]
        assert isinstance(s, CircArc)
        return "A", [*s.p0, *s.p1, s.r, float(s.large), float(s.sweep)]

    py_out, rs_out = [], []
    for arc in bnd.arcs:
        py = [py_seg(s) for s in topology._fit_arc(arc, params)]
        rs = vexel_rs._fit_arc(
            arc.pts.ravel().tolist(), arc.closed,
            None if arc.t0 is None else tuple(map(float, arc.t0)),
            None if arc.t1 is None else tuple(map(float, arc.t1)),
            float(arc.trim0), float(arc.trim1),
            None if arc.sliver is None else [bool(v) for v in arc.sliver],
            None if arc.mirror is None else (*map(float, arc.mirror[0]), *map(float, arc.mirror[1])),
            params.corner_threshold, params.tol, params.snap_axis_deg,
        )
        if [k for k, _ in py] != [k for k, _ in rs]:
            print(f"  FAIL segments  {path.name}: arc {arc.pair} ({len(arc.pts)} vertices) fits "
                  f"{''.join(k for k, _ in py)} in Python, {''.join(k for k, _ in rs)} in Rust")
            return np.zeros(1), np.full(1, 1e9)
        py_out.extend(v for _, vals in py for v in vals)
        rs_out.extend(v for _, vals in rs for v in vals)
    return np.array(py_out, dtype=np.float64), np.array(rs_out, dtype=np.float64)


def _read_labels(p: pathlib.Path) -> np.ndarray:
    lines = p.read_text().splitlines()
    return np.array([[int(v) for v in line.split()] for line in lines[1:]], dtype=np.int32)


def _read_arcs(p: pathlib.Path) -> list[tuple]:
    """Each arc as (pair, vertex count, vertices, segment kinds, segment numbers)."""
    out, cur = [], None
    for line in p.read_text().splitlines():
        if line.startswith("arc "):
            t = line.split()
            cur = [(int(t[1]), int(t[2])), int(t[3][2:]), None, "", []]
            out.append(cur)
        elif line.startswith("  pts "):
            cur[2] = [float(v) for tok in line.split()[1:] for v in tok.split(",")]
        elif line.startswith("  seg "):
            # The bled copy (`under`) is left out: it is never seen, it is
            # fitted loosely from these same vertices, and its line-or-curve
            # calls sit on knife edges that the last bits of the fills decide.
            t = line.split()
            cur[3] += t[1]
            cur[4].extend(float(v) for v in t[2:])
    return [tuple(c) for c in out]


def _traced(path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Run both engines on `path` under `VEXEL_DUMP`; the two dump directories."""
    import os
    import tempfile

    a = load(path)
    h, w = a.shape[:2]
    root = pathlib.Path(tempfile.mkdtemp(prefix="diffcheck-"))
    py_dir, rs_dir = root / "python", root / "rust"
    py_dir.mkdir()
    rs_dir.mkdir()
    saved = os.environ.get("VEXEL_DUMP")
    try:
        os.environ["VEXEL_DUMP"] = str(py_dir)
        trace_rgba(a, VexelParams())
        os.environ["VEXEL_DUMP"] = str(rs_dir)
        vexel_rs.trace(a.tobytes(), w, h, VexelParams().model_dump())
    finally:
        if saved is None:
            os.environ.pop("VEXEL_DUMP", None)
        else:
            os.environ["VEXEL_DUMP"] = saved
    return py_dir, rs_dir


_TRACED: dict[pathlib.Path, tuple[pathlib.Path, pathlib.Path]] = {}


def _trace_dirs(path):
    if path not in _TRACED:
        _TRACED[path] = _traced(path)
    return _TRACED[path]


@stage
def trace_labels(path):
    """The label map each engine actually handed `topology.build`, after every
    label stage of its own pipeline."""
    py_dir, rs_dir = _trace_dirs(path)
    return _read_labels(py_dir / "labels_to_topology.txt"), _read_labels(rs_dir / "labels_to_topology.txt")


@stage
def trace_arcs(path):
    """The boundary graph each engine built and fitted on its own labels: every
    arc's vertices and its visible fitted segments."""
    py_dir, rs_dir = _trace_dirs(path)
    py, rs = _read_arcs(py_dir / "arcs.txt"), _read_arcs(rs_dir / "arcs.txt")
    key = lambda arc: (arc[0], arc[1], tuple(round(v, 6) for v in arc[2]))  # noqa: E731
    py.sort(key=key)
    rs.sort(key=key)
    if [(a[0], a[1]) for a in py] != [(a[0], a[1]) for a in rs]:
        print(f"  FAIL trace_arcs {path.name}: {len(py)} arcs in Python, {len(rs)} in Rust, or different pairs / vertex counts")
        return np.zeros(1), np.full(1, 1e9)
    for a, b in zip(py, rs):
        if a[3] != b[3]:
            print(f"  FAIL trace_arcs {path.name}: arc {a[0]} ({a[1]} vertices) is {a[3]} in Python, {b[3]} in Rust")
            return np.zeros(1), np.full(1, 1e9)
    flat = lambda arcs: np.array([v for a in arcs for v in (*a[2], *a[4])], dtype=np.float64)  # noqa: E731
    return flat(py), flat(rs)
def _stroke_rows(stroke: Stroke | None, fidelity: float | None) -> list[list[float]]:
    """A stroke as rows the two sides can be lined up on: one header row with
    the width, the fidelity and the polyline count, then one row per polyline
    (closed flag, cap, vertex count, vertices)."""
    if stroke is None:
        return [[-1.0]]
    rows = [[float(stroke.width), float(fidelity), float(len(stroke.polylines))]]
    caps = stroke.caps or ["round"] * len(stroke.polylines)
    for xy, c, cap in zip(stroke.polylines, stroke.closed, caps):
        rows.append([float(c), 1.0 if cap == "butt" else 0.0, float(len(xy)), *np.asarray(xy, dtype=float).ravel().tolist()])
    return rows


@stage
def strokes(path):
    """Stroke recovery, thin group by thin group.

    For every region of the merged map: the `is_thin` decision. For the thin
    ones, grouped by the Python's `_group_thin` and by the Rust's `group_thin`
    (the groups must be the same sets): the centreline polylines and width
    `stroke_geometry` finds, and the `stroke_fidelity` of that centreline
    against the coverage. Every group is a block of rows the two sides are
    compared on; a group stroked by one implementation and not the other is a
    shape mismatch and fails outright.
    """
    a, prep, labels, fills = _prepared(path)
    h, w = a.shape[:2]
    fill_at = lambda lab, qx, qy: fills[lab].evaluate(qx, qy)  # noqa: E731
    ids = [int(i) for i in np.unique(labels) if i]
    py_thin = [lab for lab in ids if is_thin(labels == lab)]
    rs_thin = [lab for lab in ids if vexel_rs._is_thin((labels == lab).astype(np.uint8).ravel().tolist(), h, w)]
    if py_thin != rs_thin:
        print(f"  FAIL strokes   {path.name}: thin regions {py_thin} in Python, {rs_thin} in Rust")
        return np.zeros(1), np.full(1, 1e9)
    if not py_thin:
        return np.zeros(1), np.zeros(1)
    py_groups = _group_thin(py_thin, labels, prep.rgb, prep.alpha, fill_at)
    rs_groups = vexel_rs._group_thin(a.tobytes(), h, w, labels.astype(np.int32).ravel().tolist(), py_thin)
    if sorted(map(sorted, py_groups)) != sorted(map(sorted, rs_groups)):
        print(f"  FAIL strokes   {path.name}: groups {py_groups} in Python, {rs_groups} in Rust")
        return np.zeros(1), np.full(1, 1e9)
    py_rows, rs_rows = [], []
    mismatched = False
    for members in sorted(map(sorted, py_groups)):
        union = np.isin(labels, members)
        # The engine grows the union one pixel into transparent surroundings
        # before measuring coverage; the transparent set is the engine's, so the
        # union itself stands in for it here — the field is the same on both
        # sides either way, which is what this stage needs.
        field = thin_coverage(union, labels, prep.rgb, prep.alpha, fill_at)
        py_stroke = stroke_geometry(union, field)
        py_fid = stroke_fidelity(py_stroke, field) if py_stroke is not None else None
        rs = vexel_rs._stroke_geometry(union.astype(np.uint8).ravel().tolist(), field.astype(np.float64).ravel().tolist(), h, w)
        rs_stroke = None
        rs_fid = None
        if rs is not None:
            polys, closed, caps, width = rs
            rs_stroke = Stroke(polylines=[np.asarray(p, dtype=float).reshape(-1, 2) for p in polys], closed=list(closed), caps=list(caps), width=float(width))
            rs_fid = vexel_rs._stroke_fidelity(polys, list(closed), float(width), field.astype(np.float64).ravel().tolist(), h, w)
        if (py_stroke is None) != (rs_stroke is None):
            print(f"  FAIL strokes   {path.name}: group {members} is {'stroked' if py_stroke else 'filled'} in Python, {'stroked' if rs_stroke else 'filled'} in Rust")
            mismatched = True
            continue
        prow, rrow = _stroke_rows(py_stroke, py_fid), _stroke_rows(rs_stroke, rs_fid)
        if [len(r) for r in prow] != [len(r) for r in rrow]:
            print(f"  FAIL strokes   {path.name}: group {members} has polylines of {[int(r[2]) for r in prow[1:]]} vertices in Python, {[int(r[2]) for r in rrow[1:]]} in Rust")
            mismatched = True
            continue
        if py_stroke is not None:
            dw, df = abs(prow[0][0] - rrow[0][0]), abs(prow[0][1] - rrow[0][1])
            if dw > 1e-3 or df > 1e-3:
                print(f"  FAIL strokes   {path.name}: group {members} width {prow[0][0]:.4f} / fidelity {prow[0][1]:.4f} in Python, {rrow[0][0]:.4f} / {rrow[0][1]:.4f} in Rust")
                mismatched = True
        py_rows += prow
        rs_rows += rrow
    if mismatched:
        return np.zeros(1), np.full(1, 1e9)
    py = np.array([v for row in py_rows for v in row], dtype=np.float64)
    rs = np.array([v for row in rs_rows for v in row], dtype=np.float64)
    return py, rs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true", help="also run the end-to-end stages (trace_labels, trace_arcs); "
                    "these compare whole pipelines and differ wherever the fill fit's documented colour tolerance or "
                    "the placement's 0.05 px tolerance sits at a decision threshold, so they are diagnostics, not gates")
    ap.add_argument("stages", nargs="*", choices=list(STAGES), metavar="STAGE",
                    help=f"one or more of: {', '.join(STAGES)} (default: all)")
    ap.add_argument("--limit", type=int, default=None, help="only the first N corpus items")
    ap.add_argument("--filter", default=None, help="only items whose path contains this")
    args = ap.parse_args()

    paths = items(args.limit, args.filter)
    failures = 0
    informational = ("trace_labels", "trace_arcs")
    default = list(STAGES) if args.all else [n for n in STAGES if n not in informational]
    for name in args.stages or default:
        print(f"{name}:")
        for p in paths:
            py, rs = STAGES[name](p)
            if not _report(name, p, py, rs):
                failures += 1
    print(f"\n{failures} failing (stage, item) pairs over {len(paths)} items")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
