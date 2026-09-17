"""Stroke recovery: thin regions become stroked centreline paths.

A one- or two-pixel line traced as a *filled* region is a sliver of
semi-transparent fill — visually close, but not what the artist drew. When a
region is thin everywhere (its medial-axis distance never exceeds a couple of
pixels) we instead recover the centreline with the medial axis, estimate the
stroke width from ink area ÷ centreline length (so anti-aliased sub-pixel
lines come out at their true width), fit the centreline with curves and emit
`<path fill="none" stroke=… stroke-width=…>`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from skimage.morphology import medial_axis

from studi0trace.engines.vexel.curves import CurveParams, Segment, fit_closed_smooth, fit_open, path_d

_OFFSETS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@dataclass
class Stroke:
    polylines: list[np.ndarray] = field(default_factory=list)  # (N, 2) xy in SVG space
    closed: list[bool] = field(default_factory=list)
    caps: list[str] = field(default_factory=list)  # "butt" | "round" per polyline (ignored when closed)
    width: float = 1.0


def _sample(field: np.ndarray, x: float, y: float) -> float:
    """Nearest-pixel coverage sample in SVG space (pixel centres at +0.5)."""
    r, c = int(np.floor(y)), int(np.floor(x))
    if 0 <= r < field.shape[0] and 0 <= c < field.shape[1]:
        return float(field[r, c])
    return 0.0


def _finish_ends(xy: np.ndarray, width: float, coverage: np.ndarray) -> tuple[np.ndarray, str]:
    """Extend the medial axis to the stroke's real end and pick the cap style.

    The medial axis stops about w/2 short of a line's end. A butt-ended line has
    ink at the corners of the extended tip; a round-ended one does not.
    """
    if len(xy) < 2 or width <= 0:
        return xy, "round"
    out = xy.astype(float).copy()
    votes_butt = 0
    for end in (0, -1):
        p = out[end]
        q = out[1] if end == 0 else out[-2]
        t = p - q
        n = np.linalg.norm(t)
        if n < 1e-9:
            continue
        t /= n
        normal = np.array([-t[1], t[0]])
        tip = p + t * (width / 2.0)
        corner_ink = [_sample(coverage, *(tip + s * normal * 0.4 * width)) for s in (-1.0, 1.0)]
        centre_ink = _sample(coverage, *(p + t * (width * 0.35)))
        if centre_ink > 0.3 and min(corner_ink) > 0.35:
            votes_butt += 1
            out[end] = tip  # ink reaches the corners: square end, extend to it
        elif centre_ink > 0.3:
            out[end] = p + t * (width * 0.15)  # round cap covers the rest
    return out, ("butt" if votes_butt == 2 else "round")


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    rows = np.nonzero(mask.any(axis=1))[0]
    cols = np.nonzero(mask.any(axis=0))[0]
    return rows[0], rows[-1] + 1, cols[0], cols[-1] + 1


def is_thin(mask: np.ndarray, max_half_width: float = 1.75, min_pixels: int = 8) -> bool:
    """True when no pixel of the region is farther than `max_half_width` from its boundary."""
    if mask.sum() < min_pixels:
        return False
    r0, r1, c0, c1 = _bbox(mask)
    crop = np.pad(mask[r0:r1, c0:c1], 1)
    dist = ndimage.distance_transform_edt(crop)
    return float(dist.max()) <= max_half_width + 0.5


def _trace_skeleton(skel: np.ndarray) -> list[tuple[list[tuple[int, int]], bool]]:
    """Split a skeleton into paths: endpoint→(endpoint|junction) chains, then cycles."""
    pts = set(zip(*np.nonzero(skel)))
    if not pts:
        return []

    def nbrs(p: tuple[int, int]) -> list[tuple[int, int]]:
        return [(p[0] + dr, p[1] + dc) for dr, dc in _OFFSETS if (p[0] + dr, p[1] + dc) in pts]

    degree = {p: len(nbrs(p)) for p in pts}
    used_edges: set[frozenset] = set()
    paths: list[tuple[list[tuple[int, int]], bool]] = []

    def walk(start: tuple[int, int], first: tuple[int, int]) -> list[tuple[int, int]]:
        path = [start, first]
        used_edges.add(frozenset((start, first)))
        prev, cur = start, first
        while degree[cur] == 2:
            nxt = [q for q in nbrs(cur) if q != prev]
            if not nxt:
                break
            nxt = nxt[0]
            e = frozenset((cur, nxt))
            if e in used_edges:
                break
            used_edges.add(e)
            path.append(nxt)
            prev, cur = cur, nxt
        return path

    # chains from endpoints and junctions
    for p in sorted(pts):
        if degree[p] == 2:
            continue
        for q in nbrs(p):
            if frozenset((p, q)) not in used_edges:
                paths.append((walk(p, q), False))
    # remaining pure cycles
    for p in sorted(pts):
        if degree[p] != 2:
            continue
        for q in nbrs(p):
            if frozenset((p, q)) not in used_edges:
                cyc = walk(p, q)
                if len(cyc) > 3 and cyc[-1] == cyc[0]:
                    paths.append((cyc[:-1], True))
                elif len(cyc) > 3 and cyc[-1] in nbrs(cyc[0]):
                    paths.append((cyc, True))
                else:
                    paths.append((cyc, False))
                break
    return paths


def _smooth(xy: np.ndarray, closed: bool, window: int = 5) -> np.ndarray:
    """Moving average along the polyline: removes the medial axis' pixel zig-zag,
    which would otherwise inflate the centreline length (and shrink the width)."""
    n = len(xy)
    if n < window:
        return xy
    k = window // 2
    if closed:
        padded = np.vstack([xy[-k:], xy, xy[:k]])
        kernel = np.ones(window) / window
        sm = np.column_stack([np.convolve(padded[:, i], kernel, mode="valid") for i in range(2)])
        return sm
    out = xy.astype(float).copy()
    for i in range(1, n - 1):
        lo, hi = max(0, i - k), min(n, i + k + 1)
        out[i] = xy[lo:hi].mean(axis=0)
    return out


def stroke_geometry(mask: np.ndarray, coverage: np.ndarray, min_length: float = 3.0) -> Stroke | None:
    """Centreline polylines (SVG pixel space) and width for a thin region.

    coverage: per-pixel ink coverage of the region (0–1), e.g. alpha against a
    transparent background or the anti-aliasing coverage field.
    """
    r0, r1, c0, c1 = _bbox(mask)
    crop = mask[r0:r1, c0:c1]
    skel = medial_axis(np.pad(crop, 1))[1:-1, 1:-1]
    if not skel.any():
        return None
    paths = _trace_skeleton(skel)
    if not paths:
        return None
    ink_area = float(coverage[mask].sum())
    polylines: list[np.ndarray] = []
    closed: list[bool] = []
    total_len = 0.0
    for pts, is_cycle in paths:
        xy = np.array([(c + c0 + 0.5, r + r0 + 0.5) for r, c in pts], dtype=float)
        xy = _smooth(xy, is_cycle)
        seg_len = float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum())
        if is_cycle:
            seg_len += float(np.linalg.norm(xy[-1] - xy[0]))
        total_len += seg_len
        polylines.append(xy)
        closed.append(is_cycle)
    if total_len < min_length:
        return None
    width = max(0.25, ink_area / total_len)
    # Stroke only when a filled region would serve badly: sub-pixel/one-pixel
    # lines (the fill would be a semi-transparent sliver) or genuinely line-like
    # features. Short thick pieces such as letter stems stay filled shapes.
    if total_len < 4.0 * width or (width >= 1.5 and total_len < 8.0 * width):
        return None
    # drop spurs much shorter than the stroke is wide (medial-axis artefacts)
    keep = [i for i, xy in enumerate(polylines) if closed[i] or np.linalg.norm(np.diff(xy, axis=0), axis=1).sum() >= max(min_length, 1.5 * width)]
    if not keep:
        return None
    # the width was measured against the un-extended centreline; re-estimate after
    # extending open ends so ink area / length stays consistent
    finished: list[np.ndarray] = []
    caps: list[str] = []
    for i in keep:
        if closed[i]:
            finished.append(polylines[i])
            caps.append("round")
        else:
            xy, cap = _finish_ends(polylines[i], width, coverage)
            finished.append(xy)
            caps.append(cap)
    new_len = sum(
        float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum()) + (float(np.linalg.norm(xy[-1] - xy[0])) if c else 0.0)
        for xy, c in zip(finished, [closed[i] for i in keep])
    )
    round_len = sum(width for c in caps if c == "round" for _ in (0,))  # each round-capped line adds ~w of cap area
    width = max(0.25, ink_area / max(new_len + 0.5 * round_len, 1e-6))
    return Stroke(polylines=finished, closed=[closed[i] for i in keep], caps=caps, width=width)


def stroke_svg(stroke: Stroke, colour: str, opacity: float, params: CurveParams, precision: int) -> str:
    """One <path> per cap style (closed loops join the round group)."""
    groups: dict[str, list[str]] = {"round": [], "butt": []}
    caps = stroke.caps or ["round"] * len(stroke.polylines)
    for xy, is_closed, cap in zip(stroke.polylines, stroke.closed, caps):
        if is_closed and len(xy) >= 4:
            groups["round"].append(path_d([fit_closed_smooth(xy, params.tol)], precision))
        elif len(xy) >= 2:
            d = path_d([fit_open(xy, params.tol)], precision)
            groups[cap].append(d[:-1] if d.endswith("Z") else d)  # open strokes: no Z
    op = "" if opacity >= 0.995 else f' stroke-opacity="{opacity:.3f}"'
    w = f"{stroke.width:.{max(precision, 2)}f}".rstrip("0").rstrip(".")
    out = []
    for cap, parts in groups.items():
        if parts:
            out.append(f'<path d="{"".join(parts)}" fill="none" stroke="{colour}" stroke-width="{w}" stroke-linecap="{cap}" stroke-linejoin="round"{op}/>')
    return "".join(out)
