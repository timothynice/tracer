"""Stage 4: reconstruct the best solid / linear / radial fill for a region.

Colours are fitted in sRGB (0–255) with alpha scaled to 0–255 as a fourth
channel, because SVG interpolates gradient stops in sRGB. Positions are
pixel-centre coordinates in SVG space.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import optimize

MAX_FIT_SAMPLES = 2500


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


def ramp_fit(t: np.ndarray, colours: np.ndarray, w: np.ndarray, max_stops: int, tol: float) -> list[Stop]:
    """Weighted piecewise-linear colour ramp over t ∈ [0,1], adding knots where the residual peaks."""
    knots = [0.0, 1.0]

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
        if _rms(pred, colours, w) <= tol:
            break
        # try candidate knot positions and keep the one that removes the most error
        best: tuple[float, float, np.ndarray, np.ndarray] | None = None
        for c in candidates:
            if min(abs(c - kk) for kk in knots) < 0.04:
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


def fit_fill(xs: np.ndarray, ys: np.ndarray, rgba255: np.ndarray, params: FitParams, weights: np.ndarray | None = None) -> Fill:
    """Best fill for the pixels (xs, ys) with colours rgba255 (N, 4), alpha channel 0..255.

    `weights` (N,) lets the caller down-weight boundary pixels, which are
    anti-aliasing mixtures of two fills and would otherwise bias the colour.
    """
    xs = np.asarray(xs, dtype=float).ravel()
    ys = np.asarray(ys, dtype=float).ravel()
    col = np.asarray(rgba255, dtype=float).reshape(-1, 4)
    w_all = 0.3 + 0.7 * (col[:, 3] / 255.0)  # transparent pixels count less for colour, but their alpha still matters
    if weights is not None:
        w_all = w_all * np.asarray(weights, dtype=float).ravel()

    rng = np.random.default_rng(1234)
    sel = _subsample(xs.size, rng)
    x, y, c, w = xs[sel], ys[sel], col[sel], w_all[sel]

    mean = np.average(c, axis=0, weights=w)
    solid = Solid(rgba=mean)
    rms_solid = _rms(solid.evaluate(x, y), c, w)
    if not params.gradients or rms_solid <= params.tol or x.size < 8:
        return solid

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
        tmin, tmax = float(t_raw.min()), float(t_raw.max())
        if tmax - tmin > 1e-6:
            t = (t_raw - tmin) / (tmax - tmin)
            stops = ramp_fit(t, c, w, params.max_stops, params.tol)
            lin = Linear(x1=cx0 + tmin * d[0], y1=cy0 + tmin * d[1], x2=cx0 + tmax * d[0], y2=cy0 + tmax * d[1], stops=stops)
            candidates.append((_rms(lin.evaluate(x, y), c, w), lin))

    # --- radial: centre from the quadratic fit, refined numerically ------------------
    if x.size >= 24:
        xr, yr = x - cx0, y - cy0  # centroid-relative positions throughout the radial fit
        Q = np.stack([np.ones_like(xr), xr, yr, xr * xr, xr * yr, yr * yr], axis=1)
        qc, *_ = np.linalg.lstsq(Q * np.sqrt(w)[:, None], c * np.sqrt(w)[:, None], rcond=None)  # (6, 4)
        curv = 0.5 * (qc[3] + qc[5])  # per-channel mean curvature
        weight = np.abs(curv)
        if weight.sum() > 1e-9:
            safe = np.where(np.abs(curv) > 1e-12, curv, 1e-12)
            centre = np.array([np.average(-qc[1] / (2 * safe), weights=weight), np.average(-qc[2] / (2 * safe), weights=weight)])
            span_x, span_y = xr.max() - xr.min(), yr.max() - yr.min()
            centre = np.clip(centre, [xr.min() - 2 * span_x, yr.min() - 2 * span_y], [xr.max() + 2 * span_x, yr.max() + 2 * span_y])

            sw = np.sqrt(w)[:, None]

            def radial_objective(cen: np.ndarray) -> float:
                """Cheap, smooth surrogate for the centre search: colour as a cubic polynomial in r."""
                r = np.hypot(xr - cen[0], yr - cen[1])
                rn = r / max(float(r.max()), 1e-9)
                basis = np.stack([np.ones_like(rn), rn, rn * rn, rn**3], axis=1)
                coef, *_ = np.linalg.lstsq(basis * sw, c * sw, rcond=None)
                return _rms(basis @ coef, c, w)

            def radial_for(cen: np.ndarray) -> tuple[float, Radial]:
                r = np.hypot(xr - cen[0], yr - cen[1])
                rmax = float(r.max())
                if rmax < 1e-6:
                    return np.inf, Radial(float(cen[0] + cx0), float(cen[1] + cy0), 1.0, [])
                stops = ramp_fit(r / rmax, c, w, params.max_stops, params.tol)
                rad = Radial(cx=float(cen[0] + cx0), cy=float(cen[1] + cy0), r=rmax, stops=stops)
                return _rms(rad.evaluate(x, y), c, w), rad

            # Only search when the radial surrogate at the initial centre already
            # beats the best candidate so far by a margin; most regions are not radial.
            best_so_far = min(r for r, _ in candidates)
            if radial_objective(centre) < best_so_far - 0.25 * params.tol:
                res = optimize.minimize(radial_objective, centre, method="Nelder-Mead",
                                        options={"maxiter": 80, "xatol": 0.05, "fatol": 0.01})
                rms_rad, rad = radial_for(res.x)
                candidates.append((rms_rad, rad))

    # --- choose: a gradient must buy a real improvement over solid ---------------------
    penalty = {"solid": 0.0, "linear": 0.35 * params.tol, "radial": 0.5 * params.tol}
    best_rms, best = min(candidates, key=lambda cr: cr[0] + penalty[cr[1].kind])
    if best.kind != "solid" and rms_solid - best_rms < 0.25 * params.tol:
        return solid
    return best
