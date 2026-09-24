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


MAX_JUNCTION_COLOURS = 24


def _flat(code: np.ndarray) -> np.ndarray:
    """Pixels whose 3x3 neighbourhood is one colour: region interior, never the
    one-pixel anti-aliasing band along an edge."""
    flat = np.ones(code.shape, bool)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                flat &= np.roll(np.roll(code, dy, axis=0), dx, axis=1) == code
    flat[0, :] = flat[-1, :] = False
    flat[:, 0] = flat[:, -1] = False
    return flat


def _junction_mask(rgb: np.ndarray, edges: np.ndarray, scale: int) -> np.ndarray | None:
    """Edge pixels within one source pixel of three or more flat regions.

    Only interior pixels vote, so the mixtures along an anti-aliased edge cannot
    pass for a third region. Colours are quantised to 32 levels. A truth with
    more than MAX_JUNCTION_COLOURS flat colours (a gradient) has no junctions
    worth the name and returns None.
    """
    q = rgb.astype(np.int32) >> 3
    code = (q[..., 0] << 10) | (q[..., 1] << 5) | q[..., 2]
    flat = _flat(code)
    colours = np.unique(code[flat])
    if len(colours) < 3 or len(colours) > MAX_JUNCTION_COLOURS:
        return None
    size = 2 * scale + 1
    count = np.zeros(code.shape, np.int8)
    for c in colours:
        count += ndimage.maximum_filter(flat & (code == c), size=size).astype(np.int8)
    return edges & (count >= 3)


_TRUTH_CACHE: dict = {}


def _truth_side(truth_svg: str, width: int, height: int, scale: int):
    """The truth's edges, the distance to them and its junction zone. Two thirds
    of `outline_error`'s cost and the same for every engine scored against one
    truth, so the last one is kept (one entry: the arrays are ~200 MB at 8x 512 px)."""
    key = (truth_svg, width, height, scale)
    hit = _TRUTH_CACHE.get(key)
    if hit is not None:
        return hit
    t = to_rgb_on_white(rasterize(truth_svg, width * scale, height * scale))
    te = _edges(t)
    d_to_truth = ndimage.distance_transform_edt(~te) if te.any() else None
    junction = _junction_mask(t, te, scale) if te.any() else None
    near = None
    if junction is not None and junction.any():
        near = ndimage.distance_transform_edt(~junction) <= JUNCTION_REACH * scale
    _TRUTH_CACHE.clear()
    _TRUTH_CACHE[key] = (te, d_to_truth, near)
    return _TRUTH_CACHE[key]


def outline_error(truth_svg: str, out_svg: str, width: int, height: int, scale: int = 8) -> dict:
    """Symmetric Chamfer distance between the truth's edges and the output's, in source px.

    `outline_px` is the mean over both edge sets, `outline_p99_px` the 99th
    percentile, and `junction_px` the 90th percentile restricted to within
    `JUNCTION_REACH` of a truth junction (None when the truth has no junctions).
    A junction defect is local — a tip cut short, a bulge over a few pixels —
    and the edge that carries on through the junction is exact, so a mean over
    the zone would hide it; the upper decile is what the eye sees at 750 %.
    """
    te, d_to_truth, near = _truth_side(truth_svg, width, height, scale)
    o = to_rgb_on_white(rasterize(out_svg, width * scale, height * scale))
    oe = _edges(o)
    if not te.any() or not oe.any():
        return {"outline_px": None, "outline_p99_px": None, "junction_px": None}
    d_to_out = ndimage.distance_transform_edt(~oe)
    forward = d_to_out[te] / scale
    backward = d_to_truth[oe] / scale
    both = np.concatenate([forward, backward])
    junction_px = None
    if near is not None:
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
    """Absolute M/L/C/A/Z only; None on any other command (relative emitters)."""
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
        elif cmd == "A":
            for k in range(0, len(nums) - 6, 7):
                p = np.array(nums[k + 5:k + 7])
                out.append(("A", cur, _arc_points(cur, p, nums[k], nums[k + 3] != 0, nums[k + 4] != 0), p))
                cur = p
        elif cmd == "Z":
            cur = start
        else:
            return None
    return out


def _arc_points(p0: np.ndarray, p1: np.ndarray, r: float, large: bool, sweep: bool, n: int = 17) -> np.ndarray:
    """Points along an SVG circular arc (equal radii, no rotation), as a renderer draws it."""
    mid = 0.5 * (p0 + p1)
    d = p1 - p0
    half = 0.5 * float(np.linalg.norm(d))
    if half < 1e-12:
        return np.vstack([p0, p1])
    r = max(r, half)
    h = np.sqrt(max(r * r - half * half, 0.0))
    nrm = np.array([-d[1], d[0]]) / (2.0 * half)
    c = mid + nrm * h if sweep != large else mid - nrm * h
    a0 = np.arctan2(p0[1] - c[1], p0[0] - c[0])
    a1 = np.arctan2(p1[1] - c[1], p1[0] - c[0])
    span = (a1 - a0) % (2 * np.pi) if sweep else -((a0 - a1) % (2 * np.pi))
    t = a0 + span * np.linspace(0.0, 1.0, n)
    rr = float(np.linalg.norm(p0 - c))
    return np.column_stack([c[0] + rr * np.cos(t), c[1] + rr * np.sin(t)])


def _bow(seg) -> float:
    if seg[0] == "A":
        p0, q, p1 = seg[1], seg[2], seg[3]
    else:
        p0, c1, c2, p1 = seg[1:]
        t = np.linspace(0, 1, 17)[:, None]
        q = (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * p1
    d = p1 - p0
    n = np.linalg.norm(d)
    if n < 1e-9:
        return float(np.linalg.norm(q - p0, axis=1).max())
    return float(np.abs((q - p0) @ np.array([-d[1], d[0]]) / n).max())


def root_scale(svg: str) -> float:
    """The factor a trace drawn inside `<g transform="scale(s)">` applies to
    its coordinates (Vexel's small-input upsampling writes 0.5); 1 otherwise."""
    m = re.search(r'<g transform="scale\(([\d.]+)\)">', svg)
    return float(m.group(1)) if m else 1.0


def line_debt(svg: str) -> dict:
    """Chord length and count of cubics that should have been lines, and the
    segment density of the whole file. None for emitters using relative commands."""
    debt_px, debt_n, segs_n, length = 0.0, 0, 0, 0.0
    scale = root_scale(svg)
    for d in re.findall(r'<path[^>]*\sd="([^"]*)"', svg):
        segs = _segments(d)
        if segs is None:
            return {"line_debt_px": None, "line_debt_segments": None, "nodes_per_100px": None}
        for s in segs:
            chord = float(np.linalg.norm(s[-1] - s[1])) * scale
            segs_n += 1
            length += chord
            if s[0] in ("C", "A") and chord >= LINE_MIN and _bow(s) * scale <= LINE_BOW:
                debt_px += chord
                debt_n += 1
    return {
        "line_debt_px": debt_px,
        "line_debt_segments": debt_n,
        "nodes_per_100px": (100.0 * segs_n / length) if length > 0 else None,
    }
