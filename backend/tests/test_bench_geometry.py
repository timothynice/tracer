"""Geometry metrics against vector truth.

The colour metrics are blind to the defects a designer sees at 750 %: on a wedge
whose tip the engine cut 6.7 px short, fixing the tip moved mean ΔE from 0.018
to 0.009 and edge F1 the wrong way. These metrics compare edge sets rendered
at 8× against the corpus's exact SVG truth, so a tip, a bulge or a straight
edge drawn as a bowing cubic is a number, not an opinion.
"""
from __future__ import annotations

from bench.geometry import line_debt, outline_error

NS = 'xmlns="http://www.w3.org/2000/svg"'


def svg(body: str) -> str:
    return f'<svg {NS} viewBox="0 0 128 128"><rect width="128" height="128" fill="#fff"/>{body}</svg>'


def test_identical_geometry_has_zero_outline_error():
    truth = svg('<polygon points="20,20 100,30 90,110 30,100" fill="#36c"/>')
    m = outline_error(truth, truth, 128, 128)
    assert m["outline_px"] < 0.02 and m["outline_p99_px"] < 0.13


def test_a_shifted_edge_is_measured_in_source_pixels():
    truth = svg('<polygon points="20,20 100,20 100,100 20,100" fill="#36c"/>')
    out = svg('<polygon points="20,20 100,20 100,100.75 20,100.75" fill="#36c"/>')  # bottom edge moved 0.75 px
    m = outline_error(truth, out, 128, 128)
    assert 0.6 < m["outline_p99_px"] < 0.9
    assert 0.1 < m["outline_px"] < 0.3  # one of four edges moved


def test_junction_error_is_read_where_three_colours_meet():
    truth = svg('<polygon points="0,128 128,0 128,128" fill="#3b8ee8"/>'
                '<polygon points="40,88 128,0 100,0" fill="#bfe0ff"/>')
    blunt = svg('<polygon points="0,128 128,0 128,128" fill="#3b8ee8"/>'
                '<polygon points="46,82 128,0 100,0" fill="#bfe0ff"/>')  # tip cut 6 px short
    exact = outline_error(truth, truth, 128, 128)
    cut = outline_error(truth, blunt, 128, 128)
    assert exact["junction_px"] is not None and exact["junction_px"] < 0.15
    assert cut["junction_px"] > 0.4
    assert cut["junction_px"] > cut["outline_px"]  # the defect is local, and the metric says where


def test_line_debt_counts_cubics_that_should_have_been_lines():
    straight_as_cubic = svg('<path d="M10 10C40 10 70 10 100 10L100 100L10 100Z" fill="#36c"/>')
    m = line_debt(straight_as_cubic)
    assert m["line_debt_segments"] == 1 and 89 < m["line_debt_px"] < 91
    curved = svg('<path d="M10 10C40 40 70 40 100 10L100 100L10 100Z" fill="#36c"/>')
    assert line_debt(curved)["line_debt_segments"] == 0


def test_line_debt_is_none_for_relative_commands():
    assert line_debt(svg('<path d="m10 10c30 0 60 0 90 0z" fill="#36c"/>'))["line_debt_px"] is None
