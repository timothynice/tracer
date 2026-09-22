"""Task 9: rounded rectangles (and circular arcs) as primitives."""
from __future__ import annotations

import re

import numpy as np
import resvg_py

from studi0trace.engines.vexel.curves import CircArc, CurveParams, RoundedRect, arc_points, fit_arc_run, fit_closed, fit_shape, path_d, try_rounded_rect
from tests.test_vexel_topology import jpeg, trace


def rounded_square_png(size: int = 256, side: float = 120.0, rx: float = 18.0, fill: str = "#1b9c9c") -> bytes:
    x = (size - side) / 2.0
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"><rect width="{size}" height="{size}" fill="#fff"/>'
           f'<rect x="{x}" y="{x}" width="{side}" height="{side}" rx="{rx}" fill="{fill}"/></svg>')
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size))


def rounded_rect_poly(x0: float, y0: float, w: float, h: float, r: float, step: float = 0.7) -> np.ndarray:
    pts = []
    corners = ((x0 + w - r, y0 + r, -90.0), (x0 + w - r, y0 + h - r, 0.0), (x0 + r, y0 + h - r, 90.0), (x0 + r, y0 + r, 180.0))
    for k, (cx, cy, a0) in enumerate(corners):
        n = max(4, int(np.pi / 2 * r / step))
        for t in np.linspace(a0, a0 + 90.0, n):
            pts.append([cx + r * np.cos(np.radians(t)), cy + r * np.sin(np.radians(t))])
        ncx, ncy, na0 = corners[(k + 1) % 4]
        here = np.array(pts[-1])
        far = np.array([ncx + r * np.cos(np.radians(na0)), ncy + r * np.sin(np.radians(na0))])
        m = max(2, int(np.linalg.norm(far - here) / step))
        for f in np.linspace(0, 1, m)[1:-1]:
            pts.append(list(here + f * (far - here)))
    return np.array(pts)


def test_rounded_square_becomes_a_rect_with_rx():
    for src in (rounded_square_png(rx=18), jpeg(rounded_square_png(rx=18), quality=75)):
        svg = trace(src)
        m = re.search(r'<rect [^>]*rx="([\d.]+)"', svg)
        assert m, svg[:400]
        assert abs(float(m.group(1)) - 18.0) < 0.3, m.group(0)
        attrs = m.group(0)
        w = float(re.search(r'width="([\d.]+)"', attrs).group(1))
        assert abs(w - 120.0) < 0.3, attrs


def test_rounded_rect_fit_reads_the_geometry_and_refuses_impostors():
    p = CurveParams()
    poly = rounded_rect_poly(20.0, 30.0, 100.0, 60.0, 12.0)
    got = try_rounded_rect(poly, p)
    assert isinstance(got, RoundedRect)
    assert np.allclose([got.x, got.y, got.w, got.h, got.rx], [20.0, 30.0, 100.0, 60.0, 12.0], atol=0.15)
    assert isinstance(fit_shape([poly], p), RoundedRect)
    # unequal corner radii are not one rx
    a = rounded_rect_poly(0.0, 0.0, 80.0, 60.0, 8.0)
    b = rounded_rect_poly(0.0, 0.0, 80.0, 60.0, 20.0)
    mixed = np.vstack([a[: len(a) // 2], b[len(b) // 2 :]])
    assert try_rounded_rect(mixed, p) is None
    # a tilted one is a path
    ang = np.radians(20.0)
    rot = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
    assert try_rounded_rect(poly @ rot.T + 50.0, p) is None


def ring_sector_png(size: int = 256, r_outer: float = 60.0, r_inner: float = 40.0, sweep_deg: float = 120.0, fill: str = "#1b9c9c") -> bytes:
    import math

    cx = cy = size / 2.0
    a0, a1 = math.radians(-60.0), math.radians(-60.0 + sweep_deg)
    pt = lambda r, a: f"{cx + r * math.cos(a):.4f},{cy + r * math.sin(a):.4f}"
    large = 1 if sweep_deg > 180 else 0
    d = (f"M{pt(r_outer, a0)} A{r_outer},{r_outer} 0 {large} 1 {pt(r_outer, a1)} "
         f"L{pt(r_inner, a1)} A{r_inner},{r_inner} 0 {large} 0 {pt(r_inner, a0)} Z")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"><rect width="{size}" height="{size}" fill="#fff"/>'
           f'<path d="{d}" fill="{fill}"/></svg>')
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size))


def _d_of(svg: str, fill: str) -> str:
    for attrs in re.findall(r"<path ([^>]*)/>", svg):
        if f'fill="{fill}"' in attrs:
            return re.search(r'\bd="([^"]*)"', attrs).group(1)
    raise AssertionError(f"no path with fill {fill} in {svg[:300]}")


def test_a_ring_segment_is_emitted_as_two_arcs_and_two_lines():
    d = _d_of(trace(ring_sector_png()), "#1b9c9c")
    cmds = "".join(c for c in d if c in "MLCAZ")
    assert cmds.count("A") == 2 and cmds.count("C") == 0 and cmds.count("L") + cmds.count("Z") >= 2, d
    radii = sorted(float(m.group(1)) for m in re.finditer(r"A([\d.]+) ", d))
    assert abs(radii[0] - 40.0) < 0.3 and abs(radii[1] - 60.0) < 0.3, radii


def test_arc_fit_reads_radius_sweep_and_size_and_refuses_a_shallow_or_uneven_run():
    t = np.radians(np.linspace(20.0, 250.0, 200))
    pts = np.column_stack([100 + 30 * np.cos(t), 80 + 30 * np.sin(t)])
    arcs = fit_arc_run(pts, 0.4)
    assert arcs is not None and len(arcs) == 2, "a 230° sweep is two arcs of one circle: a near half circle's centre is ill-conditioned"
    assert all(isinstance(a, CircArc) and abs(a.r - 30.0) < 0.05 and not a.large and a.sweep for a in arcs)
    assert np.allclose(arcs[0].p1, arcs[1].p0) and np.allclose(arcs[0].p0, pts[0]) and np.allclose(arcs[1].p1, pts[-1])
    back = fit_arc_run(pts[::-1], 0.4)
    assert back is not None and all(not a.sweep for a in back)
    drawn = np.vstack([arc_points(a, 2000) for a in arcs])
    from scipy.spatial import cKDTree
    assert cKDTree(drawn).query(pts)[0].max() < 0.05, "the arcs drawn from their file form are the arc that was fitted"
    # a pinned tangent 6° off the circle's says this is not the circle
    t0 = np.array([np.cos(np.radians(20 + 90 + 6)), np.sin(np.radians(20 + 90 + 6))])
    assert fit_arc_run(pts, 0.4, t_start=t0) is None
    # an 8° sliver of a circle is a flat cubic's business
    t = np.radians(np.linspace(0.0, 8.0, 40))
    assert fit_arc_run(np.column_stack([100 * np.cos(t), 100 * np.sin(t)]), 0.4) is None
    # an ellipse is not a circle
    t = np.radians(np.linspace(0.0, 120.0, 200))
    assert fit_arc_run(np.column_stack([40 * np.cos(t), 25 * np.sin(t)]), 0.4) is None


def test_a_full_circle_survives_two_decimal_output():
    """Two half arcs with chord = diameter put the implied centre half a pixel
    off once the ends are rounded to two decimals; arcs of 120° do not."""
    import re as _re

    t = np.linspace(0, 2 * np.pi, 208, endpoint=False)
    poly = np.column_stack([64.0103 + 26.4856 * np.cos(t), 63.9976 + 26.4856 * np.sin(t)])
    segs = fit_closed(poly, 0.4)
    assert len(segs) == 3 and all(isinstance(s, CircArc) for s in segs), "a full circle is three arcs of 120°"
    d = path_d([segs], 2)
    cur = None
    drawn = []
    for cmd, body in _re.findall(r"([MLCAZ])([^MLCAZ]*)", d):
        v = [float(x) for x in _re.findall(r"-?\d*\.?\d+", body)]
        if cmd == "M":
            cur = np.array(v[:2])
        elif cmd == "A":
            end = np.array(v[5:7])
            drawn.append(arc_points(CircArc(cur, end, v[0], v[3] != 0, v[4] != 0), 100))
            cur = end
    drawn = np.vstack(drawn)
    radial = np.abs(np.hypot(drawn[:, 0] - 64.0103, drawn[:, 1] - 63.9976) - 26.4856)
    assert radial.max() < 0.02, radial.max()
