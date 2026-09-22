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
    for ang, fill in ((220.0, fills[1]), (220.0, fills[2]), (0.0, fills[0]), (0.0, fills[2])):
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
