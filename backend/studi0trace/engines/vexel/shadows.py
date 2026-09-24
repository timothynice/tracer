"""Drop shadows, glows and inner shadows as SVG filters.

A soft shadow is not a colour field to be approximated — it is a blurred,
offset, scaled copy of a shape's own alpha. Approximating it with regions
slices the falloff into bands whose boundaries are iso-contours of the blur,
and those trace as lumpy outlines against a perfectly smooth original.
Recognising it instead recovers the handful of numbers that made it:

    observed = B + o·g(x)·(C − B),      g = G_σ(shift_{dx,dy}(A))

with `A` the caster's silhouette, `B` the backdrop, `C` the shadow colour and
`o` its opacity. An inner shadow is the same with `g = A·(1 − G_σ(shift(A)))`.

The fit is global rather than per-region on purpose. A shadow's faint outer
reach is usually *merged into the backdrop* before this stage — the backdrop
comes out of the merge as a gradient that is partly modelling shadow — so only
its dark core survives as separate regions. Fitting over everything outside the
casters sees the whole falloff, and the backdrop is refitted afterwards against
colours with the shadow taken back out.

Only the product `o·(C − B)` is observable: every split renders identically.
The split chosen sends the colour to where the ray from `B` leaves the sRGB
cube, which recovers the authored values exactly for the usual black shadow.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage, optimize

# Fitting runs on a grid no larger than this on the long edge: σ scales with the
# image, so the objective's shape does not need full resolution and a
# Nelder-Mead run costs a few hundred cheap blurs.
FIT_EDGE = 224
MIN_SIGMA = 0.6
MAX_SIGMA_FRAC = 0.35  # a blur wider than this much of the image is not a shadow
RAY_TOL = 14.0  # how far off the shadow ray a region's colour may sit (0-255)
MIN_PEAK = 5.0  # a darkening smaller than this is not worth a filter
# The filter has to beat the bands *decisively*, not by a hair. The gate scores a
# model computed here, while the judge is a renderer, and the two agree only to
# within a fraction of a colour level; a fit that merely ties is the shape of a
# fit that renders worse.
WIN_MARGIN = 0.75
# On a transparent canvas, a region whose mean alpha is under this may be a
# shadow's band; the opaque rest of the ink are the casters.
CLEAR_BAND_ALPHA = 0.9


@dataclass(frozen=True)
class Shadow:
    caster: int
    dx: float
    dy: float
    sigma: float
    colour: np.ndarray  # rgb 0-255
    opacity: float  # 0..1
    inset: bool
    rms: float  # residual in colour units, against the same target as the bands
    region: tuple[float, float, float, float]  # filter region in user units (x, y, w, h)


@dataclass
class ShadowPlan:
    shadows: dict[int, Shadow] = field(default_factory=dict)  # caster label -> its shadow
    absorbed: set[int] = field(default_factory=set)  # regions the shadows explain
    refit: set[int] = field(default_factory=set)  # backdrops to refit on `corrected`
    corrected: np.ndarray | None = None  # rgba255 with the accepted shadows removed
    canvas: int | None = None  # the transparent canvas the shadows fall on (`_detect_clear`), if they do

    @property
    def empty(self) -> bool:
        return not self.shadows


# --- helpers ---------------------------------------------------------------------


def _hex(rgb: np.ndarray) -> str:
    r, g, b = (int(round(float(v))) for v in np.clip(rgb[:3], 0, 255))
    return f"#{r:02x}{g:02x}{b:02x}"


def _fmt(v: float, precision: int) -> str:
    s = f"{v:.{precision}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _cube_exit(origin: np.ndarray, direction: np.ndarray) -> float:
    """Largest t with origin + t·direction still inside [0, 255]^3."""
    ts = [((255.0 if d > 0 else 0.0) - o) / d for o, d in zip(origin, direction) if abs(d) > 1e-9]
    return float(min(ts)) if ts else 0.0


def _blur_shift(a: np.ndarray, dx: float, dy: float, sigma: float) -> np.ndarray:
    out = ndimage.shift(a, (dy, dx), order=1, mode="constant", cval=0.0) if (dx or dy) else a
    return ndimage.gaussian_filter(out, max(sigma, 1e-3), mode="constant", cval=0.0)


def _solve_scale(model: np.ndarray, target: np.ndarray) -> tuple[float, float]:
    """Least-squares k for target ≈ k·model, and the RMS that leaves."""
    denom = float(np.dot(model, model))
    if denom < 1e-9:
        return 0.0, float(np.sqrt(np.mean(target * target)))
    k = max(0.0, float(np.dot(model, target) / denom))
    return k, float(np.sqrt(np.mean((target - k * model) ** 2)))


def _fit_blur(src: np.ndarray, target: np.ndarray, domain: np.ndarray, seed: tuple[float, float], inset: bool) -> tuple[float, float, float, float, float]:
    """Fit (dx, dy, σ, k) of k·G_σ(shift(src)) to target over domain, in grid units."""
    t = target[domain]
    limit = MAX_SIGMA_FRAC * max(src.shape)

    def model_of(dx: float, dy: float, sigma: float) -> np.ndarray:
        g = _blur_shift(src, dx, dy, sigma)
        if inset:
            g = src * (1.0 - g)
        return g[domain]

    def cost(v: np.ndarray) -> float:
        sigma = float(np.exp(v[2]))
        if not (MIN_SIGMA <= sigma <= limit):
            return 1e9
        return _solve_scale(model_of(float(v[0]), float(v[1]), sigma), t)[1]

    # A coarse octave sweep first: the objective is smooth in (dx, dy) but has a
    # long flat valley in σ, and Nelder-Mead from a bad σ just settles in it.
    best = None
    for sigma0 in (1.0, 2.0, 4.0, 8.0, 16.0, 32.0):
        if sigma0 > limit:
            break
        v0 = np.array([seed[0], seed[1], np.log(sigma0)])
        c0 = cost(v0)
        if best is None or c0 < best[0]:
            best = (c0, v0)
    assert best is not None
    # An explicit simplex: scipy's default step is a fraction of each parameter,
    # which collapses to 0.00025 for an offset seeded at zero — the search then
    # never moves dx or dy at all.
    x0 = best[1]
    span = max(2.0, 0.04 * max(src.shape))
    simplex = np.array([x0, x0 + [span, 0, 0], x0 + [0, span, 0], x0 + [0, 0, 0.4]])
    res = optimize.minimize(cost, x0, method="Nelder-Mead", options={"initial_simplex": simplex, "xatol": 0.05, "fatol": 1e-4, "maxiter": 400})
    dx, dy = float(res.x[0]), float(res.x[1])
    sigma = float(np.clip(np.exp(res.x[2]), MIN_SIGMA, limit))
    k, rms = _solve_scale(model_of(dx, dy, sigma), t)
    return dx, dy, sigma, k, rms


def _backdrop_colour(values: np.ndarray) -> np.ndarray:
    """The unshadowed colour: the mode of a coarsely quantised sample."""
    q = (values // 6).astype(np.int64)
    key = (q[:, 0] << 20) | (q[:, 1] << 10) | q[:, 2]
    uniq, counts = np.unique(key, return_counts=True)
    pick = uniq[int(np.argmax(counts))]
    sel = key == pick
    return values[sel].mean(axis=0)



def _filter_region(sil: np.ndarray, dx: float, dy: float, sigma: float) -> tuple[float, float, float, float]:
    """The user-space box the blur needs.

    The usual `x="-50%" width="200%"` is a percentage of the *bounding box*, so a
    small shape with a wide blur has its shadow clipped — invisible in a model
    computed in numpy, very visible in the render.
    """
    ys, xs_ = np.nonzero(sil)
    if ys.size == 0:
        return (0.0, 0.0, 1.0, 1.0)
    margin = 3.0 * sigma + abs(dx) + abs(dy) + 2.0
    x0, x1 = float(xs_.min()) - margin, float(xs_.max()) + 1.0 + margin
    y0, y1 = float(ys.min()) - margin, float(ys.max()) + 1.0 + margin
    return (x0, y0, x1 - x0, y1 - y0)


def _is_sharp(observed: np.ndarray, sil: np.ndarray) -> bool:
    """Does the caster have a hard edge?

    A drop shadow hides behind an opaque shape, so the shape's own boundary is a
    step. If the boundary is itself a ramp then the artwork is blurred, not
    shadowed — a different effect, and out of scope: replacing it with a sharp
    shape plus a filter renders a hard edge where the original is soft.
    """
    edge = ndimage.binary_dilation(sil, iterations=1) & ~ndimage.binary_erosion(sil, iterations=1)
    if edge.sum() < 8:
        return False
    inner = ndimage.binary_erosion(sil, iterations=3)
    outer = ndimage.binary_dilation(sil, iterations=3) & ~sil
    if inner.sum() < 8 or outer.sum() < 8:
        return False
    step = float(np.linalg.norm(observed[inner].mean(axis=0) - observed[outer].mean(axis=0)))
    if step < 8.0:
        return False
    grad = np.sqrt(sum(ndimage.sobel(observed[..., c], axis=0) ** 2 + ndimage.sobel(observed[..., c], axis=1) ** 2
                       for c in range(observed.shape[-1]))) / 4.0
    return float(np.percentile(grad[edge], 90)) > 0.30 * step


def _gate(observed: np.ndarray, domain: np.ndarray, model_rgb: np.ndarray, labels: np.ndarray, fills: dict, xs: np.ndarray, ys: np.ndarray) -> tuple[float, float]:
    """RMS colour error of the filter model and of the bands it would replace.

    Full resolution and all three channels on purpose: measured on a reduced
    grid along the shadow's own colour axis, a model can look better than the
    bands while rendering visibly worse, which is exactly what the bench then
    reports.
    """
    obs = observed[domain]
    filter_rms = float(np.sqrt(np.mean((obs - model_rgb) ** 2)))
    band = np.empty_like(obs)
    for lab in np.unique(labels[domain]).tolist():
        m = labels[domain] == lab
        if not m.any():
            continue
        if int(lab) not in fills:
            band[m] = obs[m]
            continue
        band[m] = fills[int(lab)].evaluate(xs[domain][m], ys[domain][m])[:, :3]
    band_rms = float(np.sqrt(np.mean((obs - band) ** 2)))
    return filter_rms, band_rms


# --- detection -------------------------------------------------------------------


def detect_shadows(
    labels: np.ndarray,
    fills: dict,
    visible: dict[int, bool],
    silhouette,
    prep,
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    min_region: int,
    inset: bool = True,
) -> ShadowPlan:
    """Find shadows, the regions they explain, and the backdrops to refit.

    `silhouette(label)` gives the caster's painted footprint with enclosed
    regions included — the white bar on a card is part of the card's
    silhouette, so it is part of what the card casts.
    """
    plan = ShadowPlan()
    height, width = labels.shape
    total = float(labels.size)
    ids = [int(i) for i in np.unique(labels) if i != 0]
    if len(ids) < 3:
        return plan

    rgb = prep.rgb
    areas = {lab: int((labels == lab).sum()) for lab in ids}
    vis = [lab for lab in ids if visible.get(lab)]
    if not vis:
        return plan
    means = {lab: rgb[labels == lab].mean(axis=0) for lab in vis}

    border = np.zeros(labels.shape, bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    on_border = set(np.unique(labels[border]).tolist())
    backdrop = max((lab for lab in vis if lab in on_border), key=lambda l: areas[l], default=None)
    # Artwork on a transparent canvas: the canvas is the unpainted region on
    # the border, and when it is the larger ground a shadow on it is ink of its
    # own (`_detect_clear`), not a darkening of a backdrop colour.
    canvas = max((lab for lab in ids if not visible.get(lab) and lab in on_border), key=lambda l: areas[l], default=None)
    if canvas is not None and areas[canvas] >= 0.05 * total and (backdrop is None or areas[canvas] > areas[backdrop]):
        _detect_clear(plan, labels, fills, visible, silhouette, prep, xs, ys, areas, vis, canvas, min_region, total)
        return plan
    if backdrop is None or areas[backdrop] < 0.05 * total:
        return plan

    b0 = _backdrop_colour(rgb[labels == backdrop])

    # Which regions lie on a single ray away from the backdrop's own colour?
    # The best ray is the one the most regions share: a shadow arrives as a ramp
    # of several bands, a piece of artwork as one region of its own hue.
    others = [lab for lab in vis if lab != backdrop and areas[lab] < 0.4 * total]
    rays = []
    for cand in others:
        d = means[cand] - b0
        n = float(np.linalg.norm(d))
        if n < MIN_PEAK:
            continue
        u = d / n
        members = {
            lab for lab in others
            if float(np.dot(means[lab] - b0, u)) > 0
            and float(np.linalg.norm((means[lab] - b0) - float(np.dot(means[lab] - b0, u)) * u)) <= RAY_TOL
        }
        rays.append((len(members), sum(areas[m] for m in members), u, members))
    if not rays:
        return plan
    rays.sort(key=lambda r: (-r[0], r[1]))

    step = max(1, int(round(max(height, width) / FIT_EDGE)))
    sl = (slice(None, None, step), slice(None, None, step))
    small_rgb = rgb[sl]
    small_labels = labels[sl]

    def try_ray(u: np.ndarray, band_group: set[int]) -> bool:
        # Casters are the visible regions this ray does *not* explain: real ink.
        ink = [
            lab for lab in vis
            if lab != backdrop and lab not in band_group and areas[lab] >= max(4 * min_region, 0.004 * total)
        ]
        # An enclosed region rides on its container's silhouette, not its own.
        sils = {lab: silhouette(lab) for lab in ink}
        ink = [lab for lab in ink if not any(o != lab and sils[o][sils[lab]].mean() > 0.9 for o in ink)]
        if not ink:
            return False

        painted = np.zeros(labels.shape, bool)
        for lab in ink:
            painted |= sils[lab]
        # A page of cards has one shadow per card. Each caster owns the canvas
        # nearer to it than to any other, so the fits do not fight each other.
        if len(ink) > 1:
            dist = np.stack([ndimage.distance_transform_edt(~sils[lab]) for lab in ink])
            owner = np.argmin(dist, axis=0)
        else:
            owner = np.zeros(labels.shape, np.intp)

        # Fit each caster on its own cell, then judge them together: shadows
        # spill across cell borders and compose source-over, so scoring one at a
        # time passes fits that render wrong once the others are drawn too.
        fitted: list[tuple[int, float, float, float, float, np.ndarray, set[int]]] = []
        for i, caster in enumerate(ink):
            sil = sils[caster]
            if not _is_sharp(rgb, sil):
                continue
            # A drop shadow lies *behind* its caster, so only regions outside the
            # silhouette can belong to it. Without this, a blurred shape's own
            # interior bands get absorbed and repainted flat — the edge goes hard
            # and the result is worse than the banding it replaced.
            cell = ~painted & (owner == i)
            if cell.sum() < 64:
                continue
            group = {lab for lab in band_group if cell[labels == lab].mean() > 0.9}
            if not group:
                continue  # a filter that replaces nothing is not worth emitting

            cell_small = cell[sl]
            target = np.zeros(small_labels.shape)
            target[cell_small] = (small_rgb[cell_small] - b0) @ u
            if float(target.max()) < MIN_PEAK:
                continue
            src = sil[sl].astype(np.float64)
            if src.sum() < 16:
                continue
            gy, gx = np.nonzero(cell_small & (target > 0.35 * target.max()))
            sy, sx = np.nonzero(src > 0.5)
            seed = (float(gx.mean() - sx.mean()), float(gy.mean() - sy.mean())) if gy.size and sy.size else (0.0, 0.0)

            dx, dy, sigma, k, _ = _fit_blur(src, target, cell_small, seed, False)
            if k <= 0.0:
                continue
            length = _cube_exit(b0, u)
            if length <= 1e-6:
                continue
            opacity = k / length
            colour = b0 + length * u
            if opacity > 1.0:
                opacity, colour = 1.0, b0 + k * u
            if not 0.02 <= opacity <= 1.0:
                continue
            g_full = _blur_shift(sil.astype(np.float64), dx * step, dy * step, sigma * step)
            fitted.append((caster, dx * step, dy * step, sigma * step, float(opacity), colour, group))

        if not fitted:
            return False

        # Compose them the way the renderer will, then gate once.
        domain = ~painted
        model = np.broadcast_to(b0, (int(domain.sum()), 3)).astype(np.float64).copy()
        blurs = {}
        for caster, dx, dy, sigma, opacity, colour, _group in fitted:
            g = _blur_shift(sils[caster].astype(np.float64), dx, dy, sigma)
            blurs[caster] = g
            a = (opacity * g[domain])[:, None]
            model = model * (1.0 - a) + np.clip(colour, 0, 255) * a
        rms, band_rms = _gate(rgb, domain, model, labels, fills, xs, ys)
        if rms >= WIN_MARGIN * band_rms:
            return False

        corrected = np.concatenate([rgb, (prep.alpha * 255.0)[..., None]], axis=-1).copy()
        flat = np.broadcast_to(b0, rgb.shape).astype(np.float64).copy()
        composed = flat.copy()
        for caster, dx, dy, sigma, opacity, colour, group in fitted:
            a = (opacity * blurs[caster])[..., None]
            composed = composed * (1.0 - a) + np.clip(colour, 0, 255) * a
            plan.shadows[caster] = Shadow(caster, dx, dy, sigma, np.clip(colour, 0, 255), opacity, False, rms, _filter_region(sils[caster], dx, dy, sigma))
            plan.absorbed |= group
        plan.refit.add(backdrop)
        corrected[..., :3] -= composed - flat
        accepted = True
        if accepted:
            plan.corrected = np.clip(corrected, 0, 255)
        return accepted

    for count, _area, u, members in rays[:3]:
        if count < 1:
            continue
        if try_ray(u, members):
            break

    if inset and not plan.shadows:
        _detect_inset(plan, labels, fills, visible, silhouette, prep, xs, ys, areas, means, vis, min_region, total, sl, step)
    return plan


def _premultiplied(rgb: np.ndarray, alpha01: np.ndarray) -> np.ndarray:
    """(…, 4): colour times alpha, and alpha, both 0–255 — what a pixel adds
    over nothing, which is the only thing a transparent canvas can show."""
    return np.concatenate([rgb * alpha01[..., None], (alpha01 * 255.0)[..., None]], axis=-1)


def _detect_clear(plan, labels, fills, visible, silhouette, prep, xs, ys, areas, vis, canvas, min_region, total) -> None:
    """A drop shadow or glow on a transparent canvas.

    There is no backdrop colour for the shadow to darken: the shadow is ink of
    its own, colour C at alpha o·g, and the observable is alpha. So the blur is
    fitted to the alpha over the canvas side of each caster, the colour is the
    shadow pixels' own (alpha-weighted), and the filter is judged against the
    bands in premultiplied colour, where the unpainted canvas is zero. The
    bands are the translucent regions (CLEAR_BAND_ALPHA) on that side; the
    casters are the rest of the ink. Without this a transparent canvas never
    had a shadow rebuilt: its shadow came out as one hard-edged band, or — when
    the canvas's own fill took the falloff up — as a few scraps of it.
    """
    alpha = prep.alpha
    rgb = prep.rgb
    height, width = labels.shape
    seen = {lab: float(alpha[labels == lab].mean()) for lab in vis}
    bands = {lab for lab in vis if lab != canvas and seen[lab] < CLEAR_BAND_ALPHA and areas[lab] < 0.4 * total}
    ink = [lab for lab in vis if lab != canvas and lab not in bands and areas[lab] >= max(4 * min_region, 0.004 * total)]
    if not bands or not ink:
        return
    sils = {lab: silhouette(lab) for lab in ink}
    ink = [lab for lab in ink if not any(o != lab and sils[o][sils[lab]].mean() > 0.9 for o in ink)]
    if not ink:
        return
    painted = np.zeros(labels.shape, bool)
    for lab in ink:
        painted |= sils[lab]
    if len(ink) > 1:
        dist = np.stack([ndimage.distance_transform_edt(~sils[lab]) for lab in ink])
        owner = np.argmin(dist, axis=0)
    else:
        owner = np.zeros(labels.shape, np.intp)

    observed = _premultiplied(rgb, alpha)
    step = max(1, int(round(max(height, width) / FIT_EDGE)))
    sl = (slice(None, None, step), slice(None, None, step))
    small_alpha = alpha[sl] * 255.0
    fitted: list[tuple[int, float, float, float, float, np.ndarray, set[int]]] = []
    for i, caster in enumerate(ink):
        sil = sils[caster]
        if not _is_sharp(observed, sil):
            continue
        cell = ~painted & (owner == i)
        if cell.sum() < 64:
            continue
        group = {lab for lab in sorted(bands) if cell[labels == lab].mean() > 0.9}
        if not group:
            continue
        cell_small = cell[sl]
        target = np.zeros(cell_small.shape)
        target[cell_small] = small_alpha[cell_small]
        if float(target.max()) < MIN_PEAK:
            continue
        src = sil[sl].astype(np.float64)
        if src.sum() < 16:
            continue
        gy, gx = np.nonzero(cell_small & (target > 0.35 * target.max()))
        sy, sx = np.nonzero(src > 0.5)
        seed = (float(gx.mean() - sx.mean()), float(gy.mean() - sy.mean())) if gy.size and sy.size else (0.0, 0.0)
        dx, dy, sigma, k, _ = _fit_blur(src, target, cell_small, seed, False)
        opacity = min(k / 255.0, 1.0)
        if not 0.02 <= opacity:
            continue
        ink_px = np.isin(labels, sorted(group))
        colour = np.average(rgb[ink_px], axis=0, weights=np.maximum(alpha[ink_px], 1e-6))
        fitted.append((caster, dx * step, dy * step, sigma * step, float(opacity), colour, group))
    if not fitted:
        return

    # Composed as the renderer will, over nothing; the bands as they would be
    # painted, the canvas and anything else unpainted as nothing.
    domain = ~painted
    obs = observed[domain]
    model = np.zeros_like(obs)
    blurs = {}
    for caster, dx, dy, sigma, opacity, colour, _group in fitted:
        g = _blur_shift(sils[caster].astype(np.float64), dx, dy, sigma)
        blurs[caster] = g
        a = (opacity * g[domain])[:, None]
        model = model * (1.0 - a) + np.concatenate([np.clip(colour, 0, 255) * a, 255.0 * a], axis=1)
    band = np.zeros_like(obs)
    labs = labels[domain]
    for lab in np.unique(labs).tolist():
        if not visible.get(int(lab)) or int(lab) == canvas or int(lab) not in fills:
            continue
        m = labs == lab
        f = fills[int(lab)].evaluate(xs[domain][m], ys[domain][m])
        band[m] = _premultiplied(f[:, :3], f[:, 3] / 255.0)
    rms = float(np.sqrt(np.mean((obs - model) ** 2)))
    band_rms = float(np.sqrt(np.mean((obs - band) ** 2)))
    if rms >= WIN_MARGIN * band_rms:
        return
    plan.canvas = canvas
    for caster, dx, dy, sigma, opacity, colour, group in fitted:
        plan.shadows[caster] = Shadow(caster, dx, dy, sigma, np.clip(colour, 0, 255), opacity, False, rms,
                                      _filter_region(sils[caster], dx, dy, sigma))
        plan.absorbed |= group


def _detect_inset(plan, labels, fills, visible, silhouette, prep, xs, ys, areas, means, vis, min_region, total, sl, step) -> None:
    """An inner shadow darkens the inside of its own shape, against its own fill."""
    rgb = prep.rgb
    for caster in sorted(vis, key=lambda l: -areas[l]):
        if areas[caster] < 0.02 * total or areas[caster] > 0.6 * total:
            continue
        sil = silhouette(caster)
        if not _is_sharp(rgb, sil):
            continue
        inside = [lab for lab in vis if lab != caster and sil[labels == lab].mean() > 0.9 and areas[lab] < areas[caster]]
        if not inside:
            continue
        b0 = _backdrop_colour(rgb[labels == caster])
        deltas = np.stack([means[lab] - b0 for lab in inside])
        norms = np.linalg.norm(deltas, axis=1)
        if float(norms.max()) < MIN_PEAK:
            continue
        u = deltas[int(np.argmax(norms))] / float(norms.max())
        group = {
            lab for lab, d in zip(inside, deltas)
            if float(np.dot(d, u)) > 0 and float(np.linalg.norm(d - float(np.dot(d, u)) * u)) <= RAY_TOL
        }
        if not group:
            continue
        sil_small = sil[sl]
        target = np.zeros(sil_small.shape)
        target[sil_small] = (rgb[sl][sil_small] - b0) @ u
        if float(target.max()) < MIN_PEAK:
            continue
        src = sil_small.astype(np.float64)
        dx, dy, sigma, k, rms = _fit_blur(src, target, sil_small, (0.0, 0.0), True)
        if k <= 0.0:
            continue
        a_full = sil.astype(np.float64)
        g_full = a_full * (1.0 - _blur_shift(a_full, dx * step, dy * step, sigma * step))
        model = b0 + (k * g_full[sil])[:, None] * u
        rms, band_rms = _gate(rgb, sil, model, labels, fills, xs, ys)
        if rms >= WIN_MARGIN * band_rms:
            continue
        length = _cube_exit(b0, u)
        if length <= 1e-6:
            continue
        opacity, colour = k / length, b0 + length * u
        if opacity > 1.0:
            opacity, colour = 1.0, b0 + k * u
        if not 0.02 <= opacity <= 1.0:
            continue
        plan.shadows[caster] = Shadow(caster, dx * step, dy * step, sigma * step, np.clip(colour, 0, 255), float(opacity), True, rms, _filter_region(sil, dx * step, dy * step, sigma * step))
        plan.absorbed |= group
        plan.refit.add(caster)
        corrected = np.concatenate([rgb, (prep.alpha * 255.0)[..., None]], axis=-1).copy()
        corrected[..., :3] -= (k * g_full)[..., None] * u
        plan.corrected = np.clip(corrected, 0, 255)
        return


# --- emission --------------------------------------------------------------------


def shadow_filter_svg(shadow: Shadow, fid: str, precision: int) -> str:
    """The filter that regenerates the shadow.

    `color-interpolation-filters="sRGB"` is not optional: the default of
    linearRGB blurs in a different space and changes the falloff.
    """
    p = precision
    rx, ry, rw, rh = shadow.region
    head = (
        f'<filter id="{fid}" filterUnits="userSpaceOnUse" x="{_fmt(rx, 1)}" y="{_fmt(ry, 1)}" '
        f'width="{_fmt(rw, 1)}" height="{_fmt(rh, 1)}" color-interpolation-filters="sRGB">'
    )
    flood = f'<feFlood flood-color="{_hex(shadow.colour)}" flood-opacity="{_fmt(shadow.opacity, 3)}"/>'
    if shadow.inset:
        return (
            head
            + f'<feOffset in="SourceAlpha" dx="{_fmt(shadow.dx, p)}" dy="{_fmt(shadow.dy, p)}" result="o"/>'
            + f'<feGaussianBlur in="o" stdDeviation="{_fmt(shadow.sigma, p)}" result="b"/>'
            + '<feComposite in="SourceAlpha" in2="b" operator="out" result="r"/>'
            + flood
            + '<feComposite in2="r" operator="in" result="s"/>'
            + '<feMerge><feMergeNode in="SourceGraphic"/><feMergeNode in="s"/></feMerge></filter>'
        )
    return (
        head
        + f'<feGaussianBlur in="SourceAlpha" stdDeviation="{_fmt(shadow.sigma, p)}"/>'
        + f'<feOffset dx="{_fmt(shadow.dx, p)}" dy="{_fmt(shadow.dy, p)}" result="o"/>'
        + flood
        + '<feComposite in2="o" operator="in" result="s"/>'
        + '<feMerge><feMergeNode in="s"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
    )
