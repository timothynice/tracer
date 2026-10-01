"""The Rust core's scorecard (`crates/studi0trace-core`) answers as `quality.assess` does.

`studi0trace_core` is a separate extension (a Rust build, apart from `vexel_rs`):

    cd backend && VIRTUAL_ENV=$PWD/.venv .venv/bin/python -m maturin develop --release \\
        -m ../crates/studi0trace-core/Cargo.toml --features python

Without it these tests are skipped, so the Python suite never depends on a Rust
build. `python -m tools.diffcheck scorecard` is the same comparison over the
whole corpus.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pytest
from PIL import Image

from studi0trace.imaging import quality

core = pytest.importorskip("studi0trace_core")

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "crates" / "studi0trace-core" / "tests" / "fixtures"
CORPUS = ROOT / "backend" / "bench" / "corpus"

# bit-equal on the machine the core's fixtures were made on except the two ΔE
# keys (BLAS's fused multiply-add under numpy's matmul); 1e-9 anywhere else
REL = 1e-9


def native(v):
    """A numpy scalar as the Python number it is (`diffcheck._native`), so that a type is the Python's."""
    return v.item() if isinstance(v, np.generic) else v


def assert_same_card(py: dict, rs: dict) -> None:
    assert list(rs) == list(py), "the same keys in the same order"
    for key, x in py.items():
        x, y = native(x), rs[key]
        assert type(y) is type(x), f"{key}: {x!r} in Python, {y!r} in Rust"
        if isinstance(x, float):
            assert abs(x - y) <= REL * max(abs(x), abs(y)), f"{key}: {x!r} in Python, {y!r} in Rust"
        else:
            assert x == y, f"{key}: {x!r} in Python, {y!r} in Rust"


def source(path: pathlib.Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)


def both(svg: str, rgba: np.ndarray) -> tuple[dict, dict]:
    h, w = rgba.shape[:2]
    return quality.assess(svg, quality.Reference(rgba)), core.assess(svg, rgba.tobytes(), w, h)


@pytest.mark.parametrize("name, image", [
    ("geometry_logomark-128.svg", "real/logo/logomark-128.png"),
    ("geometry_thin-mark-128.svg", "synthetic/logo/thin-mark-128.png"),
])
def test_a_traced_svg_scores_as_the_python_scores_it(name: str, image: str):
    svg_path, png_path = FIXTURES / name, CORPUS / image
    if not (svg_path.exists() and png_path.exists()):
        pytest.skip("the core's fixtures or the bench corpus are not in this checkout")
    py, rs = both(svg_path.read_text(), source(png_path))
    assert_same_card(py, rs)
    assert py["elements"] > 0 and py["segments"] > 0, "a card with something in it"


def test_a_drawn_svg_on_an_opaque_source_scores_as_the_python_scores_it():
    rgba = np.full((64, 64, 4), 255, np.uint8)
    rgba[16:48, 16:48] = (0, 170, 255, 255)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
           '<path d="M0 0H64V64H0Z" fill="#fff"/>'
           '<path d="M16 16H48V48H16Z" fill="#0af"/>'
           '<circle cx="32" cy="32" r="6" fill="#fff"/></svg>')
    py, rs = both(svg, rgba)
    assert_same_card(py, rs)
    assert isinstance(rs["elements"], int) and isinstance(rs["delta_e_mean"], float)


def test_a_source_of_the_wrong_size_is_a_value_error():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><path d="M0 0H4V4H0Z" fill="#fff"/></svg>'
    with pytest.raises(ValueError, match="bytes of RGBA"):
        core.assess(svg, b"\xff" * 10, 4, 4)


def test_an_svg_that_does_not_render_is_a_value_error():
    with pytest.raises(ValueError):
        core.assess("not an svg", b"\xff" * 64, 4, 4)
