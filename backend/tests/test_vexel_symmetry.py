"""Task 10: mirror and rotational symmetry of the placed boundary."""
from __future__ import annotations

import math

import numpy as np
import resvg_py
from scipy.spatial import cKDTree

from studi0trace.engines.vexel.symmetry import (
    SYM_MEAN,
    mirror_axes,
    reflect,
    rotational_order,
    symmetrize,
    symmetrize_ring,
    symmetrize_rotational,
)
from tests.test_vexel_topology import sample_path, trace

HEART = ("M256 440C120 340 60 260 60 180C60 110 110 70 165 70C210 70 240 95 256 125"
         "C272 95 302 70 347 70C402 70 452 110 452 180C452 260 392 340 256 440Z")


def heart_png(size: int = 512, fill: str = "#d62839") -> bytes:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" fill="#fff"/>'
           f'<path d="{HEART}" fill="{fill}"/></svg>')
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size))


def chamfer(a: np.ndarray, b: np.ndarray) -> float:
    return float(cKDTree(b).query(a)[0].mean())


def noisy_regular(n_sides: int, r: float = 80.0, step: float = 0.7, sigma: float = 0.04, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    pts = []
    for k in range(n_sides):
        a0, a1 = 2 * math.pi * k / n_sides, 2 * math.pi * (k + 1) / n_sides
        p0 = np.array([128 + r * math.cos(a0), 128 + r * math.sin(a0)])
        p1 = np.array([128 + r * math.cos(a1), 128 + r * math.sin(a1)])
        m = int(np.linalg.norm(p1 - p0) / step)
        for f in np.linspace(0, 1, m, endpoint=False):
            pts.append(p0 + f * (p1 - p0))
    return np.array(pts) + rng.normal(0, sigma, (len(pts), 2))


def test_a_mirror_symmetric_mark_is_emitted_symmetric():
    svg = trace(heart_png())
    pts = sample_path(svg, "#d62839")
    mirrored = pts * [-1, 1] + [512, 0]
    assert chamfer(pts, mirrored) < 0.05, chamfer(pts, mirrored)


def test_symmetrize_makes_a_noisy_mirror_symmetric_ring_exact():
    poly = noisy_regular(4)                                  # a square, 4-fold and mirror symmetric
    axes = mirror_axes(poly)
    assert len(axes) >= 4
    hits = [ax for ax in axes if symmetrize(poly, ax) is not None]
    assert len(hits) >= 4, "a square has four mirror axes"
    out = symmetrize(poly, hits[0])
    c, d = hits[0]
    resid = cKDTree(out).query(reflect(out, c, d))[0]
    assert resid.mean() < 0.2 * SYM_MEAN and resid.max() < 0.05
    # a shape with no mirror axis is left alone
    t = np.linspace(0, 2 * math.pi, 400, endpoint=False)
    blob = np.column_stack([100 + 40 * np.cos(t) + 8 * np.cos(3 * t + 1.0), 100 + 30 * np.sin(t) + 6 * np.sin(2 * t + 0.4)])
    assert all(symmetrize(blob, ax) is None for ax in mirror_axes(blob))


def test_rotational_order_and_symmetrisation_of_a_hexagon():
    poly = noisy_regular(6)
    assert rotational_order(poly) == 6
    out = symmetrize_rotational(poly, 6)
    c = out.mean(axis=0)
    ca, sa = math.cos(math.pi / 3), math.sin(math.pi / 3)
    turned = c + (out - c) @ np.array([[ca, -sa], [sa, ca]]).T
    assert cKDTree(out).query(turned)[0].max() < 0.05
    assert rotational_order(noisy_regular(5)) == 5
    t = np.linspace(0, 2 * math.pi, 400, endpoint=False)
    assert rotational_order(np.column_stack([100 + 40 * np.cos(t), 100 + 25 * np.sin(t)])) == 2
    assert symmetrize_ring(poly) is not None and symmetrize_ring(poly[:10]) is None


def dot_grid_png(size: int = 256, rows: int = 3, cols: int = 3, r: float = 12.0, fill: str = "#2a9d8f") -> bytes:
    step = size / (cols + 1)
    body = "".join(f'<circle cx="{step * (i + 1):.1f}" cy="{step * (j + 1):.1f}" r="{r}" fill="{fill}"/>' for i in range(cols) for j in range(rows))
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"><rect width="{size}" height="{size}" fill="#fff"/>{body}</svg>'
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size))


def test_repeated_dots_become_use_elements():
    svg = trace(dot_grid_png())
    assert svg.count("<use ") == 9 and svg.count("<defs>") == 1, svg[:600]
    assert svg.count('id="u1"') == 1 and svg.count('href="#u1"') == 9
    # the definition carries no paint; every use carries its own
    import re as _re
    definition = _re.search(r"<defs>.*?</defs>", svg).group(0)
    assert "fill=" not in definition
    assert len(_re.findall(r'<use [^>]*fill="#[0-9a-f]{6}"', svg)) == 9
    # a use is translated to where its copy sat: the first stays put
    assert _re.search(r'<use href="#u1" fill=', svg), "the first copy is a plain use"
    assert len(_re.findall(r'<use href="#u1" x="[\d.-]+" y="[\d.-]+"', svg)) == 8
