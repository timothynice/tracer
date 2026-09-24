"""The artifact scorecard flags the defects a designer circles and nothing on clean drawings."""
from __future__ import annotations

import numpy as np

from bench.artifacts import parse, scorecard
from bench.raster import rasterize

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
BACK = '<path d="M0 0H64V64H0Z" fill="#ffffff"/>'


def card(body: str, size: int = 64, src: np.ndarray | None = None) -> dict:
    svg = HEAD + body + "</svg>"
    if src is None:
        src = np.full((size, size, 4), 255, np.uint8)
    return scorecard(svg, src, detail=True)


def test_clean_rounded_square_scores_zero():
    c = card(BACK + '<rect x="12" y="12" width="40" height="40" rx="4" fill="#0af"/>')
    assert c["pinholes"] == 0 and c["slivers"] == 0 and c["degenerate"] == 0
    assert c["wobble_deg_100px"] < 1.0
    assert c["inflections"] == 0
    assert c["rect_like"] == 2  # the square and the backdrop
    assert c["radius_inconsistent"] == 0 and c["rect_bowed"] == 0


def test_one_sharp_corner_on_a_rounded_square_is_inconsistent():
    d = "M16 12H48A4 4 0 0 1 52 16V52H16A4 4 0 0 1 12 48V16A4 4 0 0 1 16 12Z"  # bottom-right corner sharp
    c = card(BACK + f'<path d="{d}" fill="#0af"/>')
    assert c["rect_like"] == 2 and c["radius_inconsistent"] == 1


def test_pillow_square_is_bowed():
    d = "M14 12C26 11 38 11 50 12C53 12 52 13 52 14C53 26 53 38 52 50C52 53 51 52 50 52C38 53 26 53 14 52C11 52 12 51 12 50C11 38 11 26 12 14C12 11 13 12 14 12Z"
    c = card(BACK + f'<path d="{d}" fill="#0af"/>')
    assert c["rect_bowed"] == 1


def test_a_nick_in_a_straight_edge_is_wobble():
    clean = card(BACK + '<path d="M8 8H56V56H8Z" fill="#0af"/>')
    nicked = card(BACK + '<path d="M8 8H30L31 9.2L32 8H56V56H8Z" fill="#0af"/>')
    assert clean["wobble_deg_100px"] < 1.0
    assert nicked["wobble_deg_100px"] > 20.0


def test_a_straight_edge_drawn_as_an_s_is_an_inflection():
    clean = card(BACK + '<path d="M8 8H56V56H8Z" fill="#0af"/>')
    s = card(BACK + '<path d="M8 8C24 6.8 40 9.2 56 8V56H8Z" fill="#0af"/>')  # sags 0.35 px each way
    assert clean["inflections"] == 0
    assert s["inflections"] >= 1


def test_gap_between_shapes_is_a_pinhole():
    src = np.full((64, 64, 4), 255, np.uint8)
    body = '<path d="M0 0H32V64H0Z" fill="#000"/><path d="M32.6 0H64V64H32.6Z" fill="#000"/>'
    c = card(body, src=src)
    assert c["pinholes"] >= 1 and c["hole_subpx"] > 0
    touching = card('<path d="M0 0H32V64H0Z" fill="#000"/><path d="M31 0H64V64H31Z" fill="#000"/>', src=src)
    assert touching["pinholes"] == 0 and touching["hole_subpx"] == 0


def test_transparent_source_is_not_a_hole():
    src = np.zeros((64, 64, 4), np.uint8)
    src[16:48, 16:48, 3] = 255
    c = card('<rect x="16" y="16" width="32" height="32" fill="#000"/>', src=src)
    assert c["hole_subpx"] == 0


def test_slivers_and_degenerate_contours():
    c = card(BACK + '<path d="M10 10H40V10.4H10Z" fill="#123"/>'  # 30 x 0.4 px hairline
             '<path d="M20 30L22 32L20 30Z" fill="#123"/>'  # out and back: no area
             '<path d="M30 40H31.5V41.5H30Z" fill="#123"/>'  # 2.25 px² speck
             '<path d="M40 40L50 50" fill="none" stroke="#123" stroke-width="0.6"/>')
    assert c["slivers"] == 2
    assert c["degenerate"] == 1
    assert c["thin_strokes"] == 1


def test_root_scale_and_use_are_honoured():
    svg = (HEAD + '<defs><path id="u1" d="M0 0H8V8H0Z"/></defs><g transform="scale(0.5)">'
           '<use href="#u1" x="10" y="10" fill="#000"/><path d="M40 40H56V56H40Z" fill="#000"/></g></svg>')
    d = parse(svg, (64, 64))
    boxes = sorted((tuple(np.round(c.pts.min(axis=0), 3)), tuple(np.round(c.pts.max(axis=0), 3))) for c in d.contours)
    assert boxes == [((5.0, 5.0), (9.0, 9.0)), ((20.0, 20.0), (28.0, 28.0))]


def test_arcs_parse_as_circles():
    d = parse(HEAD + '<path d="M22 32A10 10 0 0 1 42 32A10 10 0 0 1 22 32Z" fill="#000"/></svg>', (64, 64))
    pts = d.contours[0].pts
    r = np.linalg.norm(pts - np.array([32.0, 32.0]), axis=1)
    assert np.allclose(r, 10.0, atol=1e-6)


def test_seam_index_sees_a_gap_inside_the_upsamplers_root_group():
    """The small-input upsampler wraps the whole drawing in one scaled <g>. The
    seam metric used to render element by element, found one element, and
    reported a perfect 0 for every upsampled trace."""
    from bench.metrics import seam_index

    src = np.full((64, 64, 4), 255, np.uint8)
    flat = HEAD + '<path d="M0 0H32V64H0Z" fill="#000"/><path d="M33 0H64V64H33Z" fill="#000"/></svg>'
    wrapped = (HEAD + '<g transform="scale(0.5)"><path d="M0 0H64V128H0Z" fill="#000"/>'
               '<path d="M66 0H128V128H66Z" fill="#000"/></g></svg>')
    assert seam_index(flat, src) > 1000
    assert seam_index(wrapped, src) > 1000


def test_render_of_clean_output_matches_parse():
    # the geometry we measure is the geometry resvg draws: area agrees to a pixel
    svg = HEAD + '<path d="M12 12H52V52H12Z" fill="#000"/><circle cx="32" cy="32" r="8" fill="#fff"/></svg>'
    alpha = rasterize(svg, 64, 64)[..., 3].astype(float) / 255
    assert abs(alpha.sum() - 1600.0) < 1.0
