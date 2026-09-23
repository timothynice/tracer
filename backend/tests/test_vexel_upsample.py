"""Small inputs with thin features are traced at twice their size (spec 2026-09-23)."""
from __future__ import annotations

import re

import numpy as np
import resvg_py

from bench.geometry import outline_error, root_scale
from studi0trace.engines.vexel.upsample import halve, thinnest_region, upsample2x, wants_upsample
from tests.test_vexel_topology import tilted_square_png, trace


def thin_bars_png(size: int = 128, width: float = 2.0, gap: float = 9.0, fill: str = "#1d3557") -> bytes:
    """Bars two pixels wide: the case the placement model gets a tenth of a pixel wrong per edge."""
    x, bars = 12.0, []
    while x + width < size - 12:
        bars.append(f'<rect x="{x}" y="16" width="{width}" height="{size - 32}" fill="{fill}"/>')
        x += width + gap
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"><rect width="{size}" height="{size}" fill="#fff"/>{"".join(bars)}</svg>'
    return svg, bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size))


def test_the_rule_reads_thin_regions_and_ignores_large_ones():
    labels = np.ones((64, 64), dtype=np.int32)
    labels[10:50, 30:32] = 2          # a 2 px bar: width 2·80/(2·40+2·2) ≈ 1.9
    assert thinnest_region(labels) < 2.2 and wants_upsample(labels, 64, 64)
    labels[:] = 1
    labels[10:50, 10:50] = 2          # a 40 px square
    assert thinnest_region(labels) > 2.2 and not wants_upsample(labels, 64, 64)
    big = np.where(np.arange(256)[None, :] % 8 < 2, 2, 1).astype(np.int32).repeat(256, axis=0)
    assert thinnest_region(big) < 2.2 and not wants_upsample(big, 256, 256), "a 256 px image keeps its evidence"


def test_upsample_is_lanczos_2x_and_identical_in_both_engines():
    import vexel_rs

    rng = np.random.default_rng(1)
    a = rng.integers(0, 256, (37, 29, 4), dtype=np.uint8)
    up = upsample2x(a)
    assert up.shape == (74, 58, 4) and up.dtype == np.uint8
    flat = np.full((20, 20, 4), 200, dtype=np.uint8)
    assert (upsample2x(flat) == 200).all(), "a flat field stays flat: the taps sum to one"
    rs = np.asarray(vexel_rs._stage_upsample(a.tobytes(), 37, 29), dtype=np.uint8).reshape(74, 58, 4)
    assert (rs == up).all(), "the two upsamples agree to the byte"


def test_halve_keeps_the_canvas_and_scales_the_drawing():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><defs><linearGradient id="g"/></defs><path d="M0 0L256 0"/></svg>'
    out = halve(svg, 128, 128)
    assert 'viewBox="0 0 128 128"' in out and out.count("<defs>") == 1
    assert '<g transform="scale(0.5)"><path d="M0 0L256 0"/></g></svg>' in out
    assert root_scale(out) == 0.5 and root_scale(svg) == 1.0


def test_thin_bars_are_upsampled_and_come_closer_to_their_truth_while_a_square_is_left_alone():
    truth, png = thin_bars_png()
    auto, never = trace(png), trace(png, upsample="never")
    assert 'transform="scale(0.5)"' in auto and 'viewBox="0 0 128 128"' in auto
    assert 'transform="scale(0.5)"' not in never
    e_auto = outline_error(truth, auto, 128, 128)["outline_px"]
    e_never = outline_error(truth, never, 128, 128)["outline_px"]
    assert e_auto < 0.8 * e_never, (e_never, e_auto)
    square = tilted_square_png(30, size=128, side=60.0)
    assert trace(square) == trace(square, upsample="never"), "large shapes keep the direct trace"
