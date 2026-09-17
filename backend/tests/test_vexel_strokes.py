import io
import math

import numpy as np
import resvg_py
from PIL import Image

from studi0trace.engines.vexel.curves import CurveParams
from studi0trace.engines.vexel.strokes import is_thin, stroke_geometry, stroke_svg


def render(svg: str, w: int, h: int) -> np.ndarray:
    png = resvg_py.svg_to_bytes(svg_string=svg, width=w, height=h)
    return np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"))


def test_is_thin_distinguishes_lines_from_blobs():
    line = np.zeros((40, 40), bool)
    line[20:22, 4:36] = True
    blob = np.zeros((40, 40), bool)
    blob[10:30, 10:30] = True
    assert is_thin(line)
    assert not is_thin(blob)


def test_straight_line_width_and_geometry():
    mask = np.zeros((40, 60), bool)
    mask[19:21, 5:55] = True  # 2 px thick, 50 px long
    coverage = mask.astype(np.float32)
    s = stroke_geometry(mask, coverage)
    assert s is not None
    assert abs(s.width - 2.0) < 0.15
    assert len(s.polylines) == 1 and not s.closed[0]
    xy = s.polylines[0]
    assert abs(xy[:, 1].mean() - 20.0) < 0.6
    assert xy[:, 0].min() < 7 and xy[:, 0].max() > 53


def test_subpixel_coverage_gives_subpixel_width():
    mask = np.zeros((20, 60), bool)
    mask[10, 5:55] = True
    coverage = np.where(mask, 0.5, 0.0).astype(np.float32)  # half-covered pixels: a 0.5 px line
    s = stroke_geometry(mask, coverage)
    assert s is not None and abs(s.width - 0.5) < 0.1


def test_ring_becomes_a_closed_stroke_that_renders_like_the_source():
    size = 96
    src = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"><circle cx="48" cy="48" r="30" fill="none" stroke="#123456" stroke-width="2"/></svg>'
    img = render(src, size, size)
    alpha = img[..., 3].astype(np.float32) / 255.0
    mask = alpha > 0.05
    assert is_thin(mask)
    s = stroke_geometry(mask, alpha)
    assert s is not None
    assert abs(s.width - 2.0) < 0.25
    assert any(s.closed), "the ring centreline should be a closed loop"
    out_svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">{stroke_svg(s, "#123456", 1.0, CurveParams(), 2)}</svg>'
    out = render(out_svg, size, size)
    diff = np.abs(out[..., 3].astype(float) - img[..., 3].astype(float))
    assert diff.mean() < 8.0, diff.mean()
    assert 'fill="none"' in out_svg and 'stroke-width="2' in out_svg


def test_open_curve_stroke():
    size = 80
    src = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"><path d="M10 60 C 30 10, 50 10, 70 60" fill="none" stroke="#000" stroke-width="3" stroke-linecap="round"/></svg>'
    img = render(src, size, size)
    alpha = img[..., 3].astype(np.float32) / 255.0
    s = stroke_geometry(alpha > 0.05, alpha)
    assert s is not None and abs(s.width - 3.0) < 0.35
    assert not any(s.closed)
    svg = stroke_svg(s, "#000000", 1.0, CurveParams(), 2)
    assert svg.count("M") >= 1 and "Z" not in svg
