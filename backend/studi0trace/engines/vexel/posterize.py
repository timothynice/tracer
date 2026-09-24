"""Optional stage for `gradients=False`: cut each fitted gradient into flat bands.

The partition never breaks a smooth ramp (that is the point of Vexel), so with
gradient fills disabled a ramp would collapse to one flat colour. Users who
turn gradients off expect posterised bands instead — and a designer
posterising a gradient cuts the *gradient*, not the pixels: a linear ramp into
strips between parallel straight lines, a radial one into rings between
concentric circles, each strip the mean of the ramp across it.

That is what this does. The trace runs as it would with gradients on, so each
ramp is one region with a fitted `Linear` or `Radial` fill; that fill is then
cut at levels of its own ramp parameter t, spaced so every band spans the same
colour distance (ΔE in Lab, alpha counting as `prepare.ALPHA_FEATURE_SCALE`)
and none more than `detail` of it. Band membership is read from t at each
pixel centre, never from the pixel's colour: posterising the pixels cut a ramp
along iso-lines of its noise, and those came out as wobbling edges.

The band edges are then placed on the level lines themselves (`Levels`,
consulted by `topology._place`): the vertex on each lattice edge between two
bands is where t crosses the level between the two pixel centres, so a linear
ramp's band edge is a line to the last bit and a radial ramp's is a circle,
which the fitter then writes as one `L` or one `A`. Everywhere else a band is
placed as its ramp would be: the colour it offers the sub-pixel placement is
the fitted gradient's (`Levels.model`), because that, and not the band's flat
paint, is what the anti-aliased pixels at its outline are a mix of.

Bands are never read as anything else downstream: not strokes (a narrow band
is thin by every test `strokes.is_thin` makes), not overlaps (`C = αT +
(1−α)X` holds by construction for the middle of three bands of a ramp).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from skimage.color import rgb2lab
from skimage.measure import label as cc_label

from studi0trace.engines.vexel.fills import Fill, FitParams, Linear, Radial, Solid, _interp_stops, fit_fill
from studi0trace.engines.vexel.prepare import ALPHA_FEATURE_SCALE
from studi0trace.engines.vexel.weights import interior

# Narrowest band, in px across its level lines. A ramp over a short span with a
# large colour range gets fewer, wider bands rather than slivers.
BAND_MIN_PX = 4.0

# Samples along t used to measure the ramp's colour distance.
RAMP_SAMPLES = 256

# A band piece thinner than this (2·area / perimeter, px) — where a level line
# grazes the region's outline — or smaller than `min_region` joins the
# neighbouring band it shares the most edge with.
THIN_BAND = 2.0


@dataclass(frozen=True)
class Levels:
    """Where each band came from: its ramp, and the levels that bound it."""

    band: dict[int, tuple[int, int]] = field(default_factory=dict)  # label -> (group, band index)
    fields: dict[int, Fill] = field(default_factory=dict)  # group -> its fitted Linear | Radial
    levels: dict[int, np.ndarray] = field(default_factory=dict)  # group -> t between band k and k+1, ascending

    def __bool__(self) -> bool:
        return bool(self.band)

    def model(self, lab: int) -> Fill | None:
        """The gradient a band was cut from, or None for a label that is no band."""
        found = self.band.get(int(lab))
        return None if found is None else self.fields[found[0]]

    def _between(self, a: int, b: int) -> tuple[Fill, float] | None:
        """(ramp, level) when `a` and `b` are consecutive bands of one ramp."""
        fa, fb = self.band.get(int(a)), self.band.get(int(b))
        if fa is None or fb is None or fa[0] != fb[0] or abs(fa[1] - fb[1]) != 1:
            return None
        return self.fields[fa[0]], float(self.levels[fa[0]][min(fa[1], fb[1])])

    def sibling(self, a: int, b: int) -> bool:
        """Is the edge between `a` and `b` a level line of one ramp?"""
        return self._between(a, b) is not None

    def onto(self, a: int, b: int, point: np.ndarray, along: int | None = None) -> np.ndarray:
        """`point` moved onto the level line between bands `a` and `b`: across
        it (along the ramp's direction for a linear ramp, radially for a
        radial), or, given `along` (0 = x, 1 = y), along that axis only, which
        is how a node held on the canvas edge reaches it. Unmoved where the
        line cannot be reached that way."""
        found = self._between(a, b)
        if found is None:
            return point
        ramp, level = found
        out = np.array(point, dtype=float)
        if isinstance(ramp, Linear):
            d = np.array([ramp.x2 - ramp.x1, ramp.y2 - ramp.y1])
            t = float(ramp.param(out[:1], out[1:])[0])
            if along is None:
                return out + (level - t) * d
            if abs(float(d[along])) < 1e-9:
                return out
            out[along] += (level - t) * float(d @ d) / float(d[along])
            return out
        centre = np.array([ramp.cx, ramp.cy])
        radius = level * ramp.r
        off = out - centre
        if along is None:
            dist = float(np.hypot(*off))
            return out if dist < 1e-9 else centre + off * (radius / dist)
        fixed = 1 - along
        rest = radius * radius - float(off[fixed]) ** 2
        if rest < 0.0:
            return out
        root = math.sqrt(rest)
        out[along] = float(centre[along]) + (root if off[along] >= 0.0 else -root)
        return out

    def crossing(self, a: int, b: int, c_a: np.ndarray, c_b: np.ndarray) -> np.ndarray | None:
        """For lattice edges between an `a` pixel centred at `c_a` (N, 2) and a
        `b` pixel at `c_b`: the fraction of the step from `c_a` to `c_b` at which
        the ramp crosses the level between the two bands, NaN where it does not
        cross there (a piece `THIN_BAND` moved into its neighbour). None when
        `a` and `b` are not consecutive bands of one ramp."""
        found = self._between(a, b)
        if found is None:
            return None
        ramp, level = found
        t_a = ramp.param(c_a[:, 0], c_a[:, 1])
        t_b = ramp.param(c_b[:, 0], c_b[:, 1])
        den = t_b - t_a
        s = np.where(np.abs(den) > 1e-12, (level - t_a) / np.where(np.abs(den) > 1e-12, den, 1.0), np.nan)
        return np.where((s >= 0.0) & (s <= 1.0), s, np.nan)


def _features(colours: np.ndarray) -> np.ndarray:
    """rgba 0..255 (N, 4) to the partition's features [L*, a*, b*, 100·alpha],
    through the same float32 path `prepare` takes."""
    rgb = colours[:, :3].astype(np.float32) / np.float32(255.0)
    lab = rgb2lab(rgb[None])[0].astype(np.float64)
    return np.concatenate([lab, colours[:, 3:4] / 255.0 * ALPHA_FEATURE_SCALE], axis=1)


def _invert(length: np.ndarray, ts: np.ndarray, v: float) -> float:
    """t at which the cumulative colour distance `length` (non-decreasing, over
    `ts`) reaches v, for 0 < v < length[-1]."""
    i = int(np.searchsorted(length, v, side="left"))
    i = min(max(i, 1), len(length) - 1)
    l0, l1 = float(length[i - 1]), float(length[i])
    f = (v - l0) / (l1 - l0) if l1 > l0 else 0.0
    return float(ts[i - 1] + f * (ts[i] - ts[i - 1]))


def band_levels(ramp: Fill, t_lo: float, t_hi: float, step: float) -> np.ndarray:
    """The n−1 levels of t between n bands of equal colour distance, none over
    `step` and none narrower than BAND_MIN_PX, for a ramp seen over t in
    [t_lo, t_hi]. None (an empty array): one flat colour."""
    lo, hi = min(max(t_lo, 0.0), 1.0), min(max(t_hi, 0.0), 1.0)
    reach = math.hypot(ramp.x2 - ramp.x1, ramp.y2 - ramp.y1) if isinstance(ramp, Linear) else ramp.r
    span_px = (hi - lo) * reach
    none = np.zeros(0)
    if hi - lo <= 1e-9:
        return none
    ts = lo + (hi - lo) * np.arange(RAMP_SAMPLES + 1) / RAMP_SAMPLES
    feat = _features(_interp_stops(ts, ramp.stops))
    length = np.concatenate([[0.0], np.cumsum(np.sqrt(((feat[1:] - feat[:-1]) ** 2).sum(axis=1)))])
    total = float(length[-1])
    n = min(int(math.ceil(total / max(step, 1e-9) - 1e-9)), int(math.floor(span_px / BAND_MIN_PX)))
    if n < 2:
        return none
    return np.array([_invert(length, ts, j * total / n) for j in range(1, n)])


def _absorb(pieces: np.ndarray, first: int, count: int, min_region: int) -> dict[int, int]:
    """Which band pieces (labels first..first+count−1 in `pieces`, a crop) join
    which neighbour: every piece smaller than `min_region` or thinner than
    `THIN_BAND`, smallest first, into the sibling it shares the most lattice
    edges with (the lower label on a tie). Returns {piece: absorber}."""
    ids = list(range(first, first + count))
    size = {k: 0 for k in ids}
    perim = {k: 0 for k in ids}
    shared: dict[tuple[int, int], int] = {}
    vals, counts = np.unique(pieces[pieces > 0], return_counts=True)
    for v, c in zip(vals.tolist(), counts.tolist()):
        size[v] = c
    padded = np.pad(pieces, 1, constant_values=0)
    for a, b in ((padded[:, :-1], padded[:, 1:]), (padded[:-1, :], padded[1:, :])):
        diff = a != b
        pa, pb = a[diff], b[diff]
        for v in (pa, pb):
            vv, cc = np.unique(v[v > 0], return_counts=True)
            for k, c in zip(vv.tolist(), cc.tolist()):
                perim[k] += c
        both = (pa > 0) & (pb > 0)
        lo, hi = np.minimum(pa[both], pb[both]), np.maximum(pa[both], pb[both])
        if lo.size:
            key, cnt = np.unique(np.stack([lo, hi], axis=1), axis=0, return_counts=True)
            for (x, y), c in zip(key.tolist(), cnt.tolist()):
                shared[(x, y)] = shared.get((x, y), 0) + c
    alive = set(ids)
    into: dict[int, int] = {}

    def weak(k: int) -> bool:
        return size[k] < min_region or 2.0 * size[k] / max(perim[k], 1) < THIN_BAND

    while True:
        cands = sorted((size[k], k) for k in alive if weak(k))
        moved = False
        for _, k in cands:
            nbrs = [(c, (y if x == k else x)) for (x, y), c in shared.items() if k in (x, y) and c > 0]
            if not nbrs:
                continue
            best = max(c for c, _ in nbrs)
            d = min(o for c, o in nbrs if c == best)
            # k joins d: sizes add, the edge between them is no longer outline
            size[d] += size[k]
            perim[d] += perim[k] - 2 * shared[(min(k, d), max(k, d))]
            for (x, y), c in list(shared.items()):
                if k not in (x, y):
                    continue
                del shared[(x, y)]
                o = y if x == k else x
                if o != d:
                    key = (min(o, d), max(o, d))
                    shared[key] = shared.get(key, 0) + c
            alive.discard(k)
            into[k] = d
            moved = True
            break
        if not moved:
            break
    # resolve chains (a piece absorbed into one absorbed later)
    out: dict[int, int] = {}
    for k in into:
        d = into[k]
        while d in into:
            d = into[d]
        out[k] = d
    return out


def posterize_fills(
    labels: np.ndarray,
    fills: dict[int, Fill],
    visible: dict[int, bool],
    xs: np.ndarray,
    ys: np.ndarray,
    rgba255: np.ndarray,
    step: float,
    min_region: int,
) -> tuple[np.ndarray, dict[int, Fill], dict[int, bool], Levels]:
    """Every gradient fill cut into flat bands along its own level lines.

    Returns the new labels (compact, 1..K, in the order the regions had, each
    ramp's bands in order along it), a Solid fill and the visibility for every
    label, and the `Levels` the bands were cut at. A gradient too short to cut
    becomes its core's flat colour.
    """
    solid = FitParams(gradients=False)
    out = np.zeros_like(labels)
    new_fills: dict[int, Fill] = {}
    new_visible: dict[int, bool] = {}
    band: dict[int, tuple[int, int]] = {}
    fields: dict[int, Fill] = {}
    level_of: dict[int, np.ndarray] = {}
    next_id = 1
    boxes = ndimage.find_objects(np.maximum(labels, 0))
    for index, box in enumerate(boxes):
        lab = index + 1
        if box is None:
            continue
        m = labels[box] == lab
        fill = fills[lab]
        levels = np.zeros(0)
        if isinstance(fill, (Linear, Radial)):
            t = fill.param(xs[box][m], ys[box][m])
            levels = band_levels(fill, float(t.min()), float(t.max()), step)
        if levels.size == 0:
            if not isinstance(fill, Solid):
                full = labels == lab
                w, core = interior(full)
                fill = fit_fill(xs[full], ys[full], rgba255[full], solid, weights=w, core=core)
            out[box][m] = next_id
            new_fills[next_id] = fill
            new_visible[next_id] = visible[lab]
            next_id += 1
            continue
        k_of = np.full(m.shape, -1, np.int64)
        k_of[m] = np.searchsorted(levels, t, side="right")
        pieces = np.zeros(m.shape, np.int64)
        piece_band: dict[int, int] = {}
        count = 0
        for k in range(levels.size + 1):
            cc, n = cc_label(k_of == k, connectivity=1, return_num=True)
            if n:
                pieces[cc > 0] = cc[cc > 0] + count
                for j in range(1, n + 1):
                    piece_band[count + j] = k
                count += n
        joined = _absorb(pieces, 1, count, min_region)
        for k, d in joined.items():
            pieces[pieces == k] = d
        kept = [k for k in range(1, count + 1) if k not in joined]
        region = out[box]
        model = fill.evaluate(xs[box][m], ys[box][m])
        for k in kept:
            piece = pieces == k
            region[piece] = next_id
            b = piece_band[k]
            # the ramp's mean over the band: a band that is mostly the plateau
            # beyond a steep step is the plateau's colour, not the step's
            new_fills[next_id] = Solid(rgba=model[piece[m]].mean(axis=0))
            new_visible[next_id] = visible[lab]
            band[next_id] = (lab, b)
            next_id += 1
        fields[lab] = fill
        level_of[lab] = levels
    # ids were handed out consecutively, so the map is compact already
    return out.astype(np.int32), new_fills, new_visible, Levels(band=band, fields=fields, levels=level_of)
