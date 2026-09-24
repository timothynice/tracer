"""Stage 7a: rounded rectangles drawn the way a designer draws them (`rects.py`,
`topology._rectify`)."""
from __future__ import annotations

import re

import numpy as np
import resvg_py

from bench.metrics import seam_index
from studi0trace.engines.vexel import rects
from tests.test_vexel_primitives import rounded_rect_poly
from tests.test_vexel_topology import rgba, trace

NS = 'xmlns="http://www.w3.org/2000/svg"'


def png_of(body: str, size: int = 160) -> bytes:
    svg = f'<svg {NS} viewBox="0 0 {size} {size}"><rect width="{size}" height="{size}" fill="#fff"/>{body}</svg>'
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size))


def rect_attrs(svg: str) -> list[dict[str, float]]:
    out = []
    for attrs in re.findall(r"<rect ([^>]*)/>", svg):
        vals = dict(re.findall(r'(\w+)="([-\d.]+)"', attrs))
        if "rx" in vals:
            out.append({k: float(v) for k, v in vals.items()})
    return out


def test_a_small_corner_is_read_from_the_few_vertices_it_has():
    """A 3 px corner has one or two vertices half a pixel inside both sides;
    the old test wanted three and never read a radius. The least-squares
    radius reads it from all of them."""
    for r in (2.0, 3.0, 4.5):
        poly = rounded_rect_poly(10.3, 20.6, 21.0, 21.0, r, step=1.0)
        m = rects.fit_model(poly, np.ones(len(poly), bool), 1.5, 0.4)
        assert m is not None, r
        assert np.allclose([m.x0, m.y0, m.x1, m.y1], [10.3, 20.6, 31.3, 41.6], atol=0.05)
        assert np.allclose(m.r, r, atol=0.15), (r, m.r)


def test_a_sharp_rectangle_has_sharp_corners():
    poly = rounded_rect_poly(10.0, 10.0, 30.0, 20.0, 0.01, step=1.0)
    m = rects.fit_model(poly, np.ones(len(poly), bool), 1.5, 0.4)
    assert m is not None and m.r == [0.0, 0.0, 0.0, 0.0]


def test_the_outline_is_walked_from_any_point_to_any_other():
    m = rects.Model(0.0, 0.0, 20.0, 10.0, [3.0, 3.0, 3.0, 3.0], [1.0] * 4, [np.zeros(0, np.int64)] * 4)
    total = rects.perimeter(m)
    assert abs(total - (2 * (14 + 4) + 2 * np.pi * 3)) < 1e-9
    for s in np.linspace(0.0, total, 37)[:-1]:
        p = rects.point_at(m, s)
        s2, d = rects.project(m, p)
        assert d < 1e-9 and abs((s2 - s + total / 2) % total - total / 2) < 1e-6
    segs = rects.subpath(m, 1.0, total - 1.0)
    for a, b in zip(segs, segs[1:]):
        assert np.allclose(a.p1, b.p0)
    assert np.allclose(segs[0].p0, rects.point_at(m, 1.0)) and np.allclose(segs[-1].p1, rects.point_at(m, total - 1.0))


def test_cluster_1d_splits_what_one_value_cannot_hold():
    groups = rects.cluster_1d([1.9, 2.6, 2.95, 3.2], [1, 1, 1, 1], 0.48)
    assert sorted(len(m) for _v, m in groups) == [1, 3]
    groups = rects.cluster_1d([0.0, 0.3, 0.6, 0.9, 1.2], [1] * 5, 0.2)
    assert all(max(abs(v - [0.0, 0.3, 0.6, 0.9, 1.2][i]) for i in m) <= 0.2 for v, m in groups)


def pixel_squares() -> tuple[bytes, list[tuple[float, float, float, float]]]:
    """Squares as an AI logo draws them: one corner radius, edges on shared
    guides (the bottom of one is the top of the next), each a square."""
    squares = [(96.0, 20.0, 17.0, 17.0), (60.0, 37.0, 27.0, 27.0), (93.0, 64.0, 21.0, 21.0)]
    body = "".join(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="#0a90fc"/>' for x, y, w, h in squares)
    return png_of(body), squares


def test_small_rounded_squares_are_rects_with_one_radius_on_shared_guides():
    png, squares = pixel_squares()
    got = sorted(rect_attrs(trace(png)), key=lambda a: a["y"])
    assert len(got) == 3, got
    radii = {a["rx"] for a in got}
    assert len(radii) == 1, f"one radius across the squares, got {radii}"
    assert abs(radii.pop() - 3.0) < 0.35
    for a, (x, y, w, h) in zip(got, squares):
        assert abs(a["x"] - x) < 0.2 and abs(a["y"] - y) < 0.2 and abs(a["width"] - w) < 0.3, (a, (x, y, w, h))
        assert a["width"] == a["height"], "a square is square"
    # the guides: each square's bottom is the next one's top, exactly
    for a, b in zip(got, got[1:]):
        assert abs(a["y"] + a["height"] - b["y"]) < 0.011, (a, b)


def square_against_bar() -> bytes:
    """A rounded square flush against a bar, as the lower pixel of the Vexel
    mark sits against its ribbon: its corners there meet the bar's straight
    edge, and the notch between corner and edge runs out to a point."""
    return png_of('<rect x="30" y="20" width="24" height="120" fill="#35b8f8"/>'
                  '<rect x="54" y="60" width="30" height="30" rx="3" fill="#0a8cf8"/>')


def test_a_square_against_a_bar_has_four_equal_corners_and_still_tiles():
    png = square_against_bar()
    svg = trace(png)
    d = next(m.group(1) for m in re.finditer(r'<path d="([^"]+)" fill="#0[0-9a-f]{5}"', svg) if "A" in m.group(1))
    radii = [float(r) for r in re.findall(r"A([\d.]+) ", d)]
    assert len(radii) == 4, d
    assert max(radii) - min(radii) < 1e-9 and abs(radii[0] - 3.0) < 0.4, radii
    assert seam_index(svg, rgba(png)) < 200.0


def test_a_rounded_corner_between_two_lines_is_one_circle():
    """An L whose outer top-right corner is rounded: not a rectangle, but its
    rounded corner is still a quarter circle tangent to both sides, and its
    sharp corners stay sharp."""
    png = png_of('<path d="M20 20 H60 A4 4 0 0 1 64 24 V64 H110 V110 H20 Z" fill="#1b4fd6"/>')
    svg = trace(png)
    d = re.search(r'<path d="([^"]+)" fill="#1b4fd6"', svg)
    assert d, svg[:600]
    radii = [float(r) for r in re.findall(r"A([\d.]+) ", d.group(1))]
    assert len(radii) == 1 and abs(radii[0] - 4.0) < 0.5, d.group(1)
    assert "C" not in d.group(1), d.group(1)


def test_sharp_tiles_stay_sharp():
    png = png_of('<rect x="20" y="20" width="40" height="40" fill="#e63946"/>'
                 '<rect x="60" y="20" width="40" height="40" fill="#457b9d"/>'
                 '<rect x="20" y="60" width="80" height="30" fill="#2a9d8f"/>')
    svg = trace(png)
    assert not rect_attrs(svg), rect_attrs(svg)
    assert " A" not in svg and not re.search(r"A[\d.]+ [\d.]+ 0", svg)


def blurred_png(body: str, sigma: float, size: int = 64, ss: int = 8) -> bytes:
    """`body` rendered 8x supersampled, blurred by a Gaussian of `sigma` px and
    averaged back to pixels: a soft source, as a downscaled or re-encoded logo is."""
    import io

    from PIL import Image
    from scipy import ndimage

    svg = f'<svg {NS} viewBox="0 0 {size} {size}"><rect width="{size}" height="{size}" fill="#fff"/>{body}</svg>'
    big = np.asarray(Image.open(io.BytesIO(bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size * ss, height=size * ss)))).convert("RGB")).astype(np.float64)
    big = ndimage.gaussian_filter(big, sigma=(sigma * ss, sigma * ss, 0), mode="nearest")
    small = big.reshape(size, ss, size, ss, 3).mean(axis=(1, 3))
    buf = io.BytesIO()
    Image.fromarray(np.clip(np.round(small), 0, 255).astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()


def test_the_blur_of_an_edge_is_read_across_the_sides():
    for sigma in (0.5, 0.7, 0.9):
        png = blurred_png('<rect x="20.25" y="20.4" width="24" height="24" fill="#0a8cf8"/>', sigma)
        rgb = rgba(png)[..., :3].astype(np.float32)
        m = rects.Model(20.25, 20.4, 44.25, 44.4, [0.0] * 4, [0.0] * 4, [np.zeros(0, np.int64)] * 4)
        got, n = rects.edge_sigma(rgb, m)
        assert abs(got - sigma) < 0.08 and n > 100, (sigma, got)


def test_a_blurred_sharp_square_stays_sharp():
    """Blur rounds the half-way contour a corner is placed on: a sharp corner
    under a 0.8 px blur reads as a 1.5-2 px radius, and without the correction
    came out as `rx="1.9"`."""
    for off in (0.0, 0.25):
        png = blurred_png(f'<rect x="{20 + off}" y="{20 + off}" width="24" height="24" fill="#0a8cf8"/>', 0.8)
        svg = trace(png)
        assert not rect_attrs(svg), (off, rect_attrs(svg))
        assert re.search(r'<rect [^>]*width="2[34](\.\d+)?"', svg), svg[:400]


def test_a_blurred_rounded_square_keeps_its_drawn_radius():
    # (at a half-pixel offset the blurred rim is taken into the fill as a
    # gradient and the box comes out a pixel wide, which is the fills' defect)
    for off in (0.0, 0.25):
        png = blurred_png(f'<rect x="{20 + off}" y="{20 + off}" width="24" height="24" rx="3" fill="#0a8cf8"/>', 0.7)
        got = rect_attrs(trace(png))
        assert len(got) == 1, (off, got)
        assert abs(got[0]["rx"] - 3.0) < 0.4, (off, got)


def test_two_rounded_corners_that_meet_at_a_node_mirror_each_other():
    """The foot of the Vexel mark: a bar and the square beside it, both rounded,
    their bottoms on one line, the band they sit on showing in the notch
    between their corners. The bar's corner is the square's, reflected, and the
    two meet at the node on their shared side."""
    png = png_of('<rect x="5" y="80" width="150" height="50" fill="#6fd3f5"/>'
                 '<path d="M20 20 H54 V97 A3 3 0 0 1 51 100 H10 Z" fill="#35b8f8"/>'
                 '<rect x="54" y="60" width="30" height="40" rx="3" fill="#0a5cc8"/>')
    svg = trace(png)
    bar = re.search(r'<path d="([^"]+)" fill="#3[0-9a-f]{5}"', svg)
    square = re.search(r'<(?:path d="([^"]+)"|rect [^>]*rx="([\d.]+)") fill="#0[0-9a-f]{5}"', svg)
    assert bar and square, svg[:900]
    sq_r = [float(r) for r in re.findall(r"A([\d.]+) ", square.group(1))] if square.group(1) else [float(square.group(2))]
    bar_r = [float(r) for r in re.findall(r"A([\d.]+) ", bar.group(1))]
    assert sq_r and max(sq_r) - min(sq_r) < 1e-9 and abs(sq_r[0] - 3.0) < 0.4, sq_r
    # the bar's corner at the node: an arc of the square's radius, not a hook of cubics
    assert any(abs(r - sq_r[0]) < 0.25 for r in bar_r), (bar_r, bar.group(1))
    assert seam_index(svg, rgba(png)) < 200.0
