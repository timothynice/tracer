"""The boundary graph, and the property the whole stage exists for: that the
shapes Vexel emits actually tile, leaving no hairline for the backdrop to show
through.

The regression these guard is a real one. Each region used to be traced on its
own, so the edge two regions share was described twice and the two descriptions
drifted apart by up to the fitting tolerance each way; the gap between them was
painted by neither. It scaled with `curve_tolerance`, which is a user-facing
parameter, so turning the quality knob one way made the output visibly worse.
"""
from __future__ import annotations

import io
import re

import numpy as np
import pytest
import resvg_py
from PIL import Image, ImageDraw

from bench.metrics import seam_index
from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
from studi0trace.engines.vexel.topology import Arc, _runs
from studi0trace.imaging.intake import load_upload
from tests.conftest import encode

LIMITS = dict(max_bytes=1 << 30, max_pixels=1 << 30)


def wedges(size: int = 96) -> bytes:
    """Three colours meeting at one point: a junction, and two sharp corners.

    The shape that used to go wrong — the boundary carries on through the point
    where the third region ends against it, so two arcs there are one curve.
    """
    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    mid = size // 2
    d.polygon([(0, 0), (mid, mid), (0, size)], fill=(10, 20, 120, 255))
    d.polygon([(0, 0), (mid, mid), (size, 0)], fill=(20, 120, 220, 255))
    d.polygon([(size, 0), (mid, mid), (size, size)], fill=(120, 200, 250, 255))
    return encode(img)


def trace(png: bytes, **params) -> str:
    image = load_upload(png, **LIMITS)
    return VexelEngine().trace(image, VexelParams(**params)).svg


def rgba(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"), dtype=np.uint8)


# A junction is the one place a shape cannot reach under its neighbour: the
# bleed has to come back to the node, or the ring tears open there. What is left
# is the triple point itself, a handful of sub-pixels, and it does not grow with
# the image. Anything above this is a seam along an edge, which is the defect.
A_FEW_SUBPIXELS_PPM = 200.0


@pytest.mark.parametrize("tolerance", [0.1, 0.4, 1.0, 2.0])
def test_shapes_tile_at_every_curve_tolerance(tolerance):
    """The defect this stage was built for, at both ends of the knob.

    Before the boundary became shared this rose with the tolerance — 397
    sub-pixel holes at 0.1 and 19502 at 2.0 on a 512 px logo — because each of
    the two regions either side of an edge fitted it separately and the fits
    were free to part by the tolerance each way.
    """
    png = wedges()
    seam = seam_index(trace(png, curve_tolerance=tolerance), rgba(png))
    assert seam < A_FEW_SUBPIXELS_PPM, f"{seam:.0f} ppm of the mark is not painted"


def test_a_shared_edge_is_described_once():
    """Both regions either side of an edge get the very same curve, reversed for
    one of them — not two fits of the same line, which is what used to leave a
    hairline between them."""
    ds = re.findall(r'\bd="([^"]*)"', trace(wedges()))
    assert len(ds) >= 2
    numbers = [set(re.findall(r"-?\d+\.?\d*", d)) for d in ds]
    pairs = [len(a & b) for i, a in enumerate(numbers) for b in numbers[i + 1 :]]
    assert max(pairs) >= 6, f"no two shapes share any geometry: {ds}"


def test_the_seam_metric_sees_a_shape_pulled_away_from_its_neighbour():
    """A guard on the guard: `seam_index` has to actually catch a gap, or every
    test above would pass on any output at all."""
    png = wedges()
    svg = trace(png)
    assert seam_index(svg, rgba(png)) < A_FEW_SUBPIXELS_PPM
    # Shrink one shape about the centre, opening a wedge along the edges it shares.
    torn = svg.replace("<path ", '<path transform="translate(48 48) scale(0.94) translate(-48 -48)" ', 1)
    assert seam_index(torn, rgba(png)) > 20 * A_FEW_SUBPIXELS_PPM


def test_transparent_holes_are_not_painted_in():
    """A shape reaches under the shapes painted over it so their edges land on
    ink. It must not reach under a transparent one, or the hole closes up."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([8, 8, size - 9, size - 9], fill=(200, 40, 40, 255))
    ImageDraw.Draw(img).ellipse([24, 24, size - 25, size - 25], fill=(0, 0, 0, 0))
    svg = trace(encode(img))
    png = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)
    out = np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"))
    assert out[size // 2, size // 2, 3] < 40, "the ring's hole was painted in"
    assert out[size // 2, 12, 3] > 200, "the ring itself went missing"


def test_runs_collapses_a_ring_that_starts_mid_arc():
    """A ring's walk can begin part-way along an arc and wrap onto its own start;
    that has to come back as one traversal of one arc, not two."""
    assert _runs([(4, 0), (4, 1), (4, 2), (4, 3)]) == [(4, False)]
    assert _runs([(4, 2), (4, 3), (4, 0), (4, 1)]) == [(4, False)]  # wrapped, still forwards
    assert _runs([(4, 3), (4, 2), (4, 1), (4, 0)]) == [(4, True)]
    assert _runs([(4, 1), (4, 0), (4, 3), (4, 2)]) == [(4, True)]  # wrapped, still backwards
    # A ring is a cycle, so which arc it is listed from is arbitrary: the walk
    # is rotated to begin where one arc gives way to the next.
    assert _runs([(1, 0), (1, 1), (2, 5), (2, 4)]) == [(2, True), (1, False)]


def test_arc_knows_whether_it_is_a_loop():
    loop = Arc(pair=(1, 2), pts=np.zeros((4, 2)), n0=None, n1=None)
    span = Arc(pair=(1, 2), pts=np.zeros((4, 2)), n0=7, n1=9)
    assert loop.closed and not span.closed
