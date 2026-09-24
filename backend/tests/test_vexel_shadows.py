import io

import numpy as np
import pytest
import resvg_py
from PIL import Image
from scipy import ndimage

from studi0trace.engines.vexel import engine as vexel
from studi0trace.engines.vexel.engine import VexelParams, trace_rgba
from studi0trace.engines.vexel.shadows import Shadow, _fit_blur, shadow_filter_svg


def render(svg: str, w: int, h: int) -> np.ndarray:
    png = resvg_py.svg_to_bytes(svg_string=svg, width=w, height=h)
    return np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA")).astype(float)


def card_with_shadow(sigma: float = 10.0, dx: float = 0.0, dy: float = 14.0, opacity: float = 0.45) -> np.ndarray:
    """A rounded-ish card over a light backdrop, with a real Gaussian drop shadow."""
    n = 192
    shape = np.zeros((n, n))
    shape[50:140, 45:150] = 1.0
    g = ndimage.gaussian_filter(ndimage.shift(shape, (dy, dx), order=1, mode="constant"), sigma, mode="constant")
    backdrop = np.array([243.0, 244.0, 246.0])
    out = np.broadcast_to(backdrop, (n, n, 3)).copy()
    a = (opacity * g)[..., None]
    out = out * (1 - a)  # black shadow
    out[shape > 0.5] = np.array([42.0, 157.0, 143.0])
    return np.concatenate([out, np.full((n, n, 1), 255.0)], axis=-1).astype(np.uint8)


def test_fit_recovers_the_parameters_that_made_the_shadow():
    n = 192
    shape = np.zeros((n, n))
    shape[50:140, 45:150] = 1.0
    truth = dict(dx=-6.0, dy=11.0, sigma=7.0)
    target = 80.0 * ndimage.gaussian_filter(
        ndimage.shift(shape, (truth["dy"], truth["dx"]), order=1, mode="constant"), truth["sigma"], mode="constant"
    )
    domain = shape < 0.5
    dx, dy, sigma, k, rms = _fit_blur(shape, target, domain, (0.0, 0.0), inset=False)
    assert abs(dx - truth["dx"]) < 1.5
    assert abs(dy - truth["dy"]) < 1.5
    assert abs(sigma - truth["sigma"]) < 1.0
    assert rms < 1.0
    assert k > 0


def test_emitted_filter_reproduces_the_shadow_it_describes():
    """The filter is only worth emitting if a renderer agrees with the model."""
    shadow = Shadow(caster=1, dx=0.0, dy=14.0, sigma=10.0, colour=np.zeros(3), opacity=0.45, inset=False, rms=0.0, region=(-40.0, -40.0, 272.0, 272.0))
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
        f"<defs>{shadow_filter_svg(shadow, 's1', 2)}</defs>"
        '<rect width="192" height="192" fill="#f3f4f6"/>'
        '<rect x="45" y="50" width="105" height="90" fill="#2a9d8f" filter="url(#s1)"/></svg>'
    )
    got = render(svg, 192, 192)[..., :3]
    want = card_with_shadow()[..., :3].astype(float)
    assert float(np.sqrt(((got - want) ** 2).mean())) < 3.0


def test_shadow_becomes_a_filter_not_a_pile_of_bands():
    rgba = card_with_shadow()
    with_shadows = trace_rgba(rgba, VexelParams())
    without = trace_rgba(rgba, VexelParams(shadows=False))
    assert "<filter" in with_shadows
    assert "feGaussianBlur" in with_shadows
    assert "<filter" not in without
    assert with_shadows.count("<path") + with_shadows.count("<rect") < without.count("<path") + without.count("<rect")


def test_flat_art_gets_no_filter():
    """Two hard-edged colours are not a shadow; the detector must leave them be."""
    n = 128
    rgba = np.zeros((n, n, 4), np.uint8)
    rgba[..., 3] = 255
    rgba[..., :3] = np.array([250, 250, 250], np.uint8)
    rgba[30:90, 20:60, :3] = np.array([230, 57, 70], np.uint8)
    rgba[30:90, 70:110, :3] = np.array([42, 157, 143], np.uint8)
    assert "<filter" not in trace_rgba(rgba, VexelParams())


def test_blurred_artwork_is_not_treated_as_a_shadow():
    """A shape that is itself blurred has no hard edge: replacing it with a sharp
    shape plus a filter would render a hard edge where the original is soft."""
    n = 160
    disc = np.zeros((n, n))
    yy, xx = np.mgrid[0:n, 0:n]
    disc[(yy - 80) ** 2 + (xx - 80) ** 2 < 45**2] = 1.0
    soft = ndimage.gaussian_filter(disc, 12.0, mode="constant")[..., None]
    out = np.array([17.0, 24.0, 39.0]) * (1 - soft) + np.array([6.0, 214.0, 160.0]) * soft
    rgba = np.concatenate([out, np.full((n, n, 1), 255.0)], axis=-1).astype(np.uint8)
    assert "<filter" not in trace_rgba(rgba, VexelParams())


def card_on_clear_canvas(sigma: float = 4.0, dy: float = 6.0, opacity: float = 0.5) -> np.ndarray:
    """A hard-edged card on a transparent canvas, with a black drop shadow that
    is ink of its own: colour black, alpha opacity·blur(shape)."""
    n = 128
    shape = np.zeros((n, n))
    shape[30:90, 28:100] = 1.0
    g = ndimage.gaussian_filter(ndimage.shift(shape, (dy, 0.0), order=1, mode="constant"), sigma, mode="constant")
    out = np.zeros((n, n, 4))
    out[..., 3] = 255.0 * opacity * g
    out[shape > 0.5] = [131.0, 56.0, 236.0, 255.0]
    return np.round(out).astype(np.uint8)


def _rust(rgba: np.ndarray, params: VexelParams) -> str:
    height, width = rgba.shape[:2]
    return vexel._vexel_rs.trace(np.ascontiguousarray(rgba).tobytes(), width, height, params.model_dump())


def _over_white(rgba: np.ndarray) -> np.ndarray:
    a = rgba[..., 3:4] / 255.0
    return rgba[..., :3] * a + 255.0 * (1.0 - a)


@pytest.mark.parametrize("engine", ["python", "rust"])
def test_a_shadow_on_a_transparent_canvas_becomes_a_filter(engine):
    """With no backdrop colour for it to darken, the shadow is translucent ink.
    It used to come out as one hard-edged band or, once the canvas's own fill
    took up its falloff, as two scraps the rescue promoted — which read as
    thin regions and sent the whole trace through the 2x upsample, where the
    card's edge scalloped (shadow/transparent-bg-128)."""
    if engine == "rust" and vexel._vexel_rs is None:
        pytest.skip("the vexel_rs extension is not built")
    rgba = card_on_clear_canvas()
    run = trace_rgba if engine == "python" else _rust
    svg = run(rgba, VexelParams())
    assert "feGaussianBlur" in svg, svg
    assert svg.count("<path") + svg.count("<rect") == 1, svg
    assert "scale(0.5)" not in svg
    got = _over_white(render(svg, 128, 128))
    want = _over_white(rgba.astype(float))
    assert float(np.sqrt(((got - want) ** 2).mean())) < 3.0


@pytest.mark.parametrize("engine", ["python", "rust"])
def test_antialiased_art_on_a_transparent_canvas_gets_no_filter(engine):
    """An anti-aliased rim is translucent too, but a filter does not explain it."""
    if engine == "rust" and vexel._vexel_rs is None:
        pytest.skip("the vexel_rs extension is not built")
    n = 128
    yy, xx = np.mgrid[0:n, 0:n] + 0.5
    cover = np.clip(40.0 - np.hypot(xx - 64, yy - 64) + 0.5, 0.0, 1.0)
    rgba = np.zeros((n, n, 4), np.uint8)
    rgba[..., :3] = (230, 57, 70)
    rgba[..., 3] = np.round(255 * cover).astype(np.uint8)
    run = trace_rgba if engine == "python" else _rust
    assert "<filter" not in run(rgba, VexelParams())
