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
from pathlib import Path

import numpy as np
import pytest
import resvg_py
from PIL import Image, ImageDraw

from bench.geometry import _arc_points
from bench.metrics import seam_index
from studi0trace.engines.vexel.curves import CurveParams
from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
from studi0trace.engines.vexel.fills import FitParams, Solid, fit_fill
from studi0trace.engines.vexel.merge import MergeParams, merge_regions
from studi0trace.engines.vexel.partition import discontinuity, initial_labels
from studi0trace.engines.vexel.prepare import prepare
from studi0trace.engines.vexel.weights import interior_weights
from studi0trace.engines.vexel.topology import Arc, _extend_wedges, _mix_share, _runs, _unfold
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
    # The side painted earlier draws the copy bled under its neighbour, so what
    # the two literally share are the nodes at the ends of their common arc.
    assert max(pairs) >= 4, f"no two shapes share any geometry: {ds}"


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


def test_a_placed_outline_may_not_double_back():
    """Two boundaries sharing a pixel can each reach past the other, which puts
    the vertices out of order along the arc — and a fit reads that as a curve
    that turns back and returns, which is the hitch it looks like."""
    straight = np.array([[0.0, float(y)] for y in range(8)])
    folded = straight.copy()
    folded[4] = [0.0, 2.6]  # reached back past its neighbour
    fixed = _unfold(folded)
    assert fixed[4][1] > fixed[3][1], "the vertex is still behind the one before it"
    assert np.allclose(_unfold(straight), straight), "a straight run was disturbed"


def test_a_corner_is_not_mistaken_for_a_fold():
    """A corner is sharp at every scale; a vertex that reached past its
    neighbour is sharp only against them. Recovering a hard corner pushes the
    vertices beside it outwards, and that must survive."""
    corner = np.array([[0.0, 4.0], [0.0, 3.0], [0.0, 2.0], [0.0, 1.0], [0.0, 0.0],
                       [1.0, 0.0], [2.0, 0.0], [3.0, 0.0], [4.0, 0.0]])
    assert np.allclose(_unfold(corner), corner)


# --- junctions placed from their arcs ----------------------------------------------


def synthetic_wedge(size: int = 256, tip: tuple[float, float] = (75.0, 181.0), opening: float = 15.0):
    """Blue below x + y = size, a pale wedge with its tip on that edge, rendered by
    resvg. The shape in the 750 % screenshot: two straight sides closing at a
    shallow angle onto a boundary that carries on. Returns (png, tip, opening)."""
    import math

    top = tip[1] / math.tan(math.radians(45 + opening))
    # The pale shape is painted first and reaches under the blue, so no two
    # shapes share an edge: a shared edge is anti-aliased twice by the renderer
    # and its pixels become a three-colour mix that pulls the placed edge over.
    steep = (math.cos(math.radians(-(45 + opening))), math.sin(math.radians(-(45 + opening))))
    under = (tip[0] - 6.0 * steep[0], tip[1] - 6.0 * steep[1])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
           f'<rect width="{size}" height="{size}" fill="#fff"/>'
           f'<polygon points="{under[0]:.3f},{under[1]:.3f} {tip[0] + top:.3f},0 {size},0 {size},8" fill="#bfe0ff"/>'
           f'<polygon points="0,{size} {size},0 {size},{size}" fill="#3b8ee8"/></svg>')
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)), np.array(tip), opening


def path_points(svg: str, fill: str) -> np.ndarray:
    """Every coordinate pair written in the path with this fill: anchors and controls."""
    for attrs in re.findall(r"<path ([^>]*)/>", svg):
        if re.search(r'fill="%s"' % fill, attrs):
            d = re.search(r'\bd="([^"]*)"', attrs).group(1)
            break
    else:
        raise AssertionError(f"no path with fill {fill}")
    out = []
    for cmd, body in re.findall(r"([MLCAZ])([^MLCAZ]*)", d):
        v = [float(x) for x in re.findall(r"-?\d*\.?\d+", body)]
        if cmd == "A":
            out.append(v[5:7])  # the arc's own numbers are not coordinates
        else:
            out.extend([v[k:k + 2] for k in range(0, len(v) - 1, 2)])
    return np.array(out)


def sample_path(svg: str, fill: str, per_segment: int = 60) -> np.ndarray:
    """Points along the curve of the path with this fill (absolute M/L/C/Z)."""
    for attrs in re.findall(r"<path ([^>]*)/>", svg):
        if re.search(r'fill="%s"' % fill, attrs):
            d = re.search(r'\bd="([^"]*)"', attrs).group(1)
            break
    else:
        raise AssertionError(f"no path with fill {fill}")
    t = np.linspace(0.0, 1.0, per_segment)[:, None]
    out, cur, start = [], None, None
    for cmd, body in re.findall(r"([MLCAZ])([^MLCAZ]*)", d):
        v = [float(x) for x in re.findall(r"-?\d*\.?\d+", body)]
        if cmd == "M":
            cur = start = np.array(v[:2])
        elif cmd == "L":
            p = np.array(v[:2]); out.append(cur * (1 - t) + p * t); cur = p
        elif cmd == "A":
            p = np.array(v[5:7]); out.append(_arc_points(cur, p, v[0], v[3] != 0, v[4] != 0, per_segment)); cur = p
        elif cmd == "C":
            c1, c2, p = np.array(v[0:2]), np.array(v[2:4]), np.array(v[4:6])
            out.append((1 - t) ** 3 * cur + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * p); cur = p
        elif cmd == "Z" and cur is not None and start is not None:
            out.append(cur * (1 - t) + start * t); cur = start
    return np.vstack(out)


def path_anchors(svg: str, fill: str) -> np.ndarray:
    """Segment end points only (no control points) of the path with this fill."""
    for attrs in re.findall(r"<path ([^>]*)/>", svg):
        if re.search(r'fill="%s"' % fill, attrs):
            d = re.search(r'\bd="([^"]*)"', attrs).group(1)
            break
    else:
        raise AssertionError(f"no path with fill {fill}")
    out = []
    for cmd, body in re.findall(r"([MLCAZ])([^MLCAZ]*)", d):
        v = [float(x) for x in re.findall(r"-?\d*\.?\d+", body)]
        if cmd in "ML":
            out.append(v[:2])
        elif cmd == "C":
            out.append(v[4:6])
        elif cmd == "A":
            out.append(v[5:7])
    return np.array(out)


def test_a_shallow_wedge_tip_is_placed_where_its_two_sides_cross():
    """Two lines meeting at 15 degrees pin their crossing to a fraction of a
    pixel along the bisector. The node used to stay on the raster chamfer
    whenever the incident lines were within ~32 degrees of parallel, which is
    exactly the shallow tip a designer zooms in on; it landed 1.4 px short."""
    png, tip, _ = synthetic_wedge()
    nearest = np.linalg.norm(path_anchors(trace(png), "#bfe0ff") - tip, axis=1).min()
    # Two lines at 15 degrees: a tenth of a degree of lean in either is a
    # quarter pixel of tip. This is the placement noise talking.
    assert nearest < 0.4, f"tip landed {nearest:.2f} px from the true tip"


def test_a_shape_cut_by_the_canvas_edge_is_not_a_wedge_tip():
    """The wedge's upper corner sits on the canvas top. Two of its arcs meet
    there at 60 degrees, which read as a region closing to a point, and the
    steep side was then pinned to the border's tangent and flared a pixel."""
    import math

    png, tip, opening = synthetic_wedge()
    pts = sample_path(trace(png), "#bfe0ff")
    ang = math.radians(-(45 + opening))                 # the steep side, tip -> canvas top
    u = np.array([math.cos(ang), math.sin(ang)])
    rel = pts - tip
    along, off = rel @ u, rel @ np.array([-u[1], u[0]])
    top = tip[1] / math.sin(-ang)                       # arc length from the tip to the canvas top
    near_border = (along > top - 8.0) & (along <= top + 0.5) & (np.abs(off) < 3.0)
    assert near_border.any()
    assert np.abs(off[near_border]).max() < 0.15, f"steep side leaves its line by {np.abs(off[near_border]).max():.2f} px at the border"


def test_node_estimate_recovers_a_shallow_crossing_and_declines_a_parallel_one():
    import math

    from studi0trace.engines.vexel.topology import _node_estimate

    d1 = np.array([1.0, 0.0])
    a = math.radians(15.0)
    d2 = np.array([math.cos(a), math.sin(a)])
    lines = [(np.array([0.0, 20.0]), d1, 0.0), (np.array([10.0, 20.0]) - 5.0 * d2, d2, 0.0)]
    assert np.allclose(_node_estimate(lines, np.array([9.0, 20.5]), 4.0), [10.0, 20.0], atol=1e-9)
    a5 = math.radians(5.0)
    d3 = np.array([math.cos(a5), math.sin(a5)])
    lines5 = [(np.array([0.0, 20.0]), d1, 0.0), (np.array([10.0, 20.0]) - 5.0 * d3, d3, 0.0)]
    assert np.array_equal(_node_estimate(lines5, np.array([9.0, 20.5]), 4.0), [9.0, 20.5])


# --- the approach window at a node ---------------------------------------------------


def tilted_square_png(angle: float, size: int = 256, side: float = 120.0, fill: str = "#1b9c9c") -> bytes:
    import math

    r = math.radians(angle)
    h = side / 2.0
    pts = [(size / 2 + x * math.cos(r) - y * math.sin(r), size / 2 + x * math.sin(r) + y * math.cos(r))
           for x, y in ((-h, -h), (h, -h), (h, h), (-h, h))]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"><rect width="{size}" height="{size}" fill="#fff"/>'
           f'<polygon points="{" ".join(f"{x:.4f},{y:.4f}" for x, y in pts)}" fill="{fill}"/></svg>')
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size))


def own_line_flare(pts: np.ndarray, node: np.ndarray, u: np.ndarray, other: np.ndarray,
                   near: float = 6.0, far: float = 30.0) -> float:
    """How far the curve within `near` px of `node` leaves the straight line
    through its own run `near`..`far` px out along direction `u` — the black
    line a designer draws to say where the edge should have gone. Samples are
    assigned to this side when they lie closer in angle to `u` than to `other`,
    the wedge's other side, so a 15 degree opening does not mix the two."""
    rel = pts - node
    r = np.linalg.norm(rel, axis=1)
    ok = r > 0.3
    cos_u = (rel @ u) / np.maximum(r, 1e-9)
    cos_o = (rel @ other) / np.maximum(r, 1e-9)
    mine = ok & (cos_u > cos_o) & (cos_u > 0.9)
    s, off = rel @ u, rel @ np.array([-u[1], u[0]])
    clean = pts[mine & (s > near) & (s < far)]
    c = clean.mean(axis=0)
    _, _, vt = np.linalg.svd(clean - c)
    n = np.array([-vt[0][1], vt[0][0]])
    close = pts[mine & (s <= near)]
    assert len(close) and len(clean) >= 8
    return float(np.abs((close - c) @ n).max())


def test_a_wedge_side_runs_straight_into_its_tip():
    """The bulge in the 750 % screenshot. The vertices next to a node are placed
    from pixels that mix three fills, and the fit followed them faithfully."""
    import math

    png, tip, opening = synthetic_wedge()
    svg = trace(png)
    pts = sample_path(svg, "#bfe0ff", per_segment=200)
    anchors = path_anchors(svg, "#bfe0ff")
    node = anchors[np.argmin(np.linalg.norm(anchors - tip, axis=1))]
    sides = {ang: np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))]) for ang in (-(45 + opening), -45)}
    for ang, u in sides.items():
        other = [v for a, v in sides.items() if a != ang][0]
        flare = own_line_flare(pts, node, u, other)
        assert flare < 0.15, f"side at {ang} deg flares {flare:.2f} px into the tip"


def test_a_tilted_square_stays_four_lines():
    for ang in (5, 25, 38, 45, 50):
        svg = trace(tilted_square_png(ang))
        d = re.search(r'\bd="([^"]*)"', re.search(r'<path ([^>]*fill="#1b9c9c"[^>]*)/>', svg).group(1)).group(1)
        assert d.count("C") == 0 and d.count("L") == 4, f"{ang} deg: {d[:90]}"


# --- smooth continuation is a fit, not an angle ----------------------------------------


def pie_png(size: int = 256, angles: tuple[float, float, float] = (0.0, 100.0, 220.0)) -> tuple[bytes, np.ndarray, tuple]:
    """Three sectors meeting at the centre with straight radial edges at these
    angles. The turns between neighbouring edges are 80, 60 and 40 degrees, so
    no pair continues smoothly — yet the 40 degree pair used to be pinned to one
    tangent because 40 <= corner_threshold, and both edges hooked at the centre."""
    import math

    c = size / 2.0
    fills = ("#e24b4b", "#2f8f4e", "#3f6bd6")
    body = ""
    for k in range(3):
        a0, a1 = math.radians(angles[k]), math.radians(angles[(k + 1) % 3] + (360.0 if k == 2 else 0.0))
        steps = [a0 + (a1 - a0) * t for t in np.linspace(0.0, 1.0, 40)]
        pts = [(c, c)] + [(c + 400 * math.cos(a), c + 400 * math.sin(a)) for a in steps]
        body += f'<polygon points="{" ".join(f"{x:.3f},{y:.3f}" for x, y in pts)}" fill="{fills[k]}"/>'
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">{body}</svg>'
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)), np.array([c, c]), fills


def test_radial_edges_meeting_at_forty_degrees_are_a_corner_not_a_curve():
    import math

    png, centre, fills = pie_png()
    svg = trace(png)
    # The pair that turns 40 degrees is the 220 and 0 degree edges; those are
    # the ones that were pinned. (The 100 degree edges wait for the line-first
    # fit: their placement wobbles a fifth of a pixel and the chord test bows them.)
    # The sector painted first lies under both others at the centre, so its
    # outline runs on a pixel past the node under them (two bled copies joined,
    # rather than each pinned back to the point): nothing of it is seen there.
    bottom = min(fills, key=svg.index)
    for ang, fill in ((220.0, fills[1]), (220.0, fills[2]), (0.0, fills[0]), (0.0, fills[2])):
        if fill == bottom:
            continue
        u = np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
        pts = sample_path(svg, fill, per_segment=200)
        anchors = path_anchors(svg, fill)
        node = anchors[np.argmin(np.linalg.norm(anchors - centre, axis=1))]
        assert np.linalg.norm(node - centre) < 0.5, f"centre node landed {np.linalg.norm(node - centre):.2f} px off"
        # the other radial edge of this sector, to keep its samples out
        others = [a for a in (0.0, 100.0, 220.0) if a != ang]
        other = min(others, key=lambda a: abs(((a - ang) + 180) % 360 - 180))
        v = np.array([math.cos(math.radians(other)), math.sin(math.radians(other))])
        flare = own_line_flare(pts, node, u, v)
        assert flare < 0.15, f"edge at {ang} deg of {fill} hooks {flare:.2f} px at the centre"


def circle_on_split_png(size: int = 128, r: float = 15.0) -> tuple[bytes, np.ndarray, float]:
    """A small disc over a two-colour background: its outline is one smooth
    circle although the boundary graph cuts it at two nodes."""
    c = size / 2.0
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
           f'<rect width="{size}" height="{size}" fill="#fff"/><rect width="{c}" height="{size}" fill="#3b8ee8"/>'
           f'<circle cx="{c + 3}" cy="{c}" r="{r}" fill="#1f2a44"/></svg>')
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)), np.array([c + 3, c]), r


def test_a_small_circle_crossed_by_a_boundary_stays_smooth():
    png, centre, r = circle_on_split_png()
    svg = trace(png)
    prim = re.search(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)"[^>]*fill="#1f2a44"', svg)
    if prim:  # whole-shape fitting found the circle: as smooth as it gets
        assert abs(float(prim.group(3)) - r) < 0.35
        return
    pts = sample_path(svg, "#1f2a44", per_segment=100)
    radial = np.abs(np.linalg.norm(pts - centre, axis=1) - r)
    assert radial.max() < 0.35, f"the disc's outline leaves its circle by {radial.max():.2f} px"


def test_a_node_on_the_canvas_edge_stays_on_the_edge():
    """The wedge's upper corner is on the canvas top: its node must have y == 0
    exactly, or the shape stops short of the frame and leaves a hairline."""
    png, tip, opening = synthetic_wedge()
    anchors = path_anchors(trace(png), "#bfe0ff")
    top = anchors[anchors[:, 1] < 0.5]
    assert len(top) >= 2 and np.all(top[:, 1] == 0.0), top
    assert anchors[:, 1].min() == 0.0 and anchors[:, 0].max() == 256.0, "the shape must reach the frame exactly"


def jpeg(png: bytes, quality: int = 75) -> bytes:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality)
    return out.getvalue()


def downsampled(png: bytes) -> bytes:
    """Rendered at 2x by the caller? No: resized 2x up and back down, a different
    anti-aliasing kernel than the renderer's."""
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    w, h = im.size
    lo = im.resize((2 * w, 2 * h), Image.Resampling.BICUBIC).resize((w, h), Image.Resampling.BILINEAR)
    out = io.BytesIO()
    lo.save(out, "PNG")
    return out.getvalue()


def nearest_path_d(svg: str, fill: str) -> str:
    """The `d` of the path whose fill is closest to `fill`: JPEG moves a colour a step."""
    want = np.array([int(fill[i:i + 2], 16) for i in (1, 3, 5)])
    best = None
    for attrs in re.findall(r"<path ([^>]*)/>", svg):
        m = re.search(r'fill="#([0-9a-fA-F]{6})"', attrs)
        if not m:
            continue
        got = np.array([int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)])
        dist = float(np.linalg.norm(got - want))
        if best is None or dist < best[0]:
            best = (dist, re.search(r'\bd="([^"]*)"', attrs).group(1))
    assert best is not None, "no filled path"
    return best[1]


def test_a_tilted_square_is_four_lines_under_jpeg_and_resampling():
    for degrade in (jpeg, downsampled):
        for ang in (5, 25, 38, 45, 50):
            d = nearest_path_d(trace(degrade(tilted_square_png(ang))), "#1b9c9c")
            assert d.count("C") == 0 and d.count("L") == 4, f"{degrade.__name__} {ang} deg: {d[:90]}"


# --- pinholes: where shapes that tile still leave a dot of backdrop ----------

from pathlib import Path

from bench.artifacts import scorecard
from studi0trace.engines.presets import all_presets
from studi0trace.engines.vexel import engine as vexel_engine
from studi0trace.engines.vexel import topology
from studi0trace.engines.vexel.curves import Line

CORPUS = Path(__file__).resolve().parents[1] / "bench" / "corpus"


def overlapping_polygons_png(size: int = 256, background: str | None = None) -> bytes:
    """Five opaque polygons, each laid partly over the one before, on transparency
    (so no backdrop can be painted under the artwork and hide a gap)."""
    import math

    s = size / 512
    body = ""
    for k, (colour, n, rot) in enumerate(zip(("#1d3557", "#e63946", "#2a9d8f", "#f4a261", "#457b9d"),
                                             (3, 5, 4, 6, 3), (0.3, 1.1, 0.7, 0.2, 2.0))):
        cx, cy, r = (200 + 30 * k) * s, (220 + 25 * k) * s, 140 * s
        pts = " ".join(f"{cx + r * math.cos(rot + 2 * math.pi * i / n):.2f},{cy + r * math.sin(rot + 2 * math.pi * i / n):.2f}"
                       for i in range(n))
        body += f'<polygon points="{pts}" fill="{colour}"/>'
    rect = f'<rect width="{size}" height="{size}" fill="{background}"/>' if background else ""
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">{rect}{body}</svg>'
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size))


def test_overlapping_polygons_leave_no_pinhole_at_any_corner():
    """Every corner where one polygon crosses another's edge is a junction of
    three regions. The copy of an edge bled under the later shape used to be an
    offset of the placed vertices, not of the curve drawn; where the fit had
    left the vertices by more than the bleed (every such corner) the copy
    crossed back over the drawn edge and a dot there was painted by nothing:
    three pinholes on this image."""
    png = overlapping_polygons_png()
    card = scorecard(trace(png), rgba(png))
    assert card["pinholes"] == 0 and card["hole_clusters"] == 0, card


@pytest.mark.parametrize("preset", ["balanced", "logo"])
def test_the_wordmark_v_junction_leaves_no_pinhole(preset):
    """The circled defect: where the dark arm, the white wedge and the bright
    ribbon of the Vexel wordmark meet, and along the ribbon's upper edge. The
    crop touches its own frame on every side, so no backdrop is laid under it:
    whatever tiles here tiles by its own edges (one to three pinholes at HEAD)."""
    src = Image.open(CORPUS / "real" / "logo" / "vexel-wordmark-512.png").convert("RGBA").crop((200, 170, 300, 270))
    png = encode(src)
    params = {p.id: p.params for p in all_presets()}[preset]
    card = scorecard(trace(png, **params), rgba(png))
    assert card["pinholes"] == 0 and card["hole_clusters"] == 0, card


def test_a_backdrop_is_laid_under_the_artwork_not_cut_around_it():
    """A shape whose holes hold only opaque shapes painted after it is painted
    whole, and they go on top, as a designer builds it. Cut, each hole's outline
    was a second copy of the shapes inside it, bled to meet them, and its flaws
    were pinholes (three on this image at HEAD, eight at 512 px)."""
    png = overlapping_polygons_png(background="#ffffff")
    svg = trace(png)
    first = re.search(r"<(path|rect|circle|ellipse)\b[^>]*/>", svg).group(0)
    assert first.startswith("<rect") or first.count("M") == 1, f"the backdrop still has holes cut: {first[:120]}"
    card = scorecard(svg, rgba(png))
    assert card["pinholes"] == 0 and card["hole_clusters"] == 0, card


def _straight_arc(y: float = 10.0) -> Arc:
    pts = np.column_stack([np.arange(0.0, 21.0), np.full(21, y)])
    arc = Arc(pair=(1, 2), pts=pts, normal=np.tile([0.0, 1.0], (21, 1)), n0=0, n1=1)
    arc.segments = [Line(pts[0].copy(), pts[-1].copy())]
    return arc


def _copy_points(segs) -> np.ndarray:
    return np.vstack([np.vstack([s.p0, s.p1]) for s in segs])


def test_the_bled_copy_stops_halfway_across_a_thin_later_shape():
    """Bled one pixel into a shape half a pixel thick, the copy would poke out of
    its far side into a shape painted before the painter, and show there as a
    band of the wrong colour. It reaches halfway instead."""
    arc = _straight_arc()
    wall = np.column_stack([np.arange(-2.0, 23.0), np.full(25, 10.5)])
    segs, jog = topology._under(arc, 1.0, CurveParams(), [wall])
    pts = _copy_points(segs)
    assert jog == (True, True)
    assert pts[:, 1].max() <= 10.5 - 0.1, f"the copy crossed the far side: {pts[:, 1].max():.2f}"
    assert pts[:, 1].max() >= 10.2, "the copy did not bleed at all"


def test_the_bled_copy_does_not_reach_where_a_shapes_edges_have_crossed():
    """A sliver fitted thinner than nothing — its far edge lies behind the edge
    being bled — has no inside to reach into: the copy stays on the edge there
    (the white bumps on the flat preset's "e" counter)."""
    arc = _straight_arc()
    behind = np.column_stack([np.arange(6.0, 15.0), np.full(9, 9.7)])
    segs, _jog = topology._under(arc, 1.0, CurveParams(), [behind])
    probe, _t = topology._sample(segs, 0.25)
    mid = probe[(probe[:, 0] > 7.5) & (probe[:, 0] < 13.5)]
    assert len(mid) and mid[:, 1].max() <= 10.0 + 0.3, f"the copy reached into a crossed sliver: {mid[:, 1].max():.2f}"


# Silverpeak badge, arc (26, 138): five cubics joined smoothly. The Rust engine
# has the same copy to the last bits (`under_tests` in vexel-rs/src/topology.rs).
SILVERPEAK_ARC = [
    ([600.6582867801136, 381.58124894983234], [599.3829822291585, 383.34094833479594],
     [597.8606221538046, 386.2687495606855], [595.5, 386.92069397275225]),
    ([595.5, 386.92069397275225], [593.8630926138252, 387.3727665849522],
     [592.1336850978171, 386.68693044693777], [590.5, 386.980469877491]),
    ([590.5, 386.980469877491], [589.410881879614, 387.17616188281715],
     [587.2672882785189, 388.4513126638197], [587.0, 388.5]),
    ([587.0, 388.5], [586.7744474487237, 388.5410850522558],
     [586.656932260966, 387.8605751794979], [586.5, 388.02771045517073]),
    ([586.5, 388.02771045517073], [585.4884668520069, 389.10500884175764],
     [584.9976496271961, 390.5978658107586], [584.0035466733675, 391.6912689570962]),
]
SILVERPEAK_COPY_ENDS = [(599.849, 380.994), (595.729, 385.769), (590.61, 385.953), (587.483, 387.159),
                        (585.771, 387.343), (583.264, 391.019), (584.004, 391.691)]


def test_a_bled_copy_does_not_turn_on_the_last_bit_of_a_smooth_join():
    """Each smooth join is sampled from both of its cubics, and the two offsets
    land a rounding error apart (4e-14 here). Handed to the fit as two vertices,
    they made its splits depend on that last bit, which the two engines'
    Bezier evaluations do not share: the Rust copy of this arc came out split
    2 px from the Python's. The second sample is put exactly on the first."""
    import math

    from studi0trace.engines.vexel.curves import Cubic

    segs = [Cubic(*(np.array(p, dtype=float) for p in s)) for s in SILVERPEAK_ARC]
    pts = np.array([s.p0 for s in segs] + [segs[-1].p1])
    arc = Arc(pair=(26, 138), pts=pts, normal=np.tile([-0.6, -0.8], (len(pts), 1)), n0=0, n1=1)
    arc.segments = segs
    loose = CurveParams(corner_threshold=60.0, tol=0.6, shape_fitting=True, kind_tol=math.inf)
    copy, jog = topology._under(arc, 1.0, loose, [])
    assert jog == (True, True)
    assert "".join(type(s).__name__[0] for s in copy) == "LCCCCCL"
    ends = np.array([s.p1 for s in copy])
    assert np.abs(ends - np.array(SILVERPEAK_COPY_ENDS)).max() < 2e-3, np.round(ends, 3).tolist()


def test_every_ring_is_one_unbroken_curve(monkeypatch):
    """An arc too short to carry both of its nodes (the end cap of a stem on the
    canvas edge) has no segments; the ring used to jump the gap, and written as
    a path the next segment then started from the wrong point — a stem drawn as
    a wedge on the Studi0Mail logo."""
    captured = {}
    build = topology.build

    def spy(*args, **kwargs):
        captured["bnd"] = bnd = build(*args, **kwargs)
        return bnd

    monkeypatch.setattr(vexel_engine.topology, "build", spy)
    # the spy sits on the Python build; the Rust twin is `ring_bridges_an_arc_with_no_segments`
    monkeypatch.setenv("VEXEL_BACKEND", "python")
    png = (CORPUS / "real" / "logo" / "studi0mail-logo-dark.png").read_bytes()
    trace(png)
    bnd = captured["bnd"]
    labels = sorted({lab for arc in bnd.arcs for lab in arc.pair if lab != 0})
    for lab in labels:
        for ring in bnd.rings(frozenset([lab])):
            segs = bnd.segments(ring, frozenset([lab]))
            gaps = [float(np.linalg.norm(b.p0 - a.p1)) for a, b in zip(segs, segs[1:])]
            assert not gaps or max(gaps) < 1e-6, f"label {lab}: the ring jumps {max(gaps):.2f} px"


def test_a_region_drawn_as_a_stroke_has_paint_under_it():
    """A thin region is drawn as a line of one width along its middle, which
    cannot follow its outline, and nothing bleeds into it (the line would not
    hide the bleed). The earliest neighbour painted before the line fills it
    underneath; without that, the canvas showed along both sides of every line
    on this image (an 815 sub-pixel hole; 28 pinholes at HEAD, from bleeds that
    reached through the line and showed)."""
    png = (CORPUS / "synthetic" / "logo" / "wedge-fan-128.png").read_bytes()
    card = scorecard(trace(png), rgba(png))
    assert card["pinholes"] == 0 and card["hole_subpx"] < 40, card


# --- placement against the colours actually either side of an edge ---------------


def shaded_edge_png(band: float = 0.35, size: int = 160) -> tuple[bytes, np.ndarray, np.ndarray]:
    """Two blues sixteen levels apart meeting on a straight line, the darker one
    lightening towards the edge — the wordmark's ribbon fold, where the fitted
    gradient of the darker region was a dozen levels off along the edge. The
    lighter region is painted underneath, so the edge is anti-aliased once.
    Returns (png, a point on the line, its unit normal into the lighter side)."""
    p0, p1 = np.array([40.3, 150.0]), np.array([112.7, 10.0])
    d = (p1 - p0) / np.linalg.norm(p1 - p0)
    n = np.array([-d[1], d[0]])
    n = n if n[0] > 0 else -n
    k = (p1[0] - p0[0]) / (p1[1] - p0[1])
    xt, xb = p1[0] - k * p1[1], p0[0] + k * (size - p0[1])
    mid = (p0 + p1) / 2.0
    fade = mid - 10.0 * n
    poly = f"0,0 {xt:.4f},0 {xb:.4f},{size} 0,{size}"
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"><defs>'
           f'<linearGradient id="b" gradientUnits="userSpaceOnUse" x1="0" y1="{size}" x2="{size}" y2="0">'
           f'<stop offset="0" stop-color="#0a92fc"/><stop offset="1" stop-color="#16a6fd"/></linearGradient>'
           f'<linearGradient id="a" gradientUnits="userSpaceOnUse" x1="{p0[0]}" y1="{size}" x2="{p1[0]}" y2="0">'
           f'<stop offset="0" stop-color="#0060f0"/><stop offset="0.5" stop-color="#0478f8"/><stop offset="1" stop-color="#0884fe"/></linearGradient>'
           f'<linearGradient id="h" gradientUnits="userSpaceOnUse" x1="{mid[0]:.3f}" y1="{mid[1]:.3f}" x2="{fade[0]:.3f}" y2="{fade[1]:.3f}">'
           f'<stop offset="0" stop-color="#40b0ff" stop-opacity="{band}"/><stop offset="1" stop-color="#40b0ff" stop-opacity="0"/></linearGradient>'
           f'</defs><rect width="{size}" height="{size}" fill="url(#b)"/>'
           f'<polygon points="{poly}" fill="url(#a)"/><polygon points="{poly}" fill="url(#h)"/></svg>')
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)), p0, n


def sample_d(d: str, per_segment: int = 200) -> np.ndarray:
    t = np.linspace(0.0, 1.0, per_segment)[:, None]
    out, cur, start = [], None, None
    for cmd, body in re.findall(r"([MLCAZ])([^MLCAZ]*)", d):
        v = [float(x) for x in re.findall(r"-?\d*\.?\d+", body)]
        if cmd == "M":
            cur = start = np.array(v[:2])
        elif cmd == "L":
            p = np.array(v[:2]); out.append(cur * (1 - t) + p * t); cur = p
        elif cmd == "A":
            p = np.array(v[5:7]); out.append(_arc_points(cur, p, v[0], v[3] != 0, v[4] != 0, per_segment)); cur = p
        elif cmd == "C":
            c1, c2, p = np.array(v[0:2]), np.array(v[2:4]), np.array(v[4:6])
            out.append((1 - t) ** 3 * cur + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * p); cur = p
        elif cmd == "Z" and cur is not None:
            out.append(cur * (1 - t) + start * t); cur = start
    return np.vstack(out)


@pytest.mark.parametrize("preset", [{}, {"detail": 10.0, "min_region": 16, "curve_tolerance": 0.6, "corner_threshold": 70.0},
                                    {"detail": 3.5, "min_region": 3, "max_stops": 6, "curve_tolerance": 0.25},
                                    {"detail": 14.0, "min_region": 24, "strokes": False, "overlaps": False, "shadows": False}])
def test_a_straight_edge_between_two_shaded_regions_stays_straight(preset):
    """The wavy boundary at the wordmark's V. The darker region's one gradient
    missed its own colour along the edge by about half the contrast across it,
    so its pure pixels projected to a coverage near a half, the crossing
    wandered from pixel to pixel and the fit followed: 50-200 segments and
    0.85 px of wobble on a straight line. Read against the colours the region
    actually has beside the edge, the line is one line."""
    png, p0, n = shaded_edge_png()
    svg = trace(png, **preset)
    ds = [re.search(r'\bd="([^"]*)"', a).group(1) for a in re.findall(r"<path ([^>]*)/>", svg)]
    top = ds[-1]  # the darker shape paints last: its outline is the edge you see
    pts = sample_d(top)
    off = (pts - p0) @ n
    near = (np.abs(off) < 3.0) & (pts[:, 1] > 8.0) & (pts[:, 1] < 152.0)
    assert near.sum() > 20
    assert np.abs(off[near]).max() < 0.3, f"edge leaves its line by {np.abs(off[near]).max():.2f} px"
    assert top.count("C") == 0, f"a straight edge came out as curves: {top[:120]}"


def test_local_fills_read_the_colour_beside_the_edge():
    """A region whose fitted fill is 20 levels dark: next to its edge the local
    fill is the pixels' own colour, and where the model is right it stands."""
    from studi0trace.engines.vexel.topology import _local_fills

    labels = np.ones((20, 20), np.int32)
    labels[:, 10:] = 2
    rgb = np.zeros((20, 20, 3))
    rgb[:, :10] = (20.0, 120.0, 250.0)
    rgb[:, 10:] = (10.0, 150.0, 250.0)
    alpha = np.ones((20, 20))
    model = {1: Solid(np.array([20.0, 100.0, 250.0, 255.0])), 2: Solid(np.array([10.0, 150.0, 250.0, 255.0]))}
    local = _local_fills(labels, rgb, alpha, lambda lab, qx, qy: model[lab].evaluate(qx, qy))
    at_edge = local(1, np.array([9.5]), np.array([10.5]))[0]
    assert abs(at_edge[1] - 120.0) < 3.0, at_edge  # the support term keeps back ~10 % beside the edge
    assert np.allclose(local(2, np.array([10.5]), np.array([10.5]))[0], [10.0, 150.0, 250.0, 255.0], atol=1e-6)


def test_a_wedge_tip_does_not_step_back_inside_its_own_sliver():
    """At 25 degrees the two sides' lines crossed 1.1 px short of the tip the
    handed-back sliver reaches, and the tip was put there: the sliver's last
    vertices stood beyond the node, and the fit turned back from them onto it —
    the hook at the wordmark's cyan tip. The tip may move across the wedge or
    further out, not back into it, and it sits on the boundary that carries on."""
    png, tip, _ = synthetic_wedge(opening=25.0)
    nearest = np.linalg.norm(path_anchors(trace(png), "#bfe0ff") - tip, axis=1).min()
    assert nearest < 0.4, f"tip landed {nearest:.2f} px from the true tip"


def test_a_vertex_with_no_crossing_in_reach_follows_its_neighbours():
    """One pixel labelled white holds blue ink, so all four samples along its
    step read blue and there is no half to cross; it stood at the label edge,
    0.8 px out of line with both neighbours, which found the edge — a spike."""
    from studi0trace.engines.vexel import topology

    h, w = 9, 8
    labels = np.ones((h, w), np.int32)
    labels[:, 4:] = 2
    white, blue = np.array([250.0, 250.0, 250.0]), np.array([10.0, 100.0, 240.0])
    rgb = np.where((np.arange(w) < 4)[None, :, None], white, blue) * np.ones((h, w, 3))
    rgb[:, 3] = 0.25 * white + 0.75 * blue      # the edge sits a quarter into column 3
    rgb[4, 3] = blue                             # ...but this one pixel reads pure blue
    rgb[4, 2] = 0.3 * white + 0.7 * blue         # and the one before it mostly blue too
    alpha = np.ones((h, w))
    fills = {1: Solid(np.append(white, 255.0)), 2: Solid(np.append(blue, 255.0))}
    bnd = topology.build(labels, rgb, alpha, lambda lab, qx, qy: fills[lab].evaluate(qx, qy), CurveParams(), extend=False)
    arc = next(a for a in bnd.arcs if a.pair == (1, 2))
    row = {int(round(float(y) - 0.5)): float(x) for x, y in arc.pts if abs((y - 0.5) - round(y - 0.5)) < 1e-9}
    assert abs(row[4] - 0.5 * (row[3] + row[5])) < 0.05, row


WORDMARK = Path(__file__).resolve().parents[1] / "bench" / "corpus" / "real" / "logo" / "vexel-wordmark-512.png"


@pytest.mark.skipif(not WORDMARK.exists(), reason="bench corpus not present")
def test_a_held_tip_leaves_its_curved_side_free_and_the_straight_edge_beyond_it_straight():
    """The wordmark's cyan band closes onto the ribbon, whose top edge curves
    into the tip and runs straight for 40 px beyond the curve. Held at its
    sliver, the tip was given the side's approach line as a tangent, 10 degrees
    off the curve's own direction there; the curve into the tip took three
    cubics, lines-first lost to four plain ones, and the straight top came out
    an S (0.45 px of wave in Cutfile and Dense). Left free, the side is the
    curve plus its line again."""
    from PIL import Image

    x0, y0 = 150, 100
    buf = io.BytesIO()
    Image.open(WORDMARK).convert("RGBA").crop((x0, y0, x0 + 200, y0 + 200)).save(buf, "PNG")
    cutfile = {"shadows": False, "strokes": False, "layering": "cutout", "detail": 10.0}
    dense = {"detail": 14.0, "min_region": 24, "strokes": False, "overlaps": False, "shadows": False}
    for preset in (cutfile, dense):
        svg = trace(buf.getvalue(), **preset)
        waves = []
        for d in re.findall(r'\sd="([^"]*)"', svg):
            pts = sample_d(d, 100)
            top = (pts[:, 0] > 295 - x0) & (pts[:, 0] < 328 - x0) & (np.abs(pts[:, 1] - (200 - y0)) < 1.5)
            if top.sum() > 50:
                waves.append(float(np.ptp(pts[top, 1])))
        assert waves and max(waves) < 0.1, (preset, waves)
