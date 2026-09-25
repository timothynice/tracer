"""Overlap decomposition: blended regions become overlapping shapes.

Where a semi-transparent shape T lies over another shape X, the traced image
has an extra region C whose colour is a blend: `C = α·T_colour + (1−α)·X_vis`.
A designer drew T and X, not C. Every region C that is explained this way by a
pair of its neighbours is absorbed into *both* shapes: T (painted on top with
`fill-opacity α`) and X (beneath). Absorption is transitive — the triple
overlap of three circles is "T over (A∩B)", and A∩B belongs to A and B, so the
triple joins A and B as well. A top shape only keeps its absorptions when the
extended outline is *simpler* than the bare region (a primitive, or clearly
more convex — the T-junction cue that the occluded contour continues).

Against a transparent background a shape's own alpha is its opacity; over an
opaque background the two blend equations give α and the true colour.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from skimage.measure import find_contours
from skimage.morphology import convex_hull_image

from studi0trace.engines.vexel.curves import CurveParams, PathShape, fit_shape
from studi0trace.engines.vexel.fills import Fill, Solid
from studi0trace.engines.vexel.merge import adjacency


# Least share of an overlap region's outline that must run along the two shapes
# it is the overlap of (or along other overlaps): see `decompose_overlaps`.
OVERLAP_OUTLINE = 0.5


@dataclass
class Decomposition:
    masks: dict[int, np.ndarray] = field(default_factory=dict)  # extended footprint per shape label
    fills: dict[int, Solid] = field(default_factory=dict)  # top shapes: true colour + opacity
    removed: set[int] = field(default_factory=set)  # blended regions that are no longer drawn
    above: list[tuple[int, int]] = field(default_factory=list)  # (top, below) ordering constraints
    over_backdrop: set[int] = field(default_factory=set)  # opaque regions made tops, solved over the opaque backdrop

    @property
    def empty(self) -> bool:
        return not self.removed


def _solidity(mask: np.ndarray) -> float:
    area = float(mask.sum())
    return area / float(convex_hull_image(mask).sum()) if area else 0.0


def _simpler(union: np.ndarray, part: np.ndarray, curve_params: CurveParams) -> bool:
    """The union outline is a primitive, or clearly more convex than the part."""
    polys = [np.column_stack([c[:, 1] - 0.5, c[:, 0] - 0.5]) for c in find_contours(np.pad(union.astype(np.float32), 1), 0.5)]
    polys = [p for p in polys if len(p) >= 8]
    if len(polys) == 1 and not isinstance(fit_shape(polys, curve_params), PathShape):
        return True
    return _solidity(union) > _solidity(part) + 0.06


def _blend(t_vis: np.ndarray, c_vis: np.ndarray, x_vis: np.ndarray, bg: np.ndarray | None) -> tuple[float, np.ndarray, float] | None:
    """Solve C = α·Tc + (1−α)·X for (α, Tc, rms residual); colours rgba 0–255."""
    if t_vis[3] < 250:  # semi-transparent over transparency: alpha is the opacity
        alpha = float(t_vis[3] / 255.0)
        if not 0.12 <= alpha <= 0.95:
            return None  # an almost-opaque shape does not produce a visible blend
        tc = t_vis[:3].copy()
    else:
        if bg is None:
            return None
        d1 = t_vis[:3] - c_vis[:3]
        d2 = bg[:3] - x_vis[:3]
        den = float(np.dot(d2, d2))
        if den < 1e-6:
            return None
        one_minus = float(np.dot(d1, d2) / den)
        alpha = 1.0 - one_minus
        if not 0.12 <= alpha <= 0.97:
            return None
        tc = (t_vis[:3] - one_minus * bg[:3]) / alpha
        if (tc < -12).any() or (tc > 267).any():
            return None
        tc = np.clip(tc, 0, 255)
    pred = alpha * tc + (1.0 - alpha) * x_vis[:3]
    resid = float(np.sqrt(np.mean((pred - c_vis[:3]) ** 2)))
    # The blend must be a *different* colour from both parents; otherwise C is
    # just an anti-aliased sliver of one of them, not an overlap.
    if np.linalg.norm(c_vis[:3] - t_vis[:3]) < 6.0 or np.linalg.norm(c_vis[:3] - x_vis[:3]) < 6.0:
        return None
    return alpha, tc, resid


def decompose_overlaps(
    labels: np.ndarray,
    fills: dict[int, Fill],
    visible: dict[int, bool],
    curve_params: CurveParams,
    tol: float,
    max_region_frac: float = 0.6,
) -> Decomposition:
    dec = Decomposition()
    edges = adjacency(labels)
    nbrs: dict[int, set[int]] = {}
    for a, b in edges:
        nbrs.setdefault(a, set()).add(b)
        nbrs.setdefault(b, set()).add(a)
    solid_vis = {lab for lab, f in fills.items() if visible.get(lab) and isinstance(f, Solid)}
    if len(solid_vis) < 3:
        return dec

    areas = {lab: int((labels == lab).sum()) for lab in solid_vis}
    border = np.zeros(labels.shape, bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    border_labels = set(np.unique(labels[border]).tolist())
    background = max((lab for lab in solid_vis if lab in border_labels), key=lambda l: areas[l], default=None)
    bg_colour = fills[background].rgba if background is not None and fills[background].rgba[3] > 250 else None
    total = labels.size

    # 1. explain each candidate region by the best (top, under) pair of its neighbours
    explained: dict[int, tuple[int, int, float, np.ndarray]] = {}  # C -> (T, X, alpha, Tc)
    for c in solid_vis:
        if c == background or areas[c] > max_region_frac * total:
            continue
        cand = [n for n in nbrs.get(c, set()) if n in solid_vis and n != background]
        best = None
        for t in cand:
            for x in cand:
                if t == x:
                    continue
                res = _blend(fills[t].rgba, fills[c].rgba, fills[x].rgba, bg_colour)
                if res is None or res[2] > tol:
                    continue
                if best is None or res[2] < best[0]:
                    best = (res[2], t, x, res[0], res[1])
        if best is not None:
            explained[c] = (best[1], best[2], best[3], best[4])

    if not explained:
        return dec

    # An overlap is the part two shapes share, so its outline is theirs: the
    # top's outline where it crosses the shape beneath, the under shape's where
    # it passes under the top, another overlap's where three shapes meet. A
    # region whose outline mostly runs along anything else is not these two
    # shapes' overlap, whatever its colour: a JPEG's whole face (75 000 px)
    # read as a 20 % white eye over a 22-pixel rim sliver whose chroma had
    # rung, was dropped, and was repainted over its own nose and mouth.
    perimeter: dict[int, float] = {}
    for (a, b), (cnt, _g) in edges.items():
        perimeter[a] = perimeter.get(a, 0.0) + cnt
        perimeter[b] = perimeter.get(b, 0.0) + cnt

    def shared(a: int, b: int) -> float:
        return edges.get((min(a, b), max(a, b)), [0.0, 0.0])[0]

    candidates = set(explained)
    for c in sorted(explained):
        t, x = explained[c][0], explained[c][1]
        own = sum(shared(c, n) for n in nbrs.get(c, set()) if n in (t, x) or n in candidates)
        if own < OVERLAP_OUTLINE * perimeter.get(c, 0.0):
            del explained[c]
    if not explained:
        return dec

    # 2. a top shape keeps its absorptions only if the extended outline is simpler
    tops: dict[int, list[int]] = {}
    for c, (t, _x, _a, _tc) in explained.items():
        tops.setdefault(t, []).append(c)
    accepted: dict[int, tuple[int, int, float, np.ndarray]] = {}
    for t, cs in tops.items():
        t_mask = labels == t
        union = t_mask | np.isin(labels, cs)
        if _simpler(union, t_mask, curve_params):
            for c in cs:
                accepted[c] = explained[c]
    if not accepted:
        return dec

    # 3. footprints: T ∪ C for tops, X ∪ C beneath, transitively through removed regions
    members: dict[int, set[int]] = {}

    def add(shape: int, region: int, depth: int = 0) -> None:
        if shape in accepted and depth < 8:  # shape is itself a removed overlap: pass to its owners
            t, x, _a, _tc = accepted[shape]
            add(t, region, depth + 1)
            add(x, region, depth + 1)
            return
        members.setdefault(shape, set()).add(region)

    for c, (t, x, alpha, tc) in accepted.items():
        add(t, c)
        add(x, c)
        dec.removed.add(c)
    def resolve(shape: int, index: int) -> int:
        """Follow a removed region to the shape that owns it (0 = top, 1 = under); cycle-safe."""
        seen = {shape}
        while shape in accepted:
            shape = accepted[shape][index]
            if shape in seen:
                break
            seen.add(shape)
        return shape

    for c, (t, x, alpha, tc) in accepted.items():
        if t not in accepted:
            dec.fills[t] = Solid(np.array([tc[0], tc[1], tc[2], alpha * 255.0]))
            # An opaque region read as a translucent shape over the backdrop:
            # its own area is its colour with the backdrop beneath it.
            if fills[t].rgba[3] >= 250 and bg_colour is not None:
                dec.over_backdrop.add(t)
        top_owner = resolve(t, 0)
        under = resolve(x, 1)
        if top_owner != under and top_owner not in accepted and under not in accepted:
            dec.above.append((top_owner, under))
    for shape, regs in members.items():
        if shape in dec.removed:
            continue
        dec.masks[shape] = (labels == shape) | np.isin(labels, sorted(regs))
    return dec
