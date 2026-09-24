"""`gradients=False`: every fitted ramp is cut into flat bands along its own
level lines (`vexel/posterize.py`, `vexel-rs/src/posterize.rs`).

A designer posterising a gradient cuts the gradient, not the pixels: a linear
ramp into strips between parallel straight lines, a radial one into rings
between concentric circles, every band the same colour distance wide. Cutting
the noisy pixels instead gave bands whose edges were iso-lines of the noise.
"""
from __future__ import annotations

import re

import numpy as np
import pytest

from studi0trace.engines.vexel import engine as vexel
from studi0trace.engines.vexel.engine import VexelParams, trace_rgba
from studi0trace.engines.vexel.fills import Linear, Radial, Stop
from studi0trace.engines.vexel.posterize import BAND_MIN_PX, Levels, _features, band_levels

FLAT = VexelParams(gradients=False, shadows=False, detail=14.0, min_region=16)


def _grey_ramp() -> Linear:
    return Linear(0.0, 0.0, 100.0, 0.0, stops=[Stop(0.0, np.array([0.0, 0.0, 0.0, 255.0])),
                                               Stop(1.0, np.array([255.0, 255.0, 255.0, 255.0]))])


def _colour_distance(ramp: Linear, t0: float, t1: float) -> float:
    ts = np.linspace(t0, t1, 400)
    f = _features(ramp.evaluate(ts * 100.0, np.zeros_like(ts)))
    return float(np.sqrt(((f[1:] - f[:-1]) ** 2).sum(axis=1)).sum())


def test_a_ramp_is_cut_into_bands_of_equal_colour_distance_none_over_the_step():
    ramp = _grey_ramp()
    levels = band_levels(ramp, 0.0, 1.0, 14.0)
    assert levels.size == 7, levels  # black to white is 100 ΔE: eight bands
    assert np.all(np.diff(levels) > 0)
    edges = np.concatenate([[0.0], levels, [1.0]])
    widths = [_colour_distance(ramp, a, b) for a, b in zip(edges[:-1], edges[1:])]
    assert max(widths) <= 14.0 + 0.1, widths
    assert max(widths) - min(widths) < 0.05 * max(widths), widths


def test_a_short_ramp_gets_fewer_bands_rather_than_slivers():
    ramp = _grey_ramp()
    levels = band_levels(ramp, 0.0, 0.2, 1.0)  # 20 px of ramp, a band per ΔE asked for
    edges = np.concatenate([[0.0], levels, [0.2]]) * 100.0
    assert levels.size >= 1
    assert np.diff(edges).min() >= BAND_MIN_PX - 1e-9, edges


def test_a_radial_holding_its_centre_measures_the_inner_disc_by_its_diameter():
    # a 12 px eye: a dark pupil of radius ~2.5 px, white beyond — one level,
    # though the pupil is narrower than BAND_MIN_PX from centre to edge
    eye = Radial(0.0, 0.0, 12.0, stops=[Stop(0.0, np.array([0.0, 46.0, 100.0, 255.0])),
                                        Stop(0.38, np.array([248.0, 250.0, 251.0, 255.0])),
                                        Stop(1.0, np.array([255.0, 255.0, 255.0, 255.0]))])
    levels = band_levels(eye, 0.05, 1.0, 14.0)
    assert levels.size == 1 and 2 * levels[0] * 12.0 >= BAND_MIN_PX, levels
    assert band_levels(eye, 0.3, 1.0, 14.0).size == 0, "a ring holds no disc"


def test_a_band_edge_crosses_its_lattice_edge_on_the_level():
    lv = Levels(band={1: (7, 0), 2: (7, 1), 3: (7, 2)}, fields={7: _grey_ramp()}, levels={7: np.array([0.3025, 0.6])})
    s = lv.crossing(1, 2, np.array([[30.0, 5.5]]), np.array([[31.0, 5.5]]))
    assert abs(float(s[0]) - 0.25) < 1e-12
    assert lv.crossing(1, 3, np.array([[30.0, 5.5]]), np.array([[31.0, 5.5]])) is None  # not neighbours on the ramp
    assert np.isnan(lv.crossing(2, 3, np.array([[30.0, 5.5]]), np.array([[31.0, 5.5]]))[0])
    assert np.allclose(lv.onto(1, 2, np.array([29.0, 3.0])), [30.25, 3.0])
    # held on a canvas edge, a node reaches the level along that edge
    assert np.allclose(lv.onto(1, 2, np.array([29.0, 0.0]), along=0), [30.25, 0.0])


def _linear_card(size: int = 160) -> tuple[np.ndarray, np.ndarray]:
    yy, xx = np.mgrid[0:size, 0:size]
    img = np.zeros((size, size, 4), np.uint8)
    img[..., 3] = 255
    img[..., :3] = 250
    box = (xx >= 20) & (xx < 140) & (yy >= 30) & (yy < 130)
    t = ((xx + 0.5 - 20) / 120.0).clip(0, 1)
    img[box, 0] = (20 + 200 * t)[box]
    img[box, 1] = (40 + 150 * t)[box]
    img[box, 2] = 200
    rng = np.random.default_rng(3)  # a little noise, which pixel posterising drew as wobble
    noisy = img.astype(float)
    noisy[box, :3] += rng.normal(0.0, 1.5, (int(box.sum()), 3))
    return noisy.clip(0, 255).astype(np.uint8), box


def _radial_disc(size: int = 160) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size]
    img = np.zeros((size, size, 4), np.uint8)
    img[..., 3] = 255
    img[..., :3] = 250
    r = np.hypot(xx + 0.5 - 80, yy + 0.5 - 80)
    disc = r < 64
    t = (r / 64).clip(0, 1)
    img[disc, 0] = (250 - 200 * t)[disc]
    img[disc, 1] = (200 - 150 * t)[disc]
    img[disc, 2] = (60 + 100 * t)[disc]
    return img


def _strips(svg: str) -> list[tuple[float, float, float, float, str]]:
    """Every band a linear ramp was cut into, as (x0, y0, x1, y1, fill), from
    the rectangles and the <use> copies of them."""
    defs = {m[4]: tuple(map(float, m[:4]))
            for m in re.findall(r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" id="(\w+)"/>', svg)}
    out = [(float(x), float(y), float(x) + float(w), float(y) + float(h), f)
           for x, y, w, h, f in re.findall(
               r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" fill="(#[0-9a-f]{6})"/>', svg)]
    for ref, dx, dy, f in re.findall(r'<use href="#(\w+)"(?: x="([\d.-]+)" y="([\d.-]+)")? fill="(#[0-9a-f]{6})"/>', svg):
        x, y, w, h = defs[ref]
        x, y = x + float(dx or 0), y + float(dy or 0)
        out.append((x, y, x + w, y + h, f))
    # the backdrop is the one strip that covers the canvas
    return sorted(s for s in out if s[2] - s[0] < 150)


@pytest.mark.parametrize("engine", ["python", "rust"])
def test_a_linear_ramp_posterises_into_parallel_straight_strips_that_tile(engine):
    if engine == "rust" and vexel._vexel_rs is None:
        pytest.skip("the vexel_rs extension is not built")
    img, _ = _linear_card()
    if engine == "python":
        svg = trace_rgba(img, FLAT)
    else:
        svg = vexel._vexel_rs.trace(img.tobytes(), img.shape[1], img.shape[0], FLAT.model_dump())
    assert "Gradient" not in svg
    assert 'fill="none"' not in svg and "opacity" not in svg, "a band is never a stroke or an overlap"
    strips = [s for s in _strips(svg)]
    assert len(strips) >= 5, svg
    for (x0, y0, x1, y1, _), (nx0, *_rest) in zip(strips, strips[1:]):
        assert abs(x1 - nx0) <= 0.02, (strips, "neighbouring bands share one straight edge")
    assert all(abs(s[1] - 30.0) <= 0.02 and abs(s[3] - 130.0) <= 0.02 for s in strips), strips
    greys = [int(s[4][1:3], 16) for s in strips]
    assert greys == sorted(greys), "the bands follow the ramp"


@pytest.mark.parametrize("engine", ["python", "rust"])
def test_a_radial_ramp_posterises_into_concentric_circles(engine):
    if engine == "rust" and vexel._vexel_rs is None:
        pytest.skip("the vexel_rs extension is not built")
    img = _radial_disc()
    if engine == "python":
        svg = trace_rgba(img, FLAT)
    else:
        svg = vexel._vexel_rs.trace(img.tobytes(), img.shape[1], img.shape[0], FLAT.model_dump())
    circles = [tuple(map(float, c)) for c in re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)"', svg)]
    assert len(circles) >= 6, svg
    assert "<path" not in svg, svg
    assert all(abs(cx - 80.0) <= 0.02 and abs(cy - 80.0) <= 0.02 for cx, cy, _ in circles), circles
    assert abs(max(r for *_, r in circles) - 64.0) < 0.3
