"""Task 11: opt-in render-and-compare refinement."""
from __future__ import annotations

import math

import numpy as np

from bench.geometry import outline_error
from tests.test_vexel_symmetry import HEART, heart_png
from tests.test_vexel_topology import path_anchors, synthetic_wedge, trace


def wedge_truth_svg(size: int = 256, tip: tuple[float, float] = (75.0, 181.0), opening: float = 15.0) -> str:
    """The vector the fixture in `synthetic_wedge` was rendered from."""
    top = tip[1] / math.tan(math.radians(45 + opening))
    steep = (math.cos(math.radians(-(45 + opening))), math.sin(math.radians(-(45 + opening))))
    under = (tip[0] - 6.0 * steep[0], tip[1] - 6.0 * steep[1])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
            f'<rect width="{size}" height="{size}" fill="#fff"/>'
            f'<polygon points="{under[0]:.3f},{under[1]:.3f} {tip[0] + top:.3f},0 {size},0 {size},8" fill="#bfe0ff"/>'
            f'<polygon points="0,{size} {size},0 {size},{size}" fill="#3b8ee8"/></svg>')


def test_refinement_is_off_by_default_and_leaves_tips_frame_and_exact_edges_alone():
    """A wedge is lines meeting at a tip and at the frame: nothing the renderer
    may move. The approach lines placed the tip; pixels barely change along a
    15° tip's bisector, so asking them would only wander (one run put it
    0.13 px further off)."""
    png, tip, _ = synthetic_wedge()
    plain = trace(png)
    assert trace(png, refine=False) == plain
    refined = trace(png, refine=True)
    a, b = path_anchors(plain, "#bfe0ff"), path_anchors(refined, "#bfe0ff")
    for node in (tip, np.array([256.0, 0.0])):
        assert np.linalg.norm(a - node, axis=1).min() == np.linalg.norm(b - node, axis=1).min()
    truth = wedge_truth_svg()
    assert outline_error(truth, refined, 256, 256)["outline_px"] <= outline_error(truth, plain, 256, 256)["outline_px"] + 1e-9


def test_refinement_brings_a_curved_mark_closer_to_its_vector_truth():
    """The heart is cubics with interior joints and arms: what the renderer can
    place. 0.075 px from the truth by the placement model, 0.061 after asking."""
    truth = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" fill="#fff"/>'
             f'<path d="{HEART}" fill="#d62839"/></svg>')
    png = heart_png()
    plain = outline_error(truth, trace(png), 512, 512)["outline_px"]
    refined = outline_error(truth, trace(png, refine=True), 512, 512)["outline_px"]
    assert refined <= 0.9 * plain, (plain, refined)
