"""Stage 0b: trace a small input at twice its size when it has thin features.

At 128 px the anti-aliasing model that places edges has two pixels of
evidence per edge, and a feature a couple of pixels wide is placed 0.1–1.9 px
off its truth. Tracing a 2× Lanczos upsample instead and letting the viewBox
carry the scale cut those errors by half to three quarters on every corpus
item with a region under 2.4 px wide (thin-mark 1.73 → 0.76, sticker 0.41 →
0.12, blobs 0.13 → 0.075) — and made every item made of large shapes worse,
because the resampler's ringing and its own anti-aliasing are read as edge
position (cutout 0.013 → 0.121). So the upsample is selected, never applied
blind: the direct trace runs first, and only a label map with a region
thinner than THIN_WIDTH px (width = 2·area/perimeter) sends the image back
through at 2×. The rule needs no renderer, so both engines apply it alike.

The kernel is Lanczos-3 at exactly 2×, which makes the tap weights two fixed
sets of six numbers; they are written here as literals, and the Rust twin
carries the same literals, so the two upsamples agree to the byte
(`tools/diffcheck.py upsample`).
"""
from __future__ import annotations

import numpy as np

UPSAMPLE_MAX_SIDE = 192   # px; larger inputs have the evidence they need
THIN_WIDTH = 2.2          # px; a region narrower than this (2·area/perimeter) wants the upsample
MIN_AREA = 6              # px²; specks below this are not features
# The 2× pass runs with the user's parameters as they are. Scaling min_region
# and curve_tolerance to source pixels was tried and gave back most of the
# outline gain (blobs 0.075 → 0.119) without curing the one item whose
# gradient bands split at 2× (sticker); that item is not selected instead.

# Lanczos-3 weights for an output pixel centred a quarter pixel before (even
# outputs) and after (odd outputs) input pixel i, over taps i-2 … i+3.
_W_EVEN = (-0.06850269446295365, 0.273025020812246, 0.8994068413745139, -0.13426528114738912, 0.030336113423582882, 0.0)
_W_ODD = (0.030112285361897653, -0.1332746355359615, 0.8927707740853273, 0.2710105682570789, -0.06799726302855182, 0.0073782708602093345)


def _pass(a: np.ndarray) -> np.ndarray:
    """Double the first axis. `a` is float64 (n, …)."""
    n = a.shape[0]
    out = np.empty((2 * n, *a.shape[1:]), dtype=np.float64)
    idx = np.arange(n)
    for parity, weights in ((0, _W_EVEN), (1, _W_ODD)):
        acc = np.zeros_like(a)
        for k, w in zip(range(-2, 4), weights):
            src = np.clip(idx + k, 0, n - 1)
            acc = acc + w * a[src]       # one tap at a time, in tap order: the Rust adds in the same order
        out[parity::2] = acc
    return out


def upsample2x(rgba: np.ndarray) -> np.ndarray:
    """(H, W, 4) uint8 → (2H, 2W, 4) uint8, Lanczos-3, channels straight."""
    a = rgba.astype(np.float64)
    a = _pass(a)                          # rows
    a = _pass(a.transpose(1, 0, 2)).transpose(1, 0, 2)  # columns
    return np.clip(np.floor(a + 0.5), 0.0, 255.0).astype(np.uint8)


def thinnest_region(labels: np.ndarray, min_area: int = MIN_AREA) -> float:
    """The smallest 2·area/perimeter over regions (label ≠ 0) of at least
    `min_area` pixels; perimeter counts 4-neighbour label changes, frame edges
    excluded, so a region on the frame reads wider, never thinner. inf if none."""
    best = float("inf")
    for lab in np.unique(labels):
        if lab == 0:
            continue
        m = labels == lab
        area = int(m.sum())
        if area < min_area:
            continue
        per = int((m[:, 1:] != m[:, :-1]).sum()) + int((m[1:, :] != m[:-1, :]).sum())
        best = min(best, 2.0 * area / max(per, 1))
    return best


def wants_upsample(labels: np.ndarray, height: int, width: int) -> bool:
    return max(height, width) <= UPSAMPLE_MAX_SIDE and thinnest_region(labels) < THIN_WIDTH


def halve(svg: str, width: int, height: int) -> str:
    """The SVG of the 2× trace, drawn at the original size: the root viewBox is
    the original canvas and everything but the defs sits in a half-scale group.
    Gradients (userSpaceOnUse), filters and stroke widths follow the element's
    user space, so they scale with it."""
    import re

    m = re.match(r"(<svg[^>]*>)(<defs>.*?</defs>)?(.*)</svg>$", svg, re.S)
    if not m:
        return svg
    root = re.sub(r'viewBox="[^"]*"', f'viewBox="0 0 {width} {height}"', m.group(1), count=1)
    return f'{root}{m.group(2) or ""}<g transform="scale(0.5)">{m.group(3)}</g></svg>'
