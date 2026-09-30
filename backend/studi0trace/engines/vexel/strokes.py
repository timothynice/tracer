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
from functools import lru_cache

import numpy as np
from scipy import ndimage
from skimage.morphology._skeletonize import _pattern_of, _table_lookup
from skimage.morphology._skeletonize_various_cy import _skeletonize_loop

from studi0trace.engines.vexel.curves import CurveParams, Segment, fit_closed_smooth, fit_open, path_d

_OFFSETS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_EIGHT = ndimage.generate_binary_structure(2, 2)


@lru_cache(maxsize=1)
def _skeleton_tables() -> tuple[np.ndarray, np.ndarray]:
    """skimage's keep table (a foreground pixel stays when removing it would
    change the local connectivity, or when it has fewer than three neighbours)
    and its cornerness table (background cells in the 3x3 neighbourhood)."""
    patterns = [_pattern_of(index) for index in range(512)]
    centre = (np.arange(512) & 16).astype(bool)
    splits = np.array([
        ndimage.label(p, _EIGHT)[1] != ndimage.label(_pattern_of(index & ~16), _EIGHT)[1]
        for index, p in enumerate(patterns)
    ])
    few = np.array([p.sum() < 3 for p in patterns])
    keep = centre & (splits | few)
    corner = np.array([9 - p.sum() for p in patterns])
    return np.ascontiguousarray(keep, np.uint8), corner


def medial_axis(image: np.ndarray) -> np.ndarray:
    """`skimage.morphology.medial_axis`, with a deterministic processing order.

    skimage thins pixels in order of distance to the background, then
    cornerness, and breaks the remaining ties with a generator seeded from the
    OS — so two runs over one image give two skeletons, and whether a thin
    region is stroked or filled changes between runs. Here the last tiebreak is
    a hash of the pixel's raster index (`_pixel_keys`), which
    `vexel-rs/src/core/skeleton.rs` sorts by too, so the two engines agree. It
    separates only pixels that already tie on distance and cornerness, and it is
    as even-handed as the random draw. The raster index itself is not: it thins
    the same side of a two-pixel line first everywhere, which puts the skeleton
    of a two-pixel ring half a pixel off centre all the way round, and the
    stroke fidelity of that centreline fails the gate the random draws pass.
    """
    mask = np.ascontiguousarray(image, dtype=bool)
    keep, cornerness = _skeleton_tables()
    distance = ndimage.distance_transform_edt(mask)
    corner = _table_lookup(mask, cornerness)
    rows, cols = np.nonzero(mask)  # raster order
    if rows.size == 0:
        return np.zeros_like(mask)
    keys = _pixel_keys(rows.astype(np.int64) * mask.shape[1] + cols)
    order = np.lexsort((keys, corner[mask], distance[mask]))
    result = np.ascontiguousarray(mask, np.uint8)
    _skeletonize_loop(result, np.ascontiguousarray(rows, np.intp), np.ascontiguousarray(cols, np.intp),
                      np.ascontiguousarray(order, np.int32), keep)
    return result.astype(bool)


def _pixel_keys(index: np.ndarray) -> np.ndarray:
    """A predictable pseudo-random 64-bit key per raster index (the splitmix64
    finaliser, a bijection, so distinct pixels get distinct keys)."""
    with np.errstate(over="ignore"):
        z = np.asarray(index, dtype=np.uint64) + np.uint64(0x9E3779B97F4A7C15)
        z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        return z ^ (z >> np.uint64(31))


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


# The centreline is refined to the coverage's centroid across the stroke,
# sampled bilinearly every REFINE_STEP px out to REFINE_REACH px past the half
# width (the stroke and its anti-aliasing, not a neighbouring line),
# REFINE_ITER times: the tangents settle after the first pass. On rings of
# 1–3 px at four sub-pixel offsets the centreline is then within 0.08 px RMS
# of the true circle (0.11 with nearest-pixel samples) and the fidelity
# 0.02–0.05 (0.03–0.09); a third pass or a wider reach changes neither.
REFINE_STEP = 0.25
REFINE_REACH = 1.0
REFINE_ITER = 2


def _sample_bilinear(field: np.ndarray, x: float, y: float) -> float:
    """Bilinear coverage sample in SVG space (pixel centres at +0.5, zero
    beyond the last centres)."""
    h, w = field.shape
    fx, fy = x - 0.5, y - 0.5
    x0, y0 = int(np.floor(fx)), int(np.floor(fy))
    tx, ty = fx - x0, fy - y0

    def at(r: int, c: int) -> float:
        return float(field[r, c]) if 0 <= r < h and 0 <= c < w else 0.0

    top = at(y0, x0) * (1.0 - tx) + at(y0, x0 + 1) * tx
    bottom = at(y0 + 1, x0) * (1.0 - tx) + at(y0 + 1, x0 + 1) * tx
    return top * (1.0 - ty) + bottom * ty


def _refine_centreline(xy: np.ndarray, closed: bool, coverage: np.ndarray, width: float) -> np.ndarray:
    """Move every vertex along its normal to the centroid of the coverage
    across the stroke there.

    The medial axis is a pixel path: on a two-pixel line it runs down one of
    the two rows, half a pixel off the middle, and which row is the thinning
    order's choice. The smoothing takes out the zig-zag, not that offset, and
    the fidelity of the line against its own coverage then sat at the gate —
    0.19 to 0.25 on thin-mark's 2 px ring, the gate at 0.2, by the tie-break.
    The coverage says where the middle is to a fraction of a pixel."""
    n = len(xy)
    if n < 2:
        return xy
    out = xy.astype(float).copy()
    # truncated, never rounded: at the 0.25 px width floor the count is 4.5,
    # which Python rounds to 4 and Rust to 5
    steps = int((width / 2.0 + REFINE_REACH) / REFINE_STEP)
    offsets = [k * REFINE_STEP for k in range(-steps, steps + 1)]
    for _ in range(REFINE_ITER):
        cur = out.copy()
        for i in range(n):
            if closed:
                prev, nxt = cur[(i - 1) % n], cur[(i + 1) % n]
            else:
                prev, nxt = cur[max(i - 1, 0)], cur[min(i + 1, n - 1)]
            tx, ty = nxt[0] - prev[0], nxt[1] - prev[1]
            length = float(np.hypot(tx, ty))
            if length < 1e-9:
                continue
            nx, ny = -ty / length, tx / length
            total = 0.0
            moment = 0.0
            for s in offsets:
                c = _sample_bilinear(coverage, cur[i][0] + s * nx, cur[i][1] + s * ny)
                total += c
                moment += s * c
            if total <= 1e-9:
                continue
            shift = moment / total
            out[i] = [cur[i][0] + shift * nx, cur[i][1] + shift * ny]
    return out


def _length(xy: np.ndarray, closed: bool) -> float:
    total = float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum())
    if closed:
        total += float(np.linalg.norm(xy[-1] - xy[0]))
    return total


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
    # All ink the caller attributed to this stroke (the coverage field may extend
    # one pixel beyond `mask` to catch faint anti-aliased pixels of a sub-pixel line).
    ink_area = float(coverage.sum())
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
    # the skeleton's pixel path is put on the coverage's middle; the length,
    # and so the width, are then those of the line actually drawn
    polylines = [_refine_centreline(xy, c, coverage, width) for xy, c in zip(polylines, closed)]
    total_len = sum(_length(xy, c) for xy, c in zip(polylines, closed))
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


# The centreline is fitted at this share of the curve tolerance: an error on a
# stroke's centreline shows on both of its edges, and the centreline is known
# to 0.08 px. At the full tolerance the cubics through thin-mark's 150 px ring
# sagged 0.14 px inside it (seam 16651 ppm); at half, 0.11 px and 11399 with
# 24 segments for 16; at a quarter the fit chases the centreline's noise (209
# segments, artifact index 0.4 → 6.7).
STROKE_FIT_SHARE = 0.5


def stroke_svg(stroke: Stroke, colour: str, opacity: float, params: CurveParams, precision: int) -> str:
    """One <path> per cap style (closed loops join the round group)."""
    groups: dict[str, list[str]] = {"round": [], "butt": []}
    caps = stroke.caps or ["round"] * len(stroke.polylines)
    tol = params.tol * STROKE_FIT_SHARE
    for xy, is_closed, cap in zip(stroke.polylines, stroke.closed, caps):
        if is_closed and len(xy) >= 4:
            groups["round"].append(path_d([fit_closed_smooth(xy, tol)], precision))
        elif len(xy) >= 2:
            d = path_d([fit_open(xy, tol)], precision)
            groups[cap].append(d[:-1] if d.endswith("Z") else d)  # open strokes: no Z
    op = "" if opacity >= 0.995 else f' stroke-opacity="{opacity:.3f}"'
    w = f"{stroke.width:.{max(precision, 2)}f}".rstrip("0").rstrip(".")
    out = []
    for cap, parts in groups.items():
        if parts:
            out.append(f'<path d="{"".join(parts)}" fill="none" stroke="{colour}" stroke-width="{w}" stroke-linecap="{cap}" stroke-linejoin="round"{op}/>')
    return "".join(out)

def stroke_fidelity(stroke: Stroke, coverage: np.ndarray) -> float:
    """RMS between the coverage a constant-width centreline would paint and the
    coverage actually measured.

    A drawn line *is* a constant-width centreline, so this is near zero. A
    letterform is made of strokes too — geometrically it passes every test for
    thinness, elongation and width consistency — but its terminals and joins are
    not what a single centreline paints, and that shows up here. Measuring the
    reconstruction is the only test that separated the two.

    The prediction is exact to the sub-pixel: each pixel centre's distance to
    the centreline itself, and the coverage of a band of the stroke's width
    across a unit pixel at that distance. Rasterised to whole pixels (the
    centreline's pixels and a distance transform of them), a line half a pixel
    off the lattice was scored as if it lay on it, 0.2 of error it did not have.
    """
    h, w = coverage.shape
    half = stroke.width / 2.0
    reach = half + 2.0
    dist = _polyline_distance(stroke.polylines, stroke.closed, (h, w), reach)
    near = dist <= reach
    if not near.any():
        return float("inf")
    d = dist[near]
    predicted = np.clip(np.minimum(d + 0.5, half) - np.maximum(d - 0.5, -half), 0.0, 1.0)
    return float(np.sqrt(np.mean((predicted - coverage[near]) ** 2)))


def _polyline_distance(polylines: list[np.ndarray], closed: list[bool], shape: tuple[int, int], reach: float) -> np.ndarray:
    """Distance from every pixel centre within `reach` of the polylines to the
    nearest point on them; infinity elsewhere. An open polyline's band ends
    where it ends: a pixel whose centre projects past an end takes no distance
    from the end segment. The cap there is `_finish_ends`'s guess, and the
    measure is of the line, not the guess — a square-ended 3 px bar drawn
    exactly on its axis scored 0.12 for the round cap's ink it does not have,
    and 0.14 in the engine, where the partition had cut its corners and the
    guess was round."""
    h, w = shape
    dist = np.full((h, w), np.inf)
    for xy, is_closed in zip(polylines, closed):
        pts = np.vstack([xy, xy[:1]]) if is_closed else xy
        butt = not is_closed
        last = len(pts) - 2
        for k, ((ax, ay), (bx, by)) in enumerate(zip(pts[:-1], pts[1:])):
            c0 = max(int(np.floor(min(ax, bx) - reach)), 0)
            c1 = min(int(np.ceil(max(ax, bx) + reach)) + 1, w)
            r0 = max(int(np.floor(min(ay, by) - reach)), 0)
            r1 = min(int(np.ceil(max(ay, by) + reach)) + 1, h)
            if c1 <= c0 or r1 <= r0:
                continue
            cy, cx = np.mgrid[r0:r1, c0:c1]
            cx = cx + 0.5
            cy = cy + 0.5
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy
            if l2 < 1e-18:
                raw = np.zeros(cx.shape)
            else:
                raw = ((cx - ax) * dx + (cy - ay) * dy) / l2
            t = np.clip(raw, 0.0, 1.0)
            dd = np.hypot(cx - (ax + t * dx), cy - (ay + t * dy))
            if butt and k == 0:
                dd = np.where(raw < 0.0, np.inf, dd)
            if butt and k == last:
                dd = np.where(raw > 1.0, np.inf, dd)
            dist[r0:r1, c0:c1] = np.minimum(dist[r0:r1, c0:c1], dd)
    return dist

