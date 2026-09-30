"""A soft edge — a ramp several pixels wide — has no position finer than its
own blur. The placement's four samples across it never cross a half, so every
vertex fell to the lattice edge and the fit followed the label staircase as a
wobble (the chin band of fluent's heart-eyes, the core of a glow, the band a
shadow makes over a gradient). These hold the vertices to the edge's line."""
from __future__ import annotations

from math import erf, sqrt

import numpy as np
import pytest

from studi0trace.engines.vexel import topology
from studi0trace.engines.vexel.curves import CurveParams, Line
from studi0trace.engines.vexel.fills import Solid


QUARTER = -0.6744897501960817  # Φ⁻¹(0.25): where a Gaussian ramp is at a quarter, in sigmas


def soft_diagonal(sigma: float, level: float = 0.25, h: int = 32, w: int = 48, slope: float = 1.0 / 6.0):
    """Two solid colours parted by the line y = 8 + slope·x, rendered through a
    Gaussian of `sigma` px across it. The labels are cut where the ramp is at
    `level` of the way across — as the partition cuts a soft band, not on the
    fills' half-coverage line — so the line the label staircase follows is
    y = 8 + slope·x + sigma·Φ⁻¹(level)."""
    a, b = np.array([240.0, 200.0, 40.0]), np.array([220.0, 60.0, 120.0])
    yy, xx = np.mgrid[0:h, 0:w]
    dist = (yy + 0.5) - (8.0 + slope * (xx + 0.5))
    cov = 0.5 * (1.0 + np.vectorize(erf)(dist / (sigma * sqrt(2.0))))  # share of `b`
    rgb = a + cov[..., None] * (b - a)
    labels = np.where(cov < level, 1, 2).astype(np.int32)
    fills = {1: Solid(np.append(a, 255.0)), 2: Solid(np.append(b, 255.0))}
    offset = sigma * QUARTER if level == 0.25 else 0.0
    return labels, rgb, np.ones((h, w)), fills, (slope, 8.0 + offset)


def line_residuals(pts: np.ndarray) -> np.ndarray:
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c)
    n = np.array([-vt[0][1], vt[0][0]])
    return (pts - c) @ n


def placed_arc(sigma: float, level: float = 0.25):
    labels, rgb, alpha, fills, line = soft_diagonal(sigma, level)
    bnd = topology.build(labels, rgb, alpha, lambda lab, qx, qy: fills[lab].evaluate(qx, qy), CurveParams(), extend=False)
    return next(a for a in bnd.arcs if a.pair == (1, 2)), line


@pytest.mark.parametrize("sigma", [3.0, 5.0])
def test_a_soft_edge_is_placed_on_its_line_not_on_the_label_staircase(sigma):
    """No half-crossing within the placement's reach: every vertex fell to the
    lattice edge, a staircase with a step every six pixels."""
    arc, (slope, y0) = placed_arc(sigma)
    inner = arc.pts[3:-3]
    res = line_residuals(inner)
    rms = float(np.sqrt(np.mean(res * res)))
    assert rms < 0.1, rms
    assert float(np.abs(res).max()) < 0.25, float(np.abs(res).max())
    assert len(arc.segments) == 1 and isinstance(arc.segments[0], Line), arc.segments
    # and it lies between the line the labels follow and the fills' half
    # crossing, 0.67σ further in: a crossing found in the outermost interval
    # is clipped to the far pixel centre on a ramp the placement does not trust
    off = float((inner[:, 1] - (y0 + slope * inner[:, 0])).mean())
    assert -0.15 < off < -QUARTER * sigma, off


def test_a_crisp_edge_keeps_its_sub_pixel_placement():
    """The control: the same diagonal through a crisp anti-aliased edge is
    placed within a tenth of a pixel and not smoothed away from it."""
    arc, (slope, y0) = placed_arc(0.45, level=0.5)
    inner = arc.pts[3:-3]
    true = y0 + slope * inner[:, 0]
    assert float(np.abs(inner[:, 1] - true).max()) < 0.1, float(np.abs(inner[:, 1] - true).max())
