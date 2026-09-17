from studi0trace.imaging.svg import normalize_dimensions, svg_stats

POTRACE_LIKE = (
    '<?xml version="1.0" standalone="no"?>\n'
    '<svg version="1.0" xmlns="http://www.w3.org/2000/svg"\n'
    ' width="48.000000pt" height="48.000000pt" viewBox="0 0 48.000000 48.000000"\n'
    ' preserveAspectRatio="xMidYMid meet">\n'
    '<g transform="translate(0,48) scale(0.1,-0.1)" fill="#000000" stroke="none">\n'
    '<path d="M160 320 l0 -160 160 0 160 0 0 160 0 160 -160 0 -160 0 0 -160z"/>\n'
    "</g>\n</svg>\n"
)


def test_strips_root_dimensions_and_sets_viewbox():
    out = normalize_dimensions(POTRACE_LIKE, 64, 64)
    root = out[out.index("<svg") : out.index(">", out.index("<svg")) + 1]
    assert 'width="' not in root and 'height="' not in root
    assert 'viewBox="0 0 64 64"' in root
    # the rest of the document is untouched
    assert 'transform="translate(0,48) scale(0.1,-0.1)"' in out


def test_adds_viewbox_when_missing():
    out = normalize_dimensions('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><g/></svg>', 10, 20)
    assert out.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 20">')


def test_nested_width_height_are_preserved():
    svg = '<svg width="5" height="5"><rect width="5" height="5" fill="red"/></svg>'
    out = normalize_dimensions(svg, 5, 5)
    assert '<rect width="5" height="5"' in out
    assert out.count('width="') == 1


def test_idempotent():
    once = normalize_dimensions(POTRACE_LIKE, 64, 64)
    assert normalize_dimensions(once, 64, 64) == once


def test_self_closing_root_survives():
    assert normalize_dimensions("<svg/>", 3, 4) == '<svg viewBox="0 0 3 4"/>'


def test_stats_counts_paths_nodes_gradients_and_fills():
    svg = (
        "<svg><defs>"
        '<linearGradient id="a"><stop/></linearGradient>'
        '<radialGradient id="b"><stop/></radialGradient>'
        "</defs>"
        '<path d="M0 0 L1 1 C1 2 3 4 5 6 Z" fill="#fff"/>'
        '<path d="m2 2h3v3z" fill="#FFF"/>'
        '<path d="M0 0" style="fill: red; stroke: none"/>'
        '<path d="M1 1" fill="none"/>'
        "</svg>"
    )
    stats = svg_stats(svg)
    assert stats.paths == 4
    assert stats.nodes == 5 + 2 + 1 + 1  # coordinate pairs per path
    assert stats.gradients == 2
    assert stats.unique_fills == 2  # #fff/#FFF collapse, red, "none" excluded
    assert stats.bytes == len(svg.encode())
    assert stats.as_dict()["paths"] == 4
