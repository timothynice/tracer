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
from studi0trace.engines.vexel.strokes import is_thin
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

# Does the model's family of level lines match the image's? Pixels the model
# puts at one t (one 1 px step of the ramp) should be one colour: where, within
# a band of at least FOLLOW_MIN core pixels, they spread by more than half a
# band step (RMS ΔE about their own mean), the lines are not the image's — a
# centred radial fitted to a rounded card's shadow, whose true level lines are
# the card's outline grown outwards — and cutting along them draws circles the
# image does not have.
FOLLOW_MIN = 64

# Such a ramp is cut where the pixels themselves cross its levels instead: each
# pixel's own position along the ramp (the nearest point of the ramp's colour
# curve to its colour, premultiplied), after a Gaussian of this sigma within the
# region, so the band edges are smooth level lines of the image and not of its
# noise.
SMOOTH_SIGMA = 1.5

# ...but only where those crossings are well defined. In a textured ramp (an
# AI-drawn sheen) the pixels' level lines wander with the texture, and so does
# a level in a ramp's faint tail, where the colour hardly changes. The cut at
# SMOOTH_SIGMA is compared with one at SETTLED_SIGMA, level by level: a level
# whose edge moves by more than SETTLED_PX on average (pixels crossing it per
# pixel of its edge) is not drawn, and when fewer than half of them hold, the
# pixels' lines are the texture's and the model's clean ones are kept.
SETTLED_SIGMA = 3.0
SETTLED_PX = 0.25


@dataclass(frozen=True)
class Levels:
    """Where each band came from: its ramp, and the levels that bound it."""

    band: dict[int, tuple[int, int]] = field(default_factory=dict)  # label -> (group, band index)
    fields: dict[int, Fill] = field(default_factory=dict)  # group -> its fitted Linear | Radial
    levels: dict[int, np.ndarray] = field(default_factory=dict)  # group -> t between band k and k+1, ascending
    # group -> (row, col, t) for a ramp cut where its pixels cross the levels:
    # each pixel's own t over the region's bounding box from (row, col), NaN outside
    observed: dict[int, tuple[int, int, np.ndarray]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.band)

    def model(self, lab: int) -> Fill | None:
        """The gradient a band was cut along, or None for a label that is no band
        or a band of a ramp whose level lines were not the image's."""
        found = self.band.get(int(lab))
        return None if found is None or found[0] in self.observed else self.fields[found[0]]

    def _between(self, a: int, b: int) -> tuple[Fill, float] | None:
        """(ramp, level) when `a` and `b` are consecutive bands of one ramp."""
        fa, fb = self.band.get(int(a)), self.band.get(int(b))
        if fa is None or fb is None or fa[0] != fb[0] or abs(fa[1] - fb[1]) != 1:
            return None
        return self.fields[fa[0]], float(self.levels[fa[0]][min(fa[1], fb[1])])

    def unbanded(self, labels: np.ndarray) -> np.ndarray:
        """`labels` with every ramp's bands one region again (each labelled as
        its lowest band): the shapes the partition drew, before the cut."""
        if not self.band:
            return labels
        first: dict[int, int] = {}
        for lab in sorted(self.band):
            first.setdefault(self.band[lab][0], lab)
        lut = np.arange(int(labels.max()) + 1, dtype=labels.dtype)
        for lab, (group, _) in self.band.items():
            lut[lab] = first[group]
        return lut[labels]

    def sibling(self, a: int, b: int) -> bool:
        """Is the edge between `a` and `b` one of a ramp's own level lines (a
        line or a circle, which a node can be moved onto)?"""
        fa = self.band.get(int(a))
        return self._between(a, b) is not None and fa[0] not in self.observed

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
        seen = self.observed.get(self.band[int(a)][0])
        if seen is None:
            t_a = ramp.param(c_a[:, 0], c_a[:, 1])
            t_b = ramp.param(c_b[:, 0], c_b[:, 1])
        else:  # the pixels' own t at the two pixel centres
            t_a, t_b = _sample(seen, c_a), _sample(seen, c_b)
        den = t_b - t_a
        s = np.where(np.abs(den) > 1e-12, (level - t_a) / np.where(np.abs(den) > 1e-12, den, 1.0), np.nan)
        return np.where((s >= 0.0) & (s <= 1.0), s, np.nan)


def _sample(seen: tuple[int, int, np.ndarray], c: np.ndarray) -> np.ndarray:
    """An observed t grid at pixel centres `c` (N, 2): NaN off the region (a
    pixel the wedge extension handed to a band from outside it)."""
    r0, c0, grid = seen
    r = np.floor(c[:, 1]).astype(np.int64) - r0
    q = np.floor(c[:, 0]).astype(np.int64) - c0
    ok = (r >= 0) & (r < grid.shape[0]) & (q >= 0) & (q < grid.shape[1])
    out = np.full(len(c), np.nan)
    out[ok] = grid[r[ok], q[ok]]
    return out


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
    # Equal colour distances crowd where the ramp is steep, so the count comes
    # down until no band is narrower than BAND_MIN_PX. A radial's innermost band
    # is a disc when the region holds the centre, and a disc is as wide as its
    # diameter: a pupil in a radial eye is a band.
    disc = isinstance(ramp, Radial) and lo * reach <= 1.0
    for n in range(min(int(math.ceil(total / max(step, 1e-9) - 1e-9)), int(math.floor(span_px / BAND_MIN_PX))), 1, -1):
        levels = np.array([_invert(length, ts, j * total / n) for j in range(1, n)])
        widths = np.diff(np.concatenate([[lo], levels, [hi]])) * reach
        if disc:
            widths[0] = 2.0 * float(levels[0]) * reach
        if float(widths.min()) >= BAND_MIN_PX - 1e-9:
            return levels
    return none


def follows(ramp: Fill, t: np.ndarray, features: np.ndarray, core: np.ndarray, levels: np.ndarray,
            step: float) -> bool:
    """Are the ramp's level lines the image's? `t` and `features` are the
    region's pixels' (row-major), `core` which of them are past the rim. See
    FOLLOW_MIN: false when, in some band, the pixels at one t spread by more
    than half of `step`."""
    reach = math.hypot(ramp.x2 - ramp.x1, ramp.y2 - ramp.y1) if isinstance(ramp, Linear) else ramp.r
    tc, fc = t[core], features[core].astype(np.float64)
    if tc.size == 0:
        return True
    at = np.floor((tc - tc.min()) * reach).astype(np.int64)
    count = np.bincount(at).astype(np.float64)
    mean = np.stack([np.bincount(at, weights=fc[:, k], minlength=count.size) for k in range(4)], axis=1)
    mean /= np.maximum(count, 1.0)[:, None]
    d = fc - mean[at]
    dev = ((d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1]) + d[:, 2] * d[:, 2]) + d[:, 3] * d[:, 3]
    k_of = np.searchsorted(levels, tc, side="right")
    n = np.bincount(k_of, minlength=levels.size + 1)
    spread = np.bincount(k_of, weights=dev, minlength=levels.size + 1)
    bar = (0.5 * step) ** 2
    return not any(n[k] >= FOLLOW_MIN and spread[k] / n[k] > bar for k in range(levels.size + 1))


def settled(t_fine: np.ndarray, t_coarse: np.ndarray, levels: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Which levels of a cut at the pixels' own t (`t_fine`, row-major over the
    region `m`) stay put when the smoothing doubles (`t_coarse`)? See
    SETTLED_PX. A level with no edge in the cut (the band beside it is empty)
    does not hold."""
    k = np.full(m.shape, -1, np.int64)
    k[m] = np.searchsorted(levels, t_fine, side="right")
    coarse = np.full(m.shape, -1, np.int64)
    coarse[m] = np.searchsorted(levels, t_coarse, side="right")
    lo, hi = np.minimum(k, coarse), np.maximum(k, coarse)
    pairs = [(k[:, 1:], k[:, :-1]), (k[1:], k[:-1])]
    held = np.zeros(levels.size, dtype=bool)
    for j in range(levels.size):
        moved = int(((lo <= j) & (j < hi) & m).sum())
        edge = sum(int((((p == j) & (q == j + 1)) | ((p == j + 1) & (q == j))).sum()) for p, q in pairs)
        held[j] = edge > 0 and moved <= SETTLED_PX * edge
    return held


def _premultiplied(c: np.ndarray) -> np.ndarray:
    a = c[..., 3:4] / 255.0
    return np.concatenate([c[..., :3] * a, c[..., 3:4]], axis=-1)


def observed_t(ramp: Fill, lo: float, hi: float, rgba: np.ndarray, m: np.ndarray,
               sigma: float = SMOOTH_SIGMA) -> np.ndarray:
    """Each of the region's pixels' own position t along the ramp (row-major
    over `m`, the region in the crop `rgba`): the nearest point of the ramp's
    colour curve over [lo, hi] to the pixel's colour after a Gaussian of
    SMOOTH_SIGMA within the region, both premultiplied, so a transparent pixel's
    inpainted colour counts for nothing."""
    inside = m.astype(np.float64)
    den = ndimage.gaussian_filter(inside, sigma, mode="constant")
    pm = _premultiplied(rgba.astype(np.float64)) * inside[..., None]
    num = np.stack([ndimage.gaussian_filter(pm[..., k], sigma, mode="constant") for k in range(4)], axis=-1)
    c = num[m] / den[m][:, None]
    ts = lo + (hi - lo) * np.arange(RAMP_SAMPLES + 1) / RAMP_SAMPLES
    curve = _premultiplied(_interp_stops(ts, ramp.stops))

    def d2(p: np.ndarray, q: np.ndarray) -> np.ndarray:
        d = p - q
        return ((d[..., 0] * d[..., 0] + d[..., 1] * d[..., 1]) + d[..., 2] * d[..., 2]) + d[..., 3] * d[..., 3]

    near = np.empty(len(c), dtype=np.int64)
    for i in range(0, len(c), 2048):
        near[i:i + 2048] = np.argmin(d2(c[i:i + 2048, None, :], curve[None]), axis=1)
    # the nearest point of the two segments either side of the nearest sample
    best_t = ts[near].copy()
    best_d = d2(c, curve[near])
    for j0 in (near - 1, near):
        ok = (j0 >= 0) & (j0 < RAMP_SAMPLES)
        j = np.clip(j0, 0, RAMP_SAMPLES - 1)
        p0, p1 = curve[j], curve[j + 1]
        e, f = p1 - p0, c - p0
        ee = ((e[:, 0] * e[:, 0] + e[:, 1] * e[:, 1]) + e[:, 2] * e[:, 2]) + e[:, 3] * e[:, 3]
        ef = ((e[:, 0] * f[:, 0] + e[:, 1] * f[:, 1]) + e[:, 2] * f[:, 2]) + e[:, 3] * f[:, 3]
        u = np.clip(np.where(ee > 0.0, ef / np.where(ee > 0.0, ee, 1.0), 0.0), 0.0, 1.0)
        dist = d2(c, p0 + u[:, None] * e)
        better = ok & (dist < best_d)
        best_d = np.where(better, dist, best_d)
        best_t = np.where(better, ts[j] + u * (ts[j + 1] - ts[j]), best_t)
    return best_t


def _band_colour(c: np.ndarray, own: bool) -> np.ndarray:
    """A band's paint from the colours over it: their mean, alpha-weighted for
    the pixels' own colours (`own`), whose colour under a transparent pixel is
    inpainted and means nothing."""
    c = c.astype(np.float64)  # the engine's colours are float32, and a band can be a whole canvas
    if not own:
        return c.mean(axis=0)
    a = c[:, 3]
    total = float(a.sum())
    rgb = (c[:, :3] * a[:, None]).sum(axis=0) / total if total > 0.0 else c[:, :3].mean(axis=0)
    return np.concatenate([rgb, [float(a.mean())]])


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
    features: np.ndarray,
    step: float,
    min_region: int,
) -> tuple[np.ndarray, dict[int, Fill], dict[int, bool], Levels]:
    """Every gradient fill cut into flat bands along its own level lines.

    Returns the new labels (compact, 1..K, in the order the regions had, each
    ramp's bands in order along it), a Solid fill and the visibility for every
    label, and the `Levels` the bands were cut at. A gradient too short to cut
    becomes its core's flat colour. A ramp whose level lines are not the
    image's (`follows`) is cut where its pixels cross the levels (`observed_t`),
    each band the mean of its own pixels.
    """
    solid = FitParams(gradients=False)
    out = np.zeros_like(labels)
    new_fills: dict[int, Fill] = {}
    new_visible: dict[int, bool] = {}
    band: dict[int, tuple[int, int]] = {}
    fields: dict[int, Fill] = {}
    level_of: dict[int, np.ndarray] = {}
    seen_of: dict[int, tuple[int, int, np.ndarray]] = {}
    next_id = 1
    boxes = ndimage.find_objects(np.maximum(labels, 0))
    for index, box in enumerate(boxes):
        lab = index + 1
        if box is None:
            continue
        m = labels[box] == lab
        fill = fills[lab]
        levels = np.zeros(0)
        # A thin region's ramp is its anti-aliasing along a line, and the line
        # is one stroke of one colour: it is never cut.
        if isinstance(fill, (Linear, Radial)) and not is_thin(labels == lab):
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
        _, core = interior(labels == lab)
        lo, hi = min(max(float(t.min()), 0.0), 1.0), min(max(float(t.max()), 0.0), 1.0)
        free = False
        if not follows(fill, t, features[box][m], core, levels, step):
            fine = observed_t(fill, lo, hi, rgba255[box], m)
            held = settled(fine, observed_t(fill, lo, hi, rgba255[box], m, sigma=SETTLED_SIGMA), levels, m)
            free = 2 * int(held.sum()) >= levels.size and bool(held.any())
        if free:
            levels = levels[held]
            t = fine
            grid = np.full(m.shape, np.nan)
            grid[m] = t
            seen_of[lab] = (box[0].start, box[1].start, grid)
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
        # the ramp's mean over the band: a band that is mostly the plateau
        # beyond a steep step is the plateau's colour, not the step's. A ramp
        # whose lines were not the image's is not its colours either, and a
        # band there is the mean of its own pixels (premultiplied).
        colour = rgba255[box][m] if free else fill.evaluate(xs[box][m], ys[box][m])
        for k in kept:
            piece = pieces == k
            region[piece] = next_id
            b = piece_band[k]
            new_fills[next_id] = Solid(rgba=_band_colour(colour[piece[m]], free))
            new_visible[next_id] = visible[lab]
            band[next_id] = (lab, b)
            next_id += 1
        fields[lab] = fill
        level_of[lab] = levels
    # ids were handed out consecutively, so the map is compact already
    return out.astype(np.int32), new_fills, new_visible, Levels(band=band, fields=fields, levels=level_of,
                                                                observed=seen_of)
