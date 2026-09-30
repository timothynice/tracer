import io
import math
import re

import numpy as np
import pytest
import resvg_py
from PIL import Image

from studi0trace.engines.vexel.curves import CurveParams
from studi0trace.engines.vexel.strokes import is_thin, stroke_geometry, stroke_svg


def render(svg: str, w: int, h: int) -> np.ndarray:
    png = resvg_py.svg_to_bytes(svg_string=svg, width=w, height=h)
    return np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"))


def test_is_thin_distinguishes_lines_from_blobs():
    line = np.zeros((40, 40), bool)
    line[20:22, 4:36] = True
    blob = np.zeros((40, 40), bool)
    blob[10:30, 10:30] = True
    assert is_thin(line)
    assert not is_thin(blob)


def test_straight_line_width_and_geometry():
    mask = np.zeros((40, 60), bool)
    mask[19:21, 5:55] = True  # 2 px thick, 50 px long
    coverage = mask.astype(np.float32)
    s = stroke_geometry(mask, coverage)
    assert s is not None
    assert abs(s.width - 2.0) < 0.15
    assert len(s.polylines) == 1 and not s.closed[0]
    xy = s.polylines[0]
    assert abs(xy[:, 1].mean() - 20.0) < 0.6
    assert xy[:, 0].min() < 7 and xy[:, 0].max() > 53


def test_subpixel_coverage_gives_subpixel_width():
    mask = np.zeros((20, 60), bool)
    mask[10, 5:55] = True
    coverage = np.where(mask, 0.5, 0.0).astype(np.float32)  # half-covered pixels: a 0.5 px line
    s = stroke_geometry(mask, coverage)
    assert s is not None and abs(s.width - 0.5) < 0.1


def test_ring_becomes_a_closed_stroke_that_renders_like_the_source():
    size = 96
    src = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"><circle cx="48" cy="48" r="30" fill="none" stroke="#123456" stroke-width="2"/></svg>'
    img = render(src, size, size)
    alpha = img[..., 3].astype(np.float32) / 255.0
    mask = alpha > 0.05
    assert is_thin(mask)
    s = stroke_geometry(mask, alpha)
    assert s is not None
    assert abs(s.width - 2.0) < 0.25
    assert any(s.closed), "the ring centreline should be a closed loop"
    out_svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">{stroke_svg(s, "#123456", 1.0, CurveParams(), 2)}</svg>'
    out = render(out_svg, size, size)
    diff = np.abs(out[..., 3].astype(float) - img[..., 3].astype(float))
    assert diff.mean() < 8.0, diff.mean()
    assert 'fill="none"' in out_svg
    width = float(re.search(r'stroke-width="([^"]+)"', out_svg).group(1))
    assert abs(width - 2.0) < 0.25


def test_open_curve_stroke():
    size = 80
    src = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"><path d="M10 60 C 30 10, 50 10, 70 60" fill="none" stroke="#000" stroke-width="3" stroke-linecap="round"/></svg>'
    img = render(src, size, size)
    alpha = img[..., 3].astype(np.float32) / 255.0
    s = stroke_geometry(alpha > 0.05, alpha)
    assert s is not None and abs(s.width - 3.0) < 0.35
    assert not any(s.closed)
    svg = stroke_svg(s, "#000000", 1.0, CurveParams(), 2)
    assert svg.count("M") >= 1 and "Z" not in svg


def test_stroke_fidelity_separates_a_drawn_line_from_a_letterform():
    """The only test that told them apart.

    A letterform is thin, elongated and of consistent width — it passes every
    geometric test for being a stroke. What it fails is the reconstruction: a
    single constant-width centreline does not paint its terminals and joins.
    """
    from studi0trace.engines.vexel.strokes import stroke_fidelity

    # A clean horizontal bar: exactly what a centreline paints.
    line = np.zeros((40, 80), np.float64)
    line[19:22, 10:70] = 1.0
    st = stroke_geometry(line > 0.5, line)
    assert st is not None
    line_err = stroke_fidelity(st, line)

    # A serif-ended bar: same width along its length, but the ends flare, so a
    # constant-width centreline leaves error a plain line does not.
    glyph = np.zeros((40, 80), np.float64)
    glyph[19:22, 10:70] = 1.0
    glyph[14:27, 10:14] = 1.0
    glyph[14:27, 66:70] = 1.0
    st2 = stroke_geometry(glyph > 0.5, glyph)
    assert st2 is not None
    glyph_err = stroke_fidelity(st2, glyph)

    assert line_err < glyph_err, f"line {line_err:.3f} should reconstruct better than glyph {glyph_err:.3f}"


def test_the_gate_keeps_real_strokes_and_drops_mangled_ones():
    from studi0trace.engines.vexel.engine import VexelParams, trace_rgba

    # Two thin bars: stroke recovery should still fire at the default tolerance.
    img = np.zeros((64, 64, 4), np.uint8)
    img[..., :3] = 255
    img[..., 3] = 255
    img[30:33, 8:56, :3] = 0
    stroked = trace_rgba(img, VexelParams())
    assert 'stroke-width' in stroked
    # Turned all the way strict, nothing is allowed to become a stroke.
    filled = trace_rgba(img, VexelParams(stroke_tolerance=0.05))
    assert "stroke-width" not in filled


def _ring_mask(size: int = 48, r_out: float = 18.0, r_in: float = 16.0) -> np.ndarray:
    ys, xs = np.mgrid[0:size, 0:size].astype(float) + 0.5
    rr = np.hypot(xs - size / 2, ys - size / 2)
    return (rr <= r_out) & (rr >= r_in)


def test_medial_axis_is_deterministic():
    """skimage breaks thinning-order ties with an OS-seeded generator, so its
    skeleton of a two-pixel ring changes between calls; ours must not."""
    from studi0trace.engines.vexel.strokes import medial_axis

    m = _ring_mask()
    first = medial_axis(m)
    assert first.any()
    for _ in range(5):
        assert (medial_axis(m) == first).all()


def test_medial_axis_matches_skimage_on_its_own_example():
    from studi0trace.engines.vexel.strokes import medial_axis

    square = np.zeros((7, 7), bool)
    square[1:-1, 2:-2] = True
    expected = np.array([
        [0, 0, 0, 0, 0, 0, 0],
        [0, 0, 1, 0, 1, 0, 0],
        [0, 0, 0, 1, 0, 0, 0],
        [0, 0, 0, 1, 0, 0, 0],
        [0, 0, 0, 1, 0, 0, 0],
        [0, 0, 1, 0, 1, 0, 0],
        [0, 0, 0, 0, 0, 0, 0],
    ], bool)
    assert (medial_axis(square) == expected).all()


def test_medial_axis_agrees_with_the_rust_port():
    import pytest

    from studi0trace.engines.vexel import engine as vexel
    from studi0trace.engines.vexel.strokes import medial_axis

    if vexel._vexel_rs is None:
        pytest.skip("the vexel_rs extension is not built")
    for m in (_ring_mask(), _ring_mask(size=40, r_out=15.0, r_in=14.0)):
        h, w = m.shape
        rs = np.asarray(vexel._vexel_rs._medial_axis(m.astype(np.uint8).ravel().tolist(), h, w), dtype=bool).reshape(h, w)
        assert (medial_axis(m) == rs).all()


def _thin_ring(size: int = 64, radius: float = 22.0, width: float = 2.0) -> np.ndarray:
    """A two-pixel ring: every pixel ties with its neighbour across the ring on
    both distance and cornerness, so the thinning order alone decides which of
    the two rows the skeleton keeps."""
    ys, xs = np.mgrid[0:size, 0:size].astype(float) + 0.5
    r = np.hypot(ys - size / 2, xs - size / 2)
    return np.abs(r - radius) <= width / 2


def test_medial_axis_is_deterministic_and_the_rust_port_finds_the_same_one():
    """skimage breaks ties in the thinning order with an OS-seeded permutation;
    the engine hands it a fixed order instead (a hash of each pixel's raster
    index), and the Rust port sorts by the same key, so the two skeletons — and
    everything downstream of them — are one skeleton."""
    from studi0trace.engines.vexel.strokes import medial_axis

    mask = _thin_ring()
    first = medial_axis(mask)
    assert first.any()
    assert np.array_equal(first, medial_axis(mask))
    vexel_rs = pytest.importorskip("vexel_rs")
    h, w = mask.shape
    rust = np.asarray(vexel_rs._medial_axis(mask.astype(np.uint8).ravel().tolist(), h, w), bool).reshape(h, w)
    assert np.array_equal(first, rust)


def _subpixel_ring(size: int = 96, cx: float = 48.25, cy: float = 48.5, r: float = 30.0, width: float = 2.0) -> np.ndarray:
    src = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
           f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#123456" stroke-width="{width}"/></svg>')
    return render(src, size, size)[..., 3].astype(np.float64) / 255.0


@pytest.mark.parametrize("width", [1.0, 2.0])
def test_a_ring_off_the_pixel_lattice_is_a_faithful_stroke(width):
    """A drawn line is a constant-width centreline, so its fidelity is near
    zero — unless the centreline is read to whole pixels and the prediction
    rasterised to whole pixels, when a 2 px ring whose centre sits half a
    pixel off the lattice scores 0.19–0.25 against a gate of 0.2, and which
    side of the gate it lands on is decided by the skeleton's tie-break.
    The centreline is refined to the coverage's centroid across the stroke,
    and the prediction is the exact coverage of a band at that centreline."""
    from studi0trace.engines.vexel.strokes import stroke_fidelity

    cx, cy, r = 48.25, 48.5, 30.0
    alpha = _subpixel_ring(cx=cx, cy=cy, r=r, width=width)
    mask = alpha > 0.05
    s = stroke_geometry(mask, alpha)
    assert s is not None and any(s.closed)
    # the width the ink says: resvg draws a 1 px hairline at 0.85 coverage
    assert abs(s.width - alpha.sum() / (2 * math.pi * r)) < 0.1, s.width
    fid = stroke_fidelity(s, alpha)
    assert fid < 0.1, f"fidelity {fid:.3f}"
    ring = s.polylines[s.closed.index(True)]
    radii = np.hypot(ring[:, 0] - cx, ring[:, 1] - cy)
    assert np.sqrt(np.mean((radii - r) ** 2)) < 0.1, f"centreline wobbles {np.sqrt(np.mean((radii - r) ** 2)):.3f} px RMS"
    assert np.abs(radii - r).max() < 0.25, f"centreline wobbles {np.abs(radii - r).max():.3f} px"


def test_the_rust_stroke_stage_agrees_on_the_refined_centreline():
    from studi0trace.engines.vexel.strokes import stroke_fidelity

    vexel_rs = pytest.importorskip("vexel_rs")
    alpha = _subpixel_ring()
    mask = alpha > 0.05
    h, w = mask.shape
    py = stroke_geometry(mask, alpha)
    rs = vexel_rs._stroke_geometry(mask.astype(np.uint8).ravel().tolist(), alpha.ravel().tolist(), h, w)
    assert py is not None and rs is not None
    polys, closed, caps, width = rs
    assert list(closed) == py.closed and abs(width - py.width) < 1e-9
    for a, b in zip(py.polylines, polys):
        assert np.abs(a - np.asarray(b).reshape(-1, 2)).max() < 1e-9
    rs_fid = vexel_rs._stroke_fidelity(polys, list(closed), float(width), alpha.ravel().tolist(), h, w)
    assert abs(rs_fid - stroke_fidelity(py, alpha)) < 1e-9


def test_a_wide_ring_stroke_hugs_its_circle():
    """The emitted centreline, not just the polyline: cubics fitted through
    thin-mark's 150 px ring at the full curve tolerance sagged 0.14 px inside
    it, and a stroke's centreline error shows on both of its edges (16651 ppm
    of seam). The stroke is fitted at half the tolerance."""
    from tests.test_vexel_topology import sample_d

    size, cx, cy, r = 400, 200.25, 200.5, 150.0
    alpha = _subpixel_ring(size=size, cx=cx, cy=cy, r=r, width=2.0)
    s = stroke_geometry(alpha > 0.05, alpha)
    assert s is not None
    svg = stroke_svg(s, "#000", 1.0, CurveParams(tol=0.4), 2)
    radial = np.concatenate([np.hypot(*(sample_d(d, 60) - (cx, cy)).T) - r for d in re.findall(r'\bd="([^"]*)"', svg)])
    assert abs(radial.mean()) < 0.1 and np.sqrt(np.mean(radial ** 2)) < 0.13, (radial.mean(), np.sqrt(np.mean(radial ** 2)))
