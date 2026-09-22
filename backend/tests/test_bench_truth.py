"""The vector truth's corners, for measuring a trace's corners against."""
from __future__ import annotations

from bench.truth import corner_match, corners, emitted_corners


def svg(body: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">{body}</svg>'


def test_truth_corners_of_a_polygon_are_its_vertices():
    c = corners(svg('<polygon points="10,10 90,20 80,90" fill="#000"/>'))
    assert sorted(map(tuple, c)) == [(10, 10), (80, 90), (90, 20)]


def test_rounded_rects_circles_and_tangent_arc_joins_have_no_corners():
    assert len(corners(svg('<rect x="10" y="10" width="50" height="30" rx="4" fill="#000"/><circle cx="50" cy="50" r="9" fill="#000"/>'))) == 0
    assert len(corners(svg('<path d="M20 10 h60 a10 10 0 0 1 10 10 v60 a10 10 0 0 1 -10 10 h-60 a10 10 0 0 1 -10 -10 v-60 a10 10 0 0 1 10 -10 z" fill="#000"/>'))) == 0
    assert len(corners(svg('<rect x="10" y="10" width="50" height="30" fill="#000"/>'))) == 4
    assert len(corners(svg('<path d="M10 10 L90 10 L90 90 Z" fill="#000"/>'))) == 3
    assert len(corners(svg('<rect x="10" y="10" width="50" height="30" fill="#000" filter="url(#s)"/>'))) == 0, "a blurred shape is soft"


def test_emitted_corners_follow_a_use_to_its_definition_and_match_the_truth():
    out = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><rect x="0" y="0" width="10" height="10" id="u1"/></defs>'
           '<use href="#u1" fill="#000"/><use href="#u1" x="20" y="0" fill="#000"/><path d="M50 50L60 50A5 5 0 0 1 60 60Z" fill="#000"/></svg>')
    got = emitted_corners(out)
    # the line runs straight into the arc at (60, 50): a smooth join, not a corner
    assert len(got) == 8 + 2
    truth = svg('<rect x="0" y="0" width="10" height="10" fill="#000"/><rect x="20" y="0" width="10" height="10" fill="#000"/>'
                '<path d="M50 50L60 50A5 5 0 0 1 60 60Z" fill="#000"/>')
    m = corner_match(truth, out)
    assert m["corner_precision"] == 1.0 and m["corner_recall"] == 1.0 and m["corner_f1"] == 1.0
