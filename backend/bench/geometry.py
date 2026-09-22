"""Geometry metrics against vector truth, at 1/8 px.

The colour metrics cannot see a tip cut 6 px short or a straight edge drawn as
a bowing cubic: mean ΔE moved by 0.009 when such a tip was fixed, and edge F1
at a 2 px tolerance went the wrong way. These render the truth SVG and the
traced SVG at `scale`× and compare their edge sets directly, so the question
"where does the traced outline sit relative to the drawn one" gets a number in
source pixels.

`line_debt` needs no truth: it counts cubic segments that bow so little over
their chord that they should have been lines, which is what a straight edge
drawn as a wobbling curve looks like in the file.
"""
from __future__ import annotations

import re

import numpy as np
from scipy import ndimage

from bench.raster import rasterize, to_rgb_on_white

EDGE_STEP = 12.0        # 8-bit RGB distance between neighbouring pixels that makes an edge
JUNCTION_REACH = 6.0    # source px around a truth junction that count as "at the junction"
LINE_BOW = 0.2          # a cubic bowing less than this over ≥ LINE_MIN px should have been a line
LINE_MIN = 3.0


def _edges(rgb: np.ndarray) -> np.ndarray:
    f = rgb.astype(np.float32)
    dx = np.zeros(rgb.shape[:2], bool)
    dy = np.zeros(rgb.shape[:2], bool)
    dx[:, :-1] = np.linalg.norm(f[:, 1:] - f[:, :-1], axis=2) > EDGE_STEP
    dy[:-1, :] = np.linalg.norm(f[1:] - f[:-1], axis=2) > EDGE_STEP
    return dx | dy


def _junction_mask(rgb: np.ndarray, edges: np.ndarray, scale: int) -> np.ndarray:
    """Edge pixels whose one-source-pixel neighbourhood holds three or more flat colours.

    Colours are quantised to 32 levels so the anti-aliasing mixtures along an
    edge collapse into their neighbours, and a colour has to fill at least four
    pixels of the window to count as a region rather than a mixture.
    """
    q = rgb.astype(np.int32) >> 3
    code = (q[..., 0] << 10) | (q[..., 1] << 5) | q[..., 2]
    r = scale
    out = np.zeros(edges.shape, bool)
    ys, xs = np.nonzero(edges)
    stride = max(1, len(ys) // 20000)
    for y, x in zip(ys[::stride], xs[::stride]):
        win = code[max(0, y - r): y + r + 1, max(0, x - r): x + r + 1].ravel()
        _vals, counts = np.unique(win, return_counts=True)
        if int((counts >= 4).sum()) >= 3:
            out[y, x] = True
    return out


def outline_error(truth_svg: str, out_svg: str, width: int, height: int, scale: int = 8) -> dict:
    """Symmetric Chamfer distance between the truth's edges and the output's, in source px.

    `outline_px` is the mean over both edge sets, `outline_p99_px` the 99th
    percentile, and `junction_px` the 90th percentile restricted to within
    `JUNCTION_REACH` of a truth junction (None when the truth has no junctions).
    A junction defect is local — a tip cut short, a bulge over a few pixels —
    and the edge that carries on through the junction is exact, so a mean over
    the zone would hide it; the upper decile is what the eye sees at 750 %.
    """
    t = to_rgb_on_white(rasterize(truth_svg, width * scale, height * scale))
    o = to_rgb_on_white(rasterize(out_svg, width * scale, height * scale))
    te, oe = _edges(t), _edges(o)
    if not te.any() or not oe.any():
        return {"outline_px": None, "outline_p99_px": None, "junction_px": None}
    d_to_out = ndimage.distance_transform_edt(~oe)
    d_to_truth = ndimage.distance_transform_edt(~te)
    forward = d_to_out[te] / scale
    backward = d_to_truth[oe] / scale
    both = np.concatenate([forward, backward])
    junction = _junction_mask(t, te, scale)
    junction_px = None
    if junction.any():
        near = ndimage.distance_transform_edt(~junction) <= JUNCTION_REACH * scale
        sel = np.concatenate([near[te], near[oe]])
        if sel.any():
            junction_px = float(np.percentile(both[sel], 90))
    return {
        "outline_px": float(both.mean()),
        "outline_p99_px": float(np.percentile(both, 99)),
        "junction_px": junction_px,
    }


_NUM = re.compile(r"-?\d*\.?\d+(?:e-?\d+)?")


def _segments(d: str):
    """Absolute M/L/C/Z only; None on any other command (relative emitters)."""
    out, cur, start = [], None, None
    for cmd, body in re.findall(r"([A-Za-z])([^A-Za-z]*)", d):
        nums = [float(x) for x in _NUM.findall(body)]
        if cmd == "M":
            cur = start = np.array(nums[:2])
        elif cmd == "L":
            for k in range(0, len(nums) - 1, 2):
                p = np.array(nums[k:k + 2])
                out.append(("L", cur, p))
                cur = p
        elif cmd == "C":
            for k in range(0, len(nums) - 5, 6):
                p = np.array(nums[k + 4:k + 6])
                out.append(("C", cur, np.array(nums[k:k + 2]), np.array(nums[k + 2:k + 4]), p))
                cur = p
        elif cmd == "Z":
            cur = start
        else:
            return None
    return out


def _bow(seg) -> float:
    p0, c1, c2, p1 = seg[1:]
    t = np.linspace(0, 1, 17)[:, None]
    q = (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * p1
    d = p1 - p0
    n = np.linalg.norm(d)
    if n < 1e-9:
        return float(np.linalg.norm(q - p0, axis=1).max())
    return float(np.abs((q - p0) @ np.array([-d[1], d[0]]) / n).max())


def line_debt(svg: str) -> dict:
    """Chord length and count of cubics that should have been lines, and the
    segment density of the whole file. None for emitters using relative commands."""
    debt_px, debt_n, segs_n, length = 0.0, 0, 0, 0.0
    for d in re.findall(r'<path[^>]*\sd="([^"]*)"', svg):
        segs = _segments(d)
        if segs is None:
            return {"line_debt_px": None, "line_debt_segments": None, "nodes_per_100px": None}
        for s in segs:
            chord = float(np.linalg.norm(s[-1] - s[1]))
            segs_n += 1
            length += chord
            if s[0] == "C" and chord >= LINE_MIN and _bow(s) <= LINE_BOW:
                debt_px += chord
                debt_n += 1
    return {
        "line_debt_px": debt_px,
        "line_debt_segments": debt_n,
        "nodes_per_100px": (100.0 * segs_n / length) if length > 0 else None,
    }
