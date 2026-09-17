import io
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import resvg_py
from PIL import Image
from pydantic import ValidationError

from studi0trace.engines import registry
from studi0trace.engines.vexel.engine import VexelEngine, VexelParams, trace_rgba
from studi0trace.imaging.intake import load_upload
from tests.conftest import black_square_on_transparent, encode, two_colour_image

LIMITS = dict(max_bytes=1 << 30, max_pixels=1 << 30)


def render(svg: str, w: int, h: int) -> np.ndarray:
    png = resvg_py.svg_to_bytes(svg_string=svg, width=w, height=h)
    return np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA")).astype(float)


def test_registered_with_schema():
    registry.load_builtin()
    assert "vexel" in registry.ids()
    desc = registry.describe(registry.get("vexel"))
    assert desc["params"]["properties"]["detail"]["ui"]["group"] == "Regions"
    assert desc["defaults"]["layering"] == "stacked"
    with pytest.raises(ValidationError):
        VexelParams(detail=0)


def test_black_square_on_transparent_becomes_one_rect():
    image = load_upload(black_square_on_transparent(64, 16), **LIMITS)
    result = VexelEngine().trace(image, VexelParams())
    root = ET.fromstring(result.svg)
    assert root.get("viewBox") == "0 0 64 64"
    kids = [k for k in root if not k.tag.endswith("defs")]
    assert len(kids) == 1, result.svg
    assert kids[0].tag.endswith("rect"), result.svg
    x, y, w, h = (float(kids[0].get(a)) for a in ("x", "y", "width", "height"))
    assert abs(x - 16) < 0.15 and abs(y - 16) < 0.15 and abs(w - 32) < 0.3 and abs(h - 32) < 0.3
    assert kids[0].get("fill") == "#000000"
    out = render(result.svg, 64, 64)
    assert out[2, 2, 3] == 0 and out[32, 32, 3] == 255


def test_two_colours_two_shapes_and_fidelity():
    image = load_upload(two_colour_image(64), **LIMITS)
    result = VexelEngine().trace(image, VexelParams())
    out = render(result.svg, 64, 64)
    src = np.asarray(image.image).astype(float)
    assert np.abs(out[..., :3] - src[..., :3]).mean() < 2.0
    assert result.stats.unique_fills >= 2


def test_linear_gradient_disc_is_a_gradient_circle():
    size = 96
    img = np.zeros((size, size, 4), np.uint8)
    img[..., :3] = (240, 240, 240)
    img[..., 3] = 255
    yy, xx = np.mgrid[0:size, 0:size]
    disc = (xx - 48) ** 2 + (yy - 48) ** 2 < 36**2
    t = (xx - 12) / 72.0
    img[disc, 0] = (255 * (1 - t)).clip(0, 255)[disc]
    img[disc, 1] = 40
    img[disc, 2] = (255 * t).clip(0, 255)[disc]
    svg = trace_rgba(img, VexelParams())
    root = ET.fromstring(svg)
    assert "linearGradient" in svg
    assert any(k.tag.endswith("circle") for k in root), svg
    out = render(svg, size, size)
    err = np.abs(out[..., :3] - img[..., :3].astype(float))
    assert err[disc].mean() < 6.0, err[disc].mean()  # gradient reproduced, not banded
    assert err[~disc].mean() < 2.0
    # without gradients the disc becomes several flat bands
    banded = trace_rgba(img, VexelParams(gradients=False))
    assert "linearGradient" not in banded
    assert ET.fromstring(banded).__len__() > 3


def test_jpeg_input_survives():
    img = Image.new("RGB", (48, 48), (30, 120, 200))
    for i in range(10, 38):
        for j in range(10, 38):
            img.putpixel((j, i), (250, 220, 40))
    image = load_upload(encode(img, "JPEG", quality=85), **LIMITS)
    result = VexelEngine().trace(image, VexelParams())
    assert result.stats.paths + result.svg.count("<rect") >= 2
    assert result.elapsed_ms < 5000
