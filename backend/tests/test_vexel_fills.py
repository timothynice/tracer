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
