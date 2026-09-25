"""The partition's seeds: a neck in a seed between two colours is two seeds.

A watershed basin per seed component means a strip of seed across a gap in a
ridge makes two areas one region for good. The gaps are where a weak edge meets
a strong one, and JPEG makes them common: the held-out moon's craters, a key's
collar and a leaf's veins were each traced as one gradient with the fill around
them (`partition.seed_markers`).
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
import resvg_py
from PIL import Image
from scipy import ndimage
from skimage.color import lab2rgb, rgb2lab
from skimage.measure import label as cc_label

from studi0trace.engines.vexel import engine as vexel
from studi0trace.engines.vexel.engine import VexelEngine, VexelParams
from studi0trace.engines.vexel.partition import discontinuity, initial_labels, seed_markers
from studi0trace.engines.vexel.prepare import prepare
from studi0trace.imaging.intake import load_upload

LIMITS = dict(max_bytes=1 << 30, max_pixels=1 << 30)
HELDOUT = Path(__file__).resolve().parents[1] / "bench" / "heldout" / "noto"


def render(svg: str, w: int, h: int) -> np.ndarray:
    png = resvg_py.svg_to_bytes(svg_string=svg, width=w, height=h)
    return np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA")).astype(float)


def trace(data: bytes) -> str:
    return VexelEngine().trace(load_upload(data, **LIMITS), VexelParams()).svg


def _necked(right_l: float) -> tuple[np.ndarray, np.ndarray]:
    """Two 20 px blocks of seed joined by a corridor two pixels wide; the right
    block's L* is `right_l`, the left one's 50."""
    smooth = np.zeros((30, 56), bool)
    smooth[5:25, 3:23] = True
    smooth[14:16, 23:33] = True
    smooth[5:25, 33:53] = True
    feats = np.zeros((30, 56, 4), np.float32)
    feats[..., 0] = 50.0
    feats[:, 28:, 0] = right_l
    feats[..., 3] = 100.0
    return smooth, feats


def test_a_neck_between_two_colours_splits_the_seed():
    smooth, feats = _necked(60.0)
    m, origin = seed_markers(smooth, 6, feats, detail=6.0)
    assert m.max() == 2 and origin[1] == origin[2] == 1
    assert m[15, 12] != m[15, 42] and m[15, 12] > 0 and m[15, 42] > 0
    assert np.array_equal(m > 0, smooth), "the whole seed is shared out between the two"
    assert (m[5:25, 3:23] == m[15, 12]).all() and (m[5:25, 33:53] == m[15, 42]).all()


def test_a_neck_within_one_colour_leaves_the_seed_whole():
    smooth, feats = _necked(53.0)  # nearer than `detail`
    m, origin = seed_markers(smooth, 6, feats, detail=6.0)
    assert np.array_equal(m, cc_label(smooth, connectivity=1)), "unsplit, the markers are the components as before"
    assert not origin.any()


def _disc_cut_by_an_edge(de: float) -> tuple[bytes, np.ndarray, np.ndarray]:
    """A disc `de` ΔE darker than the linear gradient it sits in, cut by the
    panel's edge against a dark field, saved as JPEG q75. Returns the JPEG and
    masks of the disc's core and of a ring of gradient round it, below the edge."""
    size, cx, cy, r, edge = 256, 150.0, 120.0, 36.0, 100.0
    ys, xs = np.mgrid[0:size, 0:size] + 0.5
    t = (xs + 0.4 * ys) / (size * 1.4)
    lab = rgb2lab((np.array([200, 150, 90]) * (1 - t[..., None]) + np.array([240, 215, 160]) * t[..., None]) / 255.0)
    disc = np.zeros((size, size))
    panel = np.zeros((size, size))
    for oy in range(4):
        for ox in range(4):
            px, py = xs - 0.5 + (ox + 0.5) / 4, ys - 0.5 + (oy + 0.5) / 4
            disc += np.hypot(px - cx, py - cy) <= r
            panel += py >= edge
    disc, panel = disc / 16, panel / 16
    dark = lab.copy()
    dark[..., 0] -= de
    rgb = lab2rgb(lab) * (1 - disc[..., None]) + lab2rgb(dark) * disc[..., None]
    rgb = rgb * panel[..., None] + np.array([40, 40, 60]) / 255.0 * (1 - panel[..., None])
    buf = io.BytesIO()
    Image.fromarray(np.clip(np.round(rgb * 255), 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=75)
    d = np.hypot(xs - cx, ys - cy)
    below = ys > edge + 5
    return buf.getvalue(), (d < r - 4) & below, (d > r + 4) & (d < r + 10) & below


@pytest.mark.parametrize("de", [6.0, 8.0, 10.0])
def test_a_low_contrast_disc_cut_by_a_strong_edge_survives_jpeg(de):
    """The disc's ridge gives out a few pixels short of the panel's edge (NMS
    runs along the strong edge there), so its seed and the gradient's were one
    and the disc was drawn as part of the ramp: its step came out at 16 %."""
    data, core, ring = _disc_cut_by_an_edge(de)
    src = np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(float)
    out = render(trace(data), 256, 256)[..., :3]
    step_src = src[ring].mean(0) - src[core].mean(0)
    step_out = out[ring].mean(0) - out[core].mean(0)
    assert np.linalg.norm(step_out) > 0.8 * np.linalg.norm(step_src)
    assert np.abs(out[core] - src[core]).mean() < 3.0


@pytest.mark.skipif(not HELDOUT.exists(), reason="held-out corpus not present")
def test_moon_craters_survive_jpeg():
    """The held-out moon at JPEG q75 (the upper-left of it: the big crater and
    the limb it runs along). The crater's edge six pixels inside the limb is a
    shoulder on the limb's smeared chroma ridge, so its seed ran along the limb
    into the body's: one region, one gradient, the crater gone (outline error
    11 px on the whole image, every other engine under 0.9)."""
    jpg = np.asarray(Image.open(HELDOUT / "u1f315-512-q75.jpg").convert("RGBA"))[0:384, 0:256]
    clean = np.asarray(Image.open(HELDOUT / "u1f315-512.png").convert("RGBA")).astype(float)[0:384, 0:256]
    buf = io.BytesIO()
    Image.fromarray(jpg).save(buf, "PNG")
    out = render(trace(buf.getvalue()), 256, 384)
    opaque = clean[..., 3] > 250
    crater = ndimage.binary_erosion(opaque & (np.linalg.norm(clean[..., :3] - [241, 198, 136], axis=-1) < 4), iterations=4)
    body = ndimage.binary_erosion(opaque & (np.linalg.norm(clean[..., :3] - [252, 229, 172], axis=-1) < 4), iterations=4)
    src = jpg.astype(float)
    step_src = src[body][:, :3].mean(0) - src[crater][:, :3].mean(0)
    step_out = out[body][:, :3].mean(0) - out[crater][:, :3].mean(0)
    assert np.linalg.norm(step_out) > 0.9 * np.linalg.norm(step_src)
    assert np.abs(out[crater][:, :3] - src[crater][:, :3]).mean() < 2.0


GLOW = Path(__file__).resolve().parents[1] / "bench" / "corpus" / "real" / "shadow" / "glow-512-q75.jpg"


@pytest.mark.skipif(not GLOW.exists(), reason="bench corpus not present")
def test_a_glow_pinched_by_jpeg_noise_stays_one_region():
    """JPEG's blocks pinch the seed of a steep radial glow into rings whose
    colours are far apart; split at those necks the disc came out as a blocky
    core inside a ring (artifact index 11 -> 106). No step divides them — the
    boundary is no steeper than the ramp either side — so `rejoin_ramps` puts
    them back together."""
    a = np.asarray(Image.open(GLOW).convert("RGBA"))
    p = prepare(a)
    labels = initial_labels(discontinuity(p.features), p.features, min_region=6, detail=6.0)
    ys, xs = np.mgrid[0:512, 0:512]
    inside = labels[np.hypot(xs - 256, ys - 256) < 150]
    assert np.bincount(inside).max() > 0.99 * inside.size


@pytest.mark.skipif(vexel._vexel_rs is None, reason="the vexel_rs extension is not built")
@pytest.mark.parametrize("detail", [3.0, 6.0, 14.0])
def test_the_engines_split_seeds_alike(detail):
    data, _, _ = _disc_cut_by_an_edge(8.0)
    a = np.ascontiguousarray(np.asarray(Image.open(io.BytesIO(data)).convert("RGBA")))
    p = prepare(a)
    py = initial_labels(discontinuity(p.features), p.features, min_region=6, detail=detail)
    rs = np.asarray(vexel._vexel_rs._stage_labels0(a.tobytes(), 256, 256, 6, detail), np.int32).reshape(256, 256)
    assert np.array_equal(py, rs)
