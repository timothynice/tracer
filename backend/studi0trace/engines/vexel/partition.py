"""Stage 2: discontinuity map and the initial, edge-bounded partition.

A discontinuity is a *ridge* of the colour gradient, not merely a large
gradient: a steep but smooth ramp (a strong linear gradient) has a large,
flat gradient magnitude and must stay one region, while a hard edge has a
peak. Non-maximum suppression along the local gradient orientation separates
the two, exactly as in Canny.

Seeds for the watershed are the pixels at least one pixel away from any ridge
(dilating the ridge closes the one-pixel gaps NMS leaves at junctions, which
would otherwise let neighbouring regions leak into one seed) **plus** valley
pixels — local gradient minima between two ridges — so that a two-pixel
stroke keeps its own seed instead of being flooded by its surroundings.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage
from skimage.filters import scharr, scharr_h, scharr_v
from skimage.measure import label as cc_label
from skimage.segmentation import relabel_sequential, watershed

_CROSS = ndimage.generate_binary_structure(2, 1)


def discontinuity(features: np.ndarray, sigma: float = 0.7) -> np.ndarray:
    """Per-pixel colour change (ΔE-like units per pixel) across all feature channels."""
    g2 = np.zeros(features.shape[:2], dtype=np.float32)
    for c in range(features.shape[2]):
        g = scharr(features[..., c])
        g2 += g * g
    grad = np.sqrt(g2)
    return ndimage.gaussian_filter(grad, sigma) if sigma > 0 else grad


def ridges_and_valleys(features: np.ndarray, grad: np.ndarray, g_low: float) -> tuple[np.ndarray, np.ndarray]:
    """(ridge, valley) boolean masks from non-maximum / non-minimum tests along
    the structure-tensor gradient orientation."""
    jxx = np.zeros(grad.shape, np.float32)
    jyy = np.zeros(grad.shape, np.float32)
    jxy = np.zeros(grad.shape, np.float32)
    for c in range(features.shape[2]):
        gy = scharr_h(features[..., c])  # derivative along rows (y)
        gx = scharr_v(features[..., c])  # derivative along columns (x)
        jxx += gx * gx
        jyy += gy * gy
        jxy += gx * gy
    for j in (jxx, jyy, jxy):
        j[...] = ndimage.gaussian_filter(j, 1.0)
    theta = 0.5 * np.arctan2(2 * jxy, jxx - jyy)  # dominant gradient direction
    dx, dy = np.cos(theta), np.sin(theta)
    rows, cols = np.mgrid[0 : grad.shape[0], 0 : grad.shape[1]].astype(np.float32)
    g_plus = ndimage.map_coordinates(grad, [rows + dy, cols + dx], order=1, mode="nearest")
    g_minus = ndimage.map_coordinates(grad, [rows - dy, cols - dx], order=1, mode="nearest")
    # Prominence is judged 1.5 px out: a real edge (smoothed peak) has fallen to
    # ≈ 40 % there, while the periodic bumps that 8-bit quantisation puts on a
    # steep ramp are only a few percent high. Sampling 1.5 px also keeps a ridge
    # whose true peak falls between two pixels from vanishing.
    g_plus_far = ndimage.map_coordinates(grad, [rows + 1.5 * dy, cols + 1.5 * dx], order=1, mode="nearest")
    g_minus_far = ndimage.map_coordinates(grad, [rows - 1.5 * dy, cols - 1.5 * dx], order=1, mode="nearest")
    # Prominent against the near samples (a thin stroke's ridge: its far sample is
    # the stroke's other ridge) or against the far samples (a wide edge whose peak
    # straddles two pixels). Quantisation bumps fail both.
    prominent = (grad > 1.10 * np.maximum(g_plus, g_minus)) | (grad > 1.10 * np.maximum(g_plus_far, g_minus_far))
    ridge = (grad > g_low) & (grad >= g_plus) & (grad >= g_minus) & prominent
    valley = (grad <= g_plus) & (grad <= g_minus) & ~ridge
    return ridge, valley


def edge_mask(features: np.ndarray, grad: np.ndarray, g_low: float) -> np.ndarray:
    """Ridge pixels dilated by one (closes NMS gaps). Kept for callers that want the edge band."""
    ridge, _ = ridges_and_valleys(features, grad, g_low)
    return ndimage.binary_dilation(ridge, _CROSS)


def seed_mask(features: np.ndarray, grad: np.ndarray, g_low: float, g_seed: float = 8.0) -> np.ndarray:
    """Seeds: pixels clear of the ridge band, or valley pixels, that also have a
    low gradient. NMS loses ridge pixels at T-junctions (where two edges meet the
    orientation estimate flips); those gap pixels still carry a high gradient, so
    the gradient test keeps neighbouring regions from leaking into one seed."""
    ridge, valley = ridges_and_valleys(features, grad, g_low)
    band = ndimage.binary_dilation(ridge, _CROSS)
    # Junction gaps sit right next to ridge segments, so the gradient test is only
    # applied within two pixels of the band; a steep but smooth ramp far from any
    # ridge keeps its seeds. Valleys (minima across the edge direction) can never
    # be edge pixels and always seed - that is what keeps thin stroke cores alive.
    near_band = ndimage.binary_dilation(band, _CROSS, iterations=2)
    leaky = near_band & (grad >= g_seed)
    return (~band & ~leaky) | valley


def _absorb_small(labels: np.ndarray, grad: np.ndarray, min_region: int, rounds: int = 3) -> np.ndarray:
    """Flood regions below `min_region` pixels from their neighbours along low gradient."""
    for _ in range(rounds):
        sizes = np.bincount(labels.ravel())
        small = sizes < min_region
        small[0] = False
        if not small.any():
            break
        markers = labels.copy()
        markers[small[labels]] = 0
        if markers.max() == 0:  # everything was small: keep the largest
            return np.ones_like(labels)
        labels = watershed(grad, markers)
    return labels


# How wide a neck in a seed may be and still be a leak rather than one area:
# the seed mask is eroded this many times (cross), which parts necks up to
# twice as wide, to find the pieces a neck joins. The weaker the edge, the
# further from the strong one its ridge gives out: the collar of a JPEG key
# reached its ring through a neck four pixels wide, and a disc 6 ΔE darker
# than a panel whose edge cuts it, through one of six (the ridge stops eight
# pixels short of the edge, inside the JPEG's ringing along it).
NECK_ERODE = 3
# How far the discontinuity along the boundary between two split pieces must
# stand above the discontinuity inside them for the split to be a step and not
# a ramp (`rejoin_ramps`). On the corpus the steps the split exists for stand
# 15 times and more above their plateaus; the rings of a JPEG glow, the halo
# of a soft logomark and the bands of a JPEG gradient stand at most 2.2 times
# above their own slope.
NECK_PROMINENCE = 3.0
# ... and how much of the colour difference between them it must carry. A step
# of D shows as a ridge of about 0.6·D on a clean edge (`MergeParams.edge_veto`)
# and 0.3–0.4·D where JPEG smears it (0.41 on the moon's craters, 0.30 at the
# least on the leaf's veins). A boundary carrying under a quarter of the
# difference sits on a ramp: the two sides differ by what lies between them.
NECK_STEP = 0.25
# Fewest pixels a piece needs, after the erosion, to be seeded apart from the
# rest of its component: a region of its own, not the JPEG ringing a strong
# edge leaves a few pixels inside it (pieces of 8 to 40 pixels along the rim of
# a JPEG radial disc and in the wordmark's ribbon were split off as specks).
NECK_PIECE = 64


def seed_markers(smooth: np.ndarray, floor: int, features: np.ndarray | None = None,
                 detail: float = 6.0, grad: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """The watershed's markers: the seed mask's connected components of at least
    `floor` pixels, each split where a neck joins areas of different colour.

    A component is one basin, so a strip of seed across a gap in a ridge makes
    the two sides one region for good: nothing downstream splits a region. The
    gaps are where a weak edge meets a strong one. NMS runs along the strong
    edge's orientation there and drops the weak ridge for a few pixels, and the
    junction test in `seed_mask` (`g_seed`) sits above a weak edge's own
    gradient. JPEG makes it common: its chroma is subsampled and smeared, so a
    colour step a few pixels from a strong edge is a shoulder on that edge's
    ridge, not a ridge of its own. A moon's craters, a key's collar and a
    leaf's veins each shared one seed with the fill around them and were drawn
    as one gradient, and a tile's bevel did the same on a clean render.

    So each component is eroded `NECK_ERODE` times and broken into the pieces
    the erosion leaves (`floor` pixels or more). The pieces are grouped by
    colour, largest first: a piece joins the first group whose first piece's
    mean feature colour is nearer than `detail` — the merge's own threshold
    for two flat areas — and starts a group otherwise, if it has `NECK_PIECE`
    pixels (a smaller one is left to the flood). A component that comes
    out as one group is seeded whole, exactly as before: its pieces are one
    smooth area pinched by noise (JPEG's blocks pinch the seed along every
    diagonal edge into a row of cells). One that comes out as several is
    seeded by its groups, each grown back over the component's own seed
    (a watershed on `grad` inside it), so only the neck is shared out and a
    thin strip of the seed stays with the area it belongs to: a strip of the
    moon's rim between a crater and the limb, left to the flood, went to the
    crater and cut the limb into arcs. `rejoin_ramps` then joins the groups
    again wherever no step divides them.
    Colour sums run in raster order in float64, so both engines group alike.

    Returns (markers, origin): int32 markers numbered in raster order of their
    first pixel, and per marker id the seed component it was split from (0 for
    a component seeded whole).
    """
    comps = cc_label(smooth, connectivity=1)
    if comps.max() == 0:
        return comps.astype(np.int32), np.zeros(1, np.int64)
    sizes = np.bincount(comps.ravel())
    keep = sizes >= floor
    keep[0] = False
    comps[~keep[comps]] = 0
    if features is None:
        markers = cc_label(comps > 0, connectivity=1).astype(np.int32)
        return markers, np.zeros(int(markers.max()) + 1, np.int64)
    core = ndimage.binary_erosion(comps > 0, _CROSS, iterations=NECK_ERODE, border_value=1)
    pieces = cc_label(core, connectivity=1)
    psize = np.bincount(pieces.ravel())
    n_p = psize.size
    big = psize >= floor
    big[0] = False
    owner = np.zeros(n_p, np.int64)  # the component each piece lies in
    owner[pieces.ravel()] = comps.ravel()
    count = np.bincount(owner[big], minlength=sizes.size)
    group = np.zeros(n_p, np.int64)  # piece -> marker id (0: not a marker)
    out = comps.astype(np.int64)
    split = np.zeros(sizes.size, bool)
    cands = np.nonzero(count >= 2)[0]
    if cands.size:
        flat = pieces.ravel()
        sums = np.stack([np.bincount(flat, weights=features[..., c].ravel().astype(np.float64), minlength=n_p)
                         for c in range(features.shape[-1])], axis=1)
        mean = sums / np.maximum(psize, 1)[:, None]
        next_id = int(sizes.size)
        by_comp: dict[int, list[int]] = {}
        for p in np.nonzero(big)[0]:
            by_comp.setdefault(int(owner[p]), []).append(int(p))
        for c in cands:
            ps = sorted(by_comp[int(c)], key=lambda p: (-int(psize[p]), p))
            anchors: list[int] = []
            ids: list[int] = []
            for p in ps:
                for a, gid in zip(anchors, ids):
                    d = mean[p] - mean[a]
                    if float(np.sum(d * d)) < detail * detail:
                        group[p] = gid
                        break
                else:
                    if not anchors or psize[p] >= NECK_PIECE:
                        anchors.append(p)
                        ids.append(next_id)
                        group[p] = next_id
                        next_id += 1
            if len(anchors) >= 2:
                split[c] = True
            else:
                group[ps] = 0
        if split.any():
            inside = split[comps]
            g = grad if grad is not None else np.zeros(smooth.shape, np.float32)
            grown = watershed(g, np.where(inside, group[pieces], 0), mask=inside)
            out[inside] = grown[inside]
    markers = _raster_order(out)
    origin = np.zeros(int(markers.max()) + 1, np.int64)
    origin[markers.ravel()] = np.where(split[comps.ravel()], comps.ravel(), 0)
    origin[0] = 0
    return markers, origin


def _raster_order(ids: np.ndarray) -> np.ndarray:
    """Renumber the non-zero ids 1..K in raster order of each id's first pixel."""
    flat = ids.ravel()
    uniq, first = np.unique(flat, return_index=True)
    keep = uniq != 0
    uniq, first = uniq[keep], first[keep]
    lut = np.zeros(int(flat.max()) + 1, np.int32)
    lut[uniq[np.argsort(first, kind="stable")]] = np.arange(1, uniq.size + 1, dtype=np.int32)
    return lut[ids].astype(np.int32)


def rejoin_ramps(labels: np.ndarray, markers: np.ndarray, origin: np.ndarray, grad: np.ndarray,
                 features: np.ndarray) -> np.ndarray:
    """Join again the basins of a split seed that no step divides.

    A neck in a seed is as often a smooth area pinched by noise as a leak: the
    rings of a JPEG glow and the halo round a soft logomark are pinched into
    pieces whose colours are far apart only because the ramp between them is
    steep. What tells a step from a ramp is where the colour changes: across a
    step it changes along the boundary between the two, so the discontinuity
    there stands far above that inside either piece; across a ramp it changes
    everywhere, and the boundary is no steeper than the pieces. So two
    adjacent basins split from one component stay apart only when the mean
    discontinuity along their shared boundary (4-neighbour pixel pairs, each
    the mean of its two pixels) is at least `NECK_PROMINENCE` times the median
    discontinuity over the marker of either (its lower middle value, so both
    engines take the same element), and at least `NECK_STEP` times the
    distance between the two markers' mean feature colours. The rest are
    joined, and a join is the union of the two basins: the flood is blind to
    labels, so it is what seeding them as one marker would have given. Sums
    run horizontal pairs then vertical ones, each in raster order, in float64.
    """
    if not origin.any():
        return labels
    k = int(origin.size)
    g = grad.astype(np.float64)
    # the interior discontinuity of each split marker
    interior = np.zeros(k)
    flat_m = markers.ravel().astype(np.int64)
    sel = origin[flat_m] > 0
    ms, gs = flat_m[sel], g.ravel()[sel]
    order = np.lexsort((gs, ms))  # by marker, then by value
    ms, gs = ms[order], gs[order]
    ids, first, n = np.unique(ms, return_index=True, return_counts=True)
    interior[ids] = gs[first + (n - 1) // 2]
    count = np.bincount(flat_m, minlength=k)
    colour = np.stack([np.bincount(flat_m, weights=features[..., c].ravel().astype(np.float64), minlength=k)
                       for c in range(features.shape[-1])], axis=1) / np.maximum(count, 1)[:, None]
    keys, weights = [], []
    for la, lb, ga, gb in ((labels[:, :-1], labels[:, 1:], g[:, :-1], g[:, 1:]),
                           (labels[:-1, :], labels[1:, :], g[:-1, :], g[1:, :])):
        a, b = la.ravel().astype(np.int64), lb.ravel().astype(np.int64)
        m = (a != b) & (origin[a] > 0) & (origin[a] == origin[b])
        a, b = a[m], b[m]
        keys.append(np.minimum(a, b) * k + np.maximum(a, b))
        weights.append(0.5 * (ga.ravel()[m] + gb.ravel()[m]))
    key = np.concatenate(keys)
    if key.size == 0:
        return labels
    uniq, inv = np.unique(key, return_inverse=True)
    cnt = np.bincount(inv)
    gsum = np.bincount(inv, weights=np.concatenate(weights))
    parent = np.arange(k)

    def find(i: int) -> int:
        while parent[i] != i:
            i = int(parent[i])
        return i

    for u, c, s in zip(uniq, cnt, gsum):
        a, b = int(u // k), int(u % k)
        edge = s / c
        d = colour[a] - colour[b]
        if edge >= NECK_PROMINENCE * max(interior[a], interior[b]) and edge >= NECK_STEP * float(np.sqrt(np.sum(d * d))):
            continue
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    root = np.array([find(i) for i in range(k)])
    return root[labels].astype(np.int32)


def initial_labels(grad: np.ndarray, features: np.ndarray | None = None, min_region: int = 6, g_low: float = 1.5,
                   detail: float = 6.0) -> np.ndarray:
    """Watershed on the discontinuity map, seeded by the connected smooth areas.

    With `features` the seeds come from ridge/valley analysis (steep smooth ramps
    and thin strokes keep their seeds) and are split at necks between areas more
    than `detail` apart (`seed_markers`); without them the legacy `grad < g_low`
    threshold is used. Returns int32 labels 1..K covering every pixel.
    """
    smooth = seed_mask(features, grad, g_low) if features is not None else grad < g_low
    markers, origin = seed_markers(smooth, max(min_region, 2), features, detail, grad)
    if markers.max() == 0:
        # No smooth area at all (tiny or extremely noisy image): seed from the
        # local minima of the gradient instead so watershed still has basins.
        minima = ndimage.minimum_filter(grad, size=3) == grad
        markers = cc_label(minima, connectivity=1)
        if markers.max() == 0:
            return np.ones(grad.shape, dtype=np.int32)
        origin = np.zeros(int(markers.max()) + 1, np.int64)
    labels = watershed(grad, markers)
    labels = rejoin_ramps(labels, markers, origin, grad, features)
    labels = _absorb_small(labels, grad, min_region)
    labels, _, _ = relabel_sequential(labels)
    return labels.astype(np.int32)
