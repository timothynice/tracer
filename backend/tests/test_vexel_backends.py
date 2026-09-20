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


def test_the_rust_backend_is_deterministic():
    """skimage's `medial_axis` is seeded from the OS, so the Python pipeline's
    stroke geometry is not reproducible between runs. The Rust one is."""
    rgba = _disc(size=48, radius=16.0)
    params = VexelParams()
    assert _rust(rgba, params) == _rust(rgba, params)


def test_the_rust_backend_rejects_a_mis_sized_buffer():
    rgba = _disc(size=32)
    with pytest.raises(ValueError, match="width x height"):
        vexel._vexel_rs.trace(rgba.tobytes(), 33, 32, VexelParams().model_dump())


def test_parameters_reach_the_rust_backend():
    rgba = _disc()
    assert "<circle" in _rust(rgba, VexelParams())
    assert "<circle" not in _rust(rgba, VexelParams(shape_fitting=False))
    assert "Gradient" not in _rust(rgba, VexelParams(gradients=False))
