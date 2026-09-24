"""Stage 4: reconstruct the best solid / linear / radial fill for a region.

Colours are fitted in sRGB (0–255) with alpha scaled to 0–255 as a fourth
channel, because SVG interpolates gradient stops in sRGB. Positions are
pixel-centre coordinates in SVG space.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import optimize

# How many pixels a fill is fitted from.
#
# At 2 500 the radial centre search carries real sampling noise: two draws from
# a 12 000-pixel backdrop put the fitted centre half a pixel apart and move the
# gradient stops by ten levels, and on two corpus items the *rescue* of
# sub-pixel lines turned on whether this particular draw happened to
# misestimate the background's alpha. Ten times the samples costs nothing
# measurable and removes it.
MAX_FIT_SAMPLES = 25000

# Fewest core pixels a gradient is fitted from (`fit_fill`'s `core`). A few
# pixels in the middle of a band-wide region leave the ramp's stops to be
# extrapolated from a handful of values: ill-posed, and the two engines'
# solvers answered it differently. Fewer, and the region is its core's solid.
CORE_GRADIENT_MIN = 24

# Fewest pixels between two gradient stops (see `ramp_fit`).
KNOT_GAP = 3.0

# A region whose alpha, averaged with its interior weights, is at most this is
# not painted: the transparent canvas, a hole (the engine's `visible`). Its fill
# is never drawn; it is only the level that its neighbours' edges are placed
# against and that the rescue measures ink against, and for that it is solid
# (see `fit_fill`).
INVISIBLE_ALPHA = 0.04


# --- fill models ------------------------------------------------------------------


@dataclass(frozen=True)
class Stop:
    offset: float  # 0..1
    rgba: np.ndarray  # (4,) r, g, b 0..255, a 0..255


def _interp_stops(t: np.ndarray, stops: list[Stop]) -> np.ndarray:
    offsets = np.array([s.offset for s in stops])
    colours = np.stack([s.rgba for s in stops])  # (S, 4)
    t = np.clip(t, 0.0, 1.0)
    out = np.empty((t.size, 4))
    for c in range(4):
        out[:, c] = np.interp(t, offsets, colours[:, c])
    return out


def _hex(rgb: np.ndarray) -> str:
    r, g, b = (int(round(float(v))) for v in np.clip(rgb[:3], 0, 255))
    return f"#{r:02x}{g:02x}{b:02x}"


def _fmt(v: float, precision: int) -> str:
    s = f"{v:.{precision}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _stops_svg(stops: list[Stop], precision: int) -> str:
    parts = []
    for s in stops:
        a = float(np.clip(s.rgba[3], 0, 255)) / 255.0
        op = "" if a >= 0.999 else f' stop-opacity="{_fmt(a, 3)}"'
        parts.append(f'<stop offset="{_fmt(s.offset, 3)}" stop-color="{_hex(s.rgba)}"{op}/>')
    return "".join(parts)


@dataclass(frozen=True)
class Solid:
    rgba: np.ndarray  # (4,) with alpha in 0..255

    def evaluate(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        return np.broadcast_to(self.rgba, (xs.size, 4)).copy()

    def svg(self, gid: str, precision: int) -> tuple[str, str]:
        a = float(np.clip(self.rgba[3], 0, 255)) / 255.0
        attrs = f'fill="{_hex(self.rgba)}"'
        if a < 0.999:
            attrs += f' fill-opacity="{_fmt(a, 3)}"'
        return "", attrs

    @property
    def kind(self) -> str:
        return "solid"


@dataclass(frozen=True)
class Linear:
    x1: float
    y1: float
    x2: float
    y2: float
    stops: list[Stop] = field(default_factory=list)

    def evaluate(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        denom = dx * dx + dy * dy
        t = ((xs - self.x1) * dx + (ys - self.y1) * dy) / denom if denom > 0 else np.zeros_like(xs, dtype=float)
        return _interp_stops(np.asarray(t, dtype=float).ravel(), self.stops)

    def svg(self, gid: str, precision: int) -> tuple[str, str]:
        p = precision
        defs = (
            f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" '
            f'x1="{_fmt(self.x1, p)}" y1="{_fmt(self.y1, p)}" x2="{_fmt(self.x2, p)}" y2="{_fmt(self.y2, p)}">'
            f"{_stops_svg(self.stops, p)}</linearGradient>"
        )
        return defs, f'fill="url(#{gid})"'

    @property
    def kind(self) -> str:
        return "linear"


@dataclass(frozen=True)
class Radial:
    cx: float
    cy: float
    r: float
    stops: list[Stop] = field(default_factory=list)

    def evaluate(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        t = np.hypot(xs - self.cx, ys - self.cy) / max(self.r, 1e-9)
        return _interp_stops(np.asarray(t, dtype=float).ravel(), self.stops)

    def svg(self, gid: str, precision: int) -> tuple[str, str]:
        p = precision
        defs = (
            f'<radialGradient id="{gid}" gradientUnits="userSpaceOnUse" '
            f'cx="{_fmt(self.cx, p)}" cy="{_fmt(self.cy, p)}" r="{_fmt(self.r, p)}">'
            f"{_stops_svg(self.stops, p)}</radialGradient>"
        )
        return defs, f'fill="url(#{gid})"'

    @property
    def kind(self) -> str:
        return "radial"


Fill = Solid | Linear | Radial


# --- fitting ------------------------------------------------------------------------


def _rms(pred: np.ndarray, target: np.ndarray, w: np.ndarray) -> float:
    err = ((pred - target) ** 2).sum(axis=1)
    return float(np.sqrt(np.sum(w * err) / max(np.sum(w) * 4.0, 1e-12)))


def ramp_fit(t: np.ndarray, colours: np.ndarray, w: np.ndarray, max_stops: int, tol: float, span: float = 0.0,
             check: tuple | None = None) -> list[Stop]:
    """Weighted piecewise-linear colour ramp over t ∈ [0,1], adding knots where the residual peaks.

    `span` is the ramp's length in pixels. Two stops closer than KNOT_GAP px
    describe a step, which is an edge (the partition's business), not a
    gradient, and the knots between them rest on a pixel or two: on a 10 px
    ramp the relative spacing alone put two stops 0.6 px apart, fitted to a
    handful of core pixels, which the two engines' solvers resolved
    differently.

    `check` = (t, colours, w) are the pixels the "good enough" test is made
    over when they are not the fitted ones (the whole region, when the ramp is
    fitted to its core).
    """
    knots = [0.0, 1.0]
    gap = max(0.04, KNOT_GAP / span) if span > 0 else 0.04

    def solve(knots: list[float]) -> tuple[np.ndarray, np.ndarray]:
        k = np.array(knots)
        # hat basis: value at knot i contributes linearly between neighbours
        basis = np.zeros((t.size, k.size))
        idx = np.clip(np.searchsorted(k, t, side="right") - 1, 0, k.size - 2)
        left, right = k[idx], k[idx + 1]
        frac = np.where(right > left, (t - left) / np.maximum(right - left, 1e-12), 0.0)
        basis[np.arange(t.size), idx] = 1.0 - frac
        basis[np.arange(t.size), idx + 1] += frac
        sw = np.sqrt(w)[:, None]
        coef, *_ = np.linalg.lstsq(basis * sw, colours * sw, rcond=None)
        return np.clip(coef, 0, 255), basis @ coef

    def sse(pred: np.ndarray) -> float:
        return float(np.sum(w * ((pred - colours) ** 2).sum(axis=1)))

    coef, pred = solve(knots)
    current = sse(pred)
    candidates = np.linspace(0.0, 1.0, 17)[1:-1]
    while len(knots) < max_stops:
        if check is None:
            good = _rms(pred, colours, w) <= tol
        else:
            tc, cc, wc = check
            kk = np.array(knots)
            pc = np.stack([np.interp(np.clip(tc, 0.0, 1.0), kk, coef[:, ch]) for ch in range(4)], axis=1)
            good = _rms(pc, cc, wc) <= tol
        if good:
            break
        # try candidate knot positions and keep the one that removes the most error
        best: tuple[float, float, np.ndarray, np.ndarray] | None = None
        for c in candidates:
            if min(abs(c - kk) for kk in knots) < gap:
                continue
            trial = sorted(knots + [float(c)])
            tc, tp = solve(trial)
            s = sse(tp)
            if best is None or s < best[0]:
                best = (s, float(c), tc, tp)
        if best is None or best[0] > current * 0.98:
            break
        current, knot, coef, pred = best
        knots = sorted(knots + [knot])
    return [Stop(offset=float(k), rgba=np.asarray(c, dtype=float)) for k, c in zip(knots, coef)]


def _subsample(n: int, rng: np.random.Generator) -> np.ndarray:
    if n <= MAX_FIT_SAMPLES:
        return np.arange(n)
    return rng.choice(n, MAX_FIT_SAMPLES, replace=False)


@dataclass(frozen=True)
class FitParams:
    gradients: bool = True
    max_stops: int = 4
    tol: float = 4.0  # RMS in 0..255 units at which a model is "good enough"


def fit_fill(xs: np.ndarray, ys: np.ndarray, rgba255: np.ndarray, params: FitParams, weights: np.ndarray | None = None,
             core: np.ndarray | None = None) -> Fill:
    """Best fill for the pixels (xs, ys) with colours rgba255 (N, 4), alpha channel 0..255.

    `weights` (N,) lets the caller down-weight boundary pixels, which are
    anti-aliasing mixtures of two fills and would otherwise bias the colour.

    `core` (N,) bool marks the region's interior (`weights.fill_core`); the
    rest is its edge band, whose colour belongs to the edge — anti-aliasing, a
    blur, a sharpening halo — and is redrawn by the shape's own anti-aliased
    outline. Down-weighting the band is not enough: nothing but the rim lies
    at the ends of a ramp across a small shape, so a stop placed a sixteenth of
    the span in owned the rim alone, whatever its weight, and a white counter
    came out as a ramp from rim grey through white to rim grey. So with a core:

    - the solid colour is the core's mean;
    - "good enough" is still judged over every pixel, rim included — the solid
      early exit and the ramp's stopping test — which keeps `tol` meaning what
      it was calibrated to mean;
    - a gradient's direction and stops are fitted to the core alone, and
      it has to beat the solid on the core. The ramp spans the whole region,
      so a real gradient reaches the edge: over the band its ends are the
      core's ramp carried on, not the band's colour;
    - a radial's centre is geometry and is searched over every pixel.

    A region that will not be painted (alpha averaged with `weights` at most
    INVISIBLE_ALPHA) is solid. Nothing draws its gradient, and a gradient
    there is worse than none: a drop shadow on a transparent canvas lies in
    the canvas's region, and a radial fitted to the canvas's core took up the
    shadow's outer falloff — so the rescue, measuring the canvas's pixels
    against that radial, saw only two scraps of the shadow and promoted
    those, and the edge placement read the unpainted canvas as grey ink
    beside the shape. A faint thin ring in an empty field was taken up the
    same way.
    """
    xs = np.asarray(xs, dtype=float).ravel()
    ys = np.asarray(ys, dtype=float).ravel()
    col = np.asarray(rgba255, dtype=float).reshape(-1, 4)
    w_all = 0.3 + 0.7 * (col[:, 3] / 255.0)  # transparent pixels count less for colour, but their alpha still matters
    if weights is not None:
        w_all = w_all * np.asarray(weights, dtype=float).ravel()
    painted = float(np.average(col[:, 3] / 255.0, weights=weights)) > INVISIBLE_ALPHA
    import os; painted = painted or bool(os.environ.get("VX_NOINV"))  # DEBUG-REMOVE

    rng = np.random.default_rng(1234)
    sel = _subsample(xs.size, rng)
    x, y, c, w = xs[sel], ys[sel], col[sel], w_all[sel]
    k = None if core is None else np.asarray(core, dtype=bool).ravel()[sel]
    if k is not None and (k.all() or not k.any()):
        k = None
    inner = slice(None) if k is None else k

    mean = np.average(c[inner], axis=0, weights=w[inner])
    solid = Solid(rgba=mean)
    rms_solid = _rms(solid.evaluate(x, y), c, w)
    too_few = x.size < 8 if k is None else int(k.sum()) < CORE_GRADIENT_MIN
    if not params.gradients or not painted or rms_solid <= params.tol or too_few:
        return solid
    full = None  # every pixel, for the ramps' "good enough" test
    if k is not None:
        full = (x, y, c, w)
        x, y, c, w = x[k], y[k], c[k], w[k]
        rms_solid = _rms(solid.evaluate(x, y), c, w)

    candidates: list[tuple[float, Fill]] = [(rms_solid, solid)]

    # --- linear: direction from per-channel planar gradients ----------------------
    cx0, cy0 = np.average(x, weights=w), np.average(y, weights=w)
    A = np.stack([np.ones_like(x), x - cx0, y - cy0], axis=1) * np.sqrt(w)[:, None]
    coef, *_ = np.linalg.lstsq(A, c * np.sqrt(w)[:, None], rcond=None)  # (3, 4)
    g = coef[1:3].T  # (4, 2) channel gradient vectors
    cov = g.T @ g
    evals, evecs = np.linalg.eigh(cov)
    d = evecs[:, np.argmax(evals)]
    # eigenvector sign is arbitrary: orient +x (or +y when vertical) so stop order is deterministic
    if d[0] < -1e-9 or (abs(d[0]) <= 1e-9 and d[1] < 0):
        d = -d
    if np.linalg.norm(d) > 0 and evals.max() > 1e-12:
        t_raw = (x - cx0) * d[0] + (y - cy0) * d[1]
        # the ramp runs on over the band (a real gradient reaches the edge),
        # with the core's colours carried on, not the band's
        t_all = t_raw if full is None else (full[0] - cx0) * d[0] + (full[1] - cy0) * d[1]
        tmin, tmax = float(t_all.min()), float(t_all.max())
        if tmax - tmin > 1e-6:
            t = (t_raw - tmin) / (tmax - tmin)
            chk = None if full is None else ((t_all - tmin) / (tmax - tmin), full[2], full[3])
            stops = ramp_fit(t, c, w, params.max_stops, params.tol, span=tmax - tmin, check=chk)
            lin = Linear(x1=cx0 + tmin * d[0], y1=cy0 + tmin * d[1], x2=cx0 + tmax * d[0], y2=cy0 + tmax * d[1], stops=stops)
            candidates.append((_rms(lin.evaluate(x, y), c, w), lin))

    # --- radial: centre from the quadratic fit, refined numerically ------------------
    # The centre is geometry and is searched over every pixel: the band is
    # where a shadow or glow is strongest (it hugs its caster), and without it
    # the search on the core alone found no radial at all. Only the stops come
    # from the core.
    X, Y, C, W = full if full is not None else (x, y, c, w)
    if x.size >= 24:
        gx0, gy0 = np.average(X, weights=W), np.average(Y, weights=W)
        xr, yr = X - gx0, Y - gy0  # centroid-relative positions throughout the radial fit
        Q = np.stack([np.ones_like(xr), xr, yr, xr * xr, xr * yr, yr * yr], axis=1)
        qc, *_ = np.linalg.lstsq(Q * np.sqrt(W)[:, None], C * np.sqrt(W)[:, None], rcond=None)  # (6, 4)
        curv = 0.5 * (qc[3] + qc[5])  # per-channel mean curvature
        weight = np.abs(curv)
        if weight.sum() > 1e-9:
            safe = np.where(np.abs(curv) > 1e-12, curv, 1e-12)
            centre = np.array([np.average(-qc[1] / (2 * safe), weights=weight), np.average(-qc[2] / (2 * safe), weights=weight)])
            span_x, span_y = xr.max() - xr.min(), yr.max() - yr.min()
            centre = np.clip(centre, [xr.min() - 2 * span_x, yr.min() - 2 * span_y], [xr.max() + 2 * span_x, yr.max() + 2 * span_y])

            sw = np.sqrt(W)[:, None]

            def radial_objective(cen: np.ndarray) -> float:
                """Cheap, smooth surrogate for the centre search: colour as a cubic polynomial in r."""
                r = np.hypot(xr - cen[0], yr - cen[1])
                rn = r / max(float(r.max()), 1e-9)
                basis = np.stack([np.ones_like(rn), rn, rn * rn, rn**3], axis=1)
                coef, *_ = np.linalg.lstsq(basis * sw, C * sw, rcond=None)
                return _rms(basis @ coef, C, W)

            def radial_for(cen: np.ndarray) -> tuple[float, Radial]:
                r_all = np.hypot(xr - cen[0], yr - cen[1])
                r = r_all if full is None else np.hypot(x - gx0 - cen[0], y - gy0 - cen[1])
                rmax = float(r_all.max())
                if rmax < 1e-6:
                    return np.inf, Radial(float(cen[0] + gx0), float(cen[1] + gy0), 1.0, [])
                chk = None if full is None else (r_all / rmax, C, W)
                stops = ramp_fit(r / rmax, c, w, params.max_stops, params.tol, span=rmax, check=chk)
                rad = Radial(cx=float(cen[0] + gx0), cy=float(cen[1] + gy0), r=rmax, stops=stops)
                return _rms(rad.evaluate(x, y), c, w), rad

            # Only search when the radial surrogate at the initial centre already
            # beats the best candidate so far by a margin; most regions are not
            # radial. Both are measured over every pixel, like the search.
            best_so_far = min(_rms(f.evaluate(X, Y), C, W) for _, f in candidates) if full is not None \
                else min(r for r, _ in candidates)
            if radial_objective(centre) < best_so_far - 0.25 * params.tol:
                res = optimize.minimize(radial_objective, centre, method="Nelder-Mead",
                                        options={"maxiter": 80, "xatol": 0.05, "fatol": 0.01})
                rms_rad, rad = radial_for(res.x)
                candidates.append((rms_rad, rad))

    # --- choose: a gradient must buy a real improvement over solid ---------------------
    penalty = {"solid": 0.0, "linear": 0.35 * params.tol, "radial": 0.5 * params.tol}
    best_rms, best = min(candidates, key=lambda cr: cr[0] + penalty[cr[1].kind])
    import builtins; getattr(builtins, "_FDEBUG", []).append((candidates, rms_solid, mean))  # DEBUG-REMOVE
    if best.kind != "solid":
        if rms_solid - best_rms < 0.25 * params.tol:
            return solid
        # Fitted to a core a few rows deep, a ramp can follow the core's own
        # noise exactly and still beat the solid there — and then carries that
        # trend on across the band: three levels over four rows of a white
        # counter became a thirteen-level grey edge. A gradient whose whole
        # range over the core is under 2·tol is one the solid already holds
        # within tol at every core pixel: there is nothing to draw.
        if full is not None and float(np.ptp(best.evaluate(x, y), axis=0).max()) < 2.0 * params.tol:
            return solid
        # In a mostly transparent region, a model that is only somewhat better
        # than solid is fitting faint ink (a sub-pixel line in an empty field), not
        # a gradient, and would paint a haze. Leave it solid so the rescue pass can
        # promote the ink. Opaque regions keep low-contrast gradients (soft shadows).
        mostly_transparent = mean[3] < 0.2 * 255.0
        if mostly_transparent and best_rms > params.tol and best_rms > 0.5 * rms_solid:
            return solid
    return best
