"""Intermediate dumps, written when `VEXEL_DUMP` names a directory.

`tools/diffcheck.py` feeds both implementations one input per stage; this is
for the other question — what did the engine *actually* hand each stage on a
real trace — and pairs with `vexel-rs/src/dump.rs`, which writes the same files
in the same format. `diffcheck`'s `trace` stage reads both.
"""
from __future__ import annotations

import os
import pathlib

import numpy as np

from studi0trace.engines.vexel.curves import CircArc, Cubic, Line, Segment


def _dir() -> pathlib.Path | None:
    where = os.environ.get("VEXEL_DUMP")
    return pathlib.Path(where) if where else None


def labels(name: str, grid: np.ndarray) -> None:
    """A label map as text: `h w` then one row per line."""
    d = _dir()
    if d is None:
        return
    with open(d / f"{name}.txt", "w") as f:
        f.write("%d %d\n" % grid.shape)
        for row in grid:
            f.write(" ".join(str(int(v)) for v in row) + "\n")


def text(name: str, line: str) -> None:
    """Append a line of free text to `<name>.txt`."""
    d = _dir()
    if d is None:
        return
    with open(d / f"{name}.txt", "a") as f:
        f.write(line)


def _seg(s: Segment) -> str:
    if isinstance(s, Line):
        return "L %.6f %.6f %.6f %.6f" % (*s.p0, *s.p1)
    if isinstance(s, Cubic):
        return "C " + " ".join("%.6f" % v for v in (*s.p0, *s.c1, *s.c2, *s.p1))
    assert isinstance(s, CircArc)
    return "A %.6f %.6f %.6f %.6f %.6f %d %d" % (*s.p0, *s.p1, s.r, s.large, s.sweep)


def arcs(name: str, bnd) -> None:
    """Every arc of the boundary graph: pair, ends' state, placed vertices,
    fitted segments and the bled copy, one arc per block, in the graph's order."""
    d = _dir()
    if d is None:
        return

    def t(v) -> str:
        return "-" if v is None else "%.6f,%.6f" % tuple(v)

    with open(d / f"{name}.txt", "w") as f:
        for a in bnd.arcs:
            f.write(
                "arc %d %d n=%d closed=%d t0=%s t1=%s tip0=%d tip1=%d trim0=%.4f trim1=%.4f sliver=%d mirror=%d\n"
                % (a.pair[0], a.pair[1], len(a.pts), a.closed, t(a.t0), t(a.t1), a.tip0, a.tip1, a.trim0, a.trim1,
                   0 if a.sliver is None else int(a.sliver.sum()), a.mirror is not None)
            )
            f.write("  pts " + " ".join("%.6f,%.6f" % tuple(p) for p in a.pts) + "\n")
            for s in a.segments:
                f.write("  seg " + _seg(s) + "\n")
            for s in a.under:
                f.write("  under " + _seg(s) + "\n")
