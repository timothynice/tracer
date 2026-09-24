"""The engine seam has two implementations behind it.

`vexel_rs` is the Rust one and is what runs in production; the Python pipeline
is the reference it was ported from and the fallback when the extension is not
built. These tests pin the switch and check that the two agree on what they
produce — not byte for byte (the numerics differ in the last bits, and
`tools/diffcheck.py` measures that properly) but on the shapes, the primitives
and the colours a caller would notice.
"""
from __future__ import annotations

import re

import numpy as np
import pytest

from studi0trace.engines.vexel import engine as vexel
from studi0trace.engines.vexel.engine import VexelParams, backend, trace_rgba

pytestmark = pytest.mark.skipif(vexel._vexel_rs is None, reason="the vexel_rs extension is not built")


def _rust(rgba: np.ndarray, params: VexelParams) -> str:
    height, width = rgba.shape[:2]
    return vexel._vexel_rs.trace(np.ascontiguousarray(rgba).tobytes(), width, height, params.model_dump())


def _disc(size: int = 64, radius: float = 20.0, rgb=(0xE6, 0x3E, 0x62)) -> np.ndarray:
    ys, xs = np.mgrid[0:size, 0:size].astype(float)
    # supersample the edge so the disc is anti-aliased like real artwork
    cover = np.zeros((size, size))
    for dy in (0.125, 0.375, 0.625, 0.875):
        for dx in (0.125, 0.375, 0.625, 0.875):
            cover += (np.hypot(ys + dy - size / 2, xs + dx - size / 2) <= radius)
    out = np.zeros((size, size, 4), np.uint8)
    out[..., :3] = rgb
    out[..., 3] = np.round(cover / 16 * 255).astype(np.uint8)
    return out


def _elements(svg: str) -> list[str]:
    return re.findall(r"<(path|circle|ellipse|rect)\b", svg)


def _colours(svg: str) -> list[str]:
    return re.findall(r"#[0-9a-f]{6}", svg)


def test_backend_selection_follows_the_environment(monkeypatch):
    monkeypatch.setenv("VEXEL_BACKEND", "python")
    assert backend() == "python"
    monkeypatch.setenv("VEXEL_BACKEND", "rust")
    assert backend() == "rust"
    monkeypatch.delenv("VEXEL_BACKEND")
    assert backend() == "rust", "the extension is installed, so it is the default"


def test_unknown_backend_value_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("VEXEL_BACKEND", "banana")
    assert backend() == "rust"


def test_asking_for_rust_without_the_extension_is_an_error(monkeypatch):
    monkeypatch.setattr(vexel, "_vexel_rs", None)
    monkeypatch.setenv("VEXEL_BACKEND", "rust")
    with pytest.raises(RuntimeError, match="not installed"):
        backend()


def test_the_app_refuses_to_start_on_the_fallback_when_rust_is_required(monkeypatch):
    """The deployment sets VEXEL_BACKEND=rust. If the extension did not make it
    into the image the container must fail to come up, not serve every trace ten
    times slower while passing its health check."""
    from studi0trace.main import create_app

    monkeypatch.setattr(vexel, "_vexel_rs", None)
    monkeypatch.setenv("VEXEL_BACKEND", "rust")
    with pytest.raises(RuntimeError, match="not installed"):
        create_app()


def test_the_app_starts_on_the_fallback_when_rust_is_not_required(monkeypatch):
    from studi0trace.main import create_app

    monkeypatch.setattr(vexel, "_vexel_rs", None)
    monkeypatch.delenv("VEXEL_BACKEND", raising=False)
    assert create_app() is not None


def test_both_backends_fit_the_same_primitive_to_a_disc():
    rgba = _disc()
    params = VexelParams()
    py, rs = trace_rgba(rgba, params), _rust(rgba, params)
    assert _elements(py) == _elements(rs) == ["circle"]
    assert _colours(py) == _colours(rs)


def test_both_backends_agree_on_a_flat_two_colour_mark():
    rgba = np.zeros((48, 48, 4), np.uint8)
    rgba[..., 3] = 255
    rgba[..., :3] = (0xF1, 0xFA, 0xEE)
    rgba[12:36, 8:40, :3] = (0x45, 0x7B, 0x9D)
    params = VexelParams()
    py, rs = trace_rgba(rgba, params), _rust(rgba, params)
    assert _elements(py) == _elements(rs)
    assert _colours(py) == _colours(rs)


def test_both_backends_reconstruct_a_ramp_as_one_gradient():
    size = 64
    rgba = np.zeros((size, size, 4), np.uint8)
    t = np.linspace(0.0, 1.0, size)[None, :]
    rgba[..., 0] = (20 + 210 * t).astype(np.uint8)
    rgba[..., 1] = 40
    rgba[..., 2] = (230 - 200 * t).astype(np.uint8)
    rgba[..., 3] = 255
    params = VexelParams()
    for svg in (trace_rgba(rgba, params), _rust(rgba, params)):
        assert svg.count("<linearGradient") == 1
        assert len(_elements(svg)) == 1


def _ring(size: int = 64, r_out: float = 22.0, r_in: float = 20.0) -> np.ndarray:
    """A two-pixel ring: a thin region whose skeleton has a tie at every pixel."""
    ys, xs = np.mgrid[0:size, 0:size].astype(float)
    cover = np.zeros((size, size))
    for dy in (0.125, 0.375, 0.625, 0.875):
        for dx in (0.125, 0.375, 0.625, 0.875):
            rr = np.hypot(ys + dy - size / 2, xs + dx - size / 2)
            cover += (rr <= r_out) & (rr >= r_in)
    out = np.zeros((size, size, 4), np.uint8)
    out[..., :3] = (0x20, 0x2B, 0x45)
    out[..., 3] = np.round(cover / 16 * 255).astype(np.uint8)
    return out


def test_both_backends_are_deterministic():
    """skimage's `medial_axis` breaks ties with an OS-seeded generator; the
    stroke stage uses its own deterministic thinning order instead, so a trace
    is reproducible in either engine."""
    rgba = _ring()
    params = VexelParams()
    assert _rust(rgba, params) == _rust(rgba, params)
    assert trace_rgba(rgba, params) == trace_rgba(rgba, params)


def test_both_backends_stroke_a_thin_ring_the_same_way():
    rgba = _ring()
    params = VexelParams()
    py, rs = trace_rgba(rgba, params), _rust(rgba, params)
    assert ("stroke=" in py) == ("stroke=" in rs)
    assert _elements(py) == _elements(rs)


def test_the_rust_backend_rejects_a_mis_sized_buffer():
    rgba = _disc(size=32)
    with pytest.raises(ValueError, match="width x height"):
        vexel._vexel_rs.trace(rgba.tobytes(), 33, 32, VexelParams().model_dump())


def test_parameters_reach_the_rust_backend():
    rgba = _disc()
    assert "<circle" in _rust(rgba, VexelParams())
    assert "<circle" not in _rust(rgba, VexelParams(shape_fitting=False))
    assert "Gradient" not in _rust(rgba, VexelParams(gradients=False))


def test_both_backends_stroke_the_thin_ring_of_thin_mark_128():
    """The red ring of `logo/thin-mark-128` is a 1.5 px line. Stroke recovery
    turns it into one stroked path only when the centreline's fidelity clears
    `stroke_tolerance`, and the centreline is the medial axis — so the two
    implementations have to thin in the same order, or one strokes the ring
    and the other paints it as four filled fragments."""
    from pathlib import Path

    from PIL import Image

    png = Path(__file__).resolve().parent.parent / "bench" / "corpus" / "synthetic" / "logo" / "thin-mark-128.png"
    rgba = np.asarray(Image.open(png).convert("RGBA"), dtype=np.uint8)
    # the stroke stage is what is under test: at 128 px this item would be
    # traced at 2x (its ring is under 2.2 px wide), where the ring is a fill
    params = VexelParams(upsample="never")
    py, rs = trace_rgba(rgba, params), _rust(rgba, params)
    ring = re.compile(r'<path[^>]*fill="none"[^>]*stroke="#ef476f"[^>]*stroke-width="([^"]+)"')
    py_ring, rs_ring = ring.search(py), ring.search(rs)
    assert py_ring is not None, "the Python engine should stroke the ring"
    assert rs_ring is not None, "the Rust engine should stroke the ring"
    assert py_ring.group(1) == rs_ring.group(1)
    assert _elements(py) == _elements(rs)


def _sharpened_glyph(size: int = 128) -> np.ndarray:
    """A dark rounded glyph with a counter on white, downsampled with Lanczos
    and unsharp-masked: the ringing band a pixel or two inside every edge that
    AI-generated and resampled logos carry (`logo/vexel-wordmark-512`)."""
    from PIL import Image, ImageDraw, ImageFilter

    big = Image.new("RGB", (size * 4, size * 4), (255, 255, 255))
    d = ImageDraw.Draw(big)
    d.rounded_rectangle([88, 120, 424, 392], radius=56, fill=(8, 10, 20))
    d.rounded_rectangle([184, 208, 328, 304], radius=24, fill=(255, 255, 255))
    img = big.resize((size, size), Image.LANCZOS).filter(ImageFilter.UnsharpMask(radius=1.2, percent=250, threshold=0))
    return np.asarray(img.convert("RGBA"), dtype=np.uint8)


def _shapes(svg: str) -> int:
    return len(re.findall(r"<(path|circle|ellipse|rect|use)\b", svg))


@pytest.mark.parametrize("engine", ["python", "rust"])
def test_lower_detail_does_not_turn_edge_ringing_into_slivers(engine):
    """Detail only ever adds real regions: the ringing inside a sharpened edge
    is the edge's rendering, and at a low `detail` it used to be rescued as a
    ring of slivers along the glyph."""
    rgba = _sharpened_glyph()
    run = trace_rgba if engine == "python" else _rust
    fine = run(rgba, VexelParams(detail=2.0, min_region=3))
    assert _shapes(fine) == _shapes(run(rgba, VexelParams())) == 3, fine
    assert 'fill="none"' not in fine


def _rim_stops(svg: str) -> list[tuple[str, float, float]]:
    """Gradient stops that own an edge band: two neighbouring stops within an
    eighth of the ramp that differ by more than 30 grey levels. That is a ramp
    across a small shape whose end stop took the shape's anti-aliased rim."""
    lum = lambda c: sum(int(c[i:i + 2], 16) for i in (0, 2, 4)) / 3  # noqa: E731
    bad = []
    for g in re.finditer(r'<(linear|radial)Gradient id="(\w+)"[^>]*>(.*?)</\1Gradient>', svg):
        stops = [(float(o), c) for o, c in re.findall(r'offset="([\d.]+)" stop-color="#(\w{6})"', g.group(3))]
        for (o1, c1), (o2, c2) in zip(stops, stops[1:]):
            if o2 - o1 <= 0.13 and abs(lum(c1) - lum(c2)) > 30:
                bad.append((g.group(2), o1, o2))
    return bad


@pytest.mark.parametrize("preset", ["balanced", "detailed"])
def test_the_wordmark_has_no_halo_fills_or_halo_regions(preset):
    """The Vexel wordmark's source was sharpened: inside every dark glyph a
    light lobe rings 2-3 px in, with an undershoot at the edge. Fitted over
    the whole region, the "e" counter came out a ramp from rim grey through
    white to rim grey and the "l" got a grey streak down its edge (six such
    rim stops in balanced, eleven in detailed); and in detailed the lobes were
    rescued as regions of their own (41 paths, seven hairline strokes)."""
    from pathlib import Path

    from PIL import Image

    from studi0trace.engines.presets import all_presets

    png = Path(__file__).resolve().parent.parent / "bench" / "corpus" / "real" / "logo" / "vexel-wordmark-512.png"
    rgba = np.asarray(Image.open(png).convert("RGBA"), dtype=np.uint8)
    params = VexelParams(**next(p for p in all_presets() if p.id == preset).params)
    svg = _rust(rgba, params)
    assert _rim_stops(svg) == []
    assert svg.count('fill="none"') == 0
    assert len(re.findall(r"<(?:path|rect|circle|ellipse|use)\b", svg)) <= 20
