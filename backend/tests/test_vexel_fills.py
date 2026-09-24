import math

import numpy as np

from studi0trace.engines.vexel.fills import FitParams, Linear, Radial, Solid, fit_fill, ramp_fit

P = FitParams(gradients=True, max_stops=4, tol=4.0)


def grid(w=64, h=64):
    ys, xs = np.mgrid[0:h, 0:w]
    return (xs + 0.5).astype(float).ravel(), (ys + 0.5).astype(float).ravel()


def opaque(rgb):
    return np.concatenate([rgb, np.full((rgb.shape[0], 1), 255.0)], axis=1)


def test_flat_region_is_solid():
    xs, ys = grid()
    col = opaque(np.tile([200.0, 30.0, 40.0], (xs.size, 1)))
    fill = fit_fill(xs, ys, col, P)
    assert isinstance(fill, Solid)
    assert np.allclose(fill.rgba[:3], [200, 30, 40], atol=0.5)
    _, attrs = fill.svg("g1", 2)
    assert attrs == 'fill="#c81e28"'


def test_linear_gradient_direction_and_colours():
    xs, ys = grid()
    ang = math.radians(30)
    t = ((xs - 32) * math.cos(ang) + (ys - 32) * math.sin(ang))
    t = (t - t.min()) / (t.max() - t.min())
    col = opaque(np.stack([255 * (1 - t), 60 + 0 * t, 255 * t], axis=1))
    fill = fit_fill(xs, ys, col, P)
    assert isinstance(fill, Linear)
    got = math.degrees(math.atan2(fill.y2 - fill.y1, fill.x2 - fill.x1)) % 180
    assert abs(got - 30) < 2.0
    assert np.allclose(fill.stops[0].rgba[:3], [255, 60, 0], atol=4)
    assert np.allclose(fill.stops[-1].rgba[:3], [0, 60, 255], atol=4)
    pred = fill.evaluate(xs, ys)
    assert np.sqrt(((pred - col) ** 2).mean()) < 2.0
    defs, attrs = fill.svg("g1", 2)
    assert defs.startswith('<linearGradient id="g1" gradientUnits="userSpaceOnUse"') and attrs == 'fill="url(#g1)"'


def test_multi_stop_ramp_gets_more_stops():
    xs, ys = grid(128, 16)
    t = (xs - 0.5) / 127.0
    r = np.where(t < 0.5, 255 * (1 - 2 * t), 0)
    g = np.where(t < 0.5, 255 * 2 * t, 255 * (2 - 2 * t))
    b = np.where(t < 0.5, 0, 255 * (2 * t - 1))
    col = opaque(np.stack([r, g, b], axis=1))
    fill = fit_fill(xs, ys, col, FitParams(max_stops=5, tol=3.0))
    assert isinstance(fill, Linear)
    assert len(fill.stops) >= 3
    assert np.sqrt(((fill.evaluate(xs, ys) - col) ** 2).mean()) < 4.0


def test_radial_gradient_centre_and_fit():
    xs, ys = grid(80, 80)
    r = np.hypot(xs - 30.0, ys - 45.0)
    t = np.clip(r / 40.0, 0, 1)
    col = opaque(np.stack([250 - 200 * t, 240 - 100 * t, 60 + 150 * t], axis=1))
    fill = fit_fill(xs, ys, col, P)
    assert isinstance(fill, Radial), type(fill)
    assert abs(fill.cx - 30.0) < 1.5 and abs(fill.cy - 45.0) < 1.5
    assert np.sqrt(((fill.evaluate(xs, ys) - col) ** 2).mean()) < 4.0
    defs, _ = fill.svg("g2", 1)
    assert "<radialGradient" in defs


def test_alpha_ramp_becomes_stop_opacity():
    xs, ys = grid(64, 16)
    t = (xs - 0.5) / 63.0
    col = np.stack([np.full_like(t, 20.0), np.full_like(t, 20.0), np.full_like(t, 20.0), 255 * t], axis=1)
    fill = fit_fill(xs, ys, col, P)
    assert isinstance(fill, Linear)
    defs, _ = fill.svg("g3", 2)
    assert 'stop-opacity="0' in defs  # the first stop is (nearly) transparent
    assert fill.stops[-1].rgba[3] > 240


def test_gradients_disabled_forces_solid():
    xs, ys = grid()
    t = (xs - 0.5) / 63.0
    col = opaque(np.stack([255 * t, 0 * t, 255 * (1 - t)], axis=1))
    assert isinstance(fit_fill(xs, ys, col, FitParams(gradients=False)), Solid)


def test_ramp_fit_recovers_two_stop_ramp():
    t = np.linspace(0, 1, 200)
    col = np.stack([255 * t, 128 + 0 * t, 255 * (1 - t), np.full_like(t, 255.0)], axis=1)
    stops = ramp_fit(t, col, np.ones_like(t), max_stops=4, tol=2.0)
    assert len(stops) == 2
    assert np.allclose(stops[0].rgba, [0, 128, 255, 255], atol=1)
    assert np.allclose(stops[1].rgba, [255, 128, 0, 255], atol=1)


# --- the edge band: anti-aliasing and sharpening halos are the edge's, not the fill's ---


def _band(profile, length, across_rows=True, pad=3):
    """A straight band region whose colour varies only across it: `profile[i]`
    is the colour of its i-th row (or column). Returns pixel centres, colours
    and the fit's (weights, core) for it."""
    from studi0trace.engines.vexel.weights import interior

    n = len(profile)
    shape = (n + 2 * pad, length + 2 * pad) if across_rows else (length + 2 * pad, n + 2 * pad)
    mask = np.zeros(shape, bool)
    if across_rows:
        mask[pad:pad + n, pad:pad + length] = True
    else:
        mask[pad:pad + length, pad:pad + n] = True
    yy, xx = np.nonzero(mask)
    col = opaque(np.asarray(profile, float)[(yy if across_rows else xx) - pad])
    wt, core = interior(mask)
    return xx + 0.5, yy + 0.5, col, wt, core


# Profiles read off the Vexel wordmark (bench/corpus/real/logo/vexel-wordmark-512.png).
# The "e" counter, top to bottom: grey anti-aliasing rows either side of white.
COUNTER = [(202, 202, 206), (246, 246, 248), (252, 251, 251), (251, 251, 251), (254, 253, 254),
           (254, 253, 254), (254, 254, 254), (252, 252, 252), (255, 255, 255), (170, 170, 172)]
# The "l", left to right: grey AA, a black undershoot and a light rebound (the
# source was sharpened), the glyph's near-black, and the same halo mirrored.
ELL = [(125, 126, 129), (0, 0, 0), (35, 36, 40)] + [(6, 10, 20)] * 11 + [(24, 26, 32), (0, 0, 0), (45, 48, 55)]


def test_counter_with_anti_aliased_rim_is_solid():
    # Fitted with the rim, this came out as #cacacf → white → #aaaaac: the rim
    # rows alone decided the end stops and painted a grey band along the edge.
    xs, ys, col, wt, core = _band(COUNTER, 38)
    fill = fit_fill(xs, ys, col, FitParams(max_stops=4, tol=3.0), weights=wt, core=core)
    assert isinstance(fill, Solid), fill
    assert np.allclose(fill.rgba[:3], 253, atol=1.5)


def test_sharpening_halo_does_not_bend_a_glyph_fill():
    # Fitted with the rim: a grey (#7d7e81) streak down the l's left edge, and
    # at max_stops 6 the whole halo profile as six stops.
    xs, ys, col, wt, core = _band(ELL, 84, across_rows=False)
    for stops, tol in ((4, 3.0), (6, 2.0)):
        fill = fit_fill(xs, ys, col, FitParams(max_stops=stops, tol=tol), weights=wt, core=core)
        assert isinstance(fill, Solid), fill
        assert np.allclose(fill.rgba[:3], [6, 10, 20], atol=1.0)


def test_real_gradient_survives_the_edge_band():
    # The V's lower arm at the logo preset's tolerance: a 34-level ramp across a
    # 72 px shape, anti-aliased against white. On the core alone a solid scores
    # 4.6, under tol 5, and the ramp would be lost; "good enough" is judged over
    # the whole region (as it was before the core existed), the ramp is fitted
    # on the core.
    from studi0trace.engines.vexel.weights import interior

    mask = np.zeros((66, 78), bool)
    mask[3:63, 3:75] = True
    yy, xx = np.nonzero(mask)
    xs, ys = xx + 0.5, yy + 0.5
    wt, core = interior(mask)
    t = (xs - xs.min()) / (xs.max() - xs.min())
    ramp = np.stack([0 + 2 * t, 89 + 34 * t, np.full_like(t, 252.0)], axis=1)
    rim = (xx == 3) | (xx == 74) | (yy == 3) | (yy == 62)
    ramp[rim] = 0.5 * ramp[rim] + 0.5 * 255.0  # anti-aliased against white
    col = opaque(ramp)
    fill = fit_fill(xs, ys, col, FitParams(max_stops=4, tol=5.0), weights=wt, core=core)
    assert isinstance(fill, Linear), fill
    inner = ~rim
    err = np.sqrt(((fill.evaluate(xs[inner], ys[inner]) - col[inner]) ** 2).mean())
    assert err < 1.5
    assert all(s.rgba[0] < 10 for s in fill.stops)  # no stop took the rim's white



def test_ramp_stops_keep_a_minimum_pixel_gap():
    # A 10 px ramp whose last pixel is a step: a stop 1/16 of the span in would
    # own that pixel alone. Stops closer than KNOT_GAP px are an edge.
    from studi0trace.engines.vexel.fills import KNOT_GAP

    t = np.linspace(0, 1, 11)
    lum = np.where(t > 0.95, 120.0, 20.0)
    col = np.stack([lum, lum, lum, np.full_like(t, 255.0)], axis=1)
    stops = ramp_fit(t, col, np.ones_like(t), max_stops=6, tol=0.5, span=10.0)
    offs = [s.offset for s in stops]
    assert all(b - a >= KNOT_GAP / 10.0 - 1e-9 for a, b in zip(offs, offs[1:]))
