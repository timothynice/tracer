"""Stage 6b: symmetry of the placed boundary.

A mark that is mirror-symmetric, or the same under a turn of 360°/k, was drawn
so, and a trace that has one side a tenth of a pixel fatter than the other
reads as hand-made even when every edge is inside tolerance. Before the nodes
are placed and the curves fitted, each region's ring is tested against its
candidate symmetries: reflect (or turn) every vertex and ask how far it lands
from the nearest vertex of the ring. When the mean is inside SYM_MEAN and the
worst inside SYM_MAX — the placement's own uncertainty — the symmetry is
real, and every vertex is replaced by the average of itself and its images,
so the ring becomes exactly symmetric. The vertices live in the shared arcs,
so the neighbour on the other side of each edge moves with it.

Vectorizer.AI lists this under "symmetry modelling". The candidate axes are
the ring's principal directions, their 45° rotations, and every 15° about the
centroid, which covers the regular polygons whose covariance is isotropic.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.spatial import cKDTree

SYM_MEAN = 0.10      # px; mean distance from each image to the nearest vertex
SYM_MAX = 0.30       # px; the 99th-percentile image — one cusp vertex handed back a sliver does not decide
SYM_CAP = 1.0        # px; but nothing may be further off than this
MIN_VERTICES = 24    # a ring shorter than this is too little to judge
ORDERS = (8, 7, 6, 5, 4, 3, 2)
AXIS_SNAP_DEG = 1.5  # a mirror axis this close to vertical or horizontal is exactly so
# Two vertices this close to equally near an image are equally near: the one
# with the lower index is its match. On a ring placed on the pixel lattice an
# image often lands exactly between two vertices, and which one a k-d tree (or
# the Rust grid) found first decided where the averaged vertex went.
NEAR_TIE = 1e-9
# A ring whose covariance is this close to isotropic (relative to its trace)
# has no principal direction: every direction is an eigenvector, and the one
# an eigensolver hands back is decided by the last bits of the sums (a square
# made exactly four-fold symmetric is one). Its candidates are the 15° grid's.
ISOTROPIC = 1e-9


def _centroid(poly: np.ndarray) -> np.ndarray:
    return poly.mean(axis=0)


def _nearest(poly: np.ndarray, images: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(distance, index) of the nearest vertex to every image, a tie (within
    NEAR_TIE) going to the lower index."""
    tree = cKDTree(poly)
    k = min(3, len(poly))
    dist, idx = tree.query(images, k=k)
    if k == 1:
        return dist, idx
    tied = dist <= dist[:, :1] + NEAR_TIE
    best = np.where(tied, idx, len(poly)).min(axis=1)
    # all k returned are tied: there may be more beyond them
    for j in np.nonzero(tied[:, -1])[0]:
        best[j] = min(tree.query_ball_point(images[j], float(dist[j, 0]) + NEAR_TIE))
    return dist[:, 0], best


def _match(poly: np.ndarray, images: np.ndarray) -> tuple[float, float, np.ndarray]:
    """(mean, worst, index of nearest vertex) for every image; `worst` is the
    99th percentile with the absolute maximum folded in through SYM_CAP."""
    dist, idx = _nearest(poly, images)
    p99 = float(np.percentile(dist, 99))
    worst = p99 if float(dist.max()) <= SYM_CAP else float(dist.max())
    return float(dist.mean()), worst, idx


def _fits(mean: float, worst: float) -> bool:
    return mean <= SYM_MEAN and worst <= SYM_MAX


def mirror_axes(poly: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """Candidate mirror axes as (point on axis, unit direction)."""
    c = _centroid(poly)
    q = poly - c
    n = max(len(q), 1)
    a, b, cc = float(q[:, 0] @ q[:, 0]) / n, float(q[:, 0] @ q[:, 1]) / n, float(q[:, 1] @ q[:, 1]) / n
    dirs: list[np.ndarray] = []
    # the principal directions in closed form, the major one first, with the
    # minor one a quarter turn anticlockwise of it (an eigensolver's signs are
    # its own, and they decided which diagonal came first)
    if math.hypot(a - cc, 2.0 * b) > ISOTROPIC * (a + cc):
        theta = 0.5 * math.atan2(2.0 * b, a - cc)
        d1 = np.array([math.cos(theta), math.sin(theta)])
        d2 = np.array([-math.sin(theta), math.cos(theta)])
        dirs = [d1, d2, d1 + d2, d1 - d2]
    dirs += [np.array([math.cos(math.radians(a)), math.sin(math.radians(a))]) for a in range(0, 180, 15)]
    out: list[tuple[np.ndarray, np.ndarray]] = []
    for d in dirs:
        n = float(np.linalg.norm(d))
        if n < 1e-12:
            continue
        d = d / n
        if d[0] < 0 or (d[0] == 0 and d[1] < 0):
            d = -d
        # an axis within AXIS_SNAP_DEG of the canvas axes is one: a symmetric
        # mark is drawn upright, and the exact direction reflects exactly
        ang = math.degrees(math.atan2(d[1], d[0]))
        if min(abs(ang), abs(180.0 - ang)) <= AXIS_SNAP_DEG:
            d = np.array([1.0, 0.0])
        elif abs(ang - 90.0) <= AXIS_SNAP_DEG:
            d = np.array([0.0, 1.0])
        if all(abs(float(d @ e)) < math.cos(math.radians(2.0)) for _c, e in out):
            out.append((c, d))
    return out


def reflect(pts: np.ndarray, c: np.ndarray, d: np.ndarray) -> np.ndarray:
    r = 2.0 * np.outer(d, d) - np.eye(2)
    return c + (pts - c) @ r


def rotate(pts: np.ndarray, c: np.ndarray, angle: float) -> np.ndarray:
    ca, sa = math.cos(angle), math.sin(angle)
    r = np.array([[ca, -sa], [sa, ca]])
    return c + (pts - c) @ r.T


def symmetrize(poly: np.ndarray, axis: tuple[np.ndarray, np.ndarray]) -> np.ndarray | None:
    """The ring made exactly mirror-symmetric about `axis`, or None when it is not."""
    c, d = axis
    images = reflect(poly, c, d)
    mean, worst, idx = _match(poly, images)
    if not _fits(mean, worst):
        return None
    return 0.5 * (poly + reflect(poly[idx], c, d))


def rotational_order(poly: np.ndarray) -> int:
    """The largest k in ORDERS under which the ring maps onto itself, else 1."""
    c = _centroid(poly)
    for k in ORDERS:
        mean, worst, _idx = _match(poly, rotate(poly, c, 2.0 * math.pi / k))
        if _fits(mean, worst):
            return k
    return 1


def symmetrize_rotational(poly: np.ndarray, k: int) -> np.ndarray:
    """Every vertex averaged with its k−1 images turned back into place."""
    c = _centroid(poly)
    acc = poly.copy()
    for j in range(1, k):
        angle = 2.0 * math.pi * j / k
        _mean, _worst, idx = _match(poly, rotate(poly, c, angle))
        acc += rotate(poly[idx], c, -angle)
    return acc / k


def ring_symmetries(poly: np.ndarray) -> tuple[np.ndarray | None, list[tuple[np.ndarray, np.ndarray]]]:
    """Apply every symmetry the ring has: the rotational order first, then each
    mirror axis that fits. Returns (the symmetrised ring or None, the mirror
    axes that fit, best first)."""
    if len(poly) < MIN_VERTICES:
        return None, []
    out = poly
    changed = False
    k = rotational_order(out)
    if k > 1:
        out = symmetrize_rotational(out, k)
        changed = True
    axes: list[tuple[float, tuple[np.ndarray, np.ndarray]]] = []
    for axis in mirror_axes(out):
        sym = symmetrize(out, axis)
        if sym is not None:
            mean, _worst, _idx = _match(out, reflect(out, *axis))
            axes.append((mean, axis))
            out = sym
            changed = True
    # best first by a rule both implementations agree on to the last bit: an
    # exact canvas-axis direction first, then the smaller angle
    axes.sort(key=lambda t: _axis_rank(t[1]))
    return (out if changed else None), [a for _m, a in axes]


def _axis_rank(axis: tuple[np.ndarray, np.ndarray]) -> tuple[int, float]:
    d = axis[1]
    exact = 0 if (d[0] == 1.0 and d[1] == 0.0) or (d[0] == 0.0 and d[1] == 1.0) else 1
    return exact, math.degrees(math.atan2(d[1], d[0])) % 180.0


def symmetrize_ring(poly: np.ndarray) -> np.ndarray | None:
    """The ring with every symmetry it has applied, or None when it has none."""
    return ring_symmetries(poly)[0]
