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
from studi0trace.engines.vexel.curves import CurveParams
from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
from studi0trace.engines.vexel.fills import FitParams, Solid, fit_fill
from studi0trace.engines.vexel.merge import MergeParams, merge_regions
from studi0trace.engines.vexel.partition import discontinuity, initial_labels
from studi0trace.engines.vexel.prepare import prepare
from studi0trace.engines.vexel.weights import interior_weights
from studi0trace.engines.vexel.topology import Arc, _extend_wedges, _mix_share, _runs
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


def cut_off_wedge(size: int = 64, over: int = 8, start: float = 2.4, taper: float = 0.04):
    """A pale wedge narrowing to nothing between white and blue, supersampled.

    Returns (rgb, labels, wedge label). The labels are each pixel's majority
    owner, which is the best a hard partition can do and is exactly where the
    defect comes from: below a pixel wide there is no pixel to give the wedge, so
    the label stops while the ink carries on. Here the label runs out at row 48
    and the artwork reaches row 58.
    """
    white, pale, blue = (
        np.array([255.0, 255.0, 255.0, 255.0]),
        np.array([150.0, 215.0, 245.0, 255.0]),
        np.array([20.0, 60.0, 200.0, 255.0]),
    )
    cover = np.zeros((size, size, 3))
    for r in range(size * over):
        y = (r + 0.5) / over
        half = max(0.0, start - taper * y)
        for c in range(size * over):
            x = (c + 0.5) / over
            which = 0 if x < size / 2 - half else (1 if x < size / 2 + half else 2)
            cover[r // over, c // over, which] += 1
    cover /= over * over
    rgb = cover[..., 0:1] * white + cover[..., 1:2] * pale + cover[..., 2:3] * blue
    labels = (np.argmax(cover, axis=2) + 1).astype(np.int32)
    fills = {1: Solid(rgba=white), 2: Solid(rgba=pale), 3: Solid(rgba=blue)}
    return rgb[..., :3], labels, fills, cover[..., 1]


def test_a_region_cut_off_at_a_point_is_handed_its_sliver_back():
    """The defect: a hard partition truncates an acute wedge, and the trace then
    shows a blunt cut where the artwork has a long fine taper."""
    rgb, labels, fills, truth = cut_off_wedge()
    label_reach = int(np.nonzero((labels == 2).any(axis=1))[0].max())
    ink_reach = int(np.nonzero(truth.sum(axis=1) > 0.02)[0].max())
    assert ink_reach > label_reach + 5, "the fixture is not truncating the wedge"

    padded = np.pad(labels.astype(np.int64), 1, constant_values=0)
    out, _ = _extend_wedges(
        padded, rgb, np.ones(labels.shape),
        lambda lab, qx, qy: fills[lab].evaluate(qx, qy),
        CurveParams(corner_threshold=60.0, tol=0.4, shape_fitting=True),
    )
    gained = np.argwhere(out != padded)
    assert len(gained) >= 3, "the wedge was left cut off"
    assert {int(out[r, c]) for r, c in gained} == {2}, "something other than the wedge was moved"
    reached = max(int(r) - 1 for r, _ in gained)
    assert label_reach < reached <= ink_reach, f"reached row {reached}, ink runs to {ink_reach}"


def test_the_sliver_stays_four_connected():
    """`_directed_rings` breaks a diagonal touch the four-connected way, so a
    chain that only meets at the corners comes back as one-pixel islands."""
    rgb, labels, fills, _ = cut_off_wedge()
    padded = np.pad(labels.astype(np.int64), 1, constant_values=0)
    out, _ = _extend_wedges(
        padded, rgb, np.ones(labels.shape),
        lambda lab, qx, qy: fills[lab].evaluate(qx, qy),
        CurveParams(corner_threshold=60.0, tol=0.4, shape_fitting=True),
    )
    from scipy import ndimage

    before = ndimage.label(padded == 2, np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))[1]
    after = ndimage.label(out == 2, np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))[1]
    assert after == before == 1, f"the wedge went from {before} four-connected pieces to {after}"


def test_mix_share_reads_the_third_ink_out_of_a_mixture():
    black = np.array([[0.0, 0.0, 0.0, 255.0]])
    white = np.array([[255.0, 255.0, 255.0, 255.0]])
    red = np.array([[255.0, 0.0, 0.0, 255.0]])
    assert _mix_share(red, red, black, white)[0] == 1.0
    assert _mix_share(black, red, black, white)[0] == 0.0
    assert 0.4 < _mix_share((red + white) / 2, red, black, white)[0] < 0.6


def test_nothing_is_handed_back_where_no_region_was_cut_off():
    """Two regions meeting cleanly must be left alone: the extension only fires
    where a third region's ink actually runs between them."""
    size = 48
    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    ImageDraw.Draw(img).rectangle([0, 0, size // 2, size], fill=(20, 60, 200, 255))
    rgba_in = np.asarray(Image.open(io.BytesIO(encode(img))).convert("RGBA"), dtype=np.uint8)

    prep = prepare(rgba_in)
    grad = discontinuity(prep.features)
    labels = merge_regions(
        initial_labels(grad, prep.features, min_region=6), prep.features,
        MergeParams(detail=6.0, gradients=True), grad,
    )
    ys, xs = np.mgrid[0:size, 0:size]
    xs = xs.astype(float) + 0.5
    ys = ys.astype(float) + 0.5
    rgba255 = np.concatenate([prep.rgb, (prep.alpha * 255.0)[..., None]], axis=-1)
    fills = {}
    for lab in (int(i) for i in np.unique(labels) if i):
        m = labels == lab
        fills[lab] = fit_fill(xs[m], ys[m], rgba255[m], FitParams(gradients=True, max_stops=4, tol=3.0),
                              weights=interior_weights(m))
    padded = np.pad(labels.astype(np.int64), 1, constant_values=0)
    out, _ = _extend_wedges(padded, prep.rgb, prep.alpha,
                         lambda lab, qx, qy: fills[lab].evaluate(qx, qy),
                         CurveParams(corner_threshold=60.0, tol=0.4, shape_fitting=True))
    assert int((out != padded).sum()) == 0
