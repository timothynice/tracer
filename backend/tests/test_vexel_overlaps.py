"""Overlap decomposition: a blend region is two shapes' overlap only if its outline is theirs."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
import resvg_py
from PIL import Image

from studi0trace.engines.vexel.curves import CurveParams
from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
from studi0trace.engines.vexel.fills import Solid
from studi0trace.engines.vexel.overlaps import decompose_overlaps
from studi0trace.imaging.intake import load_upload

CLAUS = Path(__file__).resolve().parents[1] / "bench" / "heldout" / "fluent-flat" / "mx-claus-512-q75.jpg"


def _solid(*rgb) -> Solid:
    return Solid(rgba=np.array([*rgb, 255.0]))


def test_two_translucent_squares_are_decomposed():
    """The textbook case: red at 50 % over blue on white. The overlap's outline
    is the two squares' outlines, and it is absorbed into both."""
    lab = np.ones((80, 80), np.int32)
    lab[10:50, 10:50] = 2  # red over white
    lab[30:70, 30:70] = 3  # blue
    lab[30:50, 30:50] = 4  # red over blue
    fills = {1: _solid(255, 255, 255), 2: _solid(255, 127.5, 127.5), 3: _solid(0, 0, 255), 4: _solid(127.5, 0, 127.5)}
    dec = decompose_overlaps(lab, fills, {i: True for i in fills}, CurveParams(), tol=3.0)
    assert dec.removed == {4}
    assert dec.above == [(2, 3)]


def test_a_region_bounded_by_other_shapes_is_not_an_overlap():
    """What the JPEG Claus's face was taken for: a big region whose colour
    happens to solve as 20 % of a small white eye over a 22-pixel rim sliver.
    The union with the eye is a circle, so it passed "simpler"; but nearly all
    its outline runs along the canvas, the nose and the mouth. It was dropped,
    the eye was stretched over the whole face at 20 % white and the sliver
    repainted it on top of its own features."""
    h = w = 120
    ys, xs = np.mgrid[0:h, 0:w]
    lab = np.ones((h, w), np.int32)  # canvas
    face = np.hypot(xs - 60, ys - 60) < 45
    lab[face] = 2
    lab[((xs - 45) / 11.0) ** 2 + ((ys - 50) / 7.0) ** 2 < 1] = 3  # eye white ...
    lab[(xs >= 39) & (xs < 52) & (ys >= 45)] = 7  # ... with the iris biting into it: a crescent
    lab[(xs >= 15) & (xs < 18) & (np.abs(ys - 60) < 4) & face] = 4  # rim sliver
    lab[(np.abs(xs - 60) < 5) & (ys > 55) & (ys < 75)] = 5  # nose
    lab[(np.abs(xs - 60) < 15) & (ys > 82) & (ys < 88)] = 6  # mouth
    x = np.array([254.0, 186.0, 6.0])
    face_rgb = 0.21 * np.array([245.0, 250.0, 245.0]) + 0.79 * x  # blend-consistent by construction
    fills = {1: _solid(255, 255, 255), 2: _solid(*face_rgb), 3: _solid(253, 254, 253), 4: _solid(*x),
             5: _solid(236, 147, 2), 6: _solid(154, 14, 52), 7: _solid(125, 69, 51)}
    dec = decompose_overlaps(lab, fills, {i: True for i in fills}, CurveParams(), tol=3.0)
    assert dec.empty, (dec.removed, dec.above)


def _render(svg: str, w: int, h: int) -> np.ndarray:
    png = resvg_py.svg_to_bytes(svg_string=svg, width=w, height=h)
    return np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGB")).astype(float)


@pytest.mark.skipif(not CLAUS.exists(), reason="held-out corpus not present")
def test_claus_keeps_his_nose_and_mouth():
    """fluent-flat/mx-claus at JPEG q75, default parameters: the nose, mouth and
    cheeks were painted over by the face (outline error 5.4 px)."""
    data = CLAUS.read_bytes()
    src = np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(float)
    out = _render(VexelEngine().trace(load_upload(data, max_bytes=1 << 30, max_pixels=1 << 30), VexelParams()).svg, 512, 512)
    for name, (y0, y1, x0, x1) in {"nose": (280, 320, 222, 240), "mouth": (378, 386, 220, 260)}.items():
        err = np.abs(out[y0:y1, x0:x1] - src[y0:y1, x0:x1]).mean()
        assert err < 12.0, (name, err)
